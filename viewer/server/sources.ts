/*
 Pluggable ship/engine feed for the viewer. 
  
 - ``mock``: spawns the Python ship sim (simulator.shiploop, stdio transport) 
   as a child process. Real physics, shared fuel, per-engine variability, and 
   full run control -- no broker needed. 
 - ``kafka``: consumes real engine.command / engine.telemetry / ship.status 
   from the broker; controls are produced onto the ship control topic. 
  
 Both implement the same Source interface, so the SSE server and the client 
 don't care which is wired in. 
 " */

import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import kafkajs from "kafkajs";

import {
  CommandMessage,
  ENGINE_COMMAND,
  ENGINE_TELEMETRY,
  FLIGHT_STATE,
  FlightStateMessage,
  FeedEvent,
  LineSummary,
  SHIP_CONTROL,
  SHIP_STATUS,
  ShipAck,
  ShipControlMessage,
  TelemetryMessage,
} from "../shared/types.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = process.env.REPO_ROOT ?? path.resolve(here, "..", "..");

export type SourceKind = "mock" | "kafka" | "stack" | "flight";

export interface Source {
  start(onEvent: (ev: FeedEvent) => void): void;
  stop(): void;
  isClosed(): boolean;
  sendControl(msg: ShipControlMessage): Promise<unknown>;
  restart(onEvent: (ev: FeedEvent) => void): void;
}

function thrustMN(n: number): string {
  return (n / 1e6).toFixed(3);
}

// ---------------- mock: the real ship sim, driven over a pipe ----------------

export class ShipProcessSource implements Source {
  private proc: ChildProcess | null = null;
  private buf = "";
  private onEvent: ((ev: FeedEvent) => void) | null = null;
  private lastAck: ShipAck | null = null;
  private ackWaiters: Array<{ command: string; resolve: (ack: ShipAck) => void }> = [];
  private args: string[];
  private module = "simulator.shiploop";
  private dead = false;

  constructor(cfg: { vehicleId: string; speed?: number; emitEvery?: number; stack?: boolean; flight?: boolean }) {
    if (cfg.flight) {
      // Phase-9 orbital mission: Super Heavy + Starship driving a real ECI
      // trajectory, emitting flight.state (position / orbit elements / staging)
      // alongside the per-engine + ship + stack documents.
      this.args = [
        "--seed", process.env.SHIP_SEED ?? "7",
        "--dt", "0.01",
        "--speed", String(cfg.speed ?? 1),
        "--emit-every", String(cfg.emitEvery ?? 2),
        "--log-every", "0", // silence the console status lines under the viewer
        "--sh-failures", process.env.SHIP_SH_FAILURES ?? "0",
        "--ss-failures", process.env.SHIP_SS_FAILURES ?? "0",
      ];
      this.module = "simulator.flightloop";
      return;
    }
    const a = cfg.stack
      ? [
          // full stack: Super Heavy (33) + Starship (3x VAC + 3x SL)
          "--seed", "7",
          "--dt", "0.01",
          "--speed", String(cfg.speed ?? 1),
          "--emit-every", String(cfg.emitEvery ?? 2),
          "--log-every", "0", // silence the console status lines under the viewer
        ]
      : [
          "--vehicle-id", cfg.vehicleId,
          "--engines", "33",
          "--dt", "0.01",
          "--speed", String(cfg.speed ?? 1),
          "--emit-every", String(cfg.emitEvery ?? 2),
          "--log-every", "0", // silence the console status lines under the viewer
        ];
    this.args = a;
    this.module = cfg.stack ? "simulator.stackloop" : "simulator.shiploop";
  }

