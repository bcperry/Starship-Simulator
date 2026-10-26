// Server-side feed state: buffers live events, coalesces into 100 ms flushes,
// and keeps a reconnection snapshot (latest per engine + recent tail).

import type {
  CommandMessage,
  FeedEvent,
  TelemetryMessage,
} from "../shared/types";

interface Held {
  msg: TelemetryMessage | CommandMessage;
  recv_ms: number;
}

const MAX_TAIL = 400;

export type FeedListener = (batch: FeedEvent[]) => void;

export class FeedStore {
  readonly latestTelemetry = new Map<string, Held>();
  readonly latestCommand = new Map<string, Held>();
  readonly counts = { command: 0, telemetry: 0, total: 0 };

  private listeners = new Set<FeedListener>();
  private buffer: FeedEvent[] = [];
  private timer: NodeJS.Timeout | null = null;
  private tail: FeedEvent[] = [];

  start(flushMs: number = 100): void {
    if (this.timer) throw new Error("store already started");
    this.timer = setInterval(() => this.flush(), flushMs);
    this.timer.unref();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.flush();
  }

  ingest(ev: FeedEvent): void {
    this.buffer.push(ev);
    this.counts[ev.kind] += 1;
    this.counts.total += 1;
  }

  /** Recent events not yet flushed (used for one-shot snapshots). */
  get pending(): readonly FeedEvent[] {
    return this.tail;
  }

  /** Coalesce buffered events, update "latest" maps, notify subscribers. */
  flush(): void {
    if (this.buffer.length === 0) return;
    const batch = this.buffer;
    this.buffer = [];

    for (const ev of batch) {
      if (ev.kind === "telemetry") {
        this.latestTelemetry.set(ev.engine_id, { msg: ev.msg, recv_ms: ev.recv_ms });
      } else {
        this.latestCommand.set(ev.engine_id, { msg: ev.msg, recv_ms: ev.recv_ms });
      }
      this.tail.push(ev);
    }
    if (this.tail.length > MAX_TAIL) this.tail.splice(0, this.tail.length - MAX_TAIL);

    for (const fn of this.listeners) fn(batch);
  }

  subscribe(fn: FeedListener): () => void {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  }

  /** Reconnection payload: current best-known state of every engine. */
  snapshot(): {
    generated_ms: number;
    engines: { telemetry?: Held; command?: Held }[];
  } {
    const ids = new Set([
      ...this.latestTelemetry.keys(),
      ...this.latestCommand.keys(),
    ]);
    const engines = [...ids].sort().map((id) => ({
      telemetry: this.latestTelemetry.get(id),
      command: this.latestCommand.get(id),
    }));
    return { generated_ms: Date.now(), engines };
  }
}
