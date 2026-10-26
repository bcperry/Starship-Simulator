"""Telemetry transports.

The engine sim talks to a uniform ``Publisher`` interface; the transport is
pluggable. The two provided:

- ``InMemoryPublisher``: thread-safe in-process queue. The vehicle sim can
  drain it directly — no broker needed for in-process integration or tests.
- ``KafkaPublisher``: JSON document on a shared topic, ``engine_id`` as the
  Kafka message key. One topic per vehicle (e.g. ``sh.engine.telemetry``),
  not one topic per engine: per-engine ordering is guaranteed by the key,
  consumers scale across partitions, and topic sprawl stays bounded.

Install the optional kafka dependency with:

    uv add --group kafka confluent-kafka
"""

from __future__ import annotations

import json
import queue
from collections import deque
from typing import Protocol, runtime_checkable

from .messages import TelemetryMessage


@runtime_checkable
class Publisher(Protocol):
    """Transport for one engine's telemetry stream.

    ``publish`` must not block the physics loop: buffer and hand off.
    """

    def publish(self, engine_id: str, message: TelemetryMessage) -> None: ...

    def close(self) -> None: ...


class InMemoryPublisher:
    """Thread-safe in-process sink; doubles as the vehicle sim's ingest port.

    ``drain()`` (non-blocking) or ``poll(timeout)`` (blocking) are the
    consumer side. Also keeps a bounded ``received`` ring for inspection.
    """

    def __init__(self, maxlen: int = 100_000) -> None:
        self._q: queue.Queue[tuple[str, TelemetryMessage]] = queue.Queue()
        self.received: deque[tuple[str, TelemetryMessage]] = deque(maxlen=maxlen)

    def publish(self, engine_id: str, message: TelemetryMessage) -> None:
        self._q.put((engine_id, message))
        self.received.append((engine_id, message))

    def drain(self) -> list[tuple[str, TelemetryMessage]]:
        """All currently queued (engine_id, message) pairs, oldest first."""
        out: list[tuple[str, TelemetryMessage]] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out

    def poll(self, timeout: float | None) -> tuple[str, TelemetryMessage] | None:
        """Block up to ``timeout`` seconds for one message (None: forever)."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def pending(self) -> int:
        return self._q.qsize()

    def close(self) -> None:
        pass


class KafkaPublisher:
    """Publish telemetry as JSON Kafka records, keyed by engine_id.

    Defaults target a local broker (``localhost:9092``). Pass ``config``
    through to the confluent-kafka ``Producer`` (auth, acks, compression,
    ``socket.timeout.ms``, ...).

    ``publish`` is async in the broker's sense: records are handed to the
    producer's buffer, flush/close block for durability.
    """

    def __init__(
        self,
        topic: str,
        bootstrap_servers: str | None = None,
        config: dict | None = None,
    ) -> None:
        try:
            from confluent_kafka import Producer
        except ImportError as e:  # pragma: no cover - depends on env
            raise RuntimeError(
                "confluent-kafka is not installed. "
                "Install it with: uv add --group kafka confluent-kafka"
            ) from e

        cfg = {"bootstrap.servers": bootstrap_servers or "localhost:9092"}
        if config:
            cfg.update(config)
        self.topic = topic
        self._producer = Producer(cfg)
        self._errors: list[str] = []

    def publish(self, engine_id: str, message: TelemetryMessage) -> None:
        value = json.dumps(message).encode("utf-8")
        self._producer.produce(
            self.topic,
            key=engine_id.encode("utf-8"),
            value=value,
            headers=[("Content-Type", b"application/json"),
                     ("message-type", message["type"].encode("utf-8")),
                     ("schema-version", str(message["v"]).encode("utf-8"))],
            on_delivery=self._on_delivery,
        )
        # surface delivery errors to the caller instead of a thread callback
        produced = self._producer.poll(0.0)
        if produced is not None and produced.error():
            self._errors.append(str(produced.error()))
        if self._errors:
            raise RuntimeError(f"kafka publish error: {self._errors[-1]}")

    def _on_delivery(self, err, msg) -> None:  # pragma: no cover - broker side
        if err is not None:
            self._errors.append(str(err))

    def flush(self, timeout: float = 30.0) -> None:
        remaining = self._producer.flush(timeout)
        if remaining:
            raise RuntimeError(f"kafka flush timeout: {remaining} record(s) outstanding")

    def close(self, timeout: float = 30.0) -> None:
        """Flush outstanding records, bounded by ``timeout`` (dead broker can't hold shutdown)."""
        self._producer.flush(timeout)
