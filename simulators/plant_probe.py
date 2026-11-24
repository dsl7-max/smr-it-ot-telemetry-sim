"""Dual-protocol plant probe: read the Virtual PLC over Modbus TCP and OPC UA and compare them.

    python -m simulators.plant_probe --host localhost
    python -m simulators.plant_probe --host 10.10.0.10 --trigger-fault
    python -m simulators.plant_probe --host 10.10.0.10 --modbus          # one protocol only

Prints all 7 input tags (6 plant tags + PLC_HEARTBEAT_COUNT) and FAULT_TRIGGER side by side.
"Same scan" is proven, not assumed: the Modbus block is one latched image, and the OPC UA values
are accepted only when they all carry the same SourceTimestamp (stamped per scan by the PLC) and the
OPC UA heartbeat equals the Modbus heartbeat. Otherwise the probe re-reads (scan boundary race).

Exit codes: 0 = protocols agree, 1 = mismatch or no aligned scan, 2 = connection/protocol error.
Read-only unless --trigger-fault / --clear-fault is given.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from asyncua import Client, ua
from pymodbus.client import AsyncModbusTcpClient

from simulators.virtual_plc.tag_map import (
    CLEAR_FAULT_METHOD,
    COILS,
    FAULT_TRIGGER,
    INPUT_REGISTERS,
    MODBUS_DEVICE_ID,
    OPCUA_ENDPOINT_PATH,
    OPCUA_NAMESPACE_URI,
    PLC_HEARTBEAT_COUNT,
    TRIGGER_FAULT_METHOD,
    VAULT_FOLDER,
    RegisterPoint,
    decode_int16,
    point_by_tag,
)

DEFAULT_HOST = "localhost"
DEFAULT_MODBUS_PORT = 502
DEFAULT_OPCUA_PORT = 4840
DEFAULT_ATTEMPTS = 10
RETRY_DELAY_S = 0.1
SESSION_TIMEOUT_MS = 60_000  # short-lived probe; below the server max, so no revision warning

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_ERROR = 2

INPUT_TAGS: tuple[str, ...] = tuple(p.tag for p in sorted(INPUT_REGISTERS, key=lambda p: p.address))
PROBE_TAGS: tuple[str, ...] = (*INPUT_TAGS, FAULT_TRIGGER)
FAULT_COIL = next(c for c in COILS if c.tag == FAULT_TRIGGER)


class ProbeError(Exception):
    """A protocol endpoint was unreachable or returned an error."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One protocol's view of the plant: engineering values per tag, plus the scan heartbeat."""

    protocol: str
    values: Mapping[str, float]
    fault: bool

    @property
    def heartbeat(self) -> int:
        return int(self.values[PLC_HEARTBEAT_COUNT])


@dataclass(frozen=True, slots=True)
class Row:
    tag: str
    unit: str
    modbus: float | bool | None
    opcua: float | bool | None
    ok: bool | None  # None = only one protocol read, nothing to compare


# --- pure helpers ------------------------------------------------------------------------------


def tolerance(point: RegisterPoint) -> float:
    """Max honest Modbus-vs-OPC UA difference for one scan: half an INT16 step (rounding), plus float slack."""
    return 0.5 / point.scale + 1e-9


def decode_input_registers(registers: Sequence[int]) -> dict[str, float]:
    if len(registers) != len(INPUT_REGISTERS):
        raise ProbeError(f"expected {len(INPUT_REGISTERS)} input registers, got {len(registers)}")
    return {p.tag: decode_int16(registers[p.address], p.scale) for p in INPUT_REGISTERS}


def scan_aligned(modbus_heartbeat: int, opc_heartbeat: int, opc_source_timestamps: Sequence[datetime | None]) -> bool:
    """True when the OPC UA values form one PLC scan and it is the same scan the Modbus image came from."""
    stamps = set(opc_source_timestamps)
    return len(stamps) == 1 and None not in stamps and modbus_heartbeat == opc_heartbeat


def compare(modbus: Snapshot | None, opcua: Snapshot | None) -> list[Row]:
    if modbus is None and opcua is None:
        raise ValueError("need at least one snapshot")
    rows: list[Row] = []
    for point in sorted(INPUT_REGISTERS, key=lambda p: p.address):
        mb = modbus.values[point.tag] if modbus else None
        oc = opcua.values[point.tag] if opcua else None
        ok = None if mb is None or oc is None else abs(mb - oc) <= tolerance(point)
        rows.append(Row(point.tag, point.unit, mb, oc, ok))
    mb_fault = modbus.fault if modbus else None
    oc_fault = opcua.fault if opcua else None
    rows.append(Row(FAULT_TRIGGER, "bool", mb_fault, oc_fault,
                    None if mb_fault is None or oc_fault is None else mb_fault == oc_fault))
    return rows


_ASCII_UNITS = {"°C": "degC", "µSv/h": "uSv/h"}


