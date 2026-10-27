"""Ship sim: one vehicle of N Raptor engines sharing ONE propellant tank.

Model
-----
The propellant is a *vehicle* resource: a single shared LOX tank and a single
shared CH4 tank feed all engines. Each cycle the ship

1. predicts the fleet's ox/CH4 requirement for the next step (from each
   engine's current chamber pressure and its own parameters),
2. if the shared tank cannot feed a full step, scales the commanded throttle
   down to what the tank can supply (injector starvation),
3. steps every engine, and
4. consumes from the shared tank exactly what the engines actually burned
   (closed-loop on their ``EngineOutput`` mdot).

When the tank cannot feed a full step (or runs dry) the ship commands MECO:
it stops commanding flow and the engines coast-down (chamber bleeds, mass flow
decays with each engine's spool time constant) without drawing more propellant.

Per-engine starting-condition variability
-----------------------------------------
Real ships ship N engines that are NOT identical: manufacturing spread on
chamber pressure, throat area, mixture ratio, C* efficiency and fuel-valve
spool time. ``ShipConfig.vari_frac`` (default 1%) draws each of those from a
uniform +/- band, seeded per (vehicle, run seed, engine index) so the fleet is
deterministic and reproducible, while a new ``start_run(seed)`` re-draws it --
"start a new run with fresh starting conditions".

Failure model (time-to-failure)
-------------------------------
Each engine also *can die at some random moment during the burn*. The model
is constant-hazard (exponential) time-to-failure -- the standard
reliability-theory model for random hardware failures (turbopump seizure,
valve failure, combustion instability, control glitches). It is the right
choice for a single ~2.5-min burn because (a) engines do not appreciably
"wear out" inside one short burn, and (b) real observed failures happen at
ignition, mid-burn and near MECO -- a constant per-second failure rate is
the standard model compatible with all three.

``ShipConfig.expected_failures_per_run`` (default 2.0, an input) sets the
EXPECTED number of fleet failures over a nominal full-tank launch; the ship
solves for the per-engine hazard rate lambda. During the burn, each step,
each live engine rolls P(fail) = 1 - exp(-lambda*dt); the exact discrete
survival product telescopes to the continuous exponential, so the failure
times are dt-invariant. A failed engine produces no thrust and draws no
propellant; the shared tank keeps feeding only the healthy engines, so the
fleet fuel-flow accounting stays exact for any number of failures. Set
``expected_failures_per_run=0`` for failure-free runs, or pass
``fail_hazard_per_s`` directly for the raw distribution parameter.
"""

from __future__ import annotations

import dataclasses
import math
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .engine import EngineOutput, EngineState, RaptorEngine
from .engine import step as engine_step
from .telemetry.messages import (
    SCHEMA_VERSION,
    STATE_CUT,
    STATE_FAILED,
    STATE_FIRING,
    STATE_IDLE,
    ShipControlMessage,
    to_ship_control,
    to_ship_status,
    to_telemetry,
    validate_ship_control,
)

#: default fleet spread: +/-1% per parameter
DEFAULT_VARI_FRAC = 0.01

#: default expected fleet failures per nominal full-tank launch (observed
#: reality: most launches 0-2, occasionally 3+)
DEFAULT_EXPECTED_FAILURES_PER_RUN = 2.0

#: default natural high-frequency jitter: 2 % rms per-step Gaussian on
#: chamber pressure (combustion acoustics / feed turbulence) — visible
#: high-frequency breathing on every output
DEFAULT_NOISE_FRAC = 0.02

#: below this actual mass flow an engine counts as idle (denoise floor, kg/s)
_FIRING_EPS_MDOT = 1e-9


