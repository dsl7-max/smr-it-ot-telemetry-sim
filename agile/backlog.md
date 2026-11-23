# Citadel Living Backlog — 6-Week MVP (MVP deadline: Thursday, December 31, 2026)

**Budget:** ~100 hours part-time ≈ 16–17 h/week. Sprints are one calendar week.
**MVP definition:** A live `git push` from PC #1 tests and deploys to all 3 execution PCs; reactor telemetry flows PC #2 → PC #3 (HMI + MQTT) → L3.5 firewall → PC #4 (TimescaleDB + Grafana).

| Sprint | Dates (2026) | Theme | Detail |
|--------|--------------|-------|--------|
| 1 | Nov 21 – Nov 29 | Foundation, Topology & Pipeline | see `sprint-01.md` |
| 2 | Nov 30 – Dec 6 | Citadel Plant Floor: Simulator + Virtual PLC | below |
| 3 | Dec 7 – Dec 13 | Control Room Gateway + MQTT → Enterprise Ingestion | below |
| 4 | Dec 14 – Dec 20 | FactoryTalk Optix HMI + Grafana Dashboards | below |
| 5 | Dec 21 – Dec 27 | Alarms, Scenarios & Hardening (holiday-light week) | below |
| 6 | Dec 28 – Dec 31 | Polish & Live Demo | below |

---

## Sprint 2 — Citadel Plant Floor (PC #2, L0–1)
- [ ] **US-201** Extend reactor simulator: core temp, helium loop pressure, helium flow, radiation monitors (multiple channels), with physically coupled dynamics (e.g., pressure follows temp).
- [ ] **US-202** Virtual PLC exposing simulator tags over Modbus TCP :502 (documented register map in `/docs/tag-map.md`).
- [ ] **US-203** Same tags over OPC UA :4840 (documented node IDs).
- [ ] **US-204** Dockerfiles for sim + PLC; enable services in `docker-compose.plant.yml`; deployed via `citadel-plant` runner.
- [ ] **US-205** pytest coverage for simulator dynamics and register/node mapping.

## Sprint 3 — Control Room Gateway & Enterprise Ingestion (PC #3 → PC #4)
- [ ] **US-301** OT Gateway on PC #3: poll Modbus from PC #2, publish JSON to MQTT topic hierarchy (`citadel/<area>/<tag>`).
- [ ] **US-302** MQTT topic & payload schema documented (`/docs/mqtt-schema.md`).
- [ ] **US-303** Ingestion worker on PC #4: subscribe across firewall, batch-insert into TimescaleDB hypertable.
- [ ] **US-304** DB schema migration script (hypertable, retention, continuous aggregate for 1-min rollups).
- [ ] **US-305** Firewall rule locked to TCP 1883 PC #3 ↔ PC #4; verified with negative port tests (502/4840 blocked from PC #4).
- [ ] **US-306** pytest for gateway mapping and ingestion parsing (broker/DB mocked).

## Sprint 4 — Operator HMI & Executive Dashboards
- [ ] **US-401** FactoryTalk Optix project on PC #3 (34" widescreen layout): reactor overview, helium loop, radiation panel, connected via OPC UA.
- [ ] **US-402** Optix project files versioned in repo (`/control-room/optix/`) with export/import notes.
- [ ] **US-403** Grafana provisioned as code (datasource + dashboards JSON in `/deploy/grafana/`): exec KPI view, trends, uptime.
- [ ] **US-404** End-to-end latency check (sim value change → Grafana) documented.
- [ ] **US-406** *(carried from Sprint 1 US-105)* Register self-hosted runners on PC #2 (`citadel-plant`), PC #3 (`control-room`), PC #4 (`enterprise-it`) as Windows services with Docker access; configure secrets (`POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`) and variables (`PLANT_HOST`, `CONTROL_ROOM_HOST`); set `RUNNERS_READY=true`; verify a push deploys all 3 stacks.

## Sprint 5 — Alarms, Scenarios & Hardening
- [ ] **US-501** Alarm limits (HI/HIHI/LO/LOLO) in PLC + HMI alarm banner + Grafana alert rules.
- [ ] **US-502** Scenario injector: "helium leak", "loss of flow", "radiation spike" triggerable from CLI.
- [ ] **US-503** Mosquitto auth (password file) and non-default DB/Grafana credentials via secrets.
- [ ] **US-504** Container healthchecks + `restart: unless-stopped` verified across reboot of each PC.
- [ ] **US-505** Pipeline: remove "no tests" tolerance, add lint (ruff) + type check (mypy) gates.

## Sprint 6 — Polish & Live Demo (deadline Dec 31)
- [ ] **US-601** Demo script (`/docs/demo-script.md`): live `git push` on PC #1 shown as CI/CD kickoff screen → Actions run → visible change on PC #2, PC #3 HMI, and PC #4 Grafana; timed run-of-show with fallback steps.
- [ ] **US-602** Full dress rehearsal across all 4 PCs; issues logged and fixed.
- [ ] **US-603** README + architecture final pass; screenshots/GIFs of each screen.
- [ ] **US-604** *(Optional)* Citadel enclosure graphics / signage for the 3 HP All-in-Ones (Plant Floor, Control Room, Enterprise IT).
- [ ] **US-605** Portfolio write-up and 3–5 min recorded demo video.

---

## Icebox (post-MVP)
- OPC UA → MQTT Sparkplug B
- L3.5 DMZ broker / data diode pattern
- Historian replay & anomaly detection on TimescaleDB
