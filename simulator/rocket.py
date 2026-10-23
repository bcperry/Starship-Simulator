"""Minimal 1-D rocket flight model.

Model (vertical launch, standard atmosphere approximation):
  - Constant gravity g
  - Exponential atmosphere: rho(h) = rho0 * exp(-h / H)
  - Quadratic drag: F_d = 0.5 * rho * Cd * A * v^2
  - Solid-motor style thrust: constant T during burnout, linear mass flow
  - Semi-implicit Euler integration

Good enough to sanity-check Isp/mass-ratio intuition, not for certification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

G0 = 9.81          # m/s^2, standard gravity
RHO0 = 1.225       # kg/m^3, sea-level air density
SCALE_HEIGHT = 8500.0  # m, atmospheric scale height


@dataclass(frozen=True)
class Rocket:
    """Rocket / motor parameters."""

    dry_mass: float = 10.0        # kg, rocket structure without propellant
    prop_mass: float = 25.0       # kg, propellant load
    thrust: float = 800.0         # N, constant during burn
    burn_time: float = 6.0        # s
    drag_coeff: float = 0.75      # Cd, dimensionless
    area: float = 0.0181          # m^2, frontal area (e.g. 152.4 mm tube ~ 0.018 m^2)
    delay_time: float = 3.0       # s, coast delay before recovery (optional)
    name: str = "A12-ish"

    @property
    def wet_mass(self) -> float:
        return self.dry_mass + self.prop_mass

    @property
    def prop_rate(self) -> float:
        """kg/s propellant consumption during burn."""
        return self.prop_mass / self.burn_time

    @property
    def exhaust_velocity(self) -> float:
        """Equivalent exhaust velocity ve = T / mdot (m/s), Isp = ve/g0."""
        return self.thrust / self.prop_rate

    def __post_init__(self) -> None:
        for n, v in (
            ("dry_mass", self.dry_mass),
            ("prop_mass", self.prop_mass),
            ("thrust", self.thrust),
        ):
            if v <= 0:
                raise ValueError(f"{n} must be > 0, got {v}")
        if not 0 < self.burn_time:
            raise ValueError(f"burn_time must be > 0, got {self.burn_time}")
        if self.drag_coeff * self.area < 0:
            raise ValueError("drag_coeff and area must be >= 0")


@dataclass
class SimResult:
    """Time series and derived stats for one launch."""

    t: list[float] = field(default_factory=list)
    h: list[float] = field(default_factory=list)
    v: list[float] = field(default_factory=list)
    m: list[float] = field(default_factory=list)

    # key stats
    burnout_time: float = 0.0
    burnout_altitude: float = 0.0
    burnout_velocity: float = 0.0
    apogee_time: float = 0.0
    apogee_altitude: float = 0.0
    landing_time: float = 0.0
    landing_velocity: float = 0.0
    max_speed: float = 0.0


def _rho(h: float) -> float:
    return RHO0 * math.exp(-max(h, 0.0) / SCALE_HEIGHT)


def simulate(rocket: Rocket, dt: float = 0.005, max_time: float = 120.0) -> SimResult:
    """Integrate the flight and return trajectory + stats.

    Phases: ignition (t<delay handled as t=0 here), powered burn, coast
    (incl. optional recovery delay), descent to h <= 0.
    """
    res = SimResult()
    t = 0.0
    h = 0.0
    v = 0.0
    m = rocket.wet_mass
    burn_end = rocket.delay_time + rocket.burn_time
    recorded_burnout = False

    while t < max_time:
        powered = t < burn_end
        if powered and not recorded_burnout and t + dt >= burn_end:
            # this is the last powered sample; mark burnout stats
            recorded_burnout = True
        thrust = rocket.thrust if powered else 0.0

        rho = _rho(h)
        drag = 0.5 * rho * rocket.drag_coeff * rocket.area * v * abs(v)
        g = G0
        a = (thrust - drag - m * g) / m

        # record
        res.t.append(round(t, 6))
        res.h.append(h)
        res.v.append(v)
        res.m.append(m)
        res.max_speed = max(res.max_speed, abs(v))
        if h >= res.apogee_altitude:
            res.apogee_altitude, res.apogee_time = h, t
        if recorded_burnout and res.burnout_time == 0.0:
            res.burnout_time, res.burnout_altitude, res.burnout_velocity = t, h, v
            recorded_burnout = False

        # integrate (semi-implicit Euler: update v, then h)
        v += a * dt
        m_new = rocket.dry_mass if not powered else max(rocket.dry_mass, m - rocket.prop_rate * dt)
        h += v * dt
        t += dt
        m = m_new

        if h <= 0.0 and v < 0.0 and t > rocket.delay_time + rocket.burn_time:
            res.landing_time, res.landing_velocity = t, v
            break
    else:
        res.landing_time, res.landing_velocity = t, v  # timed out, report max sim end

    if res.apogee_time == 0.0:
        res.apogee_altitude, res.apogee_time = h, t
    return res


def summary(rocket: Rocket, res: SimResult) -> str:
    """Human-readable launch summary."""
    return (
        f"{rocket.name}: ve={rocket.exhaust_velocity:.1f} m/s "
        f"(Isp~{rocket.exhaust_velocity / G0:.1f} s), "
        f"mass ratio {rocket.wet_mass / rocket.dry_mass:.2f}\n"
        f"  burnout:  t={res.burnout_time:7.1f} s  h={res.burnout_altitude:9.1f} m  "
        f"v={res.burnout_velocity:7.1f} m/s\n"
        f"  apogee:   t={res.apogee_time:7.1f} s  h={res.apogee_altitude:9.1f} m\n"
        f"  landing:  t={res.landing_time:7.1f} s  v={res.landing_velocity:7.1f} m/s\n"
        f"  max |v|:  {res.max_speed:9.1f} m/s"
    )
