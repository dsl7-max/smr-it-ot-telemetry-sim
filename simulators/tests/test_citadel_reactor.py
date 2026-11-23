from __future__ import annotations

import io
import json
import math
import random
from datetime import datetime, timezone

import pytest

from simulators.citadel_reactor import (
    ALL_TAG_NAMES,
    CONTROL_ROOM_RAD_USVH,
    CORE_TEMP_C,
    DEFAULT_TAGS,
    HE_FLOW_KGS,
    HE_LOOP_PRESS_BAR,
    KELVIN_OFFSET,
    REACTOR_POWER_PCT,
    VAULT_RAD_USVH,
    CitadelReactor,
    PlantParams,
    Reading,
    TagSpec,
    clamp,
    first_order_lag,
    loop_pressure_bar,
    main,
    ramp_toward,
    target_core_temp_c,
    vault_normal_rad_usvh,
)

FIXED_TS = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)
P = PlantParams()


def fixed_clock() -> datetime:
    return FIXED_TS


def make_plant(seed: int = 42, params: PlantParams | None = None) -> CitadelReactor:
    return CitadelReactor(params=params, rng=random.Random(seed), clock=fixed_clock)


def run(plant: CitadelReactor, seconds: float, dt: float = 1.0) -> None:
    for _ in range(round(seconds / dt)):
        plant.step(dt)


# --- pure helpers ------------------------------------------------------------------------

@pytest.mark.parametrize(("value", "expected"), [(5.0, 5.0), (-1.0, 0.0), (11.0, 10.0), (0.0, 0.0), (10.0, 10.0)])
def test_clamp(value: float, expected: float) -> None:
    assert clamp(value, 0.0, 10.0) == expected


def test_clamp_rejects_inverted_bounds() -> None:
    with pytest.raises(ValueError):
        clamp(1.0, 10.0, 0.0)


@pytest.mark.parametrize(
    ("current", "target", "max_delta", "expected"),
    [(0.0, 10.0, 3.0, 3.0), (10.0, 0.0, 3.0, 7.0), (9.0, 10.0, 3.0, 10.0), (5.0, 5.0, 1.0, 5.0)],
)
def test_ramp_toward(current: float, target: float, max_delta: float, expected: float) -> None:
    assert ramp_toward(current, target, max_delta) == expected


def test_first_order_lag_reaches_63_percent_after_one_tau() -> None:
    assert first_order_lag(0.0, 100.0, dt=30.0, tau=30.0) == pytest.approx(100.0 * (1 - math.exp(-1)))


def test_target_temp_heat_balance() -> None:
    assert target_core_temp_c(P, 100.0, P.nominal_flow_kgs) == pytest.approx(750.0)
    assert target_core_temp_c(P, 50.0, P.nominal_flow_kgs) == pytest.approx(500.0)
    assert target_core_temp_c(P, 0.0, P.nominal_flow_kgs) == pytest.approx(250.0)
    assert target_core_temp_c(P, 100.0, P.nominal_flow_kgs / 2) == pytest.approx(1250.0)  # half flow, double dT


def test_target_temp_uses_flow_floor() -> None:
    assert target_core_temp_c(P, 100.0, 0.0) == target_core_temp_c(P, 100.0, P.min_flow_kgs)


def test_loop_pressure_ideal_gas() -> None:
    assert loop_pressure_bar(P, 750.0) == pytest.approx(70.0)
    mean_k = (250.0 + 500.0) / 2 + KELVIN_OFFSET
    assert loop_pressure_bar(P, 500.0) == pytest.approx(70.0 * mean_k / P.ref_mean_temp_k)


def test_vault_normal_rad_scales_with_power() -> None:
    assert vault_normal_rad_usvh(P, 100.0) == pytest.approx(1.5)
    assert vault_normal_rad_usvh(P, 0.0) == pytest.approx(0.20)
    assert vault_normal_rad_usvh(P, 50.0) == pytest.approx(0.85)


# --- parameter / tag validation ----------------------------------------------------------

