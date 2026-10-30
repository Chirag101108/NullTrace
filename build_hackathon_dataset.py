from __future__ import annotations
import csv
import pathlib
import sqlite3
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE.parent / "opencem" / "microgrid.db"
DB = HERE / "microgrid_hackathon.db"
CSV = HERE / "microgrid_15m_dec2025.csv"
START, END = 1764547200, 1767225600  # 2025-12-01 00:00 through 2026-01-01 00:00 UTC

select_sql = """SELECT (read_ts / 900) * 900 AS bucket_epoch, inverter, COUNT(*) AS sample_count,
       AVG(pv1power), AVG(outsumw), AVG(gridpowerw_a), AVG(battsoc),
       AVG(battvolt), AVG(battcurr), AVG(battchgpower)
FROM measurements
WHERE read_ts >= ? AND read_ts < ? AND inverter IS NOT NULL
GROUP BY bucket_epoch, inverter
ORDER BY bucket_epoch, inverter"""
columns = ["interval_start_utc", "interval_start_epoch", "inverter", "sample_count",
           "avg_pv1power_w", "avg_outsumw_w", "avg_gridpowerw_a_w", "avg_battsoc_pct",
           "avg_battvolt_v", "avg_battcurr_a", "avg_battchgpower_w"]

if DB.exists(): DB.unlink()
if CSV.exists(): CSV.unlink()
src = sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True)
dst = sqlite3.connect(DB)
dst.executescript("""
PRAGMA journal_mode=DELETE;
PRAGMA synchronous=NORMAL;
CREATE TABLE microgrid_15m (
 interval_start_utc TEXT NOT NULL,
 interval_start_epoch INTEGER NOT NULL,
 inverter INTEGER NOT NULL,
 sample_count INTEGER NOT NULL,
 avg_pv1power_w REAL,
 avg_outsumw_w REAL,
 avg_gridpowerw_a_w REAL,
 avg_battsoc_pct REAL,
 avg_battvolt_v REAL,
 avg_battcurr_a REAL,
 avg_battchgpower_w REAL,
 PRIMARY KEY(interval_start_epoch, inverter)
);
CREATE TABLE context (
 id INTEGER PRIMARY KEY,
 recorded INTEGER,
 recorded_utc TEXT,
 start_ts INTEGER,
 start_utc TEXT,
 end_ts INTEGER,
 end_utc TEXT,
 inverter INTEGER,
 source TEXT,
 description TEXT,
 value_json TEXT NOT NULL
);
CREATE TABLE dataset_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
""")
rows = src.execute(select_sql, (START, END))
with CSV.open("w", encoding="utf-8", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(columns)
    batch = []
    for row in rows:
        epoch = int(row[0])
        stamp = datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = (stamp, epoch, *row[1:])
        writer.writerow(out)
        batch.append(out)
        if len(batch) >= 2000:
            dst.executemany("INSERT INTO microgrid_15m VALUES (" + ",".join("?" for _ in columns) + ")", batch)
            batch.clear()
    if batch:
        dst.executemany("INSERT INTO microgrid_15m VALUES (" + ",".join("?" for _ in columns) + ")", batch)

# Keep context events that overlap the selected December measurement window.
ctx = src.execute("""SELECT id,recorded,recorded_utc,start_ts,start_utc,end_ts,end_utc,
       inverter,source,description,value_json
       FROM context WHERE start_ts < ? AND end_ts >= ? ORDER BY start_ts,id""", (END, START))
dst.executemany("INSERT INTO context VALUES (?,?,?,?,?,?,?,?,?,?,?)", ctx)
dst.execute("CREATE INDEX idx_microgrid_time ON microgrid_15m(interval_start_epoch,inverter)")
info = {
 "dataset": "OpenCEM hackathon subset",
 "source": "https://github.com/OpenCEM-platform/opencem-dataset",
 "source_location": "CUHK-Shenzhen campus PV-battery microgrid",
 "period_utc": "2025-12-01 through 2025-12-31",
 "raw_measurement_interval": "approximately 12-15 seconds (varies)",
 "scheduler_interval": "15 minutes; means calculated from measured power samples",
 "timezone": "UTC",
 "license": "CC BY 4.0; attribute OpenCEM and cite dataset when redistributing or publishing",
 "interpretation": "gridpowerw_a and battery current/charge-power signs are kept as reported; confirm sign semantics before optimization",
 "energy_note": "power averages are not energy; integrate power over time for Wh/kWh",
 "solar_note": "pv1power is the reported PV1 channel; other PV channels were absent in this selected period",
 "load_note": "outsumw is the inverter-reported total output power, used as the measured load/output proxy",
}
dst.executemany("INSERT INTO dataset_info VALUES (?,?)", info.items())
dst.commit()
counts = {"microgrid_15m": dst.execute("SELECT COUNT(*) FROM microgrid_15m").fetchone()[0],
          "context": dst.execute("SELECT COUNT(*) FROM context").fetchone()[0]}
dst.close(); src.close()
print({"database": str(DB), "csv": str(CSV), **counts})
