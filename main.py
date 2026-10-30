#!/usr/bin/env python3
"""
AfterHours Microgrid Scheduler — first working optimization prototype.

Run:
    python optimizer.py
    python optimizer.py --db microgrid.sqlite --horizon 672

Dependencies:
    Python 3.10+
    numpy
    scipy

This prototype reads the existing synthetic telemetry and flexibility estimates,
solves a linear rolling-horizon-style whole-dataset dispatch problem, and stores
results in separate optimization_* tables. It does NOT overwrite source telemetry.

Model scope:
- Aggregate active-power balance with measured baseline network losses held fixed.
- Linear battery SOC model and throughput-based degradation cost.
- Critical load is never shed.
- Flexible-load reduction is bounded by min(reported, verified) flexibility.
- Household reliability uncertainty is represented as (1-confidence)*committed
  flexibility, and penalized in the objective.
- Grid import peak is minimized.
- PV curtailment is allowed.
- No tariff model, reactive-power redispatch, nonlinear AC re-solve, or detailed
  electrochemical ageing is claimed. Network feasibility must be rechecked in
  a full AC/thermal simulator before field deployment.

The synthetic flexibility records in this package are illustrative load proxies,
not actual household declarations or field-verified flexibility.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sqlite3
import sys
import uuid
from typing import Dict, List, Tuple

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix, csr_matrix


FLEX_ASSETS = {
    "LOAD-BLDG-001": 1000.0,
    "LOAD-HVAC-001": 500.0,
    "LOAD-EV-001": 150.0,
    "LOAD-WATER-001": 700.0,
}
CRITICAL_ASSETS = {"LOAD-CRIT-001"}
BATTERY_ID = "BESS-INV-001"
PV_ID = "PV-INV-001"
GRID_ID = "GRID-POI-001"
DT_H = 0.25
DEFAULT_REPLACEMENT_COST = 400000.0   # illustrative INR assumption
DEFAULT_EFC = 4000.0
DEFAULT_NOMINAL_KWH = 120.0
DEFAULT_PEAK_PENALTY = 1.0           # INR/kW proxy; tune with project economics
DEFAULT_CURTAILMENT_PENALTY = 0.01   # INR/kWh proxy
DEFAULT_FLEX_RISK_PENALTY = 300.0    # INR/kWh expected shortfall proxy


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _load_inputs(conn: sqlite3.Connection, horizon: int | None):
    timestamps = [r[0] for r in conn.execute(
        "SELECT interval_start FROM interval_summary ORDER BY interval_start"
    )]
    if horizon is not None:
        timestamps = timestamps[:horizon]
    if not timestamps:
        raise RuntimeError("No interval_summary records found.")

    n = len(timestamps)
    index = {ts: i for i, ts in enumerate(timestamps)}

    def measurement_map(asset_id: str) -> np.ndarray:
        out = np.zeros(n, dtype=float)
        rows = conn.execute("""
            SELECT interval_start,value FROM measurement
            WHERE asset_id=? AND measurement_type_id='active_power_kw'
        """, (asset_id,)).fetchall()
        for ts, value in rows:
            if ts in index and value is not None:
                out[index[ts]] = float(value)
        return out

    # Consumption is stored as negative active-power injection.
    load_demand = {asset: np.maximum(0.0, -measurement_map(asset))
                   for asset in sorted(set(FLEX_ASSETS) | CRITICAL_ASSETS)}
    pv = np.maximum(0.0, measurement_map(PV_ID))
    losses = np.zeros(n, dtype=float)
    loss_rows = conn.execute(
        "SELECT interval_start,network_active_losses_kw FROM interval_summary"
    ).fetchall()
    for ts, val in loss_rows:
        if ts in index:
            losses[index[ts]] = max(0.0, float(val))

    # Use the declared and verified flexibility records. A missing record means
    # zero usable flexibility, never an optimistic assumption.
    flex_usable = {asset: np.zeros(n, dtype=float) for asset in FLEX_ASSETS}
    flex_confidence = {asset: np.zeros(n, dtype=float) for asset in FLEX_ASSETS}
    q = """
      SELECT d.interval_start,d.provider_asset_id,d.declared_power_kw,
             v.verified_power_kw,v.confidence
      FROM flexibility_declaration d
      JOIN flexibility_verification v
        ON v.interval_start=d.interval_start
       AND v.provider_asset_id=d.provider_asset_id
    """
    for ts, asset, declared, verified, confidence in conn.execute(q):
        if ts in index and asset in flex_usable:
            i = index[ts]
            flex_usable[asset][i] = max(0.0, min(float(declared), float(verified)))
            flex_confidence[asset][i] = min(1.0, max(0.0, float(confidence)))

    # Battery configuration: prefer database configuration; fallback is explicit.
    model = conn.execute("""
        SELECT replacement_cost,expected_equivalent_full_cycles,nominal_capacity_kwh,
               throughput_cost_per_kwh,charge_efficiency,discharge_efficiency
        FROM battery_degradation_model WHERE battery_asset_id=? LIMIT 1
    """, (BATTERY_ID,)).fetchone() if _table_exists(conn, "battery_degradation_model") else None
    if model:
        replacement, efc, capacity, cdeg, eta_c, eta_d = map(float, model)
    else:
        replacement, efc, capacity = (
            DEFAULT_REPLACEMENT_COST, DEFAULT_EFC, DEFAULT_NOMINAL_KWH
        )
        cdeg = replacement / (2.0 * capacity * efc)
        eta_c, eta_d = 0.95, 0.94

    batt = conn.execute("""
        SELECT soc_start_pct,capacity_kwh FROM battery_state
        ORDER BY interval_start LIMIT 1
    """).fetchone()
    if not batt:
        raise RuntimeError("No battery_state records found.")
    initial_soc = float(batt[0])
    if batt[1] is not None:
        capacity = float(batt[1])

    # Asset ratings are used as hard power bounds.
    ratings = dict(conn.execute(
        "SELECT asset_id,rated_active_power_kw FROM asset"
    ).fetchall())
    battery_kw = float(ratings.get(BATTERY_ID, 30.0))
    grid_kw = float(ratings.get(GRID_ID, 100.0))
    pv_rating = float(ratings.get(PV_ID, 60.0))
    pv = np.minimum(pv, pv_rating)

    return (timestamps, load_demand, pv, losses, flex_usable, flex_confidence,
            capacity, initial_soc, battery_kw, grid_kw, cdeg, eta_c, eta_d)


def solve(conn: sqlite3.Connection, horizon: int | None = None,
          peak_penalty: float = DEFAULT_PEAK_PENALTY,
          curtailment_penalty: float = DEFAULT_CURTAILMENT_PENALTY,
          flex_risk_penalty: float = DEFAULT_FLEX_RISK_PENALTY,
          shedding_scale: float = 1.0) -> str:
    (timestamps, loads, pv, losses, flex, confidence, capacity, initial_soc,
     battery_kw, grid_kw, cdeg, eta_c, eta_d) = _load_inputs(conn, horizon)
    n = len(timestamps)
    flex_assets = sorted(FLEX_ASSETS)
    m = len(flex_assets)

    # Variable layout:
    # grid[n], charge[n], discharge[n], curtail[n], shed[m,n], soc[n+1], peak[1]
    offset = {}
    cursor = 0
    for name, size in [
        ("grid", n), ("charge", n), ("discharge", n), ("curtail", n),
        ("shed", m*n), ("soc", n+1), ("peak", 1)
    ]:
        offset[name] = cursor
        cursor += size
    nv = cursor

    def ix(name, t=0, j=0):
        if name == "shed":
            return offset[name] + j*n + t
        if name == "soc":
            return offset[name] + t
        if name == "peak":
            return offset[name]
        return offset[name] + t

    c = np.zeros(nv, dtype=float)
    bounds = [(0, None)] * nv

    # Grid import is bounded by interconnection rating and by peak variable.
    for t in range(n):
        bounds[ix("grid", t)] = (0.0, grid_kw)
        bounds[ix("charge", t)] = (0.0, battery_kw)
        bounds[ix("discharge", t)] = (0.0, battery_kw)
        bounds[ix("curtail", t)] = (0.0, float(pv[t]))
        c[ix("charge", t)] = cdeg * DT_H
        c[ix("discharge", t)] = cdeg * DT_H
        c[ix("curtail", t)] = curtailment_penalty * DT_H
        c[ix("grid", t)] = 0.0
        for j, asset in enumerate(flex_assets):
            demand = loads[asset][t]
            max_shed = min(demand, flex[asset][t])
            bounds[ix("shed", t, j)] = (0.0, max_shed)
            # Expected shortfall risk = (1-confidence) * committed flexibility.
            risk_fraction = 1.0 - confidence[asset][t]
            c[ix("shed", t, j)] = (
                FLEX_ASSETS[asset] * shedding_scale * DT_H
                + flex_risk_penalty * risk_fraction * DT_H
            )

    bounds[ix("soc", 0)] = (initial_soc, initial_soc)
    for t in range(1, n+1):
        bounds[ix("soc", t)] = (20.0, 95.0)
    bounds[ix("soc", n)] = (initial_soc, initial_soc)
    bounds[ix("peak")] = (0.0, grid_kw)
    c[ix("peak")] = peak_penalty

    # Equality constraints: aggregate active-power balance and SOC recursion.
    Aeq = lil_matrix((2*n, nv), dtype=float)
    beq = np.zeros(2*n, dtype=float)
    for t in range(n):
        # Grid + PV - curtail + discharge - charge + flexible reduction
        # = demand + measured baseline network losses.
        Aeq[t, ix("grid", t)] = 1.0
        Aeq[t, ix("curtail", t)] = -1.0
        Aeq[t, ix("discharge", t)] = 1.0
        Aeq[t, ix("charge", t)] = -1.0
        for j, asset in enumerate(flex_assets):
            Aeq[t, ix("shed", t, j)] = 1.0
        total_demand = sum(loads[a][t] for a in loads)
        beq[t] = total_demand + losses[t] - pv[t]

        # SOC[t+1] - SOC[t] - eta_c*Pch*100*dt/E
        # + Pdis*100*dt/(eta_d*E) = 0
        row = n + t
        Aeq[row, ix("soc", t+1)] = 1.0
        Aeq[row, ix("soc", t)] = -1.0
        Aeq[row, ix("charge", t)] = -100.0 * eta_c * DT_H / capacity
        Aeq[row, ix("discharge", t)] = 100.0 * DT_H / (eta_d * capacity)

    # Inequality: grid_import[t] <= peak_import.
    Aub = lil_matrix((n, nv), dtype=float)
    bub = np.zeros(n, dtype=float)
    for t in range(n):
        Aub[t, ix("grid", t)] = 1.0
        Aub[t, ix("peak")] = -1.0

    result = linprog(
        c,
        A_ub=csr_matrix(Aub), b_ub=bub,
        A_eq=csr_matrix(Aeq), b_eq=beq,
        bounds=bounds,
        method="highs",
        options={"presolve": True}
    )
    if not result.success:
        raise RuntimeError(f"Optimizer failed: {result.message}")

    x = result.x
    grid = np.array([x[ix("grid", t)] for t in range(n)])
    charge = np.array([x[ix("charge", t)] for t in range(n)])
    discharge = np.array([x[ix("discharge", t)] for t in range(n)])
    curtail = np.array([x[ix("curtail", t)] for t in range(n)])
    soc = np.array([x[ix("soc", t)] for t in range(n+1)])
    shed = {
        asset: np.array([x[ix("shed", t, j)] for t in range(n)])
        for j, asset in enumerate(flex_assets)
    }
    peak = float(x[ix("peak")])

    # Compute objective components for transparent audit.
    peak_component = peak_penalty * peak
    shedding_component = sum(
        FLEX_ASSETS[a] * shedding_scale * float(np.sum(shed[a])) * DT_H
        for a in flex_assets
    )
    curtail_component = curtailment_penalty * float(np.sum(curtail)) * DT_H
    degradation_component = cdeg * float(np.sum(charge + discharge)) * DT_H
    flex_risk_component = flex_risk_penalty * sum(
        float(np.sum((1.0-confidence[a]) * shed[a])) * DT_H
        for a in flex_assets
    )
    objective = (peak_component + shedding_component + curtail_component
                 + degradation_component + flex_risk_component)

    run_id = "OPT-" + uuid.uuid4().hex[:12].upper()
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    solver_version = "scipy-" + __import__("scipy").__version__

    # Persist only in optimizer-owned tables; never overwrite source telemetry.
    conn.execute("BEGIN")
    conn.execute("""
      INSERT INTO optimization_run
      (optimization_run_id,created_at,solver_name,solver_version,model_version,status,
       objective_value,objective_peak_component,objective_shedding_component,
       objective_curtailment_component,objective_degradation_component,
       objective_flexibility_component,degradation_cost_per_kwh,
       flexibility_penalty_per_kwh,peak_penalty_per_kw,terminal_soc_target_pct,
       input_database,notes)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (run_id,now,"scipy.optimize.linprog",solver_version,"afterhours-brain-0.1",
          "optimal",objective,peak_component,shedding_component,curtail_component,
          degradation_component,flex_risk_component,cdeg,flex_risk_penalty,
          peak_penalty,initial_soc,os.path.abspath(conn.execute("PRAGMA database_list").fetchone()[2]),
          "Linear prototype; fixed measured losses; no nonlinear AC/thermal redispatch. "
          "Synthetic flexibility proxies are not field data."))

    for t, ts in enumerate(timestamps):
        total_shed = sum(shed[a][t] for a in flex_assets)
        committed = total_shed
        expected_shortfall = sum(
            (1.0-confidence[a][t])*shed[a][t] for a in flex_assets
        )
        deg_cost = cdeg * (charge[t]+discharge[t]) * DT_H
        contrib = (peak_component / n
                   + sum(FLEX_ASSETS[a]*shed[a][t]*DT_H for a in flex_assets)
                   + curtailment_penalty*curtail[t]*DT_H
                   + deg_cost
                   + flex_risk_penalty*expected_shortfall*DT_H)
        conn.execute("""
          INSERT INTO optimization_result VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (run_id,ts,grid[t],charge[t],discharge[t],curtail[t],soc[t],soc[t+1],
              total_shed,committed,expected_shortfall,deg_cost,contrib))

        for asset in flex_assets:
            conn.execute("""
              INSERT INTO optimization_load_result VALUES (?,?,?,?,?)
            """, (run_id,ts,asset,shed[asset][t],flex[asset][t]))

        total_demand = sum(loads[a][t] for a in loads)
        balance_residual = (
            grid[t] + pv[t] - curtail[t] + discharge[t] - charge[t]
            - (total_demand - total_shed + losses[t])
        )
        simultaneous = min(charge[t], discharge[t])
        checks = [
            ("active_power_balance_residual",abs(balance_residual),1e-5,
             abs(balance_residual) <= 1e-5,
             "Aggregate active-power balance including fixed baseline losses."),
            ("grid_import_rating",grid[t],grid_kw,grid[t] <= grid_kw+1e-6,
             "Grid import bounded by interconnection rating."),
            ("battery_charge_rating",charge[t],battery_kw,charge[t] <= battery_kw+1e-6,
             "Charge power bounded by inverter rating."),
            ("battery_discharge_rating",discharge[t],battery_kw,discharge[t] <= battery_kw+1e-6,
             "Discharge power bounded by inverter rating."),
            ("battery_no_simultaneous_charge_discharge",simultaneous,0.0,
             simultaneous <= 1e-7,
             "Positive throughput cost and balance should prevent simultaneous operation."),
            ("battery_soc_min",soc[t+1],20.0,soc[t+1] >= 20.0-1e-6,
             "End-of-interval SOC lower bound."),
            ("battery_soc_max",soc[t+1],95.0,soc[t+1] <= 95.0+1e-6,
             "End-of-interval SOC upper bound."),
            ("flexibility_bound",total_shed,sum(flex[a][t] for a in flex_assets),
             total_shed <= sum(flex[a][t] for a in flex_assets)+1e-6,
             "Flexible reduction does not exceed conservative verified capability."),
        ]
        for name, val, lim, passed, explanation in checks:
            conn.execute("""
              INSERT INTO optimization_constraint_check VALUES (?,?,?,?,?,?,?)
            """, (run_id,ts,name,float(val),float(lim),int(bool(passed)),explanation))

    conn.commit()
    return run_id


def main():
    parser = argparse.ArgumentParser(description="Run the AfterHours microgrid optimization prototype.")
    parser.add_argument("--db", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     "microgrid.sqlite"),
                        help="Path to the microgrid SQLite database.")
    parser.add_argument("--horizon", type=int, default=None,
                        help="Number of intervals to optimize (default: all).")
    parser.add_argument("--peak-penalty", type=float, default=DEFAULT_PEAK_PENALTY)
    parser.add_argument("--curtailment-penalty", type=float, default=DEFAULT_CURTAILMENT_PENALTY)
    parser.add_argument("--flex-risk-penalty", type=float, default=DEFAULT_FLEX_RISK_PENALTY)
    parser.add_argument("--shedding-scale", type=float, default=1.0)
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        parser.error(f"Database not found: {args.db}")
    if args.horizon is not None and args.horizon < 1:
        parser.error("--horizon must be positive.")

    conn = sqlite3.connect(args.db)
    try:
        required = ["interval_summary","measurement","battery_state","dispatch",
                    "battery_degradation_model","flexibility_declaration",
                    "flexibility_verification","optimization_run","optimization_result"]
        missing = [t for t in required if not _table_exists(conn,t)]
        if missing:
            raise RuntimeError("Database is missing required tables: " + ", ".join(missing))
        run_id = solve(conn,args.horizon,args.peak_penalty,args.curtailment_penalty,
                       args.flex_risk_penalty,args.shedding_scale)
        row = conn.execute("""
          SELECT objective_value,objective_peak_component,objective_shedding_component,
                 objective_curtailment_component,objective_degradation_component,
                 objective_flexibility_component
          FROM optimization_run WHERE optimization_run_id=?
        """,(run_id,)).fetchone()
        stats = conn.execute("""
          SELECT MAX(grid_import_kw),AVG(grid_import_kw),SUM(load_shed_kw),
                 SUM(battery_charge_kw),SUM(battery_discharge_kw),
                 MIN(battery_soc_end_pct),MAX(battery_soc_end_pct)
          FROM optimization_result WHERE optimization_run_id=?
        """,(run_id,)).fetchone()
        print("OPTIMIZATION SUCCESS")
        print("Run ID:",run_id)
        print("Intervals:",conn.execute(
            "SELECT COUNT(*) FROM optimization_result WHERE optimization_run_id=?",
            (run_id,)).fetchone()[0])
        print("Objective total: {:.4f}".format(row[0]))
        print("Objective components [peak, shedding, curtailment, degradation, flex-risk]:")
        print("  " + ", ".join("{:.4f}".format(v) for v in row[1:]))
        print("Max / average grid import (kW): {:.3f} / {:.3f}".format(stats[0],stats[1]))
        print("Total flexible reduction (kWh): {:.3f}".format(stats[2]*DT_H))
        print("Battery charge / discharge (kWh): {:.3f} / {:.3f}".format(
            stats[3]*DT_H,stats[4]*DT_H))
        print("SOC range (%): {:.3f}–{:.3f}".format(stats[5],stats[6]))
        print("Results stored in optimization_* tables.")
        print("NOTE: This is an aggregate scheduling prototype, not a certified AC/thermal dispatch.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
