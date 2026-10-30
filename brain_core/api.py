"""Local FastAPI interface for the microgrid brain."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
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
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
        allow_headers=["*"], allow_credentials=False,
    )
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="microgrid-run")
    runs: dict[str, dict] = {}
    lock = Lock()

    def worker(run_id: str, request: RunRequest):
        with lock:
            runs[run_id]["status"] = "running"
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
            )
            with lock:
                runs[run_id].update({"status": "completed", "result": result})
        except Exception as exc:
            with lock:
                runs[run_id].update({
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                })

    @app.get("/api/health")
    def health():
        return {"status": "ok", "database_exists": Path(db_path).is_file()}

    @app.post("/api/run", status_code=202)
    def start_run(request: RunRequest):
        if not Path(db_path).is_file():
            raise HTTPException(500, "Configured microgrid database does not exist")
        run_id = "REQ-" + uuid4().hex[:12].upper()
        with lock:
            runs[run_id] = {"status": "queued", "request": request.model_dump()}
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
