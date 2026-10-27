# Starship (simulator)

A 2-D flight sim of a SpaceX-style heavy-lift booster: semi-implicit Euler
integration of point-mass dynamics with a gravity-turn pitch program,
exponential atmosphere, drag, engine ignition delay, and (optionally) a
rotating Earth.

## Layout

```
simulator/
  engine/       Raptor (LOX/CH4) physics model (throttle, lag, propellant)
  telemetry/    engine<->vehicle wire: schema, publishers, command sources,
                TelemetryEngine, EngineProcess (one step per command),
                __main__.py (standalone engine process over Kafka)
  rocket.py     flight model, pitch program, simulation loop
  __main__.py   CLI (argparse, plots to trajectory.png)
viewer/         TypeScript/React telemetry viewer (full-stack UX foundation)
                shared/ wire types · server/ (Fastify+SSE, mock|Kafka) · src/
tests/          pytest suite
scripts/        engine_demo.py, fleet_demo.py, command_fleet_demo.py,
                plot.py
```

## Setup

```
uv sync
```

## Run

```
uv run python -m simulator                 # default Super Heavy booster
uv run python -m simulator --thrust 7200000 --burn-time 165 --area 1.45
uv run python -m simulator --earth-rotation # account for the launch site spinning
```

The sim stops at apogee and saves `trajectory.png`: altitude/MAV profile,
velocity breakdown, pitch angle, and the flight path on a non-log scale.

## Engine model

`simulator/engine/raptor.py` is a parameterized gas-generator cycle
engine (default: Raptor 2): choked injector flow sets mass flow from
`Pc * A_t`, isentropic nozzle expansion gives `C_F`, and the rocket
equation `F = A_e (p_e - p_a) + mdot v_e` gives thrust at any ambient
pressure. Throttling is a first-order lag on chamber pressure; optional
finite propellant with mixture-ratio-consistent consumption. Calibrated
to the published spec sheet (~1.9 MN / ~350 s vacuum).

Quick demo (no GUI needed):

```
uv run python scripts/engine_demo.py
```

## Telemetry (simulator.telemetry)

The engine sim and the vehicle (flight) sim are meant to run separately and
communicate over a message bus. `simulator/telemetry` is that layer, built
for the 33-engine Super Heavy case (33 x 100 Hz x ~1 KB is trivially light):

