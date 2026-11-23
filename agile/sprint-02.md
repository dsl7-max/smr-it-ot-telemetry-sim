# Sprint 02 — Citadel Plant Floor: Physics, Radiation Zones & Virtual PLC

> **Status: APPROVED by Strategic PM (2026-11-23).** Decisions recorded under "PM Decisions" below.

**Dates:** Nov 30 – Dec 6, 2026 · **Capacity:** ~16 h · **MVP deadline:** Dec 31, 2026
**Sprint Goal:** PC #2 runs a containerized Citadel Plant Floor. A coupled reactor model plus two physical radiation zones feed a Virtual PLC that serves the same tags over Modbus TCP :502 and OPC UA :4840, and a fault can be triggered over the wire. Two supply-chain hardening items are also closed.

**Execution order:** US-207 → US-201 → US-202 → US-203 → US-204 + US-206 → US-205

---

## US-207: Lock Exact Dependency Versions · *0.5 h* ✅
*As the builder, I want exact dependency versions so that every build and test run is reproducible.*

- [x] `requirements-dev.txt` pins `pytest==9.2.0` (no ranges)
- [x] New `requirements.txt` (runtime) pins `pymodbus==3.16.2` and `asyncua==2.1.0`; dev file includes it via `-r requirements.txt`
- [x] *(Added)* Full transitive lock in `constraints.txt` (19 packages, incl. `cryptography==50.0.2`) so indirect deps can't drift either
- [x] Chosen versions are stable releases at least 2 weeks old (pytest 2026-10-27, pymodbus 2026-11-03, asyncua 2026-10-06); recorded in the Session Log
- [x] CI `test` job passes with the locked set (`d60d957`)

## US-201: Coupled Reactor Physics + Physical Radiation Zones + Fault Trigger · *4.5 h* ✅
*As an OT engineer, I want sensor values that react to each other and to faults so that the HMI and dashboards show realistic plant behavior.*

**Tags (unit-suffixed naming; see Open Question 1):**

| Tag | Unit | Normal | Behavior |
|-----|------|--------|----------|
| `REACTOR_POWER_PCT` | % | 100 | Setpoint; ramps toward the operator setpoint |
| `CORE_TEMP_C` | °C | ~750 | First-order lag toward a power-driven target; rises if helium flow drops |
| `HE_LOOP_PRESS_BAR` | bar | ~70 | Tracks core temp, closed-loop gas: `P = P₀ · T(K) / T₀(K)` |
| `HE_FLOW_KGS` | kg/s | ~90 | Circulator flow; lower flow means higher core temp |
| `VAULT_RAD_USVH` | µSv/h | ~1.5 | Proportional to power; **fault → ramps to 25–80** (seeded random) |
| `CONTROL_ROOM_RAD_USVH` | µSv/h | ~0.20 | Background only; the outer wall shields it, so it stays near 0.20 during a vault fault |

- [x] Simulator refactored to a **stateful, tick-based model** (`CitadelReactor.step(dt)`); still seeded and clock-injected, with no wall-clock in physics; true state is noise-free, noise is applied only at `read()`
- [x] Coupling verified: a power change moves temp with a first-order lag and pressure follows by ideal gas; half flow raises core temp 750 → 1250 °C
- [x] `trigger_fault()` / `clear_fault()` API: vault rate ramps to the fault level over 5 s and decays back (τ = 10 s) after clearing; re-triggering while active is idempotent
- [x] CLI flags `--fault-at-tick N` and `--fault-duration N` (plus `--dt`) for scripted demos
- [x] `RAD_MON_01..03` removed; `CORE_TEMP` / `HE_LOOP_PRESS` renamed to the unit-suffixed names
- [x] pytest: steady-state convergence, step-response direction and lag, pressure/temperature ratio, fault magnitude stays within 25–80, control room stays below 0.3 µSv/h during a fault, determinism by seed (64 tests total)

## US-202: Virtual PLC — Modbus TCP :502 · *3.5 h* ✅
*As a control-room engineer, I want the plant tags on Modbus TCP so that the OT gateway and HMI can poll them like a real PLC.*

