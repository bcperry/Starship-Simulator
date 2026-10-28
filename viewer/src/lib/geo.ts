/*
 Equirectangular (plate carrée) projection + GeoJSON→SVG path helpers for the
 world map view. Only runtime dep is topojson-client (decoded once).
 */
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import type { FeatureCollection, Geometry, MultiPolygon, Polygon, Position } from "geojson";

export const MAP_W = 1000;
export const MAP_H = 500;

/// lon ∈ [-180,180] → x,  lat ∈ [-90,90] → y (y grows downward, lat 90 at top).
export function project(lat: number, lon: number, w = MAP_W, h = MAP_H): readonly [number, number] {
  const x = ((lon + 180) / 360) * w;
  const y = ((90 - lat) / 180) * h;
  return [x, y];
}

function ringToPath(ring: Position[], w: number, h: number): string {
  if (!ring.length) return "";
  let d = "";
  for (let i = 0; i < ring.length; i++) {
    const lon = ring[i][0];
    const lat = ring[i][1];
    const x = project(lat, lon, w, h)[0];
    const y = project(lat, lon, w, h)[1];
    d += i === 0 ? `M ${x.toFixed(2)} ${y.toFixed(2)}` : ` L ${x.toFixed(2)} ${y.toFixed(2)}`;
  }
  return d + " Z";
}

/// One SVG `d` string for the (Multi)Polygon rings of a geometry.
export function geoPath(geo: Geometry, w = MAP_W, h = MAP_H): string {
  if (!geo) return "";
  if (geo.type === "Polygon") {
    let d = "";
    for (const ring of (geo as Polygon).coordinates) d += ringToPath(ring, w, h);
    return d;
  }
  if (geo.type === "MultiPolygon") {
    let d = "";
    for (const poly of (geo as MultiPolygon).coordinates) for (const ring of poly) d += ringToPath(ring, w, h);
    return d;
  }
  return "";
}

/// Decode the vendored land topology into a single SVG path (works for any object name).
export function landPath(topology: Topology, w = MAP_W, h = MAP_H): string {
  const key = Object.keys(topology.objects)[0];
  const obj = topology.objects[key] as unknown as GeometryCollection;
  const fc = feature(topology, obj) as FeatureCollection;
  let d = "";
  for (const f of fc.features) d += geoPath(f.geometry, w, h);
  return d;
}

/* ---- display formatters for the tickers ---- */
export const fmtLat = (v: number) => `${Math.abs(v).toFixed(3)}° ${v >= 0 ? "N" : "S"}`;
export const fmtLon = (v: number) => `${Math.abs(v).toFixed(3)}° ${v >= 0 ? "E" : "W"}`;
export const fmtAltKm = (altM: number) => (altM / 1000).toFixed(altM >= 100000 ? 1 : 2);
export const fmtSpeed = (mS: number) => mS.toFixed(0);
export const fmtMach = (m: number) => m.toFixed(1);
