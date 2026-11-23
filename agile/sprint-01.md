# Sprint 01 — Foundation, Citadel Topology & Zero-Touch Pipeline

**Dates:** Nov 21 – Nov 29, 2026 · **MVP deadline:** Dec 31, 2026 (see `backlog.md`)
**Sprint Goal:** Stand up a portable repo, a verified 4-PC Citadel network, a first reactor sensor simulator with unit tests, and a `git push` pipeline that deploys to PC #2, PC #3, and PC #4.

| PC | Role | Purdue | Runner labels | Workloads |
|----|------|--------|---------------|-----------|
| #1 | Dev & CI Workstation | — | — | VS Code, Claude Code, pytest, Git |
| #2 | Citadel Plant Floor (Flat HP AiO) | L0–1 | `citadel-plant` | Reactor simulator, Virtual PLC (Modbus :502, OPC UA :4840) |
| #3 | Citadel Control Room (34" Curved HP AiO) | L2–3 | `control-room` | FactoryTalk Optix HMI, Mosquitto (:1883) |
| #4 | Enterprise IT & Analytics (Flat HP AiO) | L4 (behind L3.5 firewall) | `enterprise-it` | Ingestion worker, TimescaleDB (:5432), Grafana (:3000) |

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
- [x] `/docs/architecture.md` created with Mermaid.js diagram of the 4-PC Citadel Purdue Model (levels, services, ports, L3.5 firewall, CI/CD runners)
- [x] `/agile/backlog.md` created and re-baselined to a 6-week MVP ending Dec 31, 2026 (Sprints 2–6, incl. Week 6 demo script)
- [x] `README.md` explains project purpose, topology, and how to clone/run

## US-103: "Hello World" Citadel Reactor Sensor Simulator & Pytest Unit Test
*As an OT engineer, I want simulated reactor telemetry so that downstream layers have realistic data to consume.*

- [x] `/simulators` contains a type-hinted Python module generating core temp (°C), helium loop pressure (bar), and radiation monitor (µSv/h) readings — `simulators/citadel_reactor.py`
- [x] Readings include timestamp, tag name, value, unit, and stay within configurable bounds with noise
- [x] Simulator runnable from CLI (`python -m simulators.citadel_reactor --seed 42 --count 5 --interval 1`), UTF-8 JSON lines
- [x] `pytest` unit tests cover value bounds, data shape, and deterministic output with a fixed seed (26 tests)
- [x] `pytest` passes locally on PC #1
- [ ] `pytest` passes in the GitHub Actions `test` job (empty-test-suite exception removed)

## US-104: 4-PC Network Topology & Static IP Verification
*As the builder, I want verified static IPs and firewall rules so that data flows L0 → L4 only through approved ports.*

- [ ] `/docs/network.md` documents hostname, static IP, subnet, gateway, and role for PC #1–#4 (OT zone vs. IT zone)
- [ ] L3.5 router firewall rules documented: only TCP 1883 between PC #3 and PC #4
- [ ] Ping checks recorded between all permitted PC pairs
- [ ] Port checks recorded (`Test-NetConnection -Port`): 502/4840 PC #3→PC #2, 1883 PC #4→PC #3, 5432/3000 on PC #4
- [ ] Negative check recorded: PC #4 cannot reach PC #2 on 502/4840
- [ ] Windows Defender Firewall inbound rules documented per PC

## US-105: Zero-Touch `git push` CI/CD Pipeline
*As the builder, I want every push to `main` to test and deploy automatically so that all three execution PCs always run the latest code.*

- [x] `/.github/workflows/deploy.yml` scaffolded: pytest → matrix deploy to 3 self-hosted runners
- [x] `/deploy/docker-compose.plant.yml`, `docker-compose.control-room.yml`, `docker-compose.enterprise-it.yml` scaffolded
- [ ] Private GitHub repo created and `main` pushed
- [ ] Self-hosted runner on PC #2 with labels `self-hosted, windows, citadel-plant`
- [ ] Self-hosted runner on PC #3 with labels `self-hosted, windows, control-room`
- [ ] Self-hosted runner on PC #4 with labels `self-hosted, windows, enterprise-it`
- [ ] Repository secrets (`POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`) and variables (`PLANT_HOST`, `CONTROL_ROOM_HOST`) configured
- [ ] Push to `main` runs pytest and blocks deploy on failure
- [ ] Successful push deploys all 3 stacks with no manual steps; `docker compose ps` healthy on PC #3 and PC #4

---

## Session Log

- **2026-11-21 (Session 1):** Scaffolded repo structure, Sprint 1 board, `CLAUDE.md` rules, starter Docker Compose files, and the test→deploy GitHub Actions pipeline. Next: complete US-101 environment verification and build the US-103 helium sensor simulator with tests.
- **2026-11-23 (Session 2):** Re-baselined to the 4-PC Citadel architecture (Plant Floor L0–1, Control Room L2–3, Enterprise IT L4 behind the L3.5 firewall) with three compose files, a matrix deploy to three runner labels, the architecture diagram, and a 6-week backlog to the Dec 31 MVP; US-102 closed, and US-101 left open pending VS Code extensions and a Git identity. Next: create the private GitHub repo, register the three self-hosted runners, and build the US-103 reactor simulator.
- **2026-11-23 (Session 3):** Installed the VS Code extensions, set the Git identity (`dsl7-max`), made the initial commit, and pushed `main` to the private repo `dsl7-max/valar-it-ot-sim`, closing US-101 and US-102. Next: build the US-103 Citadel reactor simulator with pytest, then register the three self-hosted runners.
- **2026-11-23 (Session 4):** Built the US-103 Citadel reactor simulator (CORE_TEMP °C, HE_LOOP_PRESS bar, RAD_MON_01–03 µSv/h) with a seeded, clock-injected design, a JSON-lines CLI, and 26 passing pytest tests, and made CI fail on an empty test suite. Next: push to confirm the GitHub Actions `test` job passes, then start US-104 network documentation and register the self-hosted runners.
