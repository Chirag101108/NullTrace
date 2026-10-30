"""Rebuild measurement/interval data from the accompanying CSVs into the packaged schema."""
import csv,sqlite3
from pathlib import Path
R=Path(__file__).resolve().parent
c=sqlite3.connect(R/"microgrid.sqlite");c.execute("PRAGMA foreign_keys=ON")
for t in ("measurement","weather","battery_state","dispatch","interval_summary"): c.execute(f"DELETE FROM {t}")
def read(n):
 with open(R/n,newline="",encoding="utf-8") as f: return list(csv.DictReader(f))
c.executemany("INSERT INTO measurement(interval_start,asset_id,measurement_type_id,channel,value,source_version) VALUES(:interval_start,:asset_id,:measurement_type_id,:channel,:value,:source_version)",[{**r,"value":float(r["value"])} for r in read("measurements.csv")])
c.executemany("INSERT INTO weather VALUES(:interval_start,:irradiance_w_m2,:ambient_temperature_c,:cloud_factor)",[{**r,**{k:float(r[k]) for k in ("irradiance_w_m2","ambient_temperature_c","cloud_factor")}} for r in read("weather.csv")])
c.executemany("INSERT INTO battery_state VALUES(:interval_start,:asset_id,:soc_start_pct,:soc_end_pct,:battery_bus_injection_kw,:capacity_kwh)",[{**r,**{k:float(r[k]) for k in ("soc_start_pct","soc_end_pct","battery_bus_injection_kw","capacity_kwh")}} for r in read("battery_state.csv")])
c.executemany("INSERT INTO dispatch VALUES(:interval_start,:battery_asset_id,:charge_power_kw,:discharge_power_kw,:dispatch_reason)",[{**r,**{k:float(r[k]) for k in ("charge_power_kw","discharge_power_kw")}} for r in read("dispatch.csv")])
c.executemany("INSERT INTO interval_summary VALUES(:interval_start,:pv_generation_kw,:total_load_kw,:battery_bus_injection_kw,:grid_import_kw,:reactive_balance_residual_kvar,:bus_voltage_ll_v,:frequency_hz)",[{**r,**{k:float(r[k]) for k in ("pv_generation_kw","total_load_kw","battery_bus_injection_kw","grid_import_kw","reactive_balance_residual_kvar","bus_voltage_ll_v","frequency_hz")}} for r in read("interval_summary.csv")])
c.commit();print("CSV import complete.");c.close()
