// TS mirror of aggregate_fleet() from simulator/telemetry/messages.py,
// computed over the viewer's per-engine "latest" map. The wire contract is
// what the fleet.telemetry row looks like *eventually*; this produces the
// same fields the viewer needs for the fleet hero/trends.

import type { TelemetryMessage } from "@shared/types";

export interface FleetSummary {
  engine_count: number;
  firing_count: number;
  cut_count: number;
  idle_count: number;
  failed_count: number;
  all_firing: boolean;
  any_cut: boolean;
  any_failed: boolean;
  thrust_n: number;
  mdot_total_kg_s: number;
  mdot_lox_kg_s: number;
  mdot_ch4_kg_s: number;
  min_isp_s: number | null;
  min_chamber_pressure_pa: number | null;
  min_propellant_remaining_kg: number | null;
}

export function fleetSummary(
  latest: ReadonlyMap<string, TelemetryMessage>,
): FleetSummary {
  let firing = 0;
  let cut = 0;
  let idle = 0;
  let failed = 0;
  let thrust = 0;
  let mdot = 0;
  let mdotLox = 0;
  let mdotCh4 = 0;
  let minIs: number | null = null;
  let minPc: number | null = null;
  let minProp: number | null = null;
  for (const msg of latest.values()) {
    if (msg.state === "firing") firing += 1;
    else if (msg.state === "cut") cut += 1;
    else if (msg.state === "failed") failed += 1;
    else idle += 1;
    thrust += msg.thrust_n;
    mdot += msg.mdot_kg_s.total;
    mdotLox += msg.mdot_kg_s.lox;
    mdotCh4 += msg.mdot_kg_s.ch4;
    if (Number.isFinite(msg.isp_s) && minIs === null) minIs = msg.isp_s;
    if (msg.state === "firing") {
      if (minIs === null || (msg.isp_s > 0 && msg.isp_s < minIs)) minIs = msg.isp_s;
    }
    if (minPc === null || msg.chamber_pressure_pa < minPc) {
      minPc = msg.chamber_pressure_pa;
    }
    if (minProp === null || msg.propellant_remaining_kg < minProp) {
      minProp = msg.propellant_remaining_kg;
    }
  }
  const n = latest.size;
  return {
    engine_count: n,
    firing_count: firing,
    cut_count: cut,
    idle_count: idle,
    failed_count: failed,
    all_firing: n > 0 && firing === n,
    any_cut: cut > 0,
    any_failed: failed > 0,
    thrust_n: thrust,
    mdot_total_kg_s: mdot,
    mdot_lox_kg_s: mdotLox,
    mdot_ch4_kg_s: mdotCh4,
    min_isp_s: minIs,
    min_chamber_pressure_pa: minPc,
    min_propellant_remaining_kg: minProp,
  };
}
