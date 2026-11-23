"""Modbus TCP front end for the Virtual PLC (pymodbus 3.16 SimDevice API).

pymodbus keeps its own register arrays; the ``action`` hook runs on every request before the
response is built. On reads we copy the PLC's latched image into those arrays; on writes we
validate and apply them to the PLC first, returning a Modbus exception code if rejected.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable

from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from simulators.virtual_plc.plc import IllegalAddressError, IllegalValueError, VirtualPlc
from simulators.virtual_plc.tag_map import COILS, HOLDING_REGISTERS, INPUT_REGISTERS, MODBUS_DEVICE_ID

FC_READ_COILS = 1
FC_READ_DISCRETE_INPUTS = 2
FC_READ_HOLDING = 3
FC_READ_INPUT = 4
FC_WRITE_COIL = 5
FC_WRITE_REGISTER = 6
FC_WRITE_COILS = 15
FC_WRITE_REGISTERS = 16

COIL_FCS = frozenset({FC_READ_COILS, FC_WRITE_COIL, FC_WRITE_COILS})
HOLDING_FCS = frozenset({FC_READ_HOLDING, FC_WRITE_REGISTER, FC_WRITE_REGISTERS, 22, 23})

Sleeper = Callable[[float], Awaitable[None]]


async def plc_action(
    plc: VirtualPlc,
    func_code: int,
    start_address: int,
    address: int,
    count: int,
    registers: list[int],
    set_values: list[int] | list[bool] | None,
) -> ExcCodes | None:
    """Bridge one Modbus request to the PLC. Mutates ``registers`` in place (pymodbus contract)."""
    if func_code == FC_READ_DISCRETE_INPUTS:
        return ExcCodes.ILLEGAL_ADDRESS  # no discrete inputs in the map
    try:
        if set_values is not None:
            _apply_writes(plc, func_code, address, set_values)
    except IllegalAddressError:
        return ExcCodes.ILLEGAL_ADDRESS
    except IllegalValueError:
        return ExcCodes.ILLEGAL_VALUE

    if func_code in COIL_FCS:
        # Coils are packed 16 per register, LSB = lowest address; only coil 0 is mapped.
        registers[0 - start_address] = 1 if plc.coil(COILS[0].address) else 0
    elif func_code in HOLDING_FCS:
        for point in HOLDING_REGISTERS:
            registers[point.address - start_address] = plc.holding_register(point.address)
    elif func_code == FC_READ_INPUT:
        for addr, raw in enumerate(plc.image.input_registers):
            registers[addr - start_address] = raw
    return None


def _apply_writes(plc: VirtualPlc, func_code: int, address: int, values: list[int] | list[bool]) -> None:
    # Validate every value before applying any, so a multi-write (FC15/16) is all-or-nothing.
    if func_code in COIL_FCS:
        for offset in range(len(values)):
            plc.validate_coil_write(address + offset)
        for offset, bit in enumerate(values):
            plc.write_coil(address + offset, bool(bit))
    else:
        for offset, raw in enumerate(values):
            plc.validate_holding_write(address + offset, int(raw))
        for offset, raw in enumerate(values):
            plc.write_holding_register(address + offset, int(raw))


def build_device(plc: VirtualPlc, device_id: int = MODBUS_DEVICE_ID) -> SimDevice:
    """Separate (non-shared) blocks: coils, discrete inputs, holding registers, input registers."""
    return SimDevice(
        device_id,
        simdata=(
            [SimData(0, values=False, datatype=DataType.BITS)],
            [SimData(0, values=False, datatype=DataType.BITS)],
            [SimData(0, count=len(HOLDING_REGISTERS), values=0, datatype=DataType.REGISTERS)],
            [SimData(0, count=len(INPUT_REGISTERS), values=0, datatype=DataType.REGISTERS, readonly=True)],
        ),
        action=functools.partial(plc_action, plc),
    )


class ModbusPlcServer:
    """Modbus TCP listener for one VirtualPlc. ``port=0`` binds an ephemeral port (tests)."""

    def __init__(self, plc: VirtualPlc, host: str, port: int, device_id: int = MODBUS_DEVICE_ID) -> None:
        self._server = ModbusTcpServer(build_device(plc, device_id), address=(host, port))
        self._port: int | None = None

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("Server not started")
        return self._port

    async def start(self) -> int:
        await self._server.serve_forever(background=True)
        transport = self._server.transport
        if transport is None:
            raise RuntimeError("Modbus server failed to listen")
        self._port = int(transport.sockets[0].getsockname()[1])  # type: ignore[attr-defined]
        return self._port

    async def stop(self) -> None:
        await self._server.shutdown()


async def scan_loop(
    plc: VirtualPlc,
    tick_seconds: float,
    stop: asyncio.Event,
    sleep: Sleeper = asyncio.sleep,
) -> None:
    """Scan the PLC every ``tick_seconds`` (simulated dt = tick) until ``stop`` is set."""
    if tick_seconds <= 0:
        raise ValueError("tick_seconds must be > 0")
    while not stop.is_set():
        await sleep(tick_seconds)
        plc.scan(tick_seconds)
