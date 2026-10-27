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
"""

from __future__ import annotations

import dataclasses
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .engine import EngineState, RaptorEngine
from .engine import step as engine_step
from .telemetry.messages import (
    SCHEMA_VERSION,
    STATE_CUT,
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

#: below this actual mass flow an engine counts as idle (denoise floor, kg/s)
_FIRING_EPS_MDOT = 1e-9


@dataclass(frozen=True)
class ShipConfig:
    """Starting conditions for a ship run.

    ``tank_ox_kg``/``tank_ch4_kg`` are the SHARED tank loads (vehicle fuel).
    ``vari_frac`` is the per-engine starting-condition spread fraction.
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

    def __init__(self, engine_id: str, engine: RaptorEngine) -> None:
        self.engine_id = engine_id
        self.engine = engine
        self.state = EngineState(engine)


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
        self._base = base_engine or RaptorEngine(throttle_time_constant=0.25)
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
    ) -> str:
        """(Re)start a run: re-draw fleet variability and reset tank + states.

        Any omitted argument keeps the current value. Returns the new run_id.
        """
        seed = self.seed if seed is None else int(seed)
        n_engines = self.config.n_engines if n_engines is None else int(n_engines)
        vari_frac = self.vari_frac if vari_frac is None else float(vari_frac)
        tank_ox_kg = self.config.tank_ox_kg if tank_ox_kg is None else float(tank_ox_kg)
        tank_ch4_kg = self.config.tank_ch4_kg if tank_ch4_kg is None else float(tank_ch4_kg)
        if throttle_frac is not None:
            self.throttle_cmd = float(throttle_frac)

        if not 0.0 <= vari_frac <= 0.5:
            raise ValueError(f"vari_frac must be in [0, 0.5], got {vari_frac}")
        if n_engines < 1:
            raise ValueError(f"n_engines must be >= 1, got {n_engines}")
        if tank_ox_kg < 0.0 or tank_ch4_kg < 0.0:
            raise ValueError("tank masses must be >= 0")

        self.config = dataclasses.replace(
            self.config,
            n_engines=n_engines,
            seed=seed,
            vari_frac=vari_frac,
            tank_ox_kg=tank_ox_kg,
            tank_ch4_kg=tank_ch4_kg,
        )
        self.seed = seed
        self.vari_frac = vari_frac
        self._members = [
            _Member(
                engine_id=f"{self.config.engine_prefix}-{i + 1:02d}",
                engine=make_engine(self._base, i, self.config.vehicle_id, seed, vari_frac),
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
        return self.run_id

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
        """Next step's ox/CH4 requirement (kg) if every engine is fed at ``throttle``."""
        ox = ch4 = 0.0
        for member in self._members:
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

        # step every engine (unlimited propellant; the ship is the fuel authority)
        outputs = [
            engine_step(
                member.engine,
                member.state,
                eff_cmd,
                ambient_pressure=self.config.ambient_pressure_pa,
                dt=dt,
                t=t,
            )
            for member in self._members
        ]

        # consume from the SHARED tank exactly what the engines burned this step
        if not self.meco and eff_cmd > 0.0:
            for out in outputs:
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
        for member, out in zip(self._members, outputs):
            # on the ship the vehicle is the fuel authority: after MECO every
            # engine is starved ("cut"); before, firing iff it has mass flow
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
                    "throttle_demand_frac": eff_cmd,
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

        firing = sum(1 for o in outputs if o.mdot_total > _FIRING_EPS_MDOT)
        thrusts = [max(0.0, o.thrust) for o in outputs]
        mdot_lox = sum(o.mdot_ox for o in outputs)
        mdot_ch4 = sum(o.mdot_fuel for o in outputs)
        docs.append(
            to_ship_status(
                vehicle_id=self.config.vehicle_id,
                run_id=self.run_id,
                seq=seq,
                t=t,
                n_engines=len(self._members),
                firing_count=0 if self.meco else firing,
                cut_count=len(self._members) if self.meco else 0,
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
        firing = sum(
            1 for m in self._members if m.state.chamber_pressure > 0.0
        )
        return to_ship_status(
            vehicle_id=self.config.vehicle_id,
            run_id=self.run_id,
            seq=self.seq,
            t=self.t,
            n_engines=len(self._members),
            firing_count=0 if self.meco else firing,
            cut_count=len(self._members) if self.meco else 0,
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
    "DEFAULT_VARI_FRAC",
    "Ship",
    "ShipConfig",
    "fmt_kg",
    "make_engine",
    "ship_control",
]
