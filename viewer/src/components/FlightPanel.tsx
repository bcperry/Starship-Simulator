// Orbital mission panel — renders the Phase-9 `flight.state` document:
// 6-DOF state (position / velocity / aerodynamics), the coast-orbit
// elements (perigee / apogee / inclination), staging & banked reserve, and
// the mission targets. This is the "watch the LEO mission" surface.

import type { ReactNode } from "react";

import type { FlightStateMessage } from "@shared/types";
import { Panel } from "./Panel";

const LEO_KM = 100.0; // the LEO line — perigee above this establishes orbit

function Metric({ k, v, f, m, warn }: { k: string; v: ReactNode; f?: ReactNode; m?: string; warn?: boolean }) {
  return (
    <div className="metric" style={{ ["--m" as never]: m ?? (warn ? "var(--red)" : undefined) }}>
      <span className="accent-line" />
      <div className="k">{k}</div>
      <div className="v" style={{ color: m ?? (warn ? "var(--red)" : undefined) }}>{v}</div>
      {f && <div className="f">{f}</div>}
    </div>
  );
}

function Row({ k, v, accent }: { k: string; v: ReactNode; accent?: boolean }) {
  return (
    <div className="frow">
      <span className="fk">{k}</span>
      <span className={`fv mono${accent ? " acc" : ""}`}>{v}</span>
    </div>
  );
}

