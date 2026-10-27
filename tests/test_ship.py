"""Ship sim: shared tank, fleet variability, run lifecycle, per-cycle feed.

The contract under test: a vehicle of N Raptor engines drawing from ONE shared
LOX + CH4 tank, with per-engine +/-vari starting-condition spread (seeded,
reproducible) and a run lifecycle (start_new_run re-draws the spread and
resets states + tank). The ship is the fuel authority: it feeds the engines
per cycle and commands MECO when the shared tank can no longer feed a step.
"""

from __future__ import annotations

import pytest

from simulator.engine import RaptorEngine
from simulator.ship import Ship, ShipConfig, ship_control
from simulator.telemetry import (
    ENGINE_COMMAND,
    ENGINE_TELEMETRY,
    SHIP_STATUS,
    aggregate_fleet,
    to_ship_control,
    validate_ship_control,
)

BASE = RaptorEngine(throttle_time_constant=0.05)


def _cfg(**kw) -> ShipConfig:
    base = {
        "vehicle_id": "sh-test",
        "n_engines": 4,
        "seed": 7,
        "vari_frac": 0.01,
        "tank_ox_kg": 40_000.0,
        "tank_ch4_kg": 12_000.0,
        "throttle_frac": 1.0,
        "ambient_pressure_pa": 101_325.0,
        "dt": 0.02,
        # failure model off by default in these tests (they isolate the tank
        # feed mechanics); the failure-model tests below opt in explicitly
        "expected_failures_per_run": 0.0,
    }
    base.update(kw)
    return ShipConfig(**base)


# --- fleet variability (starting-condition spread) ---


def test_fleet_spread_is_in_band_and_per_engine():
    cfg = _cfg(n_engines=8, vari_frac=0.01)
    ship = Ship(cfg)
    base = RaptorEngine(throttle_time_constant=0.05)
    assert len(ship._members) == 8

    for i, member in enumerate(ship._members):
        e = member.engine
        for field in ("chamber_pressure", "throat_area", "mixture_ratio", "cstar_efficiency"):
            base_val = getattr(base, field)
            ratio = getattr(e, field) / base_val
            # +/-1% band (cstar capped at 1.5, which is far above band)
            assert 0.99 <= ratio <= 1.01, (field, i, ratio)
        # engines are NOT all identical: at least one param differs per engine
        differs = any(
            getattr(e, f) != getattr(base, f)
            for f in ("chamber_pressure", "throat_area", "mixture_ratio", "cstar_efficiency")
        )
        assert differs, f"engine {i} is identical to base"
    # engines differ from each other
    cstars = [m.engine.cstar_efficiency for m in ship._members]
    assert len(set(cstars)) > 1


def test_fleet_is_deterministic_per_seed_and_changes_with_seed():
    a = Ship(_cfg(seed=101))
    b = Ship(_cfg(seed=101))
    c = Ship(_cfg(seed=102))
    pa = [m.engine.throat_area for m in a._members]
    pb = [m.engine.throat_area for m in b._members]
    pc = [m.engine.throat_area for m in c._members]
    assert pa == pb
    assert any(x != y for x, y in zip(pc, pa))


def test_vari_frac_zero_gives_nominal_fleet():
    ship = Ship(_cfg(vari_frac=0.0))
    base = RaptorEngine(throttle_time_constant=0.05)
    for m in ship._members:
        for f in ("chamber_pressure", "throat_area", "mixture_ratio", "cstar_efficiency"):
            assert getattr(m.engine, f) == getattr(base, f)


def test_vari_frac_rejects_out_of_range():
    with pytest.raises(ValueError):
        Ship(_cfg(vari_frac=0.9))


# --- shared tank feed + depletion ---


def test_shared_tank_depletes_and_meco():
    cfg = _cfg(tank_ox_kg=30_000.0, tank_ch4_kg=9_000.0)
    ship = Ship(cfg)
    steps = ship.run(max_steps=20000, coast_down=False)
    assert steps >= 30  # burned for a while before MECO

    # the final publishable cycle: after MECO all engines are "cut"
    last = ship.step()
    tels = [d for d in last if d["type"] == ENGINE_TELEMETRY]
    assert all(t["state"] == "cut" for t in tels)

    status = last[-1]
    assert status["type"] == SHIP_STATUS
    assert status["meco"] is True
    assert status["meco_t"] is not None
    assert status["tank"]["lox_kg"] >= 0.0
    assert status["tank"]["ch4_kg"] >= 0.0
    assert status["cut_count"] == cfg.n_engines
    assert status["firing_count"] == 0


