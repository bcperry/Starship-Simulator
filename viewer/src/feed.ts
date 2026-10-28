// Client feed store: keeps an EventSource open to /api/stream, and folds each
// server snapshot into (a) per-engine latest + history, (b) a ship fuel/trend
// series, (c) a message log. Coalesced to ~10 Hz so React renders cheaply.

import {
  fleetSummary, type FleetSummary,
} from "./lib/aggregate";
import type {
  CommandMessage,
  FlightStateMessage,
  LineSummary,
  ShipAck,
  ShipStatusMessage,
  StackStatusMessage,
  TelemetryMessage,
} from "@shared/types";

const HISTORY_CAP = 480;
const FLEET_CAP = 900;
const SHIP_CAP = 900;
const LINE_CAP = 200;

export interface SeriesPoint {
  t: number;
  seq: number;
  thrust: number;
  pc: number;
  mdot: number;
  state: string;
}

export interface EngineView {
  id: string;
  telemetry: TelemetryMessage | null;
  command: CommandMessage | null;
  history: SeriesPoint[];
  lastRecvMs: number;
}

export interface FleetPoint extends FleetSummary {
  tsMs: number;
  t: number;
}

export type FeedLineKind = "command" | "telemetry" | "ship" | "stack" | "flight";

export interface FeedLine {
  id: string;
  tsMs: number;
  kind: FeedLineKind;
  engine_id: string;
  summary: string;
}

export interface ShipPoint {
  t: number;
  thrust_n: number;
  tank_frac: number;
  mdot_kg_s: number;
  meco: boolean;
}

export interface ShipView {
  ship: ShipStatusMessage | null;
  history: ShipPoint[];
  lastAck: ShipAck | null;
}

export interface Snapshot {
  v: number;
  engines: EngineView[];
  fleet: FleetPoint[];
  lines: FeedLine[];
  counts: { command: number; telemetry: number; ship: number; engines: number };
  ship: ShipView; // the ACTIVE vessel (stack mode: follows the burning stage)
  ships: Record<string, ShipView>; // per-vehicle (single-ship feeds: one key)
  stack: StackStatusMessage | null; // full-stack phase state (stack feeds only)
  flight: FlightStateMessage | null; // Phase-9 orbital mission state (flight feeds)
  source: string | null;
  connected: boolean;
  lastEventMs: number;
  paused: boolean;
}

interface ServerHeld {
  lineSummary: LineSummary;
  message: TelemetryMessage | CommandMessage;
}

interface ServerSnapshot {
  latest: Record<string, { telemetry?: ServerHeld; command?: ServerHeld }>;
  ship: ShipStatusMessage | null;
  ships?: Record<string, ShipStatusMessage>;
  stack?: StackStatusMessage | null;
  flight?: FlightStateMessage | null;
  lines: LineSummary[];
  counts: { command: number; telemetry: number; ship: number; engines: number };
  ts_ms: number;
}

class ClientFeed {
  private es: EventSource | null = null;
  pausedFlag = false;

  private engines = new Map<string, EngineView>();
  private fleet: FleetPoint[] = [];
  private lines: FeedLine[] = [];
  private lineMap = new Map<string, FeedLine>();
  private ship: ShipView = { ship: null, history: [], lastAck: null };
  private shipViews = new Map<string, ShipView>();
  private stack: StackStatusMessage | null = null;
  private flight: FlightStateMessage | null = null;
  private runId: string | null = null;
  private counts = { command: 0, telemetry: 0, ship: 0, engines: 0 };
  private source: string | null = null;
  private connected = false;
  private lastEventMs = 0;
  private dirty = true;

  private listeners = new Set<() => void>();

  private snap: Snapshot = {
    v: 0,
    engines: [],
    fleet: [],
    lines: [],
    counts: { command: 0, telemetry: 0, ship: 0, engines: 0 },
    ship: { ship: null, history: [], lastAck: null },
    ships: {},
    stack: null,
    flight: null,
    source: null,
    connected: false,
    lastEventMs: 0,
    paused: false,
  };

