"""Smoke test against a RUNNING plant container (skipped unless PLANT_SMOKE_HOST is set).

    docker compose -f deploy/docker-compose.plant.yml up -d --build --wait
    PLANT_SMOKE_HOST=127.0.0.1 pytest -q simulators/virtual_plc/tests/test_container_smoke.py

Uses the published host ports (Modbus 502, OPC UA 4840), so it also verifies the compose port mapping.
Read-only: it never writes setpoints or triggers faults on the target.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from asyncua import Client, ua
from pymodbus.client import AsyncModbusTcpClient

from simulators.virtual_plc.tag_map import (
    INPUT_REGISTERS,
    MODBUS_DEVICE_ID,
    OPCUA_ENDPOINT_PATH,
    OPCUA_NAMESPACE_URI,
    PLC_HEARTBEAT_COUNT,
    decode_int16,
    point_by_tag,
)

HOST = os.environ.get("PLANT_SMOKE_HOST", "")
MODBUS_PORT = int(os.environ.get("PLANT_SMOKE_MODBUS_PORT", "502"))
OPCUA_PORT = int(os.environ.get("PLANT_SMOKE_OPCUA_PORT", "4840"))

pytestmark = pytest.mark.skipif(not HOST, reason="PLANT_SMOKE_HOST not set (needs a running plant container)")

HB = point_by_tag(INPUT_REGISTERS, PLC_HEARTBEAT_COUNT)


async def _read_modbus(client: AsyncModbusTcpClient) -> list[int]:
    rr = await client.read_input_registers(0, count=len(INPUT_REGISTERS), device_id=MODBUS_DEVICE_ID)
    assert not rr.isError(), rr
    return list(rr.registers)


def test_plant_serves_both_protocols() -> None:
    async def main() -> None:
        mb = AsyncModbusTcpClient(HOST, port=MODBUS_PORT, timeout=3)
        assert await mb.connect(), f"Modbus {HOST}:{MODBUS_PORT} unreachable"
        try:
            async with Client(f"opc.tcp://{HOST}:{OPCUA_PORT}{OPCUA_ENDPOINT_PATH}", timeout=5) as opc:
                ns = await opc.get_namespace_index(OPCUA_NAMESPACE_URI)

                first = await _read_modbus(mb)
                await asyncio.sleep(2.5)  # > 2 scans at the default 1 s tick
                regs = await _read_modbus(mb)
                assert regs[HB.address] != first[HB.address], "heartbeat not advancing: PLC scan loop stalled"

                # Both protocols, same tags, sane values (within one scan of each other).
                for p in INPUT_REGISTERS:
                    if p.tag == PLC_HEARTBEAT_COUNT:
                        continue
                    modbus_value = decode_int16(regs[p.address], p.scale)
                    opc_value = await opc.get_node(ua.NodeId(p.tag, ns)).read_value()
                    assert isinstance(opc_value, float), p.tag
                    assert modbus_value == pytest.approx(opc_value, rel=0.05, abs=0.5), p.tag
        finally:
            mb.close()

    asyncio.run(asyncio.wait_for(main(), timeout=60))
