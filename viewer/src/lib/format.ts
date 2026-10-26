// Formatting helpers for physical values — keep the units honest.

export function mn(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return (n / 1e6).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function kn(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return (n / 1e3).toLocaleString("en-US", { maximumFractionDigits: 1 });
}

export function mp(pa: number): string {
  if (!Number.isFinite(pa)) return "—";
  return (pa / 1e6).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function kc(pa: number): string {
  if (!Number.isFinite(pa)) return "—";
  return (pa / 1e6).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function sec(s: number): string {
  if (!Number.isFinite(s) || s === 0) return "—";
  return s.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

export function kg(k: number): string {
  if (!Number.isFinite(k)) return "—";
  if (k >= 1e6) return (k / 1e6).toLocaleString("en-US", { maximumFractionDigits: 2 }) + " t";
  if (k >= 1e4) return (k / 1e3).toLocaleString("en-US", { maximumFractionDigits: 1 }) + " t";
  return k.toLocaleString("en-US", { maximumFractionDigits: 0 }) + " kg";
}

export function kgps(k: number): string {
  if (!Number.isFinite(k)) return "—";
  if (k >= 1000) return (k / 1000).toLocaleString("en-US", { maximumFractionDigits: 2 }) + " t/s";
  return k.toLocaleString("en-US", { maximumFractionDigits: 1 }) + " kg/s";
}

export function pct(x: number): string {
  if (!Number.isFinite(x)) return "—";
  return Math.round(x * 100) + "%";
}

export function clock(tsMs: number): string {
  const d = new Date(tsMs);
  return d.toLocaleTimeString("en-GB", { hour12: false });
}