  connect(): void {
    if (this.es) return;
    const es = new EventSource("/api/stream");
    this.es = es;
    es.onopen = () => {
      this.connected = true;
      this.markDirty();
    };
    es.onerror = () => {
      this.connected = false;
      this.markDirty();
    };
    es.onmessage = (e) => {
      let snap: ServerSnapshot;
      try {
        snap = JSON.parse(e.data) as ServerSnapshot;
      } catch {
        return; // heartbeat etc.
      }
      if (typeof snap?.ts_ms !== "number") return;
      if ("latest" in snap) {
        this.apply(snap);
        this.markDirty();
      }
    };

    const flush = setInterval(() => {
      if (this.fleetDirty) {
        this.fleetDirty = false;
        const any = [...this.engines.values()].find((v) => v.telemetry);
        const lastT = any?.telemetry?.t ?? 0;
        this.fleet.push({
          ...fleetSummary(this.latestTelemetry()),
          tsMs: this.lastEventMs || Date.now(),
          t: lastT,
        });
        if (this.fleet.length > FLEET_CAP) this.fleet.shift();
      }
      if (this.dirty) {
        this.dirty = false;
        this.snap = this.rebuildSnapshot();
      }
    }, 100);
    this.flushTimer = flush;

    void fetch("/api/health")
      .then((r) => r.json())
      .then((h) => {
        if (h?.source) {
          this.source = h.source;
          this.markDirty();
        }
      })
      .catch(() => {});
  }

  disconnect(): void {
    this.es?.close();
    this.es = null;
    if (this.flushTimer) clearInterval(this.flushTimer);
    this.flushTimer = null;
  }

  pause() {
    this.pausedFlag = true;
    this.markDirty();
  }

  resume() {
    this.pausedFlag = false;
    this.markDirty();
  }

