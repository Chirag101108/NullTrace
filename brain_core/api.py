"""Local FastAPI interface for the microgrid brain."""
from __future__ import annotations
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    agent: str = Field(default="auto", pattern="^(auto|gemini|mock)$")
    max_iterations: int = Field(default=3, ge=1, le=12)
    horizon: int | None = Field(default=None, ge=1, le=672)


def create_app(db_path: str, optimizer_module):
    app = FastAPI(title="AfterHours Microgrid Brain API", version="1.0.0")
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

    def persist(run_id: str):
        with lock:
            item = dict(runs[run_id])
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute("""
                INSERT INTO brain_api_run
                    (request_id,status,request_json,progress_json,result_json,error,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(request_id) DO UPDATE SET
                    status=excluded.status, request_json=excluded.request_json,
                    progress_json=excluded.progress_json, result_json=excluded.result_json,
                    error=excluded.error, updated_at=excluded.updated_at
            """, (
                run_id, item["status"], json.dumps(item["request"]),
                json.dumps(item.get("progress", {})),
                json.dumps(item["result"]) if item.get("result") is not None else None,
                item.get("error"), datetime.now(timezone.utc).isoformat(),
            ))

    if Path(db_path).is_file():
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS brain_api_run (
                    request_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    progress_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL
                )
            """)
            persisted = conn.execute("""
                SELECT request_id,status,request_json,progress_json,result_json,error
                FROM brain_api_run
            """).fetchall()
        for request_id, status, request_json, progress_json, result_json, error in persisted:
            # A process cannot resume an in-flight optimization after restart.
            if status in {"queued", "running"}:
                status = "failed"
                error = "API process restarted before this run completed."
                progress = {**json.loads(progress_json), "stage": "failed"}
            else:
                progress = json.loads(progress_json)
            runs[request_id] = {
                "status": status,
                "request": json.loads(request_json),
                "progress": progress,
                "error": error,
            }
            if result_json:
                runs[request_id]["result"] = json.loads(result_json)
        for request_id in list(runs):
            interrupted = (
                runs[request_id]["status"] == "failed"
                and runs[request_id].get("error")
                == "API process restarted before this run completed."
            )
            if interrupted:
                persist(request_id)

    def worker(run_id: str, request: RunRequest):
        def update_progress(progress: dict):
            with lock:
                runs[run_id]["progress"] = progress
            persist(run_id)
        with lock:
            runs[run_id]["status"] = "running"
        persist(run_id)
        from .agent import GeminiAgent, MockAgent
        from .orchestrator import run_agentic_workflow
        try:
            mode = request.agent
            if mode == "auto":
                import os
                mode = "gemini" if os.getenv("GEMINI_API_KEY") else "mock"
            agent = GeminiAgent() if mode == "gemini" else MockAgent()
            result = run_agentic_workflow(
                db_path, optimizer_module, agent,
                max_iterations=request.max_iterations, horizon=request.horizon,
                progress_callback=update_progress,
            )
            with lock:
                runs[run_id].update({
                    "status": "completed", "result": result,
                    "progress": {"stage": "completed", "iteration": result["iterations"],
                                 "total_iterations": request.max_iterations},
                })
            persist(run_id)
        except Exception as exc:
            with lock:
                runs[run_id].update({
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "progress": {**runs[run_id].get("progress", {}), "stage": "failed"},
                })
            persist(run_id)

    @app.get("/api/health")
    def health():
        path = Path(db_path)
        if not path.is_file():
            return {"status": "not_ready", "database_exists": False, "database_ready": False}
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
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
            return {
                "status": "ok" if ready else "not_ready",
                "database_exists": True,
                "database_ready": ready,
                "missing_tables": sorted(required - tables),
            }
        except sqlite3.Error as exc:
            return {"status": "not_ready", "database_exists": True,
                    "database_ready": False, "error": str(exc)}

    @app.post("/api/run", status_code=202)
    def start_run(request: RunRequest):
        if not Path(db_path).is_file():
            raise HTTPException(500, "Configured microgrid database does not exist")
        run_id = "REQ-" + uuid4().hex[:12].upper()
        with lock:
            runs[run_id] = {
                "status": "queued", "request": request.model_dump(),
                "progress": {"stage": "queued", "iteration": 0,
                             "total_iterations": request.max_iterations},
            }
        persist(run_id)
        executor.submit(worker, run_id, request)
        return {"request_id": run_id, "status": "queued"}

    @app.get("/api/status/{run_id}")
    def status(run_id: str):
        with lock:
            item = runs.get(run_id)
            if item is None:
                raise HTTPException(404, "Unknown request ID")
            return {k: v for k, v in item.items() if k != "result"}

    @app.get("/api/result/{run_id}")
    def result(run_id: str):
        with lock:
            item = runs.get(run_id)
            if item is None:
                raise HTTPException(404, "Unknown request ID")
            if item["status"] != "completed":
                raise HTTPException(409, f"Run status is {item['status']}")
            return item["result"]

    @app.get("/api/report/{run_id}")
    def report(run_id: str):
        with lock:
            item = runs.get(run_id)
            if item is None:
                raise HTTPException(404, "Unknown request ID")
            if item["status"] != "completed":
                raise HTTPException(409, f"Run status is {item['status']}")
            return {"agent_run_id": item["result"]["agent_run_id"],
                    "report": item["result"]["report"],
                    "report_source": item["result"]["report_source"]}

    return app
