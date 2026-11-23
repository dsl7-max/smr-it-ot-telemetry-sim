# CLAUDE.md — Standing Tactical Rules (Project Citadel)

Role: Tactical Scrum Master & Lead Builder. Strategy and OT tutoring live with the Strategic PM (Gemini); keep work here tactical and code-centric.
**MVP deadline: December 31, 2026** (6-week plan in `/agile/backlog.md`).

## 1. 4-PC Citadel Topology (follow strictly)

| PC | Role | Purdue | Runner labels | Compose file | Runs |
|----|------|--------|---------------|--------------|------|
| #1 Dev & CI Workstation (this box) | Write code, pytest, Git; live CI/CD kickoff screen in demo | — | — | — | VS Code, Claude Code, local unit tests |
| #2 Citadel Plant Floor (Flat HP AiO) | Field devices | L0–1 | `citadel-plant` | `deploy/docker-compose.plant.yml` | Reactor simulator, Virtual PLC — Modbus TCP :502, OPC UA :4840 |
| #3 Citadel Control Room (34" Curved HP AiO) | Supervisory / ops | L2–3 | `control-room` | `deploy/docker-compose.control-room.yml` | FactoryTalk Optix HMI (native), OT gateway, Mosquitto :1883 |
| #4 Enterprise IT & Analytics (Flat HP AiO) | Business / analytics, behind L3.5 router firewall | L4 | `enterprise-it` | `deploy/docker-compose.enterprise-it.yml` | Ingestion worker, TimescaleDB :5432, Grafana :3000 |

- PC #1 never hosts long-lived plant/IT services. Deployment to PCs #2–#4 happens **only** via `git push` → GitHub Actions self-hosted runners. No manual copying of code.
- Each service lives in exactly one compose file matching its Purdue level. Never mix levels.
- The only path across the L3.5 firewall is MQTT TCP 1883 (PC #3 ↔ PC #4). PC #4 must never talk to PC #2 directly.
- Keep the repo portable: no absolute paths, no hard-coded IPs (use `PLANT_HOST` / `CONTROL_ROOM_HOST` env vars), secrets via `deploy/.env` (gitignored) or GitHub Secrets.

## 2. Code Standards

- Python 3.12+, fully type-hinted, small pure functions where possible.
- Every new module ships with `pytest` unit tests (`tests/` alongside the module). Run `pytest` before declaring work done.
- Use `random.Random(seed)` / injectable clocks so simulator output is deterministic in tests.
- Config via environment variables with sane defaults.

## 3. End-of-Session Ritual (automatic)

At the end of every coding session:
1. Update the current sprint file (`/agile/sprint-NN.md`): check off completed acceptance criteria and add a **2-sentence** Session Log entry (date, what was done, what's next).
2. Keep `/docs/architecture.md` (diagram + port inventory) in sync with any port, container, image, or service change in `/deploy`.
3. Keep `/docs/network.md` in sync with any firewall rule or port exposure change.

## Repo Map

```
/simulators         PC #2 L0–1: Citadel reactor simulator, Virtual PLC (Python)
/ot-gateway         PC #3 L2–3: Modbus/OPC UA → MQTT bridge
/it-ingestion       PC #4 L4:   MQTT → TimescaleDB worker
/deploy             docker-compose.{plant,control-room,enterprise-it}.yml, mosquitto config, .env.example
/.github/workflows  deploy.yml — pytest → matrix deploy to 3 self-hosted runners
/docs               architecture.md, network.md
/agile              sprint-NN.md, backlog.md
```
