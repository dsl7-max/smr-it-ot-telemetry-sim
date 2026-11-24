"""plant_probe: pure helpers + integration against a live in-process PLC (real scan loop, both servers)."""

from __future__ import annotations

import asyncio
import random
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from asyncua import Client, ua

from simulators import plant_probe as pp
from simulators.citadel_reactor import CitadelReactor
from simulators.virtual_plc.modbus_server import ModbusPlcServer
from simulators.virtual_plc.opcua_server import OpcUaPlcServer
from simulators.virtual_plc.plc import VirtualPlc
from simulators.virtual_plc.runtime import scan_loop
from simulators.virtual_plc.tag_map import (
    FAULT_TRIGGER,
    INPUT_REGISTERS,
    OPCUA_NAMESPACE_URI,
    PLC_HEARTBEAT_COUNT,
    encode_int16,
    point_by_tag,
)

TICK_S = 0.1  # fast scans make scan-boundary races likely, which is what alignment must survive
PLANT_TAGS = [p.tag for p in INPUT_REGISTERS if p.tag != PLC_HEARTBEAT_COUNT]


def _snap(protocol: str, fault: bool = False, **overrides: float) -> pp.Snapshot:
    values = {p.tag: 1.0 for p in INPUT_REGISTERS}
    values[PLC_HEARTBEAT_COUNT] = 7.0
    values.update(overrides)
    return pp.Snapshot(protocol, values, fault)


# --- pure helpers ------------------------------------------------------------------------------

def test_probe_covers_all_seven_input_tags_plus_fault() -> None:
    assert len(pp.INPUT_TAGS) == 7
    assert pp.PROBE_TAGS == (*pp.INPUT_TAGS, FAULT_TRIGGER)
    assert pp.INPUT_TAGS[-1] == PLC_HEARTBEAT_COUNT


def test_decode_input_registers_round_trip() -> None:
    values = {p.tag: (12.34 if p.tag != PLC_HEARTBEAT_COUNT else 99.0) for p in INPUT_REGISTERS}
    regs = [0] * len(INPUT_REGISTERS)
    for p in INPUT_REGISTERS:
        regs[p.address] = encode_int16(values[p.tag], p.scale)
    decoded = pp.decode_input_registers(regs)
    for p in INPUT_REGISTERS:
        assert decoded[p.tag] == pytest.approx(values[p.tag], abs=pp.tolerance(p))


def test_decode_input_registers_rejects_wrong_length() -> None:
    with pytest.raises(pp.ProbeError):
        pp.decode_input_registers([0, 0])


@pytest.mark.parametrize("tag", PLANT_TAGS)
def test_compare_tolerates_quantization_but_flags_real_drift(tag: str) -> None:
    half_step = 0.5 / point_by_tag(INPUT_REGISTERS, tag).scale
    within = pp.compare(_snap("modbus"), _snap("opcua", **{tag: 1.0 + half_step * 0.99}))
    assert all(r.ok for r in within)
    beyond = pp.compare(_snap("modbus"), _snap("opcua", **{tag: 1.0 + half_step * 1.5}))
    assert [r.tag for r in beyond if r.ok is False] == [tag]
    assert pp.verdict(beyond) == pp.EXIT_MISMATCH


def test_compare_flags_fault_disagreement() -> None:
    rows = pp.compare(_snap("modbus", fault=True), _snap("opcua", fault=False))
    assert [r.tag for r in rows if r.ok is False] == [FAULT_TRIGGER]


def test_compare_single_protocol_has_nothing_to_judge() -> None:
    rows = pp.compare(_snap("modbus"), None)
    assert all(r.ok is None and r.opcua is None for r in rows)
    assert pp.verdict(rows) == pp.EXIT_OK
    with pytest.raises(ValueError):
        pp.compare(None, None)


def test_scan_aligned() -> None:
    t0 = datetime(2026, 11, 23, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=1)
    assert pp.scan_aligned(5, 5, [t0] * 7)
    assert not pp.scan_aligned(5, 6, [t0] * 7)          # different scans across protocols
    assert not pp.scan_aligned(5, 5, [t0] * 6 + [t1])   # OPC UA read straddled a publish
    assert not pp.scan_aligned(5, 5, [None] * 7)        # no scan stamp at all


