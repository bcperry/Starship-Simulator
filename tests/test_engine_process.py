"""EngineProcess: command-driven engine stepping (vehicle -> engine wire)."""

import time

import pytest

from simulator.engine import RaptorEngine
from simulator.telemetry import (
    STATE_CUT,
    STATE_FIRING,
    EngineProcess,
    InMemoryCommandSource,
    InMemoryPublisher,
    aggregate_fleet,
    to_command,
    validate_command,
)

E = RaptorEngine(throttle_time_constant=0.05)
AMBIENT_SEA = 101_325.0


def make_process(engine_id="sh-01", engine=E):
    pub = InMemoryPublisher()
    src = InMemoryCommandSource()
    proc = EngineProcess(engine_id, engine, src, pub, vehicle_id="starship")
    return pub, src, proc


def test_step_on_command_emits_telemetry_echoing_command_seq():
    pub, _, proc = make_process()
    cmd = to_command(engine_id="sh-01", seq=1, throttle_demand_frac=1.0,
                     ambient_pressure_pa=AMBIENT_SEA, dt=0.01, t=0.0)
    msg = proc.step_on_command(cmd)
    assert msg["command_seq"] == 1
    assert msg["seq"] == 1
    assert msg["throttle_demand_frac"] == pytest.approx(1.0)
    assert msg["ambient_pressure_pa"] == pytest.approx(AMBIENT_SEA)
    engine_id, m = pub.drain()[0]
    assert engine_id == "sh-01" and m["command_seq"] == 1


def test_command_is_the_clock_dt_throttle_ambient_honored():
    _, _, proc = make_process()
    c1 = to_command(engine_id="sh-01", seq=1, throttle_demand_frac=0.2,
                    ambient_pressure_pa=0.0, dt=0.02, t=0.0)
    m1 = proc.step_on_command(c1)
    assert m1["t"] == pytest.approx(0.0)      # step applies at sim t=0
    assert m1["ambient_pressure_pa"] == pytest.approx(0.0)
    c2 = to_command(engine_id="sh-01", seq=2, throttle_demand_frac=1.0,
                    ambient_pressure_pa=AMBIENT_SEA, dt=0.02, t=0.02)
    m2 = proc.step_on_command(c2)
    assert m2["t"] == pytest.approx(0.02)     # advanced by the command's dt
    assert m2["command_seq"] == 2
    assert m2["ambient_pressure_pa"] == pytest.approx(AMBIENT_SEA)
    assert proc.t == pytest.approx(0.04)
    assert proc.processed == 2


def test_misaddressed_command_rejected_loudly():
    _, _, proc = make_process(engine_id="sh-01")
    cmd = to_command(engine_id="sh-02", seq=1, throttle_demand_frac=1.0,
                     ambient_pressure_pa=AMBIENT_SEA)
    with pytest.raises(ValueError, match="but engine is 'sh-01'"):
        proc.step_on_command(cmd)
    # nothing was stepped or published for it
    assert proc.processed == 0


def test_malformed_commands_rejected():
    good = to_command(engine_id="sh-01", seq=1, throttle_demand_frac=1.0,
                      ambient_pressure_pa=AMBIENT_SEA)
    cases = [
        "not a mapping",                                  # wire garbage
        dict(good, type="fleet.telemetry"),               # wrong message type
        dict(good, v=99),                                  # foreign schema
        dict(good, engine_id=""),                          # bad target
        dict(good, dt=0.0),                                # zero step
        dict(good, throttle_demand_frac=2.0),             # out of range
        dict(good, ambient_pressure_pa=-1.0),             # nonsense ambient
    ]
    for cm in cases:
        with pytest.raises(ValueError):
            validate_command(cm, expected_engine="sh-01")
    # and the good one passes
    assert validate_command(good, expected_engine="sh-01") is good


