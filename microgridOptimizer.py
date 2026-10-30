"""
OpenCEM measurements CSV -> SQLite importer.

Usage:
    python microgridOptimizer.py path/to/measurements.csv
    python microgridOptimizer.py path/to/measurements.csv --db data/microgrid.db

Schema assumptions based on the supplied header and first data row:
- read_ts: INTEGER (Unix timestamp; example: 1752472412)
- inverter: INTEGER (example: 2)
- battreserve, pvreserve, otherreserve, grid_reserve: TEXT
  (the sample contains brace-delimited values such as "{0,0,0,0,0}")
- all remaining measurement fields: REAL
- blank CSV fields are stored as SQL NULL

The script validates the header, imports incrementally in batches, and uses
(read_ts, inverter) as the measurement identity so rerunning an import does
not duplicate rows.
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

TEXT_COLUMNS = {
    "battreserve",
    "pvreserve",
    "otherreserve",
    "grid_reserve",
}
INTEGER_COLUMNS = {"read_ts", "inverter"}
REAL_COLUMNS = set(EXPECTED_COLUMNS) - TEXT_COLUMNS - INTEGER_COLUMNS


def quote_identifier(name):
    return '"' + name.replace('"', '""') + '"'


def create_schema(connection):
    column_definitions = [
        '"read_ts" INTEGER NOT NULL',
        '"inverter" INTEGER NOT NULL',
    ]

    for column in EXPECTED_COLUMNS:
        if column in INTEGER_COLUMNS:
            continue
        sql_type = "TEXT" if column in TEXT_COLUMNS else "REAL"
        column_definitions.append(f"{quote_identifier(column)} {sql_type}")

    column_definitions.append('PRIMARY KEY ("read_ts", "inverter")')

    connection.execute(
        "CREATE TABLE IF NOT EXISTS measurements "
        f"({', '.join(column_definitions)})"
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
            rows_ignored_as_duplicates INTEGER NOT NULL
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


def validate_header(fieldnames):
    if not fieldnames:
        raise ValueError("CSV is empty or has no header.")

    # Strip accidental spaces around header names.
    normalized = [name.strip() if name is not None else "" for name in fieldnames]

    if len(normalized) != len(set(normalized)):
        raise ValueError("CSV header contains duplicate column names.")

    missing = sorted(set(EXPECTED_COLUMNS) - set(normalized))
    unexpected = sorted(set(normalized) - set(EXPECTED_COLUMNS))

    if missing or unexpected:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise ValueError("CSV header mismatch (" + "; ".join(details) + ").")

    return normalized


def parse_integer(value, column, line_number):
    value = (value or "").strip()
    if not value:
        return None
    try:
        number = float(value)
        if not number.is_integer():
            raise ValueError
        return int(number)
    except ValueError:
        raise ValueError(
            f"CSV line {line_number}: {column} must be an integer; got {value!r}"
        ) from None


def parse_real(value, column, line_number):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        raise ValueError(
            f"CSV line {line_number}: {column} must be numeric; got {value!r}"
        ) from None


def convert_row(row, line_number):
    converted = []

    for column in EXPECTED_COLUMNS:
        raw_value = row.get(column)

        if column in INTEGER_COLUMNS:
            value = parse_integer(raw_value, column, line_number)
            if value is None:
                raise ValueError(
                    f"CSV line {line_number}: required field {column!r} is empty."
                )
        elif column in TEXT_COLUMNS:
            # Preserve brace-delimited reserve arrays exactly as text.
            value = raw_value.strip() if raw_value and raw_value.strip() else None
        else:
            value = parse_real(raw_value, column, line_number)

        converted.append(value)

    return tuple(converted)


def import_csv(csv_path, db_path, batch_size):
    started_at = datetime.now(timezone.utc).isoformat()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    rows_read = 0
    rows_inserted = 0

    quoted_columns = ", ".join(quote_identifier(c) for c in EXPECTED_COLUMNS)
    placeholders = ", ".join("?" for _ in EXPECTED_COLUMNS)
    insert_sql = (
        f"INSERT OR IGNORE INTO measurements ({quoted_columns}) "
        f"VALUES ({placeholders})"
    )

    try:
        create_schema(connection)
        connection.commit()

        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file, delimiter=",")
            normalized_header = validate_header(reader.fieldnames)

            # DictReader uses header names as dictionary keys. Rebuild the reader
            # if the input header had surrounding whitespace.
            if normalized_header != reader.fieldnames:
                csv_file.seek(0)
                reader = csv.DictReader(csv_file, fieldnames=normalized_header)
                next(reader, None)

            batch = []
            for row in reader:
                rows_read += 1
                batch.append(convert_row(row, reader.line_num))

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
                rows_read, rows_inserted, rows_ignored_as_duplicates
            ) VALUES (?, 'measurements', ?, ?, ?, ?, ?)
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
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import the OpenCEM measurements CSV into SQLite."
    )
    parser.add_argument("csv_file", type=Path, help="Input measurements CSV")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/microgrid.db"),
        help="SQLite database path (default: data/microgrid.db)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows inserted per batch (default: 5000)",
    )
    args = parser.parse_args()

    if not args.csv_file.is_file():
        parser.error(f"CSV file not found: {args.csv_file}")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    try:
        read, inserted, duplicates = import_csv(
            args.csv_file, args.db, args.batch_size
        )
    except (OSError, csv.Error, sqlite3.Error, ValueError) as error:
        print(f"Import failed: {error}", file=sys.stderr)
        return 1

    print(f"Database: {args.db.resolve()}")
    print(f"Rows read: {read:,}")
    print(f"Rows inserted: {inserted:,}")
    print(f"Duplicate rows ignored: {duplicates:,}")
    print("Import completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
