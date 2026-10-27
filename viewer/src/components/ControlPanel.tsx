// Run control: start a new run, live throttle, MECO.
// Talks to POST /api/control (server -> ship, stdio or Kafka).

import { useState } from "react";

import { clientFeed, type ShipView } from "../feed";

import { Panel } from "./Panel";

interface Props {
  ship: ShipView;
  vehicleId: string;
}

export function ControlPanel({ ship, vehicleId }: Props) {
  const [throttle, setThrottle] = useState<number>(100);
  const [seed, setSeed] = useState("");
  const [nEngines, setNEngines] = useState(33);
  const [vari, setVari] = useState(1);

  const send = (command: string, payload: Record<string, unknown> = {}) =>
    clientFeed.sendControl({ vehicle_id: vehicleId, command, ...payload });

  const newSeed = () => {
    const n = Number.parseInt(seed.trim(), 10);
    return Number.isFinite(n) && n > 0 ? n : 1 + Math.floor(Math.random() * 1_000_000);
  };

  const ack = ship.lastAck;
  const last = ship.ship;
  const hasRun = last != null;
  const meco = last?.meco === true;

  return (
    <div className="ctrl">
      <div className="ctrlrow">
        <button
          className="btn primary"
          onClick={() =>
            void send("start_run", {
              seed: newSeed(),
              n_engines: nEngines,
              vari_frac: vari / 100,
              lox_kg: 5_480_000,
              ch4_kg: 1_520_000,
              throttle_frac: throttle / 100,
            })
          }>
          {hasRun ? "Restart run" : "Start run"}
        </button>
        <button
          className="btn danger"
          disabled={!hasRun || meco}
          onClick={() => void send("meco")}>
          MECO
        </button>
      </div>

      <div className="ctrlrow">
        <label className="sliderlbl">
          throttle
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={throttle}
            onChange={(e) => {
              const v = Number(e.target.value);
              setThrottle(v);
              void send("throttle", { throttle_frac: v / 100 });
            }}
          />
          <span className="mono val">{throttle}%</span>
        </label>
      </div>

      <div className="ctrlrow">
        <label className="mini">
          <span>engines</span>
          <input
            type="number"
            min={1}
            max={33}
            value={nEngines}
            onChange={(e) => setNEngines(Number(e.target.value) || 1)}
          />
        </label>
        <label className="mini">
          <span>±vari %</span>
          <input
            type="number"
            min={0}
            max={5}
            step={0.1}
            value={vari}
            onChange={(e) => setVari(Number(e.target.value) || 0)}
          />
        </label>
        <label className="mini grow">
          <span>seed (empty = random redraw)</span>
          <input
            type="text"
            value={seed}
            placeholder="e.g. 42 or a name"
            onChange={(e) => setSeed(e.target.value)}
          />
        </label>
      </div>

      <div className="ack mono">
        {ack ? (
          ack.ok ? (
            <span className="ok" key={ack.ts_ms}>
              ✓ {String(ack.command ?? "")}
              {" "}
              {ack.run_id ? `· ${ack.run_id}` : ack.throttle_frac != null ? `· ${(ack.throttle_frac * 100).toFixed(0)}%` : "· ack"}
            </span>
          ) : (
            <span className="err" key={ack.ts_ms}>✗ {ack.error ?? ack.reason ?? "no ack"}</span>
          )
        ) : (
          <span className="dim">awaiting commands…</span>
        )}
      </div>
    </div>
  );
}
