# Workflow: Find Correlated Logs

This workflow guides you through pulling application logs correlated with a
specific trace ID to troubleshoot runtime errors.

## Basic Workflow

1.  **Identify Trace / Span**: Search for failed requests or traces that
    returned error codes in a specific project and time range:

    ```bash
    # Location: scripts/search_traces/
    ./run.sh \
      --projects eval-project \
      --filter="status:error" \
      --start-time "2026-06-25T13:00:00Z" \
      --end-time "2026-06-25T14:00:00Z" \
      --limit=5
    ```

2.  **Fetch Logs**: Query GCP Logging client for entries associated with the
    trace ID, limiting the query to the same time range:

    ```bash
    # Location: scripts/fetch_related_logs/
    ./run.sh \
      --trace-projects eval-project \
      --trace-id=<TRACE_ID> \
      --start-time "2026-06-25T13:00:00Z" \
      --end-time "2026-06-25T14:00:00Z" \
      --limit=100
    ```

3.  **Pinpoint Errors**: Look for stack traces or error levels in the output
    logs.

## Advanced Details

### LogEntry Schema

Logs are queried and structured according to the Google Cloud Logging LogEntry
schema.

### Correlation Mechanisms

Logs are correlated with traces using two fields:

-   **`trace`**: The resource path of the trace (e.g.
    `projects/PROJECT_ID/traces/TRACE_ID`). In some telemetry configurations,
    this may appear without the prefix as just the raw `TRACE_ID`.
-   **`spanId`**: The hex string format of the span ID (e.g.
    `spanId="1234567890abcdef"`).

### Multi-Project Log Scopes

In distributed architectures, the GCP project receiving the trace telemetry (the
trace project) might be different from the project where the application writes
its log entries (the logs project).

-   If you do not see logs returned from the trace project, specify alternative
    logging projects using the `--log-projects` flag.
-   Observability Scopes can be inspected to identify which logging projects
    monitor the target trace projects.

## See Also

*   [Logs-to-Trace Correlation Concept - Trace Skill docs](./logs-trace-correlation.md)
*   [Observability Scopes Concept - Trace Skill docs](./observability-scopes.md)
*   [LogEntry REST API Reference - Google Cloud docs](https://cloud.google.com/logging/docs/reference/v2/rest/v2/LogEntry)
*   [Logging Query Language Reference - Google Cloud docs](https://cloud.google.com/logging/docs/view/logging-query-language)
*   [Finding Traces in Console - Google Cloud docs](https://cloud.google.com/trace/docs/finding-traces)
