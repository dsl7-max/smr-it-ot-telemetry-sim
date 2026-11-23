# Citadel — End-to-End IT/OT Simulation Sandbox

A 4-PC portfolio lab that simulates a helium-cooled reactor plant and moves its telemetry up the Purdue Model — field protocols (Modbus TCP, OPC UA) → operator HMI (FactoryTalk Optix) → MQTT → across a Level 3.5 firewall → TimescaleDB + Grafana — with zero-touch `git push` deployment to every node.

| PC | Role | Purdue | Runs |
|----|------|--------|------|
| #1 | Dev & CI Workstation | — | VS Code, Claude Code, pytest, Git |
| #2 | Citadel Plant Floor | L0–1 | Reactor simulator, Virtual PLC (Modbus :502, OPC UA :4840) |
| #3 | Citadel Control Room | L2–3 | FactoryTalk Optix HMI, OT gateway, Mosquitto (:1883) |
| #4 | Enterprise IT & Analytics | L4 | Ingestion worker, TimescaleDB (:5432), Grafana (:3000) |

Full diagram: [`docs/architecture.md`](docs/architecture.md) · Plan: [`agile/backlog.md`](agile/backlog.md) · Current sprint: [`agile/sprint-01.md`](agile/sprint-01.md)

## Quick Start (PC #1)

```powershell
git clone <repo-url>
cd valar-it-ot-sim
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

## Deployment

Pushing to `main` runs `.github/workflows/deploy.yml`: pytest on a GitHub-hosted runner, then each execution PC's self-hosted runner (`citadel-plant`, `control-room`, `enterprise-it`) runs `docker compose up` on its file in `/deploy`. To run one stack by hand, copy `deploy/.env.example` to `deploy/.env` and run `docker compose -f deploy/<file>.yml --env-file deploy/.env up -d`.
