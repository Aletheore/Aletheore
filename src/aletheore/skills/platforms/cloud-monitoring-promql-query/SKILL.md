---
name: cloud-monitoring-promql-query
metadata:
  version: 1.0.0
  category: CloudObservabilityAndMonitoring
description: Generates valid PromQL queries from Cloud Monitoring metric descriptors and resource parameters. Use when asked to create, generate, write, or format PromQL queries, PromQL strings, or PromQL aggregations for Cloud Monitoring metrics and resources. Don't use for raw metric discovery or metric selection.
skill_id: platforms/cloud-monitoring-promql-query
title: Cloud Monitoring Promql Query
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/cloud-monitoring-promql-query
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/cloud-monitoring-promql-query", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/cloud-monitoring-promql-query", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["skill-ref"], "companion_files": 4, "has_scripts": true}, "curated_on": "2026-10-18", "requires": ["mcp:cloud-monitoring", "scripts"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Cloud Monitoring PromQL Generator

Use this skill to generate a valid PromQL query from any Cloud Monitoring metric
type. This guide applies to all Cloud Monitoring metric types by mapping Cloud
Monitoring metric and resource descriptors to PromQL structures.

## Workflow

### Resolve Project ID (CRITICAL & BLOCKING)

Before performing any other actions (such as searching code, reading references,
or running validation), you MUST verify whether the Google Cloud Project ID is
available:

1.  **Check Prompt/Payload**: Look for the Project ID in the user's prompt or
    input.
2.  **Check Environment**: If the Project ID is not present in the prompt, you
    MUST run `gcloud config get-value project` to attempt to resolve it from the
    environment.
3.  **Ask for Clarification (BLOCKING)**: If the Project ID is not in the prompt
    AND the `gcloud` command fails, returns an empty string, or is unavailable,
    you MUST immediately stop. Do NOT generate a PromQL query, do not run the
    validation script, and do not use placeholders (like `YOUR_PROJECT_ID`). You
    must refuse to proceed and ask the user to provide the Project ID.

### Inspect Metric and Resource Descriptors

1.  **Use Provided Descriptors First**: If the user's prompt already includes
    metric descriptor details (such as `metric.type`, `metricKind`, `valueType`,
    or `monitoredResourceTypes`) or specific resource filter values, use those
    values directly instead of calling the Cloud Monitoring API.
2.  **Discover Missing Descriptors**: If exact metric descriptors
    (`metric.type`, `metricKind`, `valueType`) are missing or underspecified,
    resolve the target metric type's descriptor using one of these paths:
    *   **Vague Query**: If the prompt is vague (for example, `"VM CPU usage"`),
        use the `cloud-monitoring-metric-selection` skill first to identify the
        specific metric type.
    *   **Known Metric Type**: If you already have the specific metric type name
        (for example, `compute.googleapis.com/instance/cpu/utilization`) but
        need its descriptor, call the
        `google-cloud-monitoring:list_metric_descriptors` MCP tool. If the tool
        is missing, refer to the `cloud-monitoring-metric-selection` skill to
        configure the Cloud Monitoring MCP server.
    *   **Fallback**: If the MCP tool cannot be configured, fall back to making
        a direct Cloud Monitoring API call.
3.  **Identify Key Fields**: From the retrieved descriptor, identify four key
    schema attributes:
    *   **`type`**: The Cloud Monitoring metric type string.
    *   **`metricKind`**: `GAUGE`, `DELTA`, or `CUMULATIVE`.
    *   **`valueType`**: `INT64`, `DOUBLE`, `DISTRIBUTION`, or `BOOL`.
    *   **`monitoredResourceTypes`**: Compatible `resource.type` strings
        required for resource scoping and grouping.

### Resolve Resource Filters & Discovery Protocol

To filter data by a specific resource instance, apply these resource rules and
discovery protocols:

1.  **Monitored Resource Filter**: Always include the
    `monitored_resource="<type>"` filter in your query to prevent collisions
    across services that share metric names.
    *   **Example**: `monitored_resource="gae_app"`
2.  **Preserve User Literals (CRITICAL)**: ALWAYS use the literal resource
    names, namespaces, and IDs provided in the user's prompt. Do **NOT**
    override or replace these values with active resource names found during
    Cloud Monitoring discovery unless the user explicitly asked you to find
    active resources. Telemetry discovery must only be used to identify metric
    type names and label keys, not to override user input.
3.  **Resource Identifier Mapping**:
    *   **Direct & Specific Keys**: Use the most specific resource identifier
        available. **Example**: `version_id`, `cluster_name`.
    *   **Name-to-ID Resolution**: If the user filters by a resource *name*
        (such as `"instance-1"`), but the resource schema uses numeric IDs (like
        `instance_id`), use PromQL string name labels instead of numeric ID
        labels. **Example**: `instance_name`, `metadata_system_name`.
    *   **Composite Identifiers**: For resources with hierarchical identifiers
        (such as Cloud SQL databases), format the filter as a single composite
        key. Do NOT split them into separate `project_id` and sub-resource
        labels. **Example**: `database_id="{project_id}:{instance_name}"`.
4.  **Resource Label Discovery**: The
    `google-cloud-monitoring:list_metric_descriptors` tool only returns
    metric-specific labels. If the label schema for a monitored resource is
    unknown, fetch the resource descriptor directly from the Cloud Monitoring v3
    REST API (`projects.monitoredResourceDescriptors.get`):

    ```bash
    TOKEN=$(gcloud auth application-default print-access-token 2>/dev/null || gcloud auth print-access-token)
    curl -s -H "Authorization: Bearer ${TOKEN}" \
    "https://monitoring.googleapis.com/v3/projects/{project_id}/monitoredResourceDescriptors/{monitored_resource_type}"
    ```

    An HTTP 200 OK response returns the `MonitoredResourceDescriptor` object
    containing the `labels` array with the exact resource label keys for that
    resource.

### Choose Aggregation Structure & Defaults

The query structure and aggregation functions (such as `rate`,
`histogram_quantile`, `sum`, or `avg`) depend on the metric type and how it is
visualized.

1.  **Consult the Reference**: Consult the
    [Cloud Monitoring to PromQL Basic Aggregations Reference](references/basic_aggregations.md)
    as the single source of truth to map Cloud Monitoring properties (Metric
    Kind, Value Type, Aligner, Reducer) to their PromQL structures.
2.  **SRE Aggregation & Visualization Rules**:
    *   **Do NOT sum or average ratio/percentage utilization metrics** (like CPU
        % or Memory limit utilization) across resource instances. Instead, keep
        them unaggregated (raw metric), group by instance, or wrap in `topk(30,
        avg_over_time(...))`.
    *   **State Label Filtering (CRITICAL)**: Only the metrics
        `agent.googleapis.com/memory/percent_used` and
        `agent.googleapis.com/disk/percent_used` require `{state!="free"}`. Do
        **NOT** filter by `{state="used"}`.

### Format & Validate Query

Before presenting any PromQL queries, validate them using the linter:

#### Python Dependencies

Before executing the validation script (`scripts/validate_promql.py`), install
the required Python dependencies:

```bash
python3 -c "import promql_parser" || pip install promql-parser
```

#### Validation Procedure

1.  **Format Constraints**:
    *   **Metric Name Normalization**: Convert Cloud Monitoring metric types to
        PromQL metric names using this recipe:
        1.  **Split Domain and Path**: Split the Cloud Monitoring metric type by
            the first slash (`/`) to separate the domain from the path.
            *   **Example**:
                `storage.googleapis.com/network/received_bytes_count` -> domain
                `storage.googleapis.com`, path `network/received_bytes_count`
        2.  **Normalize Domain**: Replace all periods (`.`) in the domain with
            underscores (`_`).
            *   **Example**: `storage.googleapis.com` ->
                `storage_googleapis_com`
        3.  **Normalize Path**: Replace all periods (`.`) and slashes (`/`) in
            the path with underscores (`_`).
            *   **Example**: `network/received_bytes_count` ->
                `network_received_bytes_count`
        4.  **Join with Colon**: Join the normalized domain and normalized path
            with a colon (`:`).
            *   **Example**:
                `storage_googleapis_com:network_received_bytes_count`
        5.  **Native Prometheus Metrics**: If the metric type has no slash, keep
            it as-is.
            *   **Example**: `up` -> `up`, `http_requests_total` ->
                `http_requests_total`
        6.  **Distribution Suffix**: If the metric's `valueType` is
            `DISTRIBUTION`, append `_bucket` to the end of the normalized name.
            *   **Example**:
                `cloudfunctions.googleapis.com/function/execution_times` ->
                `cloudfunctions_googleapis_com:function_execution_times_bucket`
    *   Ensure the final query is a **single line with no comments** (no `#` or
        `//`). Cloud Monitoring query translation collapses whitespace and can
        cause code trailing a comment to be ignored or throw parsing errors.
    *   **Grouping Clause Syntax**: Ensure grouping clauses (such as `by
        (label)`) only follow aggregation operators (such as `sum`, `avg`,
        `min`, `max`, or `count`). Never place a grouping clause directly after
        a metric selector.
        *   **Incorrect**: `metric{...} by (label)`
        *   **Correct**: `sum(rate(metric{...}[5m])) by (label)`
    *   **Fenced Output Code Block**: ALWAYS wrap the final verified PromQL
        query in a fenced `promql` code block in your final response.
2.  **Linter Verification**:
    *   Validate all generated queries in a single batch: `python3
        <path_to_skill>/scripts/validate_promql.py --query '<q1>' '<q2>'`
    *   If validation fails, read
        [PromQL Error Recovery Guide](references/promql_error_recovery.md) to
        diagnose and fix common type mismatches and syntax errors before
        repeating the loop.

## References

*   [Cloud Monitoring PromQL Basic Aggregations Reference](references/basic_aggregations.md)
*   [Cloud Monitoring PromQL Error Recovery Guide](references/promql_error_recovery.md)
*   [Cloud Monitoring PromQL Documentation](https://docs.cloud.google.com/monitoring/promql.md.txt)
*   [Cloud Monitoring Monitored Resource Types Reference](https://docs.cloud.google.com/monitoring/api/resources.md.txt)
