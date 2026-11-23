"""Run the Citadel Virtual PLC: ``python -m simulators.virtual_plc``.

Environment (all optional):
  PLC_BIND_HOST    interface to listen on            (default 0.0.0.0, i.e. inside the container)
  PLC_MODBUS_PORT  Modbus TCP port                   (default 5020; compose publishes it as 502)
  PLC_OPCUA_PORT   OPC UA port                       (default 4840)
  SIM_SEED         integer seed for repeatable output (default: random)
  SIM_TICK_SECONDS scan period in seconds            (default 1.0)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import signal
from collections.abc import Mapping
from dataclasses import dataclass

from simulators.citadel_reactor import CitadelReactor
from simulators.virtual_plc.modbus_server import ModbusPlcServer
from simulators.virtual_plc.opcua_server import OpcUaPlcServer
from simulators.virtual_plc.plc import VirtualPlc
from simulators.virtual_plc.runtime import scan_loop

log = logging.getLogger("citadel.plc")

DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_MODBUS_PORT = 5020
DEFAULT_OPCUA_PORT = 4840
DEFAULT_TICK_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class PlcConfig:
    bind_host: str = DEFAULT_BIND_HOST
    modbus_port: int = DEFAULT_MODBUS_PORT
    opcua_port: int = DEFAULT_OPCUA_PORT
    seed: int | None = None
    tick_seconds: float = DEFAULT_TICK_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> PlcConfig:
        seed_raw = env.get("SIM_SEED", "").strip()
        config = cls(
            bind_host=env.get("PLC_BIND_HOST", DEFAULT_BIND_HOST),
            modbus_port=int(env.get("PLC_MODBUS_PORT", DEFAULT_MODBUS_PORT)),
            opcua_port=int(env.get("PLC_OPCUA_PORT", DEFAULT_OPCUA_PORT)),
            seed=int(seed_raw) if seed_raw else None,
            tick_seconds=float(env.get("SIM_TICK_SECONDS", DEFAULT_TICK_SECONDS)),
        )
        for name, port in (("PLC_MODBUS_PORT", config.modbus_port), ("PLC_OPCUA_PORT", config.opcua_port)):
            if not 1 <= port <= 65535:
                raise ValueError(f"{name} {port} outside 1-65535")
        if config.modbus_port == config.opcua_port:
            raise ValueError("PLC_MODBUS_PORT and PLC_OPCUA_PORT must differ")
        if config.tick_seconds <= 0:
            raise ValueError("SIM_TICK_SECONDS must be > 0")
        return config


async def run(config: PlcConfig, stop: asyncio.Event) -> None:
    # `docker stop` sends SIGTERM; shut down cleanly. (Signal handlers are unsupported on Windows.)
    with contextlib.suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    plc = VirtualPlc(CitadelReactor(rng=random.Random(config.seed)))
    modbus = ModbusPlcServer(plc, config.bind_host, config.modbus_port)
    opcua = OpcUaPlcServer(plc, config.bind_host, config.opcua_port)
    await modbus.start()
    try:
        await opcua.start()
        try:
            log.info("Virtual PLC up: Modbus TCP %s:%d, OPC UA %s, scan %.3f s, seed %s",
                     config.bind_host, modbus.port, opcua.endpoint, config.tick_seconds, config.seed)
            await scan_loop(plc, config.tick_seconds, stop, on_scan=[opcua.publish])
        finally:
            await opcua.stop()
    finally:
        await modbus.stop()
        log.info("Virtual PLC stopped")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pymodbus").setLevel(logging.WARNING)
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    config = PlcConfig.from_env(os.environ)
    try:
        asyncio.run(run(config, asyncio.Event()))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
