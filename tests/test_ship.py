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
