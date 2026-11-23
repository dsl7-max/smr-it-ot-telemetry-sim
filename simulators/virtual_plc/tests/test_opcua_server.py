"""In-process OPC UA tests: real asyncua server + client (and a Modbus server for agreement checks)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from asyncua import Client, ua
from pymodbus.client import AsyncModbusTcpClient

from simulators.citadel_reactor import CitadelReactor
from simulators.virtual_plc.modbus_server import ModbusPlcServer
from simulators.virtual_plc.opcua_server import OpcUaPlcServer, eu_information, unece_unit_id
from simulators.virtual_plc.plc import VirtualPlc
from simulators.virtual_plc.runtime import scan_loop
from simulators.virtual_plc.tag_map import (
    CLEAR_FAULT_METHOD,
    FAULT_TRIGGER,
    INPUT_REGISTERS,
    MODBUS_DEVICE_ID,
    OPCUA_METHODS,
    OPCUA_NAMESPACE_URI,
    OPCUA_VARIABLES,
    PLC_HEARTBEAT_COUNT,
    POWER_SETPOINT_PCT,
    TRIGGER_FAULT_METHOD,
    VAULT_FOLDER,
    decode_int16,
)

_VARIANT = {"Double": ua.VariantType.Double, "Int32": ua.VariantType.Int32, "Boolean": ua.VariantType.Boolean}
N_IR = len(INPUT_REGISTERS)


@dataclass
class Rig:
    plc: VirtualPlc
    opcua: OpcUaPlcServer
    ua: Client
    mb: AsyncModbusTcpClient
    ns: int

    def node(self, node_id: str):  # noqa: ANN201 - asyncua Node
        return self.ua.get_node(ua.NodeId(node_id, self.ns))

    async def scans(self, n: int) -> None:
        for _ in range(n):
            await self.opcua.publish(self.plc.scan(1.0))


Scenario = Callable[[Rig], Awaitable[None]]


def run_rig(scenario: Scenario, seed: int = 5) -> None:
    plc = VirtualPlc(CitadelReactor(rng=random.Random(seed)))

    async def main() -> None:
        modbus = ModbusPlcServer(plc, "127.0.0.1", 0)
        opcua = OpcUaPlcServer(plc, "127.0.0.1", 0)
        await modbus.start()
        await opcua.start()
        mb = AsyncModbusTcpClient("127.0.0.1", port=modbus.port, timeout=2)
        try:
            assert await mb.connect()
            async with Client(opcua.endpoint, timeout=5) as client:
                ns = await client.get_namespace_index(OPCUA_NAMESPACE_URI)
                await scenario(Rig(plc, opcua, client, mb, ns))
        finally:
            mb.close()
            await opcua.stop()
            await modbus.stop()

    asyncio.run(asyncio.wait_for(main(), timeout=30))


# --- pure helpers (no server) ------------------------------------------------------------------

def test_unece_unit_id_packs_ascii() -> None:
    assert unece_unit_id("CEL") == 4408652  # value published in the OPC UA units CSV
    assert unece_unit_id("BAR") == 4342098


def test_eu_information_without_standard_code() -> None:
    eu = eu_information("µSv/h")
    assert eu.UnitId == -1
    assert eu.DisplayName.Text == "µSv/h"


# --- address space -----------------------------------------------------------------------------

def test_address_space_matches_tag_map() -> None:
    async def scenario(r: Rig) -> None:
        assert r.ns == 2  # documented as ns=2 in tag-map.md
        objects = r.ua.nodes.objects
        for var in OPCUA_VARIABLES:
            path = [f"{r.ns}:{p}" for p in (*var.folder, var.tag)]
            node = await objects.get_child(path)
            assert node.nodeid == ua.NodeId(var.node_id, r.ns)
            assert await node.read_data_type_as_variant_type() == _VARIANT[var.data_type]
            access = (await node.read_attribute(ua.AttributeIds.AccessLevel)).Value.Value
            assert bool(ua.ua_binary.test_bit(access, ua.AccessLevel.CurrentWrite)) == var.writable, var.tag
        for name, folder in OPCUA_METHODS:
            method = await objects.get_child([f"{r.ns}:{p}" for p in (*folder, name)])
            assert await method.read_node_class() == ua.NodeClass.Method

    run_rig(scenario)


def test_engineering_units_and_range_properties() -> None:
    async def scenario(r: Rig) -> None:
        node = r.node("CORE_TEMP_C")
        eu = await (await node.get_child(f"{r.ns}:EngineeringUnits")).read_value()
        assert eu.DisplayName.Text == "°C" and eu.UnitId == unece_unit_id("CEL")
        rng = await (await node.get_child(f"{r.ns}:EURange")).read_value()
        assert (rng.Low, rng.High) == (0.0, 1500.0)

    run_rig(scenario)


# --- reads and Modbus agreement ------------------------------------------------------------------

def test_reads_equal_latched_image() -> None:
    async def scenario(r: Rig) -> None:
        await r.scans(3)
        image = r.plc.image
        for tag, value in image.values.items():
            assert await r.node(tag).read_value() == value
        assert await r.node(PLC_HEARTBEAT_COUNT).read_value() == image.heartbeat == 3

    run_rig(scenario)


def test_source_timestamp_is_scan_time() -> None:
    async def scenario(r: Rig) -> None:
        await r.scans(1)
        dv = await r.node("CORE_TEMP_C").read_data_value()
        assert dv.SourceTimestamp == r.plc.image.timestamp

    run_rig(scenario)


def test_modbus_and_opcua_agree_on_every_tag_same_scan() -> None:
    async def scenario(r: Rig) -> None:
        r.plc.trigger_fault()
        r.plc.set_power_setpoint(60.0)
        for _ in range(4):
            await r.scans(5)
            regs = (await r.mb.read_input_registers(0, count=N_IR, device_id=MODBUS_DEVICE_ID)).registers
            for p in INPUT_REGISTERS:
                opc_value = await r.node(p.tag).read_value()
                assert decode_int16(regs[p.address], p.scale) == pytest.approx(opc_value, abs=0.5 / p.scale), p.tag

    run_rig(scenario)


def test_subscription_receives_scan_updates() -> None:
    async def scenario(r: Rig) -> None:
        received: list[int] = []

        class Handler:
            def datachange_notification(self, node, val, data) -> None:  # noqa: ANN001 - asyncua callback
                received.append(val)

        sub = await r.ua.create_subscription(20, Handler())
        await sub.subscribe_data_change(r.node(PLC_HEARTBEAT_COUNT))
        stop = asyncio.Event()
        task = asyncio.create_task(scan_loop(r.plc, 0.05, stop, on_scan=[r.opcua.publish]))
        await asyncio.sleep(0.6)
        stop.set()
        await task
        await sub.delete()
        assert len(set(received)) >= 3  # heartbeat ticks pushed to the client without polling

    run_rig(scenario)


# --- writes --------------------------------------------------------------------------------------

def test_setpoint_write_applies_to_plc_and_modbus() -> None:
    async def scenario(r: Rig) -> None:
        await r.node(POWER_SETPOINT_PCT).write_value(ua.DataValue(ua.Variant(50.0, ua.VariantType.Double)))
        assert r.plc.power_setpoint_pct == 50.0
        assert await r.node(POWER_SETPOINT_PCT).read_value() == 50.0
        hr = await r.mb.read_holding_registers(0, count=1, device_id=MODBUS_DEVICE_ID)
        assert hr.registers == [500]

    run_rig(scenario)


def test_setpoint_out_of_range_is_bad_out_of_range() -> None:
    async def scenario(r: Rig) -> None:
        for value in (100.5, -1.0, float("nan")):
            with pytest.raises(ua.uaerrors.BadOutOfRange):
                await r.node(POWER_SETPOINT_PCT).write_value(ua.DataValue(ua.Variant(value, ua.VariantType.Double)))
            assert r.plc.power_setpoint_pct == 100.0
            assert await r.node(POWER_SETPOINT_PCT).read_value() == 100.0

    run_rig(scenario)


def test_setpoint_wrong_type_rejected() -> None:
    async def scenario(r: Rig) -> None:
        with pytest.raises(ua.uaerrors.BadTypeMismatch):
            await r.node(POWER_SETPOINT_PCT).write_value(ua.DataValue(ua.Variant(50, ua.VariantType.Int32)))
        assert r.plc.power_setpoint_pct == 100.0

    run_rig(scenario)


def test_modbus_setpoint_write_appears_in_opcua_after_scan() -> None:
    async def scenario(r: Rig) -> None:
        await r.mb.write_register(0, 250, device_id=MODBUS_DEVICE_ID)
        await r.scans(1)
        assert await r.node(POWER_SETPOINT_PCT).read_value() == 25.0

    run_rig(scenario)


def test_read_only_nodes_refuse_writes() -> None:
    async def scenario(r: Rig) -> None:
        for var in OPCUA_VARIABLES:
            if var.writable:
                continue
            node = r.node(var.node_id)
            current = await node.read_value()
            with pytest.raises(ua.uaerrors.BadUserAccessDenied):
                await node.write_value(ua.DataValue(ua.Variant(current, _VARIANT[var.data_type])))
        assert not r.plc.fault_active

    run_rig(scenario)


# --- methods -------------------------------------------------------------------------------------

def test_trigger_and_clear_fault_methods() -> None:
    async def scenario(r: Rig) -> None:
        vault = await r.ua.nodes.objects.get_child([f"{r.ns}:{p}" for p in VAULT_FOLDER])
        level = await vault.call_method(f"{r.ns}:{TRIGGER_FAULT_METHOD}")
        assert 25.0 <= level <= 80.0
        assert r.plc.fault_active
        assert await r.node(FAULT_TRIGGER).read_value() is True
        assert (await r.mb.read_coils(0, count=1, device_id=MODBUS_DEVICE_ID)).bits[0] is True
        assert await vault.call_method(f"{r.ns}:{TRIGGER_FAULT_METHOD}") == level  # idempotent

        await r.scans(10)
        assert await r.node("VAULT_RAD_USVH").read_value() >= 20.0
        assert await r.node("CONTROL_ROOM_RAD_USVH").read_value() < 0.3

        await vault.call_method(f"{r.ns}:{CLEAR_FAULT_METHOD}")
        assert not r.plc.fault_active
        assert await r.node(FAULT_TRIGGER).read_value() is False

    run_rig(scenario)
