"""Compact, versioned Microgrid Decision Protocol (MDP) and DB summarization."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .aleph import ALEPH_WEIGHTS, build_run_timeseries

DT_H = 0.25
OPTIMIZER_PARAMETER_BOUNDS = {
    "peak_penalty": (0.01, 100.0),
    "curtailment_penalty": (0.0, 1000.0),
    "flex_risk_penalty": (0.0, 10000.0),
    "shedding_scale": (0.0, 100.0),
}


def build_snapshot(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    run = conn.execute("""
        SELECT objective_value, objective_peak_component, objective_shedding_component,
               objective_curtailment_component, objective_degradation_component,
               objective_flexibility_component, status
        FROM optimization_run WHERE optimization_run_id=?
    """, (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"Unknown optimization run: {run_id}")

    rows = conn.execute("""
        SELECT r.interval_start,r.grid_import_kw,r.battery_charge_kw,
               r.battery_discharge_kw,r.pv_curtailment_kw,
               r.battery_soc_start_pct,r.battery_soc_end_pct,
               r.load_shed_kw,r.estimated_degradation_cost,
               s.pv_generation_kw,s.total_load_kw,s.network_active_losses_kw
        FROM optimization_result r
        JOIN interval_summary s USING(interval_start)
        WHERE r.optimization_run_id=?
        ORDER BY r.interval_start
    """, (run_id,)).fetchall()
    if not rows:
        raise ValueError(f"Optimization run {run_id} has no interval results")
    checks = conn.execute("""
        SELECT COUNT(*),COALESCE(SUM(CASE WHEN passed=0 THEN 1 ELSE 0 END),0)
        FROM optimization_constraint_check WHERE optimization_run_id=?
    """, (run_id,)).fetchone()
    check_total, check_failed = int(checks[0]), int(checks[1])
    if check_total == 0 or check_failed:
        raise ValueError(
            f"Candidate {run_id} failed validation: {check_failed}/{check_total} failed checks"
        )

    def energy(col: int) -> float:
        return sum(float(row[col] or 0.0) for row in rows) * DT_H

    grid_import = energy(1)
    charge = energy(2)
    discharge = energy(3)
    curtailment = energy(4)
    load_shed = energy(7)
    degradation_cost = sum(float(row[8] or 0.0) for row in rows)
    pv_gross = energy(9)
    load_gross = energy(10)
    losses = energy(11)
    load_served = max(0.0, load_gross - load_shed)
    pv_used = max(0.0, pv_gross - curtailment)
    external_input = grid_import + pv_used

    # This is a useful-load utilization ratio, not a claim of thermodynamic
    # efficiency. It uses external energy input once; battery discharge is an
    # internal transfer and is deliberately not counted as a new source.
    eta = (load_served / external_input) if external_input > 1e-12 else None

    # This prototype has no export decision variable or export telemetry.
    # Therefore export revenue and eta_cost are intentionally unavailable.
    max_grid = max(float(r[1]) for r in rows)
    avg_grid = sum(float(r[1]) for r in rows) / len(rows)
    soc_min = min(float(r[6]) for r in rows)
    soc_max = max(float(r[6]) for r in rows)
    curtail_kw_peak = max(float(r[4]) for r in rows)

    aleph_data = build_run_timeseries(conn, run_id)
    cost_rows = conn.execute("""
        SELECT SUM(import_cost),SUM(export_credit),SUM(net_cost),MAX(currency_code)
        FROM optimization_energy_cost WHERE optimization_run_id=?
    """, (run_id,)).fetchone()
    cost = {
        "import_cost": float(cost_rows[0] or 0.0),
        "export_credit": float(cost_rows[1] or 0.0),
        "net_cost": float(cost_rows[2] or 0.0),
        "currency_code": cost_rows[3] or "INR",
        "baseline_net_cost": sum(aleph_data["series"]["baseline_interval_cost"]),
        "data_quality": aleph_data.get("data_quality", "unknown"),
    }
    economic_component = aleph_data.get("aleph_summary", {}).get("components", {}).get("economic_performance")

    return {
        "run_id": run_id,
        "status": str(run[6]),
        "validation": {"passed": True, "checks": check_total, "failed": check_failed},
        "intervals": len(rows),
        "objective": {
            "value": float(run[0]),
            "components": {
                "peak": float(run[1]),
                "flexible_shedding": float(run[2]),
                "pv_curtailment": float(run[3]),
                "battery_degradation": float(run[4]),
                "flexibility_risk": float(run[5]),
            },
        },
        "power": {
            "max_grid_import_kw": max_grid,
            "average_grid_import_kw": avg_grid,
            "peak_pv_curtailment_kw": curtail_kw_peak,
        },
        "energy_kwh": {
            "grid_import": grid_import,
            "solar_generation_gross": pv_gross,
            "solar_used": pv_used,
            "load_demand_gross": load_gross,
            "load_served": load_served,
            "load_shed": load_shed,
            "network_losses_baseline": losses,
            "battery_charge_internal": charge,
            "battery_discharge_internal": discharge,
            "pv_curtailed": curtailment,
            "external_input": external_input,
            "grid_export": 0.0,
            "unallocated_surplus": None,
            "grid_import_cost": cost["import_cost"],
            "grid_export_credit": cost["export_credit"],
        },
        "cost": cost,
        "battery": {
            "charge_kwh": charge,
            "discharge_kwh": discharge,
            "soc_min_pct": soc_min,
            "soc_max_pct": soc_max,
            "estimated_degradation_cost_inr": degradation_cost,
        },
        "metrics": {
            "eta_mainsys": eta,
            "eta_mainsys_definition": "load_served_kwh / (grid_import_kwh + solar_generation_used_kwh); useful-load utilization proxy, not full export-aware system efficiency",
            "eta_cost_mainsys": (economic_component / 100.0
                                  if economic_component is not None else None),
            "aleph_mainsys": aleph_data.get("aleph_summary", {}).get("score"),
            "aleph_components": aleph_data.get("aleph_summary", {}).get("components", {}),
            "aleph_weights": ALEPH_WEIGHTS,
            "aleph_data_quality": cost["data_quality"],
            "unavailable_reason": "Aleph requires all six components; missing component data is not silently reweighted.",
        },
        "aleph": aleph_data.get("aleph_summary"),
        "limitations": [
            "Aggregate active-power scheduling with measured baseline network losses held fixed.",
            "No nonlinear AC/thermal re-solve for candidate dispatch.",
            "The tariff profile is synthetic; the optimizer has no grid-export decision variable.",
            "No thermal reservoir model in the supplied hurdle database.",
            "Battery discharge is an internal transfer, not a second external energy input.",
            "Synthetic flexibility proxies are not field measurements.",
        ],
    }


def to_agent_payload(snapshot: dict[str, Any], iteration: int, max_iterations: int,
                     parameters: dict[str, float], history: list[dict[str, Any]],
                     patterns: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep the decision prompt compact and versioned; omit verbose interval rows."""
    return {
        "protocol": "MDP",
        "version": 1,
        "task": "evaluate_microgrid_candidate",
        "iteration": iteration,
        "iteration_limit": max_iterations,
        "candidate": snapshot,
        "optimizer_parameters": parameters,
        "recent_history": history[-4:],
        "relevant_patterns": patterns[:8],
        "allowed_parameter_changes": {
            "peak_penalty": {"min": 0.01, "max": 100.0},
            "curtailment_penalty": {"min": 0.0, "max": 1000.0},
            "flex_risk_penalty": {"min": 0.0, "max": 10000.0},
            "shedding_scale": {"min": 0.0, "max": 100.0},
        },
        "rules": [
            "Do not invent measurements, tariffs, export capability, or reservoir capacity.",
            "Never propose violating physical constraints.",
            "Battery discharge is internal storage transfer, not an external energy source.",
            "Use tariff and Aleph values exactly as supplied; label the configured tariff synthetic when indicated.",
            "The optimizer has no grid-export decision variable; do not claim it scheduled exports.",
            "Only propose changes to the four listed optimizer parameters.",
            "Return JSON matching the requested response schema.",
        ],
    }


