"""3-D orbital mechanics for the full-stack launch sim.

Frames and models
-----------------
- Position/velocity state in **ECI** (Earth-Centered Inertial, J2000):
  r in metres, v in m/s, z-axis on Earth's rotation axis.
- Gravity: point mass + J2 (oblateness) of WGS-84.
- Atmosphere: two-layer exponential (8.5 km scale to 86 km, then 64 km
  scale), altitude referenced to **sea level** (geoid ~ WGS-84 ellipsoid,
  good enough for a simulator).
- Earth rotation: ECEF/ECI tied by GMST of a nominal launch epoch; air
  moves with the planet, so drag is computed from v_air = v - w_E x r.
- Trajectory integrator: classical RK4 at dt = 0.01 s.

Also here: the **optimal launch heading** rule and a simple, realistic
**pitch-over program** (brief vertical rise, then a steady rotation of the
thrust vector onto the chosen heading, then gravity turns the trajectory).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = [
    "GM_ST0",
    "MU",
    "J2",
    "R_EARTH",
    "OMEGA_EARTH",
    "LAUNCH_EPOCH",
    "DAYS_SINCE_J2000",
    "FlightModel",
    "PitchProgram",
    "Site",
    "atmos_density",
    "ecef_to_geodetic",
    "eci_to_geodetic",
    "orbit_elements",
    "select_heading",
]

# --- constants ----------------------------------------------------------------
MU = 3.986004418e14        # m^3/s^2, WGS-84 gravitational parameter
R_EARTH = 6371000.0        # m, mean Earth radius (drag/alt reference)
OMEGA_EARTH = 7.2921159e-5 # rad/s, sidereal rotation rate
J2 = 1.08262668e-3         # WGS-84 J2
EARTH_A = 6378137.0        # m, WGS-84 semi-major axis
EARTH_F = 1.0 / 298.257223563
E2 = 2.0 * EARTH_F - EARTH_F * EARTH_F

G0 = 9.80665

#: Nominal launch epoch (UTC). Only sets the inertial frame's starting
#: orientation; the trajectory is frame-consistent whichever instant you pick.
LAUNCH_EPOCH = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

_DAYS_SINCE_J2000 = (LAUNCH_EPOCH - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0
DAYS_SINCE_J2000 = _DAYS_SINCE_J2000


def gmst_deg_at(days: float) -> float:
    """Greenwich mean sidereal time (deg) `days` after J2000 (Meeus)."""
    T = days / 36525.0
    g = (
        280.46061837
        + 360.98564736629 * days
        + T * T * 0.000387933
        - T * T * (1.0 / 38710000.0)
    )
    return g % 360.0


def _gmst(days: float) -> float:
    return math.radians(gmst_deg_at(days))


#: GMST at t = 0 (radians).
GM_ST0 = _gmst(_DAYS_SINCE_J2000)


def gmst_at(t: float) -> float:
    """GMST (radians) t seconds after the launch epoch."""
    return _gmst(_DAYS_SINCE_J2000 + t / 86400.0)


# --- geodetic conversions --------------------------------------------------------
def ecef(
    lat_deg: float,
    lon_deg: float,
    alt_m: float,
) -> tuple[float, float, float]:
    """Geodetic lat/lon (deg) + alt (m) -> ECEF (m)."""
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    n = EARTH_A / math.sqrt(1.0 - E2 * math.sin(lat) ** 2)
    x = (n + alt_m) * math.cos(lat) * math.cos(lon)
    y = (n + alt_m) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - E2) + alt_m) * math.sin(lat)
    return x, y, z


def ecef_to_geodetic(
    x: float,
    y: float,
    z: float,
) -> tuple[float, float, float]:
    """ECEF (m) -> geodetic lat/lon (deg, +N +E) and alt above sea level (m)."""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1.0 - E2))
    for _ in range(3):
        n = EARTH_A / math.sqrt(1.0 - E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - E2 * (n / (n + h))))
    n = EARTH_A / math.sqrt(1.0 - E2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), h


def eci_to_geodetic(
    r: tuple[float, float, float],
    t: float,
) -> tuple[float, float, float]:
    """ECI position (m) at sim time t -> geodetic lat/lon (deg) + alt (m)."""
    th = gmst_at(t)
    c, s = math.cos(th), math.sin(th)
    # ECI -> ECEF is R_z(-th)
    x = c * r[0] + s * r[1]
    y = -s * r[0] + c * r[1]
    z = r[2]
    return ecef_to_geodetic(x, y, z)


# --- atmosphere -----------------------------------------------------------------
#: Three-layer density table fit to US-Standard-atmosphere sample points:
#:   0-90 km:  1.225 * exp(-h / 8.5 km)
#:   90-200 km: exp decay (avg ~6.8 km scale) from the 90 km anchor
#:   >200 km:  exp decay (45 km scale) anchored at 3.06e-12 kg/m^3 (200 km)
_RHO_90 = 1.225 * math.exp(-90.0e3 / 8500.0)
_LN_90_200 = math.log(_RHO_90 / 3.06e-12)
_H_90_200 = 110.0e3 / _LN_90_200  # ~6.8 km


def atmos_density(alt_m: float) -> float:
    """Air density (kg/m^3) at sea-level-reference altitude (m)."""
    if alt_m <= 0.0:
        return 1.225
    if alt_m < 90.0e3:
        return 1.225 * math.exp(-alt_m / 8500.0)
    if alt_m < 200.0e3:
        return _RHO_90 * math.exp(-(alt_m - 90.0e3) / _H_90_200)
    return 3.06e-12 * math.exp(-(alt_m - 200.0e3) / 45.0e3)


def sound_speed(alt_m: float) -> float:
    """Approximate speed of sound (m/s), stepwise ISA-lite."""
    if alt_m <= 11000.0:
        return 340.0
    return 295.0 + max(0.0, alt_m - 32000.0) * 0.001  # rises weakly above ~32 km


# --- launch geometry --------------------------------------------------------------
def select_heading(lat_deg: float, target_inc_deg: float) -> float:
    """Optimal (minimum-plane-change) prograde heading for a target inclination.

    Heading is degrees clockwise from true north *through east* (90 = due
    east). For a prograde launch from latitude phi at azimuth A the orbit
    inclination satisfies cos(i) = cos(phi) * sin(A); the fuel-optimal
    azimuth for a requested inclination i (>= phi) is therefore
    A = asin(cos(i) / cos(phi)).

    If i < phi no prograde azimuth can deliver it (that is the site's
    minimum prograde inclination): we fall back to due east, which yields
    an i = phi orbit (cheapest plane change from there).
    """
    phi = math.radians(lat_deg)
    i = math.radians(max(target_inc_deg, 0.0))
    c = math.cos(i) / math.cos(phi)
    if c >= 1.0:
        return 90.0
    if c <= 0.0:
        return 180.0
    return math.degrees(math.asin(c))


@dataclass(frozen=True)
class PitchProgram:
    """Gravity-turn / pitch-over guidance expressed in the *local* (rotating) frame.

    The vehicle pitches from vertical toward the local horizon as it builds
    speed, keeping a positive *climb* component the whole way (this is what
    lets it gain the altitude orbit requires).  Thrust points along the
    turning velocity direction; a hard no-dive floor keeps the thrust vector
    at least ``fpa_min_deg`` above the local horizon so the vehicle is never
    commanded into the ground.

    The flight-path angle (FPA, above the local horizon) is commanded to ease
    from ~90deg (vertical) down to a low residual as ground-relative speed
    rises to ``v_full`` - i.e. the pitch-over is velocity-scheduled, the
    standard gravity-turn behaviour, rather than a crude fixed time-pitch-rate.
    """

    pad_clear_s: float = 8.0      # s of straight-up pad clearance
    pad_clear_v: float = 45.0     # ...and until faster than this (m/s)
    v_full: float = 2600.0        # m/s at which the pitch-over is ~complete
    fpa_min_deg: float = 4.0      # no-dive floor (deg above local horizon)

    def fpa_rad(self, v_rel: float, t: float) -> float:
        """Commanded flight-path angle above local horizon (rad)."""
        if t < self.pad_clear_s or v_rel < self.pad_clear_v:
            return math.radians(89.0)     # straight up during pad clearance
        x = min(max(v_rel / self.v_full, 0.0), 1.0)
        s = x * x * (3.0 - 2.0 * x)       # smoothstep
        fpa_deg = (89.0 * (1.0 - s) + self.fpa_min_deg * s)
        return math.radians(fpa_deg)

    # back-compat accessor used by old call-sites
    def beta(self, t: float) -> float:
        return 0.0


def _local_enu(
    r: tuple[float, float, float]
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Local East-North-Up basis (inertial components) at position ``r``."""
    rmag = math.sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2])
    u = (r[0] / rmag, r[1] / rmag, r[2] / rmag)
    z = (0.0, 0.0, 1.0)
    e = (z[1] * r[2] - z[2] * r[1], z[2] * r[0] - z[0] * r[2], z[0] * r[1] - z[1] * r[0])
    em = math.sqrt(e[0] * e[0] + e[1] * e[1] + e[2] * e[2]) or 1.0
    e = (e[0] / em, e[1] / em, e[2] / em)
    n = (u[1] * e[2] - u[2] * e[1], u[2] * e[0] - u[0] * e[2], u[0] * e[1] - u[1] * e[0])
    return e, n, u


