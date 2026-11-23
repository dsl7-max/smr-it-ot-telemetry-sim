"""PLC scan loop: step the plant, latch the image, then notify protocol front ends."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from simulators.virtual_plc.plc import ProcessImage, VirtualPlc

Sleeper = Callable[[float], Awaitable[None]]
ScanHook = Callable[[ProcessImage], Awaitable[None]]


async def scan_loop(
    plc: VirtualPlc,
    tick_seconds: float,
    stop: asyncio.Event,
    on_scan: Sequence[ScanHook] = (),
    sleep: Sleeper = asyncio.sleep,
) -> None:
    """Scan every ``tick_seconds`` (simulated dt = tick) until ``stop`` is set.

    Modbus reads ``plc.image`` directly; push-based front ends (OPC UA) get the new image via ``on_scan``.
    """
    if tick_seconds <= 0:
        raise ValueError("tick_seconds must be > 0")
    while not stop.is_set():
        await sleep(tick_seconds)
        image = plc.scan(tick_seconds)
        for hook in on_scan:
            await hook(image)
