"""Bounded read tools and a constrained tuning write for the Gemini decision loop."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .protocol import OPTIMIZER_PARAMETER_BOUNDS, build_snapshot


MAX_INTERVAL_WINDOW = 12


TOOL_DECLARATIONS = [
    {
        "type": "function",
        "name": "get_candidate_summary",
        "description": "Read the current validated optimizer candidate summary and its score components from this run's copied database.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "get_constraint_summary",
        "description": "Read counts and failed examples for deterministic constraints on the current candidate.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "get_tariff_profile",
        "description": "Read the exact tariff snapshot used by the current candidate, including its synthetic/configured label.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "get_interval_window",
        "description": "Read at most 12 adjacent measured and optimized intervals by zero-based index for a concrete local comparison.",
        "parameters": {
            "type": "object",
            "properties": {
                "start_index": {"type": "integer", "minimum": 0},
                "count": {"type": "integer", "minimum": 1, "maximum": MAX_INTERVAL_WINDOW},
            },
            "required": ["start_index", "count"],
        },
    },
    {
        "type": "function",
        "name": "set_optimizer_parameter",
        "description": "Write one bounded optimizer tuning parameter into this run-copy's agent tuning audit table. Changes apply to the next optimizer iteration; source measurements, tariffs, assets, and dispatch are not writable.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": list(OPTIMIZER_PARAMETER_BOUNDS)},
                "value": {"type": "number"},
            },
            "required": ["name", "value"],
        },
    },
]


class AgentDatabaseTools:
    """Expose named, bounded SELECT operations on a single optimizer run DB."""

    def __init__(self, db_path: str | Path, run_id: str,
                 agent_run_id: str | None = None, iteration: int | None = None,
                 allow_parameter_writes: bool = False):
        self.db_path = Path(db_path).expanduser().resolve()
        self.run_id = run_id
        self.agent_run_id = agent_run_id
        self.iteration = iteration
        self.allow_parameter_writes = allow_parameter_writes

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise FileNotFoundError("Run database is no longer available")
        conn = sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        allowed = {item["name"] for item in TOOL_DECLARATIONS}
        if name not in allowed:
            raise ValueError(f"Unsupported database tool: {name}")
        expected = {
            "get_interval_window": {"start_index", "count"},
            "set_optimizer_parameter": {"name", "value"},
        }.get(name, set())
        if set(arguments) - expected:
            raise ValueError("Unexpected argument supplied to database tool")
        if name == "set_optimizer_parameter":
            return self._set_optimizer_parameter(arguments)
        with closing(self._connect()) as conn:
            if name == "get_candidate_summary":
                snapshot = build_snapshot(conn, self.run_id)
                return {
                    "run_id": self.run_id,
                    "validation": snapshot["validation"],
                    "power": snapshot["power"],
                    "energy_kwh": snapshot["energy_kwh"],
                    "cost": snapshot["cost"],
                    "aleph": snapshot["aleph"],
                    "limitations": snapshot["limitations"],
                }
            if name == "get_constraint_summary":
                return self._constraints(conn)
            if name == "get_tariff_profile":
                return self._tariff(conn)
            return self._interval_window(conn, arguments)

    def _set_optimizer_parameter(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_parameter_writes:
            raise PermissionError("Parameter tuning writes require an available Aleph candidate score")
        if not self.agent_run_id or isinstance(self.iteration, bool) or not isinstance(self.iteration, int):
            raise ValueError("Parameter writes require an active agent run and iteration")
        name, value = arguments.get("name"), arguments.get("value")
        if name not in OPTIMIZER_PARAMETER_BOUNDS:
            raise ValueError("Unsupported optimizer parameter")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Parameter value must be numeric")
        value = float(value)
        lo, hi = OPTIMIZER_PARAMETER_BOUNDS[name]
        if not lo <= value <= hi:
            raise ValueError(f"{name} must be between {lo} and {hi}")
        try:
            with closing(self._connect()) as conn:
                marker = conn.execute(
                    "SELECT copy_version FROM afterhours_run_database WHERE singleton=1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise PermissionError("Agent writes are allowed only in a marked run database copy") from exc
        if marker is None or int(marker[0]) != 1:
            raise PermissionError("Agent writes are allowed only in a marked run database copy")
        # The only writable SQL target is a dedicated tuning audit table. The
        # agent receives no SQL tool and cannot choose the table or row keys.
        with closing(sqlite3.connect(f"{self.db_path.as_uri()}?mode=rw", uri=True, timeout=10)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA query_only=OFF")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_optimizer_tuning (
                    agent_run_id TEXT NOT NULL,
                    iteration INTEGER NOT NULL CHECK(iteration BETWEEN 1 AND 12),
                    optimizer_run_id TEXT NOT NULL,
                    parameter_name TEXT NOT NULL CHECK(parameter_name IN (
                        'peak_penalty','curtailment_penalty','flex_risk_penalty','shedding_scale'
                    )),
                    parameter_value REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(agent_run_id,iteration,parameter_name)
                )
            """)
            conn.execute("""
                INSERT INTO agent_optimizer_tuning
                    (agent_run_id,iteration,optimizer_run_id,parameter_name,parameter_value,updated_at)
                VALUES (?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(agent_run_id,iteration,parameter_name) DO UPDATE SET
                    optimizer_run_id=excluded.optimizer_run_id,
                    parameter_value=excluded.parameter_value,
                    updated_at=excluded.updated_at
            """, (self.agent_run_id, self.iteration, self.run_id, name, value))
            conn.commit()
        return {
            "accepted": True,
            "parameter": name,
            "value": value,
            "applies_to": "next optimizer iteration",
            "source_optimizer_run_id": self.run_id,
        }

    def get_parameter_updates(self) -> dict[str, float]:
        if not self.agent_run_id or not isinstance(self.iteration, int):
            return {}
        with closing(self._connect()) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_optimizer_tuning'"
            ).fetchone()
            if not exists:
                return {}
            rows = conn.execute("""
                SELECT parameter_name,parameter_value FROM agent_optimizer_tuning
                WHERE agent_run_id=? AND iteration=? AND optimizer_run_id=?
            """, (self.agent_run_id, self.iteration, self.run_id)).fetchall()
        return {str(row[0]): float(row[1]) for row in rows}

    def _constraints(self, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = conn.execute("""
            SELECT constraint_name,COUNT(*) AS checked,
                   SUM(CASE WHEN passed=0 THEN 1 ELSE 0 END) AS failed
            FROM optimization_constraint_check
            WHERE optimization_run_id=?
            GROUP BY constraint_name ORDER BY constraint_name
        """, (self.run_id,)).fetchall()
        examples = conn.execute("""
            SELECT interval_start,constraint_name,measured_value,limit_value,explanation
            FROM optimization_constraint_check
            WHERE optimization_run_id=? AND passed=0
            ORDER BY interval_start,constraint_name LIMIT 8
        """, (self.run_id,)).fetchall()
        total = sum(int(row["checked"]) for row in rows)
        failed = sum(int(row["failed"] or 0) for row in rows)
        return {
            "run_id": self.run_id,
            "total_checks": total,
            "failed_checks": failed,
            "by_constraint": [dict(row) for row in rows],
            "failed_examples": [dict(row) for row in examples],
        }

    def _tariff(self, conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("""
            SELECT profile_version,currency_code,timezone,config_json,captured_at
            FROM optimization_tariff_snapshot WHERE optimization_run_id=?
        """, (self.run_id,)).fetchone()
        if row is None:
            return {"available": False, "reason": "No tariff snapshot exists for this candidate."}
        config = json.loads(row["config_json"])
        return {
            "available": True,
            "profile_version": row["profile_version"],
            "currency_code": row["currency_code"],
            "timezone": row["timezone"],
            "captured_at": row["captured_at"],
            "is_synthetic": bool(config.get("is_synthetic")),
            "source_description": config.get("source_description"),
            "periods": config.get("periods", []),
        }

    def _interval_window(self, conn: sqlite3.Connection,
                         arguments: dict[str, Any]) -> dict[str, Any]:
        start = arguments.get("start_index")
        count = arguments.get("count")
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError("start_index must be a non-negative integer")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_INTERVAL_WINDOW:
            raise ValueError(f"count must be an integer from 1 to {MAX_INTERVAL_WINDOW}")
        rows = conn.execute("""
            SELECT r.interval_start,s.total_load_kw,s.pv_generation_kw,
                   s.grid_import_kw AS measured_grid_kw,r.grid_import_kw AS candidate_grid_kw,
                   r.battery_charge_kw,r.battery_discharge_kw,r.battery_soc_end_pct,
                   r.pv_curtailment_kw,r.load_shed_kw,c.import_rate_per_kwh,
                   c.import_cost,c.net_cost,c.currency_code
            FROM optimization_result r
            JOIN interval_summary s USING(interval_start)
            LEFT JOIN optimization_energy_cost c
              ON c.optimization_run_id=r.optimization_run_id
             AND c.interval_start=r.interval_start
            WHERE r.optimization_run_id=?
            ORDER BY r.interval_start LIMIT ? OFFSET ?
        """, (self.run_id, count, start)).fetchall()
        return {
            "run_id": self.run_id,
            "start_index": start,
            "interval_count": len(rows),
            "units": {"power": "kW", "soc": "%",
                      "energy_cost": f"{rows[0]['currency_code'] if rows else 'configured currency'} per 15-minute interval"},
            "intervals": [dict(row) for row in rows],
        }
