# Impedance-aware synthetic microgrid dataset

**All measurements are synthetic.** The feeder specifications and profiles are explicit engineering assumptions for a reproducible prototype, not claimed measurements from a real installation.

## Model scope
- 7 days, 15-minute intervals (672 intervals), Asia/Kolkata.
- Balanced three-phase, 400 V line-to-line, 50 Hz radial low-voltage network.
- Four buses: source/PCC, critical+building, DER, flexible loads.
- Three radial branches, nine phase conductors, six spatial sample stations per conductor.
- 60 kW AC PV, 120 kWh / 30 kW battery, grid intertie, five aggregate loads.
- Battery-only storage.

## Network physics
Each branch is represented by positive-sequence series impedance per phase. At 20 °C:
`R = rho * L / A`
`L' = (mu0 / (2*pi)) * ln(GMD/GMR)`
`X' = 2*pi*f*L'`
`Z = R + jX`
The conductor spatial samples store R' and X' at six positions. In this release each cable is uniform, so the samples are constant and piecewise-linear interpolation is exact; the trapezoidal integral reproduces the branch resistance. The included `electrical_model.py` exposes the corresponding calculation functions. Temperature correction is `R(T)=R20[1+alpha(T-20°C)]`.

A balanced positive-sequence backward/forward-sweep constant-PQ load flow calculates bus voltage phasors and branch currents for every interval using the declared 20 °C reference series resistance and inductive reactance. Reported branch losses are `3*I^2*R20` and reactive absorption is `3*I^2*X`, matching the impedance used by the solved load flow. Conductor temperature is separately estimated and stored. The temperature-corrected resistance relation `R(T)=R20[1+alpha(T-20°C)]` is available from the conductor metadata, but is not fed back into this release's load-flow iteration; that requires a coupled electrothermal iteration and is an explicit next refinement. Loads are constant-PQ aggregates at their specified power factors; PV and battery are unity-PF in this release.

## Topology
`BUS-000 -> BUS-010 -> {BUS-020, BUS-030}`.
- BR-001: 85 m, 35 mm² copper, GMD 0.12 m, GMR 7.2 mm.
- BR-002: 55 m, 25 mm² copper, GMD 0.10 m, GMR 5.8 mm.
- BR-003: 70 m, 25 mm² copper, GMD 0.10 m, GMR 5.8 mm.
These are declared synthetic design assumptions. Ampacity values are provisional ratings for this model, not installation-certified cable ratings.

## Key files
- `schema.sql`: normalized relational schema and constraints.
- `build_database.py`: builds SQLite from CSVs + schema.
- `validate_data.py`: integrity and physical consistency checks.
- `electrical_model.py`: reusable resistance, inductance, impedance, spatial integration and backward/forward-sweep functions.
- `microgrid.sqlite`: ready-to-query populated database.
- CSVs: assets, buses, branches, conductors, spatial conductor samples, measurements, bus/branch measurements, weather, battery, dispatch and interval summaries.

## Semantic keys
Measurement natural key: `(interval_start, asset_id, measurement_type_id, channel)`.
Conductor spatial sample key: `(conductor_id, sample_index)` and unique `(conductor_id, position_m)`.
Bus and branch measurements have composite primary keys `(interval_start,bus_id)` and `(interval_start,branch_id)`.

## Important limitations
This is a balanced positive-sequence quasi-static model, not a full electromagnetic transient or unbalanced four-wire simulation. Neutral current, phase imbalance, shunt capacitance, skin/proximity effects, joints/contact resistance, transformer magnetizing branches, protection, harmonics, and detailed thermal transients are not modelled. Reflection coefficients are not relevant to the quasi-static 50 Hz feeder model and are intentionally excluded. Aggregate equivalent resistance of all loads is not stored because constant-PQ loads do not have a fixed resistance. No single scalar “total microgrid resistance” is physically adequate for a branched network.

## Rebuild and validate
`python build_database.py`
`python validate_data.py`
Only Python standard library required.
