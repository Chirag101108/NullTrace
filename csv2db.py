"""
Bulk-import all matching OpenCEM measurement CSV files in a directory into SQLite.

Default:
    python3 microgridOptimizer.py ./csv_directory

Database default:
    data/microgrid.db

Optional:
    python3 microgridOptimizer.py ./csv_directory --db data/microgrid.db
    python3 microgridOptimizer.py ./csv_directory --pattern "*.csv" --recursive
    python3 microgridOptimizer.py ./csv_directory --batch-size 10000

Expected schema is the supplied 82-column OpenCEM measurements header.
All files are imported into one SQLite table named `measurements`.

Data types inferred from the supplied header + sample row:
- read_ts: INTEGER (Unix timestamp)
- inverter: INTEGER
- battreserve, pvreserve, otherreserve, grid_reserve: TEXT
- all other measurement columns: REAL
- empty values: SQL NULL

Rows are uniquely identified by (read_ts, inverter). Repeated imports and
overlapping CSV partitions therefore do not duplicate existing observations.
"""

import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_COLUMNS = [
    "read_ts", "inverter", "battvolt", "battcurr", "battsoc", "battchgpower",
    "linechgcurr", "pvchgcurr", "battreserve", "pv1volt", "pv1curr", "pv1power",
    "pv2volt", "pv2curr", "pv2power", "pv3volt", "pv3curr", "pv3power",
    "pvreserve", "outvolta", "outcurra", "outfreq", "invcurra", "outw_a",
    "outva_a", "loadpercenta", "normalloadw_a", "outvoltb", "outcurrb",
    "invcurrb", "outw_b", "outva_b", "loadpercentb", "normalloadw_b",
    "outvoltc", "outcurrc", "invcurrc", "outw_c", "outva_c", "loadpercentc",
    "normalloadw_c", "outsumw", "outsumva", "sumloadpercent",
    "generatedpowerp_a", "generatedpowerp_b", "generatedpowerp_c",
    "generatedpowers_a", "generatedpowers_b", "generatedpowers_c",
    "generatedpowerq_a", "generatedpowerq_b", "generatedpowerq_c",
    "linevolta", "linecurra", "linefreq", "linevoltb", "linecurrb",
    "linevoltc", "linecurrc", "linepowerva_a", "linepowerva_b",
    "linepowerva_c", "linepowerw_a", "linepowerw_b", "linepowerw_c",
    "busvoltp", "busvoltn", "temper1", "temper2", "temper3", "temper4",
    "sir", "fanduty", "otherreserve", "gridpowerw_a", "gridpowerw_b",
    "gridpowerw_c", "gridcurr_a", "gridcurr_b", "gridcurr_c", "grid_reserve",
]

TEXT_COLUMNS = {"battreserve", "pvreserve", "otherreserve", "grid_reserve"}
INTEGER_COLUMNS = {"read_ts", "inverter"}
REAL_COLUMNS = set(EXPECTED_COLUMNS) - TEXT_COLUMNS - INTEGER_COLUMNS


def quote_identifier(name):
    return '"' + name.replace('"', '""') + '"'


def create_schema(connection):
    definitions = [
        '"read_ts" INTEGER NOT NULL',
        '"inverter" INTEGER NOT NULL',
    ]
    for column in EXPECTED_COLUMNS:
        if column in INTEGER_COLUMNS:
            continue
        sql_type = "TEXT" if column in TEXT_COLUMNS else "REAL"
        definitions.append(f"{quote_identifier(column)} {sql_type}")
    definitions.append('PRIMARY KEY ("read_ts", "inverter")')

    connection.execute(
        "CREATE TABLE IF NOT EXISTS measurements "
        f"({', '.join(definitions)})"
    )
    connection.execute("""
        CREATE TABLE IF NOT EXISTS import_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            table_name TEXT NOT NULL,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT NOT NULL,
            rows_read INTEGER NOT NULL,
            rows_inserted INTEGER NOT NULL,
            rows_ignored_as_duplicates INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'success'
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurements_read_ts
        ON measurements(read_ts)
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurements_inverter_read_ts
        ON measurements(inverter, read_ts)
    """)
    connection.commit()


def validate_header(fieldnames, csv_path):
    if not fieldnames:
        raise ValueError(f"{csv_path}: missing CSV header.")

    normalized = [name.strip() if name else "" for name in fieldnames]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{csv_path}: duplicate column names in header.")

    missing = sorted(set(EXPECTED_COLUMNS) - set(normalized))
    extra = sorted(set(normalized) - set(EXPECTED_COLUMNS))
    if missing or extra:
        details = []
        if missing:
            details.append("missing columns: " + ", ".join(missing))
        if extra:
            details.append("unexpected columns: " + ", ".join(extra))
        raise ValueError(f"{csv_path}: header mismatch ({'; '.join(details)}).")

    return normalized


def parse_integer(raw, column, line_number, csv_path):
    value = (raw or "").strip()
    if not value:
        raise ValueError(
            f"{csv_path}, line {line_number}: required {column} is empty."
        )
    try:
        numeric = float(value)
        if not numeric.is_integer():
            raise ValueError
        return int(numeric)
    except ValueError:
        raise ValueError(
            f"{csv_path}, line {line_number}: {column} must be an integer; "
            f"got {value!r}."
        ) from None


