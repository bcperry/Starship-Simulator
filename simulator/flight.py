"""Full-stack 3-D launch sim: the engine sim drives a real trajectory.

`StackFlight` composes the existing `Stack` (Super Heavy + Starship, shared
propellant tanks, per-engine variability / jitter / exponential failures)
with the `orbit.FlightModel` (ECI RK4 with J2, drag, pitch-over program and
mass jettison at staging).

Mass budget ("jettison first stage")
------------------------------------
- Ascent (until SS ignites, i.e. STAGING coast completes):
    m = SH_dry + SS_dry + payload + SH_tanks + SS_tanks
- Phase UPPER / DONE (staging done, booster separated):
    m = SS_dry + payload + SS_tanks
  i.e. Super Heavy's dry mass AND its residual propellant are both dropped.

Thrust per step is the live sum of both ships' thrust (failed engines
already excluded by the engine model), so engine failures degrade the
trajectory in real time.

Mission / acceptance (tests/test_flight.py)
-------------------------------------------
- A launch suffers 1-3 engine failures in aggregate (the sim's failure spec)
  yet the FULL stack still inserts the multi-ton payload into LEO
  (perigee >= 100 km; SECO at the 130 km floor).
- Starship alone (no Super Heavy) is sub-orbital -- the stack is required.
- Super Heavy banks ~20 % of its propellant for boostback (guidance MECO).
- Losing 3+ of Starship's 6 engines is a genuine anomaly: the stage cannot
  fly through it and the flight re-enters (honest, not a sim bug).

Run it:
    .venv/bin/python -m simulator.flightloop --speed 20
    .venv/bin/python -m simulator.flightloop --seed 7 --sh-failures 2 --ss-failures 1
    .venv/bin/python scripts/headless_mission.py            # headless LEO run
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from .orbit import FlightModel, MU, PitchProgram, Site, orbit_elements, select_heading
from .ship import Ship, ShipConfig
from .stack import PHASE_DONE, PHASE_LIFTOFF, PHASE_UPPER, Stack, StackConfig
from .starship import Starship

__all__ = [
    "FlightCfg",
    "StackFlight",
    "make_flight",
]

#: Default vehicle sizing for a Raptor-3 Starship V3-class stack:
#: 33x Raptor 3 on Super Heavy (>84 MN liftoff), 6x Raptor 3 on Starship.
#: Super Heavy banks ~20 % of its propellant for boostback + landing
#: (guidance MECO, not a dry tank).  Sized so the FULL stack inserts a
#: multi-ton payload into LEO -- Super Heavy + Starship together clear the
#: ~9.4 km/s orbital Delta-v -- while Starship alone, even fully fueled,
#: cannot reach orbit.  SECO cuts Starship the moment the coast orbit's
#: perigee reaches the leo_perigee_floor (130 km, above the 100 km LEO line)
#: so the vehicle inserts with margin and banks its residual.
#:
#:   Super Heavy  propellant  ~4200 t  (LOX:CH4 = 3.6:1)  dry  200 t
#:   Starship     propellant  ~1200 t  (LOX:CH4 = 3.6:1)  dry  150 t
#:   payload                    50 t
#:
#: Acceptance (tests/test_flight.py): a launch suffers 1-3 engine failures in
#: aggregate yet still establishes LEO (perigee >= 100 km); losing 3+ of
#: Starship's 6 engines is a genuine anomaly the stage cannot fly through."
TANK_DEFAULTS = {
    "sh_lox_kg": 3_286_957.0,
    "sh_ch4_kg": 913_043.0,
    "ss_lox_kg": 939_130.0,
    "ss_ch4_kg": 260_870.0,
}

#: Flight-config keys a control doc may carry to rebuild the mission geometry.
FLIGHT_OVERRIDE_KEYS = (
    "site_lat_deg",
    "site_lon_deg",
    "site_alt_m",
    "target_inc_deg",
    "sh_dry_kg",
    "ss_dry_kg",
    "payload_kg",
    "sh_lox_kg",
    "sh_ch4_kg",
    "ss_lox_kg",
    "ss_ch4_kg",
    "cda_m2",
    "pitch_pad_clear_s",
    "pitch_v_full",
    "pitch_fpa_min_deg",
    "sh_meco_resid_frac",
    "leo_target_alt_m",
    "leo_perigee_floor_km",
    "staging_delay_s",
)


@dataclass(frozen=True)
class FlightCfg:
    """Mission + vehicle configuration for a :class:`StackFlight`."""

    # launch site (geodetic; altitude above sea level)
    site_lat_deg: float = 28.5623    # Space Coast (SLC-40)
    site_lon_deg: float = -80.5768
    site_alt_m: float = 3.0
    # mission plane
    target_inc_deg: float = 28.5
    # vehicle dry masses (structure + engines)
    sh_dry_kg: float = 200_000.0
    ss_dry_kg: float = 150_000.0
    payload_kg: float = 50_000.0      # multi-ton LEO payload
    # tank loads
    sh_lox_kg: float = TANK_DEFAULTS["sh_lox_kg"]
    sh_ch4_kg: float = TANK_DEFAULTS["sh_ch4_kg"]
    ss_lox_kg: float = TANK_DEFAULTS["ss_lox_kg"]
    ss_ch4_kg: float = TANK_DEFAULTS["ss_ch4_kg"]
    # aerodynamics
    cda_m2: float = 30.0
    # gravity-turn guidance (pitch-over, then keep climbing along the
    # turning velocity); see :class:`PitchProtocol`.
    pitch_pad_clear_s: float = 8.0
    pitch_pad_clear_v: float = 45.0
    pitch_v_full: float = 2400.0
    pitch_fpa_min_deg: float = 4.0
    # stage guidance
    sh_meco_resid_frac: float = 0.20  # SH propellant banked for boostback/landing
    leo_target_alt_m: float = 185_000.0  # target insertion altitude (reporting/near-circular)
    # SECO fires the moment the coast orbit's perigee reaches this altitude.
    # It sits ABOVE the 100 km LEO line so the vehicle inserts with margin
    # (not just at the Karman boundary) and banks its residual propellant.
    # In-spec engine failures (1-3) establish LEO here; losing 3+ of Starship's
    # 6 engines is a genuine anomaly the stage cannot fly through.
    leo_perigee_floor_km: float = 130.0
    # stack behaviour
    staging_delay_s: float = 4.0
    # run parameters (mirroring the engine-sim controls)
    seed: int = 7
    n_engines: int = 33
    vari_frac: float = 0.01
    throttle_frac: float = 1.0
    sh_expected_failures: float = 2.0
    ss_expected_failures: float = 1.0
    noise_frac: float = 0.02
    dt: float = 0.01

    @property
    def heading_deg(self) -> float:
        """Launch heading (deg clockwise through east) for the target plane."""
        return select_heading(self.site_lat_deg, self.target_inc_deg)


class StackFlight:
    """Stack (engine sim) + FlightModel (trajectory), stepped in lockstep.

    Duck-types the Ship surface the stdio loop expects (config.dt, seq, t,
    meco, _members, status(), step(), apply_control()), and every doc it
    emits is strict JSON with finite numbers.
    """

    def __init__(self, cfg: FlightCfg) -> None:
        self.cfg = cfg
        self.heading = cfg.heading_deg
        self.sh, self.ss = self._build_ships()
        self.stack = Stack(
            self.sh,
            self.ss,
            StackConfig(
                seed=self.cfg.seed,
                staging_delay_s=self.cfg.staging_delay_s,
                dt=self.cfg.dt,
            ),
        )
        self.model = self._new_model()
        self._last_sh_thrust = 0.0
        self._last_ss_thrust = 0.0
        self.sh_meco_t: float | None = None
        self.ss_seco_t: float | None = None

    # -- construction ----------------------------------------------------------
    def _build_ships(self) -> tuple[Ship, Starship]:
        c = self.cfg
        sh = Ship(
            ShipConfig(
                vehicle_id="super-heavy",
                n_engines=c.n_engines,
                seed=c.seed,
                vari_frac=c.vari_frac,
                tank_ox_kg=c.sh_lox_kg,
                tank_ch4_kg=c.sh_ch4_kg,
                throttle_frac=c.throttle_frac,
                ambient_pressure_pa=101_325.0,
                dt=c.dt,
                expected_failures_per_run=c.sh_expected_failures,
                noise_frac=c.noise_frac,
            )
        )
        ss = Starship(
            ShipConfig(
                vehicle_id="starship",
                n_engines=6,
                engine_prefix="ss",
                seed=c.seed,
                vari_frac=c.vari_frac,
                tank_ox_kg=c.ss_lox_kg,
                tank_ch4_kg=c.ss_ch4_kg,
                throttle_frac=c.throttle_frac,
                ambient_pressure_pa=101_325.0,
                dt=c.dt,
                expected_failures_per_run=c.ss_expected_failures,
                noise_frac=c.noise_frac,
            )
        )
        return sh, ss

    def _new_model(self) -> FlightModel:
        c = self.cfg
        return FlightModel(
            site=Site(c.site_lat_deg, c.site_lon_deg, c.site_alt_m),
            heading_deg=c.heading_deg,
            pitch=PitchProgram(
                pad_clear_s=c.pitch_pad_clear_s,
                pad_clear_v=c.pitch_pad_clear_v,
                v_full=c.pitch_v_full,
                fpa_min_deg=c.pitch_fpa_min_deg,
            ),
            cda_m2=c.cda_m2,
        )

    # -- duck-typed surface for shiploop.run_stdio -------------------------------
    @property
    def config(self) -> ShipConfig:
        return self.sh.config

    @property
    def seq(self) -> int:
        return self.stack.seq

    @property
    def t(self) -> float:
        return self.stack.t

    @property
    def meco(self) -> bool:
        return self.stack.meco

    @property
    def _members(self) -> list:
        return self.stack._members

    # -- masses ---------------------------------------------------------------
    @property
    def _staged(self) -> bool:
        """Booster separated: SH's dry mass AND residual propellant are gone."""
        return self.stack.phase in (PHASE_UPPER, PHASE_DONE)

    def mass_kg(self) -> float:
        c = self.cfg
        if self._staged:
            return c.payload_kg + c.ss_dry_kg + self.ss.tank_ox_kg + self.ss.tank_ch4_kg
        return (
            c.payload_kg
            + c.sh_dry_kg
            + c.ss_dry_kg
            + self.sh.tank_ox_kg
            + self.sh.tank_ch4_kg
            + self.ss.tank_ox_kg
            + self.ss.tank_ch4_kg
        )

    def _mass_after_step(self) -> float:
        """Mass at end of the current step (tank draw already applied)."""
        return self.mass_kg()

    def thrust_n(self) -> float:
        """Live total thrust (from the last step's ship.status docs; the
        no-step status() reports 0 by design, so we cache per step)."""
        return self._last_sh_thrust + self._last_ss_thrust

    # -- step / status ----------------------------------------------------------
    def step(self) -> list[dict]:
        """Advance one shared dt: engine sim, then trajectory. Returns the
        engine fleet docs ending with `flight.state`."""
        docs = self.stack.step()
        # live thrust from this step's ship.status docs
        for d in docs:
            if d.get("type") == "ship.status":
                vid = d.get("vehicle_id")
                thr = max(0.0, float(d.get("thrust_n", 0.0)))
                if vid == "super-heavy":
                    self._last_sh_thrust = thr
                elif vid == "starship":
                    self._last_ss_thrust = thr
        docs.extend(self._guidance())
        self.model.step(
            self.cfg.dt,
            self.mass_kg(),
            self.thrust_n(),
            mass_end_kg=self.mass_kg(),
        )
        docs.append(self.status())
        return docs

    # -- guidance ---------------------------------------------------------------
    def _guidance(self) -> list[dict]:
        """Stage-separation guidance, in addition to the stack's own flow.

        · Super Heavy: MECO by fuel fraction — the booster banks
          ``sh_meco_resid_frac`` of its propellant for boostback + landing
          instead of burning to empty.
        · Starship: SECO the moment the coast state is a bound orbit whose
          perigee clears the LEO floor (orbit insertion), banking the
          residual propellant for boostback instead of burning the tank dry.
        Returns any ack docs the underlying ships emitted.
        """
        out: list[dict] = []
        sh_cut = {"type": "ship.control", "v": 1, "command": "meco"}
        ss_cut = {"type": "ship.control", "v": 1, "command": "meco"}
        if self.stack.phase == PHASE_LIFTOFF and not self.sh.meco:
            sh_now = self.sh.tank_ox_kg + self.sh.tank_ch4_kg
            sh_init = self.cfg.sh_lox_kg + self.cfg.sh_ch4_kg
            if sh_init > 0.0 and sh_now <= self.cfg.sh_meco_resid_frac * sh_init:
                out.append(self.sh.apply_control(dict(sh_cut)))
                self.sh_meco_t = self.sh.meco_t
        elif self.stack.phase == PHASE_UPPER and not self.ss.meco:
            if self._leo_established() or self._near_circular(
                self.cfg.leo_target_alt_m
            ):
                out.append(self.ss.apply_control(dict(ss_cut)))
                self.ss_seco_t = self.ss.meco_t
        return out

    def _near_circular(self, target_alt_m: float) -> bool:
        """True when the coast state is a near-circular orbit around
        ``target_alt_m`` — the right moment to cut the upper stage."""
        r = self.model.r0
        rmag = math.sqrt(r[0] ** 2 + r[1] ** 2 + r[2] ** 2)
        v = self.model.v
        vmag = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        v_circ = math.sqrt(MU / rmag)
        vr = (v[0] * r[0] + v[1] * r[1] + v[2] * r[2]) / rmag
        alt = self.model.altitude()
        return (
            100_000.0 <= alt <= target_alt_m + 130_000.0
            and 0.985 <= vmag / v_circ <= 1.018
            and abs(vr) < 60.0
        )

    def _leo_established(self) -> bool:
        """True the instant the coast state is a bound orbit whose perigee is
        at or above the LEO floor -- the vehicle has inserted into LEO and
        will not re-enter.  Firing SECO here (rather than waiting for a dry
        tank) is what banks the Starship's residual propellant for
        boostback.  Because perigee is the *lowest* point of the orbit
        ellipse, it is a state (not an instant) and is not flaky, and it
        only becomes true once the vehicle is at or above the floor."""
        orb = orbit_elements(self.model.r0, self.model.v)
        return orb is not None and orb["perigee_km"] >= self.cfg.leo_perigee_floor_km

    def status(self) -> dict:
        """Current `flight.state` document (without stepping)."""
        doc = self.model.state_doc(
            thrust_n=self.thrust_n(),
            mass_kg=self.mass_kg(),
            phase=self.stack.phase,
        )
        c = self.cfg
        staged = self._staged
        # residual = what is STILL in the tanks at staging (jettisoned with it)
        sh_res_ox = self.sh.tank_ox_kg
        sh_res_ch4 = self.sh.tank_ch4_kg
        doc["staging"] = {
            "staged": staged,
            "sh_meco_t": self.sh_meco_t,
            "ss_seco_t": self.ss_seco_t,
            "sh_boostback_reserve_kg": (
                self.sh.tank_ox_kg + self.sh.tank_ch4_kg
            ),
            "sh_dry_kg": c.sh_dry_kg,
            "sh_residual_ox_kg": sh_res_ox,
            "sh_residual_ch4_kg": sh_res_ch4,
            "jettisoned_kg": (
                0.0 if not staged else c.sh_dry_kg + sh_res_ox + sh_res_ch4
            ),
        }
        doc["ships"] = {
            "super-heavy": self._ship_doc(self.sh, self._last_sh_thrust),
            "starship": self._ship_doc(self.ss, self._last_ss_thrust),
        }
        doc["mission"] = {
            "site_lat_deg": c.site_lat_deg,
            "site_lon_deg": c.site_lon_deg,
            "site_alt_m": c.site_alt_m,
            "target_inc_deg": c.target_inc_deg,
            "heading_deg": self.heading,
            "payload_kg": c.payload_kg,
            "leo_target_alt_m": c.leo_target_alt_m,
            "sh_meco_resid_frac": c.sh_meco_resid_frac,
            "pitch_pad_clear_s": c.pitch_pad_clear_s,
            "pitch_v_full": c.pitch_v_full,
            "pitch_fpa_min_deg": c.pitch_fpa_min_deg,
        }
        return doc

    @staticmethod
    def _ship_doc(ship: Ship, thrust_n: float) -> dict:
        st = ship.status()
        return {
            "vehicle_id": ship.config.vehicle_id,
            "firing": st["firing_count"],
            "failed": st["failed_count"],
            "tank_ox_kg": ship.tank_ox_kg,
            "tank_ch4_kg": ship.tank_ch4_kg,
            "thrust_n": thrust_n,
            "meco": ship.meco,
        }

    def apply_control(self, message: dict) -> dict:
        """ship.control -> engine-sim control and/or flight rebuild."""
        cmd = message.get("command")
        overrides = {
            k: v
            for k, v in message.items()
            if k in FLIGHT_OVERRIDE_KEYS and v is not None
        }
        if cmd == "start_run" or overrides:
            rebuilt = False
            if overrides:
                self.cfg = replace(self.cfg, **overrides)
                rebuilt = True
            seed = message.get("seed")
            sh_fail = message.get("sh_expected_failures")
            ss_fail = message.get("ss_expected_failures")
            noise = message.get("noise_frac")
            if rebuilt:
                self.sh, self.ss = self._build_ships()
                self.stack = Stack(
                    self.sh,
                    self.ss,
                    StackConfig(seed=self.cfg.seed, staging_delay_s=self.cfg.staging_delay_s,
                               dt=self.cfg.dt),
                )
                self.model = self._new_model()
                self._last_sh_thrust = 0.0
                self._last_ss_thrust = 0.0
                self.sh_meco_t = None
                self.ss_seco_t = None
                self.stack.start_run(
                    seed=int(seed) if seed is not None else self.cfg.seed,
                    expected_failures_sh=(
                        float(sh_fail) if sh_fail is not None else self.cfg.sh_expected_failures),
                    expected_failures_ss=(
                        float(ss_fail) if ss_fail is not None else self.cfg.ss_expected_failures),
                    noise_frac=float(noise) if noise is not None else self.cfg.noise_frac,
                )
                return {"command": "start_run", "ok": True, "rebuilt": True}
            return self.stack.start_run(
                seed=int(seed) if seed is not None else None,
                expected_failures_sh=(
                    float(sh_fail) if sh_fail is not None else None),
                expected_failures_ss=(
                    float(ss_fail) if ss_fail is not None else None),
                noise_frac=float(noise) if noise is not None else None,
            )
        if cmd in ("meco", "throttle"):
            return self.stack.apply_control(message)
        raise ValueError(f"unknown ship control command: {cmd!r}")

    @property
    def done(self) -> bool:
        return self.stack.phase == PHASE_DONE


def make_flight(cfg: FlightCfg | None = None) -> StackFlight:
    return StackFlight(cfg or FlightCfg())