def test_format_table_lists_every_tag() -> None:
    text = pp.format_table(pp.compare(_snap("modbus", fault=True), _snap("opcua", fault=True)), "HDR")
    lines = text.splitlines()
    assert lines[0] == "HDR"
    assert lines[1].split() == ["TAG", "UNIT", "MODBUS", "OPC", "UA", "MATCH"]
    for tag in pp.PROBE_TAGS:
        assert any(line.startswith(tag + " ") for line in lines), tag
    fault_line = next(line for line in lines if line.startswith(FAULT_TRIGGER))
    assert fault_line.split()[2:] == ["ON", "ON", "yes"]
    hb_line = next(line for line in lines if line.startswith(PLC_HEARTBEAT_COUNT))
    assert hb_line.split()[2:4] == ["7", "7"]


def test_ascii_units() -> None:
    assert pp.ascii_unit("°C") == "degC"
    assert pp.ascii_unit("µSv/h") == "uSv/h"
    assert pp.ascii_unit("kg/s") == "kg/s"
    assert pp.ascii_unit("éx").isascii()


def test_parse_args_defaults_and_env() -> None:
    cfg = pp.parse_args([], {})
    assert (cfg.host, cfg.modbus_port, cfg.opcua_port) == ("localhost", 502, 4840)
    assert cfg.use_modbus and cfg.use_opcua and cfg.fault_action is None
    assert pp.parse_args([], {"PLANT_HOST": "10.10.0.10"}).host == "10.10.0.10"
    assert pp.parse_args(["--host", "h"], {"PLANT_HOST": "x"}).host == "h"


def test_parse_args_protocol_and_fault_flags() -> None:
    only_mb = pp.parse_args(["--modbus"], {})
    assert only_mb.use_modbus and not only_mb.use_opcua
    only_ua = pp.parse_args(["--opcua"], {})
    assert only_ua.use_opcua and not only_ua.use_modbus
    assert pp.parse_args(["--trigger-fault"], {}).fault_action is True
    assert pp.parse_args(["--clear-fault"], {}).fault_action is False
    with pytest.raises(SystemExit):
        pp.parse_args(["--trigger-fault", "--clear-fault"], {})
    with pytest.raises(SystemExit):
        pp.parse_args(["--attempts", "0"], {})


# --- integration: live PLC, real scan loop, both protocol servers -----------------------------

@dataclass
class LivePlant:
    plc: VirtualPlc
    modbus_port: int
    opcua_port: int
    endpoint: str

    def config(self, **kw: object) -> pp.ProbeConfig:
        return pp.ProbeConfig(host="127.0.0.1", modbus_port=self.modbus_port, opcua_port=self.opcua_port,
                              timeout_s=3.0, **kw)  # type: ignore[arg-type]


@asynccontextmanager
async def live_plant(seed: int = 11) -> AsyncIterator[LivePlant]:
    plc = VirtualPlc(CitadelReactor(rng=random.Random(seed)))
    modbus = ModbusPlcServer(plc, "127.0.0.1", 0)
    opcua = OpcUaPlcServer(plc, "127.0.0.1", 0)
    await modbus.start()
    await opcua.start()
    stop = asyncio.Event()
    loop_task = asyncio.create_task(scan_loop(plc, TICK_S, stop, on_scan=[opcua.publish]))
    try:
        await asyncio.sleep(3 * TICK_S)
        yield LivePlant(plc, modbus.port, opcua.port, opcua.endpoint)
    finally:
        stop.set()
        await loop_task
        await opcua.stop()
        await modbus.stop()


def run(scenario: Callable[[LivePlant], Awaitable[None]]) -> None:
    async def main() -> None:
        async with live_plant() as plant:
            await scenario(plant)

    asyncio.run(asyncio.wait_for(main(), timeout=60))


