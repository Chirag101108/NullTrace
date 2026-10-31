# Aleph: AfterHours microgrid operating index

Aleph is a dimensionless 0–100 summary for a validated optimizer candidate.
It is an engineering score for this prototype, not a probability, a physical
efficiency by itself, or a safety certificate. Each `Aleph(t)` value is computed
from the beginning of the optimized horizon through interval `t`; the horizon
score is the last point. The time-series API uses the input interval timestamps
(15-minute local intervals with the stored Asia/Kolkata offset) on the x-axis.

## Weights

| Component | Weight |
| --- | ---: |
| Energy adequacy | 0.25 |
| Physical efficiency | 0.20 |
| Renewable utilization | 0.15 |
| Storage performance | 0.15 |
| Economic performance | 0.15 |
| Network constraint compliance | 0.10 |

The weights sum to 1.0. Aleph is `Σ(weight_i × component_i)`. If any component
needed by the formula is unavailable, Aleph is `null`; the remaining weights
are never rescaled. Optimizer feasibility checks are enforced independently.

## Components

All component scores are on a 0–100 scale and calculated cumulatively through
each interval `t`.

### Energy adequacy

`100 × (0.5 × E_load_served / E_load_demand + 0.5 × E_critical_served / E_critical_demand)`

Critical loads are not dispatchable in the optimizer, so their service is a hard
model constraint. Critical service is read from the supplied measurements; if
those measurements are missing, the component is unavailable.

### Physical efficiency

`100 × E_load_served / (E_grid_import + E_PV_used − ΔE_stored)`

`ΔE_stored = (SOC_t − SOC_0) × battery_capacity / 100`. This is the energy
boundary identity for the cumulative prefix: it accounts for net battery energy
stored or withdrawn without adding battery discharge as a new external source.
At the terminal SOC, which the optimizer constrains to equal the initial SOC,
the denominator reduces to cumulative grid import plus used PV. If the
denominator is not positive, or the ratio exceeds 100% beyond numerical
tolerance, the component is unavailable rather than clipped into a plausible
looking score.

### Renewable utilization

`100 × E_PV_used / E_PV_generated`

`E_PV_used = E_PV_generated − E_PV_curtailed`. If no PV generation exists in
the cumulative horizon, this component is unavailable.

### Storage performance

`100 × η_charge × η_discharge × (1 − min(1, C_degradation / C_replacement))`

The efficiencies, replacement cost and degradation cost come from the battery
model and optimizer output. Degradation cost is the existing linear throughput
proxy, not a detailed electrochemical lifetime prediction. SOC and power limits
remain hard checks; they are not traded against Aleph points.

### Economic performance

The candidate bill is the sum of interval import energy cost. The measured
baseline bill uses the signed historical grid measurement: positive power is
charged at the import rate; negative power receives the configured export
credit. The prototype optimizer has no export variable, so candidate export and
candidate export credit are exactly zero.

If the measured baseline bill is positive:

`100 × min(1, baseline_bill / candidate_bill)`

with a zero candidate bill scoring 100. If the baseline bill is non-positive,
the component is unavailable because this prototype does not define a sensible
positive-bill ratio benchmark for that case.

The demo tariff is synthetic: INR 5.50/kWh from 00:00–06:00, INR 7.00/kWh from
06:00–17:00, INR 10.50/kWh from 17:00–22:00, and INR 7.00/kWh from 22:00–24:00.
The demo assumes one-to-one export credit at each matching time-of-use rate.
Neither those prices nor export permission are represented as real utility
terms. Actual profiles must replace them before financial claims are made.

### Network constraint compliance

The current optimizer score is 100 when every persisted optimizer constraint
check passes and unavailable otherwise. These checks cover aggregate active
power balance, grid import rating, battery power/SOC limits, no simultaneous
charge/discharge, and conservative flexibility bounds. The prototype does not
re-solve feeder AC voltage or thermal ampacity for each candidate, so this
component does **not** certify full feeder network feasibility.

## Known scope limits

- Network losses in the LP remain the measured baseline losses.
- Export is observed in historical signed grid data but is not a decision
  variable; the optimized export series is zero.
- The demo schedule is synthetic and is not an official Asia/Kolkata tariff.
- Battery degradation is a linear throughput cost proxy.
- The score is meaningful only alongside its component values, validation
  checks, input profile, and listed limitations.
