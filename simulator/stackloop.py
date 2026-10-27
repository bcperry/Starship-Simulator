"""Full-stack run control (Super Heavy + Starship) via stdio.

Emits JSON documents to stdout (engine.command / engine.telemetry per
engine of BOTH vessels, ship.status per vessel, and stack.status as the
aggregate), and accepts ship control messages on stdin:

  {"type":"ship.control","v":1,"command":"start_run", ...}   relight stack
  {"type":"ship.control","v":1,"command":"meco", ...}         cut the active stage
  {"type":"ship.control","v":1,"command":"throttle","v":0.8}  both vessels

The pacing / reader / ack plumbing is the same as the ship loop.
"""

from __future__ import annotations

import argparse
import sys

from .ship import Ship, ShipConfig
from .shiploop import run_stdio
from .stack import Stack
from .starship import (
    DEFAULT_STARSHIP_TANK_CH4_KG,
    DEFAULT_STARSHIP_TANK_OX_KG,
    Starship,
)

__all__ = ["build_parser", "build_stack", "main"]


def build_stack(args: argparse.Namespace) -> Stack:
    """Both stages on the same physics core; one tank per vehicle."""
    sh = Ship(
        ShipConfig(
            vehicle_id="super-heavy",
            n_engines=args.sh_engines,
            seed=args.seed,
            vari_frac=args.vari,
            tank_ox_kg=args.sh_lox_kg,
            tank_ch4_kg=args.sh_ch4_kg,
            throttle_frac=args.throttle,
            ambient_pressure_pa=args.ambient,
            dt=args.dt,
            expected_failures_per_run=args.sh_failures,
            noise_frac=args.noise,
        )
    )
    ss = Starship(
        ShipConfig(
            vehicle_id="starship",
            n_engines=6,
            engine_prefix="ss",
            seed=args.seed,
            vari_frac=args.vari,
            tank_ox_kg=args.ss_lox_kg,
            tank_ch4_kg=args.ss_ch4_kg,
            throttle_frac=args.throttle,
            ambient_pressure_pa=args.ambient,
            dt=args.dt,
            expected_failures_per_run=args.ss_failures,
            noise_frac=args.noise,
        )
    )
    return Stack(sh, ss)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Full-stack (Super Heavy + Starship) simulator")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--speed", type=float, default=10.0)
    p.add_argument("--emit-every", type=int, default=1)
    p.add_argument("--log-every", type=int, default=500)
    p.add_argument("--dt", type=float, default=0.01)
    p.add_argument("--vari", type=float, default=0.01)
    p.add_argument("--throttle", type=float, default=1.0)
    p.add_argument("--ambient", type=float, default=101_325.0)
    p.add_argument("--noise", type=float, default=0.02, help="per-step engine jitter (fraction)")
    p.add_argument("--staging-delay", type=float, default=4.0, help="_ss ignition delay after MECO (s)")
    p.add_argument("--sh-engines", type=int, default=33)
    p.add_argument("--sh-lox-kg", type=float, default=5_480_000.0)
    p.add_argument("--sh-ch4-kg", type=float, default=1_520_000.0)
    p.add_argument("--sh-failures", type=float, default=2.0, help="expected failures in the SH burn")
    p.add_argument("--ss-lox-kg", type=float, default=DEFAULT_STARSHIP_TANK_OX_KG)
    p.add_argument("--ss-ch4-kg", type=float, default=DEFAULT_STARSHIP_TANK_CH4_KG)
    p.add_argument("--ss-failures", type=float, default=1.0, help="expected failures in the Starship burn")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def log(seq: int, status) -> None:
        if args.log_every > 0 and seq % args.log_every == 0:
            tank = status["tank"]
            print(
                f"stack t={status['t']:.1f}s phase={status['phase']} "
                f"tank={tank['frac']*100:.1f}%",
                file=sys.stderr,
            )

    stack = build_stack(args)
    return run_stdio(stack, speed=args.speed, emit_every=args.emit_every, log=log)


if __name__ == "__main__":
    raise SystemExit(main())
