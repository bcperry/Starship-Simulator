"""Simplified Raptor (LOX / methane) engine model.

First-principles but deliberately approximate:
  - The chamber is fed by pressure-fed choked flow at the injector:
        mdot = C* * A_t * P_c        (C* the "star function" of the hot gas)
    The mixture ratio splits that flow into oxidiser and fuel.
  - The expansion nozzel is isentropic: the exit Mach number follows from
    the area ratio (solved by bisection), and exit temperature/pressure from
    the standard relations.
  - Thrust:  F = mdot * ve + (P_e - P_a) * A_e
  - Throttling is modelled as a first-order lag of chamber pressure toward
    the demanded value (Raptor throttles from roughly 40% to 100%+).
  - Optional natural high-frequency jitter (``noise_frac``): per-step zero-mean
    Gaussian perturbation of chamber pressure and expansion efficiency, so
    mdot / Pe / Isp / thrust all breathe together like a real engine burning.
  - Optional finite propellant load: the engine cuts off when the limiting
    component runs dry.

Not a certification model: no regenerative cooling, no HX losses, no
compressor transients, single effective gas for all combustion products.
Good enough to size a vehicle and see how throttle / feed / back pressure
interact.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import cache

G0 = 9.80665          # m/s^2
R_UNIV = 8.314462     # J/(mol K)


@dataclass(frozen=True)
class RaptorEngine:
    """Parameterisable Raptor-class full-flow staged-combustion engine.

    Defaults approximate a Raptor 3 running at full power:
    ~2.5–2.8 MN sea level, Isp ~350 s, chamber pressure 350 bar.
    """

    name: str = "Raptor 3"

    # feed / mixture
    mixture_ratio: float = 3.6          # O/F by mass (LOX / CH4)
    chamber_pressure: float = 3.5e7     # Pa, target at 100% throttle (~35 MPa / 350 bar)
    throat_area: float = 0.043          # m^2, injector throat

    # nozzle
    nozzle_area_ratio: float = 30.0     # A_e / A_t (fixed geometry)

    # combustion products (single effective gas; effective values calibrated
    # so the model lands on the published Raptor 3 spec sheet)
    gas_temperature: float = 4200.0     # K, effective chamber temperature
    gas_molar_mass: float = 0.020       # kg/mol, effective products (~CO2/H2O mix)
    gas_gamma: float = 1.24             # -
    cstar_efficiency: float = 1.00      # -  (<1: injector/combustion losses)
    thrust_efficiency: float = 1.00     # -  (<1: real-gas / non-isentropic losses)

    # dynamics / load
    throttle_time_constant: float = 0.5  # s, first-order chamber-pressure lag
    propellant_mass: float = 0.0         # kg, 0 = unlimited reservoir

    # natural high-frequency jitter (combustion acoustics / feed turbulence):
    # per-step zero-mean Gaussian perturbation, fraction of the nominal value
    # (e.g. 0.02 = 2 % rms on chamber pressure; scales the derived flows/thrust)
    noise_frac: float = 0.0

    @property
    def exit_area(self) -> float:
        return self.nozzle_area_ratio * self.throat_area

    @property
    def gas_constant(self) -> float:
        """Specific gas constant of the products, J/(kg K)."""
        return R_UNIV / self.gas_molar_mass

    @property
    def max_mass_flow(self) -> float:
        """kg/s at 100% throttle and full chamber pressure."""
        return self._cstar() * self.throat_area * self.chamber_pressure

    @property
    def specific_mass_flow(self) -> float:
        """kg/s per Pa of chamber pressure (choked flow)."""
        return self._cstar() * self.throat_area

    def _cstar(self) -> float:
        """Ideal C* (N s / kg form, i.e. mdot = C* A_t P_c) for the products."""
        g = self.gas_gamma
        r = self.gas_constant
        t = self.gas_temperature
        return math.sqrt(g / (r * t)) * (2.0 / (g + 1.0)) ** ((g + 1.0) / (2.0 * (g - 1.0)))

    def __post_init__(self) -> None:
        for n, v in (("mixture_ratio", self.mixture_ratio),
                     ("chamber_pressure", self.chamber_pressure),
                     ("throat_area", self.throat_area),
                     ("nozzle_area_ratio", self.nozzle_area_ratio),
                     ("gas_temperature", self.gas_temperature),
                     ("gas_molar_mass", self.gas_molar_mass)):
            if v <= 0:
                raise ValueError(f"{n} must be > 0, got {v}")
        if not 1.0 < self.gas_gamma < 2.0:
            raise ValueError(f"gas_gamma must be in (1, 2), got {self.gas_gamma}")
        if self.throttle_time_constant < 0:
            raise ValueError("throttle_time_constant must be >= 0")
        if self.propellant_mass < 0:
            raise ValueError("propellant_mass must be >= 0")
        if not 0.0 <= self.noise_frac <= 0.05:
            raise ValueError(f"noise_frac must be in [0, 0.05], got {self.noise_frac}")
        for n, v in (("cstar_efficiency", self.cstar_efficiency),
                     ("thrust_efficiency", self.thrust_efficiency)):
            if not 0.0 < v <= 1.5:
                raise ValueError(f"{n} must be in (0, 1.5], got {v}")


@dataclass
class EngineState:
    """Mutable engine state, advanced by RaptorEngine.step()."""

    chamber_pressure: float = 0.0     # Pa, current (lags throttle demand)
    ox_mass: float = 0.0              # kg LOX remaining (0 with unlimited load)
    fuel_mass: float = 0.0            # kg CH4 remaining (0 with unlimited load)

    def __init__(self, engine: RaptorEngine,
                 ox_mass: float | None = None, fuel_mass: float | None = None,
                 seed: int | str | None = None) -> None:
        self.chamber_pressure = 0.0
        # independent high-frequency noise stream, reproducible for a given
        # seed (distinct engine instances draw distinct streams)
        self._noise_rng = random.Random(seed)
        if engine.propellant_mass > 0:
            frac_ox = engine.mixture_ratio / (1.0 + engine.mixture_ratio)
            self.ox_mass = ox_mass if ox_mass is not None else engine.propellant_mass * frac_ox
            self.fuel_mass = fuel_mass if fuel_mass is not None else \
                engine.propellant_mass - self.ox_mass
        else:
            self.ox_mass, self.fuel_mass = ox_mass or 0.0, fuel_mass or 0.0

    @property
    def propellant_remaining(self) -> float:
        return self.ox_mass + self.fuel_mass


@dataclass(frozen=True)
class EngineOutput:
    """One timestep of engine operation."""

    t: float
    throttle: float              # 0..1 demand
    chamber_pressure: float      # Pa, actual
    mdot_total: float            # kg/s
    mdot_ox: float               # kg/s
    mdot_fuel: float             # kg/s
    exit_pressure: float         # Pa
    exit_mach: float
    exhaust_velocity: float      # m/s
    thrust: float                # N, at the given ambient pressure
    isp: float                   # s, thrust-based
    ambient_pressure: float      # Pa
    propellant_remaining: float  # kg (0 if unlimited)


def _exit_mach_for_area_ratio(g: float, area_ratio: float) -> float:
    """Solve A/A* = area_ratio for Mach number (>1) by bisection."""
    expn = (g + 1.0) / (2.0 * (g - 1.0))

    def ratio(m: float) -> float:
        return (1.0 / m) * math.sqrt((1.0 + (g - 1.0) * m * m / 2.0) ** (2.0 * expn)) \
            / ((g + 1.0) / 2.0) ** expn

    lo, hi = 1.0 + 1e-9, 30.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if ratio(mid) < area_ratio:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@cache
def _get_model(eng: RaptorEngine) -> _EngineModel:
    """Cache the (expensive) model constants per engine configuration.

    Exit Mach, expansion ratios, cp etc. depend only on the engine's
    fixed geometry and gas properties, so they are computed once and
    reused across every step. RaptorEngine is a frozen dataclass of
    hashable values, so it makes a safe cache key.
    """
    return _EngineModel(eng)


class _EngineModel:
    """Pure (stateless) thermodynamics for a RaptorEngine."""

    def __init__(self, eng: RaptorEngine) -> None:
        self.e = eng
        self.g = eng.gas_gamma
        self.r = eng.gas_constant
        self.cp = eng.gas_constant * eng.gas_gamma / (eng.gas_gamma - 1.0)
        self.me = _exit_mach_for_area_ratio(eng.gas_gamma, eng.nozzle_area_ratio)
        # (P_e/P_c) and (T_e/T_c) factors are fixed by geometry+gas.
        self.pe_factor = (1.0 + (self.g - 1.0) * self.me * self.me / 2.0) ** (
            -self.g / (self.g - 1.0))
        self.te_factor = 1.0 / (1.0 + (self.g - 1.0) * self.me * self.me / 2.0)

    def statics(self, pc: float) -> tuple[float, float, float]:
        """(mdot, exhaust_velocity) for a given chamber pressure."""
        e = self.e
        mdot = e.cstar_efficiency * e.specific_mass_flow * pc
        te = self.te_factor * e.gas_temperature
        pe = self.pe_factor * pc
        ve = e.thrust_efficiency * math.sqrt(max(2.0 * self.cp * (e.gas_temperature - te), 0.0))
        return mdot, pe, ve


def step(engine: RaptorEngine, state: EngineState,
         throttle: float, ambient_pressure: float = 101_325.0,
         dt: float = 0.01, t: float = 0.0) -> EngineOutput:
    """Advance the engine state by dt seconds at the given throttle demand.

    throttling: chamber pressure follows 1st-order lag toward throttle*Pc_full.
    cutoff:     when the loaded propellant is exhausted, pc decays to 0.
    """
    eng = engine
    tau = max(eng.throttle_time_constant, dt)

    loaded = eng.propellant_mass > 0.0
    cutoff = loaded and (state.ox_mass <= 0.0 or state.fuel_mass <= 0.0)
    if cutoff:
        target = 0.0  # feeds starve, chamber bleeds down
    else:
        target = eng.chamber_pressure * max(min(throttle, 1.5), 0.0)

    # first-order chamber tracking, then consume propellant at actual flow
    state.chamber_pressure += (target - state.chamber_pressure) * dt / tau

    # natural high-frequency jitter (combustion acoustics / feed turbulence):
    # zero-mean white noise on chamber pressure + a smaller independent
    # efficiency wobble. The choked-flow relation carries the pressure jitter
    # into mdot and Pe, so the whole output breathes together like a real
    # engine; ``noise_frac = 0`` reproduces the deterministic baseline.
    pc = state.chamber_pressure
    gain = 1.0
    if eng.noise_frac > 0.0 and not cutoff:
        g = state._noise_rng.gauss(0.0, eng.noise_frac)
        pc = max(pc * (1.0 + g), 0.0)
        gain = 1.0 + state._noise_rng.gauss(0.0, 0.5 * eng.noise_frac)

    model = _get_model(eng)
    mdot, pe, ve = model.statics(pc)

    if loaded and mdot > 0.0:
        frac_ox = eng.mixture_ratio / (1.0 + eng.mixture_ratio)
        d_ox = mdot * frac_ox * dt
        d_fuel = mdot * (1.0 - frac_ox) * dt
        if d_ox > 0.0 and d_fuel > 0.0:  # guard float underflow at tiny pc
            # shrink to whatever the remaining load can sustain within this step
            scale = min(1.0, state.ox_mass / d_ox, state.fuel_mass / d_fuel)
            d_ox *= scale
            d_fuel *= scale
        new_ox = state.ox_mass - d_ox
        new_fuel = state.fuel_mass - d_fuel
        # squashes denormal dust left over from the final scaled step: once the
        # remainder is below ~1% of a step's consumption, the feed is starved
        eps = max(1e-12, 0.01 * (d_ox + d_fuel))
        state.ox_mass = max(0.0, new_ox) if new_ox > eps else 0.0
        state.fuel_mass = max(0.0, new_fuel) if new_fuel > eps else 0.0
        mdot_consumed = (d_ox + d_fuel) / dt
        thrust = mdot_consumed * ve * gain + (pe - ambient_pressure) * eng.exit_area
    else:
        thrust = mdot * ve * gain + (pe - ambient_pressure) * eng.exit_area

    return EngineOutput(
        t=t,
        throttle=throttle,
        chamber_pressure=state.chamber_pressure,
        mdot_total=mdot,
        mdot_ox=mdot * eng.mixture_ratio / (1.0 + eng.mixture_ratio),
        mdot_fuel=mdot / (1.0 + eng.mixture_ratio),
        exit_pressure=pe,
        exit_mach=model.me,
        exhaust_velocity=ve,
        thrust=thrust,
        isp=thrust / (mdot * G0) if mdot > 1e-9 else 0.0,
        ambient_pressure=ambient_pressure,
        propellant_remaining=state.ox_mass + state.fuel_mass,
    )


def run(engine: RaptorEngine, throttle: float | list[float] = 1.0,
        duration: float = 10.0, ambient_pressure: float = 101_325.0,
        dt: float = 0.01) -> list[EngineOutput]:
    """Burn the engine on a throttle profile and collect a time series.

    throttle may be a scalar (constant) or a list sampled every dt.
    """
    if isinstance(throttle, (int, float)):
        profile = [float(throttle)]
    else:
        profile = [max(0.0, float(x)) for x in throttle]

    state = EngineState(engine)
    out: list[EngineOutput] = []
    n_steps = round(duration / dt)
    for i in range(n_steps):
        demand = profile[min(i, len(profile) - 1)]
        out.append(step(engine, state, demand, ambient_pressure, dt, t=i * dt))
    return out


def summary(engine: RaptorEngine, sl: EngineOutput | None = None,
            vac: EngineOutput | None = None) -> str:
    """Static spec sheet: rated flows plus SL/vacuum point performance."""
    lines = [
        (f"{engine.name}: MR={engine.mixture_ratio:.2f}  "
        f"Pc={engine.chamber_pressure / 1e6:.1f} MPa  "
        f"throat={engine.throat_area * 1e4:.1f} cm^2  "
        f"area ratio={engine.nozzle_area_ratio:.1f}  "
        f"Tc={engine.gas_temperature:.0f} K"),
        (f"  max mdot:   {engine.max_mass_flow:9.1f} kg/s "
        f"(LOX {engine.max_mass_flow * engine.mixture_ratio / (1 + engine.mixture_ratio):.1f} / "
        f"CH4 {engine.max_mass_flow / (1 + engine.mixture_ratio):.1f} kg/s)"),
    ]
    for label, o in (("SL ", sl), ("vac", vac)):
        if o:
            lines.append(
                f"  {label} thrust:   {o.thrust / 1e6:9.3f} MN   "
                f"Isp={o.isp:7.1f} s   ve={o.exhaust_velocity:7.1f} m/s   "
                f"Pe={o.exit_pressure / 1e5 * 100:8.2f} kPa"
            )
    return "\n".join(lines)


# -- vehicle presets ---------------------------------------------------------
# Same physics, different hardware: the presets only change geometry /
# efficiency so one Raptor sim backs every engine on the full stack.

#: Sea-level Raptor 3 (Super Heavy's 33, and Starship's three sea-level engines).
#: ~2.55–2.7 MN sea level / ~2.7–2.9 MN vacuum, Isp ~350–375 s, ~850 kg/s at 100 %
#: — in band with the published Raptor 3 (2.45–2.75 MN / ~350 s / 350 bar).
def raptor_v3() -> RaptorEngine:
    return RaptorEngine()

#: Back-compat alias (pre-Raptor-3 preset name).
raptor_v2 = raptor_v3


#: Raptor 3 Vacuum: vacuum-optimised high-expansion nozzle (A_e/A_t ≈ 110).
#: ~1.9 MN sea level (over-expanded, ~30 % loss) but ~3.0 MN vacuum /
#: Isp ≈ 390 s for the upper stage.
def raptor_vacuum() -> RaptorEngine:
    return RaptorEngine(
        name="Raptor 3 Vacuum",
        throat_area=0.040,
        nozzle_area_ratio=110.0,
    )