- **`messages.py`** — versioned JSON wire format for **both directions** (`v=1`, SI units per field, docstring lists the contract): `engine.telemetry` (engine -> vehicle), `fleet.telemetry` (`aggregate_fleet()` row), and `engine.command` (vehicle -> engine). Engine-state words (`idle`/`firing`/`cut`, with cut = propellant-starved, honoring the engine model's depletion sliver); `to_telemetry()`, `aggregate_fleet()`, `to_command()`, `validate_command()`.
- **`publishers.py`** — transport-agnostic `Publisher` protocol with an
  in-memory, thread-safe `InMemoryPublisher` (FIFO per engine, so a vehicle
  sim draining it always sees per-engine order) and an optional
  `KafkaPublisher` on `confluent-kafka`: one topic per vehicle, JSON value,
  `engine_id` as the message key (per-engine ordering on one partition,
  consumers scale across partitions, bounded topic count).
- **`consumers.py`** — the vehicle -> engine direction: `CommandSource`
  protocol, `InMemoryCommandSource` (FIFO the vehicle offers into), and
  `KafkaCommandSource` (subscribes the command topic with **the engine id as
  the consumer group**, so each engine is its own single-reader, ordered
  stream and a restart rejoins its own stream).
- **`engine_feeder.py`** — two drives over one core:
  `TelemetryEngine` (`step(throttle, ambient)`, for in-process integration
  and demos) and `EngineProcess` — one physics step per consumed
  `engine.command`, command's dt/throttle/ambient honored, step's
  `command_seq` echoes the answer, mis-addressed or malformed commands
  rejected loudly (never silently burned in), `run(max_steps, idle_timeout)`
  for the standalone loop.
- **`__main__.py`** — the standalone engine process over Kafka (one per
  engine): consumes the command topic, publishes telemetry, one step per
  command in order.

Fleet demo — 33 engines publishing to one publisher, vehicle sim drains
the wire, one engine starves and the fleet row notices:

```
uv run python scripts/fleet_demo.py
```

### Vehicle -> engine: the command direction and the per-engine process

The other direction is the clock: the vehicle sim sends N `engine.command`
records per timestep (one per engine, `engine_id` as the key); each engine
process polls its own stream and takes exactly **one physics step per
command**, in order — so there is no engine-side loop to drift, and each
command carries the dt/throttle/ambient the engine must honor. The reply is
one `engine.telemetry` with `command_seq` set. In-process the wiring is
`to_command()` -> `InMemoryCommandSource` -> `EngineProcess`; over Kafka
it is one process per engine:

```
# one per engine (33 for Super Heavy), same host or spread across nodes
uv run python -m simulator.telemetry \
  --engine-id sh-07 \
  --command-topic starship.engine.command \
  --telemetry-topic starship.engine.telemetry \
  --bootstrap-servers localhost:9092 \
  --load-kg 4000 --max-steps 2000 --idle-timeout 120

# 33-engine command-driven fleet, in-process (one engine cuts off at the end)
uv run python scripts/command_fleet_demo.py
```

Kafka is an optional dependency (not installed by default):

```
uv sync --group kafka        # adds confluent-kafka
```

Producer-side sketch in a real process:

```python
pub = KafkaPublisher(topic="super-heavy.engine.telemetry",
                     bootstrap_servers="kafka:9092",
                     config={"compression.type": "zstd"})
eng = TelemetryEngine("sh-07", RaptorEngine(), pub, vehicle_id="super-heavy")
# vehicle sim: consume the topic (key = engine_id), fold each timestep
# into aggregate_fleet()
```

### Telemetry UI (`viewer/` — the full-stack foundation)

A **TypeScript/React** viewer for exactly this wire — the starting point for
building the product UX. It has two interchangeable data sources behind one
`/api/stream`:

- **`kafka`** — consumes your broker (`starship.engine.command` +
  `starship.engine.telemetry`), the same records as above;
- **`mock`** — a synthetic 33-engine stream (one starves → `cut`) so you can
  shape the UI with no broker attached.

You get a live fleet board (total thrust, propellant flow, firing/cut counts,
worst chamber pressure/ISP, min propellant remaining), a thrust-and-flow
trend chart, per-engine cards (state, P_c, Isp, mdot, propellant, throttle
demand vs actual, sparklines, command/telemetry seq), and a raw message feed.

```bash
cd viewer && npm install

SOURCE=mock npm run dev                          # build the UX with a synthetic feed
SOURCE=kafka BOOTSTRAP_SERVERS=localhost:9092 \
  COMMAND_TOPIC=starship.engine.command \
  TELEMETRY_TOPIC=starship.engine.telemetry \
  npm run dev                                     # ...or against your docker-broker

npm run build && SOURCE=kafka npm start           # one port: API + UI on :8080
```

The architecture is deliberately composable — `shared/types.ts` (wire schema),
`server/sources.ts` (`MockSource`/`KafkaSource` behind one `Source`),
`server/state.ts` (ring-buffer + snapshot pub/sub), and
`src/hooks/useFeed.ts` (store→React). Adding a graph, a new panel, a replay
source, or persistence is a local change. See `viewer/README.md`.

## Ship-level sim (simulator.ship) + engine failure model

A launch is one vehicle of N Raptor engines (Super Heavy: 33, Ship: 6)
drawing from ONE shared LOX + CH4 tank: the ship is the fuel authority,
feeds the engines each cycle, and commands MECO when the tank can no longer
feed a step. Per-engine starting conditions are spread over +/-``vari_frac``
(default 1%), seeded per (vehicle, run seed, engine index). Each engine can
also *fail* at a random moment during the burn:

- **Model**: constant-hazard (exponential) time-to-failure — the standard
  reliability-theory model for random hardware failures inside a single
  short burn (turbopump seizure, valve failure, combustion instability,
  control faults). It is the standard choice
  consistent with real Starship history: failures at ignition, mid-burn,
  near MECO, all of them — a constant per-second failure rate.
- **Inputs** (`ShipConfig` / `shiploop` CLI):
  - `expected_failures_per_run` (default **2.0**, `--failures-per-run`):
    the EXPECTED fleet failures over a nominal full-tank launch; the ship
    solves the per-engine hazard `lambda = -ln(1 - E/N) / T_nom`. 0 makes
    launches failure-free; 1-3 is the observed standard band.
  - `fail_hazard_per_s` (`--fail-hazard`): pass the distribution parameter
    directly, overriding the calibration.
- **Mechanics**: every step, each live engine rolls
  `P(fail) = 1 - exp(-lambda dt)` (exact discrete survival, dt-invariant).
  A failed engine produces no thrust and draws no propellant — the shared
tank keeps feeding only the healthy engines, so fleet fuel flow, thrust and
MECO all stay correct for any number of failures.
- **Wire**: engine `state` gains `"failed"` (distinct from `"cut"`, which is
  a feed cutoff); `ship.status` gains `failed_count`, `failed_engines`,
  `expected_failures_per_run`, `fail_hazard_per_s`. The viewer badges failed
  engines and lists them on the ship panel.

Fast physics check (no viewer/Kafka needed):

```
uv run python -c "
from simulator.ship import Ship, ShipConfig
s = Ship(ShipConfig(seed=42, expected_failures_per_run=2.0))
while not (s.meco and s.t >= (s.meco_t or 0) + 5): s.step()
st = s.status()
print(f\"meco @ {st['meco_t']:.0f}s | {st['failed_count']}/33 failed: {st['failed_engines']}\")
"
# failure-free launch: expected_failures_per_run=0.0
# raw hazard instead of calibration: ShipConfig(fail_hazard_per_s=0.001)
```

## How it works

Full details in `simulator/rocket.py` and `simulator/engine/raptor.py`
docstrings. Highlights:

- **Frame**: ENU (east-north-up). Position, velocity, and acceleration are
  integrated in this inertial-ish frame; gravity points "down" (-U), drag
  opposes velocity, and pitch is the flight-path angle `atan2(v_up,
  v_north)`.
- **Pitch program**: gravity turn. Pitch starts at 90 deg (straight up),
  then the rate of descent is proportional to local `g/cos(theta)` so
  the trajectory bends over as velocity builds — the classic
  `theta = arctan(aero/g)` style turn.
- **Atmosphere**: exponential density `rho = rho0 * exp(-h/H)` with
  `H = 8500 m`. Drag is `0.5 * rho * v^2 * Cd * A`.
- **Earth rotation (optional)**: when `earth_rotation=True`, the launch
  site spins east at `omega_E * R_E * cos(lat)`. The sim starts with the
  vehicle already moving at that speed, and a Coriolis term is included
  while in the rotating frame. This is why prograde launches from the
  equator get a free ~460 m/s kick.
- **Integrator**: semi-implicit Euler (velocity first, then position) at
  `dt = 0.01 s`. Stable enough for this problem; no subcycling.
