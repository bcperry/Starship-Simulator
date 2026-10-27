// Full-stack phase bar: which stage is live + the mission timeline.
// Data = the latest stack.status document (Super Heavy + Starship).

import type { StackStatusMessage, StackVesselStatus } from "@shared/types";

const pretty = (id: string) =>
  id === "starship" ? "STARSHIP" : id === "super-heavy" ? "SUPER HEAVY" : id.toUpperCase();

function StageChip({ label, st, active }: { label: string; st: StackVesselStatus; active: boolean }) {
  const frac = Math.max(0, Math.min(1, st.tank_frac));
  return (
    <span className={`stg ${active ? "on" : ""} ${st.meco ? "meco" : ""}`}>
      <span className="stglabel">{label}</span>
      <span className={`stgtag mono`}>{st.meco ? "MECO" : active ? "burning" : "cold"}</span>
      <span className="stgtrack">
        <span className="stgfill" style={{ width: `${frac * 100}%` }} />
      </span>
      <span className="stgtank mono">{(frac * 100).toFixed(0)}%</span>
    </span>
  );
}

export function PhaseBar({ stack }: { stack: StackStatusMessage }) {
  const phaseCls: Record<string, string> = {
    liftoff: "liftoff",
    staging: "staging",
    upper: "upper",
    done: "done",
  };
  const active = stack.active_vehicle ?? "";
  const staging =
    stack.phase === "staging" && Number.isFinite(stack.staging_in_s)
      ? `  ·  ${Number(stack.staging_in_s).toFixed(1)} s to ignition`
      : "";
  return (
    <div className="phasebar">
      <span className={`phasebadge ${phaseCls[stack.phase] ?? ""}`} title="stack mission phase">
        {stack.phase.toUpperCase()}
      </span>
      <span className="phasetime mono">T+ {stack.t.toFixed(1)}s</span>
      {staging && <span className="staging mono dim">{staging.trim()}</span>}
      <span className="phasespacer" />
      <StageChip label={pretty(stack.sh.vehicle_id)} st={stack.sh} active={active === stack.sh.vehicle_id} />
      <StageChip label={pretty(stack.ss.vehicle_id)} st={stack.ss} active={active === stack.ss.vehicle_id} />
    </div>
  );
}
