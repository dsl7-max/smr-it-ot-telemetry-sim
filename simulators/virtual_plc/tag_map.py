"""Modbus register map for the Citadel Virtual PLC (single source of truth; mirrored in docs/tag-map.md).

All addresses are 0-based protocol addresses (what goes on the wire). Analog values are scaled
signed INT16: ``raw = round(value * scale)``, saturated to -32768..32767, sent as a 16-bit register.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulators.citadel_reactor import (
    CONTROL_ROOM_RAD_USVH,
    CORE_TEMP_C,
    DEFAULT_TAGS,
    HE_FLOW_KGS,
    HE_LOOP_PRESS_BAR,
    REACTOR_POWER_PCT,
    VAULT_RAD_USVH,
)

INT16_MIN = -32768
INT16_MAX = 32767
UINT16_MAX = 0xFFFF

MODBUS_DEVICE_ID = 1

PLC_HEARTBEAT_COUNT = "PLC_HEARTBEAT_COUNT"
POWER_SETPOINT_PCT = "POWER_SETPOINT_PCT"
FAULT_TRIGGER = "FAULT_TRIGGER"

POWER_SETPOINT_MIN_PCT = 0.0
POWER_SETPOINT_MAX_PCT = 100.0


@dataclass(frozen=True, slots=True)
class RegisterPoint:
    """One 16-bit register: tag, 0-based address, scale factor (raw = value * scale), unit."""

    tag: str
    address: int
    scale: int
    unit: str

    def __post_init__(self) -> None:
        if not 0 <= self.address <= UINT16_MAX:
            raise ValueError(f"{self.tag}: address {self.address} outside 0-65535")
        if self.scale < 1:
            raise ValueError(f"{self.tag}: scale must be >= 1")


@dataclass(frozen=True, slots=True)
class CoilPoint:
    """One coil (single bit), 0-based address."""

    tag: str
    address: int


_UNITS: dict[str, str] = {spec.name: spec.unit for spec in DEFAULT_TAGS}

# FC04, read-only. One register per plant tag, contiguous from 0, plus a scan heartbeat.
INPUT_REGISTERS: tuple[RegisterPoint, ...] = (
    RegisterPoint(REACTOR_POWER_PCT, 0, 10, _UNITS[REACTOR_POWER_PCT]),
    RegisterPoint(CORE_TEMP_C, 1, 10, _UNITS[CORE_TEMP_C]),
    RegisterPoint(HE_LOOP_PRESS_BAR, 2, 100, _UNITS[HE_LOOP_PRESS_BAR]),
    RegisterPoint(HE_FLOW_KGS, 3, 10, _UNITS[HE_FLOW_KGS]),
    RegisterPoint(VAULT_RAD_USVH, 4, 100, _UNITS[VAULT_RAD_USVH]),
    RegisterPoint(CONTROL_ROOM_RAD_USVH, 5, 100, _UNITS[CONTROL_ROOM_RAD_USVH]),
    RegisterPoint(PLC_HEARTBEAT_COUNT, 6, 1, "count"),
)

# FC03 read / FC06, FC16 write.
HOLDING_REGISTERS: tuple[RegisterPoint, ...] = (
    RegisterPoint(POWER_SETPOINT_PCT, 0, 10, "%"),
)

# FC01 read / FC05, FC15 write. 1 = trigger vault radiation fault, 0 = clear.
COILS: tuple[CoilPoint, ...] = (
    CoilPoint(FAULT_TRIGGER, 0),
)


def encode_int16(value: float, scale: int) -> int:
    """Engineering value -> 16-bit register (signed INT16, two's complement), saturating."""
    raw = round(value * scale)
    raw = max(INT16_MIN, min(INT16_MAX, raw))
    return raw & UINT16_MAX


def decode_int16(register: int, scale: int) -> float:
    """16-bit register (signed INT16, two's complement) -> engineering value."""
    if not 0 <= register <= UINT16_MAX:
        raise ValueError(f"Register value {register} outside 0-65535")
    signed = register - 0x10000 if register > INT16_MAX else register
    return signed / scale


def point_by_tag(points: tuple[RegisterPoint, ...], tag: str) -> RegisterPoint:
    for point in points:
        if point.tag == tag:
            return point
    raise KeyError(tag)
