/*
 Engine diagram.
   Super Heavy  — the 33‑Raptor web, laid out exactly like the reference photo:
                 3 inner (triangle, one at top) + 10 middle + 20 outer, evenly
                 packed with small gaps, each coloured by live state.
   Starship    — six Raptors: THREE large Vacuum (Vac) engines outboard and
                 THREE smaller Sea‑Level (SL) engines inboard (interleaved),
                 the Vacs being the largest of all.
   States:       firing → filled (glow)   failed → solid red
                 cut    → hollow red ring  idle   → hollow white
 */
import { useMemo } from "react";
import type { EngineView } from "../feed";

export type EState = "idle" | "firing" | "cut" | "failed";

// One engine's footprint in UNIT space (web radius = 1.0).
interface Foot { core: number; ring: number; glow: number }
// A positioned cell.
export interface Cell {
  x: number;
  y: number;
  id: string;
  state: EState;
  thrust: number;
  core: number; // unit radii (multiplied by the render scale)
  ring: number;
  glow: number;
  kind: "vac" | "sl" | "core";
}

// SH: uniform size, ~0.13 engine radius (measured from the reference image).
const SH: Foot = { core: 0.115, ring: 0.140, glow: 0.150 };
// VAC (Starship vacuum) is the LARGEST; SL (sea‑level) the smallest.
const VAC: Foot = { core: 0.170, ring: 0.200, glow: 0.220 };
const SL: Foot = { core: 0.100, ring: 0.130, glow: 0.150 };

function stateOf(e: EngineView | undefined): EState {
  const s = e?.telemetry?.state;
  return s === "firing" || s === "cut" || s === "failed" ? s : "idle";
}
const rad = (deg: number) => (deg * Math.PI) / 180;
function spot(radius: number, deg: number): [number, number] {
  return [Math.cos(rad(deg)) * radius, Math.sin(rad(deg)) * radius];
}

/** Pure geometry — exported so it can be unit‑checked for overlaps. */
export function shOctaweb(layout: EngineView[]): Cell[] {
  const out: Omit<Cell, "id" | "state" | "thrust">[] = [];
  const push = (r: number, deg: number, foot: Foot, kind: Cell["kind"]) => {
    const [x, y] = spot(r, deg);
    out.push({ x, y, core: foot.core, ring: foot.ring, glow: foot.glow, kind });
  };
  // 3 inner — a triangle, one at the top (90/210/330°), r ≈ 0.214
  for (const a of [90, 210, 330]) push(0.214, a, SH, "core");
  // 10 middle — one at top, every 36°, r ≈ 0.599
  for (let k = 0; k < 10; k++) push(0.599, 90 + k * 36, SH, "core");
  // 20 outer — offset 9° (staggered into the 10‑ring gaps), every 18°, r = 1.0
  for (let k = 0; k < 20; k++) push(1.0, 9 + k * 18, SH, "core");
  // map engines (sh-01..33) onto the 33 slots, in order
  return out.map((c, i) => ({
    ...c,
    id: layout[i]?.id ?? `sh-slot-${i + 1}`,
    state: stateOf(layout[i]),
    thrust: layout[i]?.telemetry?.thrust_n ?? 0,
  }));
}

export function ssWeb(layout: EngineView[]): Cell[] {
  const out: Omit<Cell, "id" | "state" | "thrust">[] = [];
  const push = (r: number, deg: number, foot: Foot, kind: Cell["kind"]) => {
    const [x, y] = spot(r, deg);
    out.push({ x, y, core: foot.core, ring: foot.ring, glow: foot.glow, kind });
  };
  // three LARGEST Vacuum engines, outboard (12 / 4 / 8 o'clock)
  for (const a of [90, 210, 330]) push(0.82, a, VAC, "vac");
  // three smaller Sea‑Level engines, inboard & interleaved (2 / 6 / 10 o'clock)
  for (const a of [30, 150, 270]) push(0.38, a, SL, "sl");
  // map engines (ss-01..06): Vacs=ss-01..03 (outboard), SLs=ss-04..06 (inboard)
  return out.map((c, i) => {
    const e = layout[i];
    return { ...c, id: e?.id ?? `ss-${i + 1}`, state: stateOf(e), thrust: e?.telemetry?.thrust_n ?? 0 };
  });
}

