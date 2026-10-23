"""CLI: uv run simulator [--lat .. --lon .. --alt .. --azimuth .. --pitch-start .. --ramp ..]"""

import argparse

from .rocket import Pitch, Rocket, Site, simulate, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Minimal 3-D rocket launch simulator")
    ap.add_argument("--lat", type=float, default=28.5584, help="launch latitude (deg)")
    ap.add_argument("--lon", type=float, default=-80.5984, help="launch longitude (deg)")
    ap.add_argument("--alt", type=float, default=10.0, help="launch altitude (m above sea level)")
    ap.add_argument("--azimuth", type=float, default=90.0, help="heading clockwise from north (deg)")
    ap.add_argument("--pitch-start", type=float, default=45.0, help="launch pitch from horizon (deg)")
    ap.add_argument("--ramp", type=float, default=12.0, help="s to ramp pitch to vertical")
    ap.add_argument("--no-rotation", action="store_true", help="ignore Earth's rotation")
    ap.add_argument("--dt", type=float, default=0.01, help="integrator step (s)")
    args = ap.parse_args(argv)

    site = Site(lat=args.lat, lon=args.lon, altitude=args.alt)
    pitch = Pitch(azimuth=args.azimuth, start=args.pitch_start, ramp_time=args.ramp)
    rocket = Rocket()
    res = simulate(rocket, site, pitch, earth_rotation=not args.no_rotation, dt=args.dt)
    print(summary(rocket, site, res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
