import sqlite3
from pathlib import Path
db=Path(__file__).with_name("microgrid.sqlite")
c=sqlite3.connect(db); c.execute("PRAGMA foreign_keys=ON")
tests=[]
tests.append(("foreign keys",not c.execute("PRAGMA foreign_key_check").fetchall()))
tests.append(("semantic uniqueness",c.execute("""SELECT COUNT(*) FROM (SELECT interval_start,asset_id,measurement_type_id,channel,COUNT(*) n FROM measurement GROUP BY 1,2,3,4 HAVING n>1)""").fetchone()[0]==0))
pmax=qmax=0
for (t,) in c.execute("SELECT interval_start FROM interval_summary"):
 p=abs(c.execute("SELECT SUM(value) FROM measurement WHERE interval_start=? AND measurement_type_id='active_power_kw'",(t,)).fetchone()[0] or 0)
 q=abs(c.execute("SELECT SUM(value) FROM measurement WHERE interval_start=? AND measurement_type_id='reactive_power_kvar'",(t,)).fetchone()[0] or 0)
 pmax=max(pmax,p);qmax=max(qmax,q)
tests += [("active power balance",pmax<1e-4),("reactive power balance",qmax<1e-4),
("battery charge/discharge exclusivity",c.execute("SELECT COUNT(*) FROM dispatch WHERE charge_power_kw>0 AND discharge_power_kw>0").fetchone()[0]==0),
("SOC bounds",c.execute("SELECT COUNT(*) FROM battery_state WHERE soc_start_pct NOT BETWEEN 20 AND 95 OR soc_end_pct NOT BETWEEN 20 AND 95").fetchone()[0]==0),
("voltage bounds",c.execute("SELECT COUNT(*) FROM interval_summary WHERE bus_voltage_ll_v NOT BETWEEN 385 AND 415").fetchone()[0]==0)]
for n,ok in tests: print(("PASS" if ok else "FAIL")+" — "+n)
print(f"Max residuals: P={pmax:.9f} kW; Q={qmax:.9f} kvar")
c.close()
if not all(x[1] for x in tests): raise SystemExit(1)
