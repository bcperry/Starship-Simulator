# Starship (simulator)

A full-stack, 3-D launch simulator of a SpaceX-style Starship vehicle
(Raptor-3 powered Super Heavy booster + Starship upper stage). The core is a
point-mass 6-DOF flight model in an inertial (ECI) frame -- classical RK4
integration with J2, a 3-layer exponential atmosphere, drag, engine ignition
delay and failure -- driven by a 39-engine Raptor-3 fleet (33 on Super Heavy,
6 on Starship) with per-engine variability, high-frequency output jitter and
exponential time-to-failure. A gravity-turn pitch program flies the vehicle to
multi-ton LEO payload insertion while Super Heavy banks its propellant for
boostback.

## Layout

```
simulator/
  engine/       Raptor (LOX/CH4) physics model (throttle, lag, propellant);
                raptor_v3() = Raptor 3, raptor_vacuum() = long-nozzle variant
  telemetry/    engine<->vehicle wire: schema, publishers, command sources,
                TelemetryEngine, EngineProcess (one step per command),
                __main__.py (standalone engine process over Kafka)
  ship.py       ship = ONE vehicle (33 Super Heavy / 6 Starship) drawing from
                one shared LOX+CH4 tank; per-engine variability + failures +
                per-step jitter; EngineGroup banks (vac/sl)
  starship.py   Starship upper stage: 3x Raptor Vacuum + 3x sea-level banks
  stack.py      full stack: Super Heavy -> staging coast -> Starship,
                sequenced burns, per-stage tanks & failure models
  shiploop.py   ship over stdio (viewer/Kafka)
  stackloop.py  full-stack mission over stdio
  orbit.py      6-DOF ECI flight model: RK4, J2, 3-layer atmosphere + drag,
                gravity-turn pitch program, orbital elements
  flight.py     mission layer: engine stack + flight model + guidance
                (Super Heavy boostback MECO, Starship SECO to LEO)
  flightloop.py full-stack mission over stdio (SSE stream to the viewer)
  rocket.py     legacy 2-D single-booster flight model + plot
  __main__.py   CLI (argparse, plots to trajectory.png)
viewer/         TypeScript/React telemetry viewer (full-stack UX foundation)
                shared/ wire types · server/ (Fastify+SSE, mock|stack|Kafka) · src/
tests/          pytest suite
scripts/        engine_demo.py, fleet_demo.py, command_fleet_demo.py,
                plot.py
```

## Setup

```
uv sync
```

## Run — the LEO mission

The Phase-9 **full-stack LEO mission** is the primary runnable: Super Heavy
(33× Raptor 3) + Starship (6× Raptor 3) insert a ~50 t payload into orbit.
Run it headless, or watch it live in the viewer.

```
# headless: fly a mission to LEO, print the established orbit + SH banked reserve
uv run python scripts/headless_mission.py            # -> perigee ~132 km, apogee ~353 km, e ~0.017

# live: the mission in the browser (SSE), with the orbital-state panel
"cd viewer && SOURCE=flight npm run dev"             # → http://localhost:5173
```

Mission knobs for the viewer's `flight` source (env):

```
SHIP_SEED=7 SHIP_SH_FAILURES=0 SHIP_SS_FAILURES=0 SOURCE=flight npm run dev
```

Or drive the mission loop standalone (controls on stdin, JSON docs on stdout):

```
uv run python -m simulator.flightloop --speed 20 --seed 7 --sh-failures 0 --ss-failures 0
```

### Legacy 2-D booster (not the mission)

`uv run python -m simulator` is the **legacy 2-D single-booster model**
(`simulator/rocket.py`) — handy for a quick altitude/velocity profile, and it
plots to `trajectory.png` (altitude/MAV, velocity breakdown, pitch, flight
path). It is **not** the orbital launch above; use `scripts/headless_mission.py`
or `simulator.flightloop` for the LEO mission.

```
uv run python -m simulator                 # default Super Heavy booster
uv run python -m simulator --thrust 7200000 --burn-time 165 --area 1.45
uv run python -m simulator --earth-rotation # account for the launch site spinning
```

## Full-stack LEO mission

`simulator/flight.py` composes the engine fleet with the 6-DOF flight model
into a real orbital launch: Super Heavy (33x Raptor 3) boosts the full stack,
stages with ~20 % of its propellant banked for boostback, and Starship
(6x Raptor 3) completes insertion. The vehicle is sized so the **full stack
inserts a ~50 t payload into LEO** (perigee above the 100 km line) while
**Starship alone is sub-orbital** -- the mission requires both stages.

```
uv run python scripts/headless_mission.py            # headless LEO run + orbit readout
uv run python -m simulator.flightloop --speed 20     # live mission (SSE to the viewer)
cd viewer && SOURCE=flight npm run dev               # same mission, in the browser
```

Acceptance (`tests/test_flight.py`): a **failure-free** launch establishes LEO
(perigee >= 100 km) **robustly across multiple seeds** — a single-seed success
is explicitly not sufficient, and there is comfortable margin once Earth's
rotation is credited. An **engine-out** run completes without crashing (the
vehicle degrades gracefully; at the edge of the envelope it re-enters rather
than "crashes"). Super Heavy **banks** its boostback propellant, and that
reserve is asserted to cover a real RTLS (down + boostback + terminal) delta-v
budget with margin. And **Starship alone cannot reach orbit** — the stack is
required.

## Engine model

