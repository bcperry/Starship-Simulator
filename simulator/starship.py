"""Starship (the upper-stage / spacecraft vehicle).

Same Raptor sim, different params, different fleet: three **Raptor Vacuum**
(vacuum-optimised high-expansion nozzles) plus three **Raptor V2 (SL)**
sea-level engines, all fed from one Starship propellant tank.

Built on the same `Ship` core as Super Heavy (one tank, one authority,
per-engine variability + high-frequency jitter + exponential failures),
so the whole stack shares one physics path.
"""

from __future__ import annotations

import dataclasses

from .engine import RaptorEngine, raptor_v2, raptor_vacuum
from .ship import EngineGroup, Ship, ShipConfig

__all__ = [
    "DEFAULT_STARSHIP_TANK_CH4_KG",
    "DEFAULT_STARSHIP_TANK_OX_KG",
    "STARSHIP_SEALEVEL_ENGINES",
    "STARSHIP_VACUUM_ENGINES",
    "Starship",
    "make_starship",
]

#: Starship's six Raptor engines: 3 vacuum-optimised + 3 sea-level.
STARSHIP_VACUUM_ENGINES = 3
STARSHIP_SEALEVEL_ENGINES = 3

#: Starship's propellant load (its own tank -- the stack keeps the two tanks
#: separate). ~540 t LOX / ~150 t CH4: mixture ratio 3.6:1, in line with a
#: ~700 t-class Stage 2 tankage.
DEFAULT_STARSHIP_TANK_OX_KG = 540_000.0
DEFAULT_STARSHIP_TANK_CH4_KG = 150_000.0

#: Expected random failures per full Starship burn (1-3 guideline; the upper
#: stage is smaller, so 1 expected is the sensible default).
DEFAULT_STARSHIP_EXPECTED_FAILURES = 1.0


def starship_groups(noise_frac: float = 0.02) -> tuple[EngineGroup, ...]:
    """The two engine banks: 3x Raptor Vacuum + 3x Raptor V2 (SL).

    Same Raptor sim as Super Heavy -- only the presets' parameters differ.
    """
    def with_noise(eng: RaptorEngine) -> RaptorEngine:
        return dataclasses.replace(eng, noise_frac=noise_frac)

    return (
        EngineGroup(base=with_noise(raptor_vacuum()), count=STARSHIP_VACUUM_ENGINES, bank="vac"),
        EngineGroup(base=with_noise(raptor_v2()), count=STARSHIP_SEALEVEL_ENGINES, bank="sl"),
    )


class Starship(Ship):
    """Starship = a Ship that flies the 3x VAC + 3x SL mixed fleet."""

    def __init__(
        self,
        config: ShipConfig | None = None,
    ) -> None:
        if config is None:
            config = ShipConfig(
                vehicle_id="starship",
                n_engines=STARSHIP_VACUUM_ENGINES + STARSHIP_SEALEVEL_ENGINES,
                engine_prefix="ss",
                tank_ox_kg=DEFAULT_STARSHIP_TANK_OX_KG,
                tank_ch4_kg=DEFAULT_STARSHIP_TANK_CH4_KG,
                expected_failures_per_run=DEFAULT_STARSHIP_EXPECTED_FAILURES,
            )
        super().__init__(config=config, groups=starship_groups(config.noise_frac))


def make_starship(
    *,
    tank_ox_kg: float = DEFAULT_STARSHIP_TANK_OX_KG,
    tank_ch4_kg: float = DEFAULT_STARSHIP_TANK_CH4_KG,
    seed: int = 1,
    vari_frac: float = 0.01,
    noise_frac: float = 0.02,
    throttle_frac: float = 1.0,
    expected_failures_per_run: float = DEFAULT_STARSHIP_EXPECTED_FAILURES,
    fail_hazard_per_s: float | None = None,
    ambient_pressure_pa: float = 101_325.0,
    dt: float = 0.01,
) -> Starship:
    """Convenience factory: a Starship vessel with the standard tank / fleet."""
    config = ShipConfig(
        vehicle_id="starship",
        n_engines=STARSHIP_VACUUM_ENGINES + STARSHIP_SEALEVEL_ENGINES,
        engine_prefix="ss",
        seed=seed,
        vari_frac=vari_frac,
        tank_ox_kg=tank_ox_kg,
        tank_ch4_kg=tank_ch4_kg,
        throttle_frac=throttle_frac,
        ambient_pressure_pa=ambient_pressure_pa,
        dt=dt,
        expected_failures_per_run=expected_failures_per_run,
        fail_hazard_per_s=fail_hazard_per_s,
        noise_frac=noise_frac,
    )
    return Starship(config)
