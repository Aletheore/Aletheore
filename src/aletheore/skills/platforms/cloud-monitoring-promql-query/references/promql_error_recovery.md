# PromQL Error Recovery Guide

When `validate_promql.py` or Cloud Monitoring query execution reports a syntax
or type error, use this reference to diagnose and fix the query before repeating
the validation loop.

## Table of Contents

-   [Common Error Diagnoses & Solutions](#common-error-diagnoses-solutions)
    (~Line 24)
    -   [Range Vector vs. Instant Vector Type Mismatch](#range-vector-vs-instant-vector-type-mismatch)
        (~Line 26)
    -   [Invalid Grouping Clause Placement (`by` / `without`)](#invalid-grouping-clause-placement-by-without)
        (~Line 45)
    -   [Histogram Quantile Bucket & Label Mismatch](#histogram-quantile-bucket-label-mismatch)
        (~Line 63)
    -   [Vector Matching & Binary Operator Collision](#vector-matching-binary-operator-collision)
        (~Line 81)
    -   [Unquoted Literals & Equality Operators](#unquoted-literals-equality-operators)
        (~Line 99)
    -   [Trailing Comments or Multiline Collisions](#trailing-comments-or-multiline-collisions)
        (~Line 115)

## Common Error Diagnoses & Solutions

### Range Vector vs. Instant Vector Type Mismatch

*   **Error**: `expected type range vector in call to function "rate", got
    instant vector` (or `irate`, `increase`).
*   **Cause**: Calling `rate()` without a time duration window (such as
    `rate(metric)`), or placing an aggregation operator inside the rate (such as
    `rate(sum(metric)[5m])`). In PromQL, functions like `sum()` or `avg()`
    return an instant vector, which cannot be passed to `rate()`.
*   **Fix**: Ensure `rate()`, `irate()`, and `increase()` wrap the raw metric
    with a duration window, and place aggregation operators on the outside:

    ```
    # Wrong
    rate(sum(compute_googleapis_com:instance_network_sent_bytes_count)[5m])

    # Correct
    sum(rate(compute_googleapis_com:instance_network_sent_bytes_count[5m]))
    ```

### Invalid Grouping Clause Placement (`by` / `without`)

*   **Error**: `syntax error: unexpected BY` or `invalid label grouping`.
*   **Cause**: Attaching a grouping clause directly to a raw metric selector or
    rate function. In PromQL, `by (...)` and `without (...)` can only attach to
    aggregation operators (`sum`, `avg`, `min`, `max`, `count`, `topk`,
    `bottomk`, `quantile`, `stddev`, `stdvar`).
*   **Fix**: Move the grouping clause to attach directly to the aggregation
    operator:

    ```
    # Wrong
    rate(bigtable_googleapis_com:server_read_count[5m]) by (table_id)

    # Correct
    sum(rate(bigtable_googleapis_com:server_read_count[5m])) by (table_id)
    ```

### Histogram Quantile Bucket & Label Mismatch

*   **Error**: `histogram_quantile requires a "le" label in the input vector` or
    empty/NaN distribution calculation.
*   **Cause**: Using `histogram_quantile()` without grouping by the `le`
    (less-than-or-equal) bucket boundary label, or forgetting to append the
    `_bucket` suffix to the distribution metric name.
*   **Fix**: Append `_bucket` to the metric name and ensure `le` is included in
    the `by (...)` clause alongside any target instance labels:

    ```
    # Wrong
    histogram_quantile(0.95, sum(rate(cloudfunctions_googleapis_com:function_execution_times[5m])) by (function_name))

    # Correct
    histogram_quantile(0.95, sum(rate(cloudfunctions_googleapis_com:function_execution_times_bucket[5m])) by (le, function_name))
    ```

### Vector Matching & Binary Operator Collision

*   **Error**: `multiple matches for labels: many-to-one matching must be
    explicit (group_left/group_right)`.
*   **Cause**: Performing arithmetic between two different time series (for
    example, dividing the error count by the total request count) when the label
    sets do not match 1-to-1 across both sides of the operator.
*   **Fix**: Use `ignoring(...)` or `on(...)` to specify the exact label subset
    for matching, and append `group_left` or `group_right` for many-to-one or
    one-to-many ratios:

    ```
    # Correcting a many-to-one ratio
    sum(rate(error_metric[5m])) by (method, service)
      / on (service) group_left(method)
      sum(rate(total_requests[5m])) by (service)
    ```

### Unquoted Literals & Equality Operators

*   **Error**: `unexpected identifier` or `invalid label matcher`.
*   **Cause**: Using unquoted strings in label selectors, or using a single `=`
    instead of `==` for boolean comparisons outside label selectors.
*   **Fix**: Quote all string literals inside label matchers, and use double
    equals `==` for value comparisons:

    ```
    # Wrong
    http_requests_total{environment=prod} = 0

    # Correct
    http_requests_total{environment="prod"} == 0
    ```

### Trailing Comments or Multiline Collisions

*   **Error**: Silently ignored filters, syntax errors at the end of the query,
    or unexpected end of input.
*   **Cause**: Including comments (`# ...`) or line breaks when transmitting
    queries to single-line Cloud Monitoring translation APIs. Cloud Monitoring's
    parser collapses whitespace and treats everything after `#` on the same line
    as a comment.
*   **Fix**: Strip all markdown formatting, line breaks, and `#` comments before
    passing the string to validation or execution.
