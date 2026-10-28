/*
 Engine diagram: the Super Heavy Octaweb (33 Raptors) as 33 circles arranged in
 the real 1 + 8 + 14 + 10 web, each coloured by live engine state:
   firing → filled accent (glowing)     failed → solid red
   cut    → hollow, red ring            idle   → hollow, white ring
Plus a small Starship (6) web for completeness.
 */
import { useMemo } from "react";
import type { EngineView } from "../feed";

type EState = "idle" | "firing" | "cut" | "failed";

function stateOf(e: EngineView | undefined): EState {
  const s = e?.telemetry?.state;
  return (s === "firing" || s === "cut" || s === "failed" ? s : "idle");
}

/// Positions on a unit web. n=33 → the real Super Heavy 1/8/14/10 Octaweb;
/// otherwise a clean concentric 8/16/24… fill.
function octawebPositions(n: number): Array<[number, number]> {
  const pts: Array<[number, number]> = [];
  if (n <= 0) return pts;
  pts.push([0, 0]);
  const ring = (count: number, radius: number, phaseDeg: number) => {
    for (let k = 0; k < count; k++) {
      const a = ((phaseDeg + (k * 360) / count) * Math.PI) / 180;
      pts.push([Math.cos(a) * radius, Math.sin(a) * radius]);
    }
  };
  if (n === 33) {
    ring(8, 0.46, 90);
    ring(14, 0.74, 0);
    ring(10, 1.0, 18);
  } else {
    let remain = n - 1;
    let r = 0;
    while (remain > 0) {
      r += 1;
      const count = Math.min(remain, 8 * r);
      ring(count, r * 0.33, r % 2 === 0 ? 0 : 22.5);
      remain -= count;
    }
  }
  return pts;
}

function Cluster({ engines, n, label }: { engines: EngineView[]; n: number; label: string }) {
  const cells = useMemo(() => {
    const pos = octawebPositions(Math.min(n, engines.length) || 0);
    const out = pos.map(([px, py], i) => {
      const e = engines[i];
      return {
        x: px,
        y: py,
        id: e?.id ?? `slot-${i + 1}`,
        state: stateOf(e),
        thrust: e?.telemetry?.thrust_n ?? 0,
      };
    });
    const counts = { firing: 0, failed: 0, cut: 0, idle: 0 };
    for (const c of out) counts[c.state] += 1;
    return { out, counts };
  }, [engines, n]);

  const R = 86;
  const cx = 110;
  const cy = 110;

  return (
    <div className="ed-cluster">
      <div className="ed-cluster-head mono">
        <span>{label}</span>
        <span className="ed-ccounts">
          <b className="c-firing">{cells.counts.firing}</b>
          <span className="c-failed">{cells.counts.failed}✕</span>
          <span className="c-cut">{cells.counts.cut}</span>
          <span className="c-idle">{cells.counts.idle}</span>
        </span>
      </div>
      <svg viewBox="0 0 220 220" className="ed-svg" role="img" aria-label={`${label} engine web`}>
        <g transform={`translate(${cx} ${cy})`}>
          <circle r={92} className="ed-backplate" />
          {cells.out.map((c) => (
            <g key={c.id} className={`ed-cell ed-${c.state}`} transform={`translate(${(c.x * R).toFixed(2)} ${(c.y * R).toFixed(2)}`}>
              <circle r={13} className="ed-ring" />
              <circle r={9} className="ed-core" />
              {c.state === "firing" ? <circle r={16} className="ed-glow" /> : null}
            </g>
          ))}
        </g>
      </svg>
    </div>
  );
}

export function EngineDiagram({ engines }: { engines: EngineView[] }) {
  const byNum = (a: EngineView, b: EngineView) => a.id.localeCompare(b.id, undefined, { numeric: true });
  const sh = useMemo(() => engines.filter((e) => e.id.toLowerCase().startsWith("sh")).sort(byNum), [engines]);
  const ss = useMemo(() => engines.filter((e) => e.id.toLowerCase().startsWith("ss")).sort(byNum), [engines]);

  return (
    <section className="panel ed">
      <header>
        <span className="ttl">engines · live state</span>
        <span className="right"><span className="pill"><span className="k">web</span>raptor-3</span></span>
      </header>
      <div className="body ed-body">
        <Cluster engines={sh} n={33} label="super heavy · 33" />
        <div className="ed-row">
          <Cluster engines={ss} n={6} label="starship · 6" />
          <div className="ed-legend mono">
            <div className="lg-row"><span className="lg sw-firing" /> firing</div>
            <div className="lg-row"><span className="lg sw-failed" /> failed</div>
            <div className="lg-row"><span className="lg sw-cut" /> cut (no prop)</div>
            <div className="lg-row"><span className="lg sw-idle" /> idle / off</div>
          </div>
        </div>
      </div>
    </section>
  );
}
