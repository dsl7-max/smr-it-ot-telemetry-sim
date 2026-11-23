# Sprint 01 — Foundation, Citadel Topology & Zero-Touch Pipeline ✅ CLOSED

**Dates:** Nov 21 – Nov 29, 2026 (closed early, Nov 23) · **MVP deadline:** Dec 31, 2026 (see `backlog.md`)
**Sprint Goal:** Stand up a portable repo, a documented 4-PC Citadel network, a first reactor sensor simulator with unit tests, and a `git push` pipeline foundation for PC #2, PC #3, and PC #4.

| PC | Hostname · IP | Purdue | Workloads |
|----|---------------|--------|-----------|
| #1 | `citadel-dev` · 192.168.1.177 (corp, DHCP) | — | VS Code, Claude Code, pytest, Git |
| #2 | `citadel-plant` · 10.10.0.10 (air-gapped OT) | L0–1 | Reactor simulator, Virtual PLC (Modbus :502, OPC UA :4840) |
| #3 | `citadel-hmi` · 10.10.0.20 (air-gapped OT) | L2–3 | FactoryTalk Optix HMI, Mosquitto (:1883) |
| #4 | `citadel-it` · 192.168.1.244 + 10.10.0.30 (dual-NIC) | L4 / L3.5 boundary | Ingestion worker, TimescaleDB (:5432), Grafana (:3000), Actions runner + jump-host |

---

## US-101: Local Dev & AI CLI Environment Setup ✅
*As the builder, I want a reproducible dev environment on PC #1 so that I can write, test, and version code efficiently.*

- [x] VS Code 1.146.0 installed with Python, Docker, YAML, and Markdown Mermaid extensions
- [x] Python 3.12+ installed; `python --version` verified (3.12.10)
- [x] Project virtual environment created (`.venv`) and `requirements-dev.txt` installs cleanly
- [x] Docker Desktop installed and `docker compose version` verified (Docker 29.9.3, Compose v5.7.0)
- [x] Claude Code installed, authenticated, and running in the repo root
- [x] Git configured (`user.name`, `user.email`) and initial commit made on `main`

## US-102: Repository Structure, Architecture Diagram & Living Backlog ✅
*As the builder, I want a documented structure and architecture so that reviewers (and I) understand the system at a glance.*

- [x] Repo initialized with `/simulators`, `/ot-gateway`, `/it-ingestion`, `/deploy`, `/.github/workflows`, `/docs`, `/agile`
- [x] `/docs/architecture.md` created with Mermaid.js diagram of the 4-PC Citadel Purdue Model (levels, services, ports, L3.5 boundary, CI/CD path)
- [x] `/agile/backlog.md` created and re-baselined to a 6-week MVP ending Dec 31, 2026 (Sprints 2–6, incl. Week 6 demo script)
- [x] `README.md` explains project purpose, topology, and how to clone/run

## US-103: "Hello World" Citadel Reactor Sensor Simulator & Pytest Unit Test ✅
*As an OT engineer, I want simulated reactor telemetry so that downstream layers have realistic data to consume.*

- [x] `/simulators` contains a type-hinted Python module generating core temp (°C), helium loop pressure (bar), and radiation monitor (µSv/h) readings — `simulators/citadel_reactor.py`
- [x] Readings include timestamp, tag name, value, unit, and stay within configurable bounds with noise
- [x] Simulator runnable from CLI (`python -m simulators.citadel_reactor --seed 42 --count 5 --interval 1`), UTF-8 JSON lines
- [x] `pytest` unit tests cover value bounds, data shape, and deterministic output with a fixed seed (26 tests)
- [x] `pytest` passes locally on PC #1
- [x] `pytest` passes in the GitHub Actions `test` job (empty-test-suite exception removed) — green on `714099d`

## US-104: 4-PC Dual-NIC + Air-Gapped Network Topology & Firewall Baseline ✅ *(design & tooling scope)*
*As the builder, I want a documented, enforceable network design so that data flows L0 → L4 only through approved conduits.*

