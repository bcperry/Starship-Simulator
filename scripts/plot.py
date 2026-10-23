"""Plot flight trajectory and phase portrait: uv run python scripts/plot.py"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from simulator import Rocket, simulate, summary

OUT = "trajectory.png"


def main() -> None:
    rocket = Rocket()
    res = simulate(rocket, dt=0.005)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))

    ax1.plot(res.t, res.h, color="tab:blue")
    ax1.axvline(res.burnout_time, color="tab:red", ls="--", label="burnout")
    ax1.set(xlabel="t (s)", ylabel="altitude (m)", title="Trajectory")
    ax1.legend()

    ax2.plot(res.v, res.h, color="tab:green")
    ax2.set(xlabel="v (m/s)", ylabel="altitude (m)", title="Phase portrait")

    fig.tight_layout()
    fig.savefig(OUT, dpi=110)
    print(f"saved {OUT}\n")
    print(summary(rocket, res))


if __name__ == "__main__":
    main()
