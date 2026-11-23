"""In-process Modbus TCP tests: real server on 127.0.0.1:<ephemeral>, real pymodbus client."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import pytest
from pymodbus.client import AsyncModbusTcpClient

from simulators.citadel_reactor import CitadelReactor
from simulators.virtual_plc.modbus_server import ModbusPlcServer, scan_loop
from simulators.virtual_plc.plc import VirtualPlc
from simulators.virtual_plc.tag_map import (
    INPUT_REGISTERS,
    MODBUS_DEVICE_ID,
    PLC_HEARTBEAT_COUNT,
    decode_int16,
)

ILLEGAL_ADDRESS = 2
ILLEGAL_VALUE = 3
N_IR = len(INPUT_REGISTERS)

Scenario = Callable[[VirtualPlc, AsyncModbusTcpClient], Awaitable[None]]


def run_with_server(scenario: Scenario, seed: int = 11) -> VirtualPlc:
    plc = VirtualPlc(CitadelReactor(rng=random.Random(seed)))

    async def main() -> None:
        server = ModbusPlcServer(plc, "127.0.0.1", 0)
        port = await server.start()
        client = AsyncModbusTcpClient("127.0.0.1", port=port, timeout=2)
        try:
            assert await client.connect()
            await scenario(plc, client)
        finally:
            client.close()
            await server.stop()

    asyncio.run(asyncio.wait_for(main(), timeout=20))
    return plc


def test_fc04_returns_latched_image() -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        plc.scan(1.0)
        rr = await c.read_input_registers(0, count=N_IR, device_id=MODBUS_DEVICE_ID)
        assert not rr.isError()
        assert tuple(rr.registers) == plc.image.input_registers
        for p in INPUT_REGISTERS:
            if p.tag != PLC_HEARTBEAT_COUNT:
                assert decode_int16(rr.registers[p.address], p.scale) == pytest.approx(
                    plc.image.values[p.tag], abs=0.5 / p.scale
                )

    run_with_server(scenario)


def test_fc04_partial_read_with_offset() -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        rr = await c.read_input_registers(4, count=2, device_id=MODBUS_DEVICE_ID)
        assert rr.registers == list(plc.image.input_registers[4:6])

    run_with_server(scenario)


def test_heartbeat_advances_with_scan_loop() -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(scan_loop(plc, 0.01, stop))
        hb = INPUT_REGISTERS[-1].address
        first = (await c.read_input_registers(hb, count=1, device_id=MODBUS_DEVICE_ID)).registers[0]
        await asyncio.sleep(0.1)
        second = (await c.read_input_registers(hb, count=1, device_id=MODBUS_DEVICE_ID)).registers[0]
        stop.set()
        await task
        assert second > first

    run_with_server(scenario)


def test_fc06_setpoint_write_and_fc03_readback() -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        wr = await c.write_register(0, 500, device_id=MODBUS_DEVICE_ID)
        assert not wr.isError()
        assert plc.reactor.power_setpoint_pct == 50.0
        rr = await c.read_holding_registers(0, count=1, device_id=MODBUS_DEVICE_ID)
        assert rr.registers == [500]

    run_with_server(scenario)


def test_fc03_reflects_setpoint_changed_outside_modbus() -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        plc.reactor.set_power_setpoint(25.0)  # e.g. via OPC UA (US-203)
        rr = await c.read_holding_registers(0, count=1, device_id=MODBUS_DEVICE_ID)
        assert rr.registers == [250]

    run_with_server(scenario)


def test_fc06_out_of_range_returns_illegal_value() -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        wr = await c.write_register(0, 1001, device_id=MODBUS_DEVICE_ID)
        assert wr.isError() and wr.exception_code == ILLEGAL_VALUE
        assert plc.reactor.power_setpoint_pct == 100.0
        rr = await c.read_holding_registers(0, count=1, device_id=MODBUS_DEVICE_ID)
        assert rr.registers == [1000]

    run_with_server(scenario)


def test_fc05_coil_triggers_and_clears_fault() -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        assert not (await c.write_coil(0, True, device_id=MODBUS_DEVICE_ID)).isError()
        assert plc.reactor.fault_active
        assert (await c.read_coils(0, count=1, device_id=MODBUS_DEVICE_ID)).bits[0] is True
        assert not (await c.write_coil(0, False, device_id=MODBUS_DEVICE_ID)).isError()
        assert not plc.reactor.fault_active
        assert (await c.read_coils(0, count=1, device_id=MODBUS_DEVICE_ID)).bits[0] is False

    run_with_server(scenario)


def test_fc15_multi_coil_write_is_all_or_nothing() -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        wr = await c.write_coils(0, [True, True], device_id=MODBUS_DEVICE_ID)  # coil 1 unmapped
        assert wr.isError() and wr.exception_code == ILLEGAL_ADDRESS
        assert not plc.reactor.fault_active

    run_with_server(scenario)


@pytest.mark.parametrize(
    ("op", "kwargs"),
    [
        ("read_input_registers", {"address": N_IR, "count": 1}),
        ("read_input_registers", {"address": 0, "count": N_IR + 1}),
        ("read_holding_registers", {"address": 1, "count": 1}),
        ("read_discrete_inputs", {"address": 0, "count": 1}),
        ("write_register", {"address": 1, "value": 0}),
    ],
)
def test_unmapped_addresses_return_illegal_address(op: str, kwargs: dict[str, int]) -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        rr = await getattr(c, op)(device_id=MODBUS_DEVICE_ID, **kwargs)
        assert rr.isError() and rr.exception_code == ILLEGAL_ADDRESS

    run_with_server(scenario)


def test_fc16_multi_register_write_is_all_or_nothing() -> None:
    async def scenario(plc: VirtualPlc, c: AsyncModbusTcpClient) -> None:
        rr = await c.write_registers(0, [500, 500], device_id=MODBUS_DEVICE_ID)  # HR 1 unmapped
        assert rr.isError() and rr.exception_code == ILLEGAL_ADDRESS
        assert plc.reactor.power_setpoint_pct == 100.0

    run_with_server(scenario)


def test_scan_loop_rejects_non_positive_tick() -> None:
    plc = VirtualPlc(CitadelReactor(rng=random.Random(1)))
    with pytest.raises(ValueError):
        asyncio.run(scan_loop(plc, 0.0, asyncio.Event()))
