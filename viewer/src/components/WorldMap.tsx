/*
 World map view: equirectangular world map, the live ground track, the vehicle
 marker (rotated to heading), and big LAT / LON / ALT / SPEED / V/S / MACH
 tickers. Pure SVG — no map tiles, no network.
 */
import { useMemo } from "react";
import landTopo from "../assets/land-110m.json";
import type { Topology } from "topojson-specification";
import type { FlightStateMessage, TrailPoint } from "@shared/types";
import { MAP_H, MAP_W, fmtAltKm, fmtLat, fmtLon, fmtMach, fmtSpeed, landPath, project } from "../lib/geo";

interface Props {
  flight: FlightStateMessage;
  trail: TrailPoint[];
}

function Ticker({ label, value, unit, accent }: { label: string; value: string; unit?: string; accent?: boolean }) {
  return (
    <div className="wm-ticker">
      <div className="wm-ticker-label">{label}</div>
      <div className={"wm-ticker-value" + (accent ? " accent" : "")}>
        {value}
        {unit ? <span className="wm-ticker-unit"> {unit}</span> : null}
      </div>
    </div>
  );
}

export function WorldMap({ flight, trail }: Props) {
  const land = useMemo(() => landPath(landTopo as unknown as Topology), []);
  const pos = flight.position;
  const vel = flight.velocity;
  const aero = flight.aerodynamics;

  // Ground track (whole arc so far, from the sampled trail) as one polyline.
  const trackPts = useMemo(() => {
    const pts = trail
      .map((p) => {
        const [x, y] = project(p.lat, p.lon);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    return pts;
  }, [trail]);

  const lat = pos?.lat_deg ?? 0;
  const lon = pos?.lon_deg ?? 0;
  const [vx, vy] = project(lat, lon);
  const heading = vel?.heading_deg ?? 0;
  const site = trail[0] ?? { t: 0, lat, lon };
  const [sx, sy] = project(site.lat, site.lon);
  const leoe = flight.orbit && flight.orbit.perigee_km >= 100;

  return (
    <section className="panel wm">
      <header>
        <span className="ttl">world map · live track</span>
        <span className="right">
          <span className={"pill " + (leoe ? "pill.leo" : "pill.dim")}>
            {leoe ? "LEO inserted" : (flight.phase ?? "ascent")}
          </span>
        </span>
      </header>

      <div className="body wm-mapwrap">
        <svg viewBox={`0 0 ${MAP_W} ${MAP_H}`} className="wm-svg" role="img" aria-label="world map with vehicle track">
          {/* ocean + graticule */}
          <rect x={0} y={0} width={MAP_W} height={MAP_H} className="wm-ocean" />
          {Array.from({ length: 11 }, (_, i) => -150 + i * 30).map((lonLine) => {
            const [gx] = project(0, lonLine);
            return <line key={`lon${lonLine}`} x1={gx} y1={0} x2={gx} y2={MAP_H} className="wm-grat" />;
          })}
          {Array.from({ length: 5 }, (_, i) => -60 + i * 30).map((latLine) => {
            const [, gy] = project(latLine, 0);
            return <line key={`lat${latLine}`} x1={0} y1={gy} x2={MAP_W} y2={gy} className={"wm-grat" + (latLine === 0 ? " eq" : "")} />;
          })}
          {/* land */}
          <path d={land} className="wm-land" />

          {/* ground track */}
          {trackPts && <polyline points={trackPts} className="wm-track" />}

          {/* launch site */}
          <g key="site">
            <circle cx={sx} cy={sy} r={3.5} className="wm-site" />
            <circle cx={sx} cy={sy} r={8} className="wm-site-pulse" />
          </g>

          {/* vehicle (heading-rotated marker) */}
          <g transform={`translate(${vx} ${vy})`} key="veh">
            <circle r={9} className="wm-veh-glow" />
            <circle r={4} className="wm-veh" />
            <g transform={`rotate(${heading})`}>
              <polygon points="0,-11 4,3 0,1 -4,3" className="wm-veh-head" />
            </g>
          </g>
        </svg>

        {/* tickers */}
        <div className="wm-tickers">
          <Ticker label="latitude" value={fmtLat(lat)} />
          <Ticker label="longitude" value={fmtLon(lon)} />
          <Ticker label="altitude" value={fmtAltKm(pos?.alt_m ?? 0)} unit="km" accent />
          <Ticker label="ground speed" value={fmtSpeed(vel?.speed_ms ?? 0)} unit="m/s" accent />
          <Ticker label="v/s climb" value={(vel?.up_ms ?? 0).toFixed(0)} unit="m/s" />
          <Ticker label="mach" value={fmtMach(aero?.mach ?? 0)} />
        </div>
        <div className="wm-metarange mono">
          downrange {((pos?.downrange_m ?? 0) / 1000).toFixed(0)} km · q {(aero?.q_pa ? aero.q_pa.toLocaleString() : "—")} Pa ·
          peak-q {((aero?.max_q_pa ?? 0) / 1000).toFixed(0)} kPa
        </div>
      </div>
    </section>
  );
}