def test_run_processes_exactly_max_steps_then_stops():
    pub, src, proc = make_process()
    for i in range(1, 6):
        src.offer(to_command(engine_id="sh-01", seq=i, throttle_demand_frac=1.0,
                             ambient_pressure_pa=AMBIENT_SEA, dt=0.01, t=(i - 1) * 0.01))
    done = proc.run(max_steps=5, idle_timeout=5.0, poll_grain=0.005)
    assert done == proc.processed == 5
    assert len(pub.received) == 5


def test_run_idle_timeout_returns_promptly():
    _, _, proc = make_process()
    t0 = time.monotonic()
    done = proc.run(max_steps=0, idle_timeout=0.1, poll_grain=0.02)
    elapsed = time.monotonic() - t0
    assert done == 0
    # bounded by (a few polls ~) the idle timeout, not by a wall; keep the
    # ceiling loose because CI/WSL schedulers can stretch the poll sleeps
    assert elapsed < 10.0


def test_run_skips_unusable_commands_and_continues():
    _, src, proc = make_process()
    logs = []
    src.offer(to_command(engine_id="sh-99", seq=1, throttle_demand_frac=1.0,  # cross-wired
                         ambient_pressure_pa=AMBIENT_SEA))
    src.offer(to_command(engine_id="sh-01", seq=2, throttle_demand_frac=1.0,
                         ambient_pressure_pa=AMBIENT_SEA))
    done = proc.run(max_steps=10, idle_timeout=0.2, poll_grain=0.02, log=logs.append)
    assert done == 1
    assert len(logs) == 1 and "sh-99" in logs[0] and "sh-01" in logs[0]


def test_close_stops_the_loop():
    _, src, proc = make_process()
    src.offer(to_command(engine_id="sh-01", seq=1, throttle_demand_frac=1.0,
                         ambient_pressure_pa=AMBIENT_SEA))
    proc.close()
    assert proc.run(max_steps=10, idle_timeout=0.1, poll_grain=0.02) == 0
    assert src.pending == 1  # closed, not drained


def test_command_driven_33_engine_fleet_with_one_cutoff():
    """33 parallel EngineProcesses; one carries a finite load and cuts off."""

    engines = {f"sh-{i:02d}": (E if i < 32
                               else RaptorEngine(propellant_mass=500.0,
                                                 throttle_time_constant=0.01))
               for i in range(33)}
    pubs = [InMemoryPublisher() for _ in range(33)]
    fleet = []
    for i, eid in enumerate(engines):
        src = InMemoryCommandSource()
        fleet.append((eid, src, EngineProcess(eid, engines[eid], src, pubs[i])))

    def vehicle_step(n: int):
        """Vehicle side: one command per engine per timestep, then aggregate."""
        msgs = []
        for eid, src, proc in fleet:
            cmd = to_command(engine_id=eid, seq=n, throttle_demand_frac=1.0,
                             ambient_pressure_pa=AMBIENT_SEA, dt=0.01, t=(n - 1) * 0.01)
            src.offer(cmd)
            msgs.append(proc.step_on_command(src.poll(0.0)))
        return msgs

    cut_id = "sh-32"
    for n in range(1, 1):  # initial fire on the full fleet
        vehicle_step(n)
    for n in range(2, 10_001):
        if fleet[32][2].propellant_remaining <= 0.01 * 500.0:
            break
        vehicle_step(n)

    msgs = []
    for eid, src, proc in fleet:  # the cutoff step itself
        cmd = to_command(engine_id=eid, seq=n, throttle_demand_frac=1.0,
                         ambient_pressure_pa=AMBIENT_SEA, dt=0.01, t=(n - 1) * 0.01)
        msgs.append(proc.step_on_command(cmd))
    cut_msg = next(m for m in msgs if m["engine_id"] == cut_id)
    assert cut_msg["state"] == STATE_CUT
    assert sum(m["state"] == STATE_FIRING for m in msgs) == 32

    row = aggregate_fleet(msgs)
    assert row["any_cut"] is True
    assert row["all_firing"] is False
    assert row["firing_count"] == 32
    assert row["cut_count"] == 1
    assert row["engine_count"] == 33
