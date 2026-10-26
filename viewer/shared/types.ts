// Wire types — TS mirrors of simulator/telemetry/messages.py (schema v1).
// Keep in sync with the Python side; the server validates `v` and `type`.

export const SCHEMA_VERSION = 1;
export const ENGINE_TELEMETRY = "engine.telemetry";
export const FLEET_TELEMETRY = "fleet.telemetry";
export const ENGINE_COMMAND = "engine.command";

export type EngineStateWord = "idle" | "firing" | "cut";

export interface MDot {
  total: number;
  lox: number;
  ch4: number;
}

export interface Exhaust {
  velocity_m_s: number;
  exit_pressure_pa: number;
  exit_mach: number;
}

export interface TelemetryMessage {
  v: number;
  type: typeof ENGINE_TELEMETRY;
  vehicle_id: string;
  engine_id: string;
  seq: number;
  t: number;
  ts_ms: number;
  state: EngineStateWord;
  throttle_demand_frac: number;
  throttle_actual_frac: number;
  chamber_pressure_pa: number;
  thrust_n: number;
  isp_s: number;
  mdot_kg_s: MDot;
  exhaust: Exhaust;
  propellant_remaining_kg: number;
  ambient_pressure_pa: number;
  command_seq: number;
}

export interface CommandMessage {
  v: number;
  type: typeof ENGINE_COMMAND;
  vehicle_id: string;
  engine_id: string;
  seq: number;
  t: number;
  dt: number;
  throttle_demand_frac: number;
  ambient_pressure_pa: number;
  ts_ms: number;
}

export interface FleetRow {
  v: number;
  type: typeof FLEET_TELEMETRY;
  t_sim: number;
  ts_ms: number;
  engine_count: number;
  firing_count: number;
  cut_count: number;
  all_firing: boolean;
  any_cut: boolean;
  thrust_n: number;
  mdot_kg_s: MDot;
  min_propellant_remaining_kg: number;
  min_isp_s: number;
  min_chamber_pressure_pa: number;
  engine_ids: string[];
}

/** Envelope the SSE stream carries per record. */
export type FeedEvent =
  | { kind: "telemetry"; engine_id: string; msg: TelemetryMessage; recv_ms: number }
  | { kind: "command"; engine_id: string; msg: CommandMessage; recv_ms: number };

export type SourceName = "kafka" | "mock";