def validate_agent_decision(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Agent response must be a JSON object")
    action = data.get("action")
    if action not in {"continue", "stop"}:
        raise ValueError("Agent action must be 'continue' or 'stop'")
    proposal = data.get("parameter_updates", {})
    if proposal is None:
        proposal = {}
    if not isinstance(proposal, dict):
        raise ValueError("parameter_updates must be an object")
    allowed = OPTIMIZER_PARAMETER_BOUNDS
    clean = {}
    for key, value in proposal.items():
        if key not in allowed:
            raise ValueError(f"Unsupported optimizer parameter: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        lo, hi = allowed[key]
        value = float(value)
        if not lo <= value <= hi:
            raise ValueError(f"{key} outside permitted range [{lo}, {hi}]")
        clean[key] = value
    policy = data.get("surplus_policy")
    clean_policy = None
    if policy is not None:
        if not isinstance(policy, dict):
            raise ValueError("surplus_policy must be an object")
        store_pct = policy.get("store_pct", 100.0)
        export_pct = policy.get("export_pct", 0.0)
        for key, value in (("store_pct", store_pct), ("export_pct", export_pct)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"surplus_policy.{key} must be numeric")
            if not 0.0 <= float(value) <= 100.0:
                raise ValueError(f"surplus_policy.{key} must be between 0 and 100")
        if float(store_pct) + float(export_pct) > 100.0 + 1e-8:
            raise ValueError("surplus policy percentages cannot sum above 100")
        clean_policy = {"store_pct": float(store_pct), "export_pct": float(export_pct)}
    return {
        "action": action,
        "parameter_updates": clean,
        "surplus_policy": clean_policy,
        "reason": str(data.get("reason", ""))[:2000],
        "stop_reason": str(data.get("stop_reason", ""))[:500],
    }
