// Ship-level view: the shared propellant tanks feeding the whole engine
// section, plus run metadata. Data = the newest ship.status document.

import type { ShipView } from "../feed";

import { kg } from "../lib/format";

import { Spark } from "./Spark";

function Bar({ label, frac, kgRem, color }: { label: string; frac: number; kgRem: string; color: string }) {
  const f = Number.isFinite(frac) ? Math.max(0, Math.min(1, frac)) : 0;
  const pct = f * 100;
  return (
    <div className="tankrow">
      <span className="tanklab">{label}</span>
      <div className="track tanktrack">
        <div className="fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="tankval mono">{pct.toFixed(1)}% · {kgRem}</span>
    </div>
  );
}

export function ShipPanel({ ship }: { ship: ShipView }) {
  const s = ship.ship;
  if (!s) {
    return (
      <div className="empty small">
        No <code>ship.status</code> yet — start a run from the control panel.
      </div>
    );
  }
  const thrustPct = ((s.thrust_n / 1e6).toFixed(3));
  const tankHist = ship.history.map((h) => h.tank_frac * 100);

  return (
    <div className="ship">
      <div className="shipmeta">
        <span className={`pill run ${s.meco ? "meco" : ""}`}>
          {s.meco ? "MECO" : "burning"}
        </span>
        <span className="dim small mono">
          {s.n_engines} engines · {s.failed_count ?? 0} failed · ±{Math.round((s.vari_frac ?? 1) * 100)}%
        </span>
        <span className="dim small mono">T+ {s.t.toFixed(1)}s</span>
        <span className="dim small mono" title={s.run_id}>{s.run_id.slice(0, 13)}…</span>
      </div>

      <div className="big">
        <span>{thrustPct}<small> MN total thrust</small></span>
        <span className="right mono dim">
          spread {((s.thrust_spread_frac ?? 0) * 100).toFixed(2)}%
        </span>
      </div>

      <Bar label="LOX" frac={s.tank.lox_frac ?? s.tank.frac} kgRem={kg(s.tank.lox_kg)} color="var(--cyan)" />
      <Bar label="CH₄" frac={s.tank.ch4_frac ?? s.tank.frac} kgRem={kg(s.tank.ch4_kg)} color="var(--accent)" />

      <div className="tankfoot mono dim">
        <span>tank ṁ {(s.mdot_kg_s?.total ?? 0).toFixed(0)} kg/s</span>
        <span>mix ratio {s.mixture_ratio != null ? s.mixture_ratio.toFixed(2) : "—"}</span>
        <span>
          {s.tank.limited ? `LIMITED: ${s.tank.limited}` : "tanks nominal"}
        </span>
        <span title="constant-hazard (exponential) time-to-failure model, calibrated to this expected fleet failure count per full-tank launch">
          exp. failures ≈ {s.expected_failures_per_run != null ? s.expected_failures_per_run.toFixed(1) : "—"}
        </span>
      </div>

      {(s.failed_engines?.length ?? 0) > 0 && (
        <div className="failrow mono">
          <b style={{ color: "var(--amber)" }}>FAILED:</b>
          {(s.failed_engines ?? []).map((id) => (
            <span key={id} className="failid">{id}</span>
          ))}
        </div>
      )}

      {tankHist.length > 2 && (
        <div className="shipspark" title="tank fraction over time">
          <Spark
            values={tankHist}
            height={30}
            domain={[0, 100]}
            stroke="var(--cyan)"
            fill="rgba(79,214,255,0.10)"
            className="spark"
          />
          <span className="mono dim tiny">tank %</span>
        </div>
      )}
    </div>
  );
}
