"""OPC UA front end for the Virtual PLC (asyncua).

Address space (namespace ``urn:citadel:plant``) is built from ``tag_map.OPCUA_VARIABLES``. After every
PLC scan, ``publish()`` writes the latched ProcessImage into the nodes (so subscriptions fire and
values match Modbus for the same scan). Writes to ``POWER_SETPOINT_PCT`` go through a value setter
that validates and applies them to the PLC, answering ``BadOutOfRange`` on rejection.

MVP security: SecurityPolicy None + anonymous (PM decision 3; Basic256Sha256 in US-503).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from asyncua import Node, Server, ua
from asyncua.server.address_space import NodeData

from simulators.citadel_reactor import DEFAULT_TAGS
from simulators.virtual_plc.plc import IllegalValueError, ProcessImage, VirtualPlc
from simulators.virtual_plc.tag_map import (
    CLEAR_FAULT_METHOD,
    FAULT_TRIGGER,
    OPCUA_BOOLEAN,
    OPCUA_DOUBLE,
    OPCUA_ENDPOINT_PATH,
    OPCUA_INT32,
    OPCUA_METHODS,
    OPCUA_NAMESPACE_URI,
    OPCUA_VARIABLES,
    PLC_HEARTBEAT_COUNT,
    POWER_SETPOINT_PCT,
    TRIGGER_FAULT_METHOD,
)

log = logging.getLogger("citadel.plc.opcua")

APPLICATION_URI = "urn:citadel:plant:virtual-plc"
SERVER_NAME = "Citadel Virtual PLC"

_VARIANT_TYPES: dict[str, ua.VariantType] = {
    OPCUA_DOUBLE: ua.VariantType.Double,
    OPCUA_INT32: ua.VariantType.Int32,
    OPCUA_BOOLEAN: ua.VariantType.Boolean,
}

# Engineering units: OPC UA Part 8 uses UN/CEFACT Rec. 20 codes; UnitId packs the code's ASCII bytes.
UNECE_NAMESPACE = "http://www.opcfoundation.org/UA/units/un/cefact"
_UNECE_CODES: dict[str, str] = {"%": "P1", "°C": "CEL", "bar": "BAR", "kg/s": "KGS"}
_UNITS: dict[str, str] = {spec.name: spec.unit for spec in DEFAULT_TAGS} | {POWER_SETPOINT_PCT: "%"}
_RANGES: dict[str, tuple[float, float]] = {spec.name: (spec.span_low, spec.span_high) for spec in DEFAULT_TAGS} | {
    POWER_SETPOINT_PCT: (0.0, 100.0)
}


def unece_unit_id(code: str) -> int:
    """UN/CEFACT common code -> OPC UA EUInformation.UnitId (e.g. 'CEL' -> 4408652)."""
    unit_id = 0
    for char in code:
        unit_id = (unit_id << 8) | ord(char)
    return unit_id


def eu_information(unit: str) -> ua.EUInformation:
    code = _UNECE_CODES.get(unit)
    return ua.EUInformation(
        NamespaceUri=UNECE_NAMESPACE,
        UnitId=unece_unit_id(code) if code else -1,  # -1 = no standard code (e.g. uSv/h)
        DisplayName=ua.LocalizedText(unit),
        Description=ua.LocalizedText(unit),
    )


class OpcUaPlcServer:
    """OPC UA server for one VirtualPlc. ``port=0`` binds an ephemeral port (tests)."""

    def __init__(self, plc: VirtualPlc, host: str, port: int) -> None:
        self._plc = plc
        self._host = host
        self._requested_port = port
        self._server = Server()
        self._ns = 0
        self._nodes: dict[str, Node] = {}
        self._port: int | None = None

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("Server not started")
        return self._port

    @property
    def namespace_index(self) -> int:
        return self._ns

    @property
    def endpoint(self) -> str:
        return f"opc.tcp://{self._host}:{self.port}{OPCUA_ENDPOINT_PATH}"

    async def start(self) -> int:
        server = self._server
        await server.init()
        server.set_endpoint(f"opc.tcp://{self._host}:{self._requested_port}{OPCUA_ENDPOINT_PATH}")
        server.set_server_name(SERVER_NAME)
        await server.set_application_uri(APPLICATION_URI)
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self._ns = await server.register_namespace(OPCUA_NAMESPACE_URI)
        await self._build_address_space()
        await server.start()
        self._port = int(server.bserver._server.sockets[0].getsockname()[1])  # noqa: SLF001 - no public accessor
        await self.publish(self._plc.image)
        return self._port

    async def stop(self) -> None:
        await self._server.stop()

    # --- address space ---------------------------------------------------------------------

    def _qname(self, name: str) -> ua.QualifiedName:
        return ua.QualifiedName(name, self._ns)

    async def _folder(self, path: tuple[str, ...], cache: dict[tuple[str, ...], Node]) -> Node:
        if path in cache:
            return cache[path]
        parent = self._server.nodes.objects if len(path) == 1 else await self._folder(path[:-1], cache)
        node = await parent.add_object(ua.NodeId(".".join(path), self._ns), self._qname(path[-1]))
        cache[path] = node
        return node

    async def _build_address_space(self) -> None:
        folders: dict[tuple[str, ...], Node] = {}
        for var in OPCUA_VARIABLES:
            parent = await self._folder(var.folder, folders)
            variant_type = _VARIANT_TYPES[var.data_type]
            initial = {OPCUA_DOUBLE: 0.0, OPCUA_INT32: 0, OPCUA_BOOLEAN: False}[var.data_type]
            node = await parent.add_variable(
                ua.NodeId(var.node_id, self._ns), self._qname(var.tag), initial, varianttype=variant_type
            )
            if var.tag in _UNITS:
                await node.add_property(self._ns, "EngineeringUnits", eu_information(_UNITS[var.tag]))
                low, high = _RANGES[var.tag]
                await node.add_property(self._ns, "EURange", ua.Range(Low=low, High=high))
            if var.writable:
                await node.set_writable()
            self._nodes[var.tag] = node

        self._server.set_attribute_value_setter(self._nodes[POWER_SETPOINT_PCT].nodeid, self._write_setpoint)

        handlers = {TRIGGER_FAULT_METHOD: self._trigger_fault, CLEAR_FAULT_METHOD: self._clear_fault}
        outputs = {TRIGGER_FAULT_METHOD: [ua.VariantType.Double], CLEAR_FAULT_METHOD: []}
        for name, folder in OPCUA_METHODS:
            parent = await self._folder(folder, folders)
            await parent.add_method(ua.NodeId(name, self._ns), self._qname(name), handlers[name], [], outputs[name])

    # --- writes and methods ------------------------------------------------------------------

    def _write_setpoint(self, node: NodeData, attr: ua.AttributeIds, value: ua.DataValue) -> None:
        """Value setter: validate, apply to the PLC, then store. Raising returns the code to the client."""
        if value.Value is None or not isinstance(value.Value.Value, float):
            raise ua.UaStatusCodeError(ua.StatusCodes.BadTypeMismatch)
        try:
            self._plc.set_power_setpoint(value.Value.Value)
        except IllegalValueError as exc:
            log.warning("Rejected OPC UA write: %s", exc)
            raise ua.UaStatusCodeError(ua.StatusCodes.BadOutOfRange) from exc
        node.attributes[attr].value = value

    async def _trigger_fault(self, parent: ua.NodeId) -> list[ua.Variant]:
        level = self._plc.trigger_fault()
        await self._write(FAULT_TRIGGER, True, datetime.now(timezone.utc))
        return [ua.Variant(level, ua.VariantType.Double)]

    async def _clear_fault(self, parent: ua.NodeId) -> list[ua.Variant]:
        self._plc.clear_fault()
        await self._write(FAULT_TRIGGER, False, datetime.now(timezone.utc))
        return []

    # --- publishing --------------------------------------------------------------------------

    async def _write(self, tag: str, value: float | int | bool, source_ts: datetime) -> None:
        var_type = _VARIANT_TYPES[next(v.data_type for v in OPCUA_VARIABLES if v.tag == tag)]
        await self._server.write_attribute_value(
            self._nodes[tag].nodeid,
            ua.DataValue(ua.Variant(value, var_type), SourceTimestamp=source_ts, ServerTimestamp=datetime.now(timezone.utc)),
        )

    async def publish(self, image: ProcessImage) -> None:
        """Copy one latched scan into the address space (call after every PLC scan)."""
        for tag, value in image.values.items():
            if tag in self._nodes:
                await self._write(tag, float(value), image.timestamp)
        await self._write(PLC_HEARTBEAT_COUNT, image.heartbeat, image.timestamp)
        await self._write(POWER_SETPOINT_PCT, self._plc.power_setpoint_pct, image.timestamp)
        await self._write(FAULT_TRIGGER, self._plc.fault_active, image.timestamp)