  private spawnChild(onEvent: (ev: FeedEvent) => void): void {
    this.dead = false;
    this.buf = "";
    // Prefer the project venv's python directly -- faster start, simple stdio.
    // Fall back to `uv run` when the venv is missing.
    const venvPy = path.join(REPO_ROOT, ".venv", "bin", "python");
    const py = process.env.SHIP_PYTHON_BIN ?? (fs.existsSync(venvPy) ? venvPy : "uv");
    const isUv = path.basename(py) === "uv";
    const args = isUv
      ? ["run", "python", "-m", this.module, ...this.args]
      : ["-m", this.module, ...this.args];
    const proc = spawn(py, args, { cwd: REPO_ROOT, stdio: ["pipe", "pipe", "pipe"] });
    this.proc = proc;
    proc.stdout?.setEncoding("utf8");
    proc.stdout?.on("data", (chunk: string) => {
      this.buf += chunk;
      let i: number;
      while ((i = this.buf.indexOf("\n")) >= 0) {
        const line = this.buf.slice(0, i).trim();
        this.buf = this.buf.slice(i + 1);
        if (!line) continue;
        let doc: any;
        try {
          doc = JSON.parse(line);
        } catch {
          // never drop silently -- it hides wire bugs (e.g. Python
          // serialising Infinity/NaN into otherwise-valid documents)
          try {
            fs.appendFileSync(
              process.env.SHIP_LOG ?? "/tmp/ship-err.log",
              `[${new Date().toISOString()}] dropped malformed line: ${line.slice(0, 300)}\n`,
            );
          } catch {
            /* ignore */
          }
          continue;
        }
        if (doc?.type === "ship.ack") {
          this.lastAck = doc as ShipAck;
          const w = this.ackWaiters.shift();
          if (w) w.resolve(doc as ShipAck);
          continue;
        }
        const ev = mockToFeedEvent(doc);
        if (ev) onEvent(ev);
      }
    });
    proc.stderr?.on("data", (d: Buffer) => {
      try {
        fs.appendFileSync(process.env.SHIP_LOG ?? "/tmp/ship-err.log", d);
      } catch {
        /* ignore */
      }
    });
    proc.on("exit", () => {
      this.dead = true;
    });
  }

  start(onEvent: (ev: FeedEvent) => void): void {
    this.onEvent = onEvent;
    this.spawnChild(onEvent);
  }

  stop(): void {
    if (this.proc?.pid != null) {
      try {
        this.proc.kill("SIGTERM");
      } catch {
        /* already gone */
      }
    }
  }

  isClosed(): boolean {
    return (this.proc?.exitCode != null) || this.dead;
  }

  async sendControl(msg: ShipControlMessage): Promise<unknown> {
    if (this.proc == null || this.proc.exitCode != null) {
      // (re)start the ship if it isn't alive, then send
      if (this.onEvent) this.spawnChild(this.onEvent);
      else throw new Error("ship process not started");
    }
    return new Promise((resolve) => {
      const waiter = { command: msg.command, resolve };
      this.ackWaiters.push(waiter);
      this.proc!.stdin!.write(JSON.stringify(msg) + "\n");
      setTimeout(() => {
        const i = this.ackWaiters.indexOf(waiter);
        if (i >= 0) {
          this.ackWaiters.splice(i, 1);
          // A stale ack from an earlier command must never be reported as a
          // success for this one.
          resolve({ type: "ship.ack", ok: false, command: msg.command, error: "no ack from ship (stdin pipe may be broken)" });
        }
      }, 4000);
    });
  }

  restart(onEvent: (ev: FeedEvent) => void): void {
    this.stop();
    this.start(onEvent);
  }
}

