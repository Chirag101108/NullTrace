"""Bounded optimization ↔ agent feedback loop with deterministic validation."""
from __future__ import annotations
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .protocol import build_snapshot, to_agent_payload, validate_agent_decision
from .patterns import scan_surplus_patterns
from .surplus import fallback_allocation, apply_agent_policy

DEFAULT_PARAMS = {
    "peak_penalty": 1.0,
    "curtailment_penalty": 0.01,
    "flex_risk_penalty": 300.0,
    "shedding_scale": 1.0,
}
CONVERGENCE_PATIENCE = 2


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
    tariff_cost = float(snapshot["cost"]["net_cost"])
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
        tariff_cost
        + p.DEFAULT_PEAK_PENALTY * peak
        + shedding
        + p.DEFAULT_CURTAILMENT_PENALTY * curtail
        + degradation
        + p.DEFAULT_FLEX_RISK_PENALTY * risk
    )


def run_agentic_workflow(db_path: str, optimizer_module, agent,
                         max_iterations: int = 3,
                         horizon: int | None = None,
                         initial_parameters: dict[str, float] | None = None,
                         tolerance: float = 1e-5,
                         progress_callback: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    if max_iterations < 1 or max_iterations > 12:
        raise ValueError("max_iterations must be between 1 and 12")
    params = dict(DEFAULT_PARAMS)
    if initial_parameters:
        params.update(initial_parameters)
    agent_run_id = "AGENT-" + uuid.uuid4().hex[:12].upper()
    history: list[dict[str, Any]] = []
    patterns = []
    best = None
    best_aleph: float | None = None
    best_reference_score = float("inf")
    stale_iterations = 0
    termination = "iteration_limit"
    decisions = []
    started = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        from .tariffs import ensure_tariff_schema, load_active_tariff
        ensure_tariff_schema(conn)
        required = {
            "interval_summary", "measurement", "weather", "battery_state", "dispatch",
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
        timestamps = [row[0] for row in conn.execute(
            "SELECT interval_start FROM interval_summary ORDER BY interval_start"
            + (" LIMIT ?" if horizon is not None else ""),
            (horizon,) if horizon is not None else (),
        )]
        tariff = load_active_tariff(conn, timestamps)
        patterns = scan_surplus_patterns(conn)
        for iteration in range(1, max_iterations + 1):
            if progress_callback:
                progress_callback({"stage": "optimizing", "iteration": iteration,
                                   "total_iterations": max_iterations})
            run_id = optimizer_module.solve(
                conn, horizon=horizon,
                peak_penalty=params["peak_penalty"],
                curtailment_penalty=params["curtailment_penalty"],
                flex_risk_penalty=params["flex_risk_penalty"],
                shedding_scale=params["shedding_scale"],
                tariff_rates=tariff["rates"], tariff_snapshot=tariff,
            )
            snapshot = build_snapshot(conn, run_id)
            reference_score = _fixed_reference_score(conn, run_id, snapshot, optimizer_module)
            aleph_score = snapshot["metrics"].get("aleph_mainsys")
            aleph_score = float(aleph_score) if aleph_score is not None else None
            snapshot["comparison_score_fixed_reference"] = reference_score

            if aleph_score is None:
                # Some short horizons or incomplete source data cannot support
                # every Aleph component. Keep the workflow usable without
                # inventing or renormalizing a score; use the frozen objective
                # only when Aleph is unavailable for all candidates.
                improved = best_aleph is None and reference_score < best_reference_score - tolerance
                selection_basis = "fixed_reference_fallback_aleph_unavailable"
            else:
                improved = (
                    best_aleph is None or aleph_score > best_aleph + tolerance
                    or (abs(aleph_score - best_aleph) <= tolerance
                        and reference_score < best_reference_score - tolerance)
                )
                selection_basis = "aleph_then_fixed_reference_tiebreak"
            candidate_record = {
                "iteration": iteration,
                "optimizer_run_id": run_id,
                "aleph_score": aleph_score,
                "selection_basis": selection_basis,
                "fixed_reference_score": reference_score,
                "improved_best": improved,
                "peak_grid_import_kw": snapshot["power"]["max_grid_import_kw"],
                "load_shed_kwh": snapshot["energy_kwh"]["load_shed"],
                "pv_curtailed_kwh": snapshot["energy_kwh"]["pv_curtailed"],
                "net_energy_cost": snapshot["cost"]["net_cost"],
                "currency_code": snapshot["cost"]["currency_code"],
                "parameters": dict(params),
            }
            history.append(candidate_record)
            if improved:
                best_aleph = aleph_score
                best_reference_score = reference_score
                best = snapshot
                stale_iterations = 0
            else:
                stale_iterations += 1

            payload = to_agent_payload(
                snapshot, iteration, max_iterations, dict(params), history, patterns
            )
            if progress_callback:
                progress_callback({"stage": "agent_feedback", "iteration": iteration,
                                   "total_iterations": max_iterations})
            if getattr(agent, "supports_database_tools", False):
                from .agent_tools import AgentDatabaseTools
                tools = AgentDatabaseTools(
                    db_path, run_id, agent_run_id, iteration,
                    allow_parameter_writes=aleph_score is not None,
                )

                def execute_agent_tool(name, arguments):
                    if progress_callback:
                        progress_callback({"stage": f"agent_tool:{name}",
                                           "iteration": iteration,
                                           "total_iterations": max_iterations})
                    return tools.execute(name, arguments)

                raw_decision = agent.evaluate(payload, tool_executor=execute_agent_tool)
                if isinstance(raw_decision, dict):
                    written_updates = tools.get_parameter_updates()
                    response_updates = raw_decision.get("parameter_updates") or {}
                    if not isinstance(response_updates, dict):
                        raw_decision = {
                            **raw_decision, "parameter_updates": [],
                        }
                    elif any(
                        key in written_updates and
                        (isinstance(value, bool)
                         or not isinstance(value, (int, float))
                         or float(value) != written_updates[key])
                        for key, value in response_updates.items()
                    ):
                        raw_decision = {
                            "action": "stop", "parameter_updates": {},
                            "surplus_policy": None,
                            "reason": "Rejected conflicting parameter values from tool writes and decision JSON.",
                            "stop_reason": "conflicting_parameter_updates",
                        }
                    elif written_updates:
                        raw_decision = {
                            **raw_decision,
                            "parameter_updates": {**response_updates, **written_updates},
                        }
            else:
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
            if progress_callback:
                progress_callback({"stage": "candidate_validated", "iteration": iteration,
                                   "total_iterations": max_iterations})

            if decision["action"] == "stop":
                termination = decision["stop_reason"] or "agent_requested_stop"
                break
            if stale_iterations >= CONVERGENCE_PATIENCE:
                termination = "converged_no_improvement"
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
            if progress_callback:
                progress_callback({"stage": "generating_report", "iteration": len(history),
                                   "total_iterations": max_iterations})
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