# --- orbit elements ----------------------------------------------------------------
def orbit_elements(
    r: tuple[float, float, float],
    v: tuple[float, float, float],
) -> dict | None:
    """Keplerian elements from (r, v). None if the state is sub-orbital (a < 0)."""
    mu = MU
    rmag = math.sqrt(r[0] ** 2 + r[1] ** 2 + r[2] ** 2)
    vmag = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    energy = vmag ** 2 / 2.0 - mu / rmag
    if energy >= 0.0:
        return None
    a = -mu / (2.0 * energy)
    # h = r x v
    hx = r[1] * v[2] - r[2] * v[1]
    hy = r[2] * v[0] - r[0] * v[2]
    hz = r[0] * v[1] - r[1] * v[0]
    hmag = math.sqrt(hx * hx + hy * hy + hz * hz)
    inc = math.acos(max(-1.0, min(1.0, hz / hmag)))
    # eccentricity vector: e_vec = (v x h)/mu - r/rmag
    rv = r[0] * v[0] + r[1] * v[1] + r[2] * v[2]
    ex = (v[1] * hz - v[2] * hy - rv * v[0]) / mu - r[0] / rmag
    ey = (v[2] * hx - v[0] * hz - rv * v[1]) / mu - r[1] / rmag
    ez = (v[0] * hy - v[1] * hx - rv * v[2]) / mu - r[2] / rmag
    ecc = math.sqrt(ex * ex + ey * ey + ez * ez)
    # RAAN
    nx = -hy / hmag
    ny = hx / hmag
    if abs(nx) + abs(ny) > 1e-12:
        raan = math.atan2(ny, nx)
    else:
        raan = 0.0
    perigee_km = max(0.0, a * (1.0 - ecc) - R_EARTH) / 1000.0
    apogee_km = max(0.0, a * (1.0 + ecc) - R_EARTH) / 1000.0
    return {
        "a_km": a / 1000.0,
        "e": ecc,
        "i_deg": math.degrees(inc),
        "raan_deg": math.degrees(raan) % 360.0,
        "perigee_km": perigee_km,
        "apogee_km": apogee_km,
    }


