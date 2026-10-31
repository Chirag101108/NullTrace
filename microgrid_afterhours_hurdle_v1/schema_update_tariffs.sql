-- Additive tariff schema and explicitly synthetic demonstration tariff.
-- Period weekday_mask bits are Monday=bit 0 through Sunday=bit 6.
-- Period boundaries are local wall-clock minutes in the profile timezone.
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS tariff_profiles (
    profile_id TEXT PRIMARY KEY,
    microgrid_id TEXT NOT NULL REFERENCES microgrid(microgrid_id),
    grid_asset_id TEXT NOT NULL REFERENCES asset(asset_id),
    profile_name TEXT NOT NULL,
    currency_code TEXT NOT NULL CHECK(currency_code GLOB '[A-Z][A-Z][A-Z]'),
    timezone TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0,1)),
    is_synthetic INTEGER NOT NULL DEFAULT 0 CHECK(is_synthetic IN (0,1)),
    source_description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(microgrid_id, grid_asset_id, version)
);

-- A microgrid can have at most one active tariff profile. Historical inactive
-- profiles remain available for audit and optimization snapshots.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tariff_one_active_per_microgrid
    ON tariff_profiles(microgrid_id) WHERE is_active=1;

CREATE TABLE IF NOT EXISTS tariff_periods (
    period_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES tariff_profiles(profile_id),
    period_name TEXT NOT NULL,
    weekday_mask INTEGER NOT NULL CHECK(weekday_mask BETWEEN 1 AND 127),
    start_minute INTEGER NOT NULL CHECK(start_minute BETWEEN 0 AND 1439),
    end_minute INTEGER NOT NULL CHECK(end_minute BETWEEN 1 AND 1440),
    import_rate_per_kwh REAL NOT NULL CHECK(import_rate_per_kwh >= 0),
    export_rate_per_kwh REAL NOT NULL CHECK(export_rate_per_kwh >= 0),
    UNIQUE(profile_id, period_name),
    CHECK(start_minute < end_minute)
);
CREATE INDEX IF NOT EXISTS idx_tariff_periods_profile
    ON tariff_periods(profile_id, start_minute, end_minute);

-- Immutable JSON snapshot of the exact tariff schedule used by an optimizer
-- run, independent of later profile edits/deactivation.
CREATE TABLE IF NOT EXISTS optimization_tariff_snapshot (
    optimization_run_id TEXT PRIMARY KEY
        REFERENCES optimization_run(optimization_run_id),
    profile_id TEXT NOT NULL REFERENCES tariff_profiles(profile_id),
    profile_version INTEGER NOT NULL CHECK(profile_version > 0),
    currency_code TEXT NOT NULL CHECK(currency_code GLOB '[A-Z][A-Z][A-Z]'),
    timezone TEXT NOT NULL,
    config_json TEXT NOT NULL CHECK(json_valid(config_json)),
    captured_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_optimization_tariff_snapshot_no_update
BEFORE UPDATE ON optimization_tariff_snapshot
BEGIN
    SELECT RAISE(ABORT, 'optimization tariff snapshots are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_optimization_tariff_snapshot_no_delete
BEFORE DELETE ON optimization_tariff_snapshot
BEGIN
    SELECT RAISE(ABORT, 'optimization tariff snapshots are immutable');
END;

CREATE TABLE IF NOT EXISTS optimization_energy_cost (
    optimization_run_id TEXT NOT NULL,
    interval_start TEXT NOT NULL,
    grid_import_kwh REAL NOT NULL CHECK(grid_import_kwh >= 0),
    grid_export_kwh REAL NOT NULL CHECK(grid_export_kwh >= 0),
    import_rate_per_kwh REAL NOT NULL CHECK(import_rate_per_kwh >= 0),
    export_rate_per_kwh REAL NOT NULL CHECK(export_rate_per_kwh >= 0),
    import_cost REAL NOT NULL CHECK(import_cost >= 0),
    export_credit REAL NOT NULL CHECK(export_credit >= 0),
    net_cost REAL NOT NULL,
    currency_code TEXT NOT NULL CHECK(currency_code GLOB '[A-Z][A-Z][A-Z]'),
    PRIMARY KEY(optimization_run_id, interval_start),
    FOREIGN KEY(optimization_run_id, interval_start)
        REFERENCES optimization_result(optimization_run_id, interval_start)
);

-- Synthetic, illustrative India-style TOU data for local demonstrations only.
-- One all-days schedule covers every minute of the local week with no gaps.
-- Export is credited at the same synthetic period price as import (one-to-one
-- net-metering demonstration assumption), not as a real export contract.
INSERT OR IGNORE INTO tariff_profiles (
    profile_id, microgrid_id, grid_asset_id, profile_name, currency_code,
    timezone, version, is_active, is_synthetic, source_description, created_at
)
SELECT
    'TARIFF-SYNTHETIC-TOU-001', m.microgrid_id, a.asset_id,
    'Synthetic India TOU demonstration', 'INR', m.timezone, 1, 1, 1,
    'Synthetic demonstration schedule; not a utility tariff and not evidence of grid-export authorization.',
    '2026-10-31T00:00:00+05:30'
FROM microgrid AS m
JOIN asset AS a ON a.microgrid_id=m.microgrid_id
WHERE a.asset_id='GRID-POI-001' AND a.asset_type='grid_intertie';

INSERT OR IGNORE INTO tariff_periods (
    period_id, profile_id, period_name, weekday_mask, start_minute, end_minute,
    import_rate_per_kwh, export_rate_per_kwh
) VALUES
    ('TARIFF-SYNTHETIC-TOU-001-P1', 'TARIFF-SYNTHETIC-TOU-001', 'Off-peak',       127,    0,  360,  5.50,  5.50),
    ('TARIFF-SYNTHETIC-TOU-001-P2', 'TARIFF-SYNTHETIC-TOU-001', 'Daytime',        127,  360, 1020,  7.00,  7.00),
    ('TARIFF-SYNTHETIC-TOU-001-P3', 'TARIFF-SYNTHETIC-TOU-001', 'Evening peak',   127, 1020, 1320, 10.50, 10.50),
    ('TARIFF-SYNTHETIC-TOU-001-P4', 'TARIFF-SYNTHETIC-TOU-001', 'Late evening',   127, 1320, 1440,  7.00,  7.00);

COMMIT;
