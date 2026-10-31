"""Create bounded synthetic load/PV variants while preserving feeder physics."""
from __future__ import annotations

import math
import random
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from electrical_model import backward_forward_sweep


def create_variant(source: str | Path, destination: str | Path, dataset_id: str,
                   display_name: str, seed: int) -> dict:
    """Copy a source DB, vary daily demand/PV by bounded factors, and recalc AC data."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    try:
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as src, \
             closing(sqlite3.connect(temporary)) as db:
            src.backup(db)
            db.execute("PRAGMA foreign_keys=OFF")
            rng = random.Random(seed)
            assets = {r[0]: (r[1], r[2]) for r in db.execute(
                "SELECT asset_id,asset_type,connection_id FROM asset")}
            dates = [r[0][:10] for r in db.execute(
                "SELECT interval_start FROM interval_summary ORDER BY interval_start")]
            load_factors = {(day, asset): rng.uniform(.95, 1.05)
                            for day in set(dates) for asset, (kind, _) in assets.items()
                            if kind == "load"}
            pv_factors = {day: rng.uniform(.95, 1.05) for day in set(dates)}
            updates = []
            for mid, ts, asset, kind, value in db.execute("""
                SELECT m.measurement_id,m.interval_start,m.asset_id,m.measurement_type_id,m.value
                FROM measurement m WHERE m.measurement_type_id IN ('active_power_kw','reactive_power_kvar')
            """):
                asset_type = assets[asset][0]
                factor = (load_factors[(ts[:10], asset)] if asset_type == "load"
                          else pv_factors[ts[:10]] if asset_type == "solar_pv" else 1.0)
                updates.append((float(value or 0) * factor, mid))
            db.executemany("UPDATE measurement SET value=? WHERE measurement_id=?", updates)
            for ts, irr in db.execute("SELECT interval_start,irradiance_w_m2 FROM weather"):
                db.execute("UPDATE weather SET irradiance_w_m2=? WHERE interval_start=?",
                           (irr * pv_factors[ts[:10]], ts))
            for table, power, energy, asset_col in (
                ("flexibility_declaration", "declared_power_kw", "declared_energy_kwh", "provider_asset_id"),
                ("flexibility_verification", "verified_power_kw", "verified_energy_kwh", "provider_asset_id"),
            ):
                for rowid, ts, asset in db.execute(f"SELECT rowid,interval_start,{asset_col} FROM {table}"):
                    factor = load_factors.get((ts[:10], asset), 1.0)
                    if table == "flexibility_verification":
                        db.execute(f"UPDATE {table} SET {power}={power}*?, usable_power_kw=usable_power_kw*?, {energy}={energy}*? WHERE rowid=?",
                                   (factor, factor, factor, rowid))
                    else:
                        db.execute(f"UPDATE {table} SET {power}={power}*?, {energy}={energy}*? WHERE rowid=?",
                                   (factor, factor, rowid))
            for ts, asset in db.execute("SELECT interval_start,provider_asset_id FROM flexibility_verification"):
                usable = db.execute("SELECT verified_power_kw,usable_power_kw FROM flexibility_verification WHERE interval_start=? AND provider_asset_id=?",(ts,asset)).fetchone()
                if usable:
                    db.execute("UPDATE flexibility_verification SET usable_power_kw=? WHERE interval_start=? AND provider_asset_id=?",
                               (min(usable),ts,asset))

            # Old optimization rows describe the source profile and must not survive in a variant.
            for table in ("optimization_energy_cost", "optimization_result", "optimization_load_result",
                          "optimization_constraint_check", "optimization_tariff_snapshot",
                          "optimization_run", "brain_api_run", "agent_optimizer_tuning"):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    db.execute(f'DELETE FROM "{table}"')

            bus_order = []
            children = {}
            buses = [r[0] for r in db.execute("SELECT bus_id FROM bus")]
            children = {b: [] for b in buses}
            branches = db.execute("SELECT branch_id,from_bus_id,to_bus_id,r_ohm_20c_per_phase,x_ohm_per_phase FROM branch").fetchall()
            parent = {"BUS-000": (None, None)}
            for bid, srcbus, dstbus, r20, x in branches:
                parent[dstbus] = (srcbus, bid)
                children[srcbus].append(dstbus)
            def visit(bus):
                bus_order.append(bus)
                for child in children[bus]: visit(child)
            visit("BUS-000")
            branch_meta = {r[0]: r for r in branches}
            alpha = {r[0]: float(r[1]) for r in db.execute(
                "SELECT branch_id,AVG(temp_coefficient_per_c) FROM conductor GROUP BY branch_id")}
            intervals = [r[0] for r in db.execute("SELECT interval_start FROM interval_summary ORDER BY interval_start")]
            for ts in intervals:
                ambient = float(db.execute("SELECT ambient_temperature_c FROM weather WHERE interval_start=?",(ts,)).fetchone()[0])
                original = {r[0]: r[1:] for r in db.execute("SELECT branch_id,current_a,conductor_temperature_c FROM branch_measurement WHERE interval_start=?",(ts,))}
                pbus = {b: 0.0 for b in buses}; qbus = {b: 0.0 for b in buses}
                pvals = db.execute("SELECT asset_id,value FROM measurement WHERE interval_start=? AND measurement_type_id='active_power_kw'",(ts,)).fetchall()
                qvals = db.execute("SELECT asset_id,value FROM measurement WHERE interval_start=? AND measurement_type_id='reactive_power_kvar'",(ts,)).fetchall()
                total_load = total_pv = battery_injection = qload = 0.0
                for asset, val in pvals:
                    kind, bus = assets[asset]; val = float(val or 0)
                    if kind == "load": total_load += -val; pbus[bus] += -val
                    elif kind == "solar_pv": total_pv += val; pbus[bus] += -val
                    elif kind == "battery": battery_injection += val; pbus[bus] += -val
                for asset, val in qvals:
                    if assets[asset][0] == "load": qload += -float(val or 0); qbus[assets[asset][1]] += -float(val or 0)
                current_guess = {bid: max(float(v[0]), 1e-6) for bid,v in original.items()}
                flow = None; temps = {}; resistances = {}
                for _ in range(5):
                    for bid, old in original.items():
                        old_i, old_t = map(float, old)
                        ratio = current_guess[bid] / max(old_i, .1)
                        temps[bid] = min(90.0, max(ambient, ambient + max(0.0, old_t-ambient)*ratio*ratio))
                        r20 = float(branch_meta[bid][3])
                        resistances[bid] = r20 * (1 + alpha[bid] * (temps[bid]-20.0))
                    z = {bid: complex(resistances[bid], float(branch_meta[bid][4])) for bid in branch_meta}
                    V, I = backward_forward_sweep(bus_order,parent,children,z,pbus,qbus)
                    flow = (V,I)
                    current_guess = {bid: abs(I[(branch_meta[bid][1],branch_meta[bid][2])]) for bid in branch_meta}
                V,I = flow
                losses_p = {bid: 3*abs(I[(branch_meta[bid][1],branch_meta[bid][2])])**2*resistances[bid]/1000 for bid in branch_meta}
                losses_q = {bid: 3*abs(I[(branch_meta[bid][1],branch_meta[bid][2])])**2*float(branch_meta[bid][4])/1000 for bid in branch_meta}
                grid_p = total_load-total_pv-battery_injection+sum(losses_p.values())
                grid_q = qload+sum(losses_q.values())
                db.execute("UPDATE interval_summary SET pv_generation_kw=?,total_load_kw=?,battery_bus_injection_kw=?,grid_import_kw=?,grid_reactive_import_kvar=?,network_active_losses_kw=?,network_reactive_losses_kvar=?,remote_bus_voltage_ll_v=? WHERE interval_start=?",
                           (total_pv,total_load,battery_injection,grid_p,grid_q,sum(losses_p.values()),sum(losses_q.values()),abs(V[bus_order[-1]])*math.sqrt(3),ts))
                for bus in buses:
                    db.execute("UPDATE bus_measurement SET voltage_ll_v=?,voltage_angle_deg=? WHERE interval_start=? AND bus_id=?",
                               (abs(V[bus])*math.sqrt(3),math.degrees(math.atan2(V[bus].imag,V[bus].real)),ts,bus))
                for bid, meta in branch_meta.items():
                    i = current_guess[bid]
                    db.execute("UPDATE branch_measurement SET current_a=?,conductor_temperature_c=?,r_operating_ohm_per_phase=?,active_loss_kw=?,reactive_loss_kvar=? WHERE interval_start=? AND branch_id=?",
                               (i,temps[bid],resistances[bid],losses_p[bid],losses_q[bid],ts,bid))
                    db.execute("""UPDATE measurement SET value=? WHERE interval_start=?
                        AND measurement_type_id='current_a' AND asset_id IN
                        (SELECT asset_id FROM asset WHERE asset_type='conductor' AND connection_id=?)""",(i,ts,bid))
                    db.execute("""UPDATE measurement SET value=? WHERE interval_start=?
                        AND measurement_type_id='conductor_temperature_c' AND asset_id IN
                        (SELECT asset_id FROM asset WHERE asset_type='conductor' AND connection_id=?)""",(temps[bid],ts,bid))
                for asset, pval in pvals:
                    kind,bus=assets[asset]; qval=next((float(v or 0) for a,v in qvals if a==asset),0.0)
                    if kind == 'grid_intertie': pval,qval=grid_p,grid_q
                    if kind not in ('grid_intertie','load','solar_pv','battery'): continue
                    vll=400.0 if kind=='grid_intertie' else abs(V[bus])*math.sqrt(3)
                    kva=math.hypot(float(pval or 0),qval); amps=kva*1000/(math.sqrt(3)*vll) if vll else 0
                    pf=abs(float(pval or 0))/kva if kva else 1.0
                    for mt,val in (("active_power_kw",pval),("reactive_power_kvar",qval),("voltage_ll_v",vll),("current_a",amps),("power_factor",pf)):
                        db.execute("UPDATE measurement SET value=? WHERE interval_start=? AND asset_id=? AND measurement_type_id=?",(val,ts,asset,mt))
            db.execute("UPDATE microgrid SET name=?,description=?",(display_name,
                f"Synthetic scenario generated with seed {seed}; daily load and solar factors are 0.95–1.05. Balanced three-phase feeder quantities were recalculated. Conductor temperature rise scales with I² under unchanged ambient and cooling assumptions. Measurements remain synthetic."))
            db.execute("""CREATE TABLE IF NOT EXISTS afterhours_dataset_info
                (dataset_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, seed INTEGER NOT NULL,
                 load_factor_min REAL NOT NULL, load_factor_max REAL NOT NULL,
                 pv_factor_min REAL NOT NULL, pv_factor_max REAL NOT NULL)""")
            db.execute("INSERT OR REPLACE INTO afterhours_dataset_info VALUES (?,?,?,?,?,?,?)",
                       (dataset_id,display_name,seed,.95,1.05,.95,1.05))
            db.commit()
            check = db.execute("PRAGMA integrity_check").fetchone()[0]
            fk = db.execute("PRAGMA foreign_key_check").fetchall()
            if check != 'ok' or fk: raise sqlite3.DatabaseError(f"Variant validation failed: {check}; FK {fk[:3]}")
        temporary.replace(destination)
        return {"id":dataset_id,"name":display_name,"seed":seed,"load_factor_range":[.95,1.05],"pv_factor_range":[.95,1.05]}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
