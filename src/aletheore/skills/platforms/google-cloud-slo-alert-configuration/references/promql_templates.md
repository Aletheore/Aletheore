# PromQL Templates

These templates implement the mathematics of SRE burn rates and lookback
windows.

## Table of Contents

-   [General Templates](#general-templates) (lines 20-215)
    -   Single Window Templates (lines 22-66)
    -   Multi-Window Templates (lines 68-136)
    -   Window-Based Templates (lines 138-215)
-   [System Templates](#system-templates) (lines 216-330)
    -   Cloud Run Revision Availability (lines 218-245)
    -   Vertex AI Reasoning Engine Availability (lines 247-271)
    -   App Hub Service Availability (lines 273-298)
    -   Cloud Run Revision (Filtered) (lines 300-330)
-   [Terraform Alert Policy Template](#terraform-alert-policy-template) (lines
    331-360)

## General Templates

**Single Window - Indirect (1 - Good/Total), Variable Factor & Window**

```promql
(
  1 - (
    sum(rate({GOOD_METRIC}[{WINDOW}])) BY ({...labels})
    /
    sum(rate({TOTAL_METRIC}[{WINDOW}])) BY ({...labels})
  )
) > (1 - {SLO_TARGET}) * {BURN_RATE_FACTOR}
```

**Single Window - Direct (Bad/Total), Variable Factor & Window**

```promql
(
  sum(rate({BAD_METRIC}[{WINDOW}])) BY ({...labels})
  /
  sum(rate({TOTAL_METRIC}[{WINDOW}])) BY ({...labels})
) > (1 - {SLO_TARGET}) * {BURN_RATE_FACTOR}
```

**Single Window - Direct (Bad/Total) - FAST BURN (Factor: 14.4, Window: 1h)**

```promql
(
  sum(rate({BAD_METRIC}[1h])) BY ({...labels}) / sum(rate({TOTAL_METRIC}[1h])) BY ({...labels})
) > (1 - {SLO_TARGET}) * 14.4
```

**Single Window - Direct (Bad/Total) - MEDIUM BURN (Factor: 6.0, Window: 6h)**

```promql
(
  sum(rate({BAD_METRIC}[6h])) BY ({...labels}) / sum(rate({TOTAL_METRIC}[6h])) BY ({...labels})
) > (1 - {SLO_TARGET}) * 6.0
```

**Single Window - Direct (Bad/Total) - SLOW BURN (Factor: 1.0, Window: 3d)**

```promql
(
  sum(rate({BAD_METRIC}[3d])) BY ({...labels}) / sum(rate({TOTAL_METRIC}[3d])) BY ({...labels})
) > (1 - {SLO_TARGET}) * 1.0
```

**Multi-Window - Indirect (1 - Good/Total) - FAST BURN (Factor: 14.4, Windows:
1h & 5m)**

```promql
(
  (
    1 - (
      sum(rate({GOOD_METRIC}[5m])) BY ({...labels}) / sum(rate({TOTAL_METRIC}[5m])) BY ({...labels})
    )
  ) > (1 - {SLO_TARGET}) * 14.4
) and (
  (
    1 - (
      sum(rate({GOOD_METRIC}[1h])) BY ({...labels}) / sum(rate({TOTAL_METRIC}[1h])) BY ({...labels})
    )
  ) > (1 - {SLO_TARGET}) * 14.4
)
```

**Multi-Window - Direct (Bad/Total) - MEDIUM BURN (Factor: 6, Windows: 6h &
30m)**

```promql
(
  (
    sum(rate({BAD_METRIC}[30m])) BY ({...labels}) / sum(rate({TOTAL_METRIC}[30m])) BY ({...labels})
  ) > (1 - {SLO_TARGET}) * 6.0
) and (
  (
    sum(rate({BAD_METRIC}[6h])) BY ({...labels}) / sum(rate({TOTAL_METRIC}[6h])) BY ({...labels})
  ) > (1 - {SLO_TARGET}) * 6.0
)
```

**Multi-Window - Indirect (1 - Good/Total) - SLOW BURN (Factor: 1.0, Windows: 3d
& 6h)**

```promql
(
  (
    1 - (
      sum(rate({GOOD_METRIC}[6h])) BY ({...labels}) / sum(rate({TOTAL_METRIC}[6h])) BY ({...labels})
    )
  ) > (1 - {SLO_TARGET}) * 1.0
) and (
  (
    1 - (
      sum(rate({GOOD_METRIC}[3d])) BY ({...labels}) / sum(rate({TOTAL_METRIC}[3d])) BY ({...labels})
    )
  ) > (1 - {SLO_TARGET}) * 1.0
)
```

**Multi-Window - Distribution (Latency), FAST BURN (Factor: 14.4, Windows: 1h &
5m)**

```promql
(
  (
    1 - histogram_fraction({LATENCY_THRESHOLD_MS}, sum by (le, {...labels}) (rate({DISTRIBUTION_METRIC}[5m])))
  ) > (1 - {SLO_TARGET}) * 14.4
)
and
(
  (
    1 - histogram_fraction({LATENCY_THRESHOLD_MS}, sum by (le, {...labels}) (rate({DISTRIBUTION_METRIC}[1h])))
  ) > (1 - {SLO_TARGET}) * 14.4
)
```

**Window-Based - Multi-Window - Indirect - FAST BURN (Factor: 14.4, Windows: 1h
& 5m)**

*   **Fraction of Bad Windows** - Fraction of bad 1m windows exceeds the allowed
    count
*   **[5m:1m] / [1h:1m]** - Lookback 5m/1h, 1m evaluation interval

```promql
(
  avg_over_time(
    (
      (
        1 - (
          sum(rate({GOOD_METRIC}[1m])) BY ({...labels})
          /
          sum(rate({TOTAL_METRIC}[1m])) BY ({...labels})
        )
      ) > bool (1 - {WINDOW_TARGET})

    )[5m:1m]
  ) > (1 - {SLO_TARGET}) * 14.4
)
and
(
  avg_over_time(
    (
      (
        1 - (
          sum(rate({GOOD_METRIC}[1m])) BY ({...labels})
          /
          sum(rate({TOTAL_METRIC}[1m])) BY ({...labels})
        )
      ) > bool (1 - {WINDOW_TARGET})

    )[1h:1m]
  ) > (1 - {SLO_TARGET}) * 14.4
)
```

**Window-Based - Multi-Window - Indirect - FAST BURN (Factor: 14.4, Windows: 1h
& 5m)**

*   **Number of Bad Windows** - Number of bad 1m windows exceeds the allowed
    count
*   **[5m:1m] / [1h:1m]** - Lookback 5m/1h, 1m evaluation interval

```promql
(
  sum_over_time(
    (
      (
        1 - (
          sum(rate({GOOD_METRIC}[1m])) BY ({...labels})
          /
          sum(rate({TOTAL_METRIC}[1m])) BY ({...labels})
        )
      ) > bool (1 - {WINDOW_TARGET})

    )[5m:1m]
  ) > (1 - {SLO_TARGET}) * 14.4 * 5
)
and
(
  sum_over_time(
    (
      (
        1 - (
          sum(rate({GOOD_METRIC}[1m])) BY ({...labels})
          /
          sum(rate({TOTAL_METRIC}[1m])) BY ({...labels})
        )
      ) > bool (1 - {WINDOW_TARGET})

    )[1h:1m]
  ) > (1 - {SLO_TARGET}) * 14.4 * 60
)
```

## System Templates

**Multi-Window - Indirect - FAST BURN - Availability, Scoped to Cloud Run
Revision (Factor: 14.4, Windows: 1h & 5m)**

-   **Metric:** `run.googleapis.com/request_count`
-   **Error Filter:** `response_code_class="5xx"`
-   **Scope:** `project_id`, `service_name`, `location`

```promql
(
  (
    1 - (
      sum(rate(run_googleapis_com:request_count{response_code_class!="5xx"}[5m])) BY (project_id, service_name, location)
      /
      sum(rate(run_googleapis_com:request_count[5m])) BY (project_id, service_name, location)
    )
  ) > (1 - {SLO_TARGET}) * 14.4
)
and
(
  (
    1 - (
      sum(rate(run_googleapis_com:request_count{response_code_class!="5xx"}[1h])) BY (project_id, service_name, location)
      /
      sum(rate(run_googleapis_com:request_count[1h])) BY (project_id, service_name, location)
    )
  ) > (1 - {SLO_TARGET}) * 14.4
)
```

**Multi-Window - Direct - SLOW BURN - Availability, Scoped to Vertex AI
Reasoning Engine (Factor: 1, Windows: 3d & 6h)**

-   **Metric:** `aiplatform.googleapis.com/reasoning_engine_request_count`
-   **Error Filter:** `response_code=~"5.."`
-   **Scope:** `resource_container` (must be prefixed with `projects/`),
    `location`, `reasoning_engine_id`

```promql
(
  (
    sum(rate(aiplatform_googleapis_com:reasoning_engine_request_count{resource_container="projects/{PROJECT_ID}", response_code=~"5.."}[6h])) BY (resource_container, location, reasoning_engine_id)
    /
    sum(rate(aiplatform_googleapis_com:reasoning_engine_request_count{resource_container="projects/{PROJECT_ID}"}[6h])) BY (resource_container, location, reasoning_engine_id)
  ) > (1 - {SLO_TARGET}) * 1.0
)
and
(
  (
    sum(rate(aiplatform_googleapis_com:reasoning_engine_request_count{resource_container="projects/{PROJECT_ID}", response_code=~"5.."}[3d])) BY (resource_container, location, reasoning_engine_id)
    /
    sum(rate(aiplatform_googleapis_com:reasoning_engine_request_count{resource_container="projects/{PROJECT_ID}"}[3d])) BY (resource_container, location, reasoning_engine_id)
  ) > (1 - {SLO_TARGET}) * 1.0
)
```

**Multi-Window - Direct - FAST BURN - Availability, Scoped to App Hub Service
(Factor: 14.4, Windows: 1h & 5m)**

-   **Metric:** `run.googleapis.com/request_count`
-   **Error Filter:** `response_code_class="5xx"`
-   **Scope:** `metadata_system_apphub_application_id`,
    `metadata_system_apphub_host_project_id`, `metadata_system_apphub_location`,
    `metadata_system_apphub_service_id`

```promql
(
  (
    sum(rate(run_googleapis_com:request_count{response_code_class="5xx"}[5m])) BY (metadata_system_apphub_application_id, metadata_system_apphub_host_project_id, metadata_system_apphub_location, metadata_system_apphub_service_id)
    /
    sum(rate(run_googleapis_com:request_count[5m])) BY (metadata_system_apphub_application_id, metadata_system_apphub_host_project_id, metadata_system_apphub_location, metadata_system_apphub_service_id)
  ) > (1 - {SLO_TARGET}) * 14.4
)
and
(
  (
    sum(rate(run_googleapis_com:request_count{response_code_class="5xx"}[1h])) BY (metadata_system_apphub_application_id, metadata_system_apphub_host_project_id, metadata_system_apphub_location, metadata_system_apphub_service_id)
    /
    sum(rate(run_googleapis_com:request_count[1h])) BY (metadata_system_apphub_application_id, metadata_system_apphub_host_project_id, metadata_system_apphub_location, metadata_system_apphub_service_id)
  ) > (1 - {SLO_TARGET}) * 14.4
)
```

**Multi-Window - Indirect - FAST BURN - Availability, Scoped and Filtered to
Cloud Run Revision (Factor: 14.4, Windows: 1h & 5m)**

-   **Metric:** `run.googleapis.com/request_count`
-   **Error Filter:** `response_code_class="5xx"`
-   **Scope:** `project_id`, `service_name`, `location`
-   **Service Filter:** `service_name="frontend-api"`, `location="us-central1"`,
    `response_code_class!="5xx"`

```promql
(
  (
    1 - (
      sum(rate(run_googleapis_com:request_count{project_id="my-app-project", service_name="frontend-api", location="us-central1", response_code_class!="5xx"}[5m])) BY (project_id, service_name, location)
      /
      sum(rate(run_googleapis_com:request_count{project_id="my-app-project", service_name="frontend-api", location="us-central1"}[5m])) BY (project_id, service_name, location)
    )
  ) > (1 - 0.99) * 14.4
)
and
(
  (
    1 - (
      sum(rate(run_googleapis_com:request_count{project_id="my-app-project", service_name="frontend-api", location="us-central1", response_code_class!="5xx"}[1h])) BY (project_id, service_name, location)
      /
      sum(rate(run_googleapis_com:request_count{project_id="my-app-project", service_name="frontend-api", location="us-central1"}[1h])) BY (project_id, service_name, location)
    )
  ) > (1 - 0.99) * 14.4
)
```

## Terraform Alert Policy Template

When generating Terraform configurations for SLO alert policies, wrap the PromQL
query in a `google_monitoring_alert_policy` resource with
`condition_prometheus_query_language`, and include the required `user_labels`:

```terraform
resource "google_monitoring_alert_policy" "slo_burn_rate_alert" {
  project      = var.project_id
  display_name = "[SLO] $${var.service_name} - Burn Rate Alert"
  combiner     = "OR"

  conditions {
    display_name = "Burn Rate Condition"
    condition_prometheus_query_language {
      query    = <<EOT
{promql_query}
EOT
      duration = "300s" # Omit duration for lookback windows > 25h (e.g., 3d)
    }
  }

  user_labels = {
    created-with-google-skill = "google-cloud-slo-alert-configuration"
  }

  notification_channels = var.notification_channels
}
```
