"""Full 3-D launch sim over stdio.

Spawns the Super Heavy + Starship fleet (per-engine variability / jitter /
exponential failures) driving a real ECI trajectory (J2 + drag + pitch-over
+ staging/mass-jettison). Emits JSON documents on stdout — engine.command /
engine.telemetry / ship.status / stack.status per step plus a `flight.state`
document (position, velocity, q, Mach, orbit elements, stats, staging) as
every document batch's last line — and accepts ship.control on stdin
(start_run / meco / throttle, plus optional flight overrides).

Run:
    .venv/bin/python -m simulator.flightloop --speed 20
    .venv/bin/python -m simulator.flightloop --seed 7 --sh-failures 2 --ss-failures 1
"""

from __future__ import annotations

import argparse
import sys

from .flight import FlightCfg, StackFlight
from .shiploop import run_stdio

__all__ = ["build_parser", "make_flight_loop", "main"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Starship full-stack 3-D launch simulator")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--speed", type=float, default=20.0, help="sim-time seconds per real second")
    p.add_argument("--emit-every", type=int, default=1)
    p.add_argument("--log-every", type=int, default=2000)
    p.add_argument("--dt", type=float, default=0.01)
    p.add_argument("--vari", type=float, default=0.01, help="per-engine thrust variability")
    p.add_argument("--throttle", type=float, default=1.0)
    p.add_argument("--ambient", type=float, default=101_325.0)
    p.add_argument("--noise", type=float, default=0.02, help="per-step engine jitter fraction")
    p.add_argument("--sh-failures", type=float, default=2.0)
    p.add_argument("--ss-failures", type=float, default=1.0)
    # mission geometry
    p.add_argument("--lat", type=float, default=28.5623, help="launch latitude (deg N)")
    p.add_argument("--lon", type=float, default=-80.5768, help="launch longitude (deg E)")
    p.add_argument("--site-alt", type=float, default=3.0, help="site altitude (m)")
    p.add_argument("--target-inc", type=float, default=28.5, help="target inclination (deg)")
    # vehicle (defaults tuned with FlightCfg so the full stack reaches LEO)
    p.add_argument("--sh-engines", type=int, default=33)
    p.add_argument("--sh-lox-kg", type=float, default=3_286_957.0)
    p.add_argument("--sh-ch4-kg", type=float, default=913_043.0)
    p.add_argument("--sh-dry-kg", type=float, default=200_000.0)
    p.add_argument("--ss-lox-kg", type=float, default=939_130.0)
    p.add_argument("--ss-ch4-kg", type=float, default=260_870.0)
    p.add_argument("--ss-dry-kg", type=float, default=150_000.0)
    p.add_argument("--payload-kg", type=float, default=50_000.0)
    p.add_argument("--cda", type=float, default=30.0, help="ballistic coefficient area (m^2)")
    # gravity-turn guidance
    p.add_argument("--pitch-pad-clear-s", type=float, default=8.0, help="s of straight-up pad clearance")
    p.add_argument("--pitch-v-full", type=float, default=2400.0, help="m/s at which pitch-over is ~complete")
    p.add_argument("--pitch-fpa-min-deg", type=float, default=4.0, help="no-dive floor above horizon (deg)")
    # stage guidance
    p.add_argument("--sh-meco-resid-frac", type=float, default=0.20,
                   help="SH propellant kept for boostback+landing")
    p.add_argument("--leo-alt-m", type=float, default=185_000.0,
                   help="target LEO insertion altitude (reference; SECO fires on the perigee floor)")
    p.add_argument("--staging-delay", type=float, default=4.0)
    return p


def make_flight_loop(args: argparse.Namespace) -> StackFlight:
    return StackFlight(
        FlightCfg(
            seed=args.seed,
            site_lat_deg=args.lat,
            site_lon_deg=args.lon,
            site_alt_m=args.site_alt,
            target_inc_deg=args.target_inc,
            sh_dry_kg=args.sh_dry_kg,
            ss_dry_kg=args.ss_dry_kg,
            payload_kg=args.payload_kg,
            sh_lox_kg=args.sh_lox_kg,
            sh_ch4_kg=args.sh_ch4_kg,
            ss_lox_kg=args.ss_lox_kg,
            ss_ch4_kg=args.ss_ch4_kg,
            cda_m2=args.cda,
            pitch_pad_clear_s=args.pitch_pad_clear_s,
            pitch_v_full=args.pitch_v_full,
            pitch_fpa_min_deg=args.pitch_fpa_min_deg,
            sh_meco_resid_frac=args.sh_meco_resid_frac,
            leo_target_alt_m=args.leo_alt_m,
            leo_perigee_floor_km=130.0,
            staging_delay_s=args.staging_delay,
            n_engines=args.sh_engines,
            vari_frac=args.vari,
            throttle_frac=args.throttle,
            sh_expected_failures=args.sh_failures,
            ss_expected_failures=args.ss_failures,
            noise_frac=args.noise,
            dt=args.dt,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def log(seq: int, status) -> None:
        if args.log_every > 0 and seq % args.log_every == 0:
            alt = status.get("position", {}).get("alt_m", 0.0)
            print(
                f"flight t={status.get('t', 0.0):.1f}s alt={alt/1000.0:.1f}km "
                f"phase={status.get('phase')}",
                file=sys.stderr,
            )

    fl = make_flight_loop(args)
    return run_stdio(fl, speed=args.speed, emit_every=args.emit_every, log=log)


if __name__ == "__main__":
    raise SystemExit(main())
