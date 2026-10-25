# Cloud Trace Query Syntax (v1)

This reference outlines the filter query syntax and predicates supported by the
Google Cloud Trace API (v1) filter engine.

## Query Predicates

Trace search filters consist of one or more space-separated predicates that
restrict results.

### 1. Root Span Matchers

-   `root:[PREFIX]`: Matches traces where the root span name starts with the
    given prefix.
    -   Example: `root:/v1/`
-   `+root:[NAME]`: Matches traces where the root span name matches the given
    name exactly.
    -   Example: `+root:/v1/checkout`

### 2. Any Span Matchers

-   `span:[PREFIX]`: Matches traces where any span name (root or child) starts
    with the given prefix.
    -   Example: `span:db_`
-   `+span:[NAME]`: Matches traces where any span name matches the given name
    exactly.
    -   Example: `+span:db_query`

### 3. Latency Matcher

-   `latency:[DURATION]`: Matches traces with overall latency greater than or
    equal to the specified duration.
    -   Example: `latency:1.5s`
    -   Valid duration suffixes: `ms` (milliseconds), `s` (seconds), `m`
        (minutes). No spaces are allowed (e.g., `800ms`, `2.5s`).

### 4. Label Matchers

-   `label:[KEY]`: Matches traces where any span has the specified label key present, regardless of its value.
    -   Example: `label:error.type`
-   `[KEY]:[VAL_PREFIX]`: Matches traces where any span has a label matching the
    key, and the label value starts with the prefix.
    -   Example: `http.status_code:5`
-   `+[KEY]:[VAL]`: Matches traces where any span has a label matching the key,
    and the label value matches the value exactly.
    -   Example: `+http.status_code:500`

### 5. Root Label Matchers

-   `^label:[KEY]`: Matches traces where the root span has the specified label key present, regardless of its value.
    -   Example: `^label:error.type`
-   `^[KEY]:[VAL_PREFIX]`: Matches traces where the root span has a label
    matching the key, and the label value starts with the prefix.
    -   Example: `^http.method:GET`
-   `+^[KEY]:[VAL]`: Matches traces where the root span has a label matching the
    key, and the label value matches the value exactly.
    -   Example: `+^http.method:GET`

### 6. Status Matcher

-   `error:true`: Matches traces containing at least one span with a failed
    status (errors).
-   `error:false`: Matches traces containing no spans with failed status.

## Query Evaluation Behavior & Limitations

### Independent Span Matching (Trace-level AND)

Predicates in a query are evaluated at the **trace level**, not the span level. When you combine multiple predicates, the filter engine returns traces where **each predicate matches at least one span**, but they do not need to match the **same** span.

*   **Gotcha**: The query `+rpc.method:MyService/MyMethod label:error.type` matches a trace if Span A is `MyService/MyMethod` (success) and Span B has `error.type` (failed database call). It does *not* guarantee that `MyMethod` itself failed.
*   **Mitigation**: The agent/client must post-filter the returned traces to verify that the specific target span has the error.

### Key Presence and Post-Filtering

While `label:KEY` filters for traces containing the specified label key, some instrumentations might explicitly populate labels with default/success values (e.g. `rpc.response.status_code:ok` or `error.type:None`).

*   **Mitigation**: Always post-filter the results programmatically to verify the actual values and ensure they meet the error criteria (e.g. status is not `OK`).

## See Also

*   [Trace Filter Syntax - Google Cloud docs](https://cloud.google.com/trace/docs/finding-traces#filter_syntax)
*   [Finding Traces in Console - Google Cloud docs](https://cloud.google.com/trace/docs/finding-traces)
*   [Trace REST API v1 Reference - Google Cloud docs](https://cloud.google.com/trace/docs/reference/v1/rest)
*   [Trace REST API v2 Reference - Google Cloud docs](https://cloud.google.com/trace/docs/reference/v2/rest)
*   [Traces and Spans Concept - Google Cloud docs](https://docs.cloud.google.com/trace/docs/traces-and-spans)