def test_probe_both_protocols_agree_on_same_scan() -> None:
    async def scenario(plant: LivePlant) -> None:
        code, report = await pp.probe(plant.config())
        assert code == pp.EXIT_OK, report
        assert "same scan, heartbeat" in report
        assert report.rstrip().endswith("RESULT: PASS")
        for tag in pp.PROBE_TAGS:
            assert tag in report
        assert " NO" not in report

    run(scenario)


def test_probe_stays_aligned_under_fast_scans_and_an_active_subscription() -> None:
    """With a live OPC UA subscription the server's per-scan publish awaits data-change callbacks,
    so a read can land mid-update; the SourceTimestamp check must reject such torn reads."""

    class Handler:
        def __init__(self) -> None:
            self.changes = 0

        def datachange_notification(self, node: object, val: object, data: object) -> None:
            self.changes += 1

    async def scenario(plant: LivePlant) -> None:
        handler = Handler()
        async with Client(plant.endpoint, timeout=5) as sub_client:
            ns = await sub_client.get_namespace_index(OPCUA_NAMESPACE_URI)
            sub = await sub_client.create_subscription(20, handler)
            await sub.subscribe_data_change([sub_client.get_node(ua.NodeId(t, ns)) for t in pp.INPUT_TAGS])
            for _ in range(8):
                code, report = await pp.probe(plant.config())
                assert code == pp.EXIT_OK, report
            assert handler.changes > 0

    run(scenario)


def test_probe_trigger_and_clear_fault_over_opcua() -> None:
    async def scenario(plant: LivePlant) -> None:
        code, report = await pp.probe(plant.config(fault_action=True))
        assert code == pp.EXIT_OK, report
        assert "fault triggered via OPC UA (level" in report
        assert plant.plc.fault_active
        fault_line = next(line for line in report.splitlines() if line.startswith(FAULT_TRIGGER))
        assert fault_line.split()[2:] == ["ON", "ON", "yes"]

        await asyncio.sleep(30 * TICK_S)  # 3 s simulated: vault ramps toward the fault level
        code, report = await pp.probe(plant.config())
        assert code == pp.EXIT_OK, report
        vault = plant.plc.image.values["VAULT_RAD_USVH"]
        assert vault > 5.0
        assert plant.plc.image.values["CONTROL_ROOM_RAD_USVH"] < 0.3  # shielding holds

        code, report = await pp.probe(plant.config(fault_action=False))
        assert code == pp.EXIT_OK, report
        assert "fault cleared via OPC UA" in report
        assert not plant.plc.fault_active

    run(scenario)


def test_probe_modbus_only_can_trigger_fault_via_coil() -> None:
    async def scenario(plant: LivePlant) -> None:
        code, report = await pp.probe(plant.config(use_opcua=False, fault_action=True))
        assert code == pp.EXIT_OK, report
        assert "via Modbus coil 0" in report
        assert "single protocol" in report
        assert plant.plc.fault_active

    run(scenario)


def test_probe_opcua_only() -> None:
    async def scenario(plant: LivePlant) -> None:
        code, report = await pp.probe(plant.config(use_modbus=False))
        assert code == pp.EXIT_OK, report
        assert "[OPC UA :" in report and "Modbus TCP" not in report

    run(scenario)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_probe_reports_unreachable_plant() -> None:
    cfg = pp.ProbeConfig(host="127.0.0.1", modbus_port=_free_port(), opcua_port=_free_port(), timeout_s=1.0)
    code, report = asyncio.run(asyncio.wait_for(pp.probe(cfg), timeout=30))
    assert code == pp.EXIT_ERROR
    assert "unreachable" in report


def test_cli_main_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    """`python -m simulators.plant_probe` path: argv parsing, report on stdout, exit code."""

    async def scenario(plant: LivePlant) -> None:
        argv = ["--host", "127.0.0.1", "--modbus-port", str(plant.modbus_port),
                "--opcua-port", str(plant.opcua_port)]
        # main() owns its own event loop (asyncio.run), so drive it from a worker thread.
        assert await asyncio.to_thread(pp.main, argv) == pp.EXIT_OK

    run(scenario)
    out = capsys.readouterr().out
    assert "RESULT: PASS" in out and "Citadel plant @ 127.0.0.1" in out