def test_tank_consumption_is_shared_across_engines():
    # load the shared tank at the fleet's mix ratio (ox:ch4 = 3.6:1) so BOTH
    # components starve close together, then the ship must run the shared tank
    # to ~0 using each engine's real burned mdot -- not per-engine loads.
    ch4_0 = 6_000.0
    cfg = _cfg(tank_ox_kg=3.6 * ch4_0, tank_ch4_kg=ch4_0)
    ship = Ship(cfg)
    burned_ox = burned_ch4 = 0.0
    while not ship.meco and ship.seq < 100000:
        docs = ship.step()
        for t in docs:
            if t["type"] == ENGINE_TELEMETRY and t["state"] == "firing":
                burned_ox += t["mdot_kg_s"]["lox"] * cfg.dt
                burned_ch4 += t["mdot_kg_s"]["ch4"] * cfg.dt
    # both components drained to a small residual (shared tank, no per-engine loads)
    assert ship.tank_ox_kg < 0.10 * cfg.tank_ox_kg
    assert ship.tank_ch4_kg < 0.10 * ch4_0
    # conservation-ish: total burned is the same order as the initial shared load
    initial = cfg.tank_ox_kg + cfg.tank_ch4_kg
    burned = burned_ox + burned_ch4
    assert 0.5 * initial < burned < 1.2 * initial


def test_unbalanced_tank_starves_on_limiting_component():
    # tank richer in CH4 than the fleet's mix ratio -> LOX starves first,
    # leaving unspent CH4 (MECO on the limiting component, not both).
    cfg = _cfg(tank_ox_kg=20_000.0, tank_ch4_kg=6_000.0)  # 3.33:1 < 3.6:1
    ship = Ship(cfg)
    ship.run(max_steps=100000, coast_down=False)
    assert ship.meco
    assert ship.tank_ox_kg < 0.01 * cfg.tank_ox_kg       # limiting component gone
    assert ship.tank_ch4_kg > 100.0                       # excess component left over


def test_meco_stops_consumption():
    cfg = _cfg(tank_ox_kg=20_000.0, tank_ch4_kg=6_000.0)
    ship = Ship(cfg)
    ship.run(max_steps=100000, coast_down=False)
    assert ship.meco
    ox_before = ship.tank_ox_kg
    ch4_before = ship.tank_ch4_kg
    for _ in range(50):
        ship.step()
    # no more shared-tank drawdown after MECO
    assert ship.tank_ox_kg == ox_before
    assert ship.tank_ch4_kg == ch4_before


# --- run lifecycle ---


def test_start_new_run_resets_and_reseeds():
    cfg = _cfg(seed=101)
    ship = Ship(cfg)
    ship.run(max_steps=50, coast_down=False)
    run1 = ship.run_id

    new_id = ship.start_run(seed=202, throttle_frac=0.8)
    assert new_id != run1
    assert ship.t == 0.0
    assert ship.seq == 0
    assert ship.meco is False
    assert ship.tank_ox_kg == cfg.tank_ox_kg
    assert ship.seed == 202

    run1_fleet = [m.engine.throat_area for m in Ship(_cfg(seed=101))._members]
    new_fleet = [m.engine.throat_area for m in ship._members]
    ref = [m.engine.throat_area for m in Ship(_cfg(seed=202))._members]
    assert new_fleet == ref
    assert any(a != b for a, b in zip(new_fleet, run1_fleet))


def test_start_run_rejects_bad_args():
    ship = Ship(_cfg())
    with pytest.raises(ValueError):
        ship.start_run(n_engines=0)
    with pytest.raises(ValueError):
        ship.start_run(tank_ox_kg=-1.0)


# --- control verbs ---


def test_control_throttle_and_meco():
    ship = Ship(_cfg(seed=5))
    # spool up a bit
    for _ in range(100):
        ship.step()
    ok = ship.apply_control(ship_control("sh-test", "throttle", throttle_frac=0.0))
    assert ok["ok"] is True
    # after meco, throttle is refused
    ship.apply_control(ship_control("sh-test", "meco"))
    assert ship.meco
    refused = ship.apply_control(ship_control("sh-test", "throttle", throttle_frac=0.5))
    assert refused["ok"] is False
    assert refused["reason"] == "meco"


