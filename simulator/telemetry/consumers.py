"""Command sources: the vehicle to engine direction of the wire.

Mirrors ``publishers.py`` but for commands:

- ``InMemoryCommandSource``: thread-safe in-process FIFO the vehicle sim
  offers commands to. One per engine instance; that keying *is* the
  vehicle's per-engine addressing.
- ``KafkaCommandSource``: consumes the command topic with the engine id as
  the consumer group so each engine is its own single-reader stream.

An engine takes exactly one physics step per command, in order -- the
command stream is the clock, so there is no engine-side loop to drift.
"""

from __future__ import annotations

import json
import queue
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .messages import ENGINE_COMMAND, SCHEMA_VERSION

if TYPE_CHECKING:  # pragma: no cover
    from .messages import CommandMessage


@runtime_checkable
class CommandSource(Protocol):
    """One engine's command stream, ordered."""

    def poll(self, timeout: float | None) -> CommandMessage | None:
        """Block up to ``timeout`` seconds (None: forever) for one command."""
        ...

    def close(self) -> None: ...


class InMemoryCommandSource:
    """In-process command FIFO (vehicle offers, engine polls)."""

    def __init__(self, maxlen: int = 100_000) -> None:
        self._q: queue.Queue[CommandMessage] = queue.Queue()
        self.maxlen = maxlen

    def offer(self, cm: CommandMessage) -> None:
        self._q.put(cm)

    def poll(self, timeout: float | None) -> CommandMessage | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def pending(self) -> int:
        return self._q.qsize()

    def close(self) -> None:
        pass


class KafkaCommandSource:
    """Consume commands from a Kafka topic, one per engine.

    ``group_id`` defaults to the engine id: exactly one consumer group per
    engine stream, ordered, and a restarted engine rejoins its own stream
    without stealing another engine's commands.
    """

    def __init__(
        self,
        topic: str,
        engine_id: str,
        bootstrap_servers: str | None = None,
        group_id: str | None = None,
    ) -> None:
        try:
            from confluent_kafka import Consumer
        except ImportError as e:  # pragma: no cover - depends on env
            raise RuntimeError(
                "confluent-kafka is not installed. "
                "Install it with: uv add --group kafka confluent-kafka"
            ) from e

        cfg = {
            "bootstrap.servers": bootstrap_servers or "localhost:9092",
            "group.id": group_id or engine_id,
            "auto.offset.reset": "latest",
        }
        self.topic = topic
        self._consumer = Consumer(cfg)
        self._consumer.subscribe([topic])

    def poll(self, timeout: float | None) -> CommandMessage | None:
        msg = self._consumer.poll(timeout if timeout is not None else 1.0)
        if msg is None:
            return None
        if msg.error():
            raise RuntimeError(f"kafka poll error: {msg.error()}")
        cm = json.loads(msg.value().decode("utf-8"))
        if not isinstance(cm, dict) or cm.get("type") != ENGINE_COMMAND or cm.get("v") != SCHEMA_VERSION:
            raise ValueError(f"unusable command on {self.topic}: {cm!r}")
        return cm  # type: ignore[return-value]

    def close(self) -> None:
        self._consumer.close()