export function FlightPanel({ flight }: { flight: FlightStateMessage }) {
  const o = flight.orbit;
  const leo = o ? o.perigee_km >= LEO_KM : false;
  const altKm = flight.position.alt_m / 1000;
  const drKm = flight.position.downrange_m / 1000;
  const speedKms = flight.velocity.speed_ms / 1000;
  const leoTargetKm = (flight.mission.leo_target_alt_m ?? 0) / 1000;
  const reserveT = flight.staging.sh_boostback_reserve_kg / 1e3;
  const jettT = flight.staging.jettisoned_kg / 1e3;
  const sh = flight.ships?.["super-heavy"];
  const ss = flight.ships?.["starship"];

  const status = flight.crashed
    ? { txt: "RE-ENTRY", cls: "reentry" }
    : leo
      ? { txt: "LEO ESTABLISHED", cls: "leo" }
      : { txt: flight.phase.toUpperCase(), cls: flight.phase.toLowerCase() };

  return (
    <Panel
      title="Mission · orbital state"
      hint="6-DOF ECI ascent → gravity turn → LEO insertion"
      right={
        <span className={`pill run ${status.cls}`}>
          {flight.crashed ? "✕" : leo ? "◗" : "•"} {status.txt}
        </span>
      }>
      <div className="fp">
        {/* live position / velocity / aero board */}
        <div className="board fp-board">
          <Metric k="Altitude" v={<>{altKm.toFixed(1)}<small>km</small></>}
            f={`downrange ${drKm.toFixed(0)} km · T+ ${flight.t.toFixed(0)}s`} m="var(--accent)" />
          <Metric k="Velocity" v={<>{speedKms.toFixed(2)}<small>km/s</small></>}
            f={`heading ${flight.velocity.heading_deg.toFixed(0)}° · vert ${flight.velocity.up_ms >= 0 ? "+" : ""}${flight.velocity.up_ms.toFixed(0)} m/s`} m="var(--thrust)" />
          <Metric k="Mach" v={flight.aerodynamics.mach.toFixed(1)}
            f={`q ${Math.round(flight.aerodynamics.q_pa / 1000)} kPa · max ${(flight.aerodynamics.max_q_pa / 1000).toFixed(0)} kPa`} m="var(--pc)" />
          <Metric k="Δv spent" v={<>{(flight.stats.delta_v_ms / 1000).toFixed(2)}<small>km/s</small></>}
            f={`pitch ${flight.stats.pitch_deg.toFixed(1)}° · drag −${flight.stats.drag_loss_ms.toFixed(0)} m/s`} m="var(--mdot)" />
        </div>

        <div className="fp-cols">
          {/* orbit elements */}
          <div className="fp-block">
            <div className="fp-h">
              Coast orbit
              {o ? (
                <span className={`orbbadge ${leo ? "leo" : "sub"}`}>{leo ? "LEO" : o.perigee_km < 0 ? "UNBOUND" : "SUB-ORBITAL"}</span>
              ) : (
                <span className="orbbadge sub">{flight.crashed ? "RE-ENTRY" : "UNBOUND"}</span>
              )}
            </div>
            {o ? (
              <>
                <div className="orb-pair">
                  <Row k="Perigee" v={`${o.perigee_km.toFixed(1)} km`} accent={leo} />
                  <Row k="Apogee" v={`${o.apogee_km.toFixed(1)} km`} />
                </div>
                <div className="orb-bar" title={`perigee vs ${LEO_KM} km LEO line`}>
                  <span className="orb-barfill" style={{ width: `${Math.max(0, Math.min(1, o.perigee_km / Math.max(1, leoTargetKm))) * 100}%` }} />
                  <span className="orb-line" style={{ left: `${(LEO_KM / Math.max(1, leoTargetKm)) * 100}%` }} title={`${LEO_KM} km LEO line`} />
                </div>
                <div className="orb-grid">
                  <Row k="Inclination" v={`${o.i_deg.toFixed(2)}°`} accent />
                  <Row k="Eccentricity" v={o.e.toFixed(4)} />
                  <Row k="Semi-major" v={`${o.a_km.toFixed(1)} km`} />
                  <Row k="RAAN" v={`${o.raan_deg.toFixed(1)}°`} />
                </div>
              </>
            ) : (
              <div className="fp-note">
                {flight.crashed
                  ? "Trajectory is sub-orbital — perigee below the surface (re-entry)."
                  : "Coast orbit not established — the trajectory is unbound (past local escape). Restart the run to capture into LEO."}
              </div>
            )}
          </div>

          {/* staging & reserve */}
          <div className="fp-block">
            <div className="fp-h">Staging & reserve</div>
            <div className="orb-grid">
              <Row k="Super Heavy MECO" v={flight.staging.sh_meco_t != null ? `T+ ${flight.staging.sh_meco_t.toFixed(1)}s` : "—"} accent={flight.staging.sh_meco_t != null} />
              <Row k="Starship SECO" v={flight.staging.ss_seco_t != null ? `T+ ${flight.staging.ss_seco_t.toFixed(1)}s` : "—"} accent={flight.staging.ss_seco_t != null} />
              <Row k="SH banked reserve" v={`${reserveT.toFixed(0)} t`} accent={reserveT > 0} />
              <Row k="Jettisoned (stack)" v={`${jettT.toFixed(0)} t`} />
            </div>
            {(sh || ss) && (
              <div className="fp-vessels">
                {sh && (
                  <div className={`fp-v ${sh.meco ? "meco" : "fire"}`}>
                    <b>SUPER HEAVY</b>
                    <span className="mono">{sh.firing} firing · {sh.failed} failed · {((sh.tank_ox_kg + sh.tank_ch4_kg) / 1e3).toFixed(0)} t</span>
                  </div>
                )}
                {ss && (
                  <div className={`fp-v ${ss.meco ? "meco" : "fire"}`}>
                    <b>STARSHIP</b>
                    <span className="mono">{ss.firing} firing · {ss.failed} failed · {((ss.tank_ox_kg + ss.tank_ch4_kg) / 1e3).toFixed(0)} t</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* mission targets footer */}
        <div className="fp-meta mono">
          <span>target incl <b>{flight.mission.target_inc_deg?.toFixed(1)}°</b></span>
          <span>payload <b>{(flight.mission.payload_kg ?? 0) / 1000} t</b></span>
          <span>LEO target <b>{leoTargetKm.toFixed(0)} km</b></span>
          <span>SH MECO resid <b>{(flight.mission.sh_meco_resid_frac ?? 0) * 100}%</b></span>
        </div>
      </div>
    </Panel>
  );
}
