"""Telemetry: per-engine publish/consume pipeline.

- ``messages``: versioned JSON wire format (telemetry + command + ship docs, v1)
- ``publishers``: Publisher protocol, InMemoryPublisher, KafkaPublisher
- ``consumers``: CommandSource protocol, InMemoryCommandSource, KafkaCommandSource
- ``engine_feeder``: TelemetryEngine (direct stepping) and EngineProcess
  (one physics step per consumed command -- the per-engine process shape)
"""

from .consumers import (
    CommandSource,
    InMemoryCommandSource,
    KafkaCommandSource,
)
from .engine_feeder import EngineProcess, TelemetryEngine
from .messages import (
    ENGINE_COMMAND,
    ENGINE_TELEMETRY,
    SCHEMA_VERSION,
    SHIP_CONTROL,
    SHIP_STATUS,
    STATE_CUT,
    STATE_FIRING,
    STATE_IDLE,
    CommandMessage,
    ShipControlMessage,
    ShipStatusMessage,
    TelemetryMessage,
    aggregate_fleet,
    engine_state,
    to_command,
    to_ship_control,
    to_ship_status,
    to_telemetry,
    validate_command,
    validate_ship_control,
)
from .publishers import InMemoryPublisher, KafkaPublisher, Publisher

__all__ = [
    "ENGINE_COMMAND",
    "ENGINE_TELEMETRY",
    "SCHEMA_VERSION",
    "SHIP_CONTROL",
    "SHIP_STATUS",
    "STATE_CUT",
    "STATE_FIRING",
    "STATE_IDLE",
    "CommandMessage",
    "CommandSource",
    "EngineProcess",
    "InMemoryCommandSource",
    "InMemoryPublisher",
    "KafkaCommandSource",
    "KafkaPublisher",
    "Publisher",
    "ShipControlMessage",
    "ShipStatusMessage",
    "TelemetryEngine",
    "TelemetryMessage",
    "aggregate_fleet",
    "engine_state",
    "to_command",
    "to_ship_control",
    "to_ship_status",
    "to_telemetry",
    "validate_command",
    "validate_ship_control",
]
