# Citadel Compliance Mapping & Interview Guide

> **Purpose:** map Citadel lab design choices to nuclear and industrial cyber security principles, and prepare short spoken answers for demos and interviews.
> **Scope disclaimer:** Citadel is a **learning lab that demonstrates concepts**. It is **not** NRC-compliant and makes no regulatory claim. Regulatory summaries below are study notes. Verify them against the current revisions of **10 CFR 73.54**, **NRC Regulatory Guide 5.71 (Rev. 1)**, and **NEI 08-09 (Rev. 6)** before you quote them in a professional setting.

---

## 1. Reference Frameworks (study notes)

| Framework | What it is | Key concepts used here |
|-----------|------------|------------------------|
| **Purdue Model (ISA-95 / ISA/IEC 62443 reference)** | Layered ICS reference architecture: L0 process, L1 control (PLC), L2 supervisory (HMI), L3 site operations, L3.5 IT/OT DMZ, L4–5 enterprise. | Zones and conduits; no direct L4↔L0–1 traffic; DMZ at L3.5 |
| **10 CFR 73.54** | NRC rule requiring nuclear power reactor licensees to protect digital assets from cyber attack, up to and including the design basis threat. | **Critical Digital Assets (CDAs)** supporting **SSEP** functions: Safety (and important-to-safety), Security, Emergency Preparedness (incl. offsite comms), and the Support systems for these. Also requires a cyber security plan, defense-in-depth, configuration management, incident response, and records. |
| **NRC RG 5.71** | NRC guidance describing an acceptable way to meet 73.54. | **Defensive architecture of 5 levels.** ⚠️ Its numbering is the **reverse of Purdue**: Level 4 is the *most* protected (safety/security CDAs). Higher levels connect to lower ones only through **one-way, deterministic devices** (data diodes). |
| **NEI 08-09** | Industry cyber security plan template that the NRC has accepted for use. | Defensive architecture, CDA identification, technical controls (Appendix D), and operational/management controls (Appendix E), including portable media, configuration management, and vulnerability management. |
| **10 CFR 50 Appendix B / ASME NQA-1** | Nuclear QA program criteria. NQA-1 Part II Subpart 2.7 covers computer software QA. | Design control, verification, document control, and QA records. Together these add up to **traceability**: requirement → design → implementation → test → evidence. |

> **Memorize this one:** *"In Purdue, Level 4 is the enterprise IT network. In NRC RG 5.71, Level 4 is the most protected level, where the safety and security systems sit. Same number, opposite meaning."* Saying this shows you've actually read both.

---

## 2. Design Choice → Principle Mapping

### 2.1 Offline R8000 OT network (air gap)

| Aspect | Citadel implementation | Principle | Where it falls short of nuclear practice |
|--------|------------------------|-----------|------------------------------------------|
| No WAN on the OT router | R8000 WAN port unplugged; PCs #2/#3 have no Internet access (`docs/network.md` §1) | Purdue zone isolation; RG 5.71 isolates the high-assurance levels from the Internet | An air gap alone is **not** a control nuclear plants rely on. Stuxnet crossed an air gap on removable media. |
| Wireless bypass control | R8000 Wi-Fi radios disabled (hardening checklist) | Wireless is a classic way around a cabled air gap; NEI 08-09 controls cover wireless | Lab relies on a checklist, not continuous monitoring |
| Self-contained OT images | OT images built on PC #4 and moved with `docker save \| load`, with no runtime pulls (US-406) | Supply-chain and portable-media control: only vetted artifacts enter OT | A real plant would use scanned media through a **portable media kiosk**, not an SSH stream from an Internet-connected host |

### 2.2 Dual-NIC Level 3.5 boundary with IP forwarding disabled

