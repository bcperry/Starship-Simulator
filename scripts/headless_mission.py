"""Headless full-stack mission: fly to LEO (or crash), print a trajectory table.

    .venv/bin/python scripts/headless_mission.py
"""
from __future__ import annotations

import argparse

from simulator.flight import FlightCfg, make_flight


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--every", type=float, default=10.0, help="print a row every N sim-seconds")
    args = p.parse_args()

    cfg = FlightCfg()
    flight = make_flight(cfg)
    flight.stack.start_run()

    print(f"payload={cfg.payload_kg:.0f} kg  sh_dry={cfg.sh_dry_kg:.0f}  ss_dry={cfg.ss_dry_kg:.0f}")
    print(f"SH LOX/CH4={cfg.sh_lox_kg:.0f}/{cfg.sh_ch4_kg:.0f} "
          f"SS LOX/CH4={cfg.ss_lox_kg:.0f}/{cfg.ss_ch4_kg:.0f}")
    print(f"heading={cfg.heading_deg:.2f} deg  target_inc={cfg.target_inc_deg} deg  "
          f"SH_reserve={cfg.sh_meco_resid_frac:.0%}  pitch_v_full={cfg.pitch_v_full:.0f} m/s")
    print("-" * 110)
    print(f"{'t':>6} {'alt_km':>8} {'v_ms':>7} {'FPA':>6} {'thrust_MN':>9} "
          f"{'mass_kt':>8} {'SH_kt':>7} {'SS_kt':>7} {'phase':>8}")
    print("-" * 110)

    next_print = 0.0
    while not flight.done and not flight.model.crashed:
        flight.step()
        t = flight.t
        if t < next_print:
            continue
        next_print += args.every
        st = flight.status()
        alt = st["position"]["alt_m"] / 1000.0
        v = st["velocity"]["speed_ms"]
        fpa = st["stats"]["pitch_deg"]
        thr = st["thrust_n"] / 1e6
        mass = st["mass_kg"] / 1e3
        sh = (st["ships"]["super-heavy"]["tank_ox_kg"] + st["ships"]["super-heavy"]["tank_ch4_kg"]) / 1e3
        ss = (st["ships"]["starship"]["tank_ox_kg"] + st["ships"]["starship"]["tank_ch4_kg"]) / 1e3
        print(f"{t:6.1f} {alt:8.2f} {v:7.1f} {fpa:6.2f} {thr:9.2f} {mass:8.1f} {sh:7.1f} {ss:7.1f} {st['phase']:>8}")

    st = flight.status()
    print("-" * 110)
    m = flight.model
    if m.crashed:
        print(f"RESULT: CRASH  t={m.crash_t:.1f}s  (apogee {m.apogee_m/1000:.1f} km)")
    elif st["orbit"]:
        o = st["orbit"]
        print(f"RESULT: ORBIT  a={o['a_km']:.0f} km  e={o['e']:.4f}  i={o['i_deg']:.2f} deg")
        print(f"            perigee={o['perigee_km']:.1f} km  apogee={o['apogee_km']:.1f} km")
    print(f"T_end={flight.t:.1f}s  apogee={m.apogee_m/1000:.1f} km  "
          f"max_q={m.max_q_pa/1e3:.0f} kPa  dv={m.delta_v_ms:.0f} m/s")
    # SH banking check
    sh_res = st["ships"]["super-heavy"]["tank_ox_kg"] + st["ships"]["super-heavy"]["tank_ch4_kg"]
    print(f"SH residual propellant at/after staging: {sh_res:.0f} kg "
          f"({(st['staging']['sh_meco_t'])}s MECO)  SS_SECO={st['staging']['ss_seco_t']}")


if __name__ == "__main__":
    main()