def test_validate_ship_control_rejects_bad():
    with pytest.raises(ValueError):
        validate_ship_control(to_ship_control("bogus", vehicle_id="x"))
    with pytest.raises(ValueError):
        validate_ship_control(to_ship_control("throttle", vehicle_id="x", throttle_frac=9.0))
    with pytest.raises(ValueError):
        validate_ship_control(to_ship_control("start_run", vehicle_id="x", n_engines=0))
    # good round-trips
    validate_ship_control(to_ship_control("meco", vehicle_id="x"))


def test_step_documents_shape():
    ship = Ship(_cfg(n_engines=4))
    docs = ship.step()
    cmds = [d for d in docs if d["type"] == ENGINE_COMMAND]
    tels = [d for d in docs if d["type"] == ENGINE_TELEMETRY]
    status = docs[-1]
    assert len(cmds) == 4
    assert len(tels) == 4
    assert status["type"] == SHIP_STATUS
    # each engine got a command with that engine's id
    ids = {c["engine_id"] for c in cmds}
    assert ids == {f"sh-{i+1:02d}" for i in range(4)}
    # the ship is a single fuel authority; fleet aggregate still folds cleanly
    agg = aggregate_fleet(tels)
    assert agg["engine_count"] == 4


# --- failure model: constant-hazard (exponential) time-to-failure ---


def _run_to_meco(ship: Ship) -> Ship:
    """Step the ship until MECO plus a short coast-down; return the ship."""
    while not (ship.meco and ship.t >= (ship.meco_t or 0) + 1.0):
        ship.step()
    return ship


def test_failure_calibration_math():
    """expected_failures_per_run -> lambda = -ln(1 - E/N) / T_nominal."""
    import math

    for expected in (0.0, 1.0, 2.0, 3.5):
        ship = Ship(_cfg(expected_failures_per_run=expected))
        t_nom = ship._nominal_burn_s(52_000.0)
        if expected <= 0.0:
            assert ship._fail_lambda == 0.0
            continue
        p = min(expected / 4, 0.999)
        expected_lambda = -math.log(1.0 - p) / t_nom
        assert ship._fail_lambda == pytest.approx(expected_lambda, rel=1e-12)
    # explicit hazard is used verbatim (raw distribution parameter input)
    assert Ship(_cfg(fail_hazard_per_s=0.03))._fail_lambda == pytest.approx(0.03)
    with pytest.raises(ValueError):
        _cfg(expected_failures_per_run=-1.0)
    with pytest.raises(ValueError):
        _cfg(fail_hazard_per_s=-0.01)


def test_failures_deterministic_per_seed():
    a = Ship(_cfg(seed=11, expected_failures_per_run=2.0))
    _run_to_meco(a)
    b = Ship(_cfg(seed=11, expected_failures_per_run=2.0))
    _run_to_meco(b)
    fa = [(m.engine_id, m.fail_time) for m in a._members if m.failed]
    fb = [(m.engine_id, m.fail_time) for m in b._members if m.failed]
    assert fa and fa == fb
    assert a.status()["tank"]["frac"] == pytest.approx(b.status()["tank"]["frac"], abs=1e-12)


def test_failed_engines_stop_burning_and_tank_accounting_holds():
    """Failed engines: zero thrust/mdot on the wire; while the ship is feeding,
    the shared tank drains by EXACTLY what the live engines report burning
    (capped at what remains)."""
    ship = Ship(_cfg(seed=7, expected_failures_per_run=2.0))  # deterministically loses sh-04 early
    saw_failure = False
    while not (ship.meco and ship.t >= (ship.meco_t or 0) + 1.0):
        meco_before = ship.meco
        ox0, ch40 = ship.tank_ox_kg, ship.tank_ch4_kg
        docs = ship.step()
        tels = [d for d in docs if d["type"] == ENGINE_TELEMETRY]
        rep_ox = sum(t["mdot_kg_s"]["lox"] for t in tels) * ship.config.dt
        rep_ch4 = sum(t["mdot_kg_s"]["ch4"] for t in tels) * ship.config.dt
        for tel in tels:
            if tel["state"] == "failed":
                saw_failure = True
                assert tel["thrust_n"] == 0.0
                assert tel["mdot_kg_s"]["total"] == 0.0
        if not meco_before:
            # active feeding step: drain == reported burn, capped at available
            assert ox0 - ship.tank_ox_kg == pytest.approx(min(ox0, rep_ox), abs=1e-9)
            assert ch40 - ship.tank_ch4_kg == pytest.approx(min(ch40, rep_ch4), abs=1e-9)
    assert saw_failure, "expected a failure on this seeded run, none happened"
    st = ship.status()
    assert st["failed_count"] >= 1
    assert len(st["failed_engines"]) == st["failed_count"]
    assert st["firing_count"] + st["cut_count"] + st["failed_count"] == st["n_engines"]


