# Logs-to-Trace Correlation

This document details how logs and traces are correlated in Google Cloud.

---

## Correlation Fields

Google Cloud Logging automatically parses and maps log entries to traces when specific attributes are set:

1. **`logging.googleapis.com/trace`**:
   - Stores the full resource path of the trace: `projects/[PROJECT_ID]/traces/[TRACE_ID]`
   - Allows Cloud Logging to correlate log entries with a specific trace.
2. **`logging.googleapis.com/spanId`**:
   - Stores the active span ID as a hexadecimal string.
   - Allows correlation of log entries with a specific span within the trace.

---

## Fetching Correlated Logs

The `fetch_related_logs` script automates log retrieval by constructing a query filter:
- Standard Filter: `trace =~ "projects/.*/traces/{trace_id}"`
- Span Filter: `trace =~ "projects/.*/traces/{trace_id}" AND spanId="{span_id}"`

This script queries logs across all relevant projects (including log projects resolved via Observability Scopes) and consolidates results sorted by timestamp.

---

## See Also

* [Structured Logging Special Fields - Google Cloud docs](https://cloud.google.com/logging/docs/structured-logging#special-payload-fields)
* [Viewing Correlated Logs in Trace Details - Google Cloud docs](https://cloud.google.com/trace/docs/viewing-details#viewing_logs)
* [Correlation with Traces - OpenTelemetry docs](https://opentelemetry.io/docs/specs/otel/logs/#correlation-with-traces)
* [LogEntry REST API Reference - Google Cloud docs](https://cloud.google.com/logging/docs/reference/v2/rest/v2/LogEntry)
* [Logging Query Language Reference - Google Cloud docs](https://cloud.google.com/logging/docs/view/logging-query-language)
