---
name: distributed-tracing
description: Implement distributed tracing with Jaeger and Tempo to track requests across microservices and identify performance bottlenecks. Use when debugging microservices, analyzing request flows, or implementing observability for distributed systems.
skill_id: reliability/distributed-tracing
title: Distributed Tracing
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/observability-monitoring/skills/distributed-tracing
license: MIT
shelf: ship-operate
aletheore: {"id": "reliability/distributed-tracing", "shelf": "ship-operate", "category": "reliability", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/observability-monitoring/skills/distributed-tracing", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "tracing", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Distributed Tracing

Implement distributed tracing with Jaeger and Tempo for request flow visibility across microservices.

## Purpose

Track requests across distributed systems to understand latency, dependencies, and failure points.

## When to Use

- Debug latency issues
- Understand service dependencies
- Identify bottlenecks
- Trace error propagation
- Analyze request paths

## Detailed patterns and worked examples

Detailed pattern documentation lives in `references/details.md`. Read that file when the navigation tier above is insufficient.

## Best Practices

1. **Sample appropriately** (1-10% in production)
2. **Add meaningful tags** (user_id, request_id)
3. **Propagate context** across all service boundaries
4. **Log exceptions** in spans
5. **Use consistent naming** for operations
6. **Monitor tracing overhead** (<1% CPU impact)
7. **Set up alerts** for trace errors
8. **Implement distributed context** (baggage)
9. **Use span events** for important milestones
10. **Document instrumentation** standards

## Integration with Logging

### Correlated Logs

```python
import logging
from opentelemetry import trace

logger = logging.getLogger(__name__)

def process_request():
    span = trace.get_current_span()
    trace_id = span.get_span_context().trace_id

    logger.info(
        "Processing request",
        extra={"trace_id": format(trace_id, '032x')}
    )
```

## Troubleshooting

**No traces appearing:**

- Check collector endpoint
- Verify network connectivity
- Check sampling configuration
- Review application logs

**High latency overhead:**

- Reduce sampling rate
- Use batch span processor
- Check exporter configuration


## Related Skills

- `prometheus-configuration` - For metrics
- `grafana-dashboards` - For visualization
- `slo-implementation` - For latency SLOs
