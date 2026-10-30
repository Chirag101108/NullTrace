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

# Variable assessments: names/descriptions intentionally remain in the
# accompanying human-readable guide, not in the operational database.
variable_assessments = [
    (1, 99.91, None),
    (2, 99.83, None),
    (3, 99.74, None),
    (4, 99.65, 'b'),
    (5, 99.56, 'g'),
    (6, 99.47, 'g'),
    (7, 99.38, None),
    (8, 99.29, None),
    (9, 99.20, None),
    (10, 99.11, 'g'),
    (11, 99.02, 'b'),
    (12, 98.93, 'g'),
    (13, 98.84, None),
    (14, 98.75, None),
    (15, 98.66, None),
    (16, 98.57, 'b'),
    (17, 98.48, None),
    (18, 98.39, 'b'),
    (19, 98.30, 'b'),
    (20, 98.21, 'b'),
    (21, 98.12, 'g'),
    (22, 98.03, None),
    (23, 97.94, None),
    (24, 97.85, None),
    (25, 97.76, None),
    (26, 97.67, 'b'),
    (27, 97.58, 'g'),
    (28, 97.49, 'g'),
    (29, 97.40, 'g'),
    (30, 97.31, None),
    (31, 97.22, None),
    (32, 97.13, None),
    (33, 97.04, 'b'),
    (34, 96.95, 'g'),
    (35, 96.86, None),
    (36, 96.77, 'b'),
    (37, 96.68, 'b'),
    (38, 96.59, 'b'),
    (39, 96.50, 'b'),
    (40, 96.41, 'b'),
    (41, 96.32, 'b'),
    (42, 96.23, 'g'),
    (43, 96.14, 'b'),
    (44, 96.05, None),
    (45, 95.96, None),
    (46, 95.87, 'b'),
    (47, 95.78, 'b'),
    (48, 95.69, 'b'),
    (49, 95.60, 'b'),
    (50, 95.51, 'b'),
    (51, 95.42, 'g'),
    (52, 95.33, None),
    (53, 95.24, None),
    (54, 95.15, None),
    (55, 95.06, 'b'),
    (56, 94.97, 'b'),
    (57, 94.88, None),
    (58, 94.79, None),
    (59, 94.70, None),
    (60, 94.61, None),
    (61, 94.52, None),
    (62, 94.43, None),
    (63, 94.34, None),
    (64, 94.25, None),
    (65, 94.16, None),
    (66, 94.07, None),
    (67, 93.98, 'g'),
]
c.executemany("INSERT INTO variable_assessment(variable_id,significance_score,favorability) VALUES(?,?,?)", variable_assessments)

c.commit()
print("Built",out)
for t, in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
 print(t,c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
c.close()
