import csv,sqlite3,sys
from pathlib import Path
R=Path(__file__).resolve().parent
out=Path(sys.argv[1]) if len(sys.argv)>1 else R/"microgrid.sqlite"
if out.exists(): out.unlink()
c=sqlite3.connect(out); c.execute("PRAGMA foreign_keys=ON")
c.executescript((R/"schema.sql").read_text())
def rows(n):
 with open(R/n,newline="",encoding="utf-8") as f: return list(csv.DictReader(f))
c.execute("INSERT INTO microgrid VALUES(?,?,?,?,?,?,?)",("MG-SYN-002","Impedance-aware synthetic campus microgrid","Synthetic balanced 3-phase radial AC network; not real telemetry.","Asia/Kolkata",50,400,15))
c.executemany("INSERT INTO bus VALUES(?,?,?,?,?)",[(r["bus_id"],"MG-SYN-002",r["bus_name"],float(r["nominal_voltage_ll_v"]),r["phases"]) for r in rows("buses.csv")])
c.executemany("INSERT INTO asset VALUES(?,?,?,?,?,?,?)",[(r["asset_id"],"MG-SYN-002",r["asset_name"],r["asset_type"],r["connection_id"],float(r["rated_active_power_kw"]),float(r["rated_reactive_power_kvar"])) for r in rows("assets.csv")])
c.executemany("INSERT INTO branch VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[(r["branch_id"],"MG-SYN-002",r["from_bus_id"],r["to_bus_id"],float(r["length_m"]),r["phases"],float(r["frequency_hz"]),float(r["r_ohm_20c_per_phase"]),float(r["x_ohm_per_phase"]),float(r["r_ohm_per_m_20c"]),float(r["x_ohm_per_m"]),float(r["inductance_h_per_m"]),float(r["ampacity_a"]),r["model"]) for r in rows("branches.csv")])
c.executemany("INSERT INTO conductor VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",[(r["conductor_id"],r["branch_id"],r["phase"],r["material"],float(r["length_m"]),float(r["area_mm2"]),float(r["rho_ohm_m_at_20c"]),float(r["reference_temp_c"]),float(r["temp_coefficient_per_c"]),float(r["gmd_m"]),float(r["gmr_m"]),float(r["ampacity_a"])) for r in rows("conductors.csv")])
c.executemany("INSERT INTO conductor_sample VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",[(r["conductor_id"],int(r["sample_index"]),float(r["position_m"]),float(r["area_mm2"]),float(r["rho_ohm_m_at_20c"]),float(r["reference_temp_c"]),float(r["temp_coefficient_per_c"]),float(r["gmd_m"]),float(r["gmr_m"]),float(r["r_ohm_per_m_20c"]),float(r["x_ohm_per_m"]),float(r["ampacity_a"])) for r in rows("conductor_samples.csv")])
c.executemany("INSERT INTO measurement_type VALUES(?,?,?,?)",[(r["measurement_type_id"],r["display_name"],r["unit"],r["semantic_definition"]) for r in rows("measurement_types.csv")])
c.executemany("INSERT INTO measurement(interval_start,asset_id,measurement_type_id,channel,value,source_version) VALUES(?,?,?,?,?,?)",[(r["interval_start"],r["asset_id"],r["measurement_type_id"],r["channel"],float(r["value"]),r["source_version"]) for r in rows("measurements.csv")])
c.executemany("INSERT INTO bus_measurement VALUES(?,?,?,?,?)",[(r["interval_start"],r["bus_id"],float(r["voltage_ll_v"]),float(r["voltage_angle_deg"]),float(r["frequency_hz"])) for r in rows("bus_measurements.csv")])
c.executemany("INSERT INTO branch_measurement VALUES(?,?,?,?,?,?,?)",[(r["interval_start"],r["branch_id"],float(r["current_a"]),float(r["conductor_temperature_c"]),float(r["r_operating_ohm_per_phase"]),float(r["active_loss_kw"]),float(r["reactive_loss_kvar"])) for r in rows("branch_measurements.csv")])
c.executemany("INSERT INTO weather VALUES(?,?,?,?)",[(r["interval_start"],float(r["irradiance_w_m2"]),float(r["ambient_temperature_c"]),float(r["cloud_factor"])) for r in rows("weather.csv")])
c.executemany("INSERT INTO battery_state VALUES(?,?,?,?,?,?)",[(r["interval_start"],r["asset_id"],float(r["soc_start_pct"]),float(r["soc_end_pct"]),float(r["battery_bus_injection_kw"]),float(r["capacity_kwh"])) for r in rows("battery_state.csv")])
c.executemany("INSERT INTO dispatch VALUES(?,?,?,?,?)",[(r["interval_start"],r["battery_asset_id"],float(r["charge_power_kw"]),float(r["discharge_power_kw"]),r["dispatch_reason"]) for r in rows("dispatch.csv")])
c.executemany("INSERT INTO interval_summary VALUES(?,?,?,?,?,?,?,?,?,?)",[(r["interval_start"],float(r["pv_generation_kw"]),float(r["total_load_kw"]),float(r["battery_bus_injection_kw"]),float(r["grid_import_kw"]),float(r["grid_reactive_import_kvar"]),float(r["network_active_losses_kw"]),float(r["network_reactive_losses_kvar"]),float(r["remote_bus_voltage_ll_v"]),float(r["frequency_hz"])) for r in rows("interval_summary.csv")])
c.commit()
print("Built",out)
for t, in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
 print(t,c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
c.close()