`simulator/engine/raptor.py` is a parameterized gas-generator cycle
engine (default: Raptor 3): choked injector flow sets mass flow from
`Pc * A_t`, isentropic nozzle expansion gives `C_F`, and the rocket
equation `F = A_e (p_e - p_a) + mdot v_e` gives thrust at any ambient
pressure. Throttling is a first-order lag on chamber pressure; optional
finite propellant with mixture-ratio-consistent consumption. Calibrated to
the Raptor 3 spec sheet (~2.5-2.7 MN sea-level, ~2.7-3.0 MN vacuum, ~350 s
Isp at 350 bar).

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
building the product UX. It has four interchangeable data sources behind one
`/api/stream`:

- **`kafka`** — consumes your broker (`starship.engine.command` +
  `starship.engine.telemetry`), the same records as above;
- **`mock`** — a synthetic 33-engine stream (one starves → `cut`) so you can
  shape the UI with no broker attached;
- **`stack`** — spawns the full-stack ship-level mission (`simulator.stackloop`);
- **`flight`** — spawns the **Phase-9 orbital LEO mission**
  (`simulator.flightloop`): the same per-engine/ship/stack stream **plus** a
  `flight.state` document (position, velocity, q/Mach, coast-orbit elements,
  staging, banked reserve) that drives the **Mission · orbital state** panel.

You get a live fleet board (total thrust, propellant flow, firing/cut counts,
worst chamber pressure/ISP, min propellant remaining), a thrust-and-flow
trend chart, per-engine cards (state, P_c, Isp, mdot, propellant, throttle
demand vs actual, sparklines, command/telemetry seq), and a raw message feed.
In `flight` mode, the top panel reads the real orbit: altitude/downrange,
velocity/heading, Mach/q, Δv spent, perigee/apogee/inclination/eccentricity
against the 100 km LEO line, SH MECO / SS SECO, the banked SH reserve and
jettisoned stack, and both vessels' firing/failed/tank state.

```bash
cd viewer && npm install

SOURCE=mock npm run dev                          # build the UX with a synthetic feed
SOURCE=stack npm run dev                         # full stack: Super Heavy -> Starship (ship-level)
SOURCE=flight npm run dev                        # the Phase-9 LEO mission + orbital-state panel
SHIP_SEED=7 SHIP_SH_FAILURES=1 SHIP_SS_FAILURES=0 SOURCE=flight npm run dev  # inject failures
SOURCE=kafka BOOTSTRAP_SERVERS=localhost:9092 \
  COMMAND_TOPIC=starship.engine.command \
  TELEMETRY_TOPIC=starship.engine.telemetry \
  npm run dev                                     # ...or against your docker-broker

npm run build && SOURCE=kafka npm start           # one port: API + UI (prod)
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
  - `noise_frac` (`--noise`, default **0.02**): natural high-frequency jitter —
    a per-step zero-mean Gaussian (fraction of the nominal value) on chamber
    pressure plus a smaller expansion-efficiency wobble. The choked-flow
    relation carries the pressure jitter into mdot, exit pressure and Isp, so
    chamber pressure, propellant flow and thrust all *breathe* together at
    high frequency, independently per engine (each engine draws its own
    reproducible stream; 0.02 = 2 % rms on chamber pressure, 0 = fully
    deterministic). All other model inputs are unchanged by this.
    - Retunable live: `start_run` accepts `noise_frac` (0–0.05).
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

## Full stack (simulator.stack): Super Heavy + Starship as one mission

> The `Stack` class below is the per-ship sequencer also used by the 6-DOF LEO
> mission (`simulator.flight`). The tank loads quoted here (5,480 t SH / 540 t SS)
> are the **per-ship demo defaults** (`ShipConfig`); the LEO mission sizes its
> tanks differently (4,200 t SH / 1,200 t SS, `flight.TANK_DEFAULTS`) -- see the
> "Full-stack LEO mission" section above.

The `Stack` class composes two of these ships — a 33-engine **Super Heavy**
(first stage, `raptor_v2`) and a 6-engine **Starship** upper stage
(`simulator.starship`: 3x `raptor_vacuum` + 3x sea-level, distinct by
`bank` on every telemetry doc, `ss-01…03` = VAC, `ss-04…06` = SL) — into one
sequenced mission:

1. **liftoff** — Super Heavy burns from its own tank (default 5,480 t LOX +
   1,520 t CH4), 33 engines;
2. **staging** — Super Heavy MECO (commanded or fuel-limited) + a short
   coast (`staging_delay_s`, default 4 s);
3. **upper** — Starship's six engines ignite, burning its own tank
   (default 540 t LOX + 150 t CH4);
4. **done** — upper burn out.

Each stage keeps its own tank, run id, seed, failure hazard and noise stream
(the per-stage expected failures are independent calibration: default
`sh_expected_failures = 2.0` over the first burn, `ss_expected_failures = 1.0`
over the upper burn). One wire document per vehicle
(`vehicle_id: "super-heavy" | "starship"`) plus a mission-level
`stack.status` (phase, active stage, both tanks, staging countdown) folded
into a single `stack.status` per step. Controls apply to the *active* stage:
`meco`/`seco` cut it (MECO before staging triggers staging), `throttle`
scales the whole stack, `start_run` re-arms both stages fresh.

```
# full-stack mission over stdio (Super Heavy -> staging -> Starship)
uv run python -m simulator.stackloop --dt 0.01 --speed 20

# or via the viewer: SOURCE=stack starts a two-stage mission automatically
cd viewer && PORT=8090 SOURCE=stack node --import tsx server/index.ts
```

The viewer renders the mission phase bar (LIFTOFF → STAGING → UPPER → DONE),
a per-stage panel (own tank + own failure counts) and bank chips (VAC/SL) on
each engine card.

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
