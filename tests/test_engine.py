from simulator.engine import EngineState, RaptorEngine, run, step

E = RaptorEngine()


def test_thrust_is_positive_and_throttles_down():
    full = run(E, throttle=1.0, duration=2.0)[-1]
    part = run(E, throttle=0.4, duration=2.0)[-1]
    assert full.thrust > 0
    assert 0.3 < part.thrust / full.thrust < 0.5


def test_mass_flow_conserves_mixture_ratio():
    out = run(E, throttle=1.0, duration=2.0)[-1]
    assert abs(out.mdot_ox / out.mdot_fuel - E.mixture_ratio) < 1e-9
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
    # Raptor 3 class: 2.45–2.75 MN / ~350 s / 350 bar (vacuum a bit better).
    vac = run(E, throttle=1.0, duration=2.0, ambient_pressure=0.0)[-1]
    sl = run(E, throttle=1.0, duration=2.0, ambient_pressure=101_325.0)[-1]
    assert 2.45e6 < vac.thrust < 3.0e6
    assert 345.0 < vac.isp < 400.0
    assert 2.40e6 < sl.thrust < 2.80e6
    assert 340.0 < sl.isp < 390.0


def test_invalid_parameters_raise():
    for kw in (("mixture_ratio", 0.0), ("gas_gamma", 1.0),
               ("throttle_time_constant", -1.0), ("throat_area", -0.1)):
        try:
            RaptorEngine(**{kw[0]: kw[1]})
        except ValueError:
            pass
        else:
            raise AssertionError(f"{kw[0]}={kw[1]} should raise")


# -- natural high-frequency output jitter -----------------------------------

def _steady(eng: RaptorEngine, seed, n: int = 300, dt: float = 0.01):
    st = EngineState(eng, seed=seed)
    return [step(eng, st, throttle=1.0, dt=dt, t=i * dt) for i in range(n)]


def test_noise_frac_jitters_outputs_around_nominal():
    eng = RaptorEngine(throttle_time_constant=0.05, noise_frac=0.02)
    out = _steady(eng, seed=7)
    warm = out[100:]
    thrust = [o.thrust for o in warm]
    mean = sum(thrust) / len(thrust)
    std = (sum((x - mean) ** 2 for x in thrust) / len(thrust)) ** 0.5
    # 2 % chamber-pressure jitter -> thrust breathes at roughly that level
    assert 0.008 < std / mean < 0.045
    # zero-mean: the noise does not bias the operating point
    nominal = RaptorEngine(throttle_time_constant=0.05)
    nref = _steady(nominal, seed=0)[100:]
    nmean = sum(o.thrust for o in nref) / len(nref)
    assert abs(mean - nmean) / nmean < 0.01


def test_noise_is_deterministic_per_seed_and_independent_between_engines():
    eng = RaptorEngine(throttle_time_constant=0.05, noise_frac=0.02)
    a1 = [o.thrust for o in _steady(eng, seed=41)]
    a2 = [o.thrust for o in _steady(eng, seed=41)]
    b2 = [o.thrust for o in _steady(eng, seed=17)]
    assert a1 == a2                                  # same seed: reproducible stream
    assert a1 != b2                                  # different seed: different draw
    mean = sum(a1[100:]) / 100
    spread = max(a1[100:]) - min(a1[100:])
    assert spread > 0.01 * mean                      # it actually wobbles


def test_noise_frac_zero_is_deterministic_baseline():
    eng = RaptorEngine(throttle_time_constant=0.05, noise_frac=0.0)
    a = [o.thrust for o in _steady(eng, seed=9, n=400)]
    b = [o.thrust for o in _steady(eng, seed=9, n=400)]
    assert a == b
    tail = a[300:]                                    # converged: flat, no wobble
    assert max(tail) - min(tail) < 1e-6 * max(tail)


def test_noise_frac_out_of_range_rejected():
    import pytest
    with pytest.raises(ValueError):
        RaptorEngine(noise_frac=-0.01)
    with pytest.raises(ValueError):
        RaptorEngine(noise_frac=0.1)