/** Minimum clearance (unit) between any two engine rings — must be > 0. */
export function minClearance(cells: Cell[]): number {
  let m = Infinity;
  for (let i = 0; i < cells.length; i++) {
    for (let j = i + 1; j < cells.length; j++) {
      const a = cells[i];
      const b = cells[j];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      m = Math.min(m, d - (a.ring + b.ring));
    }
  }
  return m;
}

function Cluster({
  title,
  cells,
  scale,
  backplate,
}: {
  title: string;
  cells: Cell[];
  scale: number;
  backplate: number;
}) {
  const counts = useMemo(() => {
    const c = { firing: 0, failed: 0, cut: 0, idle: 0 };
    for (const cell of cells) c[cell.state] += 1;
    return c;
  }, [cells]);

  const cx = 150;
  return (
    <div className="ed-cluster">
      <div className="ed-cluster-head mono">
        <span>{title}</span>
        <span className="ed-ccounts">
          <b className="c-firing">{counts.firing}</b>
          <span className="c-failed">{counts.failed}✕</span>
          <span className="c-cut">{counts.cut}</span>
          <span className="c-idle">{counts.idle}</span>
        </span>
      </div>
      <svg viewBox="0 0 300 300" className="ed-svg" role="img" aria-label={`${title} engine web`}>
        <g transform={`translate(${cx} ${cx})`}>
          <circle r={backplate * scale} className="ed-backplate" />
          {cells.map((c) => (
            <g key={c.id} className={`ed-cell ed-${c.state}`} transform={`translate(${(c.x * scale).toFixed(2)} ${(c.y * scale).toFixed(2)})`}>
              <circle r={c.glow * scale} className="ed-glow" />
              <circle r={c.ring * scale} className="ed-ring" />
              <circle r={c.core * scale} className="ed-core" />
            </g>
          ))}
        </g>
      </svg>
    </div>
  );
}

export function EngineDiagram({ engines }: { engines: EngineView[] }) {
  const byNum = (a: EngineView, b: EngineView) => a.id.localeCompare(b.id, undefined, { numeric: true });
  const shIn = useMemo(() => engines.filter((e) => e.id.toLowerCase().startsWith("sh")).sort(byNum), [engines]);
  const ssIn = useMemo(() => engines.filter((e) => e.id.toLowerCase().startsWith("ss")).sort(byNum), [engines]);

  const sh = useMemo(() => shOctaweb(shIn), [shIn]);
  const ss = useMemo(() => ssWeb(ssIn), [ssIn]);
  const SCALE = 110;
  const BP = 1.18;

  return (
    <section className="panel ed">
      <header>
        <span className="ttl">engines · live state</span>
        <span className="right">
          <span className="pill">
            <span className="k">web</span>raptor‑3
          </span>
        </span>
      </header>
      <div className="body ed-body">
        <Cluster title="super heavy · 33" cells={sh} scale={SCALE} backplate={BP} />
        <div className="ed-row">
          <Cluster title="starship · 6" cells={ss} scale={SCALE} backplate={BP} />
          <div className="ed-legend mono">
            <div className="lg-row">
              <span className="lg sw-firing" /> firing
            </div>
            <div className="lg-row">
              <span className="lg sw-failed" /> failed
            </div>
            <div className="lg-row">
              <span className="lg sw-cut" /> cut (no prop)
            </div>
            <div className="lg-row">
              <span className="lg sw-idle" /> idle / off
            </div>
            <div className="ed-clear mono" style={{ opacity: 0.55 }}>
              clearance SH {minClearance(sh).toFixed(3)} / SS {minClearance(ss).toFixed(3)}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
