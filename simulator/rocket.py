"""Minimal 3-D rocket flight model (local ENU frame).

Coordinate frame: ENU (East, North, Up), origin at the launch point.
Distances are small relative to the Earth, so:
  - gravity is radial: a = -mu/r^3 * (e, n, R0 + u), r = |(e,n,R0+u)|
  - atmosphere: rho(h) = rho0 * exp(-(h - h_site) / H)
  - drag: F_d = 0.5 * rho * Cd * A * |v| * v (opposes velocity)
  - optional: Earth's rotation added as initial eastward velocity

Thrust points at fixed azimuth (deg, clockwise from north) with a pitch
angle (deg from the horizon) that ramps from start to end over ramp_time.

Semi-implicit Euler integration. Good enough for sounding-rocket /
model-rocket intuition, not for certification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

G0 = 9.80665               # m/s^2
MU = 3.986004418e14        # m^3/s^2, Earth gravitational parameter
R_EARTH = 6371000.0        # m, mean radius
OMEGA_EARTH = 7.2921159e-5 # rad/s
RHO0 = 1.225               # kg/m^3, sea-level density
SCALE_HEIGHT = 8500.0      # m
DEG = math.pi / 180.0


def _rho(altitude: float) -> float:
    """Air density at sea-level-reference altitude (m)."""
    return RHO0 * math.exp(-max(altitude, 0.0) / SCALE_HEIGHT)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _mag(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


@dataclass(frozen=True)
class Rocket:
    """Motor / vehicle parameters."""

    dry_mass: float = 10.0        # kg, structure without propellant
    prop_mass: float = 25.0       # kg, propellant load
    thrust: float = 800.0         # N, constant during burn
    burn_time: float = 6.0        # s
    drag_coeff: float = 0.75      # Cd
    area: float = 0.0181          # m^2, frontal area
    delay_time: float = 3.0       # s, initial delay
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
        """Equivalent ve = T/mdot (m/s); Isp = ve/g0."""
        return self.thrust / self.prop_rate

    def __post_init__(self) -> None:
        for n, v in (("dry_mass", self.dry_mass), ("prop_mass", self.prop_mass),
                     ("thrust", self.thrust), ("burn_time", self.burn_time)):
            if v <= 0:
                raise ValueError(f"{n} must be > 0, got {v}")
        if self.drag_coeff * self.area < 0:
            raise ValueError("drag_coeff and area must be >= 0")


@dataclass(frozen=True)
class Site:
    """Launch site: lat/lon in degrees, altitude above sea level in metres."""

    lat: float = 28.5584   # e.g. Space Coast
    lon: float = -80.5984
    altitude: float = 10.0

    @property
    def rot_velocity(self) -> float:
        """Eastward surface speed from Earth's rotation (m/s)."""
        return OMEGA_EARTH * R_EARTH * math.cos(self.lat * DEG)


@dataclass(frozen=True)
class Pitch:
    """Thrust direction profile: fixed azimuth, pitch ramp on the horizon plane."""

    azimuth: float = 90.0     # deg, clockwise from north (90 = east)
    start: float = 45.0       # deg from horizon at ignition
    end: float = 90.0         # deg from horizon (90 = straight up)
    ramp_time: float = 12.0   # s to go start -> end

    def angle(self, t: float) -> float:
        """Pitch angle from horizon (deg) at time t."""
        if self.ramp_time <= 0 or t >= self.ramp_time:
            return self.end
        return self.start + (self.end - self.start) * (t / self.ramp_time)


@dataclass
class SimResult:
    """Time series (ENU offsets from launch point) and derived stats."""

    t: list[float] = field(default_factory=list)
    e: list[float] = field(default_factory=list)
    n: list[float] = field(default_factory=list)
    u: list[float] = field(default_factory=list)
    speed: list[float] = field(default_factory=list)
    altitude: list[float] = field(default_factory=list)  # above sea level
    mass: list[float] = field(default_factory=list)

    # stats
    burnout_time: float = 0.0
    burnout_altitude: float = 0.0
    burnout_speed: float = 0.0
    apogee_time: float = 0.0
    apogee_altitude: float = 0.0
    landing_time: float = 0.0
    landing_speed: float = 0.0
    landing_lat: float = 0.0
    landing_lon: float = 0.0
    downrange: float = 0.0
    max_speed: float = 0.0


SITE_DEFAULT = Site()
PITCH_DEFAULT = Pitch()


