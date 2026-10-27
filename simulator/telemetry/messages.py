"""Wire format for engine telemetry, shared between engine sims and the vehicle sim.

Design goals
------------
- One small, self-describing JSON document per engine per timestep.
  33 engines x 100 Hz is trivially light on Kafka if messages stay ~1 KB.
- Stable field names, SI units documented per field. Producers MUST NOT
  silently rename or retype fields; add fields instead.
- Versioned: ``v`` bumps on any breaking change; consumers branch on it.
- ``engine_id`` is the identity of the source. On Kafka it is used as the
  *message key*, which guarantees per-engine ordering on one partition and
  lets consumers scale out per partition.
- ``t`` is simulation time (s since mission start); ``ts_ms`` is the wall
  clock at publish (epoch ms). Consumers that replay should key on ``t``.

Message layout (v=1)
--------------------
v                         int    schema version
vehicle_id                str    vehicle this engine is mounted on (e.g. "super-heavy")
engine_id                 str    engine identity within the vehicle (e.g. "sh-07")
seq                       int    1-based step counter for this engine instance
t                         float  sim time, s since step 0
ts_ms                     int    wall-clock publish time, epoch ms
state                     str    "idle" | "firing" | "cut" | "failed"
throttle_demand_frac      float  commanded throttle (0..1, can exceed 1 at boost)
throttle_actual_frac      float  achieved throttle = Pc_actual / Pc_full
chamber_pressure_pa       float  actual chamber pressure, Pa
thrust_n                  float  static thrust at current ambient, N
isp_s                     float  thrust-based Isp, s (0 when no mass flow)
mdot_kg_s                 obj    {"total","lox","ch4"} kg/s
exhaust                   obj    {"velocity_m_s","exit_pressure_pa","exit_mach"}
propellant_remaining_kg   float  kg left on this engine (0 if unlimited load)
ambient_pressure_pa       float  ambient passed in for this step, Pa
command_seq               int    seq of the engine.command this step answered (0 = direct step)
run_id                    str?   ship run this step belongs to (absent = standalone engine)

Command layout (v=1, vehicle -> engine, type "engine.command")
--------------------------------------------------------------
v                         int    schema version
vehicle_id                str   command issuer (vehicle sim)
engine_id                 str   target engine (Kafka message key)
seq                       int    1-based per-engine command order (assigned by the vehicle)
t                         float  vehicle sim time this step applies at, s
dt                        float  integration step the engine must take, s (vehicle is pace-setter)
throttle_demand_frac      float  commanded throttle, 0..1.5
ambient_pressure_pa       float  commanded ambient, Pa
ts_ms                     int    wall-clock creation, epoch ms
run_id                    str?   ship run this command belongs to (absent = standalone)

Ship status and control (type "ship.status" / "ship.control")
--------------------------------------------------------------
The SHIP (vehicle sim) owns the shared propellant tank: LOX and CH4 are
shared across ALL engines, so the ship tracks the tank and feeds it per
cycle (consuming what the engines actually burned from their outputs).

ship.status: one doc per cycle with the shared tank state
  {
    "lox_kg", "ch4_kg", "total_kg", "total_initial_kg", "frac"
  }, fleet totals (thrust, mdot, firing/cut counts), and run identity
  (run_id, seed, vari_frac) -- the source of truth for "how much fuel is
  left on the vehicle".

ship.control: operator -> ship loop
  command                 str    "start_run" | "throttle" | "meco"
  start_run args (any subset)  seed, vari_frac, n_engines, lox_kg, ch4_kg, throttle_frac
  throttle args           throttle_frac (0..1.5)
  meco args               (none)

The command stream is the clock: an engine process takes exactly one physics
step per command, in order. The vehicle sends N commands per step (one per
engine); 33 engines x 100 Hz = 3 300 cmd/s, same order of lightness as
the telemetry direction.
"""

from __future__ import annotations

import time
from typing import NotRequired, TypedDict

from ..engine import EngineOutput, RaptorEngine

SCHEMA_VERSION = 1

# message-type constants (also used as Kafka "message-type" headers)
ENGINE_TELEMETRY = "engine.telemetry"  # one engine -> vehicle
FLEET_TELEMETRY = "fleet.telemetry"    # vehicle-level, aggregated per step
ENGINE_COMMAND = "engine.command"      # vehicle -> one engine
SHIP_STATUS = "ship.status"            # ship loop -> viewers: shared fuel + run state
SHIP_CONTROL = "ship.control"          # operator -> ship loop: start_run / throttle / meco

