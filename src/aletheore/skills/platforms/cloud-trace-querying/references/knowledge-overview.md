# GCP Trace Querying Knowledge Base

This index documents the core concepts, query utilities, and telemetry
conventions for GCP distributed tracing.

## 1. When and Why to Look at Trace Data

Distributed traces provide a visualization of request execution flows across
microservices. Look at trace data to:

*   Identify latency bottlenecks in slow request paths.
*   Pinpoint the service or operation causing request failures.
*   Analyze execution paths and span hierarchies in distributed systems.

## 2. Ways to Query Trace

Trace data can be searched and retrieved using multiple methods. By default,
agents should use the supplied `search_traces` script or the MCP endpoints.

For detailed criteria on which method to select, see the
[Trace Querying Decision Tree](./decision-tree.md).

*   [Cloud Trace API (v1) Query Filter Syntax](./filter-syntax.md):
    Filter queries for trace search.
*   [MCP Trace Querying Tools](./querying-mcp.md): Automated tools exposed
    to the agent environment.
*   [Observability Analytics (BigQuery SQL)](./querying-observability-analytics.md):
    Querying exported traces and logs using SQL in BigQuery.

## 3. How and Why to Look at Correlated Logs

Correlated logs connect individual log entries with the request context (traces
and spans).

*   **Why**: Correlating logs helps you inspect detailed execution logs, error
    payloads, and intermediate state for the exact duration of a specific span.
*   **How**: Use the `fetch_related_logs` script to retrieve log entries stamped
    with `trace` and `spanId` context fields.
*   **Workflow Guide**: See
    [Workflow: Find Correlated Logs](./correlated-logs.md) for
    step-by-step instructions.

## 4. Concepts and Conventions

Deep dives into tracing structures and instrumentation standards:

*   [Distributed Traces](./concepts-trace.md): Overview of trace structures and
    identifiers.
*   [Spans](./concepts-span.md): Spans, parent-child relationships, and
    attributes.
*   [Observability Scopes](./observability-scopes.md): Scope-based
    multi-project telemetry query routing.
*   [Trace Span Conventions](./conventions.md): OpenTelemetry (OTel), GCP,
    and legacy span attributes.
*   [API Limits and Constraints](./api-limits.md): Quotas, retention limits, and
    endpoint constraints.

## See Also

*   [Cloud Observability Docs - Google Cloud docs](https://cloud.google.com/stackdriver/docs)
*   [Cloud Trace Overview - Google Cloud docs](https://docs.cloud.google.com/trace/docs)
*   [Trace API Reference - Google Cloud docs](https://docs.cloud.google.com/trace/docs/reference)
*   [Trace Client Libraries - Google Cloud docs](https://docs.cloud.google.com/trace/docs/client-libraries)
*   [Finding Traces Using Filters - Google Cloud docs](https://docs.cloud.google.com/trace/docs/trace-filters)
*   [Trace Span Labels - Google Cloud docs](https://docs.cloud.google.com/trace/docs/trace-labels)
*   [Trace Schema Reference - Google Cloud docs](https://docs.cloud.google.com/trace/docs/reference/trace-schema)
*   [Traces Concept - OpenTelemetry docs](https://opentelemetry.io/docs/concepts/signals/traces/)
*   [Semantic Conventions - OpenTelemetry docs](https://opentelemetry.io/docs/concepts/semantic-conventions/)
