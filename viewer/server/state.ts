/*
 In-memory fleet state: latest engine.cmd + engine.tel per engine, the newest
 ship.status, a rolling summary log, and coalesced flushes to SSE clients.
 */

import {
  CommandMessage,
  FeedEvent,
  LineSummary,
  ShipStatusMessage,
  StackStatusMessage,
  TelemetryMessage,
} from "../shared/types.js";
import type { Source } from "./sources.js";

export interface Held {
  lineSummary: LineSummary;
  message: TelemetryMessage | CommandMessage;
}

interface Subscriber {
  send: (data: string) => void;
}

export class FeedStore {
  latestTel = new Map<string, Held>();
  latestCmd = new Map<string, Held>();
  ship: ShipStatusMessage | null = null; // newest (any vessel) ship.status
  ships = new Map<string, ShipStatusMessage>(); // per-vehicle (stack mode)
  stack: StackStatusMessage | null = null;
  summaries: LineSummary[] = [];
  counts = { command: 0, telemetry: 0, ship: 0 };

  private clients = new Set<Subscriber>();
  private runKey: string | null = null;

  constructor(private source: Source) {
    this.source.start((ev) => this.ingest(ev));
  }

  private stackMode = false;

  /**
   New mission? Wipe every previous-run doc before the new ones land.

   Canonical run key = the FIRST stage's run id (stack feeds: from the
   stack.status; ship feeds: the ship.status). The Starship upper-stage
   ship doc carries its own (different) run id, so it never keys -- the
   two stages are re-armed together and the first stage's id changes in
   lockstep with the mission.
   */
  private noteRun(ev: FeedEvent): void {
    let key: string | null = null;
    if (ev.kind === "stack") {
      this.stackMode = true;
      key = ev.message.sh.run_id;
    } else if (ev.kind === "ship" && !this.stackMode) {
      key = ev.message.run_id;
    }
    if (key !== null && key !== this.runKey) {
      if (this.runKey !== null) {
        // previous run's engines / ship docs / per-vehicle failures are stale
        this.latestTel.clear();
        this.latestCmd.clear();
        this.ships.clear();
        this.ship = null;
        this.stack = null;
        this.summaries.length = 0;
        this.counts = { command: 0, telemetry: 0, ship: 0 };
      }
      this.runKey = key;
    }
  }

  ingest(ev: FeedEvent): void {
    this.noteRun(ev);
    if (ev.kind === "ship") {
      this.ship = ev.message;
      this.ships.set(ev.message.vehicle_id, ev.message);
      this.counts.ship += 1;
    } else if (ev.kind === "stack") {
      this.stack = ev.message;
    } else if (ev.kind === "command") {
      this.latestCmd.set(ev.message.engine_id, {
        lineSummary: ev.lineSummary,
        message: ev.message,
      });
      this.counts.command += 1;
    } else {
      this.latestTel.set(ev.message.engine_id, {
        lineSummary: ev.lineSummary,
        message: ev.message,
      });
      this.counts.telemetry += 1;
    }
    this.summaries.push(ev.lineSummary);
    if (this.summaries.length > 80) this.summaries.shift();
  }

  engineCount(): number {
    return new Set([...this.latestTel.keys(), ...this.latestCmd.keys()]).size;
  }

  snapshot() {
    const ids = new Set([...this.latestTel.keys(), ...this.latestCmd.keys()]);
    const latest: Record<string, { telemetry?: Held; command?: Held }> = {};
    for (const id of ids) {
      latest[id] = {
        telemetry: this.latestTel.get(id),
        command: this.latestCmd.get(id),
      };
    }
    return {
      latest,
      ship: this.ship,
      ships: Object.fromEntries(this.ships),
      stack: this.stack,
      lines: this.summaries.slice(-40),
      counts: { ...this.counts, engines: this.engineCount() },
      ts_ms: Date.now(),
    };
  }

  snapshotJSON(): string {
    return JSON.stringify(this.snapshot());
  }

  subscribe(c: Subscriber): () => void {
    this.clients.add(c);
    c.send(this.snapshotJSON());
    return () => void this.clients.delete(c);
  }

  /** Coalesce everything buffered since the last flush to all live clients. */
  flush(): void {
    if (this.source.isClosed()) {
      // ship process died (MECO exit / killed) -- respawn it, fresh run
      this.source.restart((ev) => this.ingest(ev));
    }
    if (this.clients.size === 0) return;
    const snap = this.snapshotJSON();
    for (const c of this.clients) c.send(snap);
  }

  sendControl(msg: Parameters<Source["sendControl"]>[0]): Promise<unknown> {
    // Normalise to the full ship.control wire doc once, for every transport:
    // callers may post a bare { command, ...args } body (browser, curl, relay).
    const body: Record<string, unknown> = {
      v: 1,
      type: "ship.control",
      vehicle_id: "starship",
      ts_ms: Date.now(),
      ...(msg as unknown as Record<string, unknown>),
    };
    return this.source.sendControl(body as never);
  }
}
