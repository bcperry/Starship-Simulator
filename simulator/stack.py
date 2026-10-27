"""The full stack: Super Heavy (33x Raptor V2) + Starship (3x Raptor V + 3x Raptor).

One deterministic mission timeline over the shared physics core:

    liftoff  ->  33 first-stage engines burn the SH tank
    MECO     ->  first stage cuts when its tank can no longer feed the fleet
    staging  ->  a short coast (``staging_delay_s``) with both fleets quiet
    upper    ->  Starship's six engines light and burn the second tank
    (SECO)   ->  upper stage cuts when ITS tank drains; both fleets coast

Each vessel is a `Ship` (one tank, one authority, per-engine variability,
high-frequency jitter, exponential failures -- the same sim everywhere).
The stack never reuses propellant across stages and never shares a tank;
it only sequences the two burns and reports a ``stack.status`` document
besides each vessel's own engine + ship documents.

Controls mirror the ship's: ``start_run`` (relight the whole stack),
``meco``/``seco`` (cut the *active* stage: first stage during ascent,
Starship during the upper burn), and ``throttle`` (applies to both).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .ship import Ship
from .starship import Starship

__all__ = [
    "PHASE_DONE",
    "PHASE_LIFTOFF",
    "PHASE_STAGING",
    "PHASE_UPPER",
    "Stack",
    "StackConfig",
]

PHASE_LIFTOFF = "liftoff"
PHASE_STAGING = "staging"
PHASE_UPPER = "upper"
PHASE_DONE = "done"

DEFAULT_STAGING_DELAY_S = 4.0


@dataclass(frozen=True)
class StackConfig:
    """Mission settings shared by the whole stack."""

    seed: int = 7
    staging_delay_s: float = DEFAULT_STAGING_DELAY_S
    sh_expected_failures: float = 2.0
    ss_expected_failures: float = 1.0
    dt: float = 0.01  # master timestep; must match both vessels

    def __post_init__(self) -> None:
        if self.staging_delay_s < 0.0:
            raise ValueError(f"staging_delay_s must be >= 0, got {self.staging_delay_s}")
        if self.dt <= 0.0:
            raise ValueError(f"dt must be > 0, got {self.dt}")


class Stack:
    """Super Heavy + Starship as one vehicle, sequenced over one mission."""

    def __init__(self, sh: Ship, ss: Starship, config: StackConfig | None = None) -> None:
        self.sh = sh
        self.ss = ss
        base = config or StackConfig()
        # master clock follows the first stage's dt (the two must agree)
        self.config = dataclasses.replace(base, dt=sh.config.dt)
        self._reset_phases()

    # -- shared duck-type surface for shiploop.run_stdio / _coasted ---------
    @property
    def _members(self) -> list:
        """All engine slots on both stages (used by the coast-down check)."""
        return self.sh._members + self.ss._members

    @property
    def seq(self) -> int:
        return self.sh.seq

    @property
    def t(self) -> float:
        return self.sh.t

    @property
    def meco(self) -> bool:
        """Coast condition: both stages are cut and burned down."""
        return bool(self.sh.meco and self.ss.meco)

    @property
    def active_vehicle(self) -> str | None:
        if self.phase == PHASE_LIFTOFF:
            return self.sh.config.vehicle_id
        if self.phase in (PHASE_UPPER, PHASE_DONE):
            return self.ss.config.vehicle_id
        return None  # staging coast: nobody burning

    # -- phase state ---------------------------------------------------------
    def _reset_phases(self) -> None:
        self.phase = PHASE_LIFTOFF
        self.staging_left = self.config.staging_delay_s

    # -- control -------------------------------------------------------------
    def start_run(
        self,
        seed: int | None = None,
        expected_failures_sh: float | None = None,
        expected_failures_ss: float | None = None,
        noise_frac: float | None = None,
    ) -> dict[str, str]:
        """Relight the whole stack from t=0 (both tanks refreshed)."""
        sh_kwargs: dict = {}
        if seed is not None:
            sh_kwargs["seed"] = seed
        if expected_failures_sh is not None:
            sh_kwargs["expected_failures_per_run"] = expected_failures_sh
        if noise_frac is not None:
            sh_kwargs["noise_frac"] = noise_frac
        self.sh.start_run(**sh_kwargs)

        ss_kwargs: dict = {}
        if seed is not None:
            ss_kwargs["seed"] = seed
        if expected_failures_ss is not None:
            ss_kwargs["expected_failures_per_run"] = expected_failures_ss
        if noise_frac is not None:
            ss_kwargs["noise_frac"] = noise_frac
        self.ss.start_run(**ss_kwargs)

        self._reset_phases()
        return {"sh": self.sh.run_id, "ss": self.ss.run_id}

    def apply_control(self, message: dict) -> dict:
        """The ship control wire, pointed at the active stage."""
        command = message.get("command")
        if command == "start_run":
            result = self.start_run(
                seed=message.get("seed"),
                expected_failures_sh=message.get("sh_expected_failures"),
                expected_failures_ss=message.get("ss_expected_failures"),
                noise_frac=message.get("noise_frac"),
            )
            return {
                "ok": True,
                "command": command,
                "vehicle_id": "stack",
                "run_id": {"sh": result["sh"], "ss": result["ss"]},
            }
        if command in ("meco", "seco"):
            target = self.sh if self.phase == PHASE_LIFTOFF else self.ss
            return target.apply_control({**message, "command": "meco"})
        if command == "throttle":
            out = self.sh.apply_control(message)
            self.ss.apply_control(message)
            out["vehicle_id"] = "stack"
            out["applied_to"] = [self.sh.config.vehicle_id, self.ss.config.vehicle_id]
            return out
        raise ValueError(f"unknown stack control command: {command!r}")

    # -- simulation ----------------------------------------------------------
    def step(self) -> list[dict]:
        """Advance one timestep; emit both vessels' docs + stack.status.

        The first stage publishes during the burn (and coasts after MECO).
        Starship only publishes from staging onward; before that its engines
        are cold and it has not yet ignited.
        """
        sh_docs = self.sh.step()

        if self.phase == PHASE_LIFTOFF and self.sh.meco:
            self.phase = PHASE_STAGING
            self.staging_left = self.config.staging_delay_s

        ss_docs: list[dict] = []
        if self.phase in (PHASE_STAGING, PHASE_UPPER, PHASE_DONE):
            if self.phase == PHASE_STAGING:
                self.staging_left = max(0.0, self.staging_left - self.sh.config.dt)
                if self.staging_left <= 0.0:
                    self.phase = PHASE_UPPER
            if self.phase in (PHASE_UPPER, PHASE_DONE):
                ss_docs = self.ss.step()
                if self.phase == PHASE_UPPER and self.ss.meco:
                    self.phase = PHASE_DONE

        docs = [*sh_docs, *ss_docs]
        docs.append(self._stack_status())
        return docs

    def status(self) -> dict:
        """Current stack.status document (without stepping)."""
        return self._stack_status()

    def _firing(self, ship: Ship) -> int:
        return (
            sum(1 for m in ship._members if not m.failed and m.state.chamber_pressure > 0.0)
            if not ship.meco
            else 0
        )

    def _tank_frac(self, ship: Ship) -> float:
        total = ship.config.tank_ox_kg + ship.config.tank_ch4_kg
        if total <= 0.0:
            return 0.0
        return (ship.tank_ox_kg + ship.tank_ch4_kg) / total

    def _stack_status(self) -> dict:
        sh_frac = self._tank_frac(self.sh)
        ss_frac = self._tank_frac(self.ss)
        n_total = (
            self.sh.config.tank_ox_kg
            + self.sh.config.tank_ch4_kg
            + self.ss.config.tank_ox_kg
            + self.ss.config.tank_ch4_kg
        )
        remaining = (
            self.sh.tank_ox_kg
            + self.sh.tank_ch4_kg
            + self.ss.tank_ox_kg
            + self.ss.tank_ch4_kg
        )
        frac = remaining / n_total if n_total > 0 else 0.0
        return {
            "v": 1,
            "type": "stack.status",
            "vehicle_id": "stack",
            "t": self.sh.t,
            "phase": self.phase,
            "active_vehicle": self.active_vehicle,
            "staging_in_s": self.staging_left if self.phase == PHASE_STAGING else None,
            "sh": {
                "vehicle_id": self.sh.config.vehicle_id,
                "run_id": self.sh.run_id,
                "meco": self.sh.meco,
                "firing": self._firing(self.sh),
                "failed": sum(1 for m in self.sh._members if m.failed),
                "tank_frac": sh_frac,
            },
            "ss": {
                "vehicle_id": self.ss.config.vehicle_id,
                "run_id": self.ss.run_id,
                "meco": self.ss.meco,
                "firing": self._firing(self.ss),
                "failed": sum(1 for m in self.ss._members if m.failed),
                "tank_frac": ss_frac,
                "ignited": self.phase in (PHASE_UPPER, PHASE_DONE),
            },
            "tank": {"frac": frac, "total_kg": remaining},
            "meco": self.meco,
        }
