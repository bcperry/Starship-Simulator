"""Standalone engine process: consume commands over Kafka, publish telemetry.

Usage (one process per engine):

    python -m simulator.telemetry \
        --engine-id sh-07 \
        --command-topic sh07.engine.command \
        --telemetry-topic starship.engine.telemetry \
        --bootstrap-servers localhost:9092 \
        [--vehicle-id starship] [--load-kg 4000] \
        [--max-steps 2000] [--idle-timeout 120]

Wiring: KafkaCommandSource(command topic, group=engine id) -> EngineProcess
-> KafkaPublisher(telemetry topic, key=engine id). The engine takes exactly
one physics step per command, in order; it exits after max_steps or when
idle for idle_timeout seconds.

Requires the optional kafka dependency:

    uv add --group kafka confluent-kafka
"""

from __future__ import annotations

import argparse

from ..engine import RaptorEngine
from .consumers import KafkaCommandSource
from .engine_feeder import EngineProcess
from .publishers import KafkaPublisher


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m simulator.telemetry",
        description="Run one raptor engine as a command-driven process over Kafka.",
    )
    p.add_argument("--engine-id", required=True, help="engine identity, e.g. sh-07")
    p.add_argument("--command-topic", default="starship.engine.command",
                   help="Kafka topic the vehicle publishes engine.command to")
    p.add_argument("--telemetry-topic", default="starship.engine.telemetry",
                   help="Kafka topic this engine publishes engine.telemetry to")
    p.add_argument("--bootstrap-servers", default="localhost:9092",
                   help="Kafka bootstrap servers (default: localhost:9092)")
    p.add_argument("--vehicle-id", default="starship", help="vehicle this engine rides on")
    p.add_argument("--load-kg", type=float, default=0.0,
                   help="propellant load in kg (0 = unlimited reservoir)")
    p.add_argument("--max-steps", type=int, default=0,
                   help="stop after this many commands (0 = run until idle timeout)")
    p.add_argument("--idle-timeout", type=float, default=120.0,
                   help="exit after this many seconds without a command")
    p.add_argument("--poll-grain", type=float, default=0.05,
                   help="command poll granularity, s")
    args = p.parse_args(argv)

    engine = RaptorEngine(propellant_mass=args.load_kg)
    command_source = KafkaCommandSource(
        topic=args.command_topic,
        engine_id=args.engine_id,
        bootstrap_servers=args.bootstrap_servers,
    )
    publisher = KafkaPublisher(
        topic=args.telemetry_topic,
        bootstrap_servers=args.bootstrap_servers,
    )
    process = EngineProcess(
        engine_id=args.engine_id,
        engine=engine,
        command_source=command_source,
        publisher=publisher,
        vehicle_id=args.vehicle_id,
    )
    try:
        done = process.run(
            max_steps=args.max_steps,
            idle_timeout=args.idle_timeout,
            poll_grain=args.poll_grain,
            log=print,
        )
    finally:
        process.close()
    print(f"[{args.engine_id}] processed {done} command(s); shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
