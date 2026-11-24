# Citadel — End-to-End IT/OT Simulation Sandbox

A 4-PC portfolio lab that simulates a helium-cooled reactor plant and moves its telemetry up the Purdue Model — field protocols (Modbus TCP, OPC UA) → operator HMI (FactoryTalk Optix) → MQTT → across a Level 3.5 firewall → TimescaleDB + Grafana — with zero-touch `git push` deployment to every node.

| PC | Role | Purdue | Network | Runs |
|----|------|--------|---------|------|
| #1 | Dev & CI Workstation | — | Corp LAN 192.168.1.177 | VS Code, Claude Code, pytest, Git |
| #2 | Citadel Plant Floor | L0–1 | Air-gapped OT 10.10.0.10 | Reactor simulator, Virtual PLC (Modbus :502, OPC UA :4840) |
| #3 | Citadel Control Room | L2–3 | Air-gapped OT 10.10.0.20 | FactoryTalk Optix HMI, OT gateway, Mosquitto (:1883) |
| #4 | Enterprise IT & Analytics | L4 | Dual-NIC 192.168.1.244 + 10.10.0.30 | Ingestion worker, TimescaleDB (:5432), Grafana (:3000), Actions runner / jump-host |

Diagram: [`docs/architecture.md`](docs/architecture.md) · Network & firewall: [`docs/network.md`](docs/network.md) · Compliance & interview guide: [`docs/compliance-and-interview-guide.md`](docs/compliance-and-interview-guide.md) · Plan: [`agile/backlog.md`](agile/backlog.md) · Sprint: [`agile/sprint-01.md`](agile/sprint-01.md)

## Quick Start (PC #1)

```powershell
git clone https://github.com/dsl7-max/smr-it-ot-telemetry-sim.git
cd smr-it-ot-telemetry-sim
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

## Deployment

Pushing to `main` runs `.github/workflows/deploy.yml`: pytest on a GitHub-hosted runner, then the single self-hosted runner on dual-NIC PC #4 deploys its own stack locally and the air-gapped OT stacks over SSH (PC #3 directly, PC #2 via ProxyJump through PC #3). Deploys are gated by the `RUNNERS_READY` repo variable until that runner is live (US-406). To run one stack by hand, copy `deploy/.env.example` to `deploy/.env` and run `docker compose -f deploy/<file>.yml --env-file deploy/.env up -d`.
