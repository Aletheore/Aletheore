# GCP Trace Querying References

This master index catalogs all reference documentation, conventions, concepts, and workflows for Google Cloud Trace querying.

## Workflows
- [Troubleshoot Slow Requests](./troubleshoot-latency.md): Step-by-step procedure for root-causing high latency in distributed traces.
- [Find Correlated Logs](./correlated-logs.md): Workflow for finding and inspecting Cloud Logging entries correlated by Trace ID.
- [Trace Querying Decision Tree](./decision-tree.md): Guide for selecting the optimal query tool (Trace API v1 vs. MCP vs. SQL Analytics).
- [Console Deep Links](./console-deep-links.md): Instructions for constructing shareable Google Cloud Console URLs for traces.
- [Workflows Overview](./workflows-overview.md): High-level overview of operational workflows.

## Query Syntax & Tools
- [Trace Query Filter Syntax](./filter-syntax.md): Complete reference for Cloud Trace API v1 filter operators (`root:`, `latency:`, `+span:`, `error:true`).
- [API Limits and Constraints](./api-limits.md): Quotas, timestamp restrictions, filter limits, and batch sizes.
- [Observability Analytics (BigQuery SQL)](./querying-observability-analytics.md): Querying distributed trace logs and metrics using BigQuery SQL.
- [Cloud Trace MCP Tools](./querying-mcp.md): Querying traces via Model Context Protocol (MCP) server tools.

## Concepts & Architecture
- [Observability Scopes](./observability-scopes.md): Managing cross-project telemetry visibility and `_Default` observability scopes.
- [Distributed Traces](./concepts-trace.md): Conceptual overview of trace identifiers, timelines, and root spans.
- [Spans](./concepts-span.md): Anatomical breakdown of spans, parent-child hierarchies, and labels.
- [Logs-to-Trace Correlation](./logs-trace-correlation.md): How trace contexts are injected and correlated in Cloud Logging entries.
- [Knowledge Overview](./knowledge-overview.md): Comprehensive index of core knowledge topics.

## Semantic Conventions
- [Conventions Overview](./conventions.md): Master guide for span naming, HTTP status mapping, and label conventions.
- [OpenTelemetry Core](./conventions-otel.md): Standard W3C and OpenTelemetry attribute schemas.
- [OpenTelemetry HTTP](./conventions-otel-http.md): HTTP method, target, status code, and route conventions.
- [OpenTelemetry gRPC & RPC](./conventions-otel-grpc.md): RPC service, method, and status code conventions.
- [OpenTelemetry GenAI](./conventions-otel-genai.md): Semantic conventions for LLM requests, prompts, token counts, and completions.
- [GCP App Hub & Service Conventions](./conventions-gcp.md): Canonical Google Cloud service and application metadata labels.
- [GCP Legacy Labels](./conventions-gcp-legacy.md): Backward-compatible legacy Google Cloud Trace span attributes.
