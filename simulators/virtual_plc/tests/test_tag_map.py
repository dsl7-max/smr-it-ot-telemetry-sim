from __future__ import annotations

import re
from pathlib import Path

import pytest

from simulators.citadel_reactor import ALL_TAG_NAMES, DEFAULT_TAGS
from simulators.virtual_plc.tag_map import (
    COILS,
    HOLDING_REGISTERS,
    INPUT_REGISTERS,
    INT16_MAX,
    INT16_MIN,
    PLC_HEARTBEAT_COUNT,
    RegisterPoint,
    decode_int16,
    encode_int16,
)

DOC = Path(__file__).resolve().parents[3] / "docs" / "tag-map.md"


# --- scaling -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "scale"),
    [(0.0, 10), (750.3, 10), (70.12, 100), (1.5, 100), (0.2, 100), (100.0, 10), (-12.3, 10), (32767, 1)],
)
def test_encode_decode_round_trip(value: float, scale: int) -> None:
    assert decode_int16(encode_int16(value, scale), scale) == pytest.approx(value, abs=0.5 / scale)


def test_encode_rounds_to_nearest() -> None:
    assert encode_int16(750.34, 10) == 7503
    assert encode_int16(750.36, 10) == 7504


def test_negative_values_use_twos_complement() -> None:
    assert encode_int16(-1, 1) == 0xFFFF
    assert decode_int16(0xFFFF, 1) == -1
    assert decode_int16(0x8000, 1) == INT16_MIN


def test_encode_saturates_instead_of_wrapping() -> None:
    assert decode_int16(encode_int16(5000.0, 10), 10) == INT16_MAX / 10
    assert decode_int16(encode_int16(-5000.0, 10), 10) == INT16_MIN / 10


@pytest.mark.parametrize("register", [-1, 0x10000])
def test_decode_rejects_non_16_bit(register: int) -> None:
    with pytest.raises(ValueError):
        decode_int16(register, 1)


def test_register_point_validation() -> None:
    with pytest.raises(ValueError):
        RegisterPoint("X", -1, 1, "")
    with pytest.raises(ValueError):
        RegisterPoint("X", 0, 0, "")


# --- map integrity -------------------------------------------------------------------------

def test_every_plant_tag_is_mapped_once() -> None:
    mapped = [p.tag for p in INPUT_REGISTERS if p.tag != PLC_HEARTBEAT_COUNT]
    assert sorted(mapped) == sorted(ALL_TAG_NAMES)


def test_input_registers_are_contiguous_from_zero() -> None:
    assert [p.address for p in INPUT_REGISTERS] == list(range(len(INPUT_REGISTERS)))


def test_units_match_simulator() -> None:
    units = {s.name: s.unit for s in DEFAULT_TAGS}
    for p in INPUT_REGISTERS:
        if p.tag in units:
            assert p.unit == units[p.tag]


def test_instrument_span_fits_in_int16() -> None:
    spans = {s.name: s for s in DEFAULT_TAGS}
    for p in INPUT_REGISTERS:
        if p.tag in spans:
            assert spans[p.tag].span_high * p.scale <= INT16_MAX
            assert spans[p.tag].span_low * p.scale >= INT16_MIN


def test_tag_names_are_upper_snake() -> None:
    for tag in [p.tag for p in INPUT_REGISTERS + HOLDING_REGISTERS] + [c.tag for c in COILS]:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", tag)


# --- docs/tag-map.md must match the code ------------------------------------------------------

_TABLES = {"Input register": INPUT_REGISTERS, "Holding register": HOLDING_REGISTERS}
_MODICON_BASE = {"Input register": 30001, "Holding register": 40001, "Coil": 1}


def _doc_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 9 and (m := re.fullmatch(r"`([A-Z0-9_]+)`", cells[0])):
            rows[m.group(1)] = cells
    return rows


def test_doc_lists_exactly_the_mapped_points() -> None:
    code_tags = {p.tag for pts in _TABLES.values() for p in pts} | {c.tag for c in COILS}
    assert set(_doc_rows()) == code_tags


@pytest.mark.parametrize(("table", "points"), list(_TABLES.items()))
def test_doc_register_rows_match_code(table: str, points: tuple[RegisterPoint, ...]) -> None:
    rows = _doc_rows()
    for p in points:
        _tag, unit, doc_table, _fc, address, modicon, scale, _access, _range = rows[p.tag]
        assert doc_table == table, p.tag
        assert int(address) == p.address, p.tag
        assert int(modicon) == _MODICON_BASE[table] + p.address, p.tag
        assert int(scale) == p.scale, p.tag
        assert unit == p.unit, p.tag


def test_doc_coil_rows_match_code() -> None:
    rows = _doc_rows()
    for c in COILS:
        _tag, _unit, doc_table, _fc, address, modicon, *_ = rows[c.tag]
        assert doc_table == "Coil"
        assert int(address) == c.address
        assert int(modicon) == _MODICON_BASE["Coil"] + c.address
