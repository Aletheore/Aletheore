# Concept: Observability Scopes

Observability Scopes, Metrics Scopes, Log Scopes, and Trace Scopes are distinct
but related concepts in Google Cloud used to manage and query telemetry data
across project boundaries.

## Scope Types

1.  **Observability Scopes**: A control-plane resource that groups projects
    together for a unified operations view. It acts as the coordinator that
    connects the different types of scopes (Log Scopes, Trace Scopes, Metrics
    Scopes) together. The default observability scope is named `_Default`.
2.  **Log Scopes**: Groups resource containers (projects, folders,
    organizations) for Cloud Logging. It allows querying logs across all grouped
    projects.
3.  **Trace Scopes**: Groups resource containers for Cloud Trace, allowing
    unified trace exploration.
4.  **Metrics Scopes**: Groups resource containers for Cloud Monitoring metrics
    (independent from Logging/Trace scopes).

## Scope Resolution Directions

### Trace -> Logs Resolution

When querying logs correlated with a trace in a given project, resolve the
scoped logging projects via the following sequence:

1.  **Get Default Observability Scope**:

    -   Query: `GET
        https://observability.googleapis.com/v1/projects/{project_id}/locations/global/scopes/_Default`
    -   Retrieve the `logScope` resource name from the response.

2.  **Get Log Scope Details**:

    -   Query: `GET https://logging.googleapis.com/v2/{log_scope_name}`
    -   Retrieve the list of scoped resource paths from the `resourceNames` list
        field (e.g. `projects/{monitored_project_id}`).

### Logs -> Trace Resolution

When fetching a trace given a log with tracing information (e.g., when the
`projects/[PROJECT_ID]/traces/` prefix is missing from the log entry), look up
the Trace Scopes of candidate projects to resolve the candidate values of
`[PROJECT_ID]`:

1.  **Query default observability scope**:

    -   Query: `GET
        https://observability.googleapis.com/v1/projects/{project_id}/locations/global/scopes/_Default`
    -   Retrieve the `traceScope` resource name from the response.

2.  **Get Trace Scope details**:

    -   Query: `GET https://observability.googleapis.com/v1/{trace_scope_name}`
    -   Parse candidate project IDs from the `resourceNames` list field (e.g.
        `projects/{monitored_project_id}`).

## See Also

*   [Observability Scopes Overview - Google Cloud docs](https://docs.cloud.google.com/stackdriver/docs/observability/scopes)
*   [Observability Scopes REST API Reference - Google Cloud docs](https://docs.cloud.google.com/stackdriver/docs/reference/observability/api/rest/v1/projects.locations.scopes)
*   [Create and Manage Log Scopes - Google Cloud docs](https://docs.cloud.google.com/logging/docs/log-scope/create-and-manage)
*   [Create and Manage Trace Scopes - Google Cloud docs](https://docs.cloud.google.com/trace/docs/trace-scope/create-and-manage)
*   [Create and Manage Metrics Scopes - Google Cloud docs](https://docs.cloud.google.com/monitoring/settings/multiple-projects.md.txt)
