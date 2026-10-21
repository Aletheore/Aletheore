# Filestore Monitoring Metrics

## Essential Filestore Storage Metrics
When determining if a Filestore instance needs scaling, query the Cloud Monitoring API for utilization.

### Used Capacity Metric

- **Metric Type**: `file.googleapis.com/nfs/server/used_bytes`
- **Description**: The amount of storage space currently utilized on the file share.
- **Aggregation**: It is recommended to use a 5-minute rolling average to smooth out transient spikes.

### Provisioned Capacity

-   **DO NOT fetch provisioned capacity or total_bytes from Cloud Monitoring**.
-   **Description**: The total provisioned storage capacity is identical to the
    instance capacity. You must read the `capacityGb` property directly from the
    instances returned by the `list_instances` MCP tool or the GCP API.

### Fetching Metrics Across Agent Runtimes

You must retrieve the `used_bytes` metric from the Cloud Monitoring API. Select
the method that matches your active runtime tools:

#### Option 1: GCP REST API Tool (e.g., `call_gcp_api` in Gemini Enterprise File Agent)

If your runtime provides a native GCP API execution tool, invoke it with:

-   **service**: "monitoring"
-   **version**: "v3"
-   **resource_path**: "projects/{project_id}/timeSeries"
-   **query_params**: `{"filter":
    "metric.type=\"file.googleapis.com/nfs/server/used_bytes\""}`

*(Note: Do NOT include `interval.startTime` or `interval.endTime` or aggregation
parameters; the tool calculates and attaches the latest 15-minute window
automatically).*

#### Option 2: `gcloud` CLI (Recommended for Terminal / Command-Line Agents)

If your agent environment has terminal/command execution enabled:

```bash
gcloud monitoring time-series list \
    --filter='metric.type="file.googleapis.com/nfs/server/used_bytes"' \
    --project="{project_id}" \
    --format="json"
```

#### Option 3: Direct HTTP REST (`curl`)

```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    "https://monitoring.googleapis.com/v3/projects/{project_id}/timeSeries?filter=metric.type%3D%22file.googleapis.com%2Fnfs%2Fserver%2Fused_bytes%22"
```

#### Option 4: Cloud Monitoring MCP

If an MCP server for Google Cloud Monitoring is mounted, use `list_time_series`
with:

-   `filter`: `metric.type="file.googleapis.com/nfs/server/used_bytes"`

--------------------------------------------------------------------------------

### Critical Metric Query Rules (Applies to All Methods)

1.  **NO LOCATION FILTERS REQUIRED:** Do NOT attempt to filter by zone, region,
    or location (e.g., `us-central1-a`). Filestore metric labels differ between
    zonal and regional tiers; querying at the project level avoids empty
    results.
2.  **ONE BULK QUERY PER PROJECT:** Always fetch metrics for the entire project
    in a single call to avoid per-instance agent looping and timeouts.
3.  **DO NOT FETCH `total_bytes`:** Read `capacityGb` directly from
    `list_instances`. Do not make secondary queries for provisioned capacity.
4.  **INSTANCE FILTER (IF SCOPED):** When filtering to a single instance, use
    `resource.labels.instance_name="{instance_name}"` (NOT `instance_id`).

## Calculation Formulas

- **Free Bytes**: `total_bytes - used_bytes`
- **Free Space Percentage**: `((total_bytes - used_bytes) / total_bytes) * 100`

Use these formulas when evaluating instances against the `max_threshold` and `min_threshold` safety factors.
