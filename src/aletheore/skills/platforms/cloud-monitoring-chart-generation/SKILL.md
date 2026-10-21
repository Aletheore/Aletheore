---
name: cloud-monitoring-chart-generation
metadata:
  version: 1.0.1
  category: CloudObservabilityAndMonitoring
description: "Generates Cloud Monitoring Server-Driven UI (SDUI) Widget and XyChart Protocol Buffer textprotos on Google Cloud from resolved PromQL or ListTimeSeries queries. Use when:\n  - Generating valid google.monitoring.dashboard.v1.Widget textprotos,\n    containing PrometheusQuery or TimeSeriesFilter datasets, for use with the Cloud Monitoring\n    Dashboards API, gcloud CLI, or declarative dashboard definitions.\n  - Synthesizing Server-Driven UI (SDUI) widget titles, axis labels, and\n    plot types for Prometheus or ListTimeSeries queries.\nDon't use for:\n  - Metric discovery or PromQL query generation. For those tasks, use the\n    cloud-monitoring-metric-selection or cloud-monitoring-promql-query skills."
skill_id: platforms/cloud-monitoring-chart-generation
title: Cloud Monitoring Chart Generation
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/cloud-monitoring-chart-generation
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/cloud-monitoring-chart-generation", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/cloud-monitoring-chart-generation", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 9, "has_scripts": true}, "curated_on": "2026-10-18", "requires": ["scripts"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Cloud Monitoring Chart Generation Skill (`cloud-monitoring-chart-generation`)

Transforms PromQL or ListTimeSeries JSON request payloads and metric metadata
into valid Server-Driven UI (SDUI) `google.monitoring.dashboard.v1.Widget`
Protocol Buffer textprotos. These generated textprotos are designed to be
ingested by the Cloud Monitoring Dashboards API, gcloud CLI, or declarative
dashboard provisioning pipelines.

> [!IMPORTANT] **Preferred API & Mutually Exclusive Queries**:
> - **API Preference**: Always prefer generating `ListTimeSeries` (`time_series_filter`) configurations for widgets over PromQL, unless the user explicitly requested PromQL or the metric math strictly requires it.
> - **Mutually Exclusive**: A widget dataset `time_series_query` must contain **EITHER** a `time_series_filter` OR a `prometheus_query`. You must never populate both fields in the same dataset simultaneously.
> - **Strict Passthrough**: You MUST copy the provided PromQL query or ListTimeSeries JSON exact filter string character-for-character. DO NOT invent, rewrite, or modify the queries under any circumstances.

> [!CAUTION] **CRITICAL EXECUTION & WORKING DIRECTORY RULES**:
>
> -   **DO NOT CHANGE WORKING DIRECTORY**: Keep your working directory at your
>     workspace root. Do NOT `cd` into skill subdirectories.
> -   **NO DISCOVERY OR SEARCH RULE**: The metric descriptor, PromQL query,
>     ListTimeSeries JSON payload, unit, and resource type are ALWAYS present in
>     the conversation context. **NEVER** run file or codebase search tools,
>     like grep, find, directory listings, or codebase queries, to discover
>     metric metadata or inspect repository structures.
> -   **SCRIPT EXECUTION**: Execute the bundled Python scripts directly using
>     python3.
> -   **OUTPUT GENERATION**: The `assemble_widget_proto` script automatically
>     generates a unique UUID-based filename to prevent parallel execution
>     collisions. It will print the generated filename to standard error
>     strongly prefixed with "Wrote widget textproto to:". You MUST parse this
>     exact prefix from the logs to extract the generated path and use it for
>     validation in Stage 4.

## Prerequisites: Environment Setup

Install the required dependencies in your environment or sandbox:

```bash
pip install -r scripts/requirements.txt
```

## Follow the workflow pipeline

```
[ Stage 1: compute_labels ]  --->  [ Stage 2: LLM Synthesis ]  --->  [ Stage 3: assemble_widget_proto ]
  Generates candidate labels         Formulates SemanticPlotSpec       Emits validated widget textproto
```

### Stage 1: Baseline Candidate Synthesis

Run Stage 1 using python3:

```bash
# For PromQL:
python3 scripts/compute_labels.py \
  --metric_display_name "METRIC_DISPLAY_NAME" \
  --resource_type "RESOURCE_TYPE" \
  --metric_unit "UNIT" \
  --promql_query 'PROMQL_QUERY'

# For ListTimeSeries:
python3 scripts/compute_labels.py \
  --metric_display_name "METRIC_DISPLAY_NAME" \
  --resource_type "RESOURCE_TYPE" \
  --metric_unit "UNIT" \
  --filter_string 'metric.type="m"...' \
  --per_series_aligner "ALIGN_RATE" \
  --cross_series_reducer "REDUCE_SUM"
```

### Stage 2: SemanticPlotSpec Prediction (LLM)

Review the user prompt, PromQL or LTS query structure, and Stage 1 baseline
candidates to formulate a 4-key `SemanticPlotSpec` JSON object:

1. **`title`**: Polish `titleCandidate` to ensure it is concise, human-readable,
   and under 80 characters.
2.  **`yAxisLabel`**: Set this to a concise, human-readable quantitative
    descriptor or metric concept, like `"Utilization"`, `"Bytes"`, or `"Bytes
    Rate"`. Do NOT append unit symbols or suffixes like `"(%)"`, `"(/s)"`, or
    `"(By)"` to the label, because units are rendered automatically via
    `unitOverride`.
3. **`plotType`**: Default to `LINE`. Use `STACKED_AREA` if requested by the
   user or for distribution queries.
4.  **`unitOverride`**: Set this to the Unified Code for Units of Measure (UCUM)
    unit string, derived by applying the corresponding rules below:

#### List Time Series (LTS) Unit Strategy:

-   **Trust the Candidate**: For List Time Series flows, set this directly to
    the `unitOverrideCandidate` produced by Stage 1. Stage 1 mathematically
    processes `ALIGN_RATE`, for example producing `By/s`, forces `%` for
    `ALIGN_PERCENT_CHANGE`, and correctly outputs native normalizations
    unconditionally.

#### PromQL Unit Strategy (LLM Manual Override):

Because PromQL expressions can geometrically compose, for example
`histogram_quantile(..., rate(...))`, rely on your own semantic reasoning to
govern the final unit:

-   **Rate Functions (`rate(...)`, `irate(...)`)**: Convert cumulative counters
    into per-second rates. Append `/s` to the raw metric unit. For example, a
    raw metric unit of `By` with `rate(...)` results in `unitOverride: "By/s"`.
    -   **Exception**: If `rate()` is evaluated inside a `histogram_quantile()`,
        the output is the raw bucket unit like `"s"`, not a rate.
-   **Ratios & Percentages (`100 * (A / B)`)**: Ratios of identical metric units
    typically represent percentages, resulting in `unitOverride: "%"`.
-   **Normalizations**: Normalize `10^2.%` to `"%"`.
-   **Preserved Units**: For simple aggregation functions like
    `avg_over_time(...)` or `sum by (...)`, retain and output the underlying
    metric unit without modification.

-   **Legend Template**: Do NOT configure the `legend_template` field. It is
    intentionally omitted so that the Cloud Monitoring frontend dynamically
    renders its multi-column table legend at runtime.

Example `SemanticPlotSpec`:

```json
{
  "title": "VM CPU Utilization us-central1-a",
  "yAxisLabel": "Utilization",
  "plotType": "LINE",
  "unitOverride": "%"
}
```

### Stage 3: Protobuf Assembly & Output

Run Stage 3 using python3 to generate and save the widget textproto. Use
`--promql_query` for PromQL, or `--lts_request_json` for ListTimeSeries:

```bash
# For PromQL:
python3 scripts/assemble_widget_proto.py \
  --promql_query 'PROMQL_QUERY' \
  --spec_json 'SEMANTIC_PLOT_SPEC_JSON'

# For ListTimeSeries:
python3 scripts/assemble_widget_proto.py \
  --lts_request_json '{"filter": "...", "aggregation": {...}}' \
  --spec_json 'SEMANTIC_PLOT_SPEC_JSON'
```

> [!IMPORTANT] **MANDATORY FILE OUTPUT CONTRACT**: Do not attempt to guess or
> enforce the output filename. The script will automatically generate a
> guaranteed-unique filename and print it to standard error. Search stderr for
> the explicit prefix "Wrote widget textproto to:" to deterministically capture
> this filename, and then target it in Stage 4 validation.

-   **Assigned Filename Feedback**: Whenever an output file is saved, the script
    logs the file path to stderr. Read your command execution logs for the exact
    filename created so you can target it in Stage 4 validation.
-   **Text Chat Output**: Enclose the generated SDUI widget textproto inside
    a ```` ```textproto```` code block in your response:

```textproto
title: "..."
xy_chart {
  ...
}
```

### Verify and auto-retry

> [!CAUTION] **DO NOT FINISH YOUR TURN UNTIL FILE VERIFICATION PASSES**: 1.
> **Validate Artifact**: Execute the validator script against the generated file
> output from Stage 3:
>
> ```bash
>    # For PromQL charts:
>    python3 scripts/validate_chart.py --input_file "GENERATED_FILE.textproto" \
>       --expected_promql_substring "SOME_IDENTIFYING_SUBSTRING_FROM_QUERY" \
>       --expected_unit_override "UNIT_OVERRIDE_CANDIDATE"
>
>    # For ListTimeSeries (LTS) charts:
>    python3 scripts/validate_chart.py --input_file "GENERATED_FILE.textproto" \
>       --expected_lts_filter_substring "SOME_IDENTIFYING_SUBSTRING_FROM_FILTER" \
>       --expected_unit_override "UNIT_OVERRIDE_CANDIDATE"
>
>    # ALWAYS provide an identifying substring and the Stage 1 unit override candidate to verify you didn't mutate the data.
    
>    # CRITICAL: If you generated multiple charts for multiple metrics, you MUST run this validation script independently for EACH file generated to ensure every chart is correct!
> ```
>
> 2.  **Auto-Retry if Missing or Failed**: If `validate_chart` reports that the
>     file is missing or invalid, verify your script parameters and immediately
>     re-run Stage 3:
>
>     ```bash
>     python3 scripts/assemble_widget_proto.py \
>       --promql_query 'PROMQL_QUERY' \
>       --spec_json 'SEMANTIC_PLOT_SPEC_JSON'
>     # Or use --lts_request_json if applicable
>     ```
> 3. **Validation & Retries**: Run `validate_chart` to verify the generated
>    textproto. If validation fails due to a schema or syntax error, correct
>    the parameters and retry up to 2 times. If validation still fails after 2
>    retries, stop retrying, notify the user of the validation error, and
>    present the best-effort textproto.
> 4.  **Execution vs. Validation Errors**: Note that schema/syntax validation
>     errors from `validate_chart.py` are distinct from OS or environment
>     execution restrictions, which are handled below in **Graceful Sandbox
>     Fallback**.

#### Perform graceful sandbox fallback

If `compute_labels.py`, `assemble_widget_proto.py`, or `validate_chart.py`
cannot be executed due to environment or sandbox restrictions, do the
following:

1. Notify the user which script cannot be executed and why.
2. **Synthesize and output the complete widget textproto directly in your
   response**, following all formatting and unit rules.
3. Provide a **"Local Verification"** section containing the standalone python3
   commands so the user can run and validate the schema locally if desired.

## Supporting Links

- [Dashboards API](https://docs.cloud.google.com/monitoring/dashboards/api-dashboard)
- [Prometheus Docs](https://prometheus.io/docs/prometheus/latest/querying/)
