# DBIT AfterHours 1.0 — Microgrid Energy Scheduler (Hurdle Prototype v1)

## What this package contains

- `microgrid.sqlite` — the existing 7-day, 15-minute synthetic microgrid database, extended with battery degradation, flexibility verification, AI accountability and optimization tables.
- `optimizer.py` — first executable optimization brain.
- `schema_update_hurdle.sql` — additive SQL migration used for the new tables.
- Existing CSV source files and variable-assessment guide.
- `requirements.txt` — Python dependencies.

## Quick start

Python 3.10+ is recommended. Install dependencies:

```bash
python -m pip install numpy scipy
```

Run from the extracted package directory:

```bash
python optimizer.py
```

Or specify the database explicitly:

```bash
python optimizer.py --db microgrid.sqlite
```

Optional:
```bash
python optimizer.py --horizon 96
python optimizer.py --peak-penalty 1.0 --curtailment-penalty 0.01 --flex-risk-penalty 300
```

The program writes a new optimization run into `optimization_run`, `optimization_result`,
`optimization_load_result`, and `optimization_constraint_check`. It does not overwrite
source telemetry, declarations, or the original dispatch.

## Hurdle requirements represented

### Battery degradation
- `battery_degradation_model` stores the prototype replacement-cost, cycle-life,
  nominal-capacity and efficiency assumptions.
- `battery_health` stores SOH, nominal/usable capacity, EFC, temperature and
  resistance fields.
- `battery_degradation` stores per-interval throughput, DoD, C-rate, ageing
  increments, capacity fade and estimated cost.
- Initial optimizer degradation cost is linear throughput cost:

  `c_deg = replacement_cost / (2 * nominal_capacity_kWh * expected_EFC)`

  `C_deg = c_deg * sum((P_charge + P_discharge) * dt)`

The package uses illustrative assumptions: INR 400,000 replacement cost,
120 kWh nominal capacity and 4,000 expected equivalent full cycles. This produces
approximately INR 0.4167 per kWh of charge-plus-discharge throughput. Replace these
assumptions with defensible vendor/project values before making economic claims.
This is a tractable proxy, not a full electrochemical ageing model.

### Household flexibility
- `flexibility_declaration` stores reported flexibility and operating conditions.
- `flexibility_verification` stores independently estimated capability, confidence
  and the conservative usable amount.
- `flexibility_delivery` stores committed, requested and delivered response and
  shortfall.
- The optimizer uses `min(reported_power, verified_power)` and cannot schedule
  more flexibility than that bound.
- Four existing controllable load assets are represented as explicitly synthetic
  load proxies. They are not real household records.

One marked hurdle demonstration record contains an 8 kW declaration and a 5 kW
verified estimate. This demonstrates discrepancy detection, not proof of intentional
fraud.

### AI accountability
- `agent_run`, `agent_claim` and `claim_verification` retain agent assertions,
  evidence references and independent verification results.
- A marked synthetic demonstration contains an agent claim of 8 kW against an
  authoritative verification value of 5 kW; the checker marks the claim failed
  and records the corrected value. Agent claims never overwrite telemetry.

### Optimization objective

The prototype minimizes:

`J = lambda_peak * P_peak`
`  + weighted_flexible_reduction_penalties`
`  + lambda_curtailment * PV_curtailment_energy`
`  + battery_throughput_degradation_cost`
`  + lambda_flex_risk * expected_flexibility_shortfall`

Expected flexibility shortfall is approximated as `(1 - confidence) * committed_flexibility`.
Critical load is not shed. Battery SOC, power, grid import and flexibility bounds are
enforced in the linear program. The terminal SOC is constrained to equal the initial SOC.

## Test result included in this package

The optimizer was run in the build environment against all 672 intervals. It produced
a successful HiGHS linear-programming solution and stored 672 interval results and
5,376 passing constraint checks.

Illustrative run summary:
- Optimized maximum grid import: approximately 49.731 kW.
- Original maximum grid import: approximately 50.169 kW.
- Flexible load reduction: 0 kWh in this run.
- Battery charge throughput: approximately 0.337 kWh.
- Battery discharge throughput: approximately 0.301 kWh.
- SOC remained between approximately 61.733% and 62.000%.

The small peak reduction is the result for this particular dataset and objective
weights, not a guaranteed performance claim. Average grid import rises slightly
because battery round-trip losses are present; there is no tariff/energy-price model
in this prototype.

## Important model limitations — do not misrepresent these

This is a first operational prototype, not a field-ready controller:
- It uses aggregate active-power balance and holds measured baseline network losses
  fixed during optimization.
- It does not rerun nonlinear AC load flow or the electrothermal model on each
  candidate dispatch. Therefore, its dispatch must not be represented as proven
  voltage/ampacity-feasible under changed loading. The included checks do verify aggregate active-power balance, grid/battery limits, SOC limits, no simultaneous charge/discharge, and flexibility bounds.
- It does not model reactive-power dispatch, phase imbalance, harmonics, protection,
  transients, battery electrochemistry or detailed temperature-dependent ageing.
- Battery degradation is initially linear in energy throughput; DoD, C-rate,
  temperature, calendar ageing and resistance growth are stored/planned fields for
  subsequent model refinement.
- Household flexibility data and hurdle examples are synthetic. A discrepancy does
  not establish intent to deceive.
- Agent claim verification is deterministic and evidence-based; it does not establish
  the agent's internal intent or detect every possible hallucination.
- No real tariff is provided. Objective coefficients are explicit prototype penalty
  assumptions, not actual market prices.

## Database safety

Make a copy of `microgrid.sqlite` before experiments. The optimizer appends a new run
to optimizer-owned tables. It does not replace the source measurements or original
dispatch.
