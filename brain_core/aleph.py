"""Auditable Aleph scoring and Chart.js-ready optimization time series."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

DT_H = 0.25
ALEPH_WEIGHTS = {
    "energy_adequacy": 0.25,
    "physical_efficiency": 0.20,
    "renewable_utilization": 0.15,
    "storage_performance": 0.15,
    "economic_performance": 0.15,
    "network_compliance": 0.10,
}


def _score(components: dict[str, float | None]) -> float | None:
    if any(components.get(name) is None for name in ALEPH_WEIGHTS):
        return None
    return sum(ALEPH_WEIGHTS[name] * float(components[name])
               for name in ALEPH_WEIGHTS)


def build_run_timeseries(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Return actual persisted interval values plus transparent Aleph components."""
    run = conn.execute("""
        SELECT r.interval_start,r.grid_import_kw,r.battery_charge_kw,
               r.battery_discharge_kw,r.pv_curtailment_kw,r.battery_soc_start_pct,
               r.battery_soc_end_pct,r.load_shed_kw,r.estimated_degradation_cost,
               s.pv_generation_kw,s.total_load_kw,s.network_active_losses_kw,
               s.grid_import_kw,c.grid_import_kwh,c.grid_export_kwh,
               c.import_rate_per_kwh,c.export_rate_per_kwh,c.import_cost,
               c.export_credit,c.net_cost,c.currency_code
        FROM optimization_result r
        JOIN interval_summary s USING(interval_start)
        LEFT JOIN optimization_energy_cost c
          ON c.optimization_run_id=r.optimization_run_id
         AND c.interval_start=r.interval_start
        WHERE r.optimization_run_id=?
        ORDER BY r.interval_start
    """, (run_id,)).fetchall()
    if not run:
        raise ValueError(f"Optimization run {run_id} has no interval results")
    integrity = conn.execute("""
        SELECT COUNT(*),SUM(CASE WHEN passed=0 THEN 1 ELSE 0 END)
        FROM optimization_constraint_check WHERE optimization_run_id=?
    """, (run_id,)).fetchone()
    check_count, failed_checks = int(integrity[0] or 0), int(integrity[1] or 0)
    battery = conn.execute("""
        SELECT replacement_cost,charge_efficiency,discharge_efficiency
        FROM battery_degradation_model WHERE battery_asset_id='BESS-INV-001'
        ORDER BY rowid LIMIT 1
    """).fetchone()
    if not battery:
        return {"run_id": run_id, "available": False,
                "reason": "Battery degradation/efficiency model is missing.",
                "timestamps": [], "datasets": [], "aleph": []}
    replacement_cost, eta_charge, eta_discharge = map(float, battery)
    if replacement_cost <= 0 or not (0 < eta_charge <= 1 and 0 < eta_discharge <= 1):
        raise ValueError("Battery reference cost and efficiencies must be positive and bounded")

    critical_rows = conn.execute("""
        SELECT interval_start,-value FROM measurement
        WHERE asset_id='LOAD-CRIT-001' AND measurement_type_id='active_power_kw'
    """).fetchall()
    critical_demand = {ts: max(0.0, float(v or 0.0)) for ts, v in critical_rows}

    timestamps: list[str] = []
    arrays: dict[str, list[float | None]] = {
        "demand_kw": [], "load_served_kw": [], "load_shed_kw": [],
        "pv_generation_kw": [], "pv_used_kw": [], "pv_curtailed_kw": [],
        "grid_import_kw": [], "grid_export_kw": [], "baseline_grid_kw": [],
        "battery_charge_kw": [], "battery_discharge_kw": [], "battery_soc_pct": [],
        "network_losses_kw": [], "import_rate_per_kwh": [], "export_rate_per_kwh": [],
        "interval_energy_cost": [], "baseline_interval_cost": [],
        "aleph": [],
    }
    component_arrays: dict[str, list[float | None]] = {
        name: [] for name in ALEPH_WEIGHTS
    }
    external_total = pv_used_total = pv_gross_total = 0.0
    served_total = gross_demand_total = critical_served_total = critical_demand_total = 0.0
    degradation_total = 0.0
    baseline_cost_total = candidate_cost_total = 0.0
    capacity_row = conn.execute("""
        SELECT capacity_kwh FROM battery_state ORDER BY interval_start LIMIT 1
    """).fetchone()
    if not capacity_row:
        return {"run_id": run_id, "available": False,
                "reason": "Initial battery capacity is missing.",
                "timestamps": [], "datasets": [], "aleph": []}
    capacity_kwh = float(capacity_row[0])
    initial_soc_pct = float(run[0][5])
    all_checks_passed = check_count > 0 and failed_checks == 0

    for row in run:
        (ts, grid, charge, discharge, curtail, soc_start, soc_end, shed,
         degradation, pv, demand, losses, baseline_grid, import_kwh,
         export_kwh, import_rate, export_rate, import_cost, export_credit,
         net_cost, currency) = row
        cost_present = all(v is not None for v in
                           (import_kwh, export_kwh, import_rate, export_rate,
                            import_cost, export_credit, net_cost))
        grid, charge, discharge = float(grid), float(charge), float(discharge)
        curtail, shed, pv = float(curtail), float(shed), float(pv)
        demand, losses, soc_end = float(demand), float(losses), float(soc_end)
        pv_used = max(0.0, pv-curtail)
        load_served = max(0.0, demand-shed)
        critical = critical_demand.get(str(ts))
        if cost_present:
            candidate_cost_total += float(net_cost)
            baseline_interval_cost = (
                max(0.0, float(baseline_grid)) * float(import_rate) * DT_H
                - max(0.0, -float(baseline_grid)) * float(export_rate) * DT_H
            )
            baseline_cost_total += baseline_interval_cost
        else:
            baseline_interval_cost = None
        external_total += (max(0.0, grid) + pv_used) * DT_H
        pv_used_total += pv_used * DT_H
        pv_gross_total += max(0.0, pv) * DT_H
        served_total += load_served * DT_H
        gross_demand_total += demand * DT_H
        if critical is not None:
            critical_demand_total += critical * DT_H
            critical_served_total += critical * DT_H
        degradation_total += max(0.0, float(degradation or 0.0))

        # At each prefix, adjust for the net change in stored energy. Battery
        # discharge is not added as a new external source. At the terminal SOC,
        # this reduces to the external-energy boundary over the complete horizon.
        stored_delta = (soc_end-initial_soc_pct) * capacity_kwh / 100.0
        effective_external = external_total-stored_delta
        efficiency = None
        if effective_external > 1e-9:
            ratio = served_total/effective_external
            # A value above 100% indicates inconsistent accounting, not a score
            # to conceal by clipping.
            if ratio <= 1.0+1e-6:
                efficiency = max(0.0, min(100.0, ratio*100.0))

        total_service = (100.0*served_total/gross_demand_total
                         if gross_demand_total > 1e-9 else None)
        critical_service = (100.0*critical_served_total/critical_demand_total
                            if critical is not None and critical_demand_total > 1e-9 else None)
        adequacy = (0.5*total_service + 0.5*critical_service
                    if total_service is not None and critical_service is not None else None)
        renewable = (100.0*pv_used_total/pv_gross_total if pv_gross_total > 1e-9 else None)
        storage = max(0.0, min(100.0,
                    100.0*eta_charge*eta_discharge
                    *(1.0-min(1.0,degradation_total/replacement_cost))))
        if cost_present and baseline_cost_total > 1e-9:
            economic = (100.0 if candidate_cost_total <= baseline_cost_total
                        else max(0.0,100.0*baseline_cost_total/candidate_cost_total)
                        if candidate_cost_total > 1e-9 else 100.0)
        else:
            economic = None
        network = 100.0 if all_checks_passed else None
        components = {
            "energy_adequacy": adequacy,
            "physical_efficiency": efficiency,
            "renewable_utilization": renewable,
            "storage_performance": storage,
            "economic_performance": economic,
            "network_compliance": network,
        }
        index = _score(components)
        timestamps.append(str(ts))
        values = {
            "demand_kw": demand, "load_served_kw": load_served,
            "load_shed_kw": shed, "pv_generation_kw": pv,
            "pv_used_kw": pv_used, "pv_curtailed_kw": curtail,
            "grid_import_kw": max(0.0,grid), "grid_export_kw": 0.0,
            "baseline_grid_kw": float(baseline_grid),
            "battery_charge_kw": charge, "battery_discharge_kw": discharge,
            "battery_soc_pct": soc_end, "network_losses_kw": losses,
            "import_rate_per_kwh": float(import_rate) if import_rate is not None else None,
            "export_rate_per_kwh": float(export_rate) if export_rate is not None else None,
            "interval_energy_cost": float(net_cost) if net_cost is not None else None,
            "baseline_interval_cost": baseline_interval_cost,
            "aleph": index,
        }
        for key, value in values.items():
            arrays[key].append(value)
        for key, value in components.items():
            component_arrays[key].append(value)

    final_components = {name: values[-1] for name, values in component_arrays.items()}
    final_score = _score(final_components)
    datasets = [
        {"key": "demand_kw", "label": "Demand", "unit": "kW", "data": arrays["demand_kw"]},
        {"key": "load_served_kw", "label": "Load served", "unit": "kW", "data": arrays["load_served_kw"]},
        {"key": "pv_generation_kw", "label": "PV generation", "unit": "kW", "data": arrays["pv_generation_kw"]},
        {"key": "grid_import_kw", "label": "Grid import", "unit": "kW", "data": arrays["grid_import_kw"]},
        {"key": "battery_soc_pct", "label": "Battery SOC", "unit": "%", "data": arrays["battery_soc_pct"]},
        {"key": "aleph", "label": "Aleph", "unit": "score / 100", "data": arrays["aleph"]},
    ]
    snapshot_row = conn.execute("""
        SELECT config_json FROM optimization_tariff_snapshot WHERE optimization_run_id=?
    """, (run_id,)).fetchone()
    snapshot_config = json.loads(snapshot_row[0]) if snapshot_row else {}
    return {
        "run_id": run_id,
        "available": True,
        "timestamps": timestamps,
        "units": {"power": "kW", "energy": "kWh", "tariff": f"{currency}/kWh",
                  "aleph": "0–100"},
        "currency_code": currency,
        "data_quality": ("synthetic" if snapshot_config.get("is_synthetic") else "configured")
                        if snapshot_row else "unavailable (pre-tariff run)",
        "datasets": datasets,
        "series": arrays,
        "aleph": arrays["aleph"],
        "components": component_arrays,
        "aleph_summary": {
            "score": final_score,
            "weights": ALEPH_WEIGHTS,
            "components": final_components,
            "unavailable_components": [k for k,v in final_components.items() if v is None],
            "formula": "Aleph(t)=sum(weight_i*component_i(t)); all six components are required; no weight renormalization.",
            "limitations": [
                "Tariff inputs are synthetic and illustrative, not a utility bill rate.",
                "Adequacy weights total served energy and critical-load service equally; critical-load service is also a hard optimizer assumption.",
            "Optimized export is zero because the LP has no export decision variable.",
            "For pre-tariff optimizer runs, economic performance and Aleph are unavailable because no tariff snapshot/cost rows were stored.",
                "Network compliance covers stored LP checks; feeder voltage/ampacity are not re-solved for each candidate.",
                "Physical efficiency uses cumulative load served divided by cumulative external energy adjusted for net SOC change; battery discharge is not counted as an external source.",
            ],
        },
    }
