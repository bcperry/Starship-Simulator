"""Engine instances: physics step + telemetry emission.

Two drives, one core:

- ``TelemetryEngine``: the vehicle calls ``step(throttle, ambient)`` directly
  (in-process integration, demos, tests).
- ``EngineProcess``: the vehicle pushes ``engine.command`` messages through a
  ``CommandSource`` and the engine takes exactly one physics step per command.
  The command stream is the clock, so there is no engine-side loop to drift.
  This is the shape of the per-engine production process
  (``python -m simulator.telemetry --engine-id sh-07 ...``).

Both publish one ``TelemetryMessage`` per step through a shared ``Publisher``.
"""

from __future__ import annotations

from ..engine import EngineOutput, EngineState, RaptorEngine, step
from .consumers import CommandSource
from .messages import CommandMessage, TelemetryMessage, to_telemetry, validate_command
from .publishers import Publisher


class _EngineSimCore:
    """Shared per-engine instance state + advance/emit logic."""

    def __init__(
        self,
        engine_id: str,
        engine: RaptorEngine,
        publisher: Publisher,
        vehicle_id: str = "starship",
        dt: float = 0.01,
        ambient_pressure: float = 101_325.0,
    ) -> None:
        if dt <= 0:
            raise ValueError(f"dt must be > 0, got {dt}")
        if not engine_id:
            raise ValueError("engine_id must be a non-empty string")
        self.engine_id = engine_id
        self.vehicle_id = vehicle_id
        self.engine = engine
        self.publisher = publisher
        self.dt = dt
        self.ambient_pressure = ambient_pressure
        self.state = EngineState(engine)
        self.t = 0.0
        self.seq = 0
        self.last: EngineOutput | None = None
        self.processed = 0

    def _advance(
        self,
        throttle: float,
        ambient: float,
        dt: float,
        command_seq: int,
    ) -> TelemetryMessage:
        out = step(self.engine, self.state, throttle, ambient, dt, t=self.t)
        self.seq += 1
        msg = to_telemetry(
            engine=self.engine,
            out=out,
            engine_id=self.engine_id,
            seq=self.seq,
            vehicle_id=self.vehicle_id,
            command_seq=command_seq,
        )
        self.publisher.publish(self.engine_id, msg)
        self.last = out
        self.t += dt
        self.processed += 1
        return msg

    @property
    def propellant_remaining(self) -> float:
        return self.state.propellant_remaining


class TelemetryEngine(_EngineSimCore):
    """One engine stepped directly by the caller, telemetry emitted per step.

    ``ambient_pressure`` can be updated between steps (e.g. the vehicle
    pushing altitude); the last value set is used when a step omits it.

    >>> pub = InMemoryPublisher()
    >>> eng = TelemetryEngine("sh-01", RaptorEngine(), pub)
    >>> msg = eng.step(throttle=1.0)
    >>> pub.drain()
    """

    def step(self, throttle: float, ambient_pressure: float | None = None) -> TelemetryMessage:
        """Advance the engine by self.dt at the given throttle, emit telemetry."""
        ambient = ambient_pressure if ambient_pressure is not None else self.ambient_pressure
        return self._advance(float(throttle), float(ambient), self.dt, command_seq=0)


class EngineProcess(_EngineSimCore):
    """One engine driven by a command stream (vehicle to engine).

    One ``engine.command`` per physics step; each command carries its own
    dt / throttle / ambient, so the vehicle is the sole pace-setter.
    Usable fully in-process via ``InMemoryCommandSource`` (what the fleet
    demo and tests do) or as a standalone process over Kafka
    (``simulator/telemetry/__main__.py``).

    >>> src = InMemoryCommandSource(); src.offer(cmd)
    >>> proc = EngineProcess("sh-07", RaptorEngine(), src, pub)
    >>> proc.step_on_command(src.poll(0.0))
    """

    def __init__(
        self,
        engine_id: str,
        engine: RaptorEngine,
        command_source: CommandSource,
        publisher: Publisher,
        vehicle_id: str = "starship",
        dt: float = 0.01,
        ambient_pressure: float = 101_325.0,
    ) -> None:
        super().__init__(engine_id, engine, publisher, vehicle_id, dt, ambient_pressure)
        self.command_source = command_source
        self.closed = False

    def step_on_command(self, cmd: CommandMessage) -> TelemetryMessage:
        """Validate one command and take exactly one step for it.

        Raises ValueError on a malformed or mis-addressed command so a
        cross-wired message is skipped loudly, never silently burned in.
        """
        c = validate_command(cmd, expected_engine=self.engine_id)
        return self._advance(
            float(c["throttle_demand_frac"]),
            float(c["ambient_pressure_pa"]),
            float(c["dt"]),
            command_seq=int(c["seq"]),
        )

    def run(
        self,
        max_steps: int = 0,
        idle_timeout: float = 120.0,
        poll_grain: float = 0.05,
        log=None,
    ) -> int:
        """Process commands until max_steps, an idle timeout, or closed().

        max_steps=0 means "run until told to stop". Unusable commands are
        logged (if log given) and skipped instead of killing the process;
        the broker is the source of truth for ordering either way.
        """
        if poll_grain <= 0:
            raise ValueError("poll_grain must be > 0")
        idle = 0.0
        while not self.closed and (max_steps <= 0 or self.processed < max_steps):
            cmd = self.command_source.poll(poll_grain)
            if cmd is None:
                idle += poll_grain
                if idle_timeout > 0 and idle >= idle_timeout:
                    break
                continue
            idle = 0.0
            try:
                self.step_on_command(cmd)
            except (ValueError, TypeError) as exc:
                if log is not None:
                    log(f"[{self.engine_id}] skipping unusable command: {exc}")
        return self.processed

    def close(self) -> None:
        self.closed = True
        self.command_source.close()
        self.publisher.close()
