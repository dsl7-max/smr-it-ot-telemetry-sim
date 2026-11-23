# Citadel Tag Map — Virtual PLC (PC #2, Purdue L1)

> Source of truth in code: `simulators/virtual_plc/tag_map.py`. A pytest (`simulators/virtual_plc/tests/test_tag_map.py`) fails if this table drifts from the code.
> Tag names are identical across the simulator, Modbus, OPC UA, MQTT topics and DB columns. Both protocols serve the same latched scan, so they always agree.

## Connection — Modbus TCP

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

| Tag | Unit | Modbus table | FC | Address | Modicon | Scale | Access | OPC UA NodeId | OPC UA type | Range / meaning |
|-----|------|--------------|----|---------|---------|-------|--------|---------------|-------------|-----------------|
| `REACTOR_POWER_PCT` | % | Input register | 04 | 0 | 30001 | 10 | R | `ns=2;s=REACTOR_POWER_PCT` | Double | 0–120 % |
| `CORE_TEMP_C` | °C | Input register | 04 | 1 | 30002 | 10 | R | `ns=2;s=CORE_TEMP_C` | Double | 0–1500 °C |
| `HE_LOOP_PRESS_BAR` | bar | Input register | 04 | 2 | 30003 | 100 | R | `ns=2;s=HE_LOOP_PRESS_BAR` | Double | 0–100 bar |
| `HE_FLOW_KGS` | kg/s | Input register | 04 | 3 | 30004 | 10 | R | `ns=2;s=HE_FLOW_KGS` | Double | 0–120 kg/s |
| `VAULT_RAD_USVH` | µSv/h | Input register | 04 | 4 | 30005 | 100 | R | `ns=2;s=VAULT_RAD_USVH` | Double | 0–100 µSv/h (normal ~1.5, fault 25–80) |
| `CONTROL_ROOM_RAD_USVH` | µSv/h | Input register | 04 | 5 | 30006 | 100 | R | `ns=2;s=CONTROL_ROOM_RAD_USVH` | Double | 0–10 µSv/h (normal ~0.20) |
| `PLC_HEARTBEAT_COUNT` | count | Input register | 04 | 6 | 30007 | 1 | R | `ns=2;s=PLC_HEARTBEAT_COUNT` | Int32 | Scan counter, wraps 32767 → 0. Stale data detection: alarm if unchanged for 3 scans |
| `POWER_SETPOINT_PCT` | % | Holding register | 03 / 06 / 16 | 0 | 40001 | 10 | R/W | `ns=2;s=POWER_SETPOINT_PCT` | Double | 0–1000 raw (0–100 %); out of range → exception 03 |
| `FAULT_TRIGGER` | — | Coil | 01 / 05 / 15 | 0 | 00001 | — | R/W | `ns=2;s=FAULT_TRIGGER` | Boolean | Write 1 = start vault radiation fault, 0 = clear. Reads back the actual fault state |

Unmapped addresses return exception 02 (Illegal Data Address). There are no discrete inputs (FC02 → exception 02). Multi-register or multi-coil writes (FC15/16) are all-or-nothing: if any value is rejected, none are applied.

**Example:** input register 1 = `7503` → `CORE_TEMP_C` = 750.3 °C. Holding register 0 ← `500` → power setpoint 50.0 %.

`FAULT_TRIGGER` in OPC UA is a read-only Boolean showing the actual fault state; command it with the methods below.

## Connection — OPC UA

| Item | Value |
|------|-------|
| Endpoint | `opc.tcp://10.10.0.10:4840/citadel/plant` (container port 4840, `PLC_OPCUA_PORT`) |
| Namespace | URI `urn:citadel:plant`, index **2** (resolve by URI in clients; `nsu=urn:citadel:plant;s=<TAG>` is the index-free form) |
| NodeIds | String identifier = tag name, e.g. `ns=2;s=CORE_TEMP_C` |
| Security | `SecurityPolicy None`, anonymous (MVP; `Basic256Sha256` Sign & Encrypt + certificates in US-503) |
| Values | Native types in engineering units, no scaling. Each analog node has `EngineeringUnits` (UN/CEFACT code where one exists; µSv/h has none, `UnitId = -1`) and `EURange` (instrument span) properties |
| Timestamps | `SourceTimestamp` = scan time of the latched image; nodes are updated after every scan, so subscriptions fire per scan |
| Write errors | Out-of-range or NaN setpoint → `BadOutOfRange`; wrong data type → `BadTypeMismatch`; write to read-only node → `BadUserAccessDenied`. Rejected writes are not applied |

**Address space**

```
Objects
└── Citadel                          ns=2;s=Citadel
    ├── Reactor                      ns=2;s=Citadel.Reactor
    │   ├── REACTOR_POWER_PCT, CORE_TEMP_C, HE_LOOP_PRESS_BAR, HE_FLOW_KGS
    │   └── POWER_SETPOINT_PCT       (read/write)
    ├── Radiation
    │   ├── Vault                    ns=2;s=Citadel.Radiation.Vault
    │   │   ├── VAULT_RAD_USVH, FAULT_TRIGGER
    │   │   ├── TriggerFault()  → Double fault level (µSv/h)   ns=2;s=TriggerFault
    │   │   └── ClearFault()                                   ns=2;s=ClearFault
    │   └── ControlRoom
    │       └── CONTROL_ROOM_RAD_USVH
    └── PLC
        └── PLC_HEARTBEAT_COUNT
```

`TriggerFault` is idempotent: calling it again while the fault is active returns the same level.

## Security Note

Modbus TCP has **no authentication or encryption**, and the MVP OPC UA endpoint runs with `SecurityPolicy None` and anonymous access: anyone who can reach port 502 or 4840 can write the power setpoint and trigger a radiation fault. In this lab, the only control on those writable points is network-level: PC #2's firewall admits only PC #3 (10.10.0.20), PC #4 is blocked from PC #2 at both ends, and the OT LAN is air-gapped. That is the classic L1 posture, and it is why the L3.5 boundary matters. Mitigations we have not implemented here (and the lab does not claim): OPC UA `Basic256Sha256` with certificates and user authentication (planned, US-503), Modbus/TCP Security (TLS, port 802), a protocol-aware OT firewall or DPI that allows only FC03/04 from the gateway, and a read-only gateway path. See the compliance guide gap register.
