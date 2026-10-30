# Synthetic Campus Microgrid Database

**Synthetic data only — not real industrial telemetry.** This is a deterministic, physically coherent starter dataset for the AfterHours scheduler.

## Scope
- 7 days, 15-minute intervals: 672 intervals, 19–25 October 2026.
- One grid-connected, balanced three-phase AC bus; 400 V line-to-line nominal; 50 Hz.
- 60 kW AC PV, 120 kWh / 30 kW battery, grid intertie, and five load groups.
- Asia/Kolkata ISO-8601 timestamps.
- Battery is the only storage technology.

## Contents
- `microgrid.sqlite`: populated SQLite database.
- `assets.csv`, `measurement_types.csv`, `measurements.csv`: normalized asset and measurement data.
- `weather.csv`, `battery_state.csv`, `dispatch.csv`, `interval_summary.csv`: weather, storage, control and aggregate data.
- `schema.sql`: complete database schema.
- `build_database.py`: creates a fresh SQLite database from the CSV files alone.
- `import_data.py`: reloads time-series CSV data into the packaged prebuilt database.
- `validate_data.py`: checks integrity and physical balances.

## Measurement identity
Semantic key: `(interval_start, asset_id, measurement_type_id, channel)`. A SQL UNIQUE constraint prevents duplicate semantic observations. `measurement_id` is only a surrogate key. Foreign keys protect asset and measurement-type references.

## Sign convention and electrical equations
All asset P and Q values are signed injections into the common AC bus. Loads are negative; grid import is positive. Grid P and Q close the single-bus balance each interval:
`sum(P_asset) = 0`, `sum(Q_asset) = 0`.

For balanced three-phase measurements:
`S_kVA = sqrt(P_kW^2 + Q_kvar^2)`
`I_A = 1000*S_kVA/(sqrt(3)*V_LL)`
`PF = abs(P_kW)/S_kVA` (1 when S is zero).

This is an aggregate balanced three-phase AC model, **not** a phase-domain load-flow model. It does not include feeder impedances, topology-dependent voltage drops, protection, harmonics or transient waveforms. Voltage is a plausible bounded bus-level signal, not a solved network result.

## Battery assumptions
120 kWh nominal capacity; 30 kW inverter rating; 20–95% SOC operating range; initial SOC 62%; charge efficiency 0.95 and discharge efficiency 0.94. Positive battery bus power means discharge; negative means charge. Dispatch is mutually exclusive. The generated controller charges on PV surplus and discharges in the evening peak.

## Profile assumptions
PV follows a daylight curve with bounded cloud variability. Loads follow daily profiles with lower weekend demand. The critical facility load is continuously present; HVAC, EV charging and pumping are time-dependent. Grid import/export is the balancing residual; import/export limits and tariffs are not yet modelled.

## Run
`python validate_data.py`
`python build_database.py`  # creates rebuilt_microgrid.sqlite from CSV + schema
`python import_data.py`     # reloads the packaged database

Only Python's standard library is required.

## Caveats
Synthetic profiles are representative assumptions, not site-calibrated values. Loads are aggregate groups. Reactive power uses fixed representative power factors. Battery degradation, BMS dynamics, temperature dependence and self-discharge are omitted. The database includes both raw normalized measurements and a convenience interval summary; do not add the two representations together.