# engine-state words (human-readable in JSON; stable strings, not ints)
STATE_IDLE = "idle"
STATE_FIRING = "firing"
STATE_CUT = "cut"
STATE_FAILED = "failed"  # engine faulted: no thrust, not drawing propellant

# below this actual mass flow an engine counts as idle (denoise floor, kg/s)
_FIRING_EPS_MDOT = 1e-9


class TelemetryMessage(TypedDict):
    """One engine.step() result, ready to publish. JSON-serialise directly."""

    v: int
    type: str
    vehicle_id: str
    engine_id: str
    seq: int
    t: float
    ts_ms: int
    state: str
    throttle_demand_frac: float
    throttle_actual_frac: float
    chamber_pressure_pa: float
    thrust_n: float
    isp_s: float
    mdot_kg_s: dict[str, float]
    exhaust: dict[str, float]
    propellant_remaining_kg: float
    ambient_pressure_pa: float
    command_seq: int
    run_id: NotRequired[str]  # ship run this step belongs to (absent = standalone)


class CommandMessage(TypedDict):
    """One vehicle->engine command: exactly one physics step to take."""

    v: int
    type: str
    vehicle_id: str
    engine_id: str
    seq: int
    t: float
    dt: float
    throttle_demand_frac: float
    ambient_pressure_pa: float
    ts_ms: int
    run_id: NotRequired[str]  # ship run this command belongs to (absent = standalone)


class ShipStatusMessage(TypedDict, total=False):
    """Ship per-cycle state: shared tank + fleet totals + run identity.

    ``tank`` is {"lox_kg", "ch4_kg", "lox_frac", "ch4_frac", "total_kg",
    "total_initial_kg", "frac", "limited"} where each ``*_frac`` is that
    tank's remaining fraction of its initial load and ``limited`` is which
    tank will empty first ("lox"/"ch4"/"both") or None while both are healthy.
    ``thrust_min_n``/``thrust_max_n``/``thrust_spread_frac`` describe the
    per-engine fan-out of the current total; ``mixture_ratio`` is the
    ship-level LOX/CH4 demand ratio.
    """

    v: int
    type: str
    vehicle_id: str
    run_id: str
    seq: int
    t: float
    ts_ms: int
    n_engines: int
    firing_count: int
    cut_count: int
    failed_count: int
    failed_engines: list[str]
    all_firing: bool
    expected_failures_per_run: float
    fail_hazard_per_s: float
    thrust_n: float
    thrust_min_n: float
    thrust_max_n: float
    thrust_spread_frac: float
    mdot_kg_s: dict[str, float]
    tank: dict[str, float | None | str]
    meco: bool
    meco_t: float | None
    throttle_cmd_frac: float
    seed: int
    vari_frac: float
    mixture_ratio: float


#: valid ship.control verbs
SHIP_CONTROL_COMMANDS = ("start_run", "throttle", "meco")


class ShipControlMessage(TypedDict, total=False):
    """Operator -> ship loop. ``command`` picks the verb; the rest are args.

    - start_run: seed?, vari_frac?, n_engines?, lox_kg?, ch4_kg?, throttle_frac?
    - throttle:  throttle_frac
    - meco:      (none)
    """

    v: int
    type: str
    vehicle_id: str
    command: str
    ts_ms: int
    # start_run / throttle arguments
    seed: int
    vari_frac: float
    n_engines: int
    lox_kg: float
    ch4_kg: float
    throttle_frac: float


def engine_state(engine: RaptorEngine, out: EngineOutput) -> str:
    """Classify an engine step for the vehicle sim: idle, firing, or cut.

    Cut ("starved") honors the model's depletion contract: the limiting
    component drains to zero but the model squeezes out an eps-squashed
    sliver of the excess component, so "depleted" means "remaining within
    1% of the load" for a loaded engine. See tests/test_engine.py.
    """
    if engine.propellant_mass > 0.0 and out.propellant_remaining <= 0.01 * engine.propellant_mass:
        return STATE_CUT
    if out.mdot_total > _FIRING_EPS_MDOT:
        return STATE_FIRING
    return STATE_IDLE


