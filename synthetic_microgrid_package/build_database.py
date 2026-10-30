#!/usr/bin/env python3
"""Build a fresh SQLite database from the packaged CSV files."""
import csv, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OUT=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"rebuilt_microgrid.sqlite"
if OUT.exists(): OUT.unlink()
con=sqlite3.connect(OUT)
con.execute("PRAGMA foreign_keys=ON")
con.executescript((ROOT/"schema.sql").read_text(encoding="utf-8"))
def read(name):
    with open(ROOT/name,newline="",encoding="utf-8") as f:
        return list(csv.DictReader(f))
con.execute("INSERT INTO microgrid VALUES(?,?,?,?,?,?,?)",
 ("MG-SYN-001","Synthetic Campus Microgrid",
  "Deterministic synthetic three-phase AC microgrid; not real telemetry.",
  "Asia/Kolkata",50.0,400.0,15))
con.execute("INSERT INTO bus VALUES(?,?,?,?,?)",
 ("BUS-MAIN-001","MG-SYN-001","Main LV AC bus",400.0,"ABC"))
con.executemany("INSERT INTO asset VALUES(?,?,?,?,?,?,?,?)",
 [(r["asset_id"],"MG-SYN-001","BUS-MAIN-001",r["asset_name"],r["asset_type"],
   float(r["rated_active_power_kw"]),float(r["rated_reactive_power_kvar"]),r["description"])
  for r in read("assets.csv")])
con.executemany("INSERT INTO measurement_type VALUES(?,?,?,?)",
 [(r["measurement_type_id"],r["display_name"],r["unit"],r["semantic_definition"])
  for r in read("measurement_types.csv")])
con.executemany("""INSERT INTO measurement
(interval_start,asset_id,measurement_type_id,channel,value,source_version)
VALUES(?,?,?,?,?,?)""",
 [(r["interval_start"],r["asset_id"],r["measurement_type_id"],r["channel"],
   float(r["value"]) if r["value"] else None,r["source_version"])
  for r in read("measurements.csv")])
con.executemany("INSERT INTO weather VALUES(?,?,?,?)",
 [(r["interval_start"],float(r["irradiance_w_m2"]),float(r["ambient_temperature_c"]),float(r["cloud_factor"]))
  for r in read("weather.csv")])
con.executemany("INSERT INTO battery_state VALUES(?,?,?,?,?,?)",
 [(r["interval_start"],r["asset_id"],float(r["soc_start_pct"]),float(r["soc_end_pct"]),
   float(r["battery_bus_injection_kw"]),float(r["capacity_kwh"])) for r in read("battery_state.csv")])
con.executemany("INSERT INTO dispatch VALUES(?,?,?,?,?)",
 [(r["interval_start"],r["battery_asset_id"],float(r["charge_power_kw"]),float(r["discharge_power_kw"]),r["dispatch_reason"])
  for r in read("dispatch.csv")])
con.executemany("INSERT INTO interval_summary VALUES(?,?,?,?,?,?,?,?)",
 [(r["interval_start"],float(r["pv_generation_kw"]),float(r["total_load_kw"]),
   float(r["battery_bus_injection_kw"]),float(r["grid_import_kw"]),
   float(r["reactive_balance_residual_kvar"]),float(r["bus_voltage_ll_v"]),float(r["frequency_hz"]))
  for r in read("interval_summary.csv")])
con.commit()
print(f"Created {OUT}")
for table in ("microgrid","bus","asset","measurement_type","measurement","weather","battery_state","dispatch","interval_summary"):
 print(f"{table}: {con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:,}")
con.close()
