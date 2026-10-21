---
name: threat-mitigation-mapping
description: Map identified threats to appropriate security controls and mitigations. Use when prioritizing security investments, creating remediation plans, or validating control effectiveness.
skill_id: security/threat-mitigation-mapping
title: Threat Mitigation Mapping
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/security-scanning/skills/threat-mitigation-mapping
license: MIT
shelf: quality
aletheore: {"id": "security/threat-mitigation-mapping", "shelf": "quality", "category": "security", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/security-scanning/skills/threat-mitigation-mapping", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "threat mitigation", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Threat Mitigation Mapping

Connect threats to controls for effective security planning.

## When to Use This Skill

- Prioritizing security investments
- Creating remediation roadmaps
- Validating control coverage
- Designing defense-in-depth
- Security architecture review
- Risk treatment planning

## Core Concepts

### 1. Control Categories

```
Preventive ────► Stop attacks before they occur
   │              (Firewall, Input validation)
   │
Detective ─────► Identify attacks in progress
   │              (IDS, Log monitoring)
   │
Corrective ────► Respond and recover from attacks
                  (Incident response, Backup restore)
```

### 2. Control Layers

| Layer           | Examples                             |
| --------------- | ------------------------------------ |
| **Network**     | Firewall, WAF, DDoS protection       |
| **Application** | Input validation, authentication     |
| **Data**        | Encryption, access controls          |
| **Endpoint**    | EDR, patch management                |
| **Process**     | Security training, incident response |

### 3. Defense in Depth

```
                    ┌──────────────────────┐
                    │      Perimeter       │ ← Firewall, WAF
                    │   ┌──────────────┐   │
                    │   │   Network    │   │ ← Segmentation, IDS
                    │   │  ┌────────┐  │   │
                    │   │  │  Host  │  │   │ ← EDR, Hardening
                    │   │  │ ┌────┐ │  │   │
                    │   │  │ │App │ │  │   │ ← Auth, Validation
                    │   │  │ │Data│ │  │   │ ← Encryption
                    │   │  │ └────┘ │  │   │
                    │   │  └────────┘  │   │
                    │   └──────────────┘   │
                    └──────────────────────┘
```

## Templates and detailed worked examples

Full template library and detailed mitigation/control mappings live in `references/details.md`. Read that file when you need the concrete templates for: Mitigation Model, Defense in Depth scoring, Executive Summary scaffolding, Critical Gaps reporting, Recommendations, Implementation Roadmap, Results by Control.

## Best Practices

### Do's

- **Map all threats** - No threat should be unmapped
- **Layer controls** - Defense in depth is essential
- **Mix control types** - Preventive, detective, corrective
- **Track effectiveness** - Measure and improve
- **Review regularly** - Controls degrade over time

### Don'ts

- **Don't rely on single controls** - Single points of failure
- **Don't ignore cost** - ROI matters
- **Don't skip testing** - Untested controls may fail
- **Don't set and forget** - Continuous improvement
- **Don't ignore people/process** - Technology alone isn't enough
