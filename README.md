# OpenCEM Hackathon Dataset (Compact)

A compact, real-measurement subset for a first microgrid scheduling prototype.

## Files

- `microgrid_15m_dec2025.csv` — recommended quick-start input for Python, pandas, Excel, or a spreadsheet.
- `microgrid_hackathon.db` — the same interval data in SQLite, plus matching context records and a `dataset_info` table.
- `build_hackathon_dataset.py` — rebuilds this subset from the full OpenCEM database when it is available.

## Scope and coverage

This subset uses OpenCEM's **December 2025** measurements from the CUHK-Shenzhen campus PV-and-battery microgrid. It contains **5,952 15-minute records**: 31 days × 96 intervals/day × 2 inverters. Every interval is represented for both inverters. `sample_count` reports the number of raw readings contributing to each interval mean (90–144 in this month). It also keeps **3,660 context records** in SQLite that overlap the selected measurement period.

## CSV columns

- `interval_start_utc`, `interval_start_epoch`: start of the 15-minute interval.
- `inverter`: subsystem identifier (1 or 2); keep the inverter dimension when modeling each battery/inverter separately.
- `sample_count`: number of high-frequency measurements averaged in the interval.
- `avg_pv1power_w`: mean reported PV1 power.
- `avg_outsumw_w`: mean inverter total output power; use as a load/output proxy, not a separately metered whole-site demand claim.
- `avg_gridpowerw_a_w`: mean reported grid power on phase A.
- `avg_battsoc_pct`, `avg_battvolt_v`, `avg_battcurr_a`, `avg_battchgpower_w`: mean battery readings as reported.

## Important modeling limits

- These are real measured values from Shenzhen, China; they are not representative of every Indian grid or microgrid.
- The selected month has no separate tariff or price series. Add a clearly sourced tariff profile before optimizing cost.
- The selected period reports PV1; other PV channels were blank in this period. Check hardware and channel meaning before treating PV1 as total PV for a subsystem.
- Grid power and battery current/charge-power values are kept with their reported signs. Confirm sign conventions before labeling import/export or charging/discharging.
- The interval fields are average power, not energy. Integrate power over elapsed time to calculate Wh/kWh; do not sum power samples.
- Timestamps are ISO 8601 UTC. Convert to the relevant local timezone for calendar or tariff rules.

## Suggested first prototype

Start with the CSV. Build a per-inverter 15-minute power-balance baseline, then add battery SOC/limits and a sourced tariff series. Compare optimized decisions with a simple baseline using grid energy/cost, solar use, battery cycling, and unmet load. Preserve the `sample_count` as a coverage/quality field.

Source: [OpenCEM dataset](https://github.com/OpenCEM-platform/opencem-dataset). Dataset license: CC BY 4.0; preserve attribution and cite the dataset if redistributing or publishing results.
