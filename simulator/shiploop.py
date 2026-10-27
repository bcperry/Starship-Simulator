"""Ship loop process: drive a :class:`~simulator.ship.Ship` with a control channel.

Two transports, one physics core, same documents on the wire:

- stdio (default): control documents arrive as JSON lines on stdin, ship
  documents leave as one JSON line each on stdout, paced in real time.
  This is what the telemetry viewer spawns to get an interactive ship without
  a broker.

- kafka: publishes engine.command / engine.telemetry / ship.status to topics
  (status keyed by vehicle_id) and consumes operator ship.control documents
  from a control topic. The proper replacement for the old bash vehicle loop.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import threading
import time
from queue import Empty, Queue

from .ship import Ship, ShipConfig
from .telemetry.messages import ENGINE_COMMAND, validate_ship_control

# --------------------------------------------------------------------------- #
# stdio transport: JSON lines in on stdin, JSON lines out on stdout
# --------------------------------------------------------------------------- #


class StdioControlReader:
    """Non-blocking line reader for stdin (works with pipes *and* /dev/null)."""

    def __init__(self) -> None:
        self._buf = b""
        self.eof = False
        self._fd: int | None = None
        try:
            fd = sys.stdin.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self._fd = fd
        except (OSError, ValueError):
            self.eof = True  # no usable stdin (closed / /dev/null): run uncontrolled

    def pump(self, timeout: float = 0.0) -> list[dict]:
        """Return any complete, parseable control lines currently available.

        A non-blocking read yields data, EAGAIN ("nothing yet"), or b"" (the
        peer closed the pipe). Only the last is terminal: latching EOF on
        EAGAIN would deafen the ship the moment it is asked to wait.
        """
        del timeout  # pacing happens in the caller's sleep; read is non-blocking
        controls: list[dict] = []
        if self._fd is not None and not self.eof:
            try:
                chunk = os.read(self._fd, 65536)
            except (BlockingIOError, InterruptedError):
                chunk = None  # no data right now -- not EOF
            except OSError:
                chunk = None
            if chunk is None:
                pass
            elif chunk:
                self._buf += chunk
            else:
                self.eof = True  # writer side is closed for good
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict):
                controls.append(doc)
        return controls


def _coasted(ship: Ship) -> bool:
    """True once a MECO run has bled its chambers down (mass flow ~ 0)."""
    if not ship.meco:
        return False
    return all(m.state.chamber_pressure <= 1e-6 for m in ship._members)


def run_stdio(ship: Ship, speed: float, emit_every: int, log) -> int:
    """Real-time paced ship; controls on stdin, documents out on stdout."""
    out, reader = sys.stdout, StdioControlReader()
    dt = ship.config.dt
    cycle = dt / max(speed, 1e-9)
    last_idle_emit = 0.0

    def emit(docs: list[dict]) -> None:
        nonlocal last_idle_emit
        for d in docs:
            out.write(json.dumps(d) + "\n")
        out.flush()
        last_idle_emit = time.monotonic()

    emit([ship.status()])  # initial state so the UI can render immediately
    while True:
        t0 = time.monotonic()
        for doc in reader.pump(cycle * 0.5):
            try:
                validate_ship_control(doc)
                ack = ship.apply_control(doc)
            except (ValueError, KeyError) as exc:
                ack = {"ok": False, "error": str(exc)}
            out.write(json.dumps({"type": "ship.ack", **ack}) + "\n")
            out.flush()

        if not ship.meco or not _coasted(ship):
            docs = ship.step()
            if emit_every <= 1 or ship.seq % emit_every == 0:
                emit(docs)
            log(ship.seq, docs[-1])
        elif time.monotonic() - last_idle_emit >= 0.25:
            # heartbeat while idle, waiting for a new run (UI keeps liveness)
            emit([ship.status()])

        if reader.eof and ship.meco and _coasted(ship):
            emit([{"type": "ship.eof", "status": ship.status()}])
            break
        time.sleep(max(0.0, cycle - (time.monotonic() - t0)))
    return 0


# --------------------------------------------------------------------------- #
# kafka transport
# --------------------------------------------------------------------------- #


def run_kafka(
    ship: Ship,
    bootstrap: str,
    command_topic: str,
    telemetry_topic: str,
    control_topic: str,
    duration: float | None,
    log,
) -> int:
    """Ship loop over Kafka: publish each cycle, consume ship.control."""
    from .telemetry.publishers import KafkaPublisher

    cmd_pub = KafkaPublisher(command_topic, bootstrap_servers=bootstrap)
    tel_pub = KafkaPublisher(telemetry_topic, bootstrap_servers=bootstrap)
    ctl: Queue[dict] = Queue()
    control_consumer = None

    try:
        from confluent_kafka import Consumer

        consumer = Consumer(
            {
                "bootstrap.servers": bootstrap,
                "group.id": f"ship-{ship.config.vehicle_id}",
                "auto.offset.reset": "latest",
            }
        )
        consumer.subscribe([control_topic])
        control_consumer = consumer
    except Exception as exc:  # noqa: BLE001 - transport is optional; the ship still runs
        print(f"ship: control topic unavailable ({exc}); running uncontrolled", file=sys.stderr)

    def _drain_controls() -> list[dict]:
        docs: list[dict] = []
        while True:
            try:
                docs.append(ctl.get_nowait())
            except Empty:
                return docs

    def _publish(docs: list[dict]) -> None:
        for doc in docs:
            if doc["type"] == ENGINE_COMMAND:
                cmd_pub.publish(doc["engine_id"], doc)
            else:
                # telemetry + ship.status share the telemetry topic; status
                # is keyed by the vehicle so it stays on one partition
                tel_pub.publish(doc.get("engine_id", ship.config.vehicle_id), doc)

    if control_consumer is not None:
        def _consume() -> None:
            while True:
                try:
                    msg = control_consumer.poll(0.2)
                except Exception:  # noqa: BLE001 - a dead broker must not kill the ship
                    break
                if msg is None or msg.error():
                    continue
                try:
                    doc = json.loads(msg.value().decode("utf-8"))
                    if isinstance(doc, dict):
                        ctl.put(doc)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass

        threading.Thread(target=_consume, daemon=True).start()

    last_emit_cycle = -10**9
    try:
        while True:
            t0 = time.monotonic()
            for doc in _drain_controls():
                try:
                    validate_ship_control(doc)
                    ack = ship.apply_control(doc)
                except (ValueError, KeyError) as exc:
                    ack = {"ok": False, "error": str(exc)}
                print(f"ship: control -> {ack}", file=sys.stderr)

            if not ship.meco or not _coasted(ship):
                docs = ship.step()
                _publish(docs)
                log(ship.seq, docs[-1])
                last_emit_cycle = ship.seq

            if duration is not None and ship.t >= duration:
                break
            if ship.meco and _coasted(ship):
                if last_emit_cycle < ship.seq:  # ensure the final cycle went out
                    _publish([ship.status()])
                break
            time.sleep(max(0.0, ship.config.dt - (time.monotonic() - t0)))
    finally:
        cmd_pub.close()
        tel_pub.close()
        if control_consumer is not None:
            close = getattr(control_consumer, "close", None)
            if close:
                close()
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Starship ship sim (shared-fuel fleet loop)")
    p.add_argument("--vehicle-id", default="starship")
    p.add_argument("--engines", type=int, default=33)
    p.add_argument("--seed", type=int, default=1, help="run seed (re-draws the per-engine starting-condition spread)")
    p.add_argument("--vari", type=float, default=0.01, help="per-engine starting-condition spread fraction")
    p.add_argument("--failures-per-run", type=float, default=2.0,
                   help="EXPECTED fleet failures over a nominal full-tank launch (0 disables failures; "
                        "the ship calibrates a per-engine constant hazard rate from this)")
    p.add_argument("--fail-hazard", type=float, default=None,
                   help="per-engine failure hazard rate lambda (1/s); overrides --failures-per-run")
    p.add_argument("--lox-kg", type=float, default=5_480_000.0, help="shared LOX tank load, kg")
    p.add_argument("--ch4-kg", type=float, default=1_520_000.0, help="shared CH4 tank load, kg")
    p.add_argument("--throttle", type=float, default=1.0, help="commanded throttle 0..1.5")
    p.add_argument("--dt", type=float, default=0.01, help="integration step, s")
    p.add_argument("--ambient", type=float, default=101_325.0, help="ambient pressure, Pa")
    p.add_argument("--speed", type=float, default=1.0, help="stdio mode: wall-clock multiplier")
    p.add_argument("--emit-every", type=int, default=2, help="stdio mode: emit documents every N cycles")
    p.add_argument("--log-every", type=int, default=200, help="console status line every N cycles")

    g = p.add_argument_group("kafka mode")
    g.add_argument("--bootstrap", default=None, help="Kafka brokers (enables kafka mode)")
    g.add_argument("--command-topic", default="starship.engine.command")
    g.add_argument("--telemetry-topic", default="starship.engine.telemetry")
    g.add_argument("--control-topic", default="starship.ship.control")
    g.add_argument("--duration", type=float, default=None, help="kafka mode: stop after N sim-seconds")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def log(seq: int, status) -> None:
        if args.log_every > 0 and seq % args.log_every == 0:
            tank = status["tank"]
            print(
                f"ship t={status['t']:.1f}s thrust={status['thrust_n']/1e6:.2f}MN "
                f"tank={tank['frac']*100:.1f}% ({tank['total_kg']/1e6:.2f}Mt) "
                f"meco={'yes' if status['meco'] else 'no'}",
                file=sys.stderr,
            )

    ship = Ship(
        ShipConfig(
            vehicle_id=args.vehicle_id,
            n_engines=args.engines,
            seed=args.seed,
            vari_frac=args.vari,
            tank_ox_kg=args.lox_kg,
            tank_ch4_kg=args.ch4_kg,
            throttle_frac=args.throttle,
            ambient_pressure_pa=args.ambient,
            dt=args.dt,
            expected_failures_per_run=args.failures_per_run,
            fail_hazard_per_s=args.fail_hazard,
        )
    )
    if args.bootstrap:
        return run_kafka(
            ship,
            bootstrap=args.bootstrap,
            command_topic=args.command_topic,
            telemetry_topic=args.telemetry_topic,
            control_topic=args.control_topic,
            duration=args.duration,
            log=log,
        )
    return run_stdio(ship, speed=args.speed, emit_every=args.emit_every, log=log)


if __name__ == "__main__":
    raise SystemExit(main())
