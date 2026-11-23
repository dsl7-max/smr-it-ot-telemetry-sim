from __future__ import annotations

import random

import pytest

from simulators.citadel_reactor import (
    CORE_TEMP_C,
    DEFAULT_TAGS,
    VAULT_RAD_USVH,
    CitadelReactor,
)
from simulators.virtual_plc.__main__ import PlcConfig
from simulators.virtual_plc.plc import IllegalAddressError, IllegalValueError, VirtualPlc
from simulators.virtual_plc.tag_map import (
    INPUT_REGISTERS,
    PLC_HEARTBEAT_COUNT,
    decode_int16,
    point_by_tag,
)


def make_plc(seed: int = 7) -> VirtualPlc:
    return VirtualPlc(CitadelReactor(rng=random.Random(seed)))


def decoded(plc: VirtualPlc, tag: str) -> float:
    p = point_by_tag(INPUT_REGISTERS, tag)
    return decode_int16(plc.image.input_registers[p.address], p.scale)


def test_rejects_reactor_missing_mapped_tags() -> None:
    with pytest.raises(ValueError, match="mapped tags"):
        VirtualPlc(CitadelReactor(tags=DEFAULT_TAGS[:2]))


def test_image_registers_match_latched_values() -> None:
    plc = make_plc()
    plc.scan(1.0)
    for p in INPUT_REGISTERS:
        if p.tag != PLC_HEARTBEAT_COUNT:
            assert decoded(plc, p.tag) == pytest.approx(plc.image.values[p.tag], abs=0.5 / p.scale)


def test_heartbeat_counts_scans_and_wraps() -> None:
    plc = make_plc()
    assert decoded(plc, PLC_HEARTBEAT_COUNT) == 0
    for _ in range(3):
        plc.scan(1.0)
    assert decoded(plc, PLC_HEARTBEAT_COUNT) == 3
    assert plc.image.scan_count == 3


def test_heartbeat_wraps_at_int16_max() -> None:
    plc = make_plc()
    plc._image = plc._latch(scan_count=32767)  # noqa: SLF001 - jump ahead instead of 32k scans
    plc.scan(0.1)
    assert decoded(plc, PLC_HEARTBEAT_COUNT) == 0


def test_image_only_changes_on_scan() -> None:
    plc = make_plc()
    before = plc.image
    plc.write_holding_register(0, 500)
    assert plc.image is before


def test_same_seed_same_registers() -> None:
    a, b = make_plc(3), make_plc(3)
    for _ in range(5):
        a.scan(1.0)
        b.scan(1.0)
    assert a.image.input_registers == b.image.input_registers


# --- holding register: power setpoint --------------------------------------------------------

def test_setpoint_write_reaches_reactor_and_reads_back() -> None:
    plc = make_plc()
    assert plc.holding_register(0) == 1000
    plc.write_holding_register(0, 500)
    assert plc.reactor.power_setpoint_pct == 50.0
    assert plc.holding_register(0) == 500


def test_setpoint_drives_plant_over_scans() -> None:
    plc = make_plc()
    plc.write_holding_register(0, 500)
    t0 = decoded(plc, CORE_TEMP_C)
    for _ in range(60):
        plc.scan(1.0)
    assert decoded(plc, CORE_TEMP_C) < t0 - 100


@pytest.mark.parametrize("raw", [1001, 0xFFFF, 0x8000])  # 100.1 %, -0.1 %, -3276.8 %
def test_setpoint_out_of_range_rejected_and_not_applied(raw: int) -> None:
    plc = make_plc()
    with pytest.raises(IllegalValueError):
        plc.write_holding_register(0, raw)
    assert plc.reactor.power_setpoint_pct == 100.0


def test_setpoint_bounds_inclusive() -> None:
    plc = make_plc()
    plc.write_holding_register(0, 0)
    assert plc.reactor.power_setpoint_pct == 0.0
    plc.write_holding_register(0, 1000)
    assert plc.reactor.power_setpoint_pct == 100.0


def test_unmapped_holding_register_rejected() -> None:
    plc = make_plc()
    with pytest.raises(IllegalAddressError):
        plc.holding_register(1)
    with pytest.raises(IllegalAddressError):
        plc.write_holding_register(1, 0)


# --- coil: fault trigger ---------------------------------------------------------------------

def test_fault_coil_triggers_and_clears() -> None:
    plc = make_plc()
    assert plc.coil(0) is False
    plc.write_coil(0, True)
    assert plc.coil(0) is True
    for _ in range(10):
        plc.scan(1.0)
    assert decoded(plc, VAULT_RAD_USVH) >= 20.0
    plc.write_coil(0, False)
    assert plc.coil(0) is False


def test_unmapped_coil_rejected() -> None:
    plc = make_plc()
    with pytest.raises(IllegalAddressError):
        plc.coil(1)
    with pytest.raises(IllegalAddressError):
        plc.write_coil(1, True)
    assert plc.reactor.fault_active is False


# --- config ----------------------------------------------------------------------------------

def test_config_defaults() -> None:
    cfg = PlcConfig.from_env({})
    assert cfg == PlcConfig(bind_host="0.0.0.0", modbus_port=5020, seed=None, tick_seconds=1.0)


def test_config_from_env() -> None:
    cfg = PlcConfig.from_env(
        {"PLC_BIND_HOST": "127.0.0.1", "PLC_MODBUS_PORT": "1502", "SIM_SEED": "42", "SIM_TICK_SECONDS": "0.5"}
    )
    assert cfg == PlcConfig("127.0.0.1", 1502, 42, 0.5)


@pytest.mark.parametrize(
    "env", [{"PLC_MODBUS_PORT": "0"}, {"PLC_MODBUS_PORT": "70000"}, {"SIM_TICK_SECONDS": "0"}, {"SIM_SEED": "x"}]
)
def test_config_rejects_bad_values(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        PlcConfig.from_env(env)
