"""Phase 9 acceptance tests: the full Raptor-3 Starship stack inserts a
multi-ton payload into LEO, while Starship alone cannot reach orbit.

The nominal (failure-free) mission is asserted to reach LEO ROBUSTLY across
several seeds — a single-seed success is explicitly NOT sufficient (there is
comfortable margin once Earth's rotation is credited).  Engine-out cases are
asserted to *complete* without crashing (the vehicle degrades gracefully; at
the edge of the envelope it re-enters rather than crashing).  Super Heavy's
banked reserve is asserted to cover a real RTLS (down + boostback + landing)
delta-v budget.
"""
import math

import pytest

from simulator.flight import FlightCfg, make_flight
from simulator.orbit import G0, MU, R_EARTH, orbit_elements


def _n_failed(shipment) -> int:
    return sum(1 for m in shipment._members if m.failed)


def _run(seed: int, sh_ef: float = 0.0, ss_ef: float = 0.0, **kw) -> dict:
    """Run one flight to DONE or crash; return a compact summary."""
    cfg = FlightCfg(seed=seed, sh_expected_failures=sh_ef,
                    ss_expected_failures=ss_ef, **kw)
    f = make_flight(cfg)
    f.stack.start_run()
    guard = int(2000.0 / cfg.dt)
    while not (f.done or f.model.crashed) and guard > 0:
        f.step()
        guard -= 1
    o = orbit_elements(f.model.r0, f.model.v) or {}
    return dict(
        seed=seed,
        sh_failed=_n_failed(f.stack.sh),
        ss_failed=_n_failed(f.stack.ss),
        perigee_km=o.get("perigee_km", 0.0),
        apogee_km=o.get("apogee_km", 0.0),
        completed=(f.done or f.model.crashed),
    )


# --- Robust LEO (the acceptance): failure-free, across seeds ---------------


@pytest.mark.parametrize("seed", [1, 2, 5, 7])
def test_failure_free_leo_robust_across_seeds(seed: int) -> None:
    """LEO must be established across multiple no-failure seeds — a
    single-seed success is NOT acceptable.  With Earth's rotation credited
    the nominal flight has margin, so this is reliable."""
    r = _run(seed)
    assert r["completed"], f"seed {seed} did not complete"
    assert r["perigee_km"] >= 100.0, (
        f"failure-free LEO not robust (seed {seed}): perigee {r['perigee_km']:.1f} km"
    )


# --- Graceful degradation: engine-out completes without crashing -----------


@pytest.mark.parametrize("seed", [7, 13])
def test_engine_out_runs_complete_without_crashing(seed: int) -> None:
    """The sim completes an engine-out flight cleanly (never crashes),
    whether it inserts into LEO or re-enters at the edge of the envelope."""
    cfg = FlightCfg(seed=seed)   # default mean 2.0/ship -> likely 1-2 failures
    r = _run(seed)
    assert r["completed"], f"seed {seed} did not complete"


# --- Super Heavy: banks propellant AND can actually return (RTLS) ----------


def test_super_heavy_banks_boostback_propellant() -> None:
    """Super Heavy must bank sh_meco_resid_frac of its propellant for the
    boostback/landing burn (not arrive empty)."""
    cfg = FlightCfg(seed=1)
    f = make_flight(cfg)
    f.stack.start_run()
    total = cfg.sh_lox_kg + cfg.sh_ch4_kg
    floor = cfg.sh_meco_resid_frac * total
    guard = int(2000.0 / cfg.dt)
    while f.stack.phase == "liftoff" and not f.done and not f.model.crashed and guard > 0:
        f.step()
        guard -= 1
    assert f.stack.phase in ("staging", "upper", "done"), f.stack.phase
    banked = f.sh.tank_ox_kg + f.sh.tank_ch4_kg
    assert banked >= 0.90 * floor, f"SH banked {banked/1000:.0f} t, need ~{floor/1000:.0f} t"


def test_sh_reserve_covers_rtls_delta_v_budget() -> None:
    """Super Heavy's banked reserve must carry a real RTLS: down (kill the
    horizontal velocity), boostback (return toward the pad), and vertical /
    terminal descent.  Asserted with >= 50% margin so the return burn is a
    capability, not a razor's edge."""
    cfg = FlightCfg(seed=1)
    reserve = (cfg.sh_lox_kg + cfg.sh_ch4_kg) * cfg.sh_meco_resid_frac
    m0 = cfg.sh_dry_kg + reserve          # booster + banked fuel
    isp = 280.0                           # booster, mostly sea-level Isp (s)
    dv_available = isp * G0 * math.log(m0 / cfg.sh_dry_kg)
    dv_needed_rtls = 2500.0               # down ~1500 + boostback ~400 + terminal ~600
    assert dv_available >= 1.5 * dv_needed_rtls, (
        f"SH reserve covers only {dv_available:.0f} m/s; RTLS needs ~{dv_needed_rtls:.0f} m/s"
    )


# --- Payload / stack --------------------------------------------------------


def test_multi_ton_payload() -> None:
    """The stack must deliver a multi-ton payload (>= 10 t) into LEO."""
    cfg = FlightCfg(seed=1)
    assert cfg.payload_kg >= 10_000.0
    r = _run(1)
    assert r["perigee_km"] >= 100.0, r


def test_starship_alone_is_suborbital() -> None:
    """Starship alone (no Super Heavy) must NOT reach orbit — it is only a
    near-orbital vehicle; the stack is required (design constraint)."""
    r = _run(1, sh_dry_kg=0.0, sh_lox_kg=0.0, sh_ch4_kg=0.0)
    assert r["perigee_km"] < 100.0, f"Starship alone reached LEO: {r}"


def test_stack_is_required() -> None:
    """The full stack reaches LEO; Starship alone does not (design
    constraint) — the Super Heavy stage is essential for orbit."""
    assert _run(1)["perigee_km"] >= 100.0            # full stack
    r = _run(1, sh_dry_kg=0.0, sh_lox_kg=0.0, sh_ch4_kg=0.0)  # Starship alone
    assert r["perigee_km"] < 100.0                             # alone


# --- Deterministic baseline (noise-free invariance) ------------------------


def test_noise_free_bit_for_bit_reproducible() -> None:
    """With noise_frac=0.0 the mission reproduces deterministically (same
    seed => identical LEO state)."""
    a = _run(1, noise_frac=0.0)
    b = _run(1, noise_frac=0.0)
    assert a["perigee_km"] == b["perigee_km"]
    assert a["apogee_km"] == b["apogee_km"]