  async sendControl(msg: Record<string, unknown>): Promise<ShipView["lastAck"]> {
    let ack: NonNullable<ShipView["lastAck"]> | null = null;
    try {
      const r = await fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(msg),
      });
      ack = (await r.json()) as NonNullable<ShipView["lastAck"]>;
      ack.ts_ms = Date.now();
    } catch (e) {
      ack = { ok: false, error: (e as Error).message, ts_ms: Date.now() } as ShipAck;
    }
    this.ship = { ...this.ship, lastAck: ack };
    this.markDirty();
    return ack;
  }

  // ---- data application ------------------------------------------------

  private apply(snap: ServerSnapshot): void {
    this.lastEventMs = Date.now();

    // A new ship run (or a new stack mission) resets the board.
    const rid = snap.stack
      ? snap.stack.sh.run_id + "|" + snap.stack.ss.run_id
      : (snap.ship?.run_id ?? null);
    if (rid !== this.runId) {
      if (this.runId !== null || rid !== null) {
        this.engines.clear();
        this.fleet.length = 0;
        this.lineMap.clear();
        this.lines = [];
        this.shipViews.clear();
        this.lastShipSeq = 0;
        this.ship = { ship: snap.ship, history: [], lastAck: null };
        this.stack = snap.stack ?? null;
        this.flight = snap.flight ?? null;
        this.counts = { ...snap.counts };
      }
      this.runId = rid;
    }

    const now = Date.now();
    const seen = new Set<string>();
    for (const [id, pair] of Object.entries(snap.latest ?? {})) {
      seen.add(id);
      const v = this.engines.get(id) ?? {
        id,
        telemetry: null,
        command: null,
        history: [],
        lastRecvMs: 0,
      };
      v.lastRecvMs = now;
      v.command = pair.command?.message as CommandMessage | null;
      const m = pair.telemetry?.message as TelemetryMessage | null;
      v.telemetry = m;
      if (m) {
        const last = v.history[v.history.length - 1];
        if (!last || m.seq > last.seq) {
          v.history.push({
            t: m.t,
            seq: m.seq,
            thrust: m.thrust_n,
            pc: m.chamber_pressure_pa,
            mdot: m.mdot_kg_s.total,
            state: m.state,
          });
          if (v.history.length > HISTORY_CAP) v.history.shift();
          this.fleetDirty = true;
        }
      }
      this.engines.set(id, v);
    }
    for (const id of [...this.engines.keys()]) {
      if (!seen.has(id)) this.engines.delete(id);
    }

    // per-vehicle ship views (stack feeds carry both stages; single feeds one)
    const ships: Record<string, ShipStatusMessage> = Object.fromEntries(
      Object.entries(
        snap.ships ?? (snap.ship ? { [snap.ship.vehicle_id]: snap.ship } : {})
      ) as Array<[string, ShipStatusMessage]>
    );
    for (const [vid, s] of Object.entries(ships ?? {})) {
      const view = this.shipViews.get(vid) ?? { ship: s, history: [], lastAck: null };
      const point: ShipPoint = {
        t: s.t,
        thrust_n: s.thrust_n,
        tank_frac: s.tank.frac,
        mdot_kg_s: s.mdot_kg_s?.total ?? 0,
        meco: s.meco,
      };
      if (s.seq > this.lastShipSeq) {
        view.history = [...view.history, point];
        if (view.history.length > SHIP_CAP) view.history = view.history.slice(-SHIP_CAP);
        this.lastShipSeq = s.seq;
      }
      this.shipViews.set(vid, { ...view, ship: s });
    }
    if (snap.stack) this.stack = snap.stack;
    if (snap.flight) this.flight = snap.flight;
    // active vessel view drives the single-ship panels & trend
    const activeId = this.stack?.active_vehicle ?? Object.keys(ships)[0];
    if (activeId && this.shipViews.has(activeId)) {
      this.ship = this.shipViews.get(activeId)!;
    } else if (snap.ship) {
      this.ship = { ...this.ship, ship: snap.ship };
    }
    this.counts = { ...snap.counts };

    // Wire rows: one stable row per (kind, engine) plus one ship row, upserted
    // in place each snapshot. Stable ids let React update the existing text
    // nodes instead of remounting the whole list every ~100ms -- that remount
    // storm (plus the slide-in animation replaying) is what looked like "flash".
    const keep = new Set<string>();
    for (const [eid, pair] of Object.entries(snap.latest ?? {})) {
      if (pair.command) {
        this.upsertLine("command", eid, pair.command.lineSummary.summary, pair.command.lineSummary.ts_ms ?? Date.now(), keep);
      }
      if (pair.telemetry) {
        this.upsertLine("telemetry", eid, pair.telemetry.lineSummary.summary, pair.telemetry.lineSummary.ts_ms ?? Date.now(), keep);
      }
    }
    for (const [vid, s] of Object.entries(ships ?? {})) {
      const summary = `${s.meco ? "MECO" : "burning"} T+${s.t.toFixed(1)}s · ${(s.thrust_n / 1e6).toFixed(1)} MN · tank ${((s.tank?.frac ?? 0) * 100).toFixed(0)}%`;
      this.upsertLine("ship", vid, summary, Date.now(), keep);
    }
    if (this.stack) {
      const st = this.stack;
      this.upsertLine(
        "stack",
        "stack",
        `stack ${st.phase} active=${st.active_vehicle ?? "-"} tank ${((st.tank?.frac ?? 0) * 100).toFixed(0)}%`,
        Date.now(),
        keep
      );
    }
    if (this.flight) {
      const fl = this.flight;
      const altKm = ((fl.position?.alt_m ?? 0) / 1000).toFixed(1);
      const orb = fl.orbit
        ? `peri ${fl.orbit.perigee_km.toFixed(0)}km`
        : (fl.crashed ? "re-entry" : "ascent");
      this.upsertLine("flight", "flight", `flight ${fl.phase} alt ${altKm}km · ${orb}`, Date.now(), keep);
    }
    for (const k of [...this.lineMap.keys()]) if (!keep.has(k)) this.lineMap.delete(k);
    const rank: Record<string, number> = { command: 0, telemetry: 1, ship: 2, stack: 3, flight: 4 };
    this.lines = [...this.lineMap.values()]
      .sort((a, b) => (rank[a.kind] ?? 9) - (rank[b.kind] ?? 9) || (a.engine_id < b.engine_id ? -1 : 1))
      .slice(0, LINE_CAP);
  }

  private upsertLine(kind: FeedLineKind, engineId: string, summary: string, tsMs: number, keep: Set<string>): void {
    const id = `${kind}|${engineId}`;
    keep.add(id);
    const row = this.lineMap.get(id);
    if (row) {
      row.summary = summary;
      row.tsMs = tsMs;
    } else {
      this.lineMap.set(id, { id, kind, engine_id: engineId, summary, tsMs });
    }
  }

  private latestTelemetry(): Map<string, TelemetryMessage> {
    const map = new Map<string, TelemetryMessage>();
    for (const [k, v] of this.engines) if (v.telemetry) map.set(k, v.telemetry);
    return map;
  }

  private rebuildSnapshot(): Snapshot {
    const engines = [...this.engines.values()].sort((a, b) => (a.id < b.id ? -1 : 1));
    return {
      v: this.snap.v + 1,
      engines,
      // fresh reference so consumers can memo on identity; the underlying
      // array is mutated in place and its identity is otherwise stable
      fleet: [...this.fleet],
      lines: this.lines,
      counts: { ...this.counts },
      ship: this.ship,
      ships: Object.fromEntries(this.shipViews),
      stack: this.stack,
      flight: this.flight,
      source: this.source,
      connected: this.connected,
      lastEventMs: this.lastEventMs,
      paused: this.pausedFlag,
    };
  }

  private fleetDirty = false;
  private lastShipSeq = 0;
  private flushTimer: ReturnType<typeof setInterval> | null = null;

  private markDirty() {
    this.dirty = true;
    for (const fn of this.listeners) fn();
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => void this.listeners.delete(fn);
  };

  getSnapshot = (): Snapshot => this.snap;
}

export const clientFeed = new ClientFeed();