def to_telemetry(
    *,
    engine: RaptorEngine,
    out: EngineOutput,
    engine_id: str,
    seq: int,
    vehicle_id: str = "starship",
    ts_ms: int | None = None,
    command_seq: int = 0,
) -> TelemetryMessage:
    """Build a versioned telemetry message from one engine step's output."""
    return TelemetryMessage(
        v=SCHEMA_VERSION,
        type=ENGINE_TELEMETRY,
        vehicle_id=vehicle_id,
        engine_id=engine_id,
        seq=seq,
        t=out.t,
        ts_ms=ts_ms if ts_ms is not None else int(time.time_ns() // 1_000_000),
        state=engine_state(engine, out),
        throttle_demand_frac=out.throttle,
        throttle_actual_frac=out.chamber_pressure / engine.chamber_pressure,
        chamber_pressure_pa=out.chamber_pressure,
        thrust_n=max(0.0, out.thrust),
        isp_s=out.isp,
        mdot_kg_s={
            "total": out.mdot_total,
            "lox": out.mdot_ox,
            "ch4": out.mdot_fuel,
        },
        exhaust={
            "velocity_m_s": out.exhaust_velocity,
            "exit_pressure_pa": out.exit_pressure,
            "exit_mach": out.exit_mach,
        },
        propellant_remaining_kg=out.propellant_remaining,
        ambient_pressure_pa=out.ambient_pressure,
        command_seq=command_seq,
    )


def aggregate_fleet(messages: list[TelemetryMessage]) -> dict:
    """Fold one timestep of per-engine messages into vehicle-level numbers.

    This is what the vehicle sim consumes: one row per timestep with totals
    and fleet health, independent of how many engine streams there are.
    Mixed-schema messages are rejected rather than silently mis-summed.
    """
    if not messages:
        raise ValueError("aggregate_fleet: no messages to aggregate")
    v = messages[0]["v"]
    if any(m["v"] != v for m in messages):
        raise ValueError(
            f"aggregate_fleet: mixed schema versions {sorted({m['v'] for m in messages})}"
        )

    state_counts = {STATE_IDLE: 0, STATE_FIRING: 0, STATE_CUT: 0, STATE_FAILED: 0}
    for m in messages:
        state_counts[m["state"]] += 1

    return {
        "v": v,
        "type": FLEET_TELEMETRY,
        "t_sim": messages[0]["t"],
        "ts_ms": max(m["ts_ms"] for m in messages),
        "engine_count": len(messages),
        "firing_count": state_counts[STATE_FIRING],
        "cut_count": state_counts[STATE_CUT],
        "failed_count": state_counts[STATE_FAILED],
        "all_firing": state_counts[STATE_FIRING] == len(messages),
        "any_cut": state_counts[STATE_CUT] > 0,
        "any_failed": state_counts[STATE_FAILED] > 0,
        "thrust_n": sum(m["thrust_n"] for m in messages),
        "mdot_kg_s": {
            key: sum(m["mdot_kg_s"][key] for m in messages)
            for key in messages[0]["mdot_kg_s"]
        },
        "min_propellant_remaining_kg": min(m["propellant_remaining_kg"] for m in messages),
        "min_isp_s": min(m["isp_s"] for m in messages),
        "min_chamber_pressure_pa": min(m["chamber_pressure_pa"] for m in messages),
        "engine_ids": [m["engine_id"] for m in messages],
    }


def to_command(
    *,
    engine_id: str,
    seq: int,
    throttle_demand_frac: float,
    ambient_pressure_pa: float,
    dt: float = 0.01,
    t: float = 0.0,
    vehicle_id: str = "starship",
    ts_ms: int | None = None,
) -> CommandMessage:
    """Build a versioned command for one engine step."""
    if not engine_id:
        raise ValueError("engine_id must be non-empty")
    if dt <= 0:
        raise ValueError(f"dt must be > 0, got {dt}")
    return CommandMessage(
        v=SCHEMA_VERSION,
        type=ENGINE_COMMAND,
        vehicle_id=vehicle_id,
        engine_id=engine_id,
        seq=seq,
        t=float(t),
        dt=float(dt),
        throttle_demand_frac=float(throttle_demand_frac),
        ambient_pressure_pa=float(ambient_pressure_pa),
        ts_ms=ts_ms if ts_ms is not None else int(time.time_ns() // 1_000_000),
    )


def validate_command(
    cm: CommandMessage,
    expected_engine: str | None = None,
) -> CommandMessage:
    """Lightweight type/v/range check; the engine must not choke on a wire doc.

    Raises ValueError on structural problems or (when given) a mismatched
    target engine, so a cross-wired command is skipped loudly, not burned in.
    """
    if not isinstance(cm, dict):
        raise ValueError(f"command must be a mapping, got {type(cm).__name__}")  # noqa: TRY004
    if cm.get("type") != ENGINE_COMMAND:
        raise ValueError(f"not a command: type={cm.get('type')!r}")
    if cm.get("v") != SCHEMA_VERSION:
        raise ValueError(f"unsupported command schema version: {cm.get('v')!r}")

    engine_id = cm.get("engine_id")
    if not engine_id or not isinstance(engine_id, str):
        raise ValueError(f"bad engine_id: {engine_id!r}")
    if expected_engine is not None and engine_id != expected_engine:
        raise ValueError(f"command for {engine_id!r}, but engine is {expected_engine!r}")

    dt = float(cm.get("dt", 0.0))
    if dt <= 0:
        raise ValueError(f"dt must be > 0, got {dt}")

    thr = float(cm.get("throttle_demand_frac", 0.0))
    if not 0.0 <= thr <= 1.5:
        raise ValueError(f"throttle_demand_frac out of range [0, 1.5]: {thr}")

    if float(cm.get("ambient_pressure_pa", 0.0)) < 0.0:
        raise ValueError(f"negative ambient_pressure_pa: {cm.get('ambient_pressure_pa')}")

    return cm


def to_ship_status(
    *,
    vehicle_id: str,
    run_id: str,
    seq: int,
    t: float,
    n_engines: int,
    firing_count: int,
    cut_count: int,
    thrust_n: float,
    mdot_kg_s: dict[str, float],
    tank_ox_kg: float,
    tank_ch4_kg: float,
    tank_total_initial_kg: float,
    meco: bool,
    meco_t: float | None,
    throttle_cmd_frac: float,
    seed: int,
    vari_frac: float,
    ts_ms: int | None = None,
    tank_ox0_kg: float | None = None,
    tank_ch40_kg: float | None = None,
    thrust_min_n: float | None = None,
    thrust_max_n: float | None = None,
    mixture_ratio: float | None = None,
    n_failed: int | None = None,
    failed_engines: list[str] | None = None,
    expected_failures_per_run: float | None = None,
    fail_hazard_per_s: float | None = None,
) -> ShipStatusMessage:
    """Build one ship per-cycle status document (shared fuel + run state)."""

    total = max(0.0, tank_ox_kg + tank_ch4_kg)
    frac = (total / tank_total_initial_kg) if tank_total_initial_kg > 0.0 else 0.0

    tank: dict[str, float | None | str] = {
        "lox_kg": max(0.0, float(tank_ox_kg)),
        "ch4_kg": max(0.0, float(tank_ch4_kg)),
        "total_kg": total,
        "total_initial_kg": float(tank_total_initial_kg),
        "frac": frac,
    }
    if tank_ox0_kg is not None and tank_ox0_kg > 0.0:
        tank["lox_frac"] = max(0.0, float(tank_ox_kg)) / tank_ox0_kg
    if tank_ch40_kg is not None and tank_ch40_kg > 0.0:
        tank["ch4_frac"] = max(0.0, float(tank_ch4_kg)) / tank_ch40_kg
    loxf = tank.get("lox_frac")
    ch4f = tank.get("ch4_frac")
    if loxf is not None and ch4f is not None:
        if loxf <= 1e-9 and ch4f <= 1e-9:
            tank["limited"] = "both"
        elif ch4f <= 1e-9 or (loxf < ch4f and loxf <= 0.02):
            tank["limited"] = "lox"
        elif loxf <= 1e-9 or (ch4f < loxf and ch4f <= 0.02):
            tank["limited"] = "ch4"
        else:
            tank["limited"] = None

    doc: ShipStatusMessage = {
        "v": SCHEMA_VERSION,
        "type": SHIP_STATUS,
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "seq": seq,
        "t": float(t),
        "ts_ms": ts_ms if ts_ms is not None else int(time.time_ns() // 1_000_000),
        "n_engines": int(n_engines),
        "firing_count": int(firing_count),
        "cut_count": int(cut_count),
        "failed_count": int(n_failed) if n_failed is not None else 0,
        "failed_engines": list(failed_engines) if failed_engines is not None else [],
        "all_firing": (n_engines > 0 and firing_count == n_engines),
        "thrust_n": max(0.0, float(thrust_n)),
        "mdot_kg_s": {k: float(v) for k, v in mdot_kg_s.items()},
        "tank": tank,
        "meco": bool(meco),
        "meco_t": float(meco_t) if meco_t is not None else None,
        "throttle_cmd_frac": float(throttle_cmd_frac),
        "seed": int(seed),
        "vari_frac": float(vari_frac),
    }
    if thrust_min_n is not None and thrust_max_n is not None:
        tmin, tmax = float(thrust_min_n), float(thrust_max_n)
        doc["thrust_min_n"] = max(0.0, tmin)
        doc["thrust_max_n"] = max(0.0, tmax)
        doc["thrust_spread_frac"] = (
            (tmax - tmin) / tmax if tmax > 0.0 else 0.0
        )
    if mixture_ratio is not None and mixture_ratio > 0.0:
        doc["mixture_ratio"] = float(mixture_ratio)
    if expected_failures_per_run is not None:
        doc["expected_failures_per_run"] = float(expected_failures_per_run)
    if fail_hazard_per_s is not None:
        doc["fail_hazard_per_s"] = float(fail_hazard_per_s)
    return doc


def to_ship_control(
    command: str,
    vehicle_id: str = "starship",
    ts_ms: int | None = None,
    **args: float,
) -> ShipControlMessage:
    """Build a ship.control document. Extra kwargs become that verb's arguments."""
    if command not in SHIP_CONTROL_COMMANDS:
        raise ValueError(f"unknown ship control command: {command!r}")
    msg = ShipControlMessage(
        v=SCHEMA_VERSION,
        type=SHIP_CONTROL,
        vehicle_id=vehicle_id,
        command=command,
        ts_ms=ts_ms if ts_ms is not None else int(time.time_ns() // 1_000_000),
    )
    for key, value in args.items():
        if key in ("seed", "n_engines"):
            msg[key] = int(value)  # type: ignore[literal-required]
        else:
            msg[key] = float(value)  # type: ignore[literal-required]
    return msg


def validate_ship_control(cm: dict) -> ShipControlMessage:
    """Structural + range check for a ship.control doc; raises ValueError."""
    if cm.get("type") != SHIP_CONTROL:
        raise ValueError(f"not a ship control message: type={cm.get('type')!r}")
    if cm.get("v") != SCHEMA_VERSION:
        raise ValueError(f"unsupported ship control schema version: {cm.get('v')!r}")
    command = cm.get("command")
    if command not in SHIP_CONTROL_COMMANDS:
        raise ValueError(f"unknown ship control command: {command!r}")
    if command in ("start_run", "throttle") and "throttle_frac" in cm:
        thr = float(cm["throttle_frac"])
        if not 0.0 <= thr <= 1.5:
            raise ValueError(f"throttle_frac out of range [0, 1.5]: {thr}")
    if command == "start_run":
        if cm.get("n_engines") is not None and int(cm["n_engines"]) < 1:
            raise ValueError("n_engines must be >= 1")
        if float(cm.get("lox_kg", 0.0)) < 0.0 or float(cm.get("ch4_kg", 0.0)) < 0.0:
            raise ValueError("tank masses must be >= 0")
        if float(cm.get("vari_frac", 0.0)) > 0.5:
            raise ValueError("vari_frac must be <= 0.5")
        if "expected_failures_per_run" in cm and cm["expected_failures_per_run"] is not None and float(cm["expected_failures_per_run"]) < 0.0:
            raise ValueError("expected_failures_per_run must be >= 0")
        if "fail_hazard_per_s" in cm and cm["fail_hazard_per_s"] is not None and float(cm["fail_hazard_per_s"]) < 0.0:
            raise ValueError("fail_hazard_per_s must be >= 0")
        if "noise_frac" in cm and cm["noise_frac"] is not None:
            n = float(cm["noise_frac"])
            if not 0.0 <= n <= 0.05:
                raise ValueError("noise_frac must be in [0, 0.05]")
    return cm  # type: ignore[return-value]
