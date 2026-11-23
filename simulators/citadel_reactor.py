"""Citadel reactor sensor simulator — Purdue L0–1, runs on PC #2 (Citadel Plant Floor).

Generates noisy readings for core temperature, helium loop pressure, and radiation monitors.
Randomness and time are injected so output is deterministic under test.

CLI:
    python -m simulators.citadel_reactor --seed 42 --count 5 --interval 1
Emits one JSON object per reading per line. ``--count 0`` runs until interrupted.
Env defaults (used by docker-compose.plant.yml): SIM_SEED, SIM_TICK_SECONDS.
"""

from __future__ import annotations

import argparse
import json
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


@dataclass(frozen=True, slots=True)
class TagSpec:
    """Static definition of one simulated sensor tag."""

    name: str
    unit: str
    nominal: float
    noise_std: float
    low: float
    high: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Tag name must be non-empty")
        if self.low >= self.high:
            raise ValueError(f"{self.name}: low ({self.low}) must be < high ({self.high})")
        if not self.low <= self.nominal <= self.high:
            raise ValueError(f"{self.name}: nominal {self.nominal} outside [{self.low}, {self.high}]")
        if self.noise_std < 0:
            raise ValueError(f"{self.name}: noise_std must be >= 0")


@dataclass(frozen=True, slots=True)
class Reading:
    """One timestamped sample of a tag."""

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


DEFAULT_TAGS: tuple[TagSpec, ...] = (
    TagSpec("CORE_TEMP", "°C", nominal=750.0, noise_std=2.0, low=700.0, high=800.0),
    TagSpec("HE_LOOP_PRESS", "bar", nominal=70.0, noise_std=0.3, low=65.0, high=75.0),
    TagSpec("RAD_MON_01", "µSv/h", nominal=0.5, noise_std=0.05, low=0.0, high=2.0),
    TagSpec("RAD_MON_02", "µSv/h", nominal=0.5, noise_std=0.05, low=0.0, high=2.0),
    TagSpec("RAD_MON_03", "µSv/h", nominal=0.5, noise_std=0.05, low=0.0, high=2.0),
)


def clamp(value: float, low: float, high: float) -> float:
    """Limit ``value`` to the closed interval [low, high]."""
    if low > high:
        raise ValueError(f"low ({low}) must be <= high ({high})")
    return max(low, min(high, value))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReactorSimulator:
    """Produces one reading per tag on each call to :meth:`read`."""

    def __init__(
        self,
        tags: Sequence[TagSpec] = DEFAULT_TAGS,
        rng: random.Random | None = None,
        clock: Clock = utc_now,
    ) -> None:
        if not tags:
            raise ValueError("At least one tag is required")
        names = [t.name for t in tags]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate tag names: {names}")
        self._tags: tuple[TagSpec, ...] = tuple(tags)
        self._rng = rng if rng is not None else random.Random()
        self._clock = clock

    @property
    def tags(self) -> tuple[TagSpec, ...]:
        return self._tags

    def read(self) -> list[Reading]:
        """Sample every tag once; all readings share a single timestamp."""
        ts = self._clock()
        return [
            Reading(
                timestamp=ts,
                tag=spec.name,
                value=round(
                    clamp(spec.nominal + self._rng.gauss(0.0, spec.noise_std), spec.low, spec.high),
                    VALUE_DECIMALS,
                ),
                unit=spec.unit,
            )
            for spec in self._tags
        ]


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


def build_parser() -> argparse.ArgumentParser:
    env_seed = os.environ.get("SIM_SEED", "").strip()
    env_interval = os.environ.get("SIM_TICK_SECONDS", "").strip()
    parser = argparse.ArgumentParser(description="Citadel reactor sensor simulator (JSON lines).")
    parser.add_argument(
        "--seed", type=int, default=int(env_seed) if env_seed else None,
        help="RNG seed for deterministic output (env: SIM_SEED)",
    )
    parser.add_argument(
        "--count", type=_non_negative_int, default=0,
        help="Number of ticks to emit; 0 = run forever (default: 0)",
    )
    parser.add_argument(
        "--interval", type=_non_negative_float, default=float(env_interval) if env_interval else 1.0,
        help="Seconds between ticks (env: SIM_TICK_SECONDS, default: 1.0)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    out: TextIO = sys.stdout,
    sleep: Sleeper = time.sleep,
) -> int:
    args = build_parser().parse_args(argv)
    # Units contain non-ASCII (°, µ); Windows consoles default to cp1252, so force UTF-8 on real streams.
    reconfigure = getattr(out, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    sim = ReactorSimulator(rng=random.Random(args.seed))
    tick = 0
    try:
        while args.count == 0 or tick < args.count:
            for reading in sim.read():
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