function mockToFeedEvent(doc: any): FeedEvent | null {
  if (doc?.type === ENGINE_COMMAND && typeof doc.throttle_demand_frac === "number") {
    const msg = doc as CommandMessage;
    const line: LineSummary = {
      kind: "command",
      t: msg.t,
      summary: `cmd ${msg.engine_id} #${msg.seq} thr ${msg.throttle_demand_frac}`,
      engine_id: msg.engine_id,
      seq: msg.seq,
    };
    return { kind: "command", message: msg, lineSummary: line };
  }
  if (doc?.type === ENGINE_TELEMETRY && typeof doc.thrust_n === "number") {
    const msg = doc as TelemetryMessage;
    const mdot = msg.mdot_kg_s ?? { total: 0, lox: 0, ch4: 0 };
    const line: LineSummary = {
      kind: "telemetry",
      t: msg.t,
      summary: `${msg.engine_id} #${msg.seq} ${msg.state} ${thrustMN(msg.thrust_n)}MN mdot ${mdot.total.toFixed(1)}kg/s`,
      engine_id: msg.engine_id,
      seq: msg.seq,
    };
    return { kind: "telemetry", message: msg, lineSummary: line };
  }
  if (doc?.type === SHIP_STATUS && doc.tank) {
    const msg = doc;
    const line: LineSummary = {
      kind: "ship",
      t: msg.t,
      summary: `ship #${msg.seq} ${msg.meco ? "MECO" : `${thrustMN(msg.thrust_n)}MN`} tank ${(msg.tank.frac * 100).toFixed(1)}%`,
      engine_id: msg.vehicle_id,
      seq: msg.seq,
    };
    return { kind: "ship", message: msg, lineSummary: line };
  }
  if (doc?.type === "stack.status") {
    const msg = doc;   // StackStatusMessage (shared/types)
    const line: LineSummary = {
      kind: "stack",
      t: msg.t,
      summary: `stack ${msg.phase} active=${msg.active_vehicle ?? "-"} tank ${((msg.tank?.frac ?? 0) * 100).toFixed(1)}%`,
      engine_id: "stack",
      seq: 0,
    };
    return { kind: "stack", message: msg, lineSummary: line };
  }
  if (doc?.type === FLIGHT_STATE) {
    const msg = doc as FlightStateMessage;
    const altKm = (msg.position?.alt_m ?? 0) / 1000;
    const orb = msg.orbit;
    const orbitTxt = orb
      ? `peri ${orb.perigee_km.toFixed(0)}km · apo ${orb.apogee_km.toFixed(0)}km`
      : (msg.crashed ? "re-entry" : "ascent");
    const line: LineSummary = {
      kind: "flight",
      t: msg.t,
      summary: `flight ${msg.phase} alt ${altKm.toFixed(1)}km · ${orbitTxt}`,
      engine_id: "flight",
      seq: 0,
      ts_ms: Date.now(),
    };
    return { kind: "flight", message: msg, lineSummary: line };
  }
  return null;
}

// ---------------- kafka: the real broker-fed path ----------------

export class KafkaSource implements Source {
  private client: any = null;
  private consumer: any = null;
  private producer: any = null;
  private running = false;
  private closed = false;

  constructor(
    opts: { broker: string; vehicleTopic: string; commandTopic: string; controlTopic: string; vehicleId: string },
  ) {
    const K = kafkajs.Kafka;
    this.client = new K({
      brokers: [opts.broker],
      "clientId": "ts-viewer",
      retry: {
        initialRetryTime: 200,
        maxRetryTime: 2000,
        retries: 8,
        factor: 2,
      },
    });
    this.consumer = this.client.consumer({
      groupId: `viewer-${opts.vehicleId}`,
      "autoCommit": true,
    });
    this.vehicleTopic = opts.vehicleTopic;
    this.commandTopic = opts.commandTopic;
    this.controlTopic = opts.controlTopic;
    this.vehicleId = opts.vehicleId;
  }

  private vehicleTopic: string;
  private commandTopic: string;
  private controlTopic: string;
  private vehicleId: string;

  start(onEvent: (ev: FeedEvent) => void): void {
    if (this.running) return;
    this.running = true;
    this.closed = false;
    void (async () => {
      try {
        await this.consumer.connect();
        await this.consumer.subscribe({ topic: this.vehicleTopic, fromBeginning: true });
        await this.consumer.subscribe({ topic: this.commandTopic, fromBeginning: true });
        await this.consumer.run({
          "eachBatch": async (payload: any) => {
            const batch = payload.batch ?? payload;
            for (const r of batch.messages) {
              const ev = toFeedEvent(r);
              if (ev) onEvent(ev);
            }
          },
        });
      } catch (err) {
        this.running = false;
        this.closed = true;
        console.error("kafka source start failed:", (err as Error).message);
      }
    })();
  }

