---
name: service-mesh-observability
description: Implement comprehensive observability for service meshes including distributed tracing, metrics, and visualization. Use when setting up mesh monitoring, debugging latency issues, or implementing SLOs for service communication.
skill_id: reliability/service-mesh-observability
title: Service Mesh Observability
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/cloud-infrastructure/skills/service-mesh-observability
license: MIT
shelf: ship-operate
aletheore: {"id": "reliability/service-mesh-observability", "shelf": "ship-operate", "category": "reliability", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/cloud-infrastructure/skills/service-mesh-observability", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "mesh observability", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Service Mesh Observability

Complete guide to observability patterns for Istio, Linkerd, and service mesh deployments.

## When to Use This Skill

- Setting up distributed tracing across services
- Implementing service mesh metrics and dashboards
- Debugging latency and error issues
- Defining SLOs for service communication
- Visualizing service dependencies
- Troubleshooting mesh connectivity

## Core Concepts

### 1. Three Pillars of Observability

```
┌─────────────────────────────────────────────────────┐
│                  Observability                       │
├─────────────────┬─────────────────┬─────────────────┤
│     Metrics     │     Traces      │      Logs       │
│                 │                 │                 │
│ • Request rate  │ • Span context  │ • Access logs   │
│ • Error rate    │ • Latency       │ • Error details │
│ • Latency P50   │ • Dependencies  │ • Debug info    │
│ • Saturation    │ • Bottlenecks   │ • Audit trail   │
└─────────────────┴─────────────────┴─────────────────┘
```

### 2. Golden Signals for Mesh

| Signal         | Description               | Alert Threshold   |
| -------------- | ------------------------- | ----------------- |
| **Latency**    | Request duration P50, P99 | P99 > 500ms       |
| **Traffic**    | Requests per second       | Anomaly detection |
| **Errors**     | 5xx error rate            | > 1%              |
| **Saturation** | Resource utilization      | > 80%             |

## Templates and detailed worked examples

Full template library and detailed worked examples live in `references/details.md`. Read that file when you need the concrete templates.

## Best Practices

### Do's

- **Sample appropriately** - 100% in dev, 1-10% in prod
- **Use trace context** - Propagate headers consistently
- **Set up alerts** - For golden signals
- **Correlate metrics/traces** - Use exemplars
- **Retain strategically** - Hot/cold storage tiers

### Don'ts

- **Don't over-sample** - Storage costs add up
- **Don't ignore cardinality** - Limit label values
- **Don't skip dashboards** - Visualize dependencies
- **Don't forget costs** - Monitor observability costs
