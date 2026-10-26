// Trend chart: total thrust + propellant flow over the recent window,
// with grid, soft fills, and a "now" value. Pure SVG, no chart dependency —
// cheap to re-render at 10 Hz and easy to extend with more series later.
import { useMemo } from "react";

import type { FleetPoint } from "../feed";
import { mn } from "../lib/format";

interface Props {
  points: FleetPoint[];
  /** number of tail points to show (default: all) */
  window?: number;
}

const W = 940;
const H = 220;
const P = { top: 14, right: 12, bottom: 22, left: 46 };

function scaleDomain(vals: number[]): [number, number] {
  if (!vals.length) return [0, 1];
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (lo === hi) { lo = 0; hi = 1; }
  return [Math.max(0, lo * 0.9), hi * 1.05];
}

export function TrendChart({ points, window: win }: Props) {
  const data = useMemo(
    () => (win ? points.slice(-win) : points),
    [points, win],
  );

  const iw = W - P.left - P.right;
  const ih = H - P.top - P.bottom;

  const thrust = data.map((d) => d.thrust_n / 1e6);
  const mdot = data.map((d) => d.mdot_total_kg_s / 1000);

  const tdom = scaleDomain(thrust);
  const mdom = scaleDomain(mdot);

  const x = (i: number) => P.left + (data.length <= 1 ? 0 : (i / (data.length - 1)) * iw);
  const yT = (v: number) => P.top + ih - ((v - tdom[0]) / (tdom[1] - tdom[0])) * ih;
  const yM = (v: number) => P.top + ih - ((v - mdom[0]) / (mdom[1] - mdom[0])) * ih;

  const tLine =
    thrust.length > 1
      ? thrust.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${yT(v).toFixed(1)}`).join(" ")
      : "";
  const mLine =
    mdot.length > 1
      ? mdot.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${yM(v).toFixed(1)}`).join(" ")
      : "";
  const tArea = tLine
    ? `${tLine} L ${x(thrust.length - 1)} ${P.top + ih} L ${x(0)} ${P.top + ih} Z`
    : "";
  const mArea = mLine
    ? `${mLine} L ${x(mdot.length - 1)} ${P.top + ih} L ${x(0)} ${P.top + ih} Z`
    : "";

  const last = data[data.length - 1];
  const gridY = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: "block" }}>
        <defs>
          <linearGradient id="gt" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(55,225,168,0.28)" />
            <stop offset="100%" stopColor="rgba(55,225,168,0)" />
          </linearGradient>
          <linearGradient id="gm" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(79,214,255,0.24)" />
            <stop offset="100%" stopColor="rgba(79,214,255,0)" />
          </linearGradient>
        </defs>

        {/* horizontal grid + left axis labels (thrust domain) */}
        {gridY.map((f) => {
          const yy = P.top + ih - f * ih;
          const val = tdom[0] + f * (tdom[1] - tdom[0]);
          return (
            <g key={f}>
              <line x1={P.left} x2={W - P.right} y1={yy} y2={yy}
                stroke="rgba(255,255,255,0.05)" strokeWidth={1} />
              <text x={P.left - 8} y={yy + 3.5} textAnchor="end"
                style={{ fill: "var(--ink-4)", fontSize: 10, fontFamily: "var(--mono)" }}>
                {val.toFixed(1)}
              </text>
            </g>
          );
        })}

        {tArea && <path d={tArea} fill="url(#gt)" />}
        {mArea && <path d={mArea} fill="url(#gm)" />}
        {tLine && <path d={tLine} fill="none" stroke="var(--thrust)" strokeWidth={2} />}
        {mLine && <path d={mLine} fill="none" stroke="var(--mdot)" strokeWidth={1.7} strokeDasharray="1 0" />}

        {/* now markers */}
        {last && data.length > 1 && (
          <g>
            <circle cx={x(thrust.length - 1)} cy={yT(thrust[thrust.length - 1])} r={3.5}
              fill="var(--thrust)" />
            <circle cx={x(mdot.length - 1)} cy={yM(mdot[mdot.length - 1])} r={3}
              fill="var(--mdot)" />
          </g>
        )}

        <text x={P.left} y={H - 6} style={{ fill: "var(--ink-4)", fontSize: 10 }}>
          −{(data.length * 0.1).toFixed(0)}s
        </text>
        <text x={W - P.right} y={H - 6} textAnchor="end"
          style={{ fill: "var(--ink-4)", fontSize: 10 }}>
          now
        </text>
      </svg>

      <div className="legend" style={{ marginTop: 8 }}>
        <span className="li">
          <span className="sw" style={{ background: "var(--thrust)" }} />
          total thrust {last ? mn(last.thrust_n) : "—"} MN
        </span>
        <span className="li">
          <span className="sw" style={{ background: "var(--mdot)" }} />
          propellant {last ? (last.mdot_total_kg_s / 1000).toFixed(2) : "—"} t/s
        </span>
        <span className="axis-note" style={{ marginLeft: "auto" }}>
          left axis = MN · window ≈ {Math.max(1, Math.round((data.length * 0.1) / 5) * 5)} s
        </span>
      </div>
    </div>
  );
}
