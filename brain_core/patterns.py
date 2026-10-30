"""Compact deterministic discovery of recurring surplus-relevant patterns."""
from __future__ import annotations
import sqlite3
from collections import defaultdict
from datetime import datetime


def scan_surplus_patterns(conn: sqlite3.Connection, limit: int = 8) -> list[dict]:
    rows = conn.execute("""
        SELECT s.interval_start,s.pv_generation_kw,s.total_load_kw,
               s.network_active_losses_kw,w.irradiance_w_m2,w.cloud_factor,
               b.soc_end_pct
        FROM interval_summary s
        JOIN weather w USING(interval_start)
        LEFT JOIN battery_state b USING(interval_start)
        ORDER BY s.interval_start
    """).fetchall()
    by_hour = defaultdict(list)
    for ts, pv, load, loss, irradiance, cloud, soc in rows:
        try:
            hour = datetime.fromisoformat(ts).hour
        except ValueError:
            continue
        margin = float(pv) - float(load) - float(loss)
        by_hour[hour].append({
            "timestamp": ts,
            "margin_kw": margin,
            "irradiance": float(irradiance),
            "cloud": float(cloud),
            "soc": None if soc is None else float(soc),
        })

    patterns = []
    for hour, samples in by_hour.items():
        positive = [x for x in samples if x["margin_kw"] > 0]
        # Require recurrence, not a single lucky interval.
        if len(positive) < 2 or len(positive) / len(samples) < 0.25:
            continue
        socs = [x["soc"] for x in positive if x["soc"] is not None]
        patterns.append({
            "pattern": "recurring_solar_surplus",
            "time_of_day_hour": hour,
            "observations": len(samples),
            "surplus_observations": len(positive),
            "surplus_frequency": round(len(positive) / len(samples), 3),
            "mean_solar_margin_kw": round(
                sum(x["margin_kw"] for x in positive) / len(positive), 4
            ),
            "peak_solar_margin_kw": round(
                max(x["margin_kw"] for x in positive), 4
            ),
            "mean_irradiance_w_m2": round(
                sum(x["irradiance"] for x in positive) / len(positive), 2
            ),
            "mean_cloud_factor": round(
                sum(x["cloud"] for x in positive) / len(positive), 4
            ),
            "mean_battery_soc_pct": None if not socs else round(
                sum(socs) / len(socs), 3
            ),
            "example_intervals": [x["timestamp"] for x in positive[:2]],
        })
    patterns.sort(
        key=lambda x: (x["surplus_frequency"], x["mean_solar_margin_kw"]),
        reverse=True,
    )
    return patterns[:max(0, min(limit, 20))]
