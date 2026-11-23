# Citadel Tag Map — Virtual PLC (PC #2, Purdue L1)

> Source of truth in code: `simulators/virtual_plc/tag_map.py`. A pytest (`test_tag_map_doc.py`) fails if this table drifts from the code.
> Tag names are identical across the simulator, Modbus, OPC UA (US-203), MQTT topics and DB columns.

## Connection

| Item | Value |
|------|-------|
| Protocol | Modbus TCP |
| Endpoint | `citadel-plant` 10.10.0.10 **:502** (host port) → container port 5020 |
| Unit / device ID | **1** (other IDs get exception 04) |
| Addressing | 0-based protocol addresses (what goes on the wire). Modicon references (3xxxx/4xxxx/0xxxx) are 1-based. |
| Encoding | Signed INT16, big-endian, one register per tag: `raw = round(value × scale)`, saturated to −32768…32767; `value = raw ÷ scale` |
| Consistency | All input registers are latched together once per scan (default 1 s, `SIM_TICK_SECONDS`), so one FC04 read of 0–6 returns a single consistent tick |
| Allowed client | PC #3 `citadel-hmi` 10.10.0.20 only (PC #2 firewall, see `network.md`) |

## Register Map

| Tag | Unit | Table | FC | Address | Modicon | Scale | Access | Range / meaning |
|-----|------|-------|----|---------|---------|-------|--------|-----------------|
| `REACTOR_POWER_PCT` | % | Input register | 04 | 0 | 30001 | 10 | R | 0–120 % |
| `CORE_TEMP_C` | °C | Input register | 04 | 1 | 30002 | 10 | R | 0–1500 °C |
| `HE_LOOP_PRESS_BAR` | bar | Input register | 04 | 2 | 30003 | 100 | R | 0–100 bar |
| `HE_FLOW_KGS` | kg/s | Input register | 04 | 3 | 30004 | 10 | R | 0–120 kg/s |
| `VAULT_RAD_USVH` | µSv/h | Input register | 04 | 4 | 30005 | 100 | R | 0–100 µSv/h (normal ~1.5, fault 25–80) |
| `CONTROL_ROOM_RAD_USVH` | µSv/h | Input register | 04 | 5 | 30006 | 100 | R | 0–10 µSv/h (normal ~0.20) |
| `PLC_HEARTBEAT_COUNT` | count | Input register | 04 | 6 | 30007 | 1 | R | Scan counter, wraps 32767 → 0. Stale data detection: alarm if unchanged for 3 scans |
| `POWER_SETPOINT_PCT` | % | Holding register | 03 / 06 / 16 | 0 | 40001 | 10 | R/W | 0–1000 raw (0–100 %); out of range → exception 03 |
| `FAULT_TRIGGER` | — | Coil | 01 / 05 / 15 | 0 | 00001 | — | R/W | Write 1 = start vault radiation fault, 0 = clear. Reads back the actual fault state |

Unmapped addresses return exception 02 (Illegal Data Address). There are no discrete inputs (FC02 → exception 02). Multi-register or multi-coil writes (FC15/16) are all-or-nothing: if any value is rejected, none are applied.

**Example:** input register 1 = `7503` → `CORE_TEMP_C` = 750.3 °C. Holding register 0 ← `500` → power setpoint 50.0 %.

## OPC UA

Node IDs for the same tags (`urn:citadel:plant`) are added in US-203, as extra columns in the table above.

## Security Note

Modbus TCP has **no authentication or encryption**: anyone who can reach port 502 can write the power setpoint and trigger a radiation fault. In this lab, the only control on those writable points is network-level: PC #2's firewall admits only PC #3 (10.10.0.20), PC #4 is blocked from PC #2 at both ends, and the OT LAN is air-gapped. That is the classic L1 posture, and it is why the L3.5 boundary matters. Mitigations we have not implemented here (and the lab does not claim): Modbus/TCP Security (TLS, port 802), a protocol-aware OT firewall or DPI that allows only FC03/04 from the gateway, and a read-only gateway path. See the compliance guide gap register.
