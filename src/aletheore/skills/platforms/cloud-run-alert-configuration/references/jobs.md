# Cloud Run Jobs Alert Configuration

Best-practice observability for scheduled or ad-hoc Cloud Run Jobs.

## Fast-Path Standard: Job Execution Failure Alert

For Cloud Run Jobs (scheduled cron, ad-hoc batch processing, or ETL workflows),
every execution failure represents direct user or system impact.

The standard observability strategy for Cloud Run Jobs is to **alert immediately
on any failed job execution (`result="failed"`)**.

--------------------------------------------------------------------------------

## Default Constants & Parameterization

All generated Terraform alert policies use explicit default constants. Users can
override any parameter in HCL variables or via prompt instruction:

Constant / Parameter      | Explicit Default | HCL Variable Name             | Description & Override Option
:------------------------ | :--------------- | :---------------------------- | :----------------------------
`single_failure_lookback` | `"15m"`          | `var.single_failure_lookback` | Lookback window for detecting single job execution failures.

--------------------------------------------------------------------------------

## PromQL & Terraform Configuration

### Immediate Job Failure Alert

Monitors any single execution failure (`result="failed"`). Evaluates with
`duration = "0s"` to page immediately upon task/job failure.

#### PromQL Query

```promql
sum(
  increase(run_googleapis_com:job_completed_execution_count{result="failed", job_name="[JOB_NAME]"}[15m])
) by (job_name, location) > 0
```

#### Terraform Snippet

```hcl
variable "monitored_jobs" {
  description = "List of Cloud Run job names to monitor for execution failures"
  type        = list(string)
  default     = ["data-ingest", "nightly-sync"]
}

variable "single_failure_lookback" {
  description = "Lookback window for detecting single execution failures"
  type        = string
  default     = "15m"
}

resource "google_monitoring_alert_policy" "job_execution_failure" {
  for_each     = toset(var.monitored_jobs)
  project      = var.scoping_project_id
  display_name = "[Cloud Run Job] Execution Failure - ${each.value}"
  combiner     = "OR"

  conditions {
    display_name = "Job Execution Failed - ${each.value}"
    condition_prometheus_query_language {
      query = <<-EOT
        sum(
          increase(run_googleapis_com:job_completed_execution_count{result="failed", job_name="${each.value}"}[${var.single_failure_lookback}])
        ) by (job_name, location) > 0
      EOT
      duration = "0s"
    }
  }

  user_labels = {
    severity                    = "critical"
    job                         = each.value
    tier                        = "failure"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}
```

--------------------------------------------------------------------------------

## Telemetry Data Sources Reference

Signal Indicator          | Native Metric                                      | SRE Strategy
:------------------------ | :------------------------------------------------- | :-----------
**Job Execution Failure** | `run.googleapis.com/job/completed_execution_count` | **Immediate Failure Alert**: Alert immediately on any failed execution (`result="failed"`)
