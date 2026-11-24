# CLAUDE.md — Standing Tactical Rules (Project Citadel)

Role: Tactical Scrum Master & Lead Builder. Strategy and OT tutoring live with the Strategic PM (Gemini); keep work here tactical and code-centric.
**MVP deadline: December 31, 2026** (6-week plan in `/agile/backlog.md`).

## 1. 4-PC Citadel Topology (follow strictly — detail in `docs/network.md`)

| PC | Hostname · IP | Purdue | Compose file | Runs |
|----|---------------|--------|--------------|------|
| #1 Dev & CI Workstation (this box) | `citadel-dev` · 192.168.1.177 (DHCP, corp LAN) | — | — | VS Code, Claude Code, pytest, Git; live CI/CD kickoff screen in demo |
| #2 Citadel Plant Floor (Flat HP AiO) | `citadel-plant` · 10.10.0.10 (air-gapped OT) | L0–1 | `deploy/docker-compose.plant.yml` | Reactor simulator, Virtual PLC — Modbus TCP :502, OPC UA :4840 |
| #3 Citadel Control Room (34" Curved HP AiO) | `citadel-hmi` · 10.10.0.20 (air-gapped OT) | L2–3 | `deploy/docker-compose.control-room.yml` | FactoryTalk Optix HMI (native), OT gateway, Mosquitto :1883 |
| #4 Enterprise IT & Analytics (Flat HP AiO, dual-NIC) | `citadel-it` · 192.168.1.244 (corp) + 10.10.0.30 (OT) | L4 / L3.5 boundary | `deploy/docker-compose.enterprise-it.yml` | Ingestion worker, TimescaleDB :5432, Grafana :3000, **the only** Actions runner + deploy jump-host |

- PC #1 never hosts long-lived plant/IT services. Deployment happens **only** via `git push` → the single self-hosted runner on PC #4, which deploys to PC #3 over SSH and to PC #2 via SSH ProxyJump through PC #3. No manual copying of code.
- PCs #2/#3 are **air-gapped** (no Internet): OT images must be fully self-contained (no runtime pip/apt/pulls). Configuration-control flow: GitHub Actions builds, tests and publishes images to `ghcr.io`, pinned by SHA-256 digest; PC #4 pulls those exact verified digests and transfers them to PCs #2/#3 with `docker save | docker load` (OT starts them with `--pull never`). No bind-mounted host files in OT compose files — bake configs into images.
- Each service lives in exactly one compose file matching its Purdue level. Never mix levels.
- L3.5 = PC #4 with IP forwarding disabled. The only cross-boundary flows are PC #4 → PC #3 on MQTT :1883 and SSH :22. **PC #4 never talks to PC #2**; OT never initiates into IT. Firewall baseline: `deploy/setup-firewall.ps1`.
- Keep the repo portable: no absolute paths, no hard-coded IPs in code (use `PLANT_HOST` / `CONTROL_ROOM_HOST` env vars), secrets via `deploy/.env` (gitignored) or GitHub Secrets.
- PowerShell scripts must be ASCII-only (Windows PowerShell 5.1 misreads BOM-less UTF-8).

## 2. Code Standards

- Python 3.12+, fully type-hinted, small pure functions where possible.
- Every new module ships with `pytest` unit tests (`tests/` alongside the module). Run `pytest` before declaring work done.
- Use `random.Random(seed)` / injectable clocks so simulator output is deterministic in tests.
- Config via environment variables with sane defaults.
- Tag names: `UPPER_SNAKE` with a unit suffix (`CORE_TEMP_C`, `HE_LOOP_PRESS_BAR`, `VAULT_RAD_USVH`). Keep names identical across simulator, Modbus map, OPC UA nodes, MQTT topics, and DB columns.
- Dependencies: exact `==` pins in `requirements*.txt`; full transitive lock in `constraints.txt`. New deps must be stable releases at least 2 weeks old; regenerate the lock from a fresh venv after any change.

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
/deploy             docker-compose.{plant,control-room,enterprise-it}.yml, setup-firewall.ps1, mosquitto config, .env.example
/.github/workflows  deploy.yml — pytest → deploy (gated by RUNNERS_READY; PC #4 jump-host rework in US-406)
/docs               architecture.md, network.md, tag-map.md (Modbus/OPC UA register map; test-enforced)
/agile              sprint-NN.md, backlog.md
```
