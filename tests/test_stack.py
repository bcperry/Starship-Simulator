"""Full-stack tests: Super Heavy + Starship sequenced over one mission."""

from __future__ import annotations

import pytest

from simulator.ship import Ship, ShipConfig
from simulator.stack import PHASE_DONE, PHASE_LIFTOFF, PHASE_STAGING, PHASE_UPPER, Stack
from simulator.starship import Starship


def _sh(**kw) -> Ship:
    base = {
        "vehicle_id": "super-heavy", "n_engines": 6, "seed": 7, "vari_frac": 0.0,
        "tank_ox_kg": 40_000.0, "tank_ch4_kg": 12_000.0, "dt": 0.02,
        "expected_failures_per_run": 0.0, "noise_frac": 0.0,
    }
    base.update(kw)
    return Ship(ShipConfig(**base))


def _ss(**kw) -> Starship:
    base = {
        "vehicle_id": "starship", "n_engines": 6, "engine_prefix": "ss", "seed": 7,
        "vari_frac": 0.0, "tank_ox_kg": 10_000.0, "tank_ch4_kg": 3_000.0, "dt": 0.02,
        "expected_failures_per_run": 0.0, "noise_frac": 0.0,
    }
    base.update(kw)
    return Starship(ShipConfig(**base))


def _run_to_done(stack: Stack, steps: int = 20_000) -> Stack:
    for _ in range(steps):
        stack.step()
        if stack.phase == PHASE_DONE:
            break
    return stack


def test_full_stack_timeline_phases():
    """liftoff -> MECO -> staging coast -> upper burn -> SECO/done."""
    stack = Stack(_sh(), _ss())
    assert stack.phase == PHASE_LIFTOFF
    phases: list[str] = []
    for _ in range(20_000):
        stack.step()
        phases.append(stack.phase)
        if stack.phase == PHASE_DONE:
            for _ in range(60):  # let it coast a bit after SECO
                stack.step()
            break
    assert phases[0] == PHASE_LIFTOFF
    assert PHASE_STAGING in phases, "must coast between stages"
    assert PHASE_UPPER in phases, "Starship must light after staging"
    assert phases[-1] == PHASE_DONE
    # the upper burn begins only after the staging coast (which follows MECO)
    assert phases.index(PHASE_UPPER) > phases.index(PHASE_STAGING)


def test_stages_have_separate_tanks_and_sequence_ignition():
    """SH burns its own tank first; SS lights only after staging; no sharing."""
    stack = Stack(_sh(tank_ox_kg=20_000.0, tank_ch4_kg=6_000.0),
                  _ss(tank_ox_kg=10_000.0, tank_ch4_kg=3_000.0))
    ss_ox0 = stack.ss.config.tank_ox_kg
    sh_ox0 = stack.sh.config.tank_ox_kg

    # during liftoff SS tank is untouched
    for _ in range(30):
        stack.step()
    assert stack.ss.tank_ox_kg == ss_ox0, "Starship must not burn during first-stage ascent"

    _run_to_done(stack)
    # both tanks drained to ~0
    assert stack.sh.tank_ox_kg == pytest.approx(0.0, abs=1.0)
    assert stack.ss.tank_ox_kg == pytest.approx(0.0, abs=1.0)
    # SH lost its full load
    assert sh_ox0 - stack.sh.tank_ox_kg == pytest.approx(20_000.0, rel=0.02)


def test_stack_control_meco_cuts_active_stage():
    stack = Stack(_sh(), _ss())
    for _ in range(30):
        stack.step()
    # throttle (both vessels) works while burning
    ack = stack.apply_control({"type": "ship.control", "v": 1, "command": "throttle", "throttle_frac": 0.9, "vehicle_id": "stack"})
    assert ack["ok"] is True
    assert stack.sh.throttle_cmd == pytest.approx(0.9)
    assert stack.ss.throttle_cmd == pytest.approx(0.9)
    # we are in liftoff (SH firing); meco now
    ack = stack.apply_control({"type": "ship.control", "v": 1, "command": "meco", "vehicle_id": "stack"})
    assert ack["ok"] is True
    assert stack.sh.meco is True
    # Starship still cold and unlit
    assert stack.phase in (PHASE_LIFTOFF, PHASE_STAGING)
    _run_to_done(stack)


def test_stack_start_run_relights_both():
    stack = Stack(_sh(), _ss())
    _run_to_done(stack)
    assert stack.phase == PHASE_DONE
    stack.start_run(seed=12, expected_failures_sh=0.0, expected_failures_ss=0.0)
    assert stack.phase == PHASE_LIFTOFF
    assert stack.sh.meco is False
    assert stack.ss.meco is False
    st = stack.status()
    assert st["type"] == "stack.status"
    assert st["tank"]["frac"] == pytest.approx(1.0, rel=1e-3)


def test_stack_status_is_strict_json():
    import json

    stack = Stack(_sh(), _ss())
    _run_to_done(stack)
    for doc in stack.step():
        s = json.dumps(doc, allow_nan=False)
        json.loads(s)
    # after SECO everything finite
    for m in stack._members:
        assert m.state.chamber_pressure >= 0.0
        assert m.state.chamber_pressure == m.state.chamber_pressure  # not NaN


def test_stack_failure_expectations_are_per_stage():
    """Each stage runs its own failure model (mean ~ expected over an ensemble)."""
    N = 24
    sh_failures: list[int] = []
    ss_failures: list[int] = []
    for seed in range(1, N + 1):
        stack = Stack(
            _sh(seed=seed, expected_failures_per_run=2.0, tank_ox_kg=60_000.0, tank_ch4_kg=18_000.0),
            _ss(seed=seed, expected_failures_per_run=1.0),
        )
        _run_to_done(stack)
        sh_failures.append(sum(1 for m in stack.sh._members if m.failed))
        ss_failures.append(sum(1 for m in stack.ss._members if m.failed))
    # calibration targets E=2 (SH) and E=1 (SS): ensemble means track the target
    sh_mean = sum(sh_failures) / N
    ss_mean = sum(ss_failures) / N
    assert 1.2 <= sh_mean <= 2.8, f"SH mean failures {sh_mean} too far from expected 2"
    assert 0.5 <= ss_mean <= 1.5, f"SS mean failures {ss_mean} too far from expected 1"
    # and the stages fail independently (some SH-fail with SS-clean and vice versa)
    pairs = set(zip(sh_failures, ss_failures))
    assert any(a > 0 and b == 0 for a, b in pairs) or any(a == 0 and b > 0 for a, b in pairs)
