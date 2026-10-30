"""Balanced three-phase quasi-static feeder calculations used by the synthetic dataset."""
from __future__ import annotations
import math, cmath
MU0 = 4.0 * math.pi * 1e-7

def conductor_resistance(rho_ohm_m: float, length_m: float, area_mm2: float,
                         temperature_c: float = 20.0, reference_temperature_c: float = 20.0,
                         alpha_per_c: float = 0.00393) -> float:
    """DC-equivalent conductor resistance in ohms; skin/proximity effects omitted."""
    if min(rho_ohm_m, length_m, area_mm2) <= 0:
        raise ValueError("rho, length and area must be positive")
    rho_t = rho_ohm_m * (1.0 + alpha_per_c * (temperature_c-reference_temperature_c))
    return rho_t * length_m / (area_mm2 * 1e-6)

def conductor_inductance_per_m(gmd_m: float, gmr_m: float) -> float:
    """Positive-sequence inductance per metre for simplified transposed 3-phase geometry."""
    if gmd_m <= 0 or gmr_m <= 0 or gmd_m <= gmr_m:
        raise ValueError("Require GMD > GMR > 0")
    return MU0/(2*math.pi)*math.log(gmd_m/gmr_m)

def series_impedance(r_ohm: float, inductance_h: float, frequency_hz: float,
                     capacitance_f: float = 0.0) -> complex:
    """Series Z = R + j(wL); shunt capacitance is intentionally not included here."""
    if r_ohm < 0 or inductance_h < 0 or frequency_hz <= 0 or capacitance_f < 0:
        raise ValueError("Invalid impedance parameters")
    return complex(r_ohm, 2*math.pi*frequency_hz*inductance_h)

def integrate_piecewise_linear(x, y) -> float:
    """Trapezoidal integral for ordered spatial samples."""
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("Need at least two paired samples")
    if any(x[i+1] <= x[i] for i in range(len(x)-1)):
        raise ValueError("x positions must be strictly increasing")
    return sum((y[i]+y[i+1])*0.5*(x[i+1]-x[i]) for i in range(len(x)-1))

def backward_forward_sweep(bus_order, parent, children, branch_z_ohm,
                           p_load_kw, q_load_kvar, source_voltage_ll_v=400.0,
                           tolerance_v=1e-9, max_iterations=100):
    """Balanced positive-sequence radial constant-PQ load flow.

    P/Q dictionaries are positive for consumption and negative for generation.
    Returns phase-neutral complex bus voltages and complex branch currents.
    """
    vs = source_voltage_ll_v / math.sqrt(3)
    V = {b: complex(vs, 0) for b in bus_order}
    Ibranch = {}
    for _ in range(max_iterations):
        Iinj = {}
        for bus in bus_order[1:]:
            S_va = complex(p_load_kw.get(bus, 0), q_load_kvar.get(bus, 0))*1000/3
            Iinj[bus] = (S_va/V[bus]).conjugate() if abs(V[bus]) > 1e-12 else 0j
        for bus in reversed(bus_order[1:]):
            Ibranch[(parent[bus][0], bus)] = Iinj[bus] + sum(
                Ibranch.get((bus, child), 0j) for child in children[bus])
        Vnew = {bus_order[0]: complex(vs, 0)}
        for bus in bus_order[1:]:
            upstream, branch_id = parent[bus]
            Vnew[bus] = Vnew[upstream] - branch_z_ohm[branch_id]*Ibranch[(upstream,bus)]
        error = max(abs(Vnew[b]-V[b]) for b in bus_order)
        V = Vnew
        if error < tolerance_v:
            break
    else:
        raise RuntimeError("Load-flow iteration did not converge")
    return V, Ibranch