# --- the integrator ------------------------------------------------------------------
def _j2_accel(r: tuple[float, float, float]) -> tuple[float, float, float]:
    """J2 (oblateness) acceleration (m/s^2) in ECI."""
    x, y, z = r
    r2 = x * x + y * y + z * z
    r = math.sqrt(r2)
    k = -1.5 * J2 * MU * EARTH_A * EARTH_A / (r2 * r2 * r)
    f = 1.0 - 5.0 * (z * z / r2)
    g5 = -5.0 * z * z / r2
    return (k * x * f, k * y * f, k * z * g5)


def _drag(r: tuple[float, float, float], v: tuple[float, float, float],
          cda_m2: float, mass_kg: float, alt_m: float) -> tuple[float, float, float]:
    """Aerodynamic drag (m/s^2), computed in the rotating (air) frame."""
    rho = atmos_density(alt_m)
    if rho <= 0.0 or cda_m2 <= 0.0:
        return (0.0, 0.0, 0.0)
    w = OMEGA_EARTH
    # air-relative velocity: v_air = v - w_E x r (w_E = (0, 0, w))
    vx = v[0] - (-w * r[1])
    vy = v[1] - (w * r[0])
    vz = v[2]
    vmag = math.sqrt(vx * vx + vy * vy + vz * vz)
    if vmag <= 0.0:
        return (0.0, 0.0, 0.0)
    coeff = 0.5 * rho * cda_m2 * vmag / mass_kg
    return (coeff * -vx, coeff * -vy, coeff * -vz)


