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
state                     str    "idle" | "firing" | "cut"
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

The command stream is the clock: an engine process takes exactly one physics
step per command, in order. The vehicle sends N commands per step (one per
engine); 33 engines x 100 Hz = 3 300 cmd/s, same order of lightness as
the telemetry direction.
"""

from __future__ import annotations

import time
from typing import TypedDict

from ..engine import EngineOutput, RaptorEngine

SCHEMA_VERSION = 1

# message-type constants (also used as Kafka "message-type" headers)
ENGINE_TELEMETRY = "engine.telemetry"  # one engine -> vehicle
FLEET_TELEMETRY = "fleet.telemetry"    # vehicle-level, aggregated per step
ENGINE_COMMAND = "engine.command"      # vehicle -> one engine

# engine-state words (human-readable in JSON; stable strings, not ints)
STATE_IDLE = "idle"
STATE_FIRING = "firing"
STATE_CUT = "cut"

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

    state_counts = {STATE_IDLE: 0, STATE_FIRING: 0, STATE_CUT: 0}
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
        "all_firing": state_counts[STATE_FIRING] == len(messages),
        "any_cut": state_counts[STATE_CUT] > 0,
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