- [x] `simulators/virtual_plc/` runs the simulator as its I/O image and serves Modbus TCP (pymodbus, asyncio); one latched image per scan, so a single read returns one consistent tick
- [x] **Register map in `/docs/tag-map.md`:**
  - Input registers (FC04, read-only): one scaled INT16 per tag (e.g., temp ×10, pressure ×100, radiation ×100)
  - Holding register (FC03/06): `POWER_SETPOINT_PCT` ×10, writable
  - Coil (FC01/05): `FAULT_TRIGGER`. Write 1 to trigger, 0 to clear.
  - *(Added)* `PLC_HEARTBEAT_COUNT` input register 6 (scan counter) so the gateway can detect stale data
- [x] Listens on container port 5020 (`PLC_MODBUS_PORT`), to be published as host :502 in US-204 (lets the container run as non-root)
- [x] pytest: scaling round-trips, register addresses match `tag-map.md` (the doc is parsed by a test), and a coil write triggers the simulator fault; a real server and client run in-process on an ephemeral port (59 new tests, 123 total)
- [x] Security note in `tag-map.md`: Modbus has no authentication, so a writable coil is protected only by the PC #2 firewall rule (allows PC #3 only)

## US-203: Virtual PLC — OPC UA :4840 · *3 h* ✅
*As an HMI developer, I want the same tags over OPC UA so that FactoryTalk Optix can browse typed, named nodes.*

- [x] OPC UA server (asyncua) in the same PLC process; namespace `urn:citadel:plant` (ns=2), endpoint `opc.tcp://<host>:4840/citadel/plant`; nodes are updated from the same latched scan as Modbus after every scan, so subscriptions fire per scan
- [x] Address space: `Objects/Citadel/Reactor/*`, `Objects/Citadel/Radiation/{Vault,ControlRoom}/*`, plus `Citadel/PLC/PLC_HEARTBEAT_COUNT` (Int32); values are native `Double` with `EngineeringUnits` (UN/CEFACT) and `EURange` properties; string NodeIds equal the tag names
- [x] Writable `POWER_SETPOINT_PCT` node (named per the tag rule rather than `PowerSetpoint`); out of range or NaN returns `BadOutOfRange` and is not applied. `TriggerFault` (returns the fault level) and `ClearFault` methods on `Radiation/Vault`, plus a read-only `FAULT_TRIGGER` Boolean
- [x] Node IDs documented in `/docs/tag-map.md` next to the Modbus addresses (one table, both protocols); the doc-sync test now checks the NodeId and type columns too
- [x] MVP security: `SecurityPolicy None` + anonymous; `Basic256Sha256` Sign & Encrypt deferred to US-503 (see Open Question 3)
- [x] pytest: an in-process client reads every node, values match the Modbus side for the same tick, and the method triggers the fault; also covers subscriptions, rejected writes, read-only nodes and the EU properties (141 tests total)

## US-204: Containerize the Plant Floor · *2 h*
*As the builder, I want the plant packaged as one self-contained image so that it runs on air-gapped PC #2 with no downloads.*

- [ ] `simulators/Dockerfile`: `python:3.12-slim` (digest-pinned via US-206), deps installed at build time from locked requirements, **non-root user**, `HEALTHCHECK` (TCP probe on 5020)
- [ ] `docker-compose.plant.yml`: a single `citadel-plant` service (simulator embedded in the PLC; see Open Question 2), ports `502:5020` and `4840:4840`, `SIM_SEED` / `SIM_TICK_SECONDS` env vars, **no bind mounts**
- [ ] CI `test` job also runs `docker build` (catches Dockerfile breakage on every push)
- [ ] Smoke test on PC #1: `docker compose up`, read values on 502/4840, `docker compose down` (short-lived; PC #1 still hosts no long-lived services)
- [ ] `docs/architecture.md` inventory and diagram updated (ports, container, image)

## US-206: Pin Docker Images by SHA-256 Digest · *1 h*
*As a configuration manager, I want images pinned by digest so that an upstream tag change can't silently alter our baseline.*

- [ ] All compose images and the Dockerfile `FROM` use `name:tag@sha256:<digest>` (tag kept for readability)
- [ ] Digests taken from the multi-arch index (`docker buildx imagetools inspect`), recorded in the Session Log
- [ ] `docs/architecture.md` and the compliance guide (§2.3, gap G4) updated to "digest-pinned"
- [ ] All compose files still pass `docker compose config`; the plant image builds

