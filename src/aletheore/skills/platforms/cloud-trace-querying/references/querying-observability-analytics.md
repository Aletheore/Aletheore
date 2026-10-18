# Querying Traces via Observability Analytics

Observability Analytics allows you to perform SQL queries directly on your Cloud
Trace data.

## When to Use Observability Analytics

*   **Aggregate Metrics**: Calculating aggregate percentiles (p95, p99),
    averages, or error rates across thousands of request traces.
*   **Complex Filtering**: Filtering traces by multiple complex conditions,
    pattern matching on JSON attributes, or joining trace data with logging
    buckets.
*   **Cross-Service Reports**: Building dashboards in Looker Studio or querying
    from external big data tools.

By default, for individual trace investigations or simple searches, prefer using
the **Local Script Utilities** or **MCP Tools** because they are faster and
consume fewer tokens.

## Linked Datasets in BigQuery

To query trace data outside the Google Cloud Console (e.g. from BigQuery Studio,
Looker, or standard client libraries), you must create a **linked dataset** in
BigQuery.

### 1. Checking for Existing Linked Datasets

Before creating a new linked dataset, check if one has already been set up in your BigQuery project:

1.  List datasets in your BigQuery project to check for a dataset associated with logs/traces (often named `_Trace` or `observability_analytics` or the custom name chosen during creation):
    ```bash
    gcloud alpha bq datasets list --project="your-project-id"
    ```
2.  Verify that the `_AllSpans` view exists and is queryable in that dataset (replace `LINK_ID` with the dataset name found):
    ```bash
    gcloud alpha bq tables describe "_AllSpans" \
      --dataset="LINK_ID" \
      --project="your-project-id"
    ```

### 2. How to Create a Linked Dataset

If no dataset exists, you can link the system trace dataset to BigQuery using `gcloud` (replace `LINK_ID` with your desired BigQuery dataset name, and adjust `--location` if your bucket is regionalized):

```bash
gcloud logging links create "LINK_ID" \
  --bucket="_Trace" \
  --location="global" \
  --project="your-project-id"
```
This creates a read-only dataset in BigQuery that links directly to the underlying trace data store.

## Schema Reference

The system-defined view `_Trace.Spans._AllSpans` has the following schema
structure:

| Field Path       | SQL Type    | Description                        |
| :--------------- | :---------- | :--------------------------------- |
| `trace_id`       | `STRING`    | The 128-bit unique identifier for  |
:                  :             : the trace (hex format).            :
| `span_id`        | `STRING`    | The 64-bit unique identifier for   |
:                  :             : the span (hex format).             :
| `parent_span_id` | `STRING`    | The identifier of the parent span. |
| `name`           | `STRING`    | The display name of the span       |
:                  :             : (operation name).                  :
| `kind`           | `INT64`     | Span kind (e.g., `2` for           |
:                  :             : `SPAN_KIND_SERVER`, `3` for        :
:                  :             : `SPAN_KIND_CLIENT`).               :
| `start_time`     | `TIMESTAMP` | Start timestamp of the span.       |
| `end_time`       | `TIMESTAMP` | End timestamp of the span.         |
| `status`         | `RECORD`    | Status record containing `code`    |
:                  :             : (INT64) and `message` (STRING).    :
| `attributes`     | `JSON`      | Key-value attributes associated    |
:                  :             : with the span (e.g.                :
:                  :             : `attributes.http.request.method`). :
| `resource`       | `RECORD`    | Resource record containing         |
:                  :             : `attributes` (JSON) of the         :
:                  :             : executing resource.                :

## Example SQL Queries

### 1. List Spans for a Specific Trace ID

```sql
SELECT
  span_id,
  parent_span_id,
  name,
  start_time,
  end_time,
  TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS duration_ms
FROM
  `your-project-id._Trace.Spans._AllSpans`
WHERE
  trace_id = '3c114a8bb37e74bb032c7ae9566b85d0'
ORDER BY
  start_time ASC;
```

### 2. Find Spans with Errors

```sql
SELECT
  trace_id,
  span_id,
  name,
  status.message
FROM
  `your-project-id._Trace.Spans._AllSpans`
WHERE
  status.code != 0
LIMIT 100;
```

### 3. Extract JSON Attribute Values

To extract nested JSON values from the `attributes` field:

```sql
SELECT
  trace_id,
  span_id,
  name,
  LAX_STRING(attributes.http.request.method) AS http_method,
  LAX_INT64(attributes.http.response.status_code) AS status_code
FROM
  `your-project-id._Trace.Spans._AllSpans`
WHERE
  LAX_STRING(attributes.http.request.method) IS NOT NULL
LIMIT 100;
```

## See Also

*   [Trace SQL Analytics - Google Cloud docs](https://cloud.google.com/trace/docs/analytics)
*   [BigQuery Linked Datasets Introduction - Google Cloud docs](https://cloud.google.com/bigquery/docs/analytics-hub-introduction#linked_datasets)
