"""Local API. Each optimization and its agent tools operate on an isolated DB copy."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .aleph import ALEPH_WEIGHTS, build_run_timeseries
from .db_copy import create_working_copy


class RunRequest(BaseModel):
    agent: str = Field(default="auto", pattern="^(auto|gemini|mock)$")
    max_iterations: int = Field(default=3, ge=1, le=12)
    horizon: int | None = Field(default=None, ge=1, le=672)
    dataset_id: str = Field(default="default", pattern=r"^(default|scenario-[A-Za-z0-9_-]{1,40})$")
    gemini_api_key: str | None = Field(default=None, max_length=256, repr=False)

    @field_validator("gemini_api_key")
    @classmethod
    def validate_gemini_api_key(cls, value: str | None) -> str | None:
        if value == "":
            return None
        if value is not None and (any(ch.isspace() for ch in value)
                                  or any(ord(ch) < 33 or ord(ch) > 126 for ch in value)):
            raise ValueError("API key must contain printable non-whitespace ASCII characters")
        return value

    def public_request(self) -> dict:
        """Return run metadata without the one-time credential."""
        return self.model_dump(exclude={"gemini_api_key"})


def create_app(db_path: str, optimizer_module, default_agent: str = "auto",
               run_dir: str | None = None, state_db_path: str | None = None):
    sample_path = Path(db_path).expanduser().resolve()
    working_dir = Path(run_dir).expanduser().resolve() if run_dir else sample_path.parent / "runs"
    control_db = (Path(state_db_path).expanduser().resolve() if state_db_path
                  else sample_path.parent / ".afterhours-runtime" / "api-state.sqlite")
    control_db.parent.mkdir(parents=True, exist_ok=True)
    dataset_dir = control_db.parent / "datasets"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    from .synthetic_datasets import create_variant
    initial_variant = dataset_dir / "scenario-default.sqlite"
    if not initial_variant.exists():
        create_variant(sample_path, initial_variant, "scenario-default", "Synthetic Variant 1", 41031)
    app = FastAPI(title="AfterHours Microgrid Brain API", version="1.1.0")

    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_error(_request: Request, exc: RequestValidationError):
        # Pydantic errors may include the submitted value. Never echo a request
        # body because it can contain the one-time Gemini credential.
        return JSONResponse(status_code=422, content={
            "detail": [{"loc": error.get("loc", []), "msg": error.get("msg", "Invalid request"),
                        "type": error.get("type", "value_error")} for error in exc.errors()]
        })
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"^http://127\.0\.0\.1(?::\d+)?$",
        allow_methods=["GET", "POST"],
        allow_headers=["*"], allow_credentials=False,
    )
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="microgrid-run")
    runs: dict[str, dict] = {}
    lock = Lock()

    with closing(sqlite3.connect(control_db, timeout=30)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS brain_api_run (
                request_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                progress_json TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                working_db_path TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(brain_api_run)")}
        if "working_db_path" not in columns:
            conn.execute("ALTER TABLE brain_api_run ADD COLUMN working_db_path TEXT")
        persisted = conn.execute("""
            SELECT request_id,status,request_json,progress_json,result_json,error,working_db_path
            FROM brain_api_run
        """).fetchall()
        conn.commit()

    # Preserve read access to runs created by the previous API, which stored
    # its request journal and optimizer rows inside the sample database.
    if sample_path.is_file():
        try:
            with closing(sqlite3.connect(f"{sample_path.as_uri()}?mode=ro", uri=True, timeout=5)) as legacy:
                has_legacy_api = legacy.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='brain_api_run'"
                ).fetchone()
                if has_legacy_api:
                    persisted_ids = {row[0] for row in persisted}
                    for row in legacy.execute("""
                        SELECT request_id,status,request_json,progress_json,result_json,error
                        FROM brain_api_run
                    """).fetchall():
                        if row[0] not in persisted_ids:
                            persisted.append((*row, str(sample_path)))
        except sqlite3.Error:
            pass

    for request_id, status, request_json, progress_json, result_json, error, working_db_path in persisted:
        if status in {"queued", "running"}:
            status = "failed"
            error = "API process restarted before this run completed."
            progress = {**json.loads(progress_json), "stage": "failed"}
        else:
            progress = json.loads(progress_json)
        runs[request_id] = {
            "status": status, "request": json.loads(request_json),
            "progress": progress, "error": error, "working_db_path": working_db_path,
        }
        if result_json:
            runs[request_id]["result"] = json.loads(result_json)

    def persist(run_id: str):
        with lock:
            item = dict(runs[run_id])
        with closing(sqlite3.connect(control_db, timeout=30)) as conn:
            conn.execute("""
                INSERT INTO brain_api_run
                    (request_id,status,request_json,progress_json,result_json,error,working_db_path,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(request_id) DO UPDATE SET
                    status=excluded.status, request_json=excluded.request_json,
                    progress_json=excluded.progress_json, result_json=excluded.result_json,
                    error=excluded.error, working_db_path=excluded.working_db_path,
                    updated_at=excluded.updated_at
            """, (
                run_id, item["status"], json.dumps(item["request"]),
                json.dumps(item.get("progress", {})),
                json.dumps(item["result"]) if item.get("result") is not None else None,
                item.get("error"), item.get("working_db_path"),
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()

    # Persist interrupted requests after loading them into memory.
    for request_id, item in list(runs.items()):
        if item["status"] == "failed" and item.get("error") == "API process restarted before this run completed.":
            persist(request_id)

    def worker(request_id: str, request: RunRequest, source_db: Path):
        def update_progress(progress: dict):
            with lock:
                runs[request_id]["progress"] = progress
            persist(request_id)

        with lock:
            runs[request_id]["status"] = "running"
        persist(request_id)
        try:
            work_db = create_working_copy(source_db, request_id, working_dir)
            with lock:
                runs[request_id]["working_db_path"] = str(work_db)
            persist(request_id)

            mode = request.agent
            if mode == "auto" and request.gemini_api_key:
                mode = "gemini"
            elif mode == "auto":
                mode = default_agent
            if mode == "auto":
                mode = "gemini" if request.gemini_api_key or os.getenv("GEMINI_API_KEY") else "mock"
            if mode == "gemini":
                from .agent import GeminiAgent
                agent = GeminiAgent(api_key=request.gemini_api_key)
            elif mode == "mock":
                from .agent import MockAgent
                agent = MockAgent()
            else:
                raise ValueError(f"Unsupported configured agent: {mode}")

            from .orchestrator import run_agentic_workflow
            result = run_agentic_workflow(
                str(work_db), optimizer_module, agent,
                max_iterations=request.max_iterations, horizon=request.horizon,
                progress_callback=update_progress,
            )
            with lock:
                runs[request_id].update({
                    "status": "completed", "result": result,
                    "progress": {"stage": "completed", "iteration": result["iterations"],
                                 "total_iterations": request.max_iterations},
                })
            persist(request_id)
        except Exception as exc:
            with lock:
                runs[request_id].update({
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "progress": {**runs[request_id].get("progress", {}), "stage": "failed"},
                })
            persist(request_id)

    def find_run_database(optimizer_run_id: str) -> Path:
        with lock:
            matches = [item.get("working_db_path") for item in runs.values()
                       if item.get("result", {}).get("best_candidate", {}).get("run_id") == optimizer_run_id]
        for value in matches:
            if value and Path(value).is_file():
                return Path(value)
        raise HTTPException(404, "Unknown optimizer run ID")

    @app.get("/api/config")
    def config():
        return {"default_agent": default_agent}

    def list_datasets():
        base_name = "Primary Synthetic Dataset"
        try:
            with closing(sqlite3.connect(f"{sample_path.as_uri()}?mode=ro", uri=True)) as conn:
                row = conn.execute("SELECT name FROM microgrid LIMIT 1").fetchone()
                if row and row[0]: base_name = str(row[0])
        except sqlite3.Error:
            pass
        items = [{"id": "default", "name": base_name, "kind": "primary"}]
        for path in sorted(dataset_dir.glob("scenario-*.sqlite")):
            try:
                with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=3)) as conn:
                    row = conn.execute("SELECT dataset_id,display_name,seed FROM afterhours_dataset_info LIMIT 1").fetchone()
                    if row: items.append({"id": row[0], "name": row[1], "seed": row[2], "kind": "synthetic_variant"})
            except sqlite3.Error:
                continue
        return items

    def selected_dataset(dataset_id: str) -> Path:
        if dataset_id == "default": return sample_path
        if not re.fullmatch(r"scenario-[A-Za-z0-9_-]{1,40}", dataset_id):
            raise HTTPException(422, "Unknown dataset selection")
        path = dataset_dir / f"{dataset_id}.sqlite"
        if not path.is_file(): raise HTTPException(404, "Selected dataset does not exist")
        return path

    @app.get("/api/datasets")
    def datasets():
        return {"datasets": list_datasets()}

    @app.post("/api/datasets/randomize")
    def randomize_dataset():
        existing = [item for item in list_datasets() if item["kind"] == "synthetic_variant"]
        if len(existing) >= 9:
            raise HTTPException(409, "Dataset limit reached. Remove an unused randomized variant before generating another.")
        dataset_id = "scenario-" + uuid4().hex[:12]
        name = f"Randomized Synthetic Variant {len(existing) + 1}"
        path = dataset_dir / f"{dataset_id}.sqlite"
        try:
            info = create_variant(sample_path, path, dataset_id, name, secrets.randbits(32))
        except Exception as exc:
            raise HTTPException(422, f"Could not generate a valid synthetic variant: {exc}") from exc
        return {"dataset": {"id": dataset_id, "name": name, "kind": "synthetic_variant",
                            "load_factor_range": info["load_factor_range"],
                            "pv_factor_range": info["pv_factor_range"]}}

    @app.get("/api/health")
    def health():
        if not sample_path.is_file():
            return {"status": "not_ready", "database_exists": False, "database_ready": False}
        try:
            with closing(sqlite3.connect(f"{sample_path.as_uri()}?mode=ro", uri=True, timeout=5)) as conn:
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
            required = {
                "interval_summary", "measurement", "weather", "battery_state", "dispatch",
                "battery_degradation_model", "flexibility_declaration",
                "flexibility_verification", "optimization_run", "optimization_result",
                "optimization_constraint_check", "agent_run",
            }
            ready = required.issubset(tables)
            return {"status": "ok" if ready else "not_ready", "database_exists": True,
                    "database_ready": ready, "missing_tables": sorted(required - tables),
                    "default_agent": default_agent}
        except sqlite3.Error as exc:
            return {"status": "not_ready", "database_exists": True,
                    "database_ready": False, "error": str(exc)}

    @app.post("/api/run", status_code=202)
    def start_run(request: RunRequest):
        if not sample_path.is_file():
            raise HTTPException(500, "Configured sample database does not exist")
        source_db = selected_dataset(request.dataset_id)
        effective_agent = request.agent
        if effective_agent == "auto":
            if request.gemini_api_key:
                effective_agent = "gemini"
            elif default_agent != "auto":
                effective_agent = default_agent
            else:
                effective_agent = "gemini" if os.getenv("GEMINI_API_KEY") else "mock"
        if effective_agent == "gemini" and request.max_iterations > 5:
            raise HTTPException(
                422,
                "Gemini runs are limited to 5 iterations. Select 1–5 iterations or choose Mock.",
            )
        if effective_agent == "gemini" and not (request.gemini_api_key or os.getenv("GEMINI_API_KEY")):
            raise HTTPException(400, "Gemini was selected but GEMINI_API_KEY is not set in the environment")
        request_id = "REQ-" + uuid4().hex[:12].upper()
        with lock:
            runs[request_id] = {
                "status": "queued", "request": request.public_request(),
                "progress": {"stage": "queued", "iteration": 0,
                             "total_iterations": request.max_iterations},
                "working_db_path": None,
            }
        persist(request_id)
        executor.submit(worker, request_id, request, source_db)
        return {"request_id": request_id, "status": "queued", "agent": effective_agent,
                "dataset_id": request.dataset_id}

    @app.get("/api/status/{request_id}")
    def status(request_id: str):
        with lock:
            item = runs.get(request_id)
            if item is None:
                raise HTTPException(404, "Unknown request ID")
            return {k: v for k, v in item.items() if k not in {"result", "working_db_path"}}

    @app.get("/api/result/{request_id}")
    def result(request_id: str):
        with lock:
            item = runs.get(request_id)
            if item is None:
                raise HTTPException(404, "Unknown request ID")
            if item["status"] != "completed":
                raise HTTPException(409, f"Run status is {item['status']}")
            return item["result"]

    @app.get("/api/report/{request_id}")
    def report(request_id: str):
        with lock:
            item = runs.get(request_id)
            if item is None:
                raise HTTPException(404, "Unknown request ID")
            if item["status"] != "completed":
                raise HTTPException(409, f"Run status is {item['status']}")
            return {"agent_run_id": item["result"]["agent_run_id"],
                    "report": item["result"]["report"],
                    "report_source": item["result"]["report_source"],
                    "agent": item["result"]["agent"],
                    "optimizer_run_id": item["result"]["best_candidate"]["run_id"]}

    @app.get("/api/timeseries/{run_id}")
    def timeseries(run_id: str):
        path = find_run_database(run_id)
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)) as conn:
                data = build_run_timeseries(conn, run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"run_id": data["run_id"], "available": data["available"],
                "timestamps": data["timestamps"], "units": data.get("units", {}),
                "currency_code": data.get("currency_code"),
                "data_quality": data.get("data_quality", "unknown"),
                "datasets": data.get("datasets", []), "series": data.get("series", {})}

    @app.get("/api/aleph/{run_id}")
    def aleph(run_id: str):
        path = find_run_database(run_id)
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)) as conn:
                data = build_run_timeseries(conn, run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"run_id": data["run_id"], "available": data["available"],
                "timestamps": data["timestamps"], "unit": "0–100",
                "data_quality": data.get("data_quality", "unknown"),
                "weights": ALEPH_WEIGHTS, "score": data.get("aleph_summary", {}).get("score"),
                "components": data.get("components", {}),
                "summary": data.get("aleph_summary", {}),
                "chart": {"labels": data["timestamps"],
                          "datasets": [{"label": "Aleph", "data": data.get("aleph", []),
                                        "unit": "0–100", "fill": True}]}}

    return app
