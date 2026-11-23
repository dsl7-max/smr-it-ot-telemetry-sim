"""Virtual PLC logic, protocol-independent.

The PLC owns the L0 plant model as its I/O image. Each ``scan()`` steps the plant and latches one
consistent snapshot (``ProcessImage``); Modbus and OPC UA both serve that same snapshot, so every
value read in one request comes from the same tick and both protocols agree.

Commands (power setpoint, fault trigger/clear) go through the engineering-unit methods here;
protocol adapters only translate encodings and map errors to their own status codes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

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
    """No writable point at that address (Modbus exception 02)."""


class IllegalValueError(PlcWriteError):
    """Value outside the point's allowed range (Modbus exception 03, OPC UA BadOutOfRange)."""


@dataclass(frozen=True, slots=True)
class ProcessImage:
    """One latched scan: engineering values, heartbeat, and the encoded input registers."""

    scan_count: int
    heartbeat: int
    timestamp: datetime
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
        readings = self._reactor.read()
        values = {r.tag: r.value for r in readings}
        heartbeat = scan_count % (INT16_MAX + 1)
        registers = [0] * len(INPUT_REGISTERS)
        for point in INPUT_REGISTERS:
            value = float(heartbeat) if point.tag == PLC_HEARTBEAT_COUNT else values[point.tag]
            registers[point.address] = encode_int16(value, point.scale)
        return ProcessImage(
            scan_count=scan_count,
            heartbeat=heartbeat,
            timestamp=readings[0].timestamp,
            values=values,
            input_registers=tuple(registers),
        )

    # --- commands (engineering units, protocol-neutral) -------------------------------------

    @property
    def power_setpoint_pct(self) -> float:
        return self._reactor.power_setpoint_pct

    @staticmethod
    def validate_power_setpoint(pct: float) -> None:
        if math.isnan(pct) or not POWER_SETPOINT_MIN_PCT <= pct <= POWER_SETPOINT_MAX_PCT:
            raise IllegalValueError(
                f"{POWER_SETPOINT_PCT} {pct} outside {POWER_SETPOINT_MIN_PCT}-{POWER_SETPOINT_MAX_PCT} %"
            )

    def set_power_setpoint(self, pct: float) -> None:
        self.validate_power_setpoint(pct)
        self._reactor.set_power_setpoint(pct)

    @property
    def fault_active(self) -> bool:
        return self._reactor.fault_active

    def trigger_fault(self) -> float:
        """Start the vault radiation fault; returns the fault level (uSv/h). Idempotent."""
        return self._reactor.trigger_fault()

    def clear_fault(self) -> None:
        self._reactor.clear_fault()

    # --- Modbus holding registers ----------------------------------------------------------

    def holding_register(self, address: int) -> int:
        point = self._holding_point(address)
        return encode_int16(self.power_setpoint_pct, point.scale)

    def validate_holding_write(self, address: int, register: int) -> float:
        """Check a write without applying it; returns the decoded engineering value."""
        point = self._holding_point(address)
        value = decode_int16(register, point.scale)
        self.validate_power_setpoint(value)
        return value

    def write_holding_register(self, address: int, register: int) -> None:
        self.set_power_setpoint(self.validate_holding_write(address, register))

    @staticmethod
    def _holding_point(address: int) -> RegisterPoint:
        point = point_by_tag(HOLDING_REGISTERS, POWER_SETPOINT_PCT)
        if address != point.address:
            raise IllegalAddressError(f"No holding register at address {address}")
        return point

    # --- Modbus coils ------------------------------------------------------------------------

    def coil(self, address: int) -> bool:
        self._coil_point(address)
        return self.fault_active

    def validate_coil_write(self, address: int) -> None:
        self._coil_point(address)

    def write_coil(self, address: int, value: bool) -> None:
        self._coil_point(address)
        if value:
            self.trigger_fault()
        else:
            self.clear_fault()

    @staticmethod
    def _coil_point(address: int) -> None:
        if not any(c.address == address and c.tag == FAULT_TRIGGER for c in COILS):
            raise IllegalAddressError(f"No coil at address {address}")