def test_failure_docs_are_strict_json():
    """Every published document must serialise to STRICT JSON.

    Regression: a failed engine once carried propellant_remaining=inf, and
    Python's json emits the bare token ``Infinity`` -- invalid JSON that
    downstream (Node) parsers reject, silently dropping the whole wire line.
    """
    import json

    found = False
    for seed in range(1, 11):
        ship = Ship(_cfg(seed=seed, fail_hazard_per_s=5.0, n_engines=4))
        while not (ship.meco and ship.t >= (ship.meco_t or 0) + 1.0):
            for doc in ship.step():
                strict = json.dumps(doc, allow_nan=False)  # raises on inf/nan
                json.loads(strict)
        if any(m.failed for m in ship._members):
            found = True
            break
    assert found, "expected at least one seeded run with a failure"


def test_zero_expected_failures_gives_failure_free_run():
    ship = _run_to_meco(Ship(_cfg(seed=11, expected_failures_per_run=0.0)))
    assert all(not m.failed for m in ship._members)


def test_all_engines_fail_meco_and_zero_thrust():
    ship = Ship(_cfg(seed=3, fail_hazard_per_s=0.5))
    while not (ship.meco and ship.t >= (ship.meco_t or 0) + 1.0):
        ship.step()
    assert all(m.failed for m in ship._members)
    docs = [d for d in ship.step() if d["type"] == ENGINE_TELEMETRY]
    assert all(d["state"] == "failed" and d["thrust_n"] == 0.0 for d in docs)
    st = ship.status()
    assert st["failed_count"] == st["n_engines"]
    assert st["thrust_n"] == 0.0


def test_fleet_failure_count_follows_model():
    """With expected_failures_per_run=2 over 200 launches of the 4-engine
    test ship: the fleet failure count sits near 2, 1-3 failures dominate
    ('1-3 with standard params'), and some launches still fly clean."""
    counts = []
    for seed in range(1, 201):
        ship = _run_to_meco(Ship(_cfg(seed=seed, expected_failures_per_run=2.0)))
        counts.append(sum(1 for m in ship._members if m.failed))
    dist = {k: counts.count(k) for k in sorted(set(counts))}
    mean = sum(counts) / len(counts)
    assert 1.8 <= mean <= 3.2
    assert sum(v for k, v in dist.items() if k in (1, 2, 3)) > 0.6 * len(counts)
    assert dist.get(0, 0) > 0
    assert max(counts) <= 4


def test_failure_can_happen_at_ignition_or_midburn():
    """Failures must be able to land at engine start AND late in the burn
    (real history: failed ignitions pre-T-0 AND T+1m16s cut-offs)."""
    early = late = False
    for seed in range(1, 21):
        ship = _run_to_meco(Ship(_cfg(seed=seed, expected_failures_per_run=2.0)))
        ttf = [m.fail_time for m in ship._members if m.failed]
        if not ttf:
            continue
        if min(ttf) < 2.0:
            early = True
        if max(ttf) > 15.0:
            late = True
        if early and late:
            break
    assert early and late


def test_meco_stops_failure_rolls():
    """After MECO there is no burn and therefore no new failures can appear."""
    ship = Ship(_cfg(seed=11, expected_failures_per_run=2.0))
    ids_before = [m.engine_id for m in ship._members if m.failed]
    ship.apply_control(ship_control("sh-test", "meco"))
    for _ in range(500):
        ship.step()
    ids_after = [m.engine_id for m in ship._members if m.failed]
    assert ids_after == ids_before
