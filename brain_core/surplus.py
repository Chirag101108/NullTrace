"""Surplus-energy evidence scan and conservative advisory fallback.

This module does not dispatch export or alter the optimizer's LP. The current
dataset has no export actuator/tariff model and no thermal reservoir inventory.
"""
from __future__ import annotations
import sqlite3


def fallback_allocation(conn: sqlite3.Connection, snapshot: dict,
                        reserve_soc_pct: float = 30.0,
                        export_limit_kw: float = 0.0) -> dict:
    rows = conn.execute("""
        SELECT r.interval_start,r.pv_curtailment_kw,r.battery_soc_end_pct,
               b.capacity_kwh
        FROM optimization_result r
        LEFT JOIN battery_state b USING(interval_start)
        WHERE r.optimization_run_id=?
        ORDER BY r.interval_start
    """, (snapshot["run_id"],)).fetchall()
    dt_h = 0.25
    candidate_surplus = sum(max(0.0, float(r[1])) for r in rows) * dt_h
    # Conservative headroom uses the final SOC and the lowest recorded capacity.
    if rows:
        final_soc = float(rows[-1][2])
        capacity = min(float(r[3]) for r in rows if r[3] is not None)
    else:
        final_soc, capacity = 100.0, 0.0
    usable_soc_ceiling = 95.0
    storage_headroom = max(
        0.0, capacity * (usable_soc_ceiling - max(final_soc, reserve_soc_pct)) / 100.0
    )
    battery_power_headroom = 30.0 * dt_h * len(rows)
    storage_ceiling = min(candidate_surplus, storage_headroom, battery_power_headroom)
    # The LP has not been modified to absorb this curtailed PV, and its terminal
    # SOC is fixed. Do not present spare capacity as a dispatchable allocation.
    return {
        "mode": "deterministic_fallback",
        "candidate_surplus_kwh": round(candidate_surplus, 6),
        "recommended_storage_kwh": None,
        "storage_capacity_ceiling_kwh": round(storage_ceiling, 6),
        "recommended_export_kwh": None,
        "unallocated_kwh": round(candidate_surplus, 6),
        "reservoirs": {
            "battery": {
                "available_headroom_kwh": round(storage_headroom, 6),
                "recommended_kwh": None,
            },
            "thermal": {"available_capacity_kwh": None, "recommended_kwh": 0.0,
                        "status": "not modeled in supplied database"},
        },
        "assumptions": [
            f"Battery reserve floor assumed at {reserve_soc_pct:.1f}% SOC.",
            "Storage headroom is a theoretical ceiling, not an executable charge schedule.",
            "No additional battery charging is recommended until the LP is extended and re-solved with surplus absorption and terminal-SOC constraints.",
            "Grid export is disabled because the database does not establish a safe export rating or export agreement.",
            "Candidate surplus is measured as optimizer-curtailed PV energy; it is not proof of dispatchable export energy.",
        ],
    }


def apply_agent_policy(policy: dict | None, fallback: dict) -> dict:
    if not policy:
        return fallback
    # The current project has no verified export capability. Clamp export to zero.
    requested_store = float(policy.get("store_pct", 100.0))
    requested_export = float(policy.get("export_pct", 0.0))
    total = requested_store + requested_export
    if total > 100.0 + 1e-8:
        raise ValueError("Surplus allocation percentages cannot exceed 100%")
    surplus = fallback["candidate_surplus_kwh"]
    store_ceiling = min(surplus, surplus * requested_store / 100.0,
                        fallback["reservoirs"]["battery"]["available_headroom_kwh"])
    # Policy is exposed as a recommendation only; no physical dispatch is applied.
    store = None
    export = None
    unallocated = surplus
    return {
        **fallback,
        "mode": "agent_guided_advisory",
        "agent_policy": policy,
        "recommended_storage_kwh": None,
        "agent_proposed_storage_ceiling_kwh": round(store_ceiling, 6),
        "recommended_export_kwh": None,
        "unallocated_kwh": round(unallocated, 6),
        "assumptions": fallback["assumptions"] + [
            "Agent policy is advisory and independently clamped to theoretical battery headroom.",
            "No storage/export dispatch was applied; the LP needs an explicit surplus allocation model.",
            "Requested grid export was not applied because safe export capability is unverified.",
        ],
    }
