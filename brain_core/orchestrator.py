"""Bounded optimization ↔ agent feedback loop with deterministic validation."""
from __future__ import annotations
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .protocol import build_snapshot, to_agent_payload, validate_agent_decision
from .patterns import scan_surplus_patterns
from .surplus import fallback_allocation, apply_agent_policy

DEFAULT_PARAMS = {
    "peak_penalty": 1.0,
    "curtailment_penalty": 0.01,
    "flex_risk_penalty": 300.0,
    "shedding_scale": 1.0,
}


def _fixed_reference_score(conn: sqlite3.Connection, run_id: str,
                           snapshot: dict, optimizer_module) -> float:
    """Compare candidates against one frozen reference objective.

    This prevents comparing objective values produced with different AI-proposed
    weights. It reproduces the optimizer's original objective coefficients.
    """
    p = optimizer_module
    peak = snapshot["power"]["max_grid_import_kw"]
    energy = snapshot["energy_kwh"]
    curtail = energy["pv_curtailed"]
    degradation = snapshot["battery"]["estimated_degradation_cost_inr"]
    shedding = 0.0
    risk = 0.0
    rows = conn.execute("""
        SELECT l.interval_start,l.provider_asset_id,l.shed_power_kw,
               COALESCE(v.confidence,0.0)
        FROM optimization_load_result l
        LEFT JOIN flexibility_verification v
          ON v.interval_start=l.interval_start
         AND v.provider_asset_id=l.provider_asset_id
        WHERE l.optimization_run_id=?
    """, (run_id,)).fetchall()
    for _, asset, shed_kw, confidence in rows:
        shed_kwh = float(shed_kw) * p.DT_H
        shedding += float(p.FLEX_ASSETS.get(asset, 0.0)) * shed_kwh
        risk += (1.0 - min(1.0, max(0.0, float(confidence)))) * shed_kwh
    return (
        p.DEFAULT_PEAK_PENALTY * peak
        + shedding
        + p.DEFAULT_CURTAILMENT_PENALTY * curtail
        + degradation
        + p.DEFAULT_FLEX_RISK_PENALTY * risk
    )


