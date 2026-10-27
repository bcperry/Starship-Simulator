// Shared wire types for the telemetry viewer (mirrors simulator/telemetry/messages.py).

export const ENGINE_TELEMETRY = "engine.telemetry";
export const ENGINE_COMMAND = "engine.command";
export const SHIP_STATUS = "ship.status"; // ship loop -> viewers: shared fuel + run state
export const SHIP_CONTROL = "ship.control"; // operator -> ship loop: start_run / throttle / meco

export interface MDot {
  total: number;
  lox: number;
  ch4: number;
}

export interface TelemetryMessage {
  v: number;
  type: typeof ENGINE_TELEMETRY;
  vehicle_id: string;
  engine_id: string;
  seq: number;
  t: number;
  ts_ms: number;
  state: "idle" | "firing" | "cut";
  throttle_demand_frac: number;
  throttle_actual_frac: number;
  chamber_pressure_pa: number;
  thrust_n: number;
  isp_s: number;
  mdot_kg_s: MDot;
  exhaust: { velocity_m_s: number; exit_pressure_pa: number; exit_mach: number };
  propellant_remaining_kg: number;
  ambient_pressure_pa: number;
  command_seq: number;
  run_id?: string; // ship run this step belongs to (absent = standalone engine)
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
  run_id?: string;
}

export interface ShipTank {
  lox_kg: number;
  ch4_kg: number;
  lox_frac: number;
  ch4_frac: number;
  total_kg: number;
  total_initial_kg: number;
  frac: number; // fraction of the initial shared load remaining
  limited?: "lox" | "ch4" | "both" | null;
}

export interface ShipStatusMessage {
  v: number;
  type: typeof SHIP_STATUS;
  vehicle_id: string;
  run_id: string;
  seq: number;
  t: number;
  ts_ms: number;
  n_engines: number;
  firing_count: number;
  cut_count: number;
  all_firing: boolean;
  vari_frac: number;
  seed?: string | number | null;
  thrust_n: number;
  thrust_min_n?: number;
  thrust_max_n?: number;
  thrust_spread_frac?: number;
  mdot_kg_s: { total: number; lox?: number; ch4?: number };
  tank: ShipTank;
  meco: boolean;
  meco_t?: number | null;
  throttle_cmd_frac?: number;
  mixture_ratio?: number;
}

export type ShipControlCommand = "start_run" | "throttle" | "meco";

export interface ShipControlMessage {
  v: number;
  type: typeof SHIP_CONTROL;
  vehicle_id: string;
  command: ShipControlCommand;
  ts_ms?: number;
  seed?: string | number;
  vari_frac?: number;
  n_engines?: number;
  lox_kg?: number;
  ch4_kg?: number;
  throttle_frac?: number;
}

export interface ShipAck {
  type: "ship.ack";
  command?: string;
  ok: boolean;
  run_id?: string;
  throttle_frac?: number;
  reason?: string;
  error?: string;
  ts_ms?: number;
}

export interface LineSummary {
  kind: "command" | "telemetry" | "ship";
  t: number;
  summary: string;
  engine_id: string;
  seq: number;
}

export type FeedEvent =
  | ({ kind: "command"; message: CommandMessage } & { lineSummary: LineSummary })
  | ({ kind: "telemetry"; message: TelemetryMessage } & { lineSummary: LineSummary })
  | ({ kind: "ship"; message: ShipStatusMessage } & { lineSummary: LineSummary });
