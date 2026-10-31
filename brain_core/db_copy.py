"""Create isolated working databases from the configured sample database."""
from __future__ import annotations

import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def create_working_copy(sample_path: str | Path, run_id: str,
                        run_root: str | Path | None = None) -> Path:
    """Take a consistent SQLite backup and return a unique per-run copy.

    The sample is opened read-only and never receives optimizer or agent writes.
    A temporary destination is atomically renamed after SQLite's online backup
    completes, so interrupted copies are not mistaken for usable run databases.
    """
    sample = Path(sample_path).expanduser().resolve()
    if not sample.is_file():
        raise FileNotFoundError(f"Sample database not found: {sample}")
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)
    if not safe_id:
        safe_id = uuid.uuid4().hex
    destination_dir = Path(run_root).expanduser().resolve() if run_root else sample.parent / "runs"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{safe_id}.sqlite"
    if destination.exists():
        raise FileExistsError(f"Working database already exists: {destination}")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    try:
        source_uri = f"{sample.as_uri()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source:
            with closing(sqlite3.connect(temporary, timeout=30)) as target:
                source.backup(target, pages=2048, sleep=0.01)
                result = target.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise sqlite3.DatabaseError(f"Copied database failed integrity check: {result}")
                target.execute("""
                    CREATE TABLE afterhours_run_database (
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        copy_version INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                target.execute(
                    "INSERT INTO afterhours_run_database VALUES (1,1,?)",
                    (datetime.now(timezone.utc).isoformat(),),
                )
                target.commit()
        os.replace(temporary, destination)
        return destination
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