def run_agentic_workflow(db_path: str, optimizer_module, agent,
                         max_iterations: int = 3,
                         horizon: int | None = None,
                         initial_parameters: dict[str, float] | None = None,
                         tolerance: float = 1e-5) -> dict[str, Any]:
    if max_iterations < 1 or max_iterations > 12:
        raise ValueError("max_iterations must be between 1 and 12")
    params = dict(DEFAULT_PARAMS)
    if initial_parameters:
        params.update(initial_parameters)
    agent_run_id = "AGENT-" + uuid.uuid4().hex[:12].upper()
    history: list[dict[str, Any]] = []
    patterns = []
    best = None
    best_score = float("inf")
    termination = "iteration_limit"
    decisions = []
    started = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        required = {
            "interval_summary", "measurement", "battery_state", "dispatch",
            "battery_degradation_model", "flexibility_declaration",
            "flexibility_verification", "optimization_run", "optimization_result",
            "optimization_constraint_check", "agent_run",
        }
        present = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        missing = sorted(required - present)
        if missing:
            raise RuntimeError("Database is missing required hurdle tables: " + ", ".join(missing))
        patterns = scan_surplus_patterns(conn)
        for iteration in range(1, max_iterations + 1):
            run_id = optimizer_module.solve(
                conn, horizon=horizon,
                peak_penalty=params["peak_penalty"],
                curtailment_penalty=params["curtailment_penalty"],
                flex_risk_penalty=params["flex_risk_penalty"],
                shedding_scale=params["shedding_scale"],
            )
            snapshot = build_snapshot(conn, run_id)
            score = _fixed_reference_score(conn, run_id, snapshot, optimizer_module)
            snapshot["comparison_score_fixed_reference"] = score

            candidate_record = {
                "iteration": iteration,
                "optimizer_run_id": run_id,
                "fixed_reference_score": score,
                "peak_grid_import_kw": snapshot["power"]["max_grid_import_kw"],
                "load_shed_kwh": snapshot["energy_kwh"]["load_shed"],
                "pv_curtailed_kwh": snapshot["energy_kwh"]["pv_curtailed"],
                "parameters": dict(params),
            }
            history.append(candidate_record)
            if score < best_score - tolerance:
                best_score = score
                best = snapshot

            payload = to_agent_payload(
                snapshot, iteration, max_iterations, dict(params), history, patterns
            )
            raw_decision = agent.evaluate(payload)
            try:
                decision = validate_agent_decision(raw_decision)
            except ValueError as exc:
                decision = {
                    "action": "stop", "parameter_updates": {},
                    "reason": f"Rejected malformed/unsafe agent decision: {exc}",
                    "stop_reason": "invalid_agent_response",
                    "surplus_policy": None,
                }
            decisions.append({
                "iteration": iteration,
                "action": decision["action"],
                "reason": decision["reason"],
                "parameter_updates": decision["parameter_updates"],
                "surplus_policy": decision["surplus_policy"],
            })

            if decision["action"] == "stop":
                termination = decision["stop_reason"] or "agent_requested_stop"
                break
            if iteration == max_iterations:
                termination = "iteration_limit"
                break

            updates = decision["parameter_updates"]
            if not updates:
                termination = "agent_requested_continue_without_changes"
                break
            params.update(updates)

        if best is None:
            raise RuntimeError("No validated optimization candidate was produced")

        latest_policy = next(
            (d["surplus_policy"] for d in reversed(decisions) if d.get("surplus_policy")),
            None,
        )
        fallback = fallback_allocation(conn, best)
        surplus_allocation = (
            apply_agent_policy(latest_policy, fallback)
            if patterns and latest_policy else fallback
        )
        report_context = {
            "agent_run_id": agent_run_id,
            "agent": getattr(agent, "name", agent.__class__.__name__),
            "iterations": len(history),
            "termination_reason": termination,
            "best_candidate": best,
            "surplus_allocation": surplus_allocation,
            "relevant_patterns": patterns[:8],
            "candidate_history": history,
            "agent_decisions": decisions,
            "final_parameters": params,
        }
        try:
            report = agent.generate_report(report_context)
            report_source = "gemini" if agent.__class__.__name__ == "GeminiAgent" else "mock"
        except Exception as exc:
            report = (
                "REPORT GENERATION FALLBACK\n"
                "The optimization completed, but the configured AI report call failed.\n"
                f"Reason: {type(exc).__name__}: {exc}\n\n"
                + json.dumps(report_context, indent=2, default=str)
            )
            report_source = "local_fallback"

        completed = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO agent_run
            (agent_run_id,started_at,completed_at,agent_name,model_version,
             run_status,input_snapshot,output_summary)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            agent_run_id, started, completed,
            getattr(agent, "name", agent.__class__.__name__),
            getattr(agent, "model", "deterministic"),
            "completed", json.dumps({
                "db_path": db_path, "horizon": horizon,
                "max_iterations": max_iterations,
                "initial_parameters": DEFAULT_PARAMS,
                "relevant_patterns": patterns[:8],
            }),
            json.dumps({
                "best_optimizer_run_id": best["run_id"],
                "iterations": len(history), "termination": termination,
                "report_source": report_source,
                "candidate_history": history,
                "agent_decisions": decisions,
                "surplus_allocation": surplus_allocation,
                "report": report,
            }),
        ))
        conn.commit()
        return {
            "agent_run_id": agent_run_id,
            "status": "completed",
            "iterations": len(history),
            "termination_reason": termination,
            "agent": getattr(agent, "name", agent.__class__.__name__),
            "best_candidate": best,
            "surplus_allocation": surplus_allocation,
            "relevant_patterns": patterns[:8],
            "candidate_history": history,
            "agent_decisions": decisions,
            "report": report,
            "report_source": report_source,
            "database": db_path,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
