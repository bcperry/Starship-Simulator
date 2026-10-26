import type { ReactNode } from "react";

import type { FleetSummary } from "../lib/aggregate";
import { kgps, kg, mn, mp, sec } from "../lib/format";

function Metric({ k, v, f, m }: { k: string; v: ReactNode; f?: ReactNode; m?: string }) {
  return (
    <div className="metric" style={{ ["--m" as never]: m }}>
      <span className="accent-line" />
      <div className="k">{k}</div>
      <div className="v" style={{ color: m }}>{v}</div>
      {f && <div className="f">{f}</div>}
    </div>
  );
}

export function FleetBoard({ f }: { f: FleetSummary }) {
  return (
    <div className="board">
      <Metric
        k="Total thrust"
        v={<>{mn(f.thrust_n)}<small>MN</small></>}
        f={`${f.firing_count} / ${f.engine_count} firing`}
        m="var(--thrust)"
      />
      <Metric
        k="Propellant flow"
        v={<>{(f.mdot_total_kg_s / 1000).toFixed(2)}<small>t/s</small></>}
        f={`LOX ${kgps(f.mdot_lox_kg_s)} · CH₄ ${kgps(f.mdot_ch4_kg_s)}`}
        m="var(--mdot)"
      />
      <Metric
        k="Engines"
        v={
          <>{f.firing_count}
            <small>
              firing · {f.cut_count} cut · {f.idle_count} idle
            </small>
          </>
        }
        f={f.any_cut ? "propellant shortage on ≥1 engine" : "all nominal"}
        m={f.any_cut ? "var(--red)" : f.all_firing ? "var(--accent)" : "var(--cyan)"}
      />
      <Metric
        k="Min chamber pressure"
        v={<>{mp(f.min_chamber_pressure_pa ?? 0)}<small>MPa</small></>}
        f={<>
          I<sub>sp</sub> ≥ {sec(f.min_isp_s ?? NaN)} s
        </>}
        m="var(--pc)"
      />
      <Metric
        k="Propellant min remaining"
        v={
          <>
            {f.min_propellant_remaining_kg !== null ? (
              (f.min_propellant_remaining_kg / 1e3).toFixed(0)
            ) : "—"}
            <small>t</small>
          </>
        }
        f={f.min_propellant_remaining_kg !== null ? kg(f.min_propellant_remaining_kg) : "no data"}
        m={f.any_cut ? "var(--red)" : "var(--amber)"}
      />
    </div>
  );
}
