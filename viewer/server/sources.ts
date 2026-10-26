// Feed sources. Both implement the same on-emit interface so the server
// doesn't care where data comes from:
//   - MockSource  : in-process 33-engine fleet (develop the UX with no broker)
//   - KafkaSource : real broker, consuming the command + telemetry topics

import { randomUUID } from "node:crypto";

import {
  ENGINE_COMMAND,
  ENGINE_TELEMETRY,
  type CommandMessage,
  type FeedEvent,
  type TelemetryMessage,
} from "../shared/types";

export interface Source {
  readonly name: "mock" | "kafka";
  start(): Promise<void>;
  stop(): Promise<void>;
}

// --- mock -----------------------------------------------------------------

// A deliberately simple stand-in for the Raptor fleet physics — enough to
// exercise the full UX: spool-up lag, full-flow telemetry at 10 Hz, one
// engine (sh-32) with a small load that starves and cuts, live command echo.
const N = 33;
const DT = 0.01;
const TAU = 0.05;
const P_LOAD = 3.0e7;
const P_0 = 101325;
const FULL_THRUST = 1.76e6;
const G0 = 9.81;
const ISP_FULL = 350;
const FULL_MDOT = FULL_THRUST / (ISP_FULL * G0); // ~= 51 kg/s
const MIXTURE = 3.6;

interface MockEngine {
  id: string;
  load: number;
  load0: number;
  pc: number;
  cut: boolean;
}

export class MockSource implements Source {
  readonly name = "mock";
  private timer: NodeJS.Timeout | null = null;
  private engines: MockEngine[] = [];
  private step = 0;
  private jitter = 0;

  constructor(private readonly emit: (ev: FeedEvent) => void) {}

  async start(): Promise<void> {
    this.engines = [];
    for (let i = 1; i <= N; i++) {
      const id = `sh-${String(i).padStart(2, "0")}`;
      // sh-32 carries a small load: starves and cuts ~10 s in, like the demo.
      const load = i === N ? 500 : 270_000; // kg
      this.engines.push({ id, load, load0: load, pc: P_0, cut: false });
    }
    this.timer = setInterval(() => this.tick(), DT * 1000);
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private tick(): void {
    this.step += 1;
    const step = this.step;
    const t = step * DT;
    const ts = Date.now();
    this.jitter = (Math.random() - 0.5) * 0.004;

    for (const e of this.engines) {
      // command out (vehicle side)
      this.emit({
        kind: "command",
        engine_id: e.id,
        recv_ms: ts,
        msg: {
          v: 1,
          type: ENGINE_COMMAND,
          vehicle_id: "starship",
          engine_id: e.id,
          seq: step,
          t: Number((t - DT).toFixed(4)),
          dt: DT,
          throttle_demand_frac: 1.0,
          ambient_pressure_pa: P_0,
          ts_ms: ts,
        } as CommandMessage,
      });

      // physics step (spool-up lag; starvation -> cut; decay after cut)
      if (e.cut) {
        e.pc *= Math.exp(-DT / TAU);
      } else {
        e.pc += (P_LOAD - e.pc) * (DT / TAU);
        e.load -= FULL_MDOT * (e.pc / P_LOAD) * DT;
        if (e.load <= 0.01 * e.load0) {
          e.cut = true;
        }
      }
      const thrust = (e.cut ? 0 : (e.pc / P_LOAD) * FULL_THRUST) * (1 + this.jitter);
      const mdotTotal = thrust / (ISP_FULL * G0);
      const mdotLox = mdotTotal * MIXTURE / (1 + MIXTURE);
      const isp = thrust / (mdotTotal * G0);
      const state = !e.cut && thrust > 1 ? "firing" : e.cut ? "cut" : "idle";

      // telemetry out (engine side)
      this.emit({
        kind: "telemetry",
        engine_id: e.id,
        recv_ms: ts,
        msg: {
          v: 1,
          type: ENGINE_TELEMETRY,
          vehicle_id: "starship",
          engine_id: e.id,
          seq: step,
          t: Number(t.toFixed(4)),
          ts_ms: ts,
          state,
          throttle_demand_frac: 1.0,
          throttle_actual_frac: e.pc / P_LOAD,
          chamber_pressure_pa: e.pc,
          thrust_n: thrust,
          isp_s: Number.isFinite(isp) ? isp : ISP_FULL,
          mdot_kg_s: {
            total: mdotTotal,
            lox: mdotLox,
            ch4: mdotTotal - mdotLox,
          },
          exhaust: {
            velocity_m_s: isp * G0,
            exit_pressure_pa: P_0,
            exit_mach: (isp * G0) / 343,
          },
          propellant_remaining_kg: e.cut ? Math.max(e.load0 * 0.01, e.load) : e.load,
          ambient_pressure_pa: P_0,
          command_seq: step,
        } as TelemetryMessage,
      });
    }
  }
}

// --- kafka ------------------------------------------------------------------

const JSON_KEY = "utf8";

export interface KafkaSourceOpts {
  bootstrapServers: string;
  commandTopic: string;
  telemetryTopic: string;
}

export class KafkaSource implements Source {
  readonly name = "kafka";
  private client: { disconnect?: () => Promise<void> } | null = null;
  private consumers: Array<{
    disconnect?: () => Promise<void>;
  }> = [];

