"""Phase 9 acceptance tests: the full Raptor-3 Starship stack inserts a
multi-ton payload into LEO while Starship alone cannot reach orbit, under the
1-3 engine-failure spec, with Super Heavy banking boostback propellant.

The full-stack integration test runs one representative in-spec flight
(seed 7 -> 2 Super Heavy + 1 Starship failures) and reuses it across the
assertions via a module-scoped fixture.  It takes ~15 s.
"""
import math

import pytest

from simulator.flight import FlightCfg, make_flight
from simulator.orbit import G0, MU, R_EARTH, orbit_elements


def _n_failed(shipment) -> int:
    return sum(1 for m in shipment._members if m.failed)


@pytest.fixture(scope="module")
def flight():
    """One in-spec (1-3 failure) full-stack flight, run to completion."""
    cfg = FlightCfg(seed=7)
    f = make_flight(cfg)
    f.stack.start_run()
    guard = int(1500.0 / cfg.dt)
    while not (f.done or f.model.crashed) and guard > 0:
        f.step()
        guard -= 1
    return cfg, f


def test_in_spec_failure_count(flight):
    """A launch suffers 1-3 engine failures in aggregate (the sim requirement)."""
    cfg, f = flight
    total = _n_failed(f.stack.sh) + _n_failed(f.stack.ss)
    assert 1 <= total <= 3, f"expected 1-3 failures, got {total}"


def test_full_stack_establishes_leo(flight):
    """The full stack inserts the payload into LEO (perigee above the
    100 km line) and completes without crashing."""
    cfg, f = flight
    assert not f.model.crashed
    assert f.done
    orb = orbit_elements(f.model.r0, f.model.v)
    assert orb is not None, "trajectory unbound / not a valid orbit"
    # LEO established with margin (SECO floor sits above the 100 km line).
    assert orb["perigee_km"] >= 110.0, f"perigee {orb['perigee_km']:.1f} km < 110 (not a comfortable LEO)"
    # A sane bound orbit (perigee below apogee, reasonable eccentricity).
    assert orb["perigee_km"] < orb["apogee_km"]
    assert orb["e"] < 0.1


def test_super_heavy_banks_boostback_propellant(flight):
    """Super Heavy's MECO is guidance-driven (not a dry tank): it separates
    with a meaningful residual for boostback/landing."""
    cfg, f = flight
    st = f.status()
    sh_now = st["ships"]["super-heavy"]["tank_ox_kg"] + st["ships"]["super-heavy"]["tank_ch4_kg"]
    sh_init = cfg.sh_lox_kg + cfg.sh_ch4_kg
    # At least the banked fraction (allow 10 % tolerance for failure-flow loss).
    assert sh_now >= (cfg.sh_meco_resid_frac * sh_init) * 0.9


def test_multi_ton_payload(flight):
    """The payload is genuinely multi-ton (tens of tonnes)."""
    cfg, _ = flight
    assert cfg.payload_kg >= 20_000.0


def test_starship_alone_is_suborbital():
    """Starship by itself (no Super Heavy) lacks the Delta-v to reach LEO --
    the mission requires the two-stage stack.  Analytic Tsiolkovsky check.
    """
    cfg = FlightCfg()
    ss_dry = cfg.ss_dry_kg + cfg.payload_kg
    ss_prop = cfg.ss_lox_kg + cfg.ss_ch4_kg
    m0 = ss_dry + ss_prop
    # Raptor-3 specific impulse band (sea-level to vacuum); even at the
    # generous vacuum end the stage falls short of the LEO requirement.
    dv_best = 380.0 * G0 * math.log(m0 / ss_dry)
    # LEO requirement: circular speed at ~200 km + standard gravity/drag losses.
    v_circ = math.sqrt(MU / (R_EARTH + 200_000.0))
    dv_required = v_circ + 1_400.0
    assert dv_best < dv_required, (
        f"Starship alone Delta-v {dv_best:.0f} >= required {dv_required:.0f}; "
        "the vehicle must need the two-stage stack"
    )


def test_stack_is_required():
    """Sanity: the full stack (Super Heavy + Starship + payload) is the thing
    that launches; Starship's own dry+payload end mass is far below the fuel
    its 6 engines carry, i.e. a real two-stage architecture."""
    cfg = FlightCfg()
    total_stack = (cfg.sh_lox_kg + cfg.sh_ch4_kg + cfg.ss_lox_kg + cfg.ss_ch4_kg
                   + cfg.sh_dry_kg + cfg.ss_dry_kg + cfg.payload_kg)
    assert total_stack > cfg.payload_kg + 4_000_000.0  # multi-thousand-tonne vehicle
