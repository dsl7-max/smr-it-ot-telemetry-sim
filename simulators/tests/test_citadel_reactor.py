from __future__ import annotations

import io
import json
import random
from datetime import datetime, timezone

import pytest

from simulators.citadel_reactor import (
    DEFAULT_TAGS,
    Reading,
    ReactorSimulator,
    TagSpec,
    clamp,
    main,
)

FIXED_TS = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)


def fixed_clock() -> datetime:
    return FIXED_TS


def make_sim(seed: int = 42, tags: tuple[TagSpec, ...] = DEFAULT_TAGS) -> ReactorSimulator:
    return ReactorSimulator(tags=tags, rng=random.Random(seed), clock=fixed_clock)


# --- clamp ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [(5.0, 5.0), (-1.0, 0.0), (11.0, 10.0), (0.0, 0.0), (10.0, 10.0)],
)
def test_clamp(value: float, expected: float) -> None:
    assert clamp(value, 0.0, 10.0) == expected


def test_clamp_rejects_inverted_bounds() -> None:
    with pytest.raises(ValueError):
        clamp(1.0, 10.0, 0.0)


# --- TagSpec validation ----------------------------------------------------------------

@pytest.mark.parametrize(
    "kwargs",
    [
        {"low": 10.0, "high": 10.0},          # low == high
        {"low": 20.0, "high": 10.0},          # inverted
        {"nominal": 99.0},                     # nominal above high
        {"noise_std": -0.1},                   # negative noise
        {"name": ""},                          # empty name
    ],
)
def test_tagspec_rejects_invalid(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {
        "name": "T", "unit": "u", "nominal": 5.0, "noise_std": 1.0, "low": 0.0, "high": 10.0,
    }
    base.update(kwargs)
    with pytest.raises(ValueError):
        TagSpec(**base)  # type: ignore[arg-type]


def test_default_tags_cover_mvp_sensors() -> None:
    units = {t.name: t.unit for t in DEFAULT_TAGS}
    assert units == {
        "CORE_TEMP": "°C",
        "HE_LOOP_PRESS": "bar",
        "RAD_MON_01": "µSv/h",
        "RAD_MON_02": "µSv/h",
        "RAD_MON_03": "µSv/h",
    }


# --- ReactorSimulator ------------------------------------------------------------------

def test_simulator_rejects_empty_and_duplicate_tags() -> None:
    with pytest.raises(ValueError):
        ReactorSimulator(tags=())
    with pytest.raises(ValueError):
        ReactorSimulator(tags=(DEFAULT_TAGS[0], DEFAULT_TAGS[0]))


def test_read_shape() -> None:
    readings = make_sim().read()
    assert [r.tag for r in readings] == [t.name for t in DEFAULT_TAGS]
    for reading, spec in zip(readings, DEFAULT_TAGS):
        assert isinstance(reading, Reading)
        assert isinstance(reading.value, float)
        assert reading.unit == spec.unit
        assert reading.timestamp == FIXED_TS
        assert reading.timestamp.tzinfo is not None


def test_values_stay_within_bounds_default_tags() -> None:
    sim = make_sim()
    specs = {t.name: t for t in DEFAULT_TAGS}
    for _ in range(1000):
        for r in sim.read():
            spec = specs[r.tag]
            assert spec.low <= r.value <= spec.high


def test_values_are_clamped_under_extreme_noise() -> None:
    noisy = (TagSpec("NOISY", "u", nominal=5.0, noise_std=100.0, low=0.0, high=10.0),)
    sim = make_sim(tags=noisy)
    values = [sim.read()[0].value for _ in range(500)]
    assert all(0.0 <= v <= 10.0 for v in values)
    assert 0.0 in values and 10.0 in values  # clamping actually engaged at both rails


def test_values_vary_around_nominal() -> None:
    sim = make_sim()
    temps = [sim.read()[0].value for _ in range(500)]
    assert len(set(temps)) > 1
    assert abs(sum(temps) / len(temps) - 750.0) < 1.0


def test_same_seed_is_deterministic() -> None:
    a, b = make_sim(seed=7), make_sim(seed=7)
    assert [a.read() for _ in range(10)] == [b.read() for _ in range(10)]


def test_different_seeds_differ() -> None:
    assert make_sim(seed=1).read() != make_sim(seed=2).read()


def test_reading_to_dict_is_json_serializable() -> None:
    d = make_sim().read()[0].to_dict()
    assert set(d) == {"timestamp", "tag", "value", "unit"}
    assert d["timestamp"] == "2026-11-23T12:00:00+00:00"
    assert json.loads(json.dumps(d, ensure_ascii=False)) == d


# --- CLI -------------------------------------------------------------------------------

def run_cli(args: list[str]) -> tuple[list[dict[str, object]], list[float]]:
    out = io.StringIO()
    sleeps: list[float] = []
    assert main(args, out=out, sleep=sleeps.append) == 0
    return [json.loads(line) for line in out.getvalue().splitlines()], sleeps


def test_cli_emits_one_line_per_reading_per_tick() -> None:
    lines, sleeps = run_cli(["--seed", "1", "--count", "3", "--interval", "0.5"])
    assert len(lines) == 3 * len(DEFAULT_TAGS)
    assert sleeps == [0.5, 0.5]  # no sleep after the final tick
    assert lines[0]["tag"] == "CORE_TEMP" and lines[0]["unit"] == "°C"


def test_cli_seed_is_deterministic() -> None:
    a, _ = run_cli(["--seed", "99", "--count", "2", "--interval", "0"])
    b, _ = run_cli(["--seed", "99", "--count", "2", "--interval", "0"])
    assert [x["value"] for x in a] == [x["value"] for x in b]


def test_cli_reads_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_SEED", "99")
    monkeypatch.setenv("SIM_TICK_SECONDS", "2.5")
    env_lines, sleeps = run_cli(["--count", "2"])
    flag_lines, _ = run_cli(["--seed", "99", "--count", "2"])
    assert sleeps == [2.5]
    assert [x["value"] for x in env_lines] == [x["value"] for x in flag_lines]


def test_cli_writes_utf8_on_real_streams() -> None:
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252")  # simulate a Windows console stream
    assert main(["--seed", "1", "--count", "1"], out=out, sleep=lambda _: None) == 0
    out.flush()
    text = raw.getvalue().decode("utf-8")
    assert '"unit": "°C"' in text and '"unit": "µSv/h"' in text


@pytest.mark.parametrize("args", [["--count", "-1"], ["--interval", "-1"]])
def test_cli_rejects_negative_args(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(args, out=io.StringIO(), sleep=lambda _: None)
    assert exc.value.code == 2
