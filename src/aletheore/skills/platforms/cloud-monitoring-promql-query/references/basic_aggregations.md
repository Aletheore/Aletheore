# Cloud Monitoring to PromQL Basic Aggregations Reference

This document maps Cloud Monitoring metric concepts to their PromQL query
equivalents. Use this matrix to determine the correct PromQL query structure and
default aggregation functions based on Cloud Monitoring metric properties.

## Table of Contents

-   [Translation Matrix](#translation-matrix) (~Line 26)
-   [Default Aggregations and Visualization Rules](#default-aggregations-and-visualization-rules)
    (~Line 112)
    -   [CPU and Memory Utilization (Ratios / Percentages)](#cpu-and-memory-utilization-ratios-percentages)
        (~Line 117)
    -   [Rate of Events / Throughput (Counters)](#rate-of-events-throughput-counters)
        (~Line 139)
    -   [Distribution Metrics (Quantiles / Latency)](#distribution-metrics-quantiles-latency)
        (~Line 156)
    -   [Boolean & Status Metrics (BOOL Value-Type)](#boolean-status-metrics-bool-value-type)
        (~Line 167)
    -   [Ranking & Sorting (Top N / Bottom N)](#ranking-sorting-top-n-bottom-n)
        (~Line 179)
    -   [Sampling Intervals vs. Rate Windows ([5m] vs. Coarser Metrics)](#sampling-intervals-vs-rate-windows-5m-vs-coarser-metrics)
        (~Line 189)
    -   [Backlog Age & Processing Lag](#backlog-age-processing-lag) (~Line 204)

## Translation Matrix

To use this matrix:

-   **Inputs**:
    -   Identify the metric type's `metricKind` and `valueType` from its
        `MetricDescriptor`.
    -   Infer the target calculation goal from the user's prompt (for example,
        Sum, Mean, 95th Percentile) under **Aggregation Intent**.
-   **Output PromQL Structure**:
    -   Extract the template from the **PromQL Query Structure** column for the
        matching row.
    -   Replace `<metric>` with the translated metric name (for example, mapping
        `compute.googleapis.com/instance/cpu/utilization` to
        `compute_googleapis_com:instance_cpu_utilization`).

When the user's aggregation preference is underspecified, use the row marked
**(default)**.

| Metric Kind  | Value Type     | Aggregation Intent  | PromQL Query Structure          |
| :----------- | :------------- | :------------------ | :------------------------------ |
| `GAUGE`      | `NUMERIC`      | Raw / Unaggregated  | `<metric>`                      |
:              : (`INT64` /     : (default for        :                                 :
:              : `DOUBLE`)      : ratios/utilization) :                                 :
| `GAUGE`      | `NUMERIC`      | **Sum (default for  | `sum(<metric>)`                 |
:              :                : counts/gauges)**    :                                 :
| `GAUGE`      | `NUMERIC`      | Mean / Average      | `avg(<metric>)`                 |
| `GAUGE`      | `NUMERIC`      | Min                 | `min(<metric>)`                 |
| `GAUGE`      | `NUMERIC`      | Max                 | `max(<metric>)`                 |
| `GAUGE`      | `NUMERIC`      | Count time series   | `count(<metric>)`               |
| `GAUGE`      | `NUMERIC`      | 99th percentile     | `quantile(0.99, <metric>)`      |
| `GAUGE`      | `NUMERIC`      | 95th percentile     | `quantile(0.95, <metric>)`      |
| `GAUGE`      | `NUMERIC`      | 50th percentile     | `quantile(0.50, <metric>)`      |
:              :                : (Median)            :                                 :
| `GAUGE`      | `DISTRIBUTION` | **Distribution      | `sum(rate(<metric>_bucket[5m])) |
:              :                : Buckets (default)** : by (le, <group_labels>)`        :
| `GAUGE`      | `DISTRIBUTION` | 99th percentile     | `histogram_quantile(0.99,       |
:              :                :                     : sum(rate(<metric>_bucket[5m]))  :
:              :                :                     : by (le, <group_labels>))`       :
| `GAUGE`      | `DISTRIBUTION` | 95th percentile     | `histogram_quantile(0.95,       |
:              :                :                     : sum(rate(<metric>_bucket[5m]))  :
:              :                :                     : by (le, <group_labels>))`       :
| `GAUGE`      | `DISTRIBUTION` | 50th percentile     | `histogram_quantile(0.50,       |
:              :                : (Median)            : sum(rate(<metric>_bucket[5m]))  :
:              :                :                     : by (le, <group_labels>))`       :
| `GAUGE`      | `BOOL`         | Raw / Unaggregated  | `<metric>`                      |
:              :                : Points              :                                 :
| `GAUGE`      | `BOOL`         | **Fraction true /   | `avg(<metric>) * 100` *(or      |
:              :                : Availability        : `avg(<metric>)` in `[0.0,       :
:              :                : (default)**         : 1.0]`)*                         :
| `GAUGE`      | `BOOL`         | Count true          | `sum(<metric>)`                 |
| `GAUGE`      | `BOOL`         | Filter Unhealthy /  | `<metric> == 0`                 |
:              :                : Down                :                                 :
| `DELTA` /    | `NUMERIC`      | Raw / Per-Series    | `rate(<metric>[5m])`            |
: `CUMULATIVE` : (`INT64` /     : Rate                :                                 :
:              : `DOUBLE`)      :                     :                                 :
| `DELTA` /    | `NUMERIC`      | **Sum Rate /        | `sum(rate(<metric>[5m]))`       |
: `CUMULATIVE` :                : Throughput          :                                 :
:              :                : (default)**         :                                 :
| `DELTA` /    | `NUMERIC`      | Mean Rate           | `avg(rate(<metric>[5m]))`       |
: `CUMULATIVE` :                :                     :                                 :
| `DELTA` /    | `NUMERIC`      | Min Rate            | `min(rate(<metric>[5m]))`       |
: `CUMULATIVE` :                :                     :                                 :
| `DELTA` /    | `NUMERIC`      | Max Rate            | `max(rate(<metric>[5m]))`       |
: `CUMULATIVE` :                :                     :                                 :
| `DELTA` /    | `NUMERIC`      | 99th percentile     | `quantile(0.99,                 |
: `CUMULATIVE` :                : Rate                : rate(<metric>[5m]))`            :
| `DELTA` /    | `NUMERIC`      | 95th percentile     | `quantile(0.95,                 |
: `CUMULATIVE` :                : Rate                : rate(<metric>[5m]))`            :
| `DELTA` /    | `NUMERIC`      | Total Cumulative    | `sum(increase(<metric>[1h]))`   |
: `CUMULATIVE` :                : Volume (over 1h)    :                                 :
| `DELTA` /    | `DISTRIBUTION` | **Distribution      | `sum(rate(<metric>_bucket[5m])) |
: `CUMULATIVE` :                : Buckets (default)** : by (le, <group_labels>)`        :
| `DELTA` /    | `DISTRIBUTION` | 99th percentile     | `histogram_quantile(0.99,       |
: `CUMULATIVE` :                : Latency             : sum(rate(<metric>_bucket[5m]))  :
:              :                :                     : by (le, <group_labels>))`       :
| `DELTA` /    | `DISTRIBUTION` | 95th percentile     | `histogram_quantile(0.95,       |
: `CUMULATIVE` :                : Latency             : sum(rate(<metric>_bucket[5m]))  :
:              :                :                     : by (le, <group_labels>))`       :
| `DELTA` /    | `DISTRIBUTION` | 50th percentile     | `histogram_quantile(0.50,       |
: `CUMULATIVE` :                : Latency (Median)    : sum(rate(<metric>_bucket[5m]))  :
:              :                :                     : by (le, <group_labels>))`       :
| `ANY`        | `ANY`          | Top N Outliers      | `topk(<N>, <metric_or_rate>)`   |
| `ANY`        | `ANY`          | Bottom N Outliers   | `bottomk(<N>,                   |
:              :                :                     : <metric_or_rate>)`              :

## Default Aggregations and Visualization Rules

Apply these standard defaults when generating queries or when the user's
aggregation preference is underspecified:

### CPU and Memory Utilization (Ratios / Percentages)

*   **Use when**: Querying CPU or memory utilization metrics (ratios or
    percentages) for any service or agent (for example,
    `compute_googleapis_com:instance_cpu_utilization` or
    `agent_googleapis_com:memory_percent_used`).
*   **Default Query Structure**: Reference the metric name directly without
    wrapping it in an aggregator (such as `avg` or `sum`) to return all
    individual time series (unaggregated). This is the preferred default to
    monitor all instances.
    *   **Example**:
        `agent_googleapis_com:memory_percent_used{monitored_resource="gce_instance",
        state!="free"}`
*   **Aggregation Constraints**:
    *   **No Summing**: Do NOT use `sum()`. Utilization metrics represent ratios
        or percentages; summing them yields mathematically invalid values.
    *   **No Averaging for Resource Limits**: Avoid averaging utilization across
        instances (such as `avg(metric)`) for resource limits (like memory
        limits or disk space utilization) because it masks outliers. Instead,
        group by the resource instance (such as `by (pod_name)`) or use
        `topk(30, ...)` to highlight outliers.

### Rate of Events / Throughput (Counters)

*   **Use when**: Querying `DELTA` or `CUMULATIVE` event counter metrics (for
    example, request count or disk bytes count).
*   **Throughput Rule**: Convert event counts to a rate by applying `rate(...)`
    over a window (default `[5m]`).
    *   *Default*: Sum the rates across relevant groupings:
        `sum(rate(compute_googleapis_com:instance_disk_read_bytes_count{...}[5m]))`
*   **Volume Rule (`increase` vs `rate`)**: Use `rate()` for per-second
    throughput (such as requests per second). Use `increase()` when the user
    asks for total cumulative event volume over a specific time window.
    *   **Example**: Total errors in the last 1h: `sum(increase(<metric>[1h]))`
*   **DELTA vs. CUMULATIVE Handling**: Trust Cloud Monitoring PromQL to
    automatically handle both `DELTA` and `CUMULATIVE` metric kinds natively
    when using `rate()`, `irate()`, or `increase()`. Do not perform manual
    type-casting.

### Distribution Metrics (Quantiles / Latency)

*   **Use when**: Querying `DISTRIBUTION` metrics (such as request latencies).
*   **Rule**: Append `_bucket` to the metric name, wrap it in
    `histogram_quantile()`, and ensure you group by the `le`
    (less-than-or-equal) bucket boundary label.
*   **Default Query Structure**:
    *   **Example (p95)**: `histogram_quantile(0.95,
        sum(rate(spanner_googleapis_com:query_latency_bucket{...}[5m])) by (le,
        instance_id))`

### Boolean & Status Metrics (`BOOL` Value-Type)

*   **Use when**: Querying `BOOL` value-type metrics (such as endpoint health or
    binary uptime checks).
*   **Rule**: Represent boolean metrics numerically where `1` is true/up and `0`
    is false/down.
*   **Default Query Structures**:
    *   *Filter for Unhealthy/Down Instances*: `<metric> == 0`
    *   *Count Healthy Instances*: `sum(<metric>)`
    *   *Cluster Availability Percentage*: `avg(<metric>) * 100` (or
        `avg(<metric>)` for `[0.0, 1.0]`)

### Ranking & Sorting (Top N / Bottom N)

*   **Use when**: Identifying the highest- or lowest-consuming instances
    (commonly used for sorting).
*   **Rule**: Use `topk()` or `bottomk()` to filter results.
*   **Default Query Structure**: Wrap the rate or gauge in the ranking function.
    *   **Example (Top 10)**: `topk(10, avg_over_time(<metric>[5m]))`
    *   **Example (Top 5 rate)**: `topk(5, sum(rate(<metric>[5m])) by
        (instance_id))`

### Sampling Intervals vs. Rate Windows (`[5m]` vs. Coarser Metrics)

*   **Use when**: Determining the correct range window (for example, `[5m]`) for
    rate functions.
*   **Rule**: Ensure the rate window spans at least **twice the scrape
    interval** (sampling period) of the underlying metric to avoid chart gaps
    and zero-scraping artifacts.
*   **Default Query Structure**:
    *   Use `[5m]` as a safe default for standard 1-minute resolution metrics.
    *   Increase the rate window (for example, to `[15m]` or `[30m]`) for
        coarser metrics (emitted every 5m or 10m) to cover at least 2
        consecutive samples.
    *   Use `[${__interval}]` as the default range window if the sampling period
        is unknown.

### Backlog Age & Processing Lag

*   **Use when**: Querying metrics tracking maximum age, lag, or oldest
    unacknowledged items (for example,
    `pubsub.googleapis.com/subscription/oldest_unacked_message_age` or
    `dataflow.googleapis.com/job/system_lag`).
*   **Rule**: Use `max()` to aggregate across instances to surface the peak
    delay. Do NOT use `sum()` or `avg()`.
*   **Default Query Structure**:
    *   **Example**:
        `max(pubsub_googleapis_com:subscription_oldest_unacked_message_age{monitored_resource="pubsub_subscription"})`
