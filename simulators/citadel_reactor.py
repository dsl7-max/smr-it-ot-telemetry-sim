"""Citadel reactor plant model — Purdue L0 physics, runs on PC #2 (Citadel Plant Floor).

A stateful, tick-based model of a helium-cooled reactor with two physical radiation zones.
The *true* plant state is noise-free and fully deterministic; sensor noise is applied only
when readings are taken, so physics can be tested exactly.

Coupling (simplified, documented for reviewers — not a licensing-grade model):
  * Power and helium flow ramp toward operator setpoints at fixed rates.
  * Core outlet temp follows a first-order lag toward a heat-balance target:
        T_target = T_inlet + dT_full * (power / 100) * (flow_nominal / flow)
    i.e. less flow -> hotter core (Q = m_dot * cp * dT).
  * Helium loop pressure follows the closed-loop ideal gas law on mean loop temperature:
        P = P_ref * T_mean[K] / T_mean_ref[K]   (no inventory/pressure control modelled)
  * VAULT_RAD_USVH (inside vault, outside the concrete biological shield) scales with power
    from background up to ~1.5 uSv/h; a fault ramps it to a seeded random 25-80 uSv/h, and
    it decays back exponentially after the fault clears.
  * CONTROL_ROOM_RAD_USVH (outside the outer Citadel wall) stays at background (~0.20 uSv/h)
    even during a vault fault: the shielding effectiveness demonstration (GDC 19).

CLI:
    python -m simulators.citadel_reactor --seed 42 --count 30 --interval 1 --fault-at-tick 10 --fault-duration 10
Emits one JSON object per tag per tick. ``--count 0`` runs until interrupted.
Env defaults (used by docker-compose.plant.yml): SIM_SEED, SIM_TICK_SECONDS.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TextIO

Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]

VALUE_DECIMALS = 3
KELVIN_OFFSET = 273.15

# Tag names: UPPER_SNAKE with a unit suffix (PM decision, Sprint 2).
REACTOR_POWER_PCT = "REACTOR_POWER_PCT"
CORE_TEMP_C = "CORE_TEMP_C"
HE_LOOP_PRESS_BAR = "HE_LOOP_PRESS_BAR"
HE_FLOW_KGS = "HE_FLOW_KGS"
VAULT_RAD_USVH = "VAULT_RAD_USVH"
CONTROL_ROOM_RAD_USVH = "CONTROL_ROOM_RAD_USVH"
ALL_TAG_NAMES: tuple[str, ...] = (
    REACTOR_POWER_PCT, CORE_TEMP_C, HE_LOOP_PRESS_BAR, HE_FLOW_KGS, VAULT_RAD_USVH, CONTROL_ROOM_RAD_USVH,
)


# ------------------------------------------------------------------------------------------
# Value types
# ------------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TagSpec:
    """A sensor channel: instrument span (readings saturate at the span ends) and noise."""

    name: str
    unit: str
    noise_std: float
    span_low: float
    span_high: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Tag name must be non-empty")
        if self.span_low >= self.span_high:
            raise ValueError(f"{self.name}: span_low ({self.span_low}) must be < span_high ({self.span_high})")
        if self.noise_std < 0:
            raise ValueError(f"{self.name}: noise_std must be >= 0")


@dataclass(frozen=True, slots=True)
class Reading:
    """One timestamped sensor sample."""

    timestamp: datetime
    tag: str
    value: float
    unit: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "tag": self.tag,
            "value": self.value,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class PlantParams:
    """Physics constants. Defaults give the nominal full-power operating point."""

    inlet_temp_c: float = 250.0
    full_power_outlet_temp_c: float = 750.0
    temp_time_constant_s: float = 30.0
    nominal_flow_kgs: float = 90.0
    min_flow_kgs: float = 9.0             # floor keeps the heat balance finite
    max_flow_kgs: float = 110.0
    flow_ramp_kgs_per_s: float = 5.0
    power_ramp_pct_per_s: float = 1.0
    ref_pressure_bar: float = 70.0        # at full-power mean loop temperature
    background_rad_usvh: float = 0.20
    vault_full_power_rad_usvh: float = 1.5
    fault_min_usvh: float = 25.0
    fault_max_usvh: float = 80.0
    fault_ramp_s: float = 5.0
    fault_decay_time_constant_s: float = 10.0

    def __post_init__(self) -> None:
        if self.full_power_outlet_temp_c <= self.inlet_temp_c:
            raise ValueError("full_power_outlet_temp_c must exceed inlet_temp_c")
        if not 0 < self.min_flow_kgs <= self.nominal_flow_kgs <= self.max_flow_kgs:
            raise ValueError("Require 0 < min_flow <= nominal_flow <= max_flow")
        if not self.background_rad_usvh <= self.vault_full_power_rad_usvh < self.fault_min_usvh <= self.fault_max_usvh:
            raise ValueError("Require background <= vault_full_power < fault_min <= fault_max")
        for name in ("temp_time_constant_s", "flow_ramp_kgs_per_s", "power_ramp_pct_per_s",
                     "ref_pressure_bar", "fault_ramp_s", "fault_decay_time_constant_s"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")

    @property
    def ref_mean_temp_k(self) -> float:
        return (self.inlet_temp_c + self.full_power_outlet_temp_c) / 2 + KELVIN_OFFSET


@dataclass(frozen=True, slots=True)
class PlantState:
    """Noise-free true plant values."""

    power_pct: float
    core_temp_c: float
    he_pressure_bar: float
    he_flow_kgs: float
    vault_rad_usvh: float
    control_room_rad_usvh: float
    fault_active: bool

    def as_tag_values(self) -> dict[str, float]:
        return {
            REACTOR_POWER_PCT: self.power_pct,
            CORE_TEMP_C: self.core_temp_c,
            HE_LOOP_PRESS_BAR: self.he_pressure_bar,
            HE_FLOW_KGS: self.he_flow_kgs,
            VAULT_RAD_USVH: self.vault_rad_usvh,
            CONTROL_ROOM_RAD_USVH: self.control_room_rad_usvh,
        }


DEFAULT_TAGS: tuple[TagSpec, ...] = (
    TagSpec(REACTOR_POWER_PCT, "%", noise_std=0.1, span_low=0.0, span_high=120.0),
    TagSpec(CORE_TEMP_C, "°C", noise_std=1.0, span_low=0.0, span_high=1500.0),
    TagSpec(HE_LOOP_PRESS_BAR, "bar", noise_std=0.1, span_low=0.0, span_high=100.0),
    TagSpec(HE_FLOW_KGS, "kg/s", noise_std=0.3, span_low=0.0, span_high=120.0),
    TagSpec(VAULT_RAD_USVH, "µSv/h", noise_std=0.03, span_low=0.0, span_high=100.0),
    TagSpec(CONTROL_ROOM_RAD_USVH, "µSv/h", noise_std=0.01, span_low=0.0, span_high=10.0),
)


# ------------------------------------------------------------------------------------------
# Pure physics helpers
# ------------------------------------------------------------------------------------------

def clamp(value: float, low: float, high: float) -> float:
    """Limit ``value`` to the closed interval [low, high]."""
    if low > high:
        raise ValueError(f"low ({low}) must be <= high ({high})")
    return max(low, min(high, value))


def ramp_toward(current: float, target: float, max_delta: float) -> float:
    """Move ``current`` toward ``target`` by at most ``max_delta`` (rate limiter)."""
    if abs(target - current) <= max_delta:
        return target
    return current + math.copysign(max_delta, target - current)


def first_order_lag(current: float, target: float, dt: float, tau: float) -> float:
    """Exact discretization of dx/dt = (target - x) / tau; stable for any dt."""
    return target + (current - target) * math.exp(-dt / tau)


def target_core_temp_c(params: PlantParams, power_pct: float, flow_kgs: float) -> float:
    """Heat-balance outlet temperature: dT scales with power / flow."""
    full_dt = params.full_power_outlet_temp_c - params.inlet_temp_c
    flow = max(flow_kgs, params.min_flow_kgs)
    return params.inlet_temp_c + full_dt * (power_pct / 100.0) * (params.nominal_flow_kgs / flow)


def loop_pressure_bar(params: PlantParams, core_temp_c: float) -> float:
    """Closed-loop ideal gas on mean loop temperature (inlet/outlet average)."""
    mean_k = (params.inlet_temp_c + core_temp_c) / 2 + KELVIN_OFFSET
    return params.ref_pressure_bar * mean_k / params.ref_mean_temp_k


def vault_normal_rad_usvh(params: PlantParams, power_pct: float) -> float:
    """Vault dose rate without a fault: background at 0 %, full-power value at 100 %."""
    span = params.vault_full_power_rad_usvh - params.background_rad_usvh
    return params.background_rad_usvh + span * (power_pct / 100.0)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------------
# Plant model
# ------------------------------------------------------------------------------------------

class CitadelReactor:
    """Stateful plant model. Starts at steady state, full power, nominal flow, no fault."""

    def __init__(
        self,
        params: PlantParams | None = None,
        tags: Sequence[TagSpec] = DEFAULT_TAGS,
        rng: random.Random | None = None,
        clock: Clock = utc_now,
    ) -> None:
        if not tags:
            raise ValueError("At least one tag is required")
        names = [t.name for t in tags]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate tag names: {names}")
        unknown = set(names) - set(ALL_TAG_NAMES)
        if unknown:
            raise ValueError(f"Tags not produced by the plant model: {sorted(unknown)}")

        self.params = params if params is not None else PlantParams()
        self._tags: tuple[TagSpec, ...] = tuple(tags)
        self._rng = rng if rng is not None else random.Random()
        self._clock = clock

        p = self.params
        self._power_sp = 100.0
        self._flow_sp = p.nominal_flow_kgs
        self._power = 100.0
        self._flow = p.nominal_flow_kgs
        self._core_temp = target_core_temp_c(p, self._power, self._flow)
        self._fault_active = False
        self._fault_level = 0.0      # total vault dose rate the fault drives toward
        self._fault_excess = 0.0     # current dose rate above the normal vault value

    # --- operator inputs -----------------------------------------------------------------

    @property
    def tags(self) -> tuple[TagSpec, ...]:
        return self._tags

    @property
    def power_setpoint_pct(self) -> float:
        return self._power_sp

    @property
    def flow_setpoint_kgs(self) -> float:
        return self._flow_sp

    def set_power_setpoint(self, pct: float) -> None:
        if not 0.0 <= pct <= 100.0:
            raise ValueError(f"Power setpoint {pct} outside 0-100 %")
        self._power_sp = float(pct)

    def set_flow_setpoint(self, kgs: float) -> None:
        p = self.params
        if not p.min_flow_kgs <= kgs <= p.max_flow_kgs:
            raise ValueError(f"Flow setpoint {kgs} outside {p.min_flow_kgs}-{p.max_flow_kgs} kg/s")
        self._flow_sp = float(kgs)

    # --- fault trigger -------------------------------------------------------------------

    @property
    def fault_active(self) -> bool:
        return self._fault_active

    def trigger_fault(self) -> float:
        """Start a vault radiation fault; returns the drawn fault level (uSv/h). Idempotent."""
        if not self._fault_active:
            self._fault_active = True
            self._fault_level = self._rng.uniform(self.params.fault_min_usvh, self.params.fault_max_usvh)
        return self._fault_level

    def clear_fault(self) -> None:
        """End the fault; the excess dose rate then decays exponentially."""
        self._fault_active = False

    # --- dynamics ------------------------------------------------------------------------

    def step(self, dt: float) -> None:
        """Advance the plant by ``dt`` simulated seconds."""
        if dt <= 0:
            raise ValueError("dt must be > 0")
        p = self.params
        self._power = ramp_toward(self._power, self._power_sp, p.power_ramp_pct_per_s * dt)
        self._flow = ramp_toward(self._flow, self._flow_sp, p.flow_ramp_kgs_per_s * dt)
        target = target_core_temp_c(p, self._power, self._flow)
        self._core_temp = first_order_lag(self._core_temp, target, dt, p.temp_time_constant_s)

        normal = vault_normal_rad_usvh(p, self._power)
        if self._fault_active:
            target_excess = max(self._fault_level - normal, 0.0)
            self._fault_excess = ramp_toward(self._fault_excess, target_excess, target_excess / p.fault_ramp_s * dt)
        else:
            self._fault_excess *= math.exp(-dt / p.fault_decay_time_constant_s)

    @property
    def state(self) -> PlantState:
        p = self.params
        return PlantState(
            power_pct=self._power,
            core_temp_c=self._core_temp,
            he_pressure_bar=loop_pressure_bar(p, self._core_temp),
            he_flow_kgs=self._flow,
            vault_rad_usvh=vault_normal_rad_usvh(p, self._power) + self._fault_excess,
            control_room_rad_usvh=p.background_rad_usvh,
            fault_active=self._fault_active,
        )

    def read(self) -> list[Reading]:
        """Sample every sensor once (true value + noise, saturated to span); shared timestamp."""
        ts = self._clock()
        values = self.state.as_tag_values()
        return [
            Reading(
                timestamp=ts,
                tag=spec.name,
                value=round(
                    clamp(values[spec.name] + self._rng.gauss(0.0, spec.noise_std), spec.span_low, spec.span_high),
                    VALUE_DECIMALS,
                ),
                unit=spec.unit,
            )
            for spec in self._tags
        ]


# ------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------

def _non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _non_negative_float(raw: str) -> float:
    value = float(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _positive_float(raw: str) -> float:
    value = float(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    env_seed = os.environ.get("SIM_SEED", "").strip()
    env_interval = os.environ.get("SIM_TICK_SECONDS", "").strip()
    parser = argparse.ArgumentParser(description="Citadel reactor plant simulator (JSON lines).")
    parser.add_argument("--seed", type=int, default=int(env_seed) if env_seed else None,
                        help="RNG seed for deterministic output (env: SIM_SEED)")
    parser.add_argument("--count", type=_non_negative_int, default=0,
                        help="Number of ticks to emit; 0 = run forever (default: 0)")
    parser.add_argument("--interval", type=_non_negative_float,
                        default=float(env_interval) if env_interval else 1.0,
                        help="Wall-clock seconds between ticks (env: SIM_TICK_SECONDS, default: 1.0)")
    parser.add_argument("--dt", type=_positive_float, default=1.0,
                        help="Simulated seconds advanced per tick (default: 1.0)")
    parser.add_argument("--fault-at-tick", type=_non_negative_int, default=None,
                        help="Trigger the vault radiation fault at this tick")
    parser.add_argument("--fault-duration", type=_non_negative_int, default=None,
                        help="Clear the fault this many ticks after it starts (requires --fault-at-tick)")
    return parser


def main(
    argv: Sequence[str] | None = None,
    out: TextIO = sys.stdout,
    sleep: Sleeper = time.sleep,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.fault_duration is not None and args.fault_at_tick is None:
        parser.error("--fault-duration requires --fault-at-tick")

    # Units contain non-ASCII (°, µ); Windows consoles default to cp1252, so force UTF-8 on real streams.
    reconfigure = getattr(out, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")

    plant = CitadelReactor(rng=random.Random(args.seed))
    clear_at = (args.fault_at_tick + args.fault_duration
                if args.fault_at_tick is not None and args.fault_duration is not None else None)
    tick = 0
    try:
        while args.count == 0 or tick < args.count:
            if tick == args.fault_at_tick:
                plant.trigger_fault()
            if tick == clear_at:
                plant.clear_fault()
            plant.step(args.dt)
            for reading in plant.read():
                out.write(json.dumps(reading.to_dict(), ensure_ascii=False) + "\n")
            out.flush()
            tick += 1
            if args.count == 0 or tick < args.count:
                sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
