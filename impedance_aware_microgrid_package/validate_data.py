import sqlite3,math
from pathlib import Path
c=sqlite3.connect(Path(__file__).with_name("microgrid.sqlite")); c.execute("PRAGMA foreign_keys=ON")
checks=[]
checks.append(("foreign keys",not c.execute("PRAGMA foreign_key_check").fetchall()))
checks.append(("measurement semantic uniqueness",c.execute("""SELECT COUNT(*) FROM (SELECT interval_start,asset_id,measurement_type_id,channel,COUNT(*) n FROM measurement GROUP BY 1,2,3,4 HAVING n>1)""").fetchone()[0]==0))
checks.append(("conductor samples span cable length",c.execute("""SELECT COUNT(*) FROM conductor d WHERE (SELECT MIN(position_m) FROM conductor_sample s WHERE s.conductor_id=d.conductor_id)>1e-9 OR abs((SELECT MAX(position_m) FROM conductor_sample s WHERE s.conductor_id=d.conductor_id)-d.length_m)>1e-7""").fetchone()[0]==0))
# Check sampled R' against rho/A and branch total R against integral of sampled R' dx.
rerr=0.0
for cid,L,A,rho in c.execute("SELECT conductor_id,length_m,area_mm2,rho_ohm_m_at_20c FROM conductor"):
 vals=c.execute("SELECT position_m,r_ohm_per_m_20c FROM conductor_sample WHERE conductor_id=? ORDER BY position_m",(cid,)).fetchall()
 for x,rp in vals: rerr=max(rerr,abs(rp-rho/(A*1e-6)))
 integral=sum((vals[i][1]+vals[i+1][1])*.5*(vals[i+1][0]-vals[i][0]) for i in range(len(vals)-1))
 rtotal=c.execute("SELECT r_ohm_20c_per_phase FROM branch b JOIN conductor d ON d.branch_id=b.branch_id WHERE d.conductor_id=?",(cid,)).fetchone()[0]
 rerr=max(rerr,abs(integral-rtotal))
checks.append(("resistivity/area and integrated conductor R",rerr<1e-8))
# Power balance: source import = loads - PV - battery injection + network losses.
pres=0
for t,pv,load,pb,pg,pl in c.execute("SELECT interval_start,pv_generation_kw,total_load_kw,battery_bus_injection_kw,grid_import_kw,network_active_losses_kw FROM interval_summary"):
 pres=max(pres,abs(pg-(load-pv-pb+pl)))
checks.append(("active power balance incl. feeder losses",pres<2e-4))
# Q source = load Q + branch reactive losses (PV/BESS unity PF)
qres=0
for t,qg,qloss in c.execute("SELECT interval_start,grid_reactive_import_kvar,network_reactive_losses_kvar FROM interval_summary"):
 qloads=-(c.execute("""SELECT SUM(value) FROM measurement WHERE interval_start=? AND measurement_type_id='reactive_power_kvar' AND asset_id LIKE 'LOAD-%'""",(t,)).fetchone()[0] or 0)
 qres=max(qres,abs(qg-(qloads+qloss)))
checks.append(("reactive power balance incl. feeder vars",qres<2e-4))
checks.append(("no branch exceeds ampacity",c.execute("""SELECT COUNT(*) FROM branch_measurement bm JOIN branch b USING(branch_id) WHERE bm.current_a>b.ampacity_a""").fetchone()[0]==0))
checks.append(("SOC limits",c.execute("SELECT COUNT(*) FROM battery_state WHERE soc_start_pct NOT BETWEEN 20 AND 95 OR soc_end_pct NOT BETWEEN 20 AND 95").fetchone()[0]==0))
checks.append(("dispatch mutually exclusive",c.execute("SELECT COUNT(*) FROM dispatch WHERE charge_power_kw>0 AND discharge_power_kw>0").fetchone()[0]==0))
vmin,vmax=c.execute("SELECT MIN(voltage_ll_v),MAX(voltage_ll_v) FROM bus_measurement").fetchone()
checks.append(("bus voltages within 0.90–1.10 pu",vmin>=360 and vmax<=440))
for n,ok in checks: print(("PASS" if ok else "FAIL")+" — "+n)
print(f"Max residuals: P={pres:.8g} kW; Q={qres:.8g} kvar; conductor R error={rerr:.3g}")
print(f"Bus voltage range: {vmin:.3f}–{vmax:.3f} V LL")
c.close()
if not all(x[1] for x in checks): raise SystemExit(1)
