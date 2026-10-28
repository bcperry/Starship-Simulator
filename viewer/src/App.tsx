import { useMemo } from "react";

import type { TelemetryMessage } from "@shared/types";

import { useFeed } from "./hooks/useFeed";

import { ControlPanel } from "./components/ControlPanel";
import { EngineGrid } from "./components/EngineGrid";
import { FlameIcon, PauseIcon, PlayIcon, RocketIcon } from "./components/Icons";
import { FleetBoard } from "./components/FleetBoard";
import { FlightPanel } from "./components/FlightPanel";
import { MessageFeed } from "./components/MessageFeed";
import { Panel } from "./components/Panel";
import { PhaseBar } from "./components/PhaseBar";
import { ShipPanel } from "./components/ShipPanel";
import { TrendChart } from "./components/TrendChart";
import { fleetSummary } from "./lib/aggregate";
import { clock } from "./lib/format";

export default function App() {
  const { snapshot, fleet, live, paused, togglePause } = useFeed();
  const { engines, counts, lines, source } = snapshot;

  const summary = useMemo(() => {
    const latest = new Map<string, TelemetryMessage>();
    for (const e of engines) if (e.telemetry) latest.set(e.id, e.telemetry);
    return fleetSummary(latest);
  }, [engines]);

  const hasData = engines.length > 0;
  const tNow = engines.length
    ? Math.max(...engines.map((e) => e.telemetry?.t ?? 0))
    : null;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="glyph">
            <RocketIcon style={{ color: "var(--accent)" }} />
          </span>
          <div>
            <h1>Starship · {snapshot.flight ? "LEO mission" : "Ship + Engine"}</h1>
            <div className="sub mono">
              {source
                ? `${source === "mock" ? "synthetic feed (mock)" : source === "flight" ? "LEO mission feed" : source === "stack" ? "full-stack feed" : "kafka broker"} · ${
                    summary.engine_count
                  } engines`
                : "connecting to feed…"}
              {tNow !== null && <> · T+ {tNow.toFixed(1)}s</>}
            </div>
          </div>
        </div>

        <span className="spacer" />

        <div className="status">
          <span className="pill">
            <span className={`dot ${live ? "live" : paused ? "" : "off"}`} />
            {paused ? "paused" : live ? "live" : "awaiting"}
          </span>
          <span className="pill">
            <span className="k">cmd</span>
            <strong className="mono">{counts.command.toLocaleString()}</strong>
          </span>
          <span className="pill">
            <span className="k">tel</span>
            <strong className="mono">{counts.telemetry.toLocaleString()}</strong>
          </span>
          <span className="pill" style={{ color: "var(--ink-3)" }}>
            {clock(Date.now())}
          </span>
          <button className={`iconbtn ${paused ? "active" : ""}`} onClick={togglePause}>
            {paused ? <PlayIcon /> : <PauseIcon />}
            {paused ? "resume" : "freeze"}
          </button>
        </div>
      </header>

      {snapshot.stack && <PhaseBar stack={snapshot.stack} />}

      <div className="grid">
        <div className="col">
          {snapshot.flight && <FlightPanel flight={snapshot.flight} />}

          <Panel title="Run control" hint={snapshot.stack ? "new run · throttle · cut active stage — drives the stack" : "new run · throttle · MECO — drives the ship sim"}>
            <ControlPanel ship={snapshot.ship} vehicleId={`starship`} stackMode={!!snapshot.stack} />
          </Panel>

          {!snapshot.stack && (
            <Panel title="Ship — shared propellant" hint="one LOX + one CH₄ tank feeds every engine"
              right={<span className={`${snapshot.ship.ship?.meco ? "dim" : "livepill"}`}>
                {snapshot.ship.ship ? (snapshot.ship.ship.meco ? "MECO" : "burning") : "standby"}
              </span>}>
              <ShipPanel ship={snapshot.ship} />
            </Panel>
          )}

          {snapshot.stack &&
            [snapshot.stack.sh.vehicle_id, snapshot.stack.ss.vehicle_id].map((vid) => {
              const label = vid === "starship" ? "Starship — upper stage" : vid === "super-heavy" ? "Super Heavy — first stage" : vid;
              const hint = vid === "starship" ? "own tank · 6× Raptor (3 VAC + 3 SL)" : "own tank · 33× Raptor";
              const view =
                snapshot.ships[vid] ??
                (snapshot.stack ? { ship: null, history: [], lastAck: null } : null);
              return (
                <Panel key={vid} title={label} hint={hint}
                  right={<span className={`${view?.ship?.meco ? "dim" : "livepill"}`}>
                    {view?.ship ? (view.ship.meco ? "MECO" : "burning") : "standby"}
                  </span>}>
                  <ShipPanel ship={view!} />
                </Panel>
              );
            })}

          <Panel title="Fleet status" hint="derived from latest per-engine telemetry"
            right={<span className={`${summary.any_cut ? "" : "dim"} small`}>
              {summary.any_cut ? "⚠ propellant shortage detected" : "all nominal"}
            </span>}>
            {hasData ? (
              <FleetBoard f={summary} />
            ) : (
              <div className="empty">
                <FlameIcon />
                <b>No engines reporting yet.</b>
                <span>
                  Start the feed — run the sim + Kafka (<code>docker compose up</code>) —
                  or the server with <code>SOURCE=mock</code> to develop the UI against a
                  synthetic 33-engine stream.
                </span>
              </div>
            )}
          </Panel>

          <Panel title="Thrust & propellant flow" hint="fleet-level trend, ~5 s per gridline">
            {fleet.length > 1 ? (
              <TrendChart points={fleet} window={300} />
            ) : (
              <div className="empty">Collecting trend…</div>
            )}
          </Panel>

          <Panel
            title="Engines"
            hint={`${summary.firing_count} firing · ${summary.cut_count} cut · ${summary.idle_count} idle`}
            right={
              summary.cut_count > 0 ? (
                <span className="badge" style={{ color: "var(--red)", background: "rgba(255,93,108,0.12)" }}>
                  {summary.cut_count} cut
                </span>
              ) : undefined
            }>
            <EngineGrid engines={engines} />
          </Panel>
        </div>

        <div className="col">
          <Panel title="Live wire" hint="most recent records, newest first">
            <MessageFeed lines={lines} />
          </Panel>
        </div>
      </div>

      <footer className="foot">
        <span className="up">wire</span>
        <span>
          vehicle ⇄ 33 engines over Kafka · <code>starship.engine.command</code> /{" "}
          <code>starship.engine.telemetry</code> · schema v1
        </span>
        <span style={{ marginLeft: "auto" }} className="mono">
          {source ?? "offline"}
        </span>
      </footer>
    </div>
  );
}