  constructor(
    private readonly opts: KafkaSourceOpts,
    private readonly emit: (ev: FeedEvent) => void,
    private readonly log: (line: string) => void = () => {},
  ) {}

  async start(): Promise<void> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const kafkaModule: any = await import("kafkajs").then((m) => m.default ?? m);
    const kafka = new kafkaModule.Kafka({
      clientId: `sim-viewer-${randomUUID().slice(0, 8)}`,
      brokers: [this.opts.bootstrapServers],
      retry: { initialBackoff: 200, maxBackoff: 2000, retries: 12 },
    });
    this.client = kafka;

    const specs: [string, "command" | "telemetry"][] = [
      [this.opts.commandTopic, "command"],
      [this.opts.telemetryTopic, "telemetry"],
    ];
    for (const [topic, kind] of specs) {
      const consumer = kafka.consumer({
        groupId: `sim-viewer-${kind}`,
        "auto.offset.reset": "earliest",
        allowAutoTopicCreation: true,
      });
      this.consumers.push(consumer);
      consumer.on(consumer.events.CRASH, (e: { message: string }) =>
        this.log(`consumer(${topic}) crash: ${e.message}`),
      );
      await consumer
        .run({
          autoCommit: true,
          eachBatch: async (payload: TopicPartitionFetchBatch) => {
            for (const msg of payload.messages) {
              if (!msg.value) continue;
              const event = toFeedEvent(kind, msg.value);
              if (event) this.emit(event);
            }
          },
        })
        .catch((err: Error) => this.log(`consumer(${topic}) run: ${err.message}`));
    }
    this.log(
      `kafka source up (broker=${this.opts.bootstrapServers}, ` +
        `command=${this.opts.commandTopic}, telemetry=${this.opts.telemetryTopic})`,
    );
  }

  async stop(): Promise<void> {
    for (const c of this.consumers) await c.disconnect?.().catch(() => {});
    await this.client?.disconnect?.().catch(() => {});
  }
}

type TopicPartitionFetchBatch = { messages: { value: Buffer | null }[] };

function toFeedEvent(
  kind: "command" | "telemetry",
  raw: Buffer,
): FeedEvent | null {
  let json: any;
  try {
    json = JSON.parse(raw.toString(JSON_KEY));
  } catch {
    return null;
  }
  if (json.v !== 1 || typeof json.engine_id !== "string") return null;
  const want = kind === "command" ? ENGINE_COMMAND : ENGINE_TELEMETRY;
  if (json.type !== want) return null;
  return {
    kind,
    engine_id: json.engine_id,
    msg: json,
    recv_ms: Date.now(),
  };
}
