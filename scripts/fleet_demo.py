"""33-engine Super Heavy telemetry fan-out demo (no broker needed).

Boots 33 Raptor instances that all publish to one shared InMemoryPublisher,
steps the fleet, folds each timestep into a single fleet row with
aggregate_fleet, and shows the vehicle sim's view: total thrust, firing /
cut counts, and the weakest engine's propellant margin.

Run: uv run python scripts/fleet_demo.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator.engine import RaptorEngine
from simulator.telemetry import (
    InMemoryPublisher,
    TelemetryEngine,
    aggregate_fleet,
)

N_ENGINES = 33
DT = 0.01
AMBIENT = 101_325.0


def main() -> None:
    # all engines publish to ONE publisher (like one shared Kafka topic)
    pub = InMemoryPublisher()
    fleet = [
        TelemetryEngine(
            engine_id=f"sh-{i:02d}",
            engine=RaptorEngine(throttle_time_constant=0.05),
            publisher=pub,
            vehicle_id="super-heavy",
            dt=DT,
        )
        for i in range(N_ENGINES)
    ]

    print(f"igniting {N_ENGINES} engines (sea level, {AMBIENT} Pa)\n")
    print(f"{'t(s)':>6} {'F-tot(MN)':>10} {'firing':>7} {'cut':>4} "
          f"{'minIsp(s)':>10}")

    n_steps = 200  # 2.0 s
    sampled = None
    for i in range(1, n_steps + 1):
        throttle = min(1.0, i / 50)  # 50-step spool-up ramp, then full
        # one step each engine -> one TelemetryMessage each -> fleet row
        sampled = aggregate_fleet([eng.step(throttle=throttle) for eng in fleet])
        if i % 50 == 0:
            print(f"{i * DT:6.1f} {sampled['thrust_n'] / 1e6:10.2f} "
                  f"{sampled['firing_count']:7d} {sampled['cut_count']:4d} "
                  f"{sampled['min_isp_s']:10.1f}")

    final = sampled
    assert final["firing_count"] == N_ENGINES, "all 33 should be firing at steady state"
    print(f"\nsteady-state fleet: {final['thrust_n'] / 1e6:.2f} MN total, "
          f"{final['firing_count']} firing, {final['cut_count']} cut")

    # now prove the vehicle sim can consume the wire format: drain everything
    drained = pub.drain()
    per_engine: dict[str, list[int]] = {}
    for engine_id, msg in drained:
        per_engine.setdefault(engine_id, []).append(msg["seq"])
    assert len(per_engine) == N_ENGINES, "every engine must have delivered"
    assert all(seqs == list(range(1, n_steps + 1)) for seqs in per_engine.values())
    print(f"vehicle sim drained {len(drained)} messages from the wire: "
          f"{N_ENGINES} engine streams x {n_steps} steps, per-engine order intact")

    # one engine starves (short load): the fleet row must notice
    print("\ncutting engine sh-17 with an exhausted load:")
    fleet[17] = TelemetryEngine("sh-17",
                                RaptorEngine(propellant_mass=500.0,
                                             throttle_time_constant=0.02),
                                pub, vehicle_id="super-heavy", dt=DT)
    while fleet[17].propellant_remaining > 0.01 * 500.0:
        fleet[17].step(throttle=1.0)
    msgs = [eng.step(throttle=1.0) for eng in fleet]
    row = aggregate_fleet(msgs)
    assert row["any_cut"] is True and row["firing_count"] == N_ENGINES - 1
    assert next(m for m in msgs if m["engine_id"] == "sh-17")["state"] == "cut"
    print(f"  fleet row: firing={row['firing_count']} cut={row['cut_count']} "
          f"thrust={row['thrust_n'] / 1e6:.2f} MN (vehicle sim sees the loss)")
    print("\nexample fleet row (JSON, what the vehicle sim consumes):")
    print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
