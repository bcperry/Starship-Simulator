// Client feed store: keeps an EventSource open to /api/stream, folds every
// event into (a) per-engine latest/history state, (b) a fleet trend point per
// flush, (c) a raw message log. Exposes a stable snapshot per flush so React
// can useSyncExternalStore without re-rendering per message.

import { fleetSummary, type FleetSummary } from "./lib/aggregate";
import type {
  CommandMessage,
  FeedEvent,
  TelemetryMessage,
} from "@shared/types";

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

export type FeedLineKind = "command" | "telemetry";

export interface FeedLine {
  id: number;
  tsMs: number;
  kind: FeedLineKind;
  engine_id: string;
  summary: string;
  raw: FeedEvent;
}

export interface Snapshot {
  v: number;
  engines: EngineView[];
  fleet: FleetPoint[];
  lines: FeedLine[];
  counts: { command: number; telemetry: number };
  source: string | null;
  connected: boolean;
  lastEventMs: number;
  paused: boolean;
}

const HISTORY_CAP = 480; // ~48 s at 10 Hz
const FLEET_CAP = 900;
const LINE_CAP = 400;
const PENDING_CAP = 4000;

function summarize(ev: FeedEvent): string {
  if (ev.kind === "command") {
    const c = ev.msg;
    return `${c.engine_id} → ${Math.round(c.throttle_demand_frac * 100)}% @ t=${c.t}s (seq ${c.seq})`;
  }
  const m = ev.msg;
  return `${m.engine_id} ${m.state} ${((m.thrust_n / 1e6).toFixed(2))} MN ${(m.chamber_pressure_pa / 1e6).toFixed(1)} MPa (seq ${m.seq})`;
}

interface HeldSnapshot {
  generated_ms: number;
  engines: { telemetry?: { msg: any; recv_ms: number }; command?: { msg: any; recv_ms: number } }[];
}

interface ServerPayload {
  type: string;
  source?: string;
  snapshot?: HeldSnapshot;
  tail?: FeedEvent[];
  events?: FeedEvent[];
}

class ClientFeed {
  private es: EventSource | null = null;
  private dirty = false;
  private pausedFlag = false;
  private pending: FeedEvent[] = [];

  private engines = new Map<string, EngineView>();
  private fleet: FleetPoint[] = [];
  private lines: FeedLine[] = [];
  private counts = { command: 0, telemetry: 0 };
  private source: string | null = null;
  private connected = false;
  private lastEventMs = 0;
  private lineSeq = 0;
  private fleetDirty = false;
  private flushTimer: ReturnType<typeof setInterval> | null = null;

  private snap: Snapshot = {
    v: 0,
    engines: [],
    fleet: [],
    lines: [],
    counts: { command: 0, telemetry: 0 },
    source: null,
    connected: false,
    lastEventMs: 0,
    paused: false,
  };

  // ---- lifecycle ------------------------------------------------------

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
      // EventSource auto-reconnects (server sends `retry: 3000`)
    };
    es.onmessage = (e) => {
      let payload: ServerPayload;
      try {
        payload = JSON.parse(e.data) as ServerPayload;
      } catch {
        return;
      }
      this.lastEventMs = Date.now();
      if (payload.type === "hello") {
        if (payload.source) this.source = payload.source;
        if (payload.snapshot) this.seed(payload.snapshot);
        if (payload.tail) for (const ev of payload.tail) this.apply(ev);
        this.markDirty();
      } else if (payload.type === "events") {
        for (const ev of payload.events ?? []) this.apply(ev);
        this.markDirty();
      }
    };

    // flush loop (10 Hz) so React renders at most once per flush
    const flush = setInterval(() => {
      this.flushPending();
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
  }

  disconnect(): void {
    this.es?.close();
    this.es = null;
    if (this.flushTimer) clearInterval(this.flushTimer);
    this.flushTimer = null;
  }

  pause(): void {
    this.pausedFlag = true;
    this.markDirty();
  }

  resume(): void {
    this.pausedFlag = false;
    this.markDirty();
  }

  // ---- data application -----------------------------------------------

  private seed(snap: HeldSnapshot): void {
    // Reset state on a fresh connection's prime payload.
    this.engines.clear();
    this.fleet.length = 0;
    this.lines.length = 0;
    this.counts = { command: 0, telemetry: 0 };
    for (const e of snap.engines) {
      const v: EngineView = {
        id: e.telemetry?.msg?.engine_id ?? e.command?.msg?.engine_id ?? "?",
        telemetry: e.telemetry?.msg ?? null,
        command: e.command?.msg ?? null,
        history: [],
        lastRecvMs: e.telemetry?.recv_ms ?? e.command?.recv_ms ?? 0,
      };
      if (v.telemetry) {
        const m = v.telemetry;
        v.history.push({
          t: m.t,
          seq: m.seq,
          thrust: m.thrust_n,
          pc: m.chamber_pressure_pa,
          mdot: m.mdot_kg_s.total,
          state: m.state,
        });
      }
      this.engines.set(v.id, v);
    }
  }

  private apply(ev: FeedEvent): void {
    if (this.pausedFlag) {
      this.pending.push(ev);
      if (this.pending.length > PENDING_CAP) this.pending.shift();
      return;
    }
    const key = ev.engine_id;
    const v = this.engines.get(key) ?? {
      id: key,
      telemetry: null,
      command: null,
      history: [],
      lastRecvMs: 0,
    };
    v.lastRecvMs = ev.recv_ms;

    if (ev.kind === "telemetry") {
      const m = ev.msg;
      v.telemetry = m;
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
      }
      this.counts.telemetry += 1;
    } else {
      v.command = ev.msg;
      this.counts.command += 1;
    }
    this.fleetDirty = true;
    this.engines.set(key, v);

    this.lines.unshift({
      id: ++this.lineSeq,
      tsMs: ev.recv_ms,
      kind: ev.kind,
      engine_id: key,
      summary: summarize(ev),
      raw: ev,
    });
    if (this.lines.length > LINE_CAP) this.lines.length = LINE_CAP;
  }

  private latestTelemetry(): Map<string, TelemetryMessage> {
    const map = new Map<string, TelemetryMessage>();
    for (const [k, v] of this.engines) if (v.telemetry) map.set(k, v.telemetry);
    return map;
  }

  private flushPending(): void {
    if (this.pausedFlag || this.pending.length === 0) return;
    const batch = this.pending.splice(0, this.pending.length);
    for (const ev of batch) this.apply(ev);
  }

  // ---- snapshot for React ----------------------------------------------

  private rebuildSnapshot(): Snapshot {
    const engines = [...this.engines.values()].sort((a, b) =>
      a.id < b.id ? -1 : 1,
    );
    return {
      v: this.snap.v + 1,
      engines,
      fleet: this.fleet,
      lines: this.lines,
      counts: { ...this.counts },
      source: this.source,
      connected: this.connected,
      lastEventMs: this.lastEventMs,
      paused: this.pausedFlag,
    };
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private listeners = new Set<() => void>();

  private markDirty(): void {
    this.dirty = true;
  }

  getSnapshot = (): Snapshot => this.snap;
}

export const clientFeed = new ClientFeed();