@pytest.mark.parametrize(
    "kwargs",
    [
        {"full_power_outlet_temp_c": 200.0},          # outlet below inlet
        {"min_flow_kgs": 0.0},
        {"min_flow_kgs": 100.0},                       # above nominal
        {"vault_full_power_rad_usvh": 30.0},           # overlaps fault range
        {"fault_min_usvh": 90.0},                      # min > max
        {"temp_time_constant_s": 0.0},
        {"fault_ramp_s": -1.0},
    ],
)
def test_plant_params_reject_invalid(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        PlantParams(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [{"span_low": 10.0, "span_high": 10.0}, {"span_low": 20.0, "span_high": 10.0}, {"noise_std": -0.1}, {"name": ""}],
)
def test_tagspec_rejects_invalid(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {"name": "T", "unit": "u", "noise_std": 1.0, "span_low": 0.0, "span_high": 10.0}
    base.update(kwargs)
    with pytest.raises(ValueError):
        TagSpec(**base)  # type: ignore[arg-type]


def test_plant_rejects_bad_tag_sets() -> None:
    with pytest.raises(ValueError):
        CitadelReactor(tags=())
    with pytest.raises(ValueError):
        CitadelReactor(tags=(DEFAULT_TAGS[0], DEFAULT_TAGS[0]))
    with pytest.raises(ValueError):
        CitadelReactor(tags=(TagSpec("NOT_A_PLANT_TAG", "u", 0.0, 0.0, 1.0),))


def test_default_tags_match_pm_naming_and_units() -> None:
    assert {t.name: t.unit for t in DEFAULT_TAGS} == {
        REACTOR_POWER_PCT: "%",
        CORE_TEMP_C: "°C",
        HE_LOOP_PRESS_BAR: "bar",
        HE_FLOW_KGS: "kg/s",
        VAULT_RAD_USVH: "µSv/h",
        CONTROL_ROOM_RAD_USVH: "µSv/h",
    }
    assert tuple(t.name for t in DEFAULT_TAGS) == ALL_TAG_NAMES


@pytest.mark.parametrize("pct", [-1.0, 100.1])
def test_power_setpoint_validation(pct: float) -> None:
    with pytest.raises(ValueError):
        make_plant().set_power_setpoint(pct)


@pytest.mark.parametrize("kgs", [P.min_flow_kgs - 0.1, P.max_flow_kgs + 0.1])
def test_flow_setpoint_validation(kgs: float) -> None:
    with pytest.raises(ValueError):
        make_plant().set_flow_setpoint(kgs)


def test_step_rejects_non_positive_dt() -> None:
    with pytest.raises(ValueError):
        make_plant().step(0.0)


# --- steady state & coupled dynamics -----------------------------------------------------

def test_initial_state_is_nominal_full_power() -> None:
    s = make_plant().state
    assert s.power_pct == 100.0
    assert s.core_temp_c == pytest.approx(750.0)
    assert s.he_pressure_bar == pytest.approx(70.0)
    assert s.he_flow_kgs == pytest.approx(90.0)
    assert s.vault_rad_usvh == pytest.approx(1.5)
    assert s.control_room_rad_usvh == pytest.approx(0.20)
    assert s.fault_active is False


def test_steady_state_holds_without_inputs() -> None:
    plant = make_plant()
    before = plant.state
    run(plant, 600)
    after = plant.state
    assert after.core_temp_c == pytest.approx(before.core_temp_c)
    assert after.he_pressure_bar == pytest.approx(before.he_pressure_bar)
    assert after.vault_rad_usvh == pytest.approx(before.vault_rad_usvh)


def test_power_ramps_at_rate_limit() -> None:
    plant = make_plant()
    plant.set_power_setpoint(50.0)
    run(plant, 10)
    assert plant.state.power_pct == pytest.approx(90.0)  # 1 %/s
    run(plant, 60)
    assert plant.state.power_pct == pytest.approx(50.0)  # holds at setpoint


def test_core_temp_lags_power_with_first_order_response() -> None:
    fast = PlantParams(power_ramp_pct_per_s=1000.0)  # effectively a power step
    plant = make_plant(params=fast)
    plant.set_power_setpoint(50.0)
    plant.step(1e-6)                                  # apply the step
    run(plant, fast.temp_time_constant_s, dt=0.5)     # one time constant
    expected = 500.0 + (750.0 - 500.0) * math.exp(-1)
    assert plant.state.core_temp_c == pytest.approx(expected, rel=1e-3)


def test_power_reduction_cools_core_and_drops_pressure_monotonically() -> None:
    plant = make_plant()
    plant.set_power_setpoint(50.0)
    temps, pressures = [], []
    for _ in range(400):
        plant.step(1.0)
        temps.append(plant.state.core_temp_c)
        pressures.append(plant.state.he_pressure_bar)
    assert all(b <= a + 1e-9 for a, b in zip(temps, temps[1:]))
    assert all(b <= a + 1e-9 for a, b in zip(pressures, pressures[1:]))
    assert temps[-1] == pytest.approx(500.0, abs=0.5)
    assert pressures[-1] < 70.0


def test_pressure_always_tracks_temperature_by_ideal_gas() -> None:
    plant = make_plant()
    plant.set_power_setpoint(30.0)
    for _ in range(50):
        plant.step(2.0)
        s = plant.state
        assert s.he_pressure_bar == pytest.approx(loop_pressure_bar(P, s.core_temp_c))


def test_reduced_flow_heats_core() -> None:
    plant = make_plant()
    plant.set_flow_setpoint(45.0)  # half flow
    run(plant, 600)
    assert plant.state.he_flow_kgs == pytest.approx(45.0)
    assert plant.state.core_temp_c == pytest.approx(1250.0, abs=1.0)
    assert plant.state.he_pressure_bar > 70.0


def test_vault_rad_follows_power_without_fault() -> None:
    plant = make_plant()
    plant.set_power_setpoint(50.0)
    run(plant, 120)
    assert plant.state.vault_rad_usvh == pytest.approx(0.85)


# --- radiation zones & fault trigger -----------------------------------------------------

def test_fault_ramps_rather_than_jumps() -> None:
    plant = make_plant()
    level = plant.trigger_fault()
    plant.step(1.0)                                   # 1 s into a 5 s ramp
    v = plant.state.vault_rad_usvh
    assert 1.5 < v < level
    assert v == pytest.approx(1.5 + (level - 1.5) / 5.0)


def test_fault_reaches_drawn_level_within_25_to_80() -> None:
    plant = make_plant()
    level = plant.trigger_fault()
    run(plant, P.fault_ramp_s + 5)
    assert 25.0 <= level <= 80.0
    assert plant.state.vault_rad_usvh == pytest.approx(level)
    assert plant.state.fault_active is True


def test_fault_magnitude_bounded_and_varies_across_seeds() -> None:
    levels = [make_plant(seed=s).trigger_fault() for s in range(200)]
    assert all(25.0 <= lv <= 80.0 for lv in levels)
    assert max(levels) - min(levels) > 30.0           # spread across the range


def test_trigger_fault_is_idempotent_while_active() -> None:
    plant = make_plant()
    first = plant.trigger_fault()
    run(plant, 2)
    assert plant.trigger_fault() == first


def test_fault_clears_and_decays_back_to_normal() -> None:
    plant = make_plant()
    plant.trigger_fault()
    run(plant, 10)
    plant.clear_fault()
    assert plant.state.fault_active is False
    plant.step(1.0)
    assert plant.state.vault_rad_usvh > 20.0          # decays, does not snap back
    run(plant, 10 * P.fault_decay_time_constant_s)
    assert plant.state.vault_rad_usvh == pytest.approx(1.5, abs=0.01)


def test_control_room_true_value_unaffected_by_vault_fault() -> None:
    plant = make_plant()
    plant.trigger_fault()
    for _ in range(60):
        plant.step(1.0)
        assert plant.state.control_room_rad_usvh == pytest.approx(0.20)


def test_control_room_readings_stay_below_0_3_during_fault() -> None:
    plant = make_plant()
    plant.trigger_fault()
    for _ in range(500):
        plant.step(1.0)
        cr = next(r for r in plant.read() if r.tag == CONTROL_ROOM_RAD_USVH)
        assert cr.value < 0.3


# --- sensor readings ---------------------------------------------------------------------

def test_read_shape() -> None:
    readings = make_plant().read()
    assert [r.tag for r in readings] == list(ALL_TAG_NAMES)
    for reading, spec in zip(readings, DEFAULT_TAGS):
        assert isinstance(reading, Reading)
        assert isinstance(reading.value, float)
        assert reading.unit == spec.unit
        assert reading.timestamp == FIXED_TS


def test_readings_stay_within_instrument_span() -> None:
    plant = make_plant()
    specs = {t.name: t for t in DEFAULT_TAGS}
    plant.trigger_fault()
    plant.set_flow_setpoint(P.min_flow_kgs)          # drive temp past the span top
    for _ in range(300):
        plant.step(2.0)
        for r in plant.read():
            assert specs[r.tag].span_low <= r.value <= specs[r.tag].span_high
    temp = next(r for r in plant.read() if r.tag == CORE_TEMP_C)
    assert temp.value == 1500.0                       # saturated at span, like a real transmitter


def test_readings_are_noisy_around_true_value() -> None:
    plant = make_plant()
    temps = [next(r for r in plant.read() if r.tag == CORE_TEMP_C).value for _ in range(500)]
    assert len(set(temps)) > 1
    assert sum(temps) / len(temps) == pytest.approx(750.0, abs=0.3)


def test_same_seed_is_deterministic() -> None:
    def trace(seed: int) -> list[list[Reading]]:
        plant = make_plant(seed)
        out = []
        for tick in range(30):
            if tick == 5:
                plant.trigger_fault()
            plant.step(1.0)
            out.append(plant.read())
        return out

    assert trace(7) == trace(7)
    assert trace(7) != trace(8)


def test_reading_to_dict_is_json_serializable() -> None:
    d = make_plant().read()[0].to_dict()
    assert set(d) == {"timestamp", "tag", "value", "unit"}
    assert d["timestamp"] == "2026-11-23T12:00:00+00:00"
    assert json.loads(json.dumps(d, ensure_ascii=False)) == d


# --- CLI ---------------------------------------------------------------------------------

def run_cli(args: list[str]) -> tuple[list[dict[str, object]], list[float]]:
    out = io.StringIO()
    sleeps: list[float] = []
    assert main(args, out=out, sleep=sleeps.append) == 0
    return [json.loads(line) for line in out.getvalue().splitlines()], sleeps


def vault_series(lines: list[dict[str, object]]) -> list[float]:
    return [float(x["value"]) for x in lines if x["tag"] == VAULT_RAD_USVH]  # type: ignore[arg-type]


def test_cli_emits_one_line_per_tag_per_tick() -> None:
    lines, sleeps = run_cli(["--seed", "1", "--count", "3", "--interval", "0.5"])
    assert len(lines) == 3 * len(DEFAULT_TAGS)
    assert sleeps == [0.5, 0.5]                       # no sleep after the final tick
    assert [x["tag"] for x in lines[:6]] == list(ALL_TAG_NAMES)


def test_cli_fault_at_tick_spikes_vault_only() -> None:
    lines, _ = run_cli(["--seed", "3", "--count", "15", "--interval", "0", "--fault-at-tick", "5"])
    vault = vault_series(lines)
    cr = [float(x["value"]) for x in lines if x["tag"] == CONTROL_ROOM_RAD_USVH]  # type: ignore[arg-type]
    assert max(vault[:5]) < 2.0
    assert vault[-1] >= 25.0
    assert max(cr) < 0.3


def test_cli_fault_duration_clears_fault() -> None:
    lines, _ = run_cli(["--seed", "3", "--count", "120", "--interval", "0",
                        "--fault-at-tick", "5", "--fault-duration", "10"])
    vault = vault_series(lines)
    assert max(vault) >= 25.0
    assert vault[-1] < 2.0                            # decayed back after clearing


def test_cli_dt_advances_simulated_time() -> None:
    lines, _ = run_cli(["--seed", "1", "--count", "1", "--interval", "0", "--dt", "5", "--fault-at-tick", "0"])
    vault = vault_series(lines)
    assert vault[0] >= 25.0                           # a 5 s step completes the 5 s fault ramp


def test_cli_seed_is_deterministic() -> None:
    a, _ = run_cli(["--seed", "99", "--count", "5", "--interval", "0", "--fault-at-tick", "2"])
    b, _ = run_cli(["--seed", "99", "--count", "5", "--interval", "0", "--fault-at-tick", "2"])
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
    out = io.TextIOWrapper(raw, encoding="cp1252")    # simulate a Windows console stream
    assert main(["--seed", "1", "--count", "1"], out=out, sleep=lambda _: None) == 0
    out.flush()
    text = raw.getvalue().decode("utf-8")
    assert '"unit": "°C"' in text and '"unit": "µSv/h"' in text


@pytest.mark.parametrize(
    "args",
    [["--count", "-1"], ["--interval", "-1"], ["--dt", "0"], ["--fault-duration", "5"]],
)
def test_cli_rejects_invalid_args(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(args, out=io.StringIO(), sleep=lambda _: None)
    assert exc.value.code == 2