def simulate(
    rocket: Rocket,
    site: Site = SITE_DEFAULT,
    pitch: Pitch = PITCH_DEFAULT,
    earth_rotation: bool = True,
    dt: float = 0.01,
    max_time: float = 300.0,
) -> SimResult:
    """Integrate the flight. Positions are ENU metres relative to the launch point."""
    res = SimResult()
    res.landing_lat, res.landing_lon = site.lat, site.lon

    # state: ENU position and velocity
    p = (0.0, 0.0, 0.0)
    v = (site.rot_velocity if earth_rotation else 0.0, 0.0, 0.0)
    m = rocket.wet_mass

    burn_end = rocket.delay_time + rocket.burn_time
    burnout_done = False
    az = pitch.azimuth * DEG

    t = 0.0
    while t < max_time:
        h = site.altitude + p[2]
        on_pad = t < rocket.delay_time
        powered = rocket.delay_time < t < burn_end
        if on_pad:
            # launch clamps hold the vehicle; ignore dynamics
            a = (0.0, 0.0, 0.0)
        else:
            # gravity (radial)
            r_vec = (p[0], p[1], R_EARTH + h)
            r = _mag(r_vec)
            g = _scale(r_vec, -MU / (r * r * r))
            # drag
            rho = _rho(h)
            d = _scale(v, 0.5 * rho * rocket.drag_coeff * rocket.area * _mag(v))
            # thrust
            f_t = (0.0, 0.0, 0.0)
            if powered:
                al = math.radians(pitch.angle(t - rocket.delay_time))
                thrust_dir = (math.sin(az) * math.cos(al),
                              math.cos(az) * math.cos(al), math.sin(al))
                f_t = _scale(thrust_dir, rocket.thrust)
            a = _add(g, _scale(_add(f_t, _scale(d, -1.0)), 1.0 / m))

        # record
        res.t.append(round(t, 6))
        res.e.append(p[0])
        res.n.append(p[1])
        res.u.append(p[2])
        s = _mag(v)
        res.speed.append(s)
        res.altitude.append(h)
        res.mass.append(m)
        res.max_speed = max(res.max_speed, s)
        if h >= res.apogee_altitude:
            res.apogee_altitude, res.apogee_time = h, t
        if powered and not burnout_done and t + dt >= burn_end:
            res.burnout_time, res.burnout_altitude, res.burnout_speed = t, h, s
            burnout_done = True

        # integrate: v first, then p; consume propellant only while powered
        v = _add(v, _scale(a, dt))
        p = _add(p, _scale(v, dt))
        if not on_pad and t < burn_end:
            m = max(rocket.dry_mass, m - rocket.prop_rate * dt)
        t += dt

        if p[2] <= 0.0 and v[2] < 0.0 and t > burn_end:
            res.landing_time = t
            res.landing_speed = _mag(v)
            res.landing_lat = site.lat + (p[1] / R_EARTH) / DEG
            res.landing_lon = site.lon + (p[0] / (R_EARTH * math.cos(site.lat * DEG))) / DEG
            res.downrange = math.hypot(p[0], p[1])
            break
    else:
        res.landing_time = t
        res.landing_speed = _mag(v)
        res.downrange = math.hypot(p[0], p[1])

    if res.apogee_time == 0.0:
        res.apogee_altitude = site.altitude + p[2]
    return res


def summary(rocket: Rocket, site: Site, res: SimResult) -> str:
    """Human-readable launch summary."""
    return (
        f"{rocket.name}: ve={rocket.exhaust_velocity:.1f} m/s "
        f"(Isp~{rocket.exhaust_velocity / G0:.1f} s), "
        f"mass ratio {rocket.wet_mass / rocket.dry_mass:.2f}\n"
        f"  site:     {site.lat:+.4f}, {site.lon:+.4f} @ {site.altitude:.0f} m "
        f"(rot {site.rot_velocity:.0f} m/s east)\n"
        f"  burnout:  t={res.burnout_time:7.1f} s  h={res.burnout_altitude:9.1f} m  "
        f"|v|={res.burnout_speed:7.1f} m/s\n"
        f"  apogee:   t={res.apogee_time:7.1f} s  h={res.apogee_altitude:9.1f} m\n"
        f"  landing:  t={res.landing_time:7.1f} s  |v|={res.landing_speed:7.1f} m/s  "
        f"{res.landing_lat:+.5f}, {res.landing_lon:+.5f}  "
        f"(downrange {res.downrange / 1000:.2f} km)\n"
        f"  max |v|:  {res.max_speed:9.1f} m/s"
    )
