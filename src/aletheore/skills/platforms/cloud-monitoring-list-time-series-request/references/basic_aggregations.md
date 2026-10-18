# Cloud Monitoring ListTimeSeries Basic Aggregations Reference

This document maps Cloud Monitoring Aligner and Reducer concepts to structured
`ListTimeSeries` (`list_timeseries`) REST request fields. Use this matrix and
default rules to determine the correct `aggregation` query parameters
(`aggregation.perSeriesAligner`, `aggregation.crossSeriesReducer`, and
`aggregation.groupByFields`) based on Cloud Monitoring metric properties
(`metricKind` and `valueType`) and desired calculation goals.

## Table of Contents

-   [Translation Matrix](#translation-matrix) (~Line 26)
-   [Default Aggregations and Visualization Rules](#default-aggregations-and-visualization-rules)
    (~Line 74)
    -   [1. CPU and Memory Utilization (Ratios / Percentages)](#1-cpu-and-memory-utilization-ratios--percentages)
        (~Line 80)
    -   [2. Rate of Events / Throughput (Counters)](#2-rate-of-events--throughput-counters)
        (~Line 99)
    -   [3. Distribution Metrics (Quantiles / Latency)](#3-distribution-metrics-quantiles--latency)
        (~Line 114)
    -   [4. Boolean & Status Metrics (BOOL Value Type)](#4-boolean--status-metrics-bool-value-type)
        (~Line 132)
    -   [5. Backlog Age & Processing Lag (ALIGN_MAX / REDUCE_MAX)](#5-backlog-age--processing-lag-align_max--reduce_max)
        (~Line 143)

## Translation Matrix

To use this matrix:

-   **Inputs**: Identify the metric's `metricKind` and `valueType` from its
    `MetricDescriptor`, then infer the target calculation goal from the user's
    prompt (e.g., Mean, Sum, 95th Percentile) under **Aggregation Intent** to
    select the matching `perSeriesAligner` and `crossSeriesReducer`.
-   **Output Aggregation Fields (MANDATORY)**: Extract BOTH `perSeriesAligner`
    AND `crossSeriesReducer` values from the table below to populate the
    `aggregation` query parameters (`aggregation.perSeriesAligner` and
    `aggregation.crossSeriesReducer`) in your `ListTimeSeries` REST request.
    Every request MUST specify both `perSeriesAligner` and `crossSeriesReducer`.

Metric Kind            | Value Type                     | Aggregation Intent          | `perSeriesAligner`    | `crossSeriesReducer`
:--------------------- | :----------------------------- | :-------------------------- | :-------------------- | :-------------------
`GAUGE`                | `NUMERIC` (`INT64` / `DOUBLE`) | None / Raw Points           | `ALIGN_MEAN`          | `REDUCE_NONE`
`GAUGE`                | `NUMERIC`                      | Mean                        | `ALIGN_MEAN`          | `REDUCE_MEAN`
`GAUGE`                | `NUMERIC`                      | Min                         | `ALIGN_MIN`           | `REDUCE_MIN`
`GAUGE`                | `NUMERIC`                      | Max                         | `ALIGN_MAX`           | `REDUCE_MAX`
`GAUGE`                | `NUMERIC`                      | **Sum (default)**           | `ALIGN_MEAN`          | `REDUCE_SUM`
`GAUGE`                | `NUMERIC`                      | Count time series           | `ALIGN_MEAN`          | `REDUCE_COUNT`
`GAUGE`                | `NUMERIC`                      | 99th percentile             | `ALIGN_MEAN`          | `REDUCE_PERCENTILE_99`
`GAUGE`                | `NUMERIC`                      | 95th percentile             | `ALIGN_MEAN`          | `REDUCE_PERCENTILE_95`
`GAUGE`                | `NUMERIC`                      | 50th percentile             | `ALIGN_MEAN`          | `REDUCE_PERCENTILE_50`
`GAUGE`                | `NUMERIC`                      | 5th percentile              | `ALIGN_MEAN`          | `REDUCE_PERCENTILE_05`
`GAUGE`                | `DISTRIBUTION`                 | **Distribution (default)**  | `ALIGN_SUM`           | `REDUCE_SUM`
`GAUGE`                | `DISTRIBUTION`                 | Mean                        | `ALIGN_SUM`           | `REDUCE_MEAN`
`GAUGE`                | `DISTRIBUTION`                 | 99th percentile             | `ALIGN_PERCENTILE_99` | `REDUCE_NONE` / `REDUCE_PERCENTILE_99`
`GAUGE`                | `DISTRIBUTION`                 | 95th percentile             | `ALIGN_PERCENTILE_95` | `REDUCE_NONE` / `REDUCE_PERCENTILE_95`
`GAUGE`                | `DISTRIBUTION`                 | 50th percentile             | `ALIGN_PERCENTILE_50` | `REDUCE_NONE` / `REDUCE_PERCENTILE_50`
`GAUGE`                | `BOOL`                         | None / Raw Points           | `ALIGN_FRACTION_TRUE` | `REDUCE_NONE`
`GAUGE`                | `BOOL`                         | **Fraction true (default)** | `ALIGN_FRACTION_TRUE` | `REDUCE_MEAN`
`GAUGE`                | `BOOL`                         | Count true                  | `ALIGN_FRACTION_TRUE` | `REDUCE_SUM`
`DELTA` / `CUMULATIVE` | `NUMERIC` (`INT64` / `DOUBLE`) | None / Raw Points           | `ALIGN_RATE`          | `REDUCE_NONE`
`DELTA` / `CUMULATIVE` | `NUMERIC`                      | **Sum (default)**           | `ALIGN_RATE`          | `REDUCE_SUM`
`DELTA` / `CUMULATIVE` | `NUMERIC`                      | Mean                        | `ALIGN_RATE`          | `REDUCE_MEAN`
`DELTA` / `CUMULATIVE` | `NUMERIC`                      | Min                         | `ALIGN_RATE`          | `REDUCE_MIN`
`DELTA` / `CUMULATIVE` | `NUMERIC`                      | Max                         | `ALIGN_RATE`          | `REDUCE_MAX`
`DELTA` / `CUMULATIVE` | `NUMERIC`                      | 99th percentile             | `ALIGN_RATE`          | `REDUCE_PERCENTILE_99`
`DELTA` / `CUMULATIVE` | `NUMERIC`                      | 95th percentile             | `ALIGN_RATE`          | `REDUCE_PERCENTILE_95`
`DELTA` / `CUMULATIVE` | `NUMERIC`                      | 50th percentile             | `ALIGN_RATE`          | `REDUCE_PERCENTILE_50`
`DELTA` / `CUMULATIVE` | `DISTRIBUTION`                 | **Distribution (default)**  | `ALIGN_DELTA`         | `REDUCE_SUM`
`DELTA` / `CUMULATIVE` | `DISTRIBUTION`                 | Mean                        | `ALIGN_DELTA`         | `REDUCE_MEAN`
`DELTA` / `CUMULATIVE` | `DISTRIBUTION`                 | 99th percentile             | `ALIGN_PERCENTILE_99` | `REDUCE_NONE` / `REDUCE_PERCENTILE_99`
`DELTA` / `CUMULATIVE` | `DISTRIBUTION`                 | 95th percentile             | `ALIGN_PERCENTILE_95` | `REDUCE_NONE` / `REDUCE_PERCENTILE_95`
`DELTA` / `CUMULATIVE` | `DISTRIBUTION`                 | 50th percentile             | `ALIGN_PERCENTILE_50` | `REDUCE_NONE` / `REDUCE_PERCENTILE_50`

## Default Aggregations and Visualization Rules

Apply these standard defaults when constructing `ListTimeSeries` REST query
specifications for charts, dashboards, or when the user's aggregation preference
is underspecified:

### 1. CPU and Memory Utilization (Ratios / Percentages)

*   **Use when**: Querying CPU or memory utilization metrics (ratios or
    percentages) for any service or agent (e.g.,
    `compute.googleapis.com/instance/cpu/utilization` or
    `agent.googleapis.com/memory/percent_used`).
*   **Default Aggregation Directive**: For CPU and memory utilization metrics,
    default to `perSeriesAligner = ALIGN_MEAN` and `crossSeriesReducer =
    REDUCE_NONE`. If needed, group specifically by instance, such as
    `groupByFields = ["resource.labels.instance_id"]`.
*   **Aggregation Constraints**:
    *   **No Cross-Series Summing**: Do NOT use `crossSeriesReducer =
        REDUCE_SUM`. Utilization metrics represent ratios or percentages;
        summing them across instances yields invalid percentages over 100%.
    *   **No Cross-Series Averaging for Resource Limits**: Averaging utilization
        across instances (`crossSeriesReducer = REDUCE_MEAN`) masks severe
        outliers. For example, one instance crashing at 100% while others sit
        idle at 0%.

### 2. Rate of Events / Throughput (Counters)

*   **Use when**: Querying `DELTA` or `CUMULATIVE` event counter metrics.
*   **Throughput Rule**: You MUST convert `DELTA` or `CUMULATIVE` metrics
    representing event counts (`INT64` / `DOUBLE`) to a rate by setting
    `perSeriesAligner = ALIGN_RATE`.
*   **Cross-Series Reducer**: Use `crossSeriesReducer = REDUCE_SUM` when
    combining throughput across instances (e.g., total read bytes per second
    across all VMs in a zone).
    *   *Example*: `perSeriesAligner = ALIGN_RATE`, `crossSeriesReducer =
        REDUCE_SUM`, `alignmentPeriod = "300s"`.
*   **Removal of Transform Functions**: Do NOT apply multi-layer transform
    aligners. Apply `perSeriesAligner = ALIGN_RATE` and `crossSeriesReducer =
    REDUCE_SUM` cleanly in a single primary aggregation query.

### 3. Distribution Metrics (Quantiles / Latency)

*   **Use when**: Querying `DISTRIBUTION` metrics (such as request latencies).
*   **Rule**: For `DISTRIBUTION` metrics, such as Cloud Run request latencies
    (`run.googleapis.com/request_latency/e2e_latencies`) or Pub/Sub ack
    latencies (`pubsub.googleapis.com/subscription/ack_latencies`):
    *   To retrieve raw histogram bucket distributions across instances, use
        `perSeriesAligner = ALIGN_DELTA` (for `DELTA`/`CUMULATIVE`) or
        `perSeriesAligner = ALIGN_SUM` (for `GAUGE`) with `crossSeriesReducer =
        REDUCE_SUM`.
    *   To extract specific percentile latency gauges directly via the API, use
        percentile aligners: `perSeriesAligner = ALIGN_PERCENTILE_99`,
        `perSeriesAligner = ALIGN_PERCENTILE_95`, `perSeriesAligner =
        ALIGN_PERCENTILE_50`, or `perSeriesAligner = ALIGN_PERCENTILE_05`. When
        reduction across instances is requested, combine with the matching
        reducer (e.g., `perSeriesAligner = ALIGN_PERCENTILE_95`,
        `crossSeriesReducer = REDUCE_PERCENTILE_95`).

### 4. Boolean & Status Metrics (`BOOL` Value Type)

*   **Use when**: Querying `BOOL` value type metrics.
*   **Default Aggregations**:
    *   *Fraction True / Availability*: `perSeriesAligner =
        ALIGN_FRACTION_TRUE`, `crossSeriesReducer = REDUCE_MEAN` returns the
        fraction of healthy instances in `[0.0, 1.0]`.
    *   *Count True*: `perSeriesAligner = ALIGN_FRACTION_TRUE`,
        `crossSeriesReducer = REDUCE_SUM` returns the total count of healthy
        instances (`INT64`).

### 5. Backlog Age & Processing Lag (`ALIGN_MAX` / `REDUCE_MAX`)

*   **Use when**: Querying metrics tracking maximum age, lag, or oldest
    unacknowledged items.
*   **Rule**: For metrics tracking the maximum age, lag, or oldest
    unacknowledged item across services or workers (e.g.,
    `pubsub.googleapis.com/subscription/oldest_unacked_message_age` or
    `dataflow.googleapis.com/job/system_lag`), always default to
    `perSeriesAligner = ALIGN_MAX` and `crossSeriesReducer = REDUCE_MAX` to
    surface peak delays across instances.
