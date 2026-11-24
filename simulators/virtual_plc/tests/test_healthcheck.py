from __future__ import annotations

import socket

from simulators.virtual_plc.healthcheck import check, main, ports_from_env


class _FakeSocket:
    def close(self) -> None:
        pass


def test_ports_default_and_override() -> None:
    assert ports_from_env({}) == [5020, 4840]
    assert ports_from_env({"PLC_MODBUS_PORT": "1502", "PLC_OPCUA_PORT": "14840"}) == [1502, 14840]


def test_healthy_when_all_ports_accept() -> None:
    seen: list[tuple[str, int]] = []

    def connect(addr: tuple[str, int], timeout: float) -> socket.socket:
        seen.append(addr)
        return _FakeSocket()  # type: ignore[return-value]

    assert check("127.0.0.1", [5020, 4840], connect=connect) == []
    assert seen == [("127.0.0.1", 5020), ("127.0.0.1", 4840)]


def test_reports_each_closed_port() -> None:
    def connect(addr: tuple[str, int], timeout: float) -> socket.socket:
        if addr[1] == 4840:
            raise ConnectionRefusedError("refused")
        return _FakeSocket()  # type: ignore[return-value]

    failures = check("127.0.0.1", [5020, 4840], connect=connect)
    assert len(failures) == 1 and "4840" in failures[0]


def test_main_against_real_listener() -> None:
    with socket.socket() as a, socket.socket() as b:
        for s in (a, b):
            s.bind(("127.0.0.1", 0))
            s.listen()
        env = {"PLC_MODBUS_PORT": str(a.getsockname()[1]), "PLC_OPCUA_PORT": str(b.getsockname()[1])}
        assert main(env) == 0
    assert main(env) == 1  # sockets closed -> unhealthy
