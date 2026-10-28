// Sim speed: sim-seconds advanced per real second. Picking a speed respawns
// the Python sim at that rate and lights a fresh run, so the change is
// immediately visible.
import { useState } from "react";

import { clientFeed } from "../feed";

const PRESETS = [1, 5, 10, 30, 60, 120, 400];

export function SpeedControl({ initial = 8 }: { initial?: number }) {
  const [speed, setSpeed] = useState<number | null>(initial);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [custom, setCustom] = useState("");

  const apply = async (n: number) => {
    if (!Number.isFinite(n) || n <= 0) return;
    setBusy(true);
    setMsg(null);
    const r = await clientFeed.setSpeed(n);
    setBusy(false);
    if (r.ok) {
      setSpeed(n);
      setMsg(`${n}× · fresh run at that speed`);
    } else {
      setMsg(`✗ ${r.error ?? "speed change failed"}`);
    }
  };

  return (
    <div className="speedctl">
      <span className="sc-lbl">sim&nbsp;speed</span>
      {PRESETS.map((n) => (
        <button
          key={n}
          type="button"
          className={`sc-btn ${speed === n ? "sel" : ""}`}
          disabled={busy}
          onClick={() => void apply(n)}>
          {n}×
        </button>
      ))}
      <input
        className="sc-custom mono"
        type="number"
        min={0.1}
        step={0.5}
        placeholder="n×"
        value={custom}
        disabled={busy}
        onChange={(e) => setCustom(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            const v = Number(custom);
            setCustom("");
            void apply(v);
          }
        }}
      />
      <span className={`sc-msg mono ${msg && msg.startsWith("✗") ? "err" : "ok"}`}>{busy ? "spawning run…" : msg}</span>
    </div>
  );
}
