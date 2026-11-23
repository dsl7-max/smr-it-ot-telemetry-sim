"""Virtual PLC logic, protocol-independent.

The PLC owns the L0 plant model as its I/O image. Each ``scan()`` steps the plant and latches one
consistent snapshot; every protocol (Modbus now, OPC UA in US-203) serves that same snapshot, so
all registers read in one request come from the same tick.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulators.citadel_reactor import CitadelReactor
from simulators.virtual_plc.tag_map import (
    COILS,
    FAULT_TRIGGER,
    HOLDING_REGISTERS,
    INPUT_REGISTERS,
    INT16_MAX,
    PLC_HEARTBEAT_COUNT,
    POWER_SETPOINT_MAX_PCT,
    POWER_SETPOINT_MIN_PCT,
    POWER_SETPOINT_PCT,
    RegisterPoint,
    decode_int16,
    encode_int16,
    point_by_tag,
)


class PlcWriteError(Exception):
    """Base class for rejected writes."""


class IllegalAddressError(PlcWriteError):
    """No writable point at that address (maps to Modbus exception 02)."""


class IllegalValueError(PlcWriteError):
    """Value outside the point's allowed range (maps to Modbus exception 03)."""


@dataclass(frozen=True, slots=True)
class ProcessImage:
    """One latched scan: engineering values plus their encoded input registers."""

    scan_count: int
    values: dict[str, float]
    input_registers: tuple[int, ...]


class VirtualPlc:
    def __init__(self, reactor: CitadelReactor) -> None:
        produced = {spec.name for spec in reactor.tags}
        missing = {p.tag for p in INPUT_REGISTERS if p.tag != PLC_HEARTBEAT_COUNT} - produced
        if missing:
            raise ValueError(f"Reactor does not produce mapped tags: {sorted(missing)}")
        self._reactor = reactor
        self._image = self._latch(scan_count=0)

    @property
    def reactor(self) -> CitadelReactor:
        return self._reactor

    @property
    def image(self) -> ProcessImage:
        return self._image

    def scan(self, dt: float) -> ProcessImage:
        """Advance the plant by ``dt`` seconds and latch a new process image."""
        self._reactor.step(dt)
        self._image = self._latch(scan_count=self._image.scan_count + 1)
        return self._image

    def _latch(self, scan_count: int) -> ProcessImage:
        values = {r.tag: r.value for r in self._reactor.read()}
        heartbeat = scan_count % (INT16_MAX + 1)
        registers = [0] * len(INPUT_REGISTERS)
        for point in INPUT_REGISTERS:
            value = float(heartbeat) if point.tag == PLC_HEARTBEAT_COUNT else values[point.tag]
            registers[point.address] = encode_int16(value, point.scale)
        return ProcessImage(scan_count=scan_count, values=values, input_registers=tuple(registers))

    # --- holding registers ---------------------------------------------------------------

    def holding_register(self, address: int) -> int:
        point = self._holding_point(address)
        return encode_int16(self._reactor.power_setpoint_pct, point.scale)

    def validate_holding_write(self, address: int, register: int) -> float:
        """Check a write without applying it; returns the decoded engineering value."""
        point = self._holding_point(address)
        value = decode_int16(register, point.scale)
        if not POWER_SETPOINT_MIN_PCT <= value <= POWER_SETPOINT_MAX_PCT:
            raise IllegalValueError(
                f"{point.tag} {value} outside {POWER_SETPOINT_MIN_PCT}-{POWER_SETPOINT_MAX_PCT} %"
            )
        return value

    def write_holding_register(self, address: int, register: int) -> None:
        self._reactor.set_power_setpoint(self.validate_holding_write(address, register))

    def _holding_point(self, address: int) -> RegisterPoint:
        point = point_by_tag(HOLDING_REGISTERS, POWER_SETPOINT_PCT)
        if address != point.address:
            raise IllegalAddressError(f"No holding register at address {address}")
        return point

    # --- coils ---------------------------------------------------------------------------

    def coil(self, address: int) -> bool:
        self._coil_point(address)
        return self._reactor.fault_active

    def validate_coil_write(self, address: int) -> None:
        self._coil_point(address)

    def write_coil(self, address: int, value: bool) -> None:
        self._coil_point(address)
        if value:
            self._reactor.trigger_fault()
        else:
            self._reactor.clear_fault()

    @staticmethod
    def _coil_point(address: int) -> None:
        if not any(c.address == address and c.tag == FAULT_TRIGGER for c in COILS):
            raise IllegalAddressError(f"No coil at address {address}")