def ascii_unit(unit: str) -> str:
    """Console-safe unit text (Windows consoles often mangle degree and micro signs)."""
    return _ASCII_UNITS.get(unit, unit).encode("ascii", "replace").decode("ascii")


def _cell(value: float | bool | None, tag: str) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "ON" if value else "off"
    if tag == PLC_HEARTBEAT_COUNT:
        return str(int(value))
    return f"{value:.3f}"


def format_table(rows: Sequence[Row], header: str) -> str:
    table = [("TAG", "UNIT", "MODBUS", "OPC UA", "MATCH")]
    for r in rows:
        match = "-" if r.ok is None else ("yes" if r.ok else "NO")
        table.append((r.tag, ascii_unit(r.unit), _cell(r.modbus, r.tag), _cell(r.opcua, r.tag), match))
    widths = [max(len(line[i]) for line in table) for i in range(5)]
    aligns = ("<", "<", ">", ">", "^")
    lines = [header]
    for n, line in enumerate(table):
        lines.append("  ".join(f"{cell:{a}{w}}" for cell, a, w in zip(line, aligns, widths)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def verdict(rows: Sequence[Row]) -> int:
    return EXIT_MISMATCH if any(r.ok is False for r in rows) else EXIT_OK


# --- protocol I/O ------------------------------------------------------------------------------


async def read_modbus(client: AsyncModbusTcpClient) -> Snapshot:
    rr = await client.read_input_registers(0, count=len(INPUT_REGISTERS), device_id=MODBUS_DEVICE_ID)
    if rr.isError():
        raise ProbeError(f"Modbus FC04 read failed: {rr}")
    cr = await client.read_coils(FAULT_COIL.address, count=1, device_id=MODBUS_DEVICE_ID)
    if cr.isError():
        raise ProbeError(f"Modbus FC01 read failed: {cr}")
    return Snapshot("modbus", decode_input_registers(rr.registers), bool(cr.bits[0]))


async def read_opcua(client: Client, ns: int) -> tuple[Snapshot, list[datetime | None]]:
    """One Read request for all tags; returns the snapshot and the input tags' SourceTimestamps."""
    nodes = [client.get_node(ua.NodeId(tag, ns)) for tag in PROBE_TAGS]
    data_values = await client.read_attributes(nodes, ua.AttributeIds.Value)
    for tag, dv in zip(PROBE_TAGS, data_values):
        if dv.StatusCode is not None and not dv.StatusCode.is_good():
            raise ProbeError(f"OPC UA read of {tag} failed: {dv.StatusCode}")
    by_tag = dict(zip(PROBE_TAGS, data_values))
    values = {tag: float(by_tag[tag].Value.Value) for tag in INPUT_TAGS}
    stamps = [by_tag[tag].SourceTimestamp for tag in INPUT_TAGS]
    return Snapshot("opcua", values, bool(by_tag[FAULT_TRIGGER].Value.Value)), stamps


async def aligned_read(
    mb: AsyncModbusTcpClient | None, opc: Client | None, ns: int, attempts: int
) -> tuple[Snapshot | None, Snapshot | None, bool]:
    """Read until both protocols show the same PLC scan; returns (modbus, opcua, aligned)."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    modbus = opcua = None
    for attempt in range(attempts):
        modbus = await read_modbus(mb) if mb else None
        if opc is None:
            return modbus, None, True
        opcua, stamps = await read_opcua(opc, ns)
        if modbus is None:
            return None, opcua, len(set(stamps)) == 1
        if scan_aligned(modbus.heartbeat, opcua.heartbeat, stamps):
            return modbus, opcua, True
        if attempt + 1 < attempts:
            await asyncio.sleep(RETRY_DELAY_S)
    return modbus, opcua, False


async def set_fault(mb: AsyncModbusTcpClient | None, opc: Client | None, ns: int, active: bool) -> str:
    """Trigger/clear the vault fault: OPC UA method when available (reports the level), else the Modbus coil."""
    if opc is not None:
        vault = await opc.nodes.objects.get_child([f"{ns}:{name}" for name in VAULT_FOLDER])
        method = TRIGGER_FAULT_METHOD if active else CLEAR_FAULT_METHOD
        result = await vault.call_method(f"{ns}:{method}")
        return f"fault triggered via OPC UA (level {float(result):.1f} uSv/h)" if active else "fault cleared via OPC UA"
    assert mb is not None
    wr = await mb.write_coil(FAULT_COIL.address, active, device_id=MODBUS_DEVICE_ID)
    if wr.isError():
        raise ProbeError(f"Modbus FC05 write failed: {wr}")
    return f"fault {'triggered' if active else 'cleared'} via Modbus coil {FAULT_COIL.address}"


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    host: str = DEFAULT_HOST
    modbus_port: int = DEFAULT_MODBUS_PORT
    opcua_port: int = DEFAULT_OPCUA_PORT
    use_modbus: bool = True
    use_opcua: bool = True
    fault_action: bool | None = None  # True = trigger, False = clear, None = read-only
    attempts: int = DEFAULT_ATTEMPTS
    timeout_s: float = 5.0

    @property
    def opcua_endpoint(self) -> str:
        return f"opc.tcp://{self.host}:{self.opcua_port}{OPCUA_ENDPOINT_PATH}"


async def probe(config: ProbeConfig) -> tuple[int, str]:
    """Run one probe; returns (exit code, report text). Never raises for unreachable endpoints."""
    mb: AsyncModbusTcpClient | None = None
    opc: Client | None = None
    notes: list[str] = []
    try:
        if config.use_modbus:
            mb = AsyncModbusTcpClient(config.host, port=config.modbus_port, timeout=config.timeout_s, retries=1)
            if not await mb.connect():
                raise ProbeError(f"Modbus TCP {config.host}:{config.modbus_port} unreachable")
        ns = 0
        if config.use_opcua:
            opc = Client(config.opcua_endpoint, timeout=config.timeout_s)
            opc.session_timeout = SESSION_TIMEOUT_MS
            try:
                await opc.connect()
            except (OSError, asyncio.TimeoutError, ua.UaError) as exc:
                opc = None
                raise ProbeError(f"OPC UA {config.opcua_endpoint} unreachable: {exc!r}") from exc
            ns = await opc.get_namespace_index(OPCUA_NAMESPACE_URI)

        if config.fault_action is not None:
            notes.append(await set_fault(mb, opc, ns, config.fault_action))

        modbus, opcua, aligned = await aligned_read(mb, opc, ns, config.attempts)
        rows = compare(modbus, opcua)
        if modbus and opcua:
            scan = (f"same scan, heartbeat {modbus.heartbeat}" if aligned
                    else f"NOT ALIGNED after {config.attempts} reads "
                         f"(Modbus hb {modbus.heartbeat}, OPC UA hb {opcua.heartbeat})")
        else:
            scan = f"heartbeat {(modbus or opcua).heartbeat}"  # type: ignore[union-attr]
        protocols = " + ".join(p for p, on in (("Modbus TCP :%d" % config.modbus_port, config.use_modbus),
                                                ("OPC UA :%d" % config.opcua_port, config.use_opcua)) if on)
        header = f"Citadel plant @ {config.host}  [{protocols}]  {scan}"
        code = verdict(rows) if aligned else EXIT_MISMATCH
        summary = {EXIT_OK: "RESULT: PASS", EXIT_MISMATCH: "RESULT: FAIL (protocols disagree)"}[code]
        if not (modbus and opcua):
            summary = "RESULT: PASS (single protocol, no cross-check)"
        return code, "\n".join([*notes, format_table(rows, header), summary])
    except ProbeError as exc:
        return EXIT_ERROR, "\n".join([*notes, f"RESULT: ERROR {exc}"])
    except (ua.UaError, ConnectionError, asyncio.TimeoutError) as exc:
        return EXIT_ERROR, "\n".join([*notes, f"RESULT: ERROR {exc!r}"])
    finally:
        if opc is not None:
            await opc.disconnect()
        if mb is not None:
            mb.close()


# --- CLI ---------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None, env: Mapping[str, str]) -> ProbeConfig:
    parser = argparse.ArgumentParser(prog="python -m simulators.plant_probe", description=__doc__.split("\n")[0])
    parser.add_argument("--host", default=env.get("PLANT_HOST", DEFAULT_HOST),
                        help="plant PLC host (default: $PLANT_HOST or localhost)")
    parser.add_argument("--modbus-port", type=int, default=DEFAULT_MODBUS_PORT)
    parser.add_argument("--opcua-port", type=int, default=DEFAULT_OPCUA_PORT)
    parser.add_argument("--modbus", action="store_true", help="read Modbus only (default: both)")
    parser.add_argument("--opcua", action="store_true", help="read OPC UA only (default: both)")
    fault = parser.add_mutually_exclusive_group()
    fault.add_argument("--trigger-fault", action="store_true", help="start the vault radiation fault first")
    fault.add_argument("--clear-fault", action="store_true", help="clear the vault radiation fault first")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                        help="reads allowed to land both protocols on the same scan")
    parser.add_argument("--timeout", type=float, default=5.0, help="per-request timeout, seconds")
    args = parser.parse_args(argv)
    if args.attempts < 1:
        parser.error("--attempts must be >= 1")
    both = not (args.modbus or args.opcua)
    return ProbeConfig(
        host=args.host,
        modbus_port=args.modbus_port,
        opcua_port=args.opcua_port,
        use_modbus=both or args.modbus,
        use_opcua=both or args.opcua,
        fault_action=True if args.trigger_fault else (False if args.clear_fault else None),
        attempts=args.attempts,
        timeout_s=args.timeout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_args(argv, os.environ)
    code, report = asyncio.run(probe(config))
    print(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