@dataclass(frozen=True)
class ShipConfig:
    """Starting conditions for a ship run.

    ``tank_ox_kg``/``tank_ch4_kg`` are the SHARED tank loads (vehicle fuel).
    ``vari_frac`` is the per-engine starting-condition spread fraction.

    Failure model inputs (see module docstring):
      - ``expected_failures_per_run``: expected fleet failures over a nominal
        full-tank launch (default 2.0; 0 disables failures). The ship converts
        this to a per-engine constant hazard rate.
      - ``fail_hazard_per_s``: directly specify the exponentials-time-to-failure
        hazard rate lambda (1/s) per engine; overrides the expected-failures
        calibration when set.

    Natural output jitter (see module docstring):
      - ``noise_frac``: per-step zero-mean Gaussian jitter (fraction of the
        nominal value) on chamber pressure and expansion efficiency, so
        chamber pressure, mass flows and thrust all breathe together at high
        frequency (default 0.02 = 2 % rms; 0.0 = deterministic baseline).
    """

    vehicle_id: str = "super-heavy"
    n_engines: int = 33
    seed: int = 1
    vari_frac: float = DEFAULT_VARI_FRAC
    tank_ox_kg: float = 5_480_000.0
    tank_ch4_kg: float = 1_520_000.0
    throttle_frac: float = 1.0
    ambient_pressure_pa: float = 101_325.0
    dt: float = 0.01
    engine_prefix: str = "sh"
    expected_failures_per_run: float = DEFAULT_EXPECTED_FAILURES_PER_RUN
    fail_hazard_per_s: float | None = None
    noise_frac: float = DEFAULT_NOISE_FRAC

    def __post_init__(self) -> None:
        if self.n_engines < 1:
            raise ValueError(f"n_engines must be >= 1, got {self.n_engines}")
        if not 0.0 <= self.vari_frac <= 0.5:
            raise ValueError(f"vari_frac must be in [0, 0.5], got {self.vari_frac}")
        if self.tank_ox_kg < 0.0 or self.tank_ch4_kg < 0.0:
            raise ValueError("tank masses must be >= 0")
        if not 0.0 <= self.throttle_frac <= 1.5:
            raise ValueError(f"throttle_frac must be in [0, 1.5], got {self.throttle_frac}")
        if self.dt <= 0.0:
            raise ValueError(f"dt must be > 0, got {self.dt}")
        if self.expected_failures_per_run < 0.0:
            raise ValueError(
                f"expected_failures_per_run must be >= 0, got {self.expected_failures_per_run}"
            )
        if self.fail_hazard_per_s is not None and self.fail_hazard_per_s < 0.0:
            raise ValueError(
                f"fail_hazard_per_s must be >= 0, got {self.fail_hazard_per_s}"
            )
        if not 0.0 <= self.noise_frac <= 0.05:
            raise ValueError(f"noise_frac must be in [0, 0.05], got {self.noise_frac}")


def make_engine(
    base: RaptorEngine, engine_index: int, vehicle_id: str, seed: int, vari_frac: float
) -> RaptorEngine:
    """One engine with +/-``vari_frac`` starting-condition spread around ``base``.

    Deterministic: the same (vehicle_id, seed, engine_index) always draws the
    same engine; different seeds draw different (but in-band) engines.
    """
    vari = max(0.0, vari_frac)
    if vari == 0.0:
        return base

    rng = random.Random(f"{vehicle_id}|{seed}|{engine_index}")

    def jitter(x: float) -> float:
        return x * (1.0 + rng.uniform(-vari, vari))

    return dataclasses.replace(
        base,
        chamber_pressure=jitter(base.chamber_pressure),
        throat_area=jitter(base.throat_area),
        mixture_ratio=jitter(base.mixture_ratio),
        cstar_efficiency=min(1.5, jitter(base.cstar_efficiency)),
        throttle_time_constant=jitter(base.throttle_time_constant),
    )


class _Member:
    """One engine slot on the ship: identity + model + its state."""

    def __init__(self, engine_id: str, engine: RaptorEngine,
                 noise_seed: str | int | None = None) -> None:
        self.engine_id = engine_id
        self.engine = engine
        # each engine draws its own reproducible high-frequency noise stream
        self.state = EngineState(engine, seed=f"starship|noise|{noise_seed}")
        self.failed = False
        self.fail_time: float | None = None