- [x] `/docs/network.md` documents hostname, IP, subnet, gateway, NIC role, and Internet access for PC #1–#4 (corp LAN `192.168.1.0/24` vs. air-gapped R8000 OT LAN `10.10.0.0/24`)
- [x] Full flow matrix documented: 9 allowed flows + 5 explicitly blocked (incl. PC #4 ↔ PC #2 blocked at both ends)
- [x] Level 3.5 enforcement documented: PC #4 dual-NIC with IP forwarding/IPEnableRouter/ICS/RRAS disabled and no gateway on the OT NIC; honest caveat vs. a true DMZ recorded
- [x] Windows Defender Firewall rules documented per PC
- [x] `/deploy/setup-firewall.ps1` created: per-role `New-NetFirewallRule` baseline, forwarding guards, PASS/FAIL `-VerifyOnly` report; parse-checked and dry-run (`-WhatIf`) for all 3 roles on PC #1
- [x] PC #4 jump-host deployment path documented (single runner, SSH + ProxyJump, `docker save | load` across the air gap)
- [ ] ➜ **Moved to US-406 (Sprint 4):** apply the script on PC #2–#4 and record ping/port checks, including negative checks PC #4 ↛ PC #2 *(requires hands-on time on the execution PCs; 502/4840 can't pass until the Virtual PLC exists in Sprint 2)*

## US-105: Zero-Touch `git push` CI/CD Pipeline ✅ *(foundation scope)*
*As the builder, I want every push to `main` to test and deploy automatically so that all three execution PCs always run the latest code.*

- [x] `/.github/workflows/deploy.yml` scaffolded: pytest → deploy matrix
- [x] `/deploy/docker-compose.plant.yml`, `docker-compose.control-room.yml`, `docker-compose.enterprise-it.yml` scaffolded
- [x] Private GitHub repo created and `main` pushed (`dsl7-max/valar-it-ot-sim`)
- [x] Push to `main` runs pytest and blocks deploy on failure (deploy `needs: test`; verified green on `714099d`)
- [x] Deploy jobs gated behind repo variable `RUNNERS_READY == 'true'` so `main` stays green until the runner exists
- [ ] ➜ **Moved to US-406 (Sprint 4, PO directive 2026-11-23):** single runner on PC #4 (air gap means PCs #2/#3 can't host runners), SSH jump-host deploy, secrets/variables, `RUNNERS_READY=true`, end-to-end deploy of all 3 stacks

---

## Sprint 1 Review

| Story | Outcome |
|-------|---------|
| US-101 Dev environment | ✅ Done |
| US-102 Repo, architecture, backlog | ✅ Done |
| US-103 Reactor simulator + pytest | ✅ Done (26 tests, CI green) |
| US-104 Network design + firewall baseline | ✅ Done — live verification carried to US-406 |
| US-105 CI/CD pipeline | ✅ Foundation done — runner + jump-host deploy carried to US-406 |

**Key decisions:** 4-PC Citadel topology · air-gapped OT LAN with dual-NIC PC #4 as the non-routing L3.5 boundary · single runner on PC #4 deploying over SSH (ProxyJump to PC #2) · deploys gated by `RUNNERS_READY` until Sprint 4.
**Carried over:** live network verification and runner/jump-host deployment → **US-406** (Sprint 4).

---

## Session Log

- **2026-11-21 (Session 1):** Scaffolded repo structure, Sprint 1 board, `CLAUDE.md` rules, starter Docker Compose files, and the test→deploy GitHub Actions pipeline. Next: complete US-101 environment verification and build the US-103 helium sensor simulator with tests.
- **2026-11-23 (Session 2):** Re-baselined to the 4-PC Citadel architecture (Plant Floor L0–1, Control Room L2–3, Enterprise IT L4 behind the L3.5 firewall) with three compose files, a matrix deploy to three runner labels, the architecture diagram, and a 6-week backlog to the Dec 31 MVP; US-102 closed, and US-101 left open pending VS Code extensions and a Git identity. Next: create the private GitHub repo, register the three self-hosted runners, and build the US-103 reactor simulator.
- **2026-11-23 (Session 3):** Installed the VS Code extensions, set the Git identity (`dsl7-max`), made the initial commit, and pushed `main` to the private repo `dsl7-max/valar-it-ot-sim`, closing US-101 and US-102. Next: build the US-103 Citadel reactor simulator with pytest, then register the three self-hosted runners.
- **2026-11-23 (Session 4):** Built the US-103 Citadel reactor simulator (CORE_TEMP °C, HE_LOOP_PRESS bar, RAD_MON_01–03 µSv/h) with a seeded, clock-injected design, a JSON-lines CLI, and 26 passing pytest tests, and made CI fail on an empty test suite. Next: push to confirm the GitHub Actions `test` job passes, then start US-104 network documentation and register the self-hosted runners.
- **2026-11-23 (Session 5):** The GitHub Actions `test` job passed on `714099d`, closing US-103, and the pytest gate in front of deploy is confirmed working. Next: US-104 network documentation (hostnames, static IPs, L3.5 firewall rules) and registering the self-hosted runners on PC #2–#4.
- **2026-11-23 (Session 6):** Gated the three deploy jobs behind the `RUNNERS_READY` repo variable so pushes to `main` stay green on pytest, and deferred runner registration and end-to-end deploy to Sprint 4 (US-406). Next: US-104 network documentation and static IP verification.
- **2026-11-23 (Session 7 — Sprint 1 close):** Locked the dual-NIC L4 + air-gapped OT design in `docs/network.md` and `deploy/setup-firewall.ps1`, re-architected deployment around a single PC #4 runner and SSH jump-host (ProxyJump to PC #2 keeps PC #4 ↛ PC #2 intact), and closed US-104 and Sprint 1 with live verification and runner work carried to US-406. Next: Sprint 2 — extend the reactor simulator (US-201) and build the Virtual PLC with Modbus TCP :502 (US-202).
- **2026-11-23 (Session 8 — post-close doc add-on):** Added `docs/compliance-and-interview-guide.md` mapping the air gap, dual-NIC L3.5 boundary, pinned configuration control, and dual-zone radiation monitoring to Purdue, 10 CFR 73.54 / RG 5.71 / NEI 08-09, and Appendix B/NQA-1 traceability, with a 7-item gap register and 12 thirty-second Q&As. Next: Sprint 2 planning (US-201/US-202).
