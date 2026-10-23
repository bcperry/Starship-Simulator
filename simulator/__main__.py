"""CLI: python -m simulator [dt]. Prints a launch summary."""

import sys

from .rocket import Rocket, simulate, summary


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dt = float(argv[0]) if argv else 0.005
    rocket = Rocket()
    print(summary(rocket, simulate(rocket, dt=dt)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
