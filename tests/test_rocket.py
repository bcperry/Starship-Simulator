from simulator import Pitch, Rocket, Site, simulate

VERT = Pitch(azimuth=90.0, start=90.0, end=90.0, ramp_time=0.0)
SITE = Site(lat=0.0, lon=0.0, altitude=0.0)


def test_mass_drops_to_dry_at_burnout():
    r = Rocket(dry_mass=10.0, prop_mass=20.0, thrust=1000.0, burn_time=5.0)
    res = simulate(r, SITE, VERT, earth_rotation=False, dt=0.01)
    bo_idx = res.t.index(round(res.burnout_time, 6))
    assert abs(res.mass[bo_idx] - r.dry_mass) < 1.0


def test_no_thrust_lands_quickly():
    r = Rocket(dry_mass=10.0, prop_mass=1.0, thrust=50.0, burn_time=1.0)
    res = simulate(r, SITE, VERT, earth_rotation=False, dt=0.01)
    assert res.apogee_altitude < 1.0


def test_stronger_thrust_reaches_higher():
    low = simulate(Rocket(thrust=800.0), SITE, VERT, dt=0.01)
    hi = simulate(Rocket(thrust=2000.0), SITE, VERT, dt=0.01)
    assert hi.apogee_altitude > low.apogee_altitude


def test_drag_limits_altitude():
    res = simulate(Rocket(drag_coeff=0.7, area=0.018), SITE, VERT, dt=0.005)
    vacuum = simulate(Rocket(drag_coeff=0.0, area=0.018), SITE, VERT, dt=0.005)
    assert res.apogee_altitude <= vacuum.apogee_altitude


def test_vertical_launch_stays_overhead():
    res = simulate(Rocket(), SITE, VERT, earth_rotation=False, dt=0.01)
    assert res.downrange < 50.0  # no horizontal kick -> falls back to pad


def test_slanted_launch_falls_downrange_in_azimuth():
    p = Pitch(azimuth=90.0, start=45.0, end=90.0, ramp_time=10.0)  # east
    res = simulate(Rocket(), SITE, p, earth_rotation=False, dt=0.01)
    assert res.downrange > 1000.0
    assert res.e[-1] > 0  # eastward landing offset
    import math
    assert abs(math.radians(res.landing_lon - SITE.lon)) * SITE.rot_velocity <= 1.0


def test_earth_rotation_boosts_eastern_downrange():
    p = Pitch(azimuth=90.0, start=10.0, end=90.0, ramp_time=15.0)
    with_rot = simulate(Rocket(), Site(lat=0.0, lon=0.0), p, earth_rotation=True, dt=0.01)
    no_rot = simulate(Rocket(), Site(lat=0.0, lon=0.0), p, earth_rotation=False, dt=0.01)
    assert with_rot.downrange > no_rot.downrange


def test_higher_pitch_launches_vertically_and_lowers_downrange():
    low = simulate(Rocket(), SITE, Pitch(azimuth=90.0, start=10.0, ramp_time=8.0),
                   earth_rotation=False, dt=0.01)
    hi = simulate(Rocket(), SITE, Pitch(azimuth=90.0, start=70.0, ramp_time=8.0),
                  earth_rotation=False, dt=0.01)
    assert hi.apogee_altitude > low.apogee_altitude
    assert hi.downrange < low.downrange
