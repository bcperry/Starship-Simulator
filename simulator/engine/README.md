# Raptor engine simulation

A parameterizable LOX/CH4 rocket engine model (defaults: **Raptor 2** class,
~1.759 MN / ~350 s vacuum) plus the tooling to run it: a quick-print
demo, a Python API for custom loops, and a
telemetry adapter for feeding a vehicle-level sim.

The model is deliberately simple and fast (~8 µs per step including
telemetry emission), not certification-grade: choked injector flow sets
mass flow from chamber pressure, isentropic nozzle expansion (bisection on
exit Mach) gives thrust and Isp, throttling is a first-order lag on
chamber pressure, and propellant is optional with mixture-ratio-consistent
consumption.

## Quick start

### 1. Run one engine — print the spec sheet

```
uv run python scripts/engine_demo.py
```

Prints the rated flows (max mdot, LOX/CH4 split) and SL vs vacuum thrust,
Isp, exhaust velocity, exit pressure for the default Raptor 2.

### 2. Call it from Python

```python
from simulator.engine import RaptorEngine, run, summary

e = RaptorEngine(propellant_mass=4000)      # kg loaded; 0 = unlimited
out = run(e, throttle=1.0, duration=10.0, ambient_pressure=101_325.0)
print(summary(e, sl=out[-1],               # sea-level point   (101 325 Pa)
                  vac=run(e, ambient_pressure=1e3)[-1]))       # vacuum point  (~1 kPa)

# or drive a custom loop yourself (per-step ambient, variable throttle):
from simulator.engine import EngineState, step
st = EngineState(e)
for i, thr in enumerate([0.4, 0.6, 0.8, 1.0] * 25):
    o = step(e, st, thr, ambient_pressure=101_325.0, dt=0.01, t=i * 0.01)
    print(o.thrust, o.isp, o.propellant_remaining)
```

## Engine parameters

All fields of the frozen `RaptorEngine` dataclass (SI units):

| field                    | default        | meaning                                          |
|--------------------------|----------------|--------------------------------------------------|
| `name`                   | `"Raptor 2"`   | label, free-form                                 |
| `mixture_ratio`          | `3.6`          | O/F mass ratio (LOX/CH4)                         |
| `chamber_pressure`       | `3.0e7` Pa     | target Pc at 100% throttle                       |
| `throat_area`            | `0.035` m²     | injector throat area                             |
| `nozzle_area_ratio`      | `40.0`         | A_e/A_t, fixed geometry                          |
| `gas_temperature`        | `3900.0` K     | effective chamber temperature                    |
| `gas_molar_mass`         | `0.0223` kg/mol| effective products molar mass                    |
| `gas_gamma`              | `1.22`         | effective specific-heat ratio                    |
| `cstar_efficiency`       | `1.00`         | injector/combustion losses (<1 derates C*)       |
| `thrust_efficiency`      | `1.00`         | real-gas / non-isentropic losses (<1 derates CF) |
| `throttle_time_constant` | `0.5` s        | first-order lag between throttle and Pc          |
| `propellant_mass`        | `0.0` kg       | propellant load; `0` = unlimited reservoir       |

Outputs each step (`EngineOutput`): thrust (N), chamber pressure (Pa),
LOX/CH4/total mass flow (kg/s), propellant remaining (kg), Isp (s),
exhaust velocity (m/s), exit pressure (Pa), exit Mach, ambient (Pa), t (s).

## Feeding a vehicle sim (telemetry)

`simulator/telemetry` wraps one engine per instance and emits one JSON
telemetry message per physics step to a shared transport (in-process queue
or Kafka). The vehicle side feeds `aggregate_fleet()` a timestep's worth of
messages to get vehicle-level totals. There is also a **command** direction:
the vehicle sends one `engine.command` per engine per timestep and
`EngineProcess` takes exactly one physics step per command — one process per
engine via `python -m simulator.telemetry` over Kafka (see `../README.md` →
*Telemetry*). The 33-engine demos are `scripts/fleet_demo.py` (telemetry)
and `scripts/command_fleet_demo.py` (command-driven).

```python
from simulator.engine import RaptorEngine
from simulator.telemetry import TelemetryEngine, InMemoryPublisher, aggregate_fleet

pub = InMemoryPublisher()
eng = TelemetryEngine("sh-07", RaptorEngine(propellant_mass=4000), pub, dt=0.01)
msg = eng.step(throttle=1.0, ambient_pressure=101_325.0)
row = aggregate_fleet([msg])          # vehicle-level view of this step
```

## Model caveats

- Single effective gas for all combustion products (no per-species dynamics).
- No regenerative cooling, no HX losses; C*/CF efficiencies are lumps.
- Finite propellant with an *exhaustion sliver*: at the mixture-ratio
  boundary the excess component drains to 0 but the model squeezes out a
  numerically small sliver of the limiting component (see
  `tests/test_engine.py`), so "depleted" means `remaining <= 1% of load`.
- Throttling is first-order only (no valve dynamics, no transients).
- Not a certification-grade model — use for vehicle-level trajectory /
  guidance work, not detail design.
