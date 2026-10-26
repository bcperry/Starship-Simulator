from simulator.engine import EngineState, RaptorEngine, run, step

E = RaptorEngine()


def test_thrust_is_positive_and_throttles_down():
    full = run(E, throttle=1.0, duration=2.0)[-1]
    part = run(E, throttle=0.4, duration=2.0)[-1]
    assert full.thrust > 0
    assert 0.3 < part.thrust / full.thrust < 0.5


def test_mass_flow_conserves_mixture_ratio():
    out = run(E, throttle=1.0, duration=2.0)[-1]
    assert out.mdot_ox / out.mdot_fuel == E.mixture_ratio
    assert abs(out.mdot_ox + out.mdot_fuel - out.mdot_total) < 1e-9


def test_back_pressure_reduces_thrust():
    sl = run(E, throttle=1.0, duration=2.0, ambient_pressure=101_325.0)[-1]
    vac = run(E, throttle=1.0, duration=2.0, ambient_pressure=0.0)[-1]
    assert vac.thrust > sl.thrust


def test_chamber_pressure_lags_throttle_demand():
    eng = RaptorEngine(throttle_time_constant=0.2)
    st = EngineState(eng)
    out = step(eng, st, throttle=1.0, dt=0.01)
    assert 0.0 < out.chamber_pressure < eng.chamber_pressure
    for _ in range(300):
        out = step(eng, st, throttle=1.0, dt=0.01)
    assert abs(out.chamber_pressure - eng.chamber_pressure) / eng.chamber_pressure < 0.01


def test_propellant_runoff_depletes_load():
    eng = RaptorEngine(propellant_mass=1000.0, throttle_time_constant=0.05)
    st = EngineState(eng)
    out = None
    for _ in range(int(60 / 0.01)):
        out = step(eng, st, throttle=1.0, dt=0.01)
    # the limiting component is exhausted; an eps-squashed sliver of the
    # excess component is acceptable (<=1% of the load)
    assert min(st.ox_mass, st.fuel_mass) == 0.0
    assert out.propellant_remaining < 0.01 * eng.propellant_mass


def test_spec_sheet_lands_near_published_raptor_class():
    # Raptor 2 class: ~2000 kN / ~350 s vacuum, ~1840 kN / ~327 s sea level.
    vac = run(E, throttle=1.0, duration=2.0, ambient_pressure=0.0)[-1]
    sl = run(E, throttle=1.0, duration=2.0, ambient_pressure=101_325.0)[-1]
    assert 1.7e6 < vac.thrust < 2.3e6
    assert 335.0 < vac.isp < 365.0
    assert 1.6e6 < sl.thrust < 2.0e6
    assert 310.0 < sl.isp < 340.0


def test_invalid_parameters_raise():
    for kw in (("mixture_ratio", 0.0), ("gas_gamma", 1.0),
               ("throttle_time_constant", -1.0), ("throat_area", -0.1)):
        try:
            RaptorEngine(**{kw[0]: kw[1]})
        except ValueError:
            pass
        else:
            raise AssertionError(f"{kw[0]}={kw[1]} should raise")