| Aspect | Citadel implementation | Principle | Where it falls short of nuclear practice |
|--------|------------------------|-----------|------------------------------------------|
| PC #4 cannot route | IPv4 forwarding, IPEnableRouter, ICS, and RRAS disabled; no gateway on the OT NIC (`setup-firewall.ps1`) | Prevents the dual-homed host from becoming a bridge | A dual-homed host is a **known anti-pattern**. RG 5.71 expects a **hardware one-way device** between levels. |
| One data conduit | Only MQTT :1883 (and SSH :22 for deploy) from PC #4 → PC #3 | Zones and conduits (IEC 62443); least functionality | Traffic is **bidirectional TCP**, and **IT initiates the connection into OT**. That is the opposite of a data diode, where plant data only flows *out*. |
| PC #4 ↛ PC #2 | Blocked at both ends; deploys reach PC #2 through PC #3 (ProxyJump) | Defense-in-depth; respects Purdue layering (L4 → L3 → L1) | Remote software deployment into control systems would itself be prohibited or tightly controlled at a plant |
| Evidence-producing controls | `setup-firewall.ps1 -VerifyOnly` gives a PASS/FAIL report; the §7 test matrix includes negative tests | NEI 08-09 expects controls to be verified and records kept | Verification is manual and point-in-time; there's no continuous monitoring or IDS |

### 2.3 Pinned container configuration control

| Aspect | Citadel implementation | Principle | Where it falls short of nuclear practice |
|--------|------------------------|-----------|------------------------------------------|
| Version-pinned images | `eclipse-mosquitto:2.0.20`, `timescaledb:2.17.2-pg16`, `grafana-oss:11.3.0` | **Configuration management / baseline control** (73.54, NEI 08-09) | **Tags can be re-pointed.** True immutability needs **digest pinning** (`image@sha256:…`) plus a recorded SBOM. |
| Everything as code | Compose files, firewall rules, CI pipeline, and docs all in Git | Controlled baseline; every change goes through review and leaves an audit trail | No formal change-approval workflow (no branch protection or required reviews yet) |
| Line-ending control | `.gitattributes` forces LF line endings (`2669ca0`) | Same input gives the same build | — |
| Python dependencies | `requirements-dev.txt` uses `pytest>=8.3` | — | **Gap:** a version range, not a lock. Fix: pinned lockfile with hashes. |
| Test gate | Deploy `needs: test`; an empty test suite fails CI | Software QA: verification before release | No independent verification (NQA-1 expects reviewers independent of the designer) |

### 2.4 Dual-zone radiation monitoring

