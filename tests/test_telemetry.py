import json
import threading

import pytest

from simulator.engine import RaptorEngine
from simulator.telemetry import (
    STATE_CUT,
    STATE_FIRING,
    InMemoryPublisher,
    TelemetryEngine,
    aggregate_fleet,
)

N = 33
E = RaptorEngine(throttle_time_constant=0.05)


def make_fleet(n=N):
    """n engines fanning out to one shared publisher (like one Kafka topic)."""
    pub = InMemoryPublisher()
    fleet = [TelemetryEngine(f"sh-{i:02d}", E, pub, dt=0.01) for i in range(n)]
    return pub, fleet


def test_message_schema_fields_and_units():
    _, fleet = make_fleet(n=1)
    out = fleet[0].step(throttle=1.0)
    for _ in range(50):
        out = fleet[0].step(throttle=1.0)  # let the throttle lag settle

    expected = {
        "v", "type", "vehicle_id", "engine_id", "seq", "t", "ts_ms",
        "state", "throttle_demand_frac", "throttle_actual_frac",
        "chamber_pressure_pa", "thrust_n", "isp_s", "mdot_kg_s", "exhaust",
        "propellant_remaining_kg", "ambient_pressure_pa", "command_seq",
    }
    assert set(out) == expected
    assert out["type"] == "engine.telemetry"
    assert out["state"] == STATE_FIRING
    assert 0.9 < out["throttle_actual_frac"] <= 1.5
    assert out["thrust_n"] > 1e6
    assert set(out["mdot_kg_s"]) == {"total", "lox", "ch4"}
    assert set(out["exhaust"]) == {"velocity_m_s", "exit_pressure_pa", "exit_mach"}
    # the LoX/CH4 split reported must match the model's mixture ratio
    assert out["mdot_kg_s"]["lox"] / out["mdot_kg_s"]["ch4"] == pytest.approx(E.mixture_ratio)
    # must be JSON-serialisable - that is what goes on the wire
    json.dumps(out)


def test_one_message_per_step_and_seq_advances():
    pub, fleet = make_fleet(n=2)
    fleet[0].step(throttle=1.0)
    fleet[1].step(throttle=0.4)
    msgs = pub.drain()
    assert len(msgs) == 2
    by_id = {engine_id: m for engine_id, m in msgs}
    assert set(by_id) == {"sh-00", "sh-01"}
    for m in by_id.values():
        assert m["seq"] == 1
        assert m["engine_id"] in by_id
    # second step -> seq 2, sim time advanced by dt
    fleet[0].step(throttle=1.0)
    engine_id, m2 = pub.drain()[0]
    assert engine_id == "sh-00" and m2["seq"] == 2 and m2["t"] == 0.01


def test_fleet_aggregation_sums_thrust_and_counts():
    _, fleet = make_fleet(n=N)
    for _ in range(50):  # warm the fleet up so every engine is flowing
        msgs = [e.step(throttle=1.0) for e in fleet]
    fleet_row = aggregate_fleet(msgs)
    assert fleet_row["type"] == "fleet.telemetry"
    assert fleet_row["engine_count"] == N
    assert fleet_row["all_firing"] is True
    assert fleet_row["any_cut"] is False
    assert fleet_row["thrust_n"] == pytest.approx(sum(m["thrust_n"] for m in msgs))
    assert fleet_row["mdot_kg_s"]["total"] == pytest.approx(
        sum(m["mdot_kg_s"]["total"] for m in msgs))
    assert fleet_row["engine_ids"] == [f"sh-{i:02d}" for i in range(N)]


def test_fleet_aggregation_sees_engine_shutdown():
    # one engine with a finite load, run to depletion; the rest keep firing
    pub = InMemoryPublisher()
    loaded = TelemetryEngine("sh-cut",
                             RaptorEngine(propellant_mass=500.0,
                                          throttle_time_constant=0.01), pub)
    keepers = [TelemetryEngine(f"sh-{i:02d}", E, pub) for i in range(3)]
    load = 500.0
    # the model starves and leaves an eps sliver of the excess component at
    # cutoff (contract in tests/test_engine.py), so run until ~1% remains
    for _ in range(10_000):
        if loaded.propellant_remaining <= 0.01 * load:
            break
        loaded.step(throttle=1.0)
    assert loaded.propellant_remaining <= 0.01 * load
    for _ in range(10):
        [k.step(throttle=1.0) for k in keepers]

    msgs = [loaded.step(throttle=1.0)] + [k.step(throttle=1.0) for k in keepers]
    assert msgs[0]["state"] == STATE_CUT  # starved: no flow on the cut engine
    row = aggregate_fleet(msgs)
    assert row["any_cut"] is True
    assert row["all_firing"] is False
    assert row["cut_count"] == 1
    assert row["firing_count"] == 3


def test_fleet_aggregation_rejects_mixed_versions():
    _, fleet = make_fleet(n=1)
    m = fleet[0].step(throttle=1.0)
    m2 = dict(m, v=99)
    with pytest.raises(ValueError, match="mixed schema versions"):
        aggregate_fleet([m, m2])


def test_empty_aggregation_raises():
    with pytest.raises(ValueError):
        aggregate_fleet([])


def test_in_memory_publisher_is_thread_safe_and_orderable_per_key():
    pub = InMemoryPublisher()
    fleet = [TelemetryEngine(f"sh-{i:02d}", E, pub) for i in range(8)]

    def worker(eng):
        for _ in range(50):
            eng.step(throttle=1.0)

    t = threading.Thread(target=worker, args=(fleet[0],))
    t.start()
    worker(fleet[1])
    t.join()

    drained = pub.drain()
    assert len(drained) == 100
    per_engine: dict[str, list[int]] = {}
    for engine_id, m in drained:
        per_engine.setdefault(engine_id, []).append(m["seq"])
    for engine_id, seqs in per_engine.items():
        assert seqs == list(range(1, 51)), f"{engine_id} is out of order"


def test_ambient_pressure_is_used_per_step_and_default_sticks():
    _, fleet = make_fleet(n=1)
    fleet[0].step(throttle=1.0, ambient_pressure=101_325.0)
    for _ in range(50):
        sl = fleet[0].step(throttle=1.0)  # ambient falls back to 101 325
    vac = fleet[0].step(throttle=1.0, ambient_pressure=0.0)
    assert vac["thrust_n"] > sl["thrust_n"]  # back pressure costs thrust


def test_kafka_publisher_constructs_without_a_broker():
    pytest.importorskip("confluent_kafka", reason="only exercised with the kafka group")
    from simulator.telemetry import KafkaPublisher

    # constructing and handing records to the producer is async (buffered);
    # no broker must be required to publish. We do not close(): flush() would
    # block up to 30 s against a non-existent broker.
    pub = KafkaPublisher(topic="sh.engine.telemetry")
    assert pub.topic == "sh.engine.telemetry"
