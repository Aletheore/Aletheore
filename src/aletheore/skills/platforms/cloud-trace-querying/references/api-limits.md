# API Limits and Constraints

This reference details the limits, quotas, and constraints when querying Cloud
Trace and Cloud Logging APIs. Note that limits are endpoint-specific and vary
between the v1 and v2 API versions.

## Cloud Trace API (v1) Quotas & Limits

1.  **Query Page Size / List Limits**:
    *   The v1 `projects.traces.list` API endpoint caps the maximum number of
        traces returned in a single request at 1000.
2.  **Rate Limits**:
    *   API v1 has standard rate-limiting quotas (e.g. read requests per minute
        per project). Consult the GCP Quotas page for your current project
        limits.
3.  **Data Retention**:
    *   Trace data is stored for 30 days. Queries looking back further than 30
        days will return empty results.
4.  **Filter Formatting Constraints**:
    *   Latency filters must be formatted as an integer value followed
        immediately by a unit suffix (`ms`, `s`, `m`). No spaces are allowed
        (e.g. `800ms`, not `800 ms`).

## Cloud Logging API Limits

1.  **Log Entry Retention**:
    *   Default retention is 30 days, but custom log buckets can be configured
        for longer retention periods (up to 3650 days).
2.  **Log Query Page Size**:
    *   Maximum page size for list log entries is 1000 entries.

## See Also

*   [Cloud Trace Quotas and Limits - Google Cloud docs](https://cloud.google.com/trace/docs/quotas)
*   [Cloud Logging Quotas and Limits - Google Cloud docs](https://cloud.google.com/logging/quotas)
*   [Cloud Observability Pricing - Google Cloud docs](https://cloud.google.com/stackdriver/pricing)
