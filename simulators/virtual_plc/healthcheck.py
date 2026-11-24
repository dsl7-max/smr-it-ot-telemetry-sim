"""Container HEALTHCHECK: exit 0 only if both PLC listeners accept TCP connections.

``python -m simulators.virtual_plc.healthcheck`` - reads PLC_MODBUS_PORT / PLC_OPCUA_PORT (defaults 5020 / 4840).
A TCP accept proves the asyncio loop is alive and both servers are bound; protocol-level checks
belong to the probe (US-205).
"""

from __future__ import annotations

import os
import socket
import sys
from collections.abc import Callable, Mapping, Sequence

Connector = Callable[[tuple[str, int], float], socket.socket]

DEFAULT_PORTS = {"PLC_MODBUS_PORT": 5020, "PLC_OPCUA_PORT": 4840}


def ports_from_env(env: Mapping[str, str]) -> list[int]:
    return [int(env.get(name, default)) for name, default in DEFAULT_PORTS.items()]


def check(
    host: str,
    ports: Sequence[int],
    timeout: float = 2.0,
    connect: Connector = socket.create_connection,
) -> list[str]:
    """Return a list of failures (empty = healthy)."""
    failures: list[str] = []
    for port in ports:
        try:
            connect((host, port), timeout).close()
        except OSError as exc:
            failures.append(f"{host}:{port} {exc}")
    return failures


def main(env: Mapping[str, str] | None = None) -> int:
    failures = check("127.0.0.1", ports_from_env(os.environ if env is None else env))
    for failure in failures:
        print(f"unhealthy: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
