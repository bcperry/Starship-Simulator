"""Command-driven 33-engine fleet demo (vehicle -> engine wire, in-process).

The vehicle sim side, per timestep:

  1. builds one ``engine.command`` per engine (``to_command``)
     and offers it to that engine's ``InMemoryCommandSource``;
  2. each of the 33 ``EngineProcess`` instances polls its own source and
     takes exactly one physics step per command — the command stream is the
     clock, so there is no engine-side loop to drift;
  3. the 33 telemetry rows are folded into one vehicle-level row with
     ``aggregate_fleet``.

Engine sh-32 carries a finite 500 kg propellant load, so its feeds starve
mid-demo and it reports ``state="cut"``: ``any_cut=True``, ``firing_count=32``.

Over Kafka the same wiring is: vehicle publishes 33 ``engine.command``
records keyed by engine id -> one ``python -m simulator.telemetry`` process
per engine consumes them -> 33 ``engine.telemetry`` records back.

Run: python scripts/command_fleet_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulator.engine import RaptorEngine
from simulator.telemetry import (
    EngineProcess,
    InMemoryCommandSource,
    InMemoryPublisher,
    aggregate_fleet,
    to_command,
)

N = 33
CUT_ID = f"sh-{N - 1:02d}"
DT = 0.01
THROTTLE = 1.0
AMBIENT_SEA = 101_325.0
LOAD_KG = 500.0


def main() -> int:
    pubs = [InMemoryPublisher() for _ in range(N)]
    fleet = []
    for i in range(N):
        eid = f"sh-{i:02d}"
        engine = RaptorEngine(
            throttle_time_constant=0.05,
            propellant_mass=LOAD_KG if eid == CUT_ID else 0.0,
        )
        src = InMemoryCommandSource()
        fleet.append((eid, src, EngineProcess(eid, engine, src, pubs[i])))

    def vehicle_step(n: int) -> dict:
        """One sim timestep: N commands out, N steps in, one fleet row."""
        msgs = []
        for eid, src, proc in fleet:
            cmd = to_command(
                engine_id=eid, seq=n, throttle_demand_frac=THROTTLE,
                ambient_pressure_pa=AMBIENT_SEA, dt=DT,
            )
            src.offer(cmd)                       # vehicle -> engine
            msgs.append(proc.step_on_command(    # engine: one step per command
                src.poll(0.0)))
        return aggregate_fleet(msgs)

    header = (f"{'step':>6} {'thrust (MN)':>12} {'mdot (t/s)':>11} "
              f"{'firing':>7} {'cut':>4} {'sh-32 load (kg)':>16}")
    print(f"command-driven fleet: {N} engines at {THROTTLE:.0%}, dt={DT}s; "
          f"{CUT_ID} carries a finite {LOAD_KG:.0f} kg load")
    print(header)
    print("-" * len(header))

    step_no = 0
    row = None
    while step_no < 5000:
        step_no += 1
        row = vehicle_step(step_no)
        cut_load = fleet[N - 1][2].propellant_remaining
        if step_no <= 6 or step_no % 250 == 0 or row["any_cut"]:
            print(f"{step_no:>6} {row['thrust_n'] / 1e6:>12.2f} "
                  f"{row['mdot_kg_s']['total'] / 1e3:>11.1f} "
                  f"{row['firing_count']:>7} {row['cut_count']:>4} "
                  f"{cut_load:>16.0f}")
        if row["any_cut"]:
            break

    print("-" * len(header))
    print(f"step {step_no}: {CUT_ID} starved -> state='cut' "
          f"({row['firing_count']} of {N} still firing)")
    print(f"aggregate_fleet row: any_cut={row['any_cut']} "
          f"all_firing={row['all_firing']} firing_count={row['firing_count']} "
          f"cut_count={row['cut_count']} min_propellant_remaining_kg="
          f"{row['min_propellant_remaining_kg']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
