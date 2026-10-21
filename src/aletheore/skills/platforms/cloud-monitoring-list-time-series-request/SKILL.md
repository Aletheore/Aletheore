---
name: cloud-monitoring-list-time-series-request
metadata:
  version: 1.0.0
  category: CloudObservabilityAndMonitoring
description: Generates valid Cloud Monitoring ListTimeSeries requests and aggregation specifications from metric descriptors and resource parameters. Use when asked to create, generate, format, or build ListTimeSeries requests, JSON payloads, filter expressions, or aligner/reducer aggregations for Cloud Monitoring metrics and charts. Don't use for metric discovery or metric selection.
skill_id: platforms/cloud-monitoring-list-time-series-request
title: Cloud Monitoring List Time Series Request
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/cloud-monitoring-list-time-series-request
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/cloud-monitoring-list-time-series-request", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/cloud-monitoring-list-time-series-request", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["skill-ref"], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["mcp:cloud-monitoring"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Cloud Monitoring ListTimeSeries Request Generator

Use this skill to translate any Cloud Monitoring metric descriptor into valid,
production-ready `ListTimeSeries` REST API query parameters (`name`, `filter`,
`interval.startTime`, `interval.endTime`, `aggregation.*`, `view`).

## CRITICAL RULES

*   **Mandatory Project ID Clarification**: You MUST ensure the GCP Project ID
    is present in the user prompt, input payload, or environment context (such
    as via `gcloud config get-value project`). If the Project ID is missing and
    cannot be resolved, you MUST ask the user to clarify it before generating or
    executing `ListTimeSeries` requests. Do NOT use placeholders for project
    names.

## Workflow

### Inspect Metric Metadata

1.  **Use Provided Metric Metadata First**: If the user's prompt already
    includes metric metadata such as `metric.type`, `metricKind`, `valueType`,
    resource types, or label keys, use those values directly instead of calling
    API tools.
2.  **Discover Missing Metadata**: If exact metric descriptors including
    `metric.type`, `metricKind`, and `valueType` are missing or underspecified,
    resolve the target metric's descriptor using one of these paths:
    *   **Vague Query**: If the prompt is vague, such as asking for VM CPU
        usage, use the `cloud-monitoring-metric-selection` skill first to
        identify the specific metric type.
    *   **Known Metric Type**: If you already have the specific metric type name
        such as `compute.googleapis.com/instance/cpu/utilization`, but need its
        descriptor, call the `list_metric_descriptors` MCP tool. If the tool is
        missing, refer to the `cloud-monitoring-metric-selection` skill to
        configure the Cloud Monitoring MCP server.
    *   **Fallback**: If the MCP tool cannot be configured, fall back to making
        a direct Cloud Monitoring API call.
3.  **Identify Key Fields**: From the retrieved descriptor, identify key schema
    attributes:
    *   **`type`**: The Cloud Monitoring metric type string.
    *   **`metricKind`**: `GAUGE`, `DELTA`, or `CUMULATIVE`.
    *   **`valueType`**: `INT64`, `DOUBLE`, `DISTRIBUTION`, or `BOOL`.
    *   **`monitoredResourceTypes`**: Compatible `resource.type` strings, for
        example `["cloudsql_database", "cloudsql_instance"]`. If multiple
        resource types are listed, select the specific `resource.type` that
        matches the target granularity of the user's request.

--------------------------------------------------------------------------------

### Construct Monitoring Filter

The `filter` parameter is a mandatory string in Cloud Monitoring syntax that
restricts the query to a single `metric.type` and optional resource and metric
labels:

1.  **Single Metric Type Restriction**: Every `filter` MUST specify exactly one
    `metric.type` clause using an equality operator. For example:
    *   `metric.type = "compute.googleapis.com/instance/cpu/utilization"`
2.  **Monitored Resource Type Filter**: MUST include the `resource.type` filter
    when the target resource granularity is known, preventing collisions across
    services that share metric types or sub-resources. For example:
    *   `metric.type = "cloudsql.googleapis.com/database/cpu/utilization" AND
        resource.type = "cloudsql_database"`
3.  **Preserve User Literals and IDs**: You MUST use literal resource names,
    IDs, zones, and project parameters provided by the user without alteration.
    Do NOT override or replace user-specified identifiers with active resources
    found during metric metadata discovery unless explicitly requested.

4.  **Label Type Prefixing**:

    *   Prefix resource-level dimensions, such as instance ID, zone, project,
        database ID, or subscription ID, with the `resource.labels.` prefix. For
        example:
        *   `resource.labels.instance_id = "123456789"`
        *   `resource.labels.database_id = "my-project:my-instance"`
    *   Prefix metric-level dimensions, such as state, command, response code,
        or instance name metadata when stored on the metric, with the
        `metric.labels.` prefix. For example:
        *   `metric.labels.state != "free"`
        *   `metric.labels.instance_name = "instance-1"`

5.  **Resource Name versus ID Resolution**:

    *   If the user specifies a human-readable GCE VM instance name such as
        `"instance-1"`, but `resource.labels.instance_id` expects a numeric ID,
        you MUST filter using either `metric.labels.instance_name =
        "instance-1"` or `metadata.system_labels.name = "instance-1"`.
    *   Do NOT use `resource.metadata.name` or `resource.metadata.*`. This
        prefix is invalid in Cloud Monitoring filter syntax.
    *   Do NOT assign a string instance name directly to
        `resource.labels.instance_id` unless the resource type explicitly uses
        string IDs.

6.  **Database Identifier Labels**: Database labels such as `database_id` for
    Cloud SQL and Spanner, or `dataset_id` for BigQuery, use composite keys
    formatted as `<project_id>:<instance_name>`. For example:
    `resource.labels.database_id = "my-project:foo"`.

7.  **Ops Agent Metrics State Label Filtering**: For
    `agent.googleapis.com/memory/percent_used` and
    `agent.googleapis.com/disk/percent_used` metrics, you MUST use
    `metric.labels.state != "free"`. Do NOT filter by `metric.labels.state =
    "used"`.

--------------------------------------------------------------------------------

### Choose Aggregation Structure

Select the `perSeriesAligner`, `crossSeriesReducer`, `groupByFields`, and
`alignmentPeriod` according to the metric properties and visualization goal:

1.  **Consult the Aggregations Reference**: You MUST include both
    `perSeriesAligner` and `crossSeriesReducer` in the `aggregation` query
    parameters of every request. Read and follow the
    [Cloud Monitoring ListTimeSeries Basic Aggregations Reference](references/basic_aggregations.md)
    to select the exact `perSeriesAligner` and `crossSeriesReducer` combinations
    for your metric's Metric Kind and Value Type pairing, and to apply mandatory
    SRE rules for utilization metrics, counters, distributions, and state-based
    gauges such as memory filtered by `state != "free"`.
2.  **Grouping Fields and Resource Granularity**: When `crossSeriesReducer` is
    specified as anything other than `REDUCE_NONE`, list the exact labels to
    preserve. When querying multi-instance resources like VMs, databases, or
    subscriptions, include the primary resource identifier in `groupByFields`.
    For example, use `resource.labels.instance_id` for VMs or
    `resource.labels.database_id` for databases. This prevents collapsing
    separate resource streams into a single global aggregate.
3.  **Alignment Period Determination**: Calculate the query lookback duration
    from `endTime` minus `startTime`, ensuring `startTime` precedes `endTime`.
    If `endTime <= startTime`, flag an error before computing duration. Set
    `alignmentPeriod` according to Cloud Console default fine granularity
    standards:
    *   **Duration <= 110 minutes**: Set `alignmentPeriod = "60s"`.
    *   **Duration <= 23 hours**: Set `alignmentPeriod = "300s"`.
    *   **Duration <= 6 days**: Set `alignmentPeriod = "3600s"`.
    *   **Duration <= 23 days**: Set `alignmentPeriod = "10800s"`.
    *   **Duration <= 80 days**: Set `alignmentPeriod = "21600s"`.
    *   **Duration <= 180 days**: Set `alignmentPeriod = "43200s"`.
    *   **Duration <= 350 days**: Set `alignmentPeriod = "86400s"`.
    *   **Duration <= 500 days**: Set `alignmentPeriod = "172800s"`.
    *   **Omission Rule**: `alignmentPeriod` is omitted only when
        `perSeriesAligner` is set to `ALIGN_NONE`.

--------------------------------------------------------------------------------

### Format Valid Request

Present the generated `ListTimeSeries` REST query parameters. For example:

```json
{
  "name": "projects/<project_id>",
  "filter": "metric.type = \"<metric_type>\" AND resource.type = \"<resource_type>\"",
  "interval": {
    "startTime": "<iso_8601_start>",
    "endTime": "<iso_8601_end>"
  },
  "aggregation": {
    "alignmentPeriod": "60s",
    "perSeriesAligner": "ALIGN_RATE",
    "crossSeriesReducer": "REDUCE_SUM",
    "groupByFields": [
      "resource.labels.zone"
    ]
  },
  "view": "FULL"
}
```

*   **Aggregation Requirements**: Populate the `aggregation` parameters with the
    `perSeriesAligner`, `crossSeriesReducer`, `alignmentPeriod`, and optional
    `groupByFields` values determined during aggregation selection.
*   **Interval Requirements**: `startTime` and `endTime` MUST be valid RFC 3339
    and ISO 8601 timestamps such as `"YYYY-MM-DDTHH:MM:SSZ"`. If not explicitly
    provided by the user, dynamically compute a one-hour lookback interval
    ending at the current time, where `endTime` is the present moment and
    `startTime` is one hour prior. Do NOT hardcode static dates from examples.
*   **Alignment Period Requirement**: Determine `alignmentPeriod` from the
    lookback duration of `endTime` minus `startTime` using the mapping above.
    For the default one-hour lookback interval, `alignmentPeriod` is `"60s"`.
*   **View Requirement**: MUST default to `"FULL"` when time series data points
    are needed, or `"HEADERS"` when inspecting metadata and series identities
    only.

--------------------------------------------------------------------------------

### Validate Request via list_timeseries MCP Tool

You MUST validate the generated request parameters against live Cloud Monitoring
telemetry before returning the final output. Call the `list_timeseries` MCP tool
passing all generated query parameters (`name`, `filter`, `interval`,
`aggregation`). When validating you MUST set `view="HEADERS"` to minimize
latency and payload size while verifying request structure. A response without
API errors confirms that your filter and aggregation settings are valid.

If the `list_timeseries` tool is unavailable, fall back to a direct API call.

--------------------------------------------------------------------------------

## References

*   [Cloud Monitoring ListTimeSeries Basic Aggregations Reference](references/basic_aggregations.md)
*   [Cloud Monitoring Monitored Resource Types Reference](https://docs.cloud.google.com/monitoring/api/resources.md.txt)
*   [Cloud Monitoring Filter Syntax](https://docs.cloud.google.com/monitoring/api/v3/filters.md.txt)
*   [Cloud Monitoring REST API Reference: projects.timeSeries.list](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.timeSeries/list.md.txt)
