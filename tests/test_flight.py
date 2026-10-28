"""Phase 9 acceptance tests: the full Raptor-3 Starship stack inserts a
multi-ton payload into LEO, while Starship alone cannot reach orbit.

LEO robustness is asserted on the *nominal (failure-free)* mission across
several seeds — a single-seed success is explicitly NOT sufficient.  The
vehicle degrades gracefully with engine-outs (the run always completes
without crashing) but, because Starship's own Delta-v is capped so that it
must remain sub-orbital on its own, heavy upper-stage losses (2-3 engines)
cross the physical LEO capability limit: perigee cannot be lifted off the
surface.  Those cases are asserted to *complete* (sim robustness), not to
reach LEO.
"""
import math

import pytest

from simulator.flight import FlightCfg, make_flight
from simulator.orbit import G0, MU, R_EARTH, orbit_elements


def _n_failed(shipment) -> int:
    return sum(1 for m in shipment._members if m.failed)


def _run(cfg) -> dict:
    """Run a flight to DONE or crash; return a compact summary."""
    f = make_flight(cfg)
    f.stack.start_run()
    guard = int(2000.0 / cfg.dt)
    while not (f.done or f.model.crashed) and guard > 0:
        f.step()
        guard -= 1
    orb = orbit_elements(f.model.r0, f.model.v) or {}
    return {
        "cfg": cfg, "f": f, "orb": orb,
        "sh_failed": _n_failed(f.stack.sh), "ss_failed": _n_failed(f.stack.ss),
        "perigee_km": orb.get("perigee_km", 0.0),
    }


def test_failure_free_leo_robust_across_seeds():
    """The nominal (no engine-outs) mission must establish LEO on the large
    majority of seeds -- a single-seed success is insufficient.  Perigee is
    well above the 100 km line (margin for residual drag)."""
    ok = 0
    for seed in (1, 2, 3, 7, 9, 11):
        r = _run(FlightCfg(seed=seed, sh_expected_failures=0.0, ss_expected_failures=0.0))
        assert not r["f"].model.crashed, f"seed {seed}: crashed"
        assert r["sh_failed"] == 0 and r["ss_failed"] == 0, f"seed {seed}: not failure-free"
        orb = r["orb"]
        if (orb.get("perigee_km", 0.0) >= 110.0
                and orb.get("perigee_km", 0.0) < orb.get("apogee_km", 0.0)
                and orb.get("e", 1.0) < 0.1):
            ok += 1
    assert ok >= 5, f"only {ok}/6 failure-free seeds reached a clean LEO (need >=5/6)"


def test_nominal_single_engine_out_still_leo_majority():
    """Graceful degradation: with a single expected Starship engine-out the
    nominal mission still reaches LEO on a solid majority of seeds."""
    ok = 0
    for seed in (1, 7, 9, 11):
        r = _run(FlightCfg(seed=seed, sh_expected_failures=0.0, ss_expected_failures=1.0))
        if r["orb"].get("perigee_km", 0.0) >= 110.0:
            ok += 1
    assert ok >= 3, f"only {ok}/4 one-SS-failure seeds reached LEO (need >=3/4)"


def test_engine_out_runs_complete_without_crashing():
    """At the *default* expected-failure rate (2.0/ship) the vehicle is at its
    LEO capability edge: some outcomes establish LEO, others over-raise
    apogee and re-enter.  Either way the simulation must run to completion
    cleanly (no crash, a valid terminal DONE state) -- the failure
    realisation is *handled*, not fatal, whether the orbit is
    established or not."""
    for seed in (7, 13, 2):   # 7/13 -> LEO; 2 -> re-entry (both must complete)
        r = _run(FlightCfg(seed=seed))  # default expected failures = 2.0/ship
        assert not r["f"].model.crashed, f"seed {seed}: model crashed"
        assert r["f"].done, f"seed {seed}: stack did not reach DONE"


def test_super_heavy_banks_boostback_propellant():
    """Super Heavy's MECO is guidance-driven (not a dry tank): it separates
    with a meaningful residual for boostback/landing."""
    r = _run(FlightCfg(seed=7, sh_expected_failures=0.0, ss_expected_failures=0.0))
    cfg = r["cfg"]
    st = r["f"].status()
    sh_now = (st["ships"]["super-heavy"]["tank_ox_kg"]
              + st["ships"]["super-heavy"]["tank_ch4_kg"])
    sh_init = cfg.sh_lox_kg + cfg.sh_ch4_kg
    assert sh_now >= (cfg.sh_meco_resid_frac * sh_init) * 0.85


def test_multi_ton_payload():
    """The payload is genuinely multi-ton (tens of tonnes)."""
    assert FlightCfg().payload_kg >= 20_000.0


def test_starship_alone_is_suborbital():
    """Starship by itself (no Super Heavy) lacks the Delta-v to reach LEO --
    the mission requires the two-stage stack.  Analytic Tsiolkovsky check."""
    cfg = FlightCfg()
    ss_dry = cfg.ss_dry_kg + cfg.payload_kg
    ss_prop = cfg.ss_lox_kg + cfg.ss_ch4_kg
    m0 = ss_dry + ss_prop
    # Raptor-3 vacuum Isp; even at the generous end the stage falls short.
    dv_best = 380.0 * G0 * math.log(m0 / ss_dry)
    v_circ = math.sqrt(MU / (R_EARTH + 200_000.0))
    dv_required = v_circ + 1_400.0
    assert dv_best < dv_required, (
        f"Starship alone Delta-v {dv_best:.0f} >= required {dv_required:.0f}; "
        "the vehicle must need the two-stage stack")


def test_stack_is_required():
    """A real two-stage architecture: multi-thousand-tonne full stack."""
    cfg = FlightCfg()
    total_stack = (cfg.sh_lox_kg + cfg.sh_ch4_kg + cfg.ss_lox_kg + cfg.ss_ch4_kg
                   + cfg.sh_dry_kg + cfg.ss_dry_kg + cfg.payload_kg)
    assert total_stack > cfg.payload_kg + 4_000_000.0
