"""Tariff schema migration, validation, and local-time rate lookup."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "microgrid_afterhours_hurdle_v1"
    / "schema_update_tariffs.sql"
)


def ensure_tariff_schema(conn: sqlite3.Connection) -> None:
    """Apply the additive, idempotent tariff migration to this database."""
    if not _MIGRATION.is_file():
        raise RuntimeError(f"Tariff migration not found: {_MIGRATION}")
    conn.executescript(_MIGRATION.read_text(encoding="utf-8"))


def _profile_and_periods(conn: sqlite3.Connection) -> tuple[dict, list[dict]]:
    profiles = conn.execute("""
        SELECT profile_id,microgrid_id,grid_asset_id,profile_name,currency_code,
               timezone,version,is_active,is_synthetic,source_description
        FROM tariff_profiles WHERE is_active=1
    """).fetchall()
    if len(profiles) != 1:
        raise ValueError("Exactly one active tariff profile is required")
    (profile_id, microgrid_id, grid_asset_id, name, currency, tz, version,
     active, synthetic, source) = profiles[0]
    profile = {
        "profile_id": profile_id, "microgrid_id": microgrid_id,
        "grid_asset_id": grid_asset_id, "profile_name": name,
        "currency_code": currency, "timezone": tz, "version": int(version),
        "is_active": bool(active), "is_synthetic": bool(synthetic),
        "source_description": source,
    }
    periods = [dict(zip(
        ("period_id", "period_name", "weekday_mask", "start_minute",
         "end_minute", "import_rate_per_kwh", "export_rate_per_kwh"), row
    )) for row in conn.execute("""
        SELECT period_id,period_name,weekday_mask,start_minute,end_minute,
               import_rate_per_kwh,export_rate_per_kwh
        FROM tariff_periods WHERE profile_id=? ORDER BY start_minute
    """, (profile_id,))]
    return profile, periods


def validate_tariff(conn: sqlite3.Connection, profile: dict,
                    periods: list[dict]) -> None:
    """Require valid money, timezone, interval-aligned complete weekly coverage."""
    if not profile["currency_code"].isalpha() or len(profile["currency_code"]) != 3:
        raise ValueError("Tariff currency_code must be a three-letter code")
    try:
        ZoneInfo(profile["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown tariff timezone: {profile['timezone']}") from exc
    interval = conn.execute("""
        SELECT interval_minutes,timezone FROM microgrid
        WHERE microgrid_id=?
    """, (profile["microgrid_id"],)).fetchone()
    if interval is None:
        raise ValueError("Tariff profile references an unknown microgrid")
    interval_minutes, microgrid_timezone = int(interval[0]), interval[1]
    if interval_minutes <= 0:
        raise ValueError("Microgrid interval_minutes must be positive")
    if profile["timezone"] != microgrid_timezone:
        raise ValueError("Tariff timezone must match the microgrid timezone")
    asset = conn.execute("""
        SELECT asset_type,microgrid_id FROM asset WHERE asset_id=?
    """, (profile["grid_asset_id"],)).fetchone()
    if asset is None or asset[0] != "grid_intertie" or asset[1] != profile["microgrid_id"]:
        raise ValueError("Tariff must reference a grid intertie on its microgrid")
    if not periods:
        raise ValueError("Active tariff profile has no periods")

    coverage = [[0] * 1440 for _ in range(7)]
    for row in periods:
        mask, start, end = int(row["weekday_mask"]), int(row["start_minute"]), int(row["end_minute"])
        if not (1 <= mask <= 127 and 0 <= start < end <= 1440):
            raise ValueError("Tariff periods must be valid non-wrapping local-day ranges")
        if start % interval_minutes or end % interval_minutes:
            raise ValueError("Tariff period boundaries must align to the microgrid interval")
        for key in ("import_rate_per_kwh", "export_rate_per_kwh"):
            rate = float(row[key])
            if not math.isfinite(rate) or rate < 0:
                raise ValueError(f"{key} must be a finite non-negative rate")
        for day in range(7):
            if mask & (1 << day):
                for minute in range(start, end):
                    coverage[day][minute] += 1
    if any(value != 1 for day in coverage for value in day):
        raise ValueError("Tariff periods must cover each local week minute exactly once")


def load_active_tariff(conn: sqlite3.Connection,
                       timestamps: list[str] | None = None) -> dict:
    """Return the validated profile, rates and config snapshot for timestamps."""
    ensure_tariff_schema(conn)
    profile, periods = _profile_and_periods(conn)
    validate_tariff(conn, profile, periods)
    rates = []
    zone = ZoneInfo(profile["timezone"])
    for value in timestamps or []:
        try:
            dt = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid interval timestamp: {value!r}") from exc
        if dt.tzinfo is None:
            raise ValueError("Interval timestamps must include a timezone offset")
        local = dt.astimezone(zone)
        minute = local.hour * 60 + local.minute
        day_bit = 1 << local.weekday()
        matches = [p for p in periods
                   if int(p["weekday_mask"]) & day_bit
                   and int(p["start_minute"]) <= minute < int(p["end_minute"])]
        if len(matches) != 1:
            raise ValueError(f"Expected one tariff rate for interval {value}, got {len(matches)}")
        rates.append({
            "interval_start": value,
            "import_rate_per_kwh": float(matches[0]["import_rate_per_kwh"]),
            "export_rate_per_kwh": float(matches[0]["export_rate_per_kwh"]),
            "period_id": matches[0]["period_id"],
            "period_name": matches[0]["period_name"],
        })
    snapshot = {**profile, "periods": periods}
    return {"profile": profile, "periods": periods, "rates": rates,
            "snapshot_json": json.dumps(snapshot, sort_keys=True, separators=(",", ":"))}


def save_tariff_snapshot(conn: sqlite3.Connection, run_id: str,
                         tariff: dict, captured_at: str) -> None:
    profile = tariff["profile"]
    conn.execute("""
        INSERT INTO optimization_tariff_snapshot
        (optimization_run_id,profile_id,profile_version,currency_code,timezone,config_json,captured_at)
        VALUES (?,?,?,?,?,?,?)
    """, (run_id, profile["profile_id"], profile["version"],
          profile["currency_code"], profile["timezone"], tariff["snapshot_json"], captured_at))
