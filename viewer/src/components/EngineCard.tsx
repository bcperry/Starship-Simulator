import type { EngineView } from "../feed";

import { kc, kg, pct, sec } from "../lib/format";

import { Spark } from "./Spark";

const STATE_META: Record<string, { label: string; cls: string }> = {
  firing: { label: "firing", cls: "firing" },
  idle: { label: "idle", cls: "idle" },
  cut: { label: "cut", cls: "cut" },
  failed: { label: "failed", cls: "failed" },
};

export function EngineCard({ v }: { v: EngineView }) {
  const m = v.telemetry;
  const c = v.command;
  const meta = STATE_META[m?.state ?? "idle"];
  const hist = v.history.slice(-160);
  const domain: [number, number] = [0, Math.max(1.9e6, ...hist.map((h) => h.thrust))];
  const stale = m ? Date.now() - v.lastRecvMs > 3000 : !m;

  return (
    <div className="card" style={stale ? { opacity: 0.55 } : undefined}>
      <div className="top">
        <span className="id">{v.id}</span>
        <span className={`state ${meta.cls}`}>{meta.label}</span>
      </div>

      <div className="thrust">
        {m ? (m.thrust_n / 1e6).toFixed(2) : "—."}
        <small>MN thrust</small>
      </div>

      <Spark values={hist.map((h) => h.thrust)} height={36} domain={domain}
        stroke={meta.cls === "cut" ? "var(--red)" : "var(--thrust)"} />

      <div className="kv">
        <span className="it">
          P<sub>c</sub>
          <b>{m ? kc(m.chamber_pressure_pa) : "—"} MPa</b>
        </span>
        <span className="it">
          I<sub>sp</sub>
          <b>{m && m.state === "firing" ? sec(m.isp_s) : "—"} s</b>
        </span>
        <span className="it">
          ṁ
          <b>{m ? m.mdot_kg_s.total.toFixed(1) : "—"} kg/s</b>
        </span>
        <span className="it">
          prop remaining
          <b>{m ? kg(m.propellant_remaining_kg) : "—"}</b>
        </span>
      </div>

      <div className="bar">
        <div className="lbl">
          <span>throttle demand {c ? pct(c.throttle_demand_frac) : "—"}</span>
          <span>actual {m ? pct(m.throttle_actual_frac) : "—"}</span>
        </div>
        <div className="track">
          <i style={{ width: `${m ? Math.min(100, m.throttle_actual_frac * 100) : 0}%` }} />
        </div>
      </div>

      <div className="tiny dimmer mono" style={{ marginTop: 8, display: "flex", justifyContent: "space-between" }}>
        <span>cmd #{c?.seq ?? "—"} · {c ? pct(c.throttle_demand_frac) : "—"}</span>
        <span>tel #{m?.seq ?? "—"} · t={(m?.t ?? 0).toFixed(2)}s</span>
      </div>
    </div>
  );
}
