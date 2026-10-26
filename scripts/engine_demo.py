"""Raptor engine demo: uv run python scripts/engine_demo.py

Prints the spec sheet, a throttle ramp response, and a short vacuum burn.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator.engine import RaptorEngine, run, summary

E = RaptorEngine()


def main() -> None:
    sl = run(E, throttle=1.0, duration=2.0, ambient_pressure=101_325.0)[-1]
    vac = run(E, throttle=1.0, duration=2.0, ambient_pressure=0.0)[-1]
    print(summary(E, sl, vac))
    print()

    print("throttle ramp 0.4 -> 1.0 (sea-level ambient):")
    profile = [0.4 if i < 200 else 1.0 for i in range(400)]
    out = run(E, throttle=profile, duration=4.0, dt=0.01, ambient_pressure=101_325.0)
    for o in out[::50]:
        print(f"  t={o.t:5.2f}s  throttle={o.throttle:4.2f}  "
              f"Pc={o.chamber_pressure / 1e6:6.2f} MPa  "
              f"F={o.thrust / 1e6:6.3f} MN  Isp={'-' if o.isp <= 0 else format(o.isp, '6.1f') + 's'}  "
              f"mdot={o.mdot_total:7.1f} kg/s")
    print()

    print("100 s vacuum burn on a 4000 kg propellant load:")
    eng = RaptorEngine(propellant_mass=4000.0, throttle_time_constant=0.1)
    out = run(eng, throttle=1.0, duration=10.0, dt=0.01, ambient_pressure=0.0)
    print(f"  propellant remaining: {out[-1].propellant_remaining:7.3f} kg")
    assert out[-1].propellant_remaining == 0.0, "load should be exhausted"
    print("  (load exhausted - feeds starved, engine cut off)")


if __name__ == "__main__":
    main()
