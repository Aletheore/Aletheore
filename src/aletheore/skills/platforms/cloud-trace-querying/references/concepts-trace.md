# Concept: Distributed Traces

A distributed trace represents the end-to-end execution path of a transaction or
request as it flows through multiple microservices, hosts, or cloud services.

## Key Fields

*   **Trace ID**: A globally unique 32-character hexadecimal string representing
    the transaction (conforming to the
    [W3C Trace Context Specification](https://www.w3.org/TR/trace-context/)).
    All spans within a single transaction share the same Trace ID.
*   **Span Collection**: A trace is formed by a directed acyclic graph (DAG) of
    [spans](./concepts-span.md) representing individual operations.
*   **Cross-Boundary Analysis**: Traces allow engineers to track RPC boundaries,
    measure latency across hops, and isolate failures (see
    [Troubleshoot Slow Requests Workflow](./troubleshoot-latency.md)).

## See Also

*   [Traces and Spans Concept - Google Cloud docs](https://docs.cloud.google.com/trace/docs/traces-and-spans)
*   [Cloud Trace Documentation - Google Cloud docs](https://cloud.google.com/trace/docs)
*   [Traces Concept - OpenTelemetry docs](https://opentelemetry.io/docs/concepts/signals/traces/)
*   [W3C Trace Context Specification - W3C spec](https://www.w3.org/TR/trace-context/)
*   [Trace API Reference - Google Cloud docs](https://cloud.google.com/trace/docs/reference)