## US-205: Protocol Integration Tests + `plant_probe` CLI · *1.5 h*
*As a tester, I want a probe that reads the running PLC over both protocols so that I can verify a deployment (now on PC #1, later from PC #3).*

- [ ] `python -m tools.plant_probe --host <ip> [--modbus] [--opcua]` prints tag, value, and unit from both protocols side by side, and can `--trigger-fault` / `--clear-fault`
- [ ] Integration test: start the PLC in-process on ephemeral ports, read back via the probe, confirm Modbus and OPC UA agree
- [ ] Probe output becomes the evidence format for US-406's live verification on PC #3 → PC #2

---

## PM Decisions (approved 2026-11-23)

1. **Tag naming:** ✅ standardize on unit-suffixed names: `REACTOR_POWER_PCT`, `CORE_TEMP_C`, `HE_LOOP_PRESS_BAR`, `HE_FLOW_KGS`, `VAULT_RAD_USVH`, `CONTROL_ROOM_RAD_USVH`.
2. **Packaging:** ✅ L0 physics and the L1 Virtual PLC stay separate Python modules, shipped in a **single container**.
3. **OPC UA security:** ✅ `SecurityPolicy None` for the MVP; `Basic256Sha256` + certificates hardened in US-503 (Sprint 5).
4. **Control room shielding:** ✅ `CONTROL_ROOM_RAD_USVH` stays at normal background (~0.20 µSv/h) during a vault fault, demonstrating shielding effectiveness (GDC 19 habitability).

## Risks

- **Library API churn:** pymodbus has made breaking changes between minor versions; US-207 pins the version before any code is written against it.
- **Capacity:** 16 h planned against ~16–17 h available. If it runs over, US-205's integration test moves to Sprint 3 (the probe CLI stays).
- **Port 502 is privileged:** handled by publishing it from container port 5020 (US-202/204).

---

## Session Log

- **2026-11-23 (Planning):** Drafted Sprint 2: 7 stories, ~16 h, covering coupled physics with physical radiation zones and a fault trigger, a Modbus/OPC UA Virtual PLC, plant containerization, and digest/version pinning. Next: PM review of the 3 open questions, then start with US-207.
- **2026-11-23 (Session 1):** Closed US-207 (pytest 9.2.0, pymodbus 3.16.2, asyncua 2.1.0, plus a 19-package `constraints.txt` transitive lock) and US-201: a stateful coupled plant model with six unit-suffixed tags, physical radiation zones (vault 1.5 → 25–80 µSv/h on fault, control room steady at 0.20), and a code/CLI fault trigger, verified by 64 passing tests. Next: push to confirm CI on the locked set, then US-202 (Modbus TCP Virtual PLC and `docs/tag-map.md`).
- **2026-11-23 (Session 1, CI):** CI on `d60d957` confirmed green with the locked set, closing US-207; the run had been stuck behind pre-gate run #2, so the workflow's `concurrency` group was moved from workflow level to the `deploy` job (per target PC) so the test job can never queue behind a stuck deploy. Next: US-202 (Modbus TCP Virtual PLC and `docs/tag-map.md`).
- **2026-11-23 (Session 2):** Closed US-202: a Virtual PLC (`python -m simulators.virtual_plc`) that serves the reactor over Modbus TCP :5020 with a doc-verified register map (`docs/tag-map.md`), a writable power setpoint, a fault coil, a heartbeat and all-or-nothing multi-writes, built on pymodbus 3.16's `SimDevice` action hook (the old datastore API is deprecated). A live smoke run on PC #1 triggered a vault fault over the wire (1.5 → 52 µSv/h, control room steady at 0.21); next is US-203 (OPC UA :4840 in the same process).
- **2026-11-23 (Session 3):** Closed US-203: the Virtual PLC now also serves OPC UA on :4840 (`urn:citadel:plant`) from the same latched scan as Modbus, with typed nodes, engineering-unit metadata, a validated writable setpoint, `TriggerFault`/`ClearFault` methods, and NodeIds added to the test-enforced `docs/tag-map.md` (141 tests; a live run showed all 7 tags agreeing across both protocols on the same scan). Next: US-204 + US-206 (containerize the plant on python:3.12-slim with digest-pinned images, ports 502:5020 and 4840:4840).