def parse_real(raw, column, line_number, csv_path):
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        raise ValueError(
            f"{csv_path}, line {line_number}: {column} must be numeric; "
            f"got {value!r}."
        ) from None


def convert_row(row, line_number, csv_path):
    result = []
    for column in EXPECTED_COLUMNS:
        raw = row.get(column)

        if column in INTEGER_COLUMNS:
            value = parse_integer(raw, column, line_number, csv_path)
        elif column in TEXT_COLUMNS:
            # Preserve reserve arrays exactly as text; their structure is not
            # interpreted until the dataset documentation is checked.
            value = raw.strip() if raw and raw.strip() else None
        else:
            value = parse_real(raw, column, line_number, csv_path)

        result.append(value)

    return tuple(result)


def import_one_csv(connection, csv_path, batch_size):
    started_at = datetime.now(timezone.utc).isoformat()
    rows_read = 0
    rows_inserted = 0

    column_sql = ", ".join(quote_identifier(c) for c in EXPECTED_COLUMNS)
    placeholders = ", ".join("?" for _ in EXPECTED_COLUMNS)
    insert_sql = (
        f"INSERT OR IGNORE INTO measurements ({column_sql}) "
        f"VALUES ({placeholders})"
    )

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        normalized_header = validate_header(reader.fieldnames, csv_path)

        # Normalize accidental whitespace in header names.
        if normalized_header != reader.fieldnames:
            handle.seek(0)
            reader = csv.DictReader(handle, fieldnames=normalized_header)
            next(reader, None)

        batch = []
        for row in reader:
            rows_read += 1
            batch.append(convert_row(row, reader.line_num, csv_path))

            if len(batch) >= batch_size:
                before = connection.total_changes
                connection.executemany(insert_sql, batch)
                rows_inserted += connection.total_changes - before
                connection.commit()
                batch.clear()

        if batch:
            before = connection.total_changes
            connection.executemany(insert_sql, batch)
            rows_inserted += connection.total_changes - before
            connection.commit()

    completed_at = datetime.now(timezone.utc).isoformat()
    duplicates = rows_read - rows_inserted

    connection.execute(
        """
        INSERT INTO import_log (
            source_file, table_name, started_at_utc, completed_at_utc,
            rows_read, rows_inserted, rows_ignored_as_duplicates, status
        ) VALUES (?, 'measurements', ?, ?, ?, ?, ?, 'success')
        """,
        (
            str(csv_path.resolve()),
            started_at,
            completed_at,
            rows_read,
            rows_inserted,
            duplicates,
        ),
    )
    connection.commit()

    return rows_read, rows_inserted, duplicates


def find_csv_files(directory, pattern, recursive):
    iterator = directory.rglob(pattern) if recursive else directory.glob(pattern)
    return sorted(path for path in iterator if path.is_file())


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Import every matching OpenCEM measurements CSV in a directory "
            "into one SQLite measurements table."
        )
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory containing CSV files",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/microgrid.db"),
        help="SQLite database path (default: data/microgrid.db)",
    )
    parser.add_argument(
        "--pattern",
        default="*.csv",
        help='Filename glob pattern (default: "*.csv")',
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search subdirectories recursively",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows per SQLite insert batch (default: 5000)",
    )
    args = parser.parse_args()

    if not args.directory.is_dir():
        parser.error(f"Directory does not exist: {args.directory}")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    csv_files = find_csv_files(args.directory, args.pattern, args.recursive)
    if not csv_files:
        parser.error(
            f"No files matching {args.pattern!r} found in {args.directory}"
        )

    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.db)

    total_read = 0
    total_inserted = 0
    total_duplicates = 0
    failures = []

    try:
        create_schema(connection)

        print(f"Found {len(csv_files)} CSV file(s).")
        print(f"Database: {args.db.resolve()}\n")

        for index, csv_path in enumerate(csv_files, start=1):
            print(f"[{index}/{len(csv_files)}] Importing {csv_path.name} ...")
            try:
                read, inserted, duplicates = import_one_csv(
                    connection, csv_path, args.batch_size
                )
                total_read += read
                total_inserted += inserted
                total_duplicates += duplicates
                print(
                    f"    read={read:,} inserted={inserted:,} "
                    f"duplicates={duplicates:,}"
                )
            except (OSError, csv.Error, sqlite3.Error, ValueError) as error:
                # Earlier committed batches remain safe: a rerun ignores rows
                # already inserted, thanks to the composite primary key.
                connection.rollback()
                failures.append((csv_path, str(error)))
                print(f"    FAILED: {error}", file=sys.stderr)

    finally:
        connection.close()

    print("\n========== IMPORT SUMMARY ==========")
    print(f"Files discovered: {len(csv_files)}")
    print(f"Files failed: {len(failures)}")
    print(f"Rows read: {total_read:,}")
    print(f"Rows inserted: {total_inserted:,}")
    print(f"Duplicate rows ignored: {total_duplicates:,}")
    print(f"Database: {args.db.resolve()}")

    if failures:
        print("\nFailed files:")
        for csv_path, error in failures:
            print(f"- {csv_path}: {error}")
        return 1

    print("All CSV files imported successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
