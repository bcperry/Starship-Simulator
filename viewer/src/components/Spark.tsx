// Dependency-free SVG sparkline with soft area fill.
interface Props {
  values: number[];
  width?: number;
  height?: number;
  stroke?: string;
  fill?: string;
  /** fix the y-domain so the line doesn't rescale every frame */
  domain?: [number, number];
}

export function Spark({
  values,
  width = 180,
  height = 40,
  stroke = "var(--accent)",
  fill = "rgba(55,225,168,0.10)",
  domain,
  className = "spark",
}: Props & { className?: string }) {
  if (values.length < 2) {
    return (
      <svg height={height} viewBox={`0 0 ${width} ${height}`} className={className} style={{ width: "100%" }} aria-hidden>
        <line x1={0} y1={height - 1} x2={width} y2={height - 1}
          stroke="rgba(255,255,255,0.07)" />
      </svg>
    );
  }
  let min = domain ? domain[0] : Math.min(...values);
  let max = domain ? domain[1] : Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const pad = 2;
  const step = (width - pad * 2) / (values.length - 1);
  const y = (v: number) => height - pad - ((v - min) / span) * (height - pad * 2);
  const pts = values.map((v, i) => [pad + i * step, y(v)] as const);
  const line = pts.map(([x, v], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${v.toFixed(1)}`).join(" ");
  const area = `${line} L ${pad + (values.length - 1) * step} ${height} L ${pad} ${height} Z`;
  return (
    <svg height={height} viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none" className={className} style={{ width: "100%" }} aria-hidden>
      <path d={area} fill={fill} stroke="none" />
      <path d={line} fill="none" stroke={stroke} strokeWidth={1.6}
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
