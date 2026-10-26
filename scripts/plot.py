"""Plot 3-D flight path plus downrange/altitude vs time:

    uv run python scripts/plot.py [--lat .. --lon .. --azimuth .. --pitch-start .. --ramp ..]
"""

from __future__ import annotations

import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from simulator import Pitch, Rocket, Site, simulate, summary

OUT = "trajectory.png"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=28.5584)
    ap.add_argument("--lon", type=float, default=-80.5984)
    ap.add_argument("--alt", type=float, default=10.0)
    ap.add_argument("--azimuth", type=float, default=90.0)
    ap.add_argument("--pitch-start", type=float, default=45.0)
    ap.add_argument("--ramp", type=float, default=12.0)
    args = ap.parse_args()

    site = Site(lat=args.lat, lon=args.lon, altitude=args.alt)
    pitch = Pitch(azimuth=args.azimuth, start=args.pitch_start, ramp_time=args.ramp)
    rocket = Rocket()
    res = simulate(rocket, site, pitch, dt=0.005)

    fig = plt.figure(figsize=(13, 5.5))
    ax3 = fig.add_subplot(1, 2, 1, projection="3d")
    ax3.plot(res.e, res.n, res.altitude, color="tab:blue", lw=1.5)
    ax3.plot(res.e[:1], res.n[:1], [res.altitude[0]], "o", color="tab:green", label="launch")
    bo = res.t.index(round(res.burnout_time, 6))
    ax3.plot(res.e[bo], res.n[bo], res.altitude[bo], "o", color="tab:red", label="burnout")
    ax3.plot(res.e[-1], res.n[-1], res.altitude[-1], "o", color="tab:orange", label="landing")
    ax3.set(xlabel="east (km)", ylabel="north (km)", zlabel="alt (km)", title="3-D flight path")
    ax3.set_xticks([])
    ax3.set_yticks([])
    ax3.set_zticks([])
    ax3.legend()

    ax2 = fig.add_subplot(1, 2, 2)
    time = res.t
    downrange = [0.0] * len(res.t)
    for i in range(len(res.t)):
        downrange[i] = (res.e[i] ** 2 + res.n[i] ** 2) ** 0.5
    ax2.plot(time, res.altitude, color="tab:blue", label="altitude")
    ax2.plot(time, downrange, color="tab:gray", ls="--", label="downrange")
    ax2.axvline(res.burnout_time, color="tab:red", ls=":", alpha=0.6, label="burnout")
    ax2.set(xlabel="t (s)", ylabel="m", title="Altitude & downrange")
    ax2.legend()
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(OUT, dpi=110)
    print(f"saved {OUT}")
    print(summary(rocket, site, res))


if __name__ == "__main__":
    main()