class Ship:
    """A vehicle of shared-fuel engines, step-able or runnable as a loop.

    The ship is the pace-setter and the fuel authority: it owns the run, the
    shared tank, and the per-cycle feed decision. Engines carry unlimited
    propellant (``propellant_mass=0``) -- they burn whatever the ship feeds.
    """

    def __init__(
        self,
        config: ShipConfig | None = None,
        base_engine: RaptorEngine | None = None,
    ) -> None:
        self.config = config or ShipConfig()
        self._base = base_engine or RaptorEngine(
            throttle_time_constant=0.25, noise_frac=self.config.noise_frac
        )
        self.seed = self.config.seed
        self.vari_frac = self.config.vari_frac
        self.throttle_cmd = self.config.throttle_frac
        self.run_id = self.start_run()  # fresh fleet + tank + states

    # -- run lifecycle ------------------------------------------------------
    def start_run(
        self,
        *,
        seed: int | None = None,
        n_engines: int | None = None,
        vari_frac: float | None = None,
        tank_ox_kg: float | None = None,
        tank_ch4_kg: float | None = None,
        throttle_frac: float | None = None,
        expected_failures_per_run: float | None = None,
        fail_hazard_per_s: float | None = None,
        noise_frac: float | None = None,
    ) -> str:
        """(Re)start a run: re-draw fleet variability and reset tank + states.

        Any omitted argument keeps the current value. Returns the new run_id.

        ``expected_failures_per_run`` / ``fail_hazard_per_s`` set the
        per-launch engine-failure model (exponential time-to-failure):
        expected fleet failures over the nominal full-tank burn, or the raw
        hazard rate in 1/s (takes precedence when > 0; pass 0 to clear an
        earlier override and fall back to the expectation).
        """
        seed = self.seed if seed is None else int(seed)
        n_engines = self.config.n_engines if n_engines is None else int(n_engines)
        vari_frac = self.vari_frac if vari_frac is None else float(vari_frac)
        tank_ox_kg = self.config.tank_ox_kg if tank_ox_kg is None else float(tank_ox_kg)
        tank_ch4_kg = self.config.tank_ch4_kg if tank_ch4_kg is None else float(tank_ch4_kg)
        expected_failures = (
            self.config.expected_failures_per_run
            if expected_failures_per_run is None
            else float(expected_failures_per_run)
        )
        fail_hazard = (
            self.config.fail_hazard_per_s
            if fail_hazard_per_s is None
            else (None if float(fail_hazard_per_s) <= 0.0 else float(fail_hazard_per_s))
        )
        if throttle_frac is not None:
            self.throttle_cmd = float(throttle_frac)

        if not 0.0 <= vari_frac <= 0.5:
            raise ValueError(f"vari_frac must be in [0, 0.5], got {vari_frac}")
        if n_engines < 1:
            raise ValueError(f"n_engines must be >= 1, got {n_engines}")
        if tank_ox_kg < 0.0 or tank_ch4_kg < 0.0:
            raise ValueError("tank masses must be >= 0")
        if expected_failures < 0.0:
            raise ValueError(f"expected_failures_per_run must be >= 0, got {expected_failures}")
        if fail_hazard is not None and fail_hazard < 0.0:
            raise ValueError(f"fail_hazard_per_s must be >= 0, got {fail_hazard}")
        if noise_frac is not None and not 0.0 <= float(noise_frac) <= 0.05:
            raise ValueError(f"noise_frac must be in [0, 0.05], got {noise_frac}")
        if noise_frac is not None and self._base is not None:
            # live retune for runs started via the control path
            self._base = dataclasses.replace(self._base, noise_frac=float(noise_frac))

        self.config = dataclasses.replace(
            self.config,
            n_engines=n_engines,
            seed=seed,
            vari_frac=vari_frac,
            tank_ox_kg=tank_ox_kg,
            tank_ch4_kg=tank_ch4_kg,
            expected_failures_per_run=expected_failures,
            fail_hazard_per_s=fail_hazard,
            **( {"noise_frac": float(noise_frac)} if noise_frac is not None else {} ),
        )
        self.seed = seed
        self.vari_frac = vari_frac
        self._members = [
            _Member(
                engine_id=f"{self.config.engine_prefix}-{i + 1:02d}",
                engine=make_engine(self._base, i, self.config.vehicle_id, seed, vari_frac),
                noise_seed=f"{self.config.vehicle_id}|{seed}|{i}",
            )
            for i in range(n_engines)
        ]
        self.tank_ox_kg = tank_ox_kg
        self.tank_ch4_kg = tank_ch4_kg
        self.tank_ox0_kg = tank_ox_kg
        self.tank_ch40_kg = tank_ch4_kg
        self.seq = 0
        self.t = 0.0
        self.meco = False
        self.meco_t: float | None = None
        self.run_id = f"run-{seed:04d}-{uuid.uuid4().hex[:8]}"
        # per-engine failure hazard for this launch (constant hazard /
        # exponential time-to-failure) + deterministic roll stream; re-drawn
        # for every new run so each launch gets a fresh failure profile
        self._fail_lambda = self._fail_hazard(n_engines, tank_ox_kg + tank_ch4_kg)
        self._fail_rng = random.Random(f"{self.config.vehicle_id}|{seed}|ttf")
        return self.run_id

    def _nominal_burn_s(self, tank_total_kg: float) -> float:
        """Seconds for the whole fleet to consume ``tank_total_kg`` at 100% throttle (nominal engine)."""
        eng = self._base
        if eng is None or tank_total_kg <= 0.0 or not self._members:
            return 0.0
        mdot_full = eng.cstar_efficiency * eng.specific_mass_flow * eng.chamber_pressure
        fleet_mdot = mdot_full * len(self._members)
        return tank_total_kg / fleet_mdot if fleet_mdot > 0.0 else 0.0

    def _fail_hazard(self, n_engines: int, tank_total_kg: float) -> float:
        """Per-engine failure hazard rate lambda (1/s) for this run.

        If ``fail_hazard_per_s`` is set it is the distribution parameter
        directly. Otherwise calibrate the constant-hazard model so the EXPECTED
        fleet failures over a nominal full-tank, 100%-throttle burn equal
        ``expected_failures_per_run``:
            E[fleet failures] = N * (1 - exp(-lambda * T_nom))
        ->  lambda = -ln(1 - min(E/N, 0.999)) / T_nom
        """
        if self.config.fail_hazard_per_s is not None:
            return max(0.0, float(self.config.fail_hazard_per_s))
        expected = float(self.config.expected_failures_per_run)
        if expected <= 0.0:
            return 0.0
        t_nominal = self._nominal_burn_s(tank_total_kg)
        if t_nominal <= 0.0:
            return 0.0
        per_engine_p = min(expected / max(1, n_engines), 0.999)
        return -math.log(1.0 - per_engine_p) / t_nominal

    # -- command handling ---------------------------------------------------
    def apply_control(self, cm: ShipControlMessage | dict) -> dict:
        """Apply one ship.control verb. Returns a small ack dict."""
        ctrl = validate_ship_control(dict(cm))
        command = ctrl["command"]

        if command == "start_run":
            run_id = self.start_run(
                seed=ctrl.get("seed"),
                n_engines=ctrl.get("n_engines"),
                vari_frac=ctrl.get("vari_frac"),
                tank_ox_kg=ctrl.get("lox_kg"),
                tank_ch4_kg=ctrl.get("ch4_kg"),
                throttle_frac=ctrl.get("throttle_frac"),
                expected_failures_per_run=ctrl.get("expected_failures_per_run"),
                fail_hazard_per_s=ctrl.get("fail_hazard_per_s"),
                noise_frac=ctrl.get("noise_frac"),
            )
            return {"command": "start_run", "ok": True, "run_id": run_id}

        if command == "throttle":
            if self.meco:
                return {"command": "throttle", "ok": False, "reason": "meco"}
            self.throttle_cmd = float(ctrl.get("throttle_frac", 0.0) or 0.0)
            return {"command": "throttle", "ok": True, "throttle_frac": self.throttle_cmd}

        if command == "meco":
            if not self.meco:
                self.meco = True
                self.meco_t = self.t
            return {"command": "meco", "ok": True}

        raise ValueError(f"unhandled ship control command: {command!r}")

    # -- per-cycle feed + step ----------------------------------------------
    def _predicted_consumption(self, throttle: float, dt: float) -> tuple[float, float]:
        """Next step's ox/CH4 requirement (kg) if every healthy engine is fed at ``throttle``.

        Failed engines draw nothing, so they are excluded from the demand.
        """
        ox = ch4 = 0.0
        for member in self._members:
            if member.failed:
                continue
            eng = member.engine
            pc = member.state.chamber_pressure
            target = max(0.0, throttle * eng.chamber_pressure)
            pc_next = max(pc + (target - pc) * (dt / eng.throttle_time_constant), 0.0)
            mdot = eng.cstar_efficiency * eng.specific_mass_flow * pc_next
            ratio = 1.0 / (1.0 + eng.mixture_ratio)
            ch4 += mdot * ratio * dt
            ox += mdot * (1.0 - ratio) * dt
        return ox, ch4

    def step(self) -> list[dict]:
        """Advance the ship one cycle.

        Returns the publishable documents for this cycle: one engine.command
        per engine, one engine.telemetry per engine, and one ship.status.
        """
        self.seq += 1
        seq = self.seq
        t = self.t
        dt = self.config.dt

        # failure rolls: each live engine fails this step with the exact
        # discrete survival probability of a constant-hazard (exponential
        # time-to-failure) model, P(fail | alive) = 1 - exp(-lambda*dt).
        # the telescoping survival product is dt-invariant, so failure
        # times do not depend on the integration step.
        if self._fail_lambda > 0.0 and not self.meco:
            q = 1.0 - math.exp(-self._fail_lambda * dt)
            for member in self._members:
                if not member.failed and self._fail_rng.random() < q:
                    member.failed = True
                    member.fail_time = t

        cmd_frac = 0.0 if self.meco else self.throttle_cmd

        # shared-tank feed decision: how much of this step can we actually feed?
        feed_frac = 1.0
        if cmd_frac > 0.0:
            need_ox, need_ch4 = self._predicted_consumption(cmd_frac, dt)
            if need_ox > 0.0:
                feed_frac = min(feed_frac, self.tank_ox_kg / need_ox)
            if need_ch4 > 0.0:
                feed_frac = min(feed_frac, self.tank_ch4_kg / need_ch4)
            if need_ox <= 0.0 and need_ch4 <= 0.0:
                feed_frac = 0.0
        eff_cmd = cmd_frac * max(0.0, min(1.0, feed_frac))

        # step the healthy engines (unlimited propellant; the ship is the fuel
        # authority). A failed engine produces zero thrust and draws zero
        # propellant -- it just isn't run.
        outputs: list[EngineOutput] = []
        for member in self._members:
            if member.failed:
                outputs.append(dataclasses.replace(
                    EngineOutput(
                        t=t,
                        throttle=0.0,
                        chamber_pressure=member.state.chamber_pressure,
                        mdot_total=0.0,
                        mdot_ox=0.0,
                        mdot_fuel=0.0,
                        exit_pressure=0.0,
                        exit_mach=0.0,
                        exhaust_velocity=0.0,
                        thrust=0.0,
                        isp=0.0,
                        ambient_pressure=self.config.ambient_pressure_pa,
                        # 0.0 = "untracked" (vehicle is the fuel authority)
                        # -- must stay FINITE: Infinity is invalid JSON and
                        # downstream parsers drop the whole line.
                        propellant_remaining=0.0,
                    )
                ))
                continue
            outputs.append(
                engine_step(
                    member.engine,
                    member.state,
                    eff_cmd,
                    ambient_pressure=self.config.ambient_pressure_pa,
                    dt=dt,
                    t=t,
                )
            )

        # consume from the SHARED tank exactly what the healthy engines burned
        if not self.meco and eff_cmd > 0.0:
            for member, out in zip(self._members, outputs):
                if member.failed:
                    continue
                self.tank_ox_kg = max(0.0, self.tank_ox_kg - out.mdot_ox * dt)
                self.tank_ch4_kg = max(0.0, self.tank_ch4_kg - out.mdot_fuel * dt)

        # MECO: the tank cannot feed a full step (or ran dry) -> stop commanding flow
        if (not self.meco) and (
            feed_frac < 1.0 or self.tank_ox_kg <= 0.0 or self.tank_ch4_kg <= 0.0
        ):
            self.meco = True
            if self.meco_t is None:
                self.meco_t = t

        # build the publishable documents for this cycle
        docs: list[dict] = []
        healthy = [(m, o) for m, o in zip(self._members, outputs) if not m.failed]
        for member, out in zip(self._members, outputs):
            # a failed engine is dead: zero thrust, zero draw, state "failed".
            # on the ship the vehicle is the fuel authority otherwise:
            # after MECO every live engine is starved ("cut")
            if member.failed:
                state = STATE_FAILED
            else:
                state = STATE_CUT if self.meco else (STATE_FIRING if out.mdot_total > _FIRING_EPS_MDOT else STATE_IDLE)
            docs.append(
                {
                    "v": SCHEMA_VERSION,
                    "type": "engine.command",
                    "vehicle_id": self.config.vehicle_id,
                    "engine_id": member.engine_id,
                    "seq": seq,
                    "t": t,
                    "dt": dt,
                    "throttle_demand_frac": 0.0 if member.failed else eff_cmd,
                    "ambient_pressure_pa": self.config.ambient_pressure_pa,
                    "run_id": self.run_id,
                }
            )
            tel = {
                **to_telemetry(
                    engine=member.engine,
                    out=out,
                    engine_id=member.engine_id,
                    seq=seq,
                    vehicle_id=self.config.vehicle_id,
                ),
                "run_id": self.run_id,
            }
            tel["state"] = state
            docs.append(tel)

        # fleet totals count only live engines (failed engines contribute
        # nothing: zero thrust, zero flow)
        healthy_outs = [o for _, o in healthy]
        firing = sum(1 for o in healthy_outs if o.mdot_total > _FIRING_EPS_MDOT)
        thrusts = [max(0.0, o.thrust) for o in healthy_outs]
        mdot_lox = sum(o.mdot_ox for o in healthy_outs)
        mdot_ch4 = sum(o.mdot_fuel for o in healthy_outs)
        n_failed = len(self._members) - len(healthy)
        failed_ids = [m.engine_id for m in self._members if m.failed]
        docs.append(
            to_ship_status(
                vehicle_id=self.config.vehicle_id,
                run_id=self.run_id,
                seq=seq,
                t=t,
                n_engines=len(self._members),
                firing_count=0 if self.meco else firing,
                cut_count=len(healthy) if self.meco else 0,
                n_failed=n_failed,
                failed_engines=failed_ids,
                expected_failures_per_run=self.config.expected_failures_per_run,
                fail_hazard_per_s=self._fail_lambda,
                thrust_n=sum(thrusts),
                thrust_min_n=min(thrusts) if thrusts else 0.0,
                thrust_max_n=max(thrusts) if thrusts else 0.0,
                mdot_kg_s={
                    "total": sum(o.mdot_total for o in outputs),
                    "lox": mdot_lox,
                    "ch4": mdot_ch4,
                },
                tank_ox_kg=self.tank_ox_kg,
                tank_ch4_kg=self.tank_ch4_kg,
                tank_total_initial_kg=self.tank_ox0_kg + self.tank_ch40_kg,
                tank_ox0_kg=self.tank_ox0_kg,
                tank_ch40_kg=self.tank_ch40_kg,
                meco=self.meco,
                meco_t=self.meco_t,
                throttle_cmd_frac=eff_cmd,
                seed=self.seed,
                vari_frac=self.vari_frac,
                mixture_ratio=self.mixture_ratio,
            )
        )

        self.t += dt
        return docs

    @property
    def mixture_ratio(self) -> float:
        """Ship-level LOX:CH4 demand ratio (all engines share the recipe)."""
        if self._members:
            return float(self._members[0].engine.mixture_ratio)
        return 3.6

    def status(self) -> dict:
        """The current ship.status document (without stepping)."""
        healthy = [m for m in self._members if not m.failed]
        firing = sum(1 for m in healthy if m.state.chamber_pressure > 0.0)
        n_failed = len(self._members) - len(healthy)
        return to_ship_status(
            vehicle_id=self.config.vehicle_id,
            run_id=self.run_id,
            seq=self.seq,
            t=self.t,
            n_engines=len(self._members),
            firing_count=0 if self.meco else firing,
            cut_count=len(healthy) if self.meco else 0,
            n_failed=n_failed,
            failed_engines=[m.engine_id for m in self._members if m.failed],
            expected_failures_per_run=self.config.expected_failures_per_run,
            fail_hazard_per_s=getattr(self, "_fail_lambda", 0.0),
            thrust_n=0.0,
            mdot_kg_s={"total": 0.0, "lox": 0.0, "ch4": 0.0},
            tank_ox_kg=self.tank_ox_kg,
            tank_ch4_kg=self.tank_ch4_kg,
            tank_total_initial_kg=self.tank_ox0_kg + self.tank_ch40_kg,
            tank_ox0_kg=self.tank_ox0_kg,
            tank_ch40_kg=self.tank_ch40_kg,
            meco=self.meco,
            meco_t=self.meco_t,
            throttle_cmd_frac=self.throttle_cmd,
            seed=self.seed,
            vari_frac=self.vari_frac,
            mixture_ratio=self.mixture_ratio,
        )

    def run(
        self,
        max_steps: int,
        publisher: Callable[[dict], None] | None = None,
        coast_down: bool = True,
    ) -> int:
        """Run up to ``max_steps``; publish each cycle's documents if given a publisher.

        With ``coast_down`` the engines keep stepping after MECO until the
        chamber bleeds (so the coast-down is visible), else stop at MECO.
        Returns the number of steps run.
        """
        steps_run = 0
        for _ in range(max_steps):
            docs = self.step()
            if publisher is not None:
                for doc in docs:
                    publisher(doc)
            steps_run += 1
            if self.meco and (not coast_down or docs[-1]["mdot_kg_s"]["total"] < 1e-6):
                break
        return steps_run


def ship_control(vehicle_id: str, command: str, **kw: float) -> ShipControlMessage:
    """Build a ship.control doc: ``ship_control("starship", "throttle", throttle_frac=0.8)``."""
    return to_ship_control(command, vehicle_id=vehicle_id, **kw)


def fmt_kg(kg: float) -> str:
    return f"{kg / 1000.0:,.1f} kt" if kg >= 1000.0 else f"{kg:,.0f} kg"


__all__ = [
    "DEFAULT_EXPECTED_FAILURES_PER_RUN",
    "DEFAULT_VARI_FRAC",
    "Ship",
    "ShipConfig",
    "fmt_kg",
    "make_engine",
    "ship_control",
]