> **Definition used here:** the **same radiation data presented in two security zones with different authority**. The OT Control Room HMI (PC #3) is the **authoritative operational display**. Enterprise Grafana (PC #4) is a **read-only, informational copy**.
> **Current status:** the simulator emits `RAD_MON_01..03` (µSv/h). Zone tagging and alarming are planned (US-501). *If the PM means physical monitoring zones (e.g., reactor hall vs. helium loop area), the zone metadata can be added to the simulator tags.*

| Aspect | Citadel implementation | Principle | Where it falls short of nuclear practice |
|--------|------------------------|-----------|------------------------------------------|
| OT display is authoritative | Operators act on the FactoryTalk Optix HMI fed directly over OPC UA from the PLC (Sprint 4) | Safety and operational decisions stay inside the protected zone; never depend on IT-side systems | — |
| IT display is informational | Grafana reads the replicated MQTT → TimescaleDB copy | Information flows outward; enterprise users get visibility without control | A data diode would *guarantee* IT can't write back. Here it rests on firewall policy. |
| SSEP relevance | Radiation monitors feed emergency classification decisions | Under 73.54, assets supporting **Emergency Preparedness** functions can be **CDAs** | Lab doesn't do CDA identification or consequence analysis (e.g., NEI 13-10 approach) |
| Redundancy | Three monitor channels | **2-out-of-3 voting** is a standard way to tolerate a single failed channel without spurious trips (planned in US-501 alarms) | Real safety-related monitoring also needs qualified hardware, diversity, and independence |

---

## 3. QA Traceability Matrix (Sprint 1)

Requirement → implementation → verification → evidence. This chain is what Appendix B/NQA-1 auditors look for.

| Requirement | Implementation | Verification | Evidence |
|-------------|----------------|--------------|----------|
| US-103 reactor telemetry within bounds | `simulators/citadel_reactor.py` | 26 pytest tests (bounds, determinism, encoding) | CI `test` job green on `714099d` |
| US-105 no deploy without passing tests | `deploy.yml` `needs: test` | Pipeline structure; failed test blocks deploy jobs | Commit `c9ce76a`, CI history |
| US-105 controlled deploy enablement | `RUNNERS_READY` gate | Deploy jobs skip while unset | Commit `bae4c53` |
| US-104 L4 never reaches L0–1 | `setup-firewall.ps1` X1/X2 rules on PC #2 and PC #4 | `-WhatIf` dry run on all roles (done); `-VerifyOnly` + negative tests (US-406) | Commit `0135115`; **live evidence pending US-406** |
| US-104 PC #4 does not route | Forwarding/ICS/RRAS guards | `-VerifyOnly` PASS/FAIL report | **Pending US-406** |
| Configuration baseline | Pinned image tags, `.gitattributes` | Compose `config` validation | Commits `2669ca0`, `0135115` |

**Traceability chain in practice:** `agile/backlog.md` story → `agile/sprint-NN.md` acceptance criteria → commit (message references US-ID) → pytest / script output → GitHub Actions run → Session Log entry.

---

## 4. Known Gaps vs. Nuclear Practice (say these before someone else does)

| # | Gap | Production remedy |
|---|-----|-------------------|
| G1 | Bidirectional TCP across L3.5, with IT initiating | Hardware **data diode**, plant → corporate only; replicate historian data outward |
| G2 | `git push` deploys into OT | Controlled change process: approved package → scanned portable media / kiosk → manual install with verification |
| G3 | Dual-homed PC #4 as the boundary | Dedicated firewall pair or diode plus a DMZ historian or broker |
| G4 | Tag-pinned images, ranged Python deps | Digest pinning, hashed lockfiles, SBOM, signed images |
| G5 | Flat OT /24 | VLANs / separate switches per level; monitored conduits |
| G6 | No continuous monitoring | OT IDS (passive span port), centralized logs, periodic control reviews |
| G7 | No independent review | Branch protection + required reviewer; independent V&V for safety-relevant software |

---

## 5. 30-Second Talking Points

*Each answer is about 75 words, roughly 30 seconds spoken.*

**Q1. Walk me through your architecture.**
> "Four Windows PCs mapped to the Purdue Model. PC 2 is the plant floor, a reactor simulator and virtual PLC speaking Modbus and OPC UA. PC 3 is the control room with a FactoryTalk Optix HMI and an MQTT broker. Both sit on an air-gapped network with no Internet. PC 4 is enterprise IT. It's dual-homed with forwarding disabled, pulls telemetry over one MQTT conduit, and lands it in TimescaleDB and Grafana. PC 1 is my dev box; a git push drives everything."

**Q2. Is this NRC-compliant?**
> "No, and I'm deliberate about saying that. It's a lab that demonstrates the concepts behind 10 CFR 73.54: zoning, defense-in-depth, configuration control, and verified controls. I keep a written gap list. The big ones are that I use a dual-homed host instead of a hardware data diode, and that my IT side initiates connections into OT. A plant would never allow either. Knowing exactly where the lab differs is the point of the exercise."

**Q3. What's a Critical Digital Asset?**
> "Under 73.54, a CDA is a digital asset that supports SSEP functions: safety and important-to-safety, security, emergency preparedness including offsite communications, or the support systems those depend on. In my lab, the radiation monitors are the closest analogue. Radiation data feeds emergency classification, so in a real plant those channels would likely be CDAs, and they'd get the full set of technical and operational controls."

**Q4. Purdue Level 4 vs. NRC Level 4?**
> "They're opposites, and it's an easy trap. In Purdue, Level 4 is the enterprise business network, the least trusted inside the company. In NRC Reg Guide 5.71's defensive architecture, Level 4 is the most protected tier, where safety and security systems sit. Data can only leave those high levels through one-way deterministic devices. I map my PCs to Purdue, but I can translate to the NRC model."

**Q5. Why an air gap, and is it enough?**
> "The air gap removes the largest attack surface, any path to the Internet, so my OT PCs can't be reached remotely or call out. But it isn't enough on its own; Stuxnet crossed an air gap on USB media. So I also disable the router's Wi-Fi, build images outside OT, and in production I'd add portable media scanning kiosks and strict media controls. The air gap is one layer, not the whole defense."

**Q6. Why disable IP forwarding on PC 4?**
> "PC 4 has a network card in both worlds. If it routed, it would quietly become a bridge from the Internet into the plant, which defeats the whole design. So the setup script disables forwarding, the router registry key, connection sharing, and RRAS. It also leaves no gateway on the OT card. Then it verifies each item and prints PASS or FAIL, so I have evidence rather than assumptions."

**Q7. Why not a data diode?**
> "Cost and scope for a home lab. But I designed around the diode principle: OT never starts a connection into IT, PC 4 can only reach the control room, never the PLC, and that block is enforced on both ends. In production, I'd replace PC 4's role with a hardware diode, pushing historian data outward only. Firewall rules are a policy; a diode is a physics guarantee, and nuclear sites want the physics."

**Q8. How do you handle configuration management?**
> "Everything is code in Git: compose files, firewall rules, pipeline, docs. Container images are pinned to exact versions, so a rebuild gives the same thing. Every change leaves an audit trail. My known gap is that tags can be re-pointed upstream, so the next step is pinning by SHA-256 digest, adding a lockfile for Python dependencies, and generating an SBOM. That's the baseline control NEI 08-09 expects."

**Q9. How do you show QA traceability?**
> "Each requirement is a user story with checkbox acceptance criteria. Commits reference the story ID, tests verify the criteria, and the CI run is the evidence record. For example, US-103: the requirement for bounded, deterministic telemetry traces to the simulator module, 26 pytest tests, and a green CI run on a specific commit. That's the same requirement-to-evidence chain Appendix B and NQA-1 auditors look for."

**Q10. Why `git push` deployment, and would a plant allow that?**
> "It demonstrates modern DevOps discipline: tests gate every release, and nothing is deployed by hand. But a nuclear plant wouldn't allow a network push into control systems. Changes go through an approved package, scanned media, and a manual install with verification. I'm explicit that the pipeline shows the *discipline* of controlled release, and I'd adapt the delivery mechanism to the site's change control process."

**Q11. Why three radiation monitors?**
> "Redundancy. With three channels you can do two-out-of-three voting: one failed or noisy sensor can't cause a spurious trip or hide a real event. It's a standard pattern in safety instrumentation. Alarms are on my backlog. I also show the data in two zones: the control room HMI is authoritative, Grafana is a read-only copy. Decisions happen in OT; IT only gets visibility."

**Q12. What would you change first for production?**
> "Three things. First, replace the dual-homed PC with a data diode, so data only flows outward. Second, replace git-push deploys into OT with a controlled media-based change process. Third, add continuous monitoring: a passive OT intrusion detection sensor plus centralized logging. After that: digest-pinned images, VLAN segmentation of the OT network, and required independent review on every change. All of it is already listed in my gap register."

---

## 6. Demo Cheat-Sheet (60-second architecture pitch)

1. **Point at PC #2:** "Plant floor, Purdue L0–1: reactor physics and a virtual PLC."
2. **Point at PC #3 (34" screen):** "Control room, L2–3: operator HMI, the authoritative view."
3. **Point at the R8000:** "No WAN cable. The OT side has never touched the Internet."
4. **Point at PC #4:** "The only machine in both worlds. It doesn't route, and it can't reach the PLC. One conduit in."
5. **Turn to PC #1, `git push`:** "Tests gate every change; watch it propagate." *(Then say Q10's caveat before anyone asks.)*
