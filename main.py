#!/usr/bin/env python3
"""AfterHours Microgrid Brain: optimizer orchestration, AI loop, report and API.

Examples:
  python main.py
  python main.py run --agent mock --max-iterations 2
  GEMINI_API_KEY=... python main.py run --agent gemini
  python main.py serve --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PACKAGE_DIR = ROOT / "microgrid_afterhours_hurdle_v1"
DEFAULT_DB = PACKAGE_DIR / "microgrid.sqlite"


def load_optimizer():
    if not (PACKAGE_DIR / "optimizer.py").is_file():
        raise SystemExit(f"Optimizer not found: {PACKAGE_DIR / 'optimizer.py'}")
    sys.path.insert(0, str(PACKAGE_DIR))
    import optimizer
    return optimizer


def select_agent(mode: str):
    from brain_core.agent import GeminiAgent, MockAgent
    if mode == "mock":
        return MockAgent()
    if mode == "gemini":
        return GeminiAgent()
    if mode == "auto":
        return GeminiAgent() if os.getenv("GEMINI_API_KEY") else MockAgent()
    raise ValueError(f"Unsupported agent mode: {mode}")


def run_command(args):
    from brain_core.orchestrator import run_agentic_workflow
    from brain_core.db_copy import create_working_copy
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.is_file():
        raise SystemExit(
            f"Database not found: {db_path}\n"
            "The default is the hurdle database at microgrid_afterhours_hurdle_v1/microgrid.sqlite."
        )
    agent = select_agent(args.agent)
    optimizer = load_optimizer()
    run_db = create_working_copy(db_path, f"CLI-{os.urandom(6).hex().upper()}")
    result = run_agentic_workflow(
        str(run_db), optimizer, agent,
        max_iterations=args.max_iterations, horizon=args.horizon,
    )
    output = {
        "agent_run_id": result["agent_run_id"],
        "status": result["status"],
        "agent": result["agent"],
        "iterations": result["iterations"],
        "termination_reason": result["termination_reason"],
        "best_optimizer_run_id": result["best_candidate"]["run_id"],
        "validation": result["best_candidate"]["validation"],
        "metrics": result["best_candidate"]["metrics"],
        "relevant_pattern_count": len(result["relevant_patterns"]),
        "power": result["best_candidate"]["power"],
        "energy_kwh": result["best_candidate"]["energy_kwh"],
        "surplus_allocation": result["surplus_allocation"],
        "report_source": result["report_source"],
        "working_database": str(run_db),
    }
    print(json.dumps(output, indent=2))
    print("\n" + result["report"])
    if args.report_out:
        Path(args.report_out).expanduser().write_text(result["report"], encoding="utf-8")
        print(f"\nReport written to {Path(args.report_out).expanduser().resolve()}")


def serve_command(args):
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install API dependencies first: pip install -r requirements.txt") from exc
    from brain_core.api import create_app
    optimizer = load_optimizer()
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.is_file():
        raise SystemExit(f"Database not found: {db_path}")
    default_agent = os.getenv("AFTERHOURS_DEFAULT_AGENT", "auto").lower()
    if default_agent not in {"auto", "mock", "gemini"}:
        raise SystemExit("AFTERHOURS_DEFAULT_AGENT must be auto, mock, or gemini")
    app = create_app(str(db_path), optimizer, default_agent=default_agent)
    print(f"AfterHours Brain API: http://{args.host}:{args.port}")
    print("Docs: /docs | Health: /api/health")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def build_parser():
    parser = argparse.ArgumentParser(description="AfterHours Microgrid Brain")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Run optimization + bounded AI feedback loop")
    run.add_argument("--db", default=str(DEFAULT_DB))
    run.add_argument("--agent", choices=("auto", "gemini", "mock"), default="auto")
    run.add_argument("--max-iterations", type=int, default=3)
    run.add_argument("--horizon", type=int, default=None)
    run.add_argument("--report-out", default=None)
    run.set_defaults(func=run_command)
    serve = sub.add_parser("serve", help="Run the local HTTP API")
    serve.add_argument("--db", default=str(DEFAULT_DB))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=serve_command)
    return parser


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] not in {"run", "serve", "-h", "--help"}:
        argv = ["run", *argv]
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
