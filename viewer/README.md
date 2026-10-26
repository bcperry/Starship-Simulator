# Engine telemetry viewer (TypeScript)

A TypeScript/React foundation for building the full-stack UX of the simulator.
It consumes the **same v1 wire schema** the Python side publishes and gives you:

- A live **fleet status board** (total thrust, propellant flow, engine counts,
  worst chamber pressure/ISP, min propellant remaining).
- A **thrust & flow trend** chart.
- Per-**engine cards**: state (firing/idle/cut), thrust, chamber pressure,
  Isp, mdot, propellant remaining, throttle demand-vs-actual, sparklines,
  and command/telemetry sequence numbers.
- A **live message feed** of raw command/telemetry records.

## Data sources (pluggable)

The server has two interchangeable sources behind `GET /api/stream`:

| source | what it does |
|---|---|
| `kafka` (default) | Consumes `starship.engine.command` + `starship.engine.telemetry` from your broker. |
| `mock` | Emits a synthetic 33-engine stream (one slowly starves → `cut`) — great for prototyping UI with **no broker**. |

This is the intended dev loop: build the UX against `mock`, then point it at
the real broker the same way the CLI does.

## Run

```bash
cd viewer
npm install

# 1) Develop the UI with no backend at all (synthetic feed):
SOURCE=mock npm run dev          # web on :5173, server on :8080

# 2) Point at a real Kafka broker (e.g. docker compose up from the repo root):
SOURCE=kafka \
BOOTSTRAP_SERVERS=localhost:9092 \
COMMAND_TOPIC=starship.engine.command \
TELEMETRY_TOPIC=starship.engine.telemetry \
npm run dev
```

Production (one port):
```bash
npm run build && SOURCE=kafka npm start   # :8080 serves the API + the UI
```

## API

| route | description |
|---|---|
| `GET /api/health` | Source, engine count, running. |
| `GET /api/state` | Snapshot for (re)connecting clients. |
| `GET /api/stream` | **Server-Sent Events** — a batch of `FeedEvent`s, primed with a snapshot + recent tail on connect. |

Every event is one wire record, typed by `kind` (`command` | `telemetry`) and
carrying the full message plus a receive timestamp. The client folds them into
per-engine state and fleet trends at ~10 Hz.

## Building the full-stack UX on this

The pieces are deliberately small and composable:

- `shared/types.ts` — the single source of truth for the wire schema; add a
  new message type here and it flows through server + client.
- `server/sources.ts` — `MockSource` / `KafkaSource` both satisfy `Source`.
  Add a file-based replay source or a WebSocket source the same way.
- `server/state.ts` — a tiny pub/sub store with a bounded ring buffer and a
  snapshot. Swap in real persistence here.
- `src/hooks/useFeed.ts` — the store → React bridge (`useSyncExternalStore`).
  Any new panel reads from the same snapshot, so panels stay in sync.
- `src/components/*` — each panel (board, trend, engine grid, feed) is a standalone
  component that receives plain props. To add a graph: drop in a component that
  reads `snapshot.engines` or the derived `fleet` series.

The theme lives in `src/theme.css` (design tokens) + `src/app.css` (layout).