  stop(): void {
    this.running = false;
    void (async () => {
      try { await this.consumer?.close(); } catch { /* ignore */ }
      try { await this.producer?.disconnect(); } catch { /* ignore */ }
    })();
  }

  isClosed(): boolean {
    return this.closed;
  }

  async sendControl(msg: ShipControlMessage): Promise<unknown> {
    return new Promise((resolve, reject) => {
      void (async () => {
        try {
          if (!this.producer) this.producer = this.client.producer();
          await this.producer.connect();
          await this.producer.send({
            topic: this.controlTopic,
            messages: [{ key: msg.vehicle_id, value: JSON.stringify(msg) }],
          });
          resolve({ type: "ship.ack", ok: true, note: "queued to control topic" });
        } catch (e) {
          reject(e as Error);
        }
      })();
    });
  }

  restart(onEvent: (ev: FeedEvent) => void): void {
    this.stop();
    this.start(onEvent);
  }
}

function toFeedEvent(raw: any): FeedEvent | null {
  const doc = raw?.value
    ? ((): any => { try { return JSON.parse(raw.value.toString("utf8")); } catch { return null; } })()
    : raw;
  if (!doc || typeof doc !== "object") return null;

  if (doc.type === ENGINE_COMMAND) {
    const msg = doc as CommandMessage;
    const line: LineSummary = {
      kind: "command",
      t: msg.t,
      summary: `cmd ${msg.engine_id} #${msg.seq} thr ${msg.throttle_demand_frac}`,
      engine_id: msg.engine_id,
      seq: msg.seq,
    };
    return { kind: "command", message: msg, lineSummary: line };
  }
  if (doc.type === ENGINE_TELEMETRY) {
    const msg = doc as TelemetryMessage;
    const mdot = msg.mdot_kg_s ?? { total: 0, lox: 0, ch4: 0 };
    const line: LineSummary = {
      kind: "telemetry",
      t: msg.t,
      summary: `${msg.engine_id} #${msg.seq} ${msg.state} ${thrustMN(msg.thrust_n)}MN mdot ${mdot.total.toFixed(1)}kg/s`,
      engine_id: msg.engine_id,
      seq: msg.seq,
    };
    return { kind: "telemetry", message: msg, lineSummary: line };
  }
  if (doc.type === SHIP_STATUS) {
    const msg = doc;
    const line: LineSummary = {
      kind: "ship",
      t: msg.t,
      summary: `ship #${msg.seq} ${msg.meco ? "MECO" : `${thrustMN(msg.thrust_n)}MN`} tank ${(msg.tank.frac * 100).toFixed(1)}%`,
      engine_id: msg.vehicle_id,
      seq: msg.seq,
    };
    return { kind: "ship", message: msg, lineSummary: line };
  }
  return null;
}

// ---------------- factory ----------------

export function makeSource(kind: SourceKind, opts: Record<string, string | undefined>): Source {
  if (kind === "kafka") {
    const broker = opts.broker ?? process.env.BOOTSTRAP_SERVERS ?? "localhost:9092";
    const vehicleId = process.env.VEHICLE_ID ?? "starship";
    return new KafkaSource({
      broker,
      vehicleTopic: opts.vehicleTopic ?? "starship.engine.telemetry",
      commandTopic: opts.commandTopic ?? "starship.engine.command",
      controlTopic: opts.controlTopic ?? "starship.ship.control",
      vehicleId,
    });
  }
  return new ShipProcessSource({
    vehicleId: process.env.VEHICLE_ID ?? "starship",
    speed: process.env.SHIP_SPEED ? Number(process.env.SHIP_SPEED) : undefined,
    emitEvery: process.env.SHIP_EMIT_EVERY ? Number(process.env.SHIP_EMIT_EVERY) : undefined,
    stack: kind === "stack",
    flight: kind === "flight",
  });
}
