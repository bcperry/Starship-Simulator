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
  state: "idle" | "firing" | "cut" | "failed";
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
  bank?: "vac" | "sl" | null; // engine bank on multi-bank vessels (Starship)
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
  bank?: "vac" | "sl" | null;
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
  failed_count?: number;
  failed_engines?: string[];
  expected_failures_per_run?: number;
  fail_hazard_per_s?: number;
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
  /** expected fleet failures over the nominal full-tank burn (0 = failure-free) */
  expected_failures_per_run?: number;
  /** raw per-engine hazard rate in 1/s; overrides the calibration when > 0 (0 clears) */
  fail_hazard_per_s?: number;
  /** natural high-frequency jitter fraction (0.004 = 0.4 % rms on chamber pressure) */
  noise_frac?: number;
  /** full-stack control: expected failures in the Super Heavy burn */
  sh_expected_failures?: number;
  /** full-stack control: expected failures in the Starship burn */
  ss_expected_failures?: number;
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
  kind: "command" | "telemetry" | "ship" | "stack";
  t: number;
  summary: string;
  engine_id: string;
  seq: number;
  state?: string;
  ts_ms?: number;
}

export type StackPhase = "liftoff" | "staging" | "upper" | "done";

export interface StackVesselStatus {
  vehicle_id: string;
  run_id: string;
  meco: boolean;
  firing: number;
  failed: number;
  tank_frac: number;
  ignited?: boolean;
}

export interface StackStatusMessage {
  v: number;
  type: "stack.status";
  vehicle_id: string; // "stack"
  t: number;
  phase: StackPhase;
  active_vehicle: string | null;
  staging_in_s?: number | null;
  sh: StackVesselStatus;
  ss: StackVesselStatus;
  tank: { frac: number; total_kg?: number };
  meco?: boolean;
}

export type FeedEvent =
  | ({ kind: "command"; message: CommandMessage } & { lineSummary: LineSummary })
  | ({ kind: "telemetry"; message: TelemetryMessage } & { lineSummary: LineSummary })
  | ({ kind: "ship"; message: ShipStatusMessage } & { lineSummary: LineSummary })
  | ({ kind: "stack"; message: StackStatusMessage } & { lineSummary: LineSummary });