@dataclass(frozen=True)
class Site:
    """Launch site: geodetic coordinates + altitude above sea level."""

    lat_deg: float = 28.5623    # Space Coast (SLC-40)
    lon_deg: float = -80.5768
    alt_m: float = 3.0


@dataclass
class FlightModel:
    """RK4 point-mass flight model in ECI with J2 + drag + thrust program.

    The thrust *direction* is a gravity turn (radial while slow, then along
    the ground-relative velocity); gravity does the pitch-over. The
    magnitude (N) and vehicle mass (kg) are supplied per step, so this
    model composes cleanly with whatever engine sim drives the vehicle.
    """

    site: Site
    heading_deg: float
    pitch: PitchProgram
    cda_m2: float = 25.0

    def __post_init__(self) -> None:
        r0_ecef = ecef(self.site.lat_deg, self.site.lon_deg, self.site.alt_m)
        th0 = gmst_at(0.0)
        c, s = math.cos(th0), math.sin(th0)
        # ECEF -> ECI is R_z(+th)
        self.r0 = (c * r0_ecef[0] - s * r0_ecef[1], s * r0_ecef[0] + c * r0_ecef[1], r0_ecef[2])
        self.t = 0.0
        # starts on the pad, moving with the rotating Earth
        self.v = (
            OMEGA_EARTH * -self.r0[1],
            OMEGA_EARTH * self.r0[0],
            0.0,
        )
        # launch geometry axes in ECI (inertially fixed at t = 0)
        self._up0 = self._axis("up")
        self._hdg0 = self._axis("hdg")
        self.apogee_m = self.site.alt_m
        self.max_q_pa = 0.0
        self.delta_v_ms = 0.0
        self.drag_loss_ms = 0.0
        self.crashed = False
        self.crash_t: float | None = None
        self.crash_lat: float | None = None
        self.crash_lon: float | None = None

    # -- axes --------------------------------------------------------------------
    def _axis(self, which: str) -> tuple[float, float, float]:
        lat, lon = math.radians(self.site.lat_deg), math.radians(self.site.lon_deg)
        e = (-math.sin(lon), math.cos(lon), 0.0)                # east
        n = (-math.sin(lat) * math.cos(lon),
             -math.sin(lat) * math.sin(lon),
             math.cos(lat))                                      # north
        u = (math.cos(lat) * math.cos(lon),
             math.cos(lat) * math.sin(lon),
             math.sin(lat))                                      # up
        az = math.radians(self.heading_deg)
        hdg = (math.sin(az) * e[0] + math.cos(az) * n[0],
               math.sin(az) * e[1] + math.cos(az) * n[1],
               math.sin(az) * e[2] + math.cos(az) * n[2])
        vec = u if which == "up" else hdg
        th0 = gmst_at(0.0)
        c, s = math.cos(th0), math.sin(th0)
        return (c * vec[0] - s * vec[1], s * vec[0] + c * vec[1], vec[2])

    # -- reporting basics ---------------------------------------------------------
    def altitude(self) -> float:
        """Altitude above sea level (m), geodetic (ellipsoidal)."""
        return eci_to_geodetic(self.r0, self.t)[2]

    def geodetic(self) -> tuple[float, float, float]:
        return eci_to_geodetic(self.r0, self.t)

    def thrust_dir(self, t: float, r, v) -> tuple[float, float, float]:
        """Gravity turn in the local (rotating) frame.

        Thrust pitches from vertical toward the local horizon as ground-
        relative speed builds (a velocity-scheduled pitch-over), and always
        keeps a climb component: the no-dive floor keeps the thrust vector
        at least ``fpa_min_deg`` above the local horizon, so the vehicle is
        never commanded into the ground.  All geometry is evaluated at the
        current position, so "horizontal" means local horizontal throughout.
        """
        e, n, u = _local_enu(r)
        az = math.radians(self.heading_deg)
        hdg = (math.sin(az) * e[0] + math.cos(az) * n[0],
               math.sin(az) * e[1] + math.cos(az) * n[1],
               math.sin(az) * e[2] + math.cos(az) * n[2])
        vx = v[0] + OMEGA_EARTH * r[1]      # ground-relative velocity
        vy = v[1] - OMEGA_EARTH * r[0]
        vz = v[2]
        s = math.sqrt(vx * vx + vy * vy + vz * vz)
        fpa = self.pitch.fpa_rad(s, t)      # commanded FPA above local horizon
        d = (math.cos(fpa) * hdg[0] + math.sin(fpa) * u[0],
             math.cos(fpa) * hdg[1] + math.sin(fpa) * u[1],
             math.cos(fpa) * hdg[2] + math.sin(fpa) * u[2])
        m2 = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) or 1.0
        return (d[0] / m2, d[1] / m2, d[2] / m2)

    def flight_path_deg(self) -> float:
        """Flight-path angle above local horizontal (deg; > 0 = climbing)."""
        rmag = math.sqrt(self.r0[0] ** 2 + self.r0[1] ** 2 + self.r0[2] ** 2)
        vr = (self.v[0] * self.r0[0] + self.v[1] * self.r0[1] + self.v[2] * self.r0[2]) / rmag
        vmag = math.sqrt(self.v[0] ** 2 + self.v[1] ** 2 + self.v[2] ** 2)
        vh = max(math.sqrt(max(vmag * vmag - vr * vr, 0.0)), 1e-6)
        return math.degrees(math.atan2(vr, vh))

    # -- dynamics ------------------------------------------------------------------
    def accel(
        self,
        t: float,
        r: tuple[float, float, float],
        v: tuple[float, float, float],
        mass_kg: float,
        thrust_n: float,
    ) -> tuple[float, float, float]:
        rmag = math.sqrt(r[0] ** 2 + r[1] ** 2 + r[2] ** 2)
        gm = MU / (rmag * rmag * rmag)
        ax, ay, az = -gm * r[0], -gm * r[1], -gm * r[2]
        jx, jy, jz = _j2_accel(r)
        ax += jx
        ay += jy
        az += jz
        lat, lon, alt_g = eci_to_geodetic(r, t)
        alt = max(alt_g, 0.0)
        dx, dy, dz = _drag(r, v, self.cda_m2, mass_kg, alt)
        ax += dx
        ay += dy
        az += dz
        if thrust_n > 0.0 and mass_kg > 0.0:
            d = self.thrust_dir(t, r, v)
            k = thrust_n / mass_kg
            ax += k * d[0]
            ay += k * d[1]
            az += k * d[2]
        return ax, ay, az

    # -- stepping -------------------------------------------------------------------
    def step(
        self,
        dt: float,
        mass_kg: float,
        thrust_n: float,
        mass_end_kg: float | None = None,
    ) -> None:
        """Advance dt seconds (RK4, subcycled for orbit accuracy).

        ``mass_end_kg``: mass at t + dt (linearly interpolated); defaults to
        ``mass_kg``. Thrust is held constant over the step (the engine state
        lives at the outer timestep); the pitch program is evaluated exactly
        at each substep's time, so the turn stays smooth.
        """
        m = 0.5 * (mass_kg + (mass_end_kg if mass_end_kg is not None else mass_kg))
        n_sub = max(1, int(round(dt / 0.002)))
        h = dt / n_sub
        for _ in range(n_sub):
            self._rk4(h, m, thrust_n)
        self.t += dt

        # record / bookkeeping
        alt = self.altitude()
        if alt > self.apogee_m:
            self.apogee_m = alt
        vmag = math.sqrt(self.v[0] ** 2 + self.v[1] ** 2 + self.v[2] ** 2)
        if thrust_n > 0.0:
            self.delta_v_ms += thrust_n * dt / mass_kg
        rho = atmos_density(alt)
        q = 0.5 * rho * vmag * vmag
        if q > self.max_q_pa:
            self.max_q_pa = q
        if vmag > 0.0:
            self.drag_loss_ms += 0.5 * rho * self.cda_m2 * vmag * dt / mass_kg
        if alt <= 0.0 and self.t > 5.0:
            rnorm = math.sqrt(self.r0[0] ** 2 + self.r0[1] ** 2 + self.r0[2] ** 2)
            vertical = (
                self.v[0] * self.r0[0]
                + self.v[1] * self.r0[1]
                + self.v[2] * self.r0[2]
            ) / rnorm
            if vertical < 0.0:
                lat, lon, _ = self.geodetic()
                self.crashed = True
                self.crash_t = self.t
                self.crash_lat = lat
                self.crash_lon = lon

    def _rk4(self, h: float, m: float, thrust_n: float) -> None:
        """One RK4 substep of duration ``h`` (s) at the current model time."""

        def deriv(tt: float, rr: tuple[float, float, float], vv: tuple[float, float, float]):
            ax, ay, az = self.accel(tt, rr, vv, m, thrust_n)
            return (vv[0], vv[1], vv[2]), (ax, ay, az)

        r, v = self.r0, self.v
        t = self.t
        k1r, k1v = deriv(t, r, v)
        r2 = (r[0] + k1r[0] * h / 2, r[1] + k1r[1] * h / 2, r[2] + k1r[2] * h / 2)
        v2 = (v[0] + k1v[0] * h / 2, v[1] + k1v[1] * h / 2, v[2] + k1v[2] * h / 2)
        k2r, k2v = deriv(t + h / 2, r2, v2)
        r3 = (r[0] + k2r[0] * h / 2, r[1] + k2r[1] * h / 2, r[2] + k2r[2] * h / 2)
        v3 = (v[0] + k2v[0] * h / 2, v[1] + k2v[1] * h / 2, v[2] + k2v[2] * h / 2)
        k3r, k3v = deriv(t + h / 2, r3, v3)
        r4 = (r[0] + k3r[0] * h, r[1] + k3r[1] * h, r[2] + k3r[2] * h)
        v4 = (v[0] + k3v[0] * h, v[1] + k3v[1] * h, v[2] + k3v[2] * h)
        k4r, k4v = deriv(t + h, r4, v4)
        self.r0 = (
            r[0] + h / 6.0 * (k1r[0] + 2 * k2r[0] + 2 * k3r[0] + k4r[0]),
            r[1] + h / 6.0 * (k1r[1] + 2 * k2r[1] + 2 * k3r[1] + k4r[1]),
            r[2] + h / 6.0 * (k1r[2] + 2 * k2r[2] + 2 * k3r[2] + k4r[2]),
        )
        self.v = (
            v[0] + h / 6.0 * (k1v[0] + 2 * k2v[0] + 2 * k3v[0] + k4v[0]),
            v[1] + h / 6.0 * (k1v[1] + 2 * k2v[1] + 2 * k3v[1] + k4v[1]),
            v[2] + h / 6.0 * (k1v[2] + 2 * k2v[2] + 2 * k3v[2] + k4v[2]),
        )

    # -- reporting -------------------------------------------------------------------
    def velocity_enu(self) -> tuple[float, float, float]:
        """Velocity components in the local East/North/Up frame AT THE CURRENT
        position (m/s) — this is the frame a flight computer reports."""
        th = gmst_at(self.t)
        c, s = math.cos(th), math.sin(th)
        x = c * self.v[0] + s * self.v[1]
        y = -s * self.v[0] + c * self.v[1]
        z = self.v[2]
        lat, lon = eci_to_geodetic(self.r0, self.t)[:2]
        lat, lon = math.radians(lat), math.radians(lon)
        e = (-math.sin(lon), math.cos(lon), 0.0)
        n = (-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat))
        u = (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))
        ve = e[0] * x + e[1] * y + e[2] * z
        vn = n[0] * x + n[1] * y + n[2] * z
        vu = u[0] * x + u[1] * y + u[2] * z
        return ve, vn, vu

    def speed(self) -> float:
        return math.sqrt(self.v[0] ** 2 + self.v[1] ** 2 + self.v[2] ** 2)

    def downrange_m(self) -> float:
        """Chord distance from the launch point (m)."""
        r0e = ecef(self.site.lat_deg, self.site.lon_deg, self.site.alt_m)
        th0 = gmst_at(0.0)
        c, s = math.cos(th0), math.sin(th0)
        r0eci = (c * r0e[0] - s * r0e[1], s * r0e[0] + c * r0e[1], r0e[2])
        dr = (self.r0[0] - r0eci[0], self.r0[1] - r0eci[1], self.r0[2] - r0eci[2])
        return math.sqrt(dr[0] ** 2 + dr[1] ** 2 + dr[2] ** 2)

    def state_doc(self, thrust_n: float, mass_kg: float, phase: str | None = None) -> dict:
        """A ``flight.state`` wire document: position, speed, q, orbit, stats."""
        lat, lon, alt = self.geodetic()
        speed = self.speed()
        ve, vn, vu = self.velocity_enu()
        vmag = math.sqrt(ve * ve + vn * vn)
        if vmag > 1.0:
            heading = math.degrees(math.atan2(ve, vn)) % 360.0
        else:
            heading = float(self.heading_deg)
        mach = speed / sound_speed(alt) if speed > 0.0 else 0.0
        q_pa = 0.5 * atmos_density(alt) * speed * speed
        orbe = orbit_elements(self.r0, self.v)
        if orbe is not None and orbe["perigee_km"] < 1.0:
            orbe = None  # would intersect the surface: not an orbit
        return {
            "v": 1,
            "type": "flight.state",
            "t": self.t,
            "phase": phase,
            "position": {
                "lat_deg": lat,
                "lon_deg": lon,
                "alt_m": alt,
                "downrange_m": self.downrange_m(),
            },
            "velocity": {
                "speed_ms": speed,
                "east_ms": ve,
                "north_ms": vn,
                "up_ms": vu,
                "heading_deg": heading,
            },
            "aerodynamics": {
                "mach": mach,
                "q_pa": q_pa,
                "max_q_pa": self.max_q_pa,
            },
            "mass_kg": mass_kg,
            "thrust_n": thrust_n,
            "stats": {
                "apogee_m": self.apogee_m,
                "delta_v_ms": self.delta_v_ms,
                "drag_loss_ms": self.drag_loss_ms,
                "pitch_deg": self.flight_path_deg(),
            },
            "orbit": orbe,
            "crashed": self.crashed,
        }
