from simulator import Rocket, simulate


def test_mass_drops_to_dry_at_burnout():
    r = Rocket(dry_mass=10.0, prop_mass=20.0, thrust=1000.0, burn_time=5.0)
    res = simulate(r, dt=0.01)
    bo_idx = res.t.index(round(res.burnout_time, 6))
    assert abs(res.m[bo_idx] - r.dry_mass) < 1.0


def test_no_thrust_falls_straight_down():
    r = Rocket(dry_mass=10.0, prop_mass=1.0, thrust=50.0, burn_time=1.0)
    res = simulate(r, dt=0.01)
    assert res.apogee_altitude < 1.0  # barely leaves the pad


def test_stronger_thrust_reaches_higher():
    low = simulate(Rocket(thrust=800.0), dt=0.01)
    hi = simulate(Rocket(thrust=2000.0), dt=0.01)
    assert hi.apogee_altitude > low.apogee_altitude


def test_energy_budget_apogee_bound():
    # vacuum-ish upper bound with no drag: apogee can't exceed it
    r = Rocket(drag_coeff=0.7, area=0.018)
    res = simulate(r, dt=0.005)
    vacuum = simulate(Rocket(drag_coeff=0.0, area=0.018), dt=0.005)
    assert res.apogee_altitude <= vacuum.apogee_altitude


def test_lands_after_launch():
    res = simulate(Rocket(), dt=0.01)
    assert 0 < res.landing_time
    assert res.landing_velocity < 0
