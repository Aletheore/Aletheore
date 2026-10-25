# Cloud Run Services Alert Configuration

Best-practice observability for HTTP-triggered Cloud Run Services.

--------------------------------------------------------------------------------

## Table of Contents

*   [Fast-Path Defaults](#fast-path-defaults) (#fast-path-defaults)
    *   [Core SLO & Instance Saturation Suite](#core-slo-instance-saturation-suite)
    *   [Extended Dashboard & Resource Protection Suite](#extended-dashboard-resource-protection-suite)
*   [Default Constants & Override Protocol](#default-constants-override-protocol)
*   [SLO Delegation & Fallback Rule](#slo-delegation-fallback-rule) (~line 83)
*   [PromQL & Terraform Query Templates](#promql-terraform-query-templates)
    *   [1. Container Instance Saturation](#1-container-instance-saturation)
    *   [2. Container Memory & CPU Utilization (OOM Prevention & Throttling)](#2-container-memory-cpu-utilization-oom-prevention-throttling)
    *   [3. Availability SLO Burn Rate Queries](#3-availability-slo-burn-rate-queries)
    *   [4. Request Latency SLO Queries (P95 & P99)](#4-request-latency-slo-queries-p95-p99)
    *   [5. Client Error Surge (4xx Response Code Storm)](#5-client-error-surge-4xx-response-code-storm)
    *   [6. Traffic & FinOps Anomalies](#6-traffic-finops-anomalies)
    *   [7. Conditional Diagnostic: Cold Start Latency](#7-conditional-diagnostic-cold-start-latency)
*   [Telemetry Data Sources Reference](#telemetry-data-sources-reference)

--------------------------------------------------------------------------------

## Fast-Path Defaults
When standard alerting is requested or thresholds are unspecified, apply the standard suite tailored to user requirements:

### Core SLO & Instance Saturation Suite

1. **Availability SLO (Fast Burn)**: 99.9% target, 1h & 5m windows, $14.4\times$ burn rate, `duration = "300s"` (Critical/Paging).
2. **Availability SLO (Slow Burn)**: 99.9% target, 3d & 6h windows, $1.0\times$ burn rate, **omit `duration`** (Warning/Ticket).
3. **Request Latency SLO (P95)**: Window-based 1h lookback, P95 $\le 500\text{ms}$ in 99% of 5m windows (Warning).
4. **Request Latency SLO (P99)**: Window-based 1h lookback, P99 $\le 1000\text{ms}$ in 99% of 5m windows (Warning).
5. **Instance Saturation (Warning)**: >80% of service `max_instances` ceiling (Warning).
6. **Instance Saturation (Critical)**: $\ge 95\%$ of service `max_instances` ceiling (Critical).
7. **Traffic Drop Anomaly**: 5m throughput drop vs. 1h moving average baseline with min-volume guard (`rate(...[1h]) > 1.0`).

### Extended Dashboard & Resource Protection Suite

8. **Container Memory Saturation & OOM Risk**: P95 container memory utilization $\ge 90\%$ (Critical - Exit Code 137 prevention).
9. **Container CPU Saturation & Throttling**: P95 container CPU utilization $> 85\%$ (Warning).
10. **Client Error Surge (4xx)**: 4xx response code ratio $> 10\%$ with 5 req/s min-volume guard (Warning).
11. **Billable Instance Time Surge**: 5m billable time rate $> 3\times$ 1h moving average baseline (FinOps Warning).
12. **Traffic Surge Anomaly**: 5m request rate $> 3\times$ 1h moving average baseline with min-volume guard (Warning).

---

## Default Constants & Override Protocol

All generated Terraform alert policies use explicit default constants. Users can override any parameter in HCL variables or via prompt instruction:

| Constant / Parameter | Explicit Default | HCL Variable Name | Description & Override Option |
| :--- | :--- | :--- | :--- |
| `availability_slo_target` | `0.999` (99.9%) | `var.availability_slo_target` | Availability target ratio. Override to 0.99 or 0.9999. |
| `latency_p95_threshold_ms` | `500` (500ms) | `var.latency_p95_threshold_ms` | P95 latency limit in milliseconds. |
| `latency_p99_threshold_ms` | `1000` (1000ms) | `var.latency_p99_threshold_ms` | P99 latency limit in milliseconds. |
| `saturation_warning_ratio` | `0.80` (80%) | `var.saturation_warning_ratio` | Instance saturation warning threshold relative to `max_instances`. |
| `saturation_critical_ratio`| `0.95` (95%) | `var.saturation_critical_ratio` | Instance saturation critical threshold relative to `max_instances`. |
| `memory_saturation_ratio` | `0.90` (90%) | `var.memory_saturation_ratio` | P95 container memory utilization critical limit (OOM prevention). |
| `cpu_saturation_ratio` | `0.85` (85%) | `var.cpu_saturation_ratio` | P95 container CPU utilization warning limit. |
| `client_error_4xx_ratio` | `0.10` (10%) | `var.client_error_4xx_ratio` | Maximum allowable ratio of 4xx client errors. |
| `traffic_drop_ratio` | `0.20` (80% drop) | `var.traffic_drop_ratio` | Throughput drop threshold vs 1h moving average. |
| `traffic_min_hourly_rate` | `1.0` (1 req/s) | `var.traffic_min_hourly_rate` | Min hourly rate guard to prevent scale-to-zero false alarms. |
| `traffic_surge_multiplier` | `3.0` (3x surge) | `var.traffic_surge_multiplier` | Throughput spike multiplier vs 1h moving average. |
| `traffic_surge_min_rate` | `15.0` (15 req/s) | `var.traffic_surge_min_rate` | Minimum 5m request rate guard (req/s) to suppress traffic surge false alarms on low volume. |
| `billable_time_surge_multiplier` | `3.0` (3x surge) | `var.billable_time_surge_multiplier` | Billable compute time multiplier vs 1h moving average. |
| `billable_time_min_rate` | `5.0` (5 s/s) | `var.billable_time_min_rate` | Minimum 5m billable instance-seconds rate guard to prevent FinOps false alarms on low baseline usage. |
| `retest_duration_buffer` | `"300s"` (5m) | `var.retest_duration_buffer` | Retest window for lookbacks $\le 25$h. Omitted for lookbacks $>25$h. |

---

## SLO Delegation & Fallback Rule

* **If `google-cloud-slo-alert-configuration` skill is available**: use it to inform SLO creation.
* **If `google-cloud-slo-alert-configuration` skill is NOT available**: Use the standard PromQL burn-rate templates provided directly in this reference guide as a complete, standalone solution.

---

## PromQL & Terraform Query Templates

### 1. Container Instance Saturation
Monitors when active container instances approach configured scaling ceilings (`max_instances`).
> [!NOTE]
> PromQL queries for `container_instance_count` MUST include the label matcher `monitored_resource="cloud_run_revision"` to prevent API validation errors resulting from metric ambiguity with `cloud_run_worker_pool`.

#### Warning Query (>80% Capacity)

```promql
sum(
  max_over_time(run_googleapis_com:container_instance_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]", state="active"}[5m])
) by (service_name, location) / [SERVICE_MAX_INSTANCES] > 0.80
```

#### Critical Query (>=95% Capacity)

```promql
sum(
  max_over_time(run_googleapis_com:container_instance_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]", state="active"}[5m])
) by (service_name, location) / [SERVICE_MAX_INSTANCES] >= 0.95
```

#### Terraform Snippet (Instance Saturation Policies)

```hcl
variable "cloud_run_services" {
  description = "Map of Cloud Run services to monitor and their max_instances ceilings"
  type = map(object({
    max_instances = number
  }))
  default = {
    "api-gateway" = { max_instances = 1000 }
  }
}

variable "saturation_warning_ratio" {
  description = "Ratio threshold for container instance saturation warning"
  type        = number
  default     = 0.80
}

variable "saturation_critical_ratio" {
  description = "Ratio threshold for container instance saturation critical"
  type        = number
  default     = 0.95
}

resource "google_monitoring_alert_policy" "instance_saturation_warning" {
  for_each     = var.cloud_run_services
  project      = var.scoping_project_id
  display_name = "[Cloud Run] Container Instance Saturation Warning (>80% Limit) - ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = "Active Instances Exceed 80% of ${each.key} Ceiling (${each.value.max_instances})"
    condition_prometheus_query_language {
      query = <<-EOT
        sum(
          max_over_time(run_googleapis_com:container_instance_count{monitored_resource="cloud_run_revision", service_name="${each.key}", state="active"}[5m])
        ) by (service_name, location) / ${each.value.max_instances} > ${var.saturation_warning_ratio}
      EOT
      duration = "300s"
    }
  }

  user_labels = {
    severity                    = "warning"
    service                     = each.key
    tier                        = "saturation"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}

resource "google_monitoring_alert_policy" "instance_saturation_critical" {
  for_each     = var.cloud_run_services
  project      = var.scoping_project_id
  display_name = "[Cloud Run] Container Instance Saturation Critical (>=95% Limit) - ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = "Active Instances Exceed 95% of ${each.key} Ceiling (${each.value.max_instances})"
    condition_prometheus_query_language {
      query = <<-EOT
        sum(
          max_over_time(run_googleapis_com:container_instance_count{monitored_resource="cloud_run_revision", service_name="${each.key}", state="active"}[5m])
        ) by (service_name, location) / ${each.value.max_instances} >= ${var.saturation_critical_ratio}
      EOT
      duration = "300s"
    }
  }

  user_labels = {
    severity                    = "critical"
    service                     = each.key
    tier                        = "saturation"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}
```

---

### 2. Container Memory & CPU Utilization (OOM Prevention & Throttling)

Monitors actual CPU and Memory resource consumption inside container instances.

#### 2.1 Container Memory Saturation (OOM Risk)
Triggers when the 95th percentile memory utilization reaches critical levels, preventing container abrupt termination via Linux kernel OOMKilled (exit code 137).

```promql
histogram_quantile(0.95,
  sum(
    rate(run_googleapis_com:container_memory_utilizations_bucket{
      monitored_resource="cloud_run_revision",
      service_name="[SERVICE_NAME]"
    }[5m])
  ) by (le, service_name, location)
) >= 0.90
```

#### 2.2 Container CPU Saturation & Throttling
Triggers when the 95th percentile container CPU utilization exceeds 85%, indicating compute starvation or inadequate vCPU sizing.

```promql
histogram_quantile(0.95,
  sum(
    rate(run_googleapis_com:container_cpu_utilizations_bucket{
      monitored_resource="cloud_run_revision",
      service_name="[SERVICE_NAME]"
    }[5m])
  ) by (le, service_name, location)
) > 0.85
```

#### Terraform Snippet (Resource Saturation Policies)

```hcl
variable "memory_saturation_ratio" {
  description = "P95 container memory utilization threshold (OOM danger)"
  type        = number
  default     = 0.90
}

variable "cpu_saturation_ratio" {
  description = "P95 container CPU utilization threshold (throttling danger)"
  type        = number
  default     = 0.85
}

resource "google_monitoring_alert_policy" "container_memory_saturation_critical" {
  for_each     = var.cloud_run_services
  project      = var.scoping_project_id
  display_name = "[Cloud Run] Container Memory Saturation Critical (P95 >= 90% OOM Risk) - ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = "P95 Memory Utilization >= 90% on ${each.key}"
    condition_prometheus_query_language {
      query = <<-EOT
        histogram_quantile(0.95,
          sum(
            rate(run_googleapis_com:container_memory_utilizations_bucket{
              monitored_resource = "cloud_run_revision",
              service_name       = "${each.key}"
            }[5m])
          ) by (le, service_name, location)
        ) >= ${var.memory_saturation_ratio}
      EOT
      duration = "300s"
    }
  }

  user_labels = {
    severity                    = "critical"
    service                     = each.key
    tier                        = "saturation"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}

resource "google_monitoring_alert_policy" "container_cpu_saturation_warning" {
  for_each     = var.cloud_run_services
  project      = var.scoping_project_id
  display_name = "[Cloud Run] Container CPU Saturation Warning (P95 > 85%) - ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = "P95 CPU Utilization > 85% on ${each.key}"
    condition_prometheus_query_language {
      query = <<-EOT
        histogram_quantile(0.95,
          sum(
            rate(run_googleapis_com:container_cpu_utilizations_bucket{
              monitored_resource = "cloud_run_revision",
              service_name       = "${each.key}"
            }[5m])
          ) by (le, service_name, location)
        ) > ${var.cpu_saturation_ratio}
      EOT
      duration = "300s"
    }
  }

  user_labels = {
    severity                    = "warning"
    service                     = each.key
    tier                        = "saturation"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}
```

---

### 3. Availability SLO Burn Rate Queries

#### Availability Fast Burn (1h & 5m Windows)

```promql
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]", response_code_class="5xx"}[5m])) by (service_name, location)
  /
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (service_name, location)
  > (1 - [SLO_TARGET]) * 14.4
)
and
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]", response_code_class="5xx"}[1h])) by (service_name, location)
  /
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[1h])) by (service_name, location)
  > (1 - [SLO_TARGET]) * 14.4
)
and
(
  # Min-volume guard: ensure at least 1 req/s to prevent false alerts on scale-to-zero or low-traffic services
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[1h])) by (service_name, location) > 1.0
)
```

#### Availability Slow Burn (3d & 6h Windows)
> [!WARNING]
> Lookback windows >25h (like `[3d]`) require **omitting `duration`** in HCL.

```promql
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]", response_code_class="5xx"}[6h])) by (service_name, location)
  /
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[6h])) by (service_name, location)
  > (1 - [SLO_TARGET]) * 1.0
)
and
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]", response_code_class="5xx"}[3d])) by (service_name, location)
  /
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[3d])) by (service_name, location)
  > (1 - [SLO_TARGET]) * 1.0
)
and
(
  # Min-volume guard: ensure at least 1 req/s to prevent false alerts on scale-to-zero or low-traffic services
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[6h])) by (service_name, location) > 1.0
)
```

---

### 4. Request Latency SLO Queries (P95 & P99)

#### P95 Request Latency SLO

```promql
avg_over_time(
  (
    histogram_quantile(0.95, sum(rate(run_googleapis_com:request_latencies_bucket{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (le, service_name, location)) > [LATENCY_THRESHOLD_MS]
  )[1h:5m]
) > (1 - [WINDOW_SLO_TARGET])
```

#### P99 Request Latency SLO

```promql
avg_over_time(
  (
    histogram_quantile(0.99, sum(rate(run_googleapis_com:request_latencies_bucket{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (le, service_name, location)) > [LATENCY_THRESHOLD_MS]
  )[1h:5m]
) > (1 - [WINDOW_SLO_TARGET])
```

---

### 5. Client Error Surge (4xx Response Code Storm)
Detects client authorization failures (401/403) or concurrency rate-limiting (429) across inbound requests.

```promql
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]", response_code_class="4xx"}[5m])) by (service_name, location)
  /
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (service_name, location)
  > [CLIENT_ERROR_RATIO]
)
and
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (service_name, location) > [MIN_REQ_RATE]
)
```

#### Terraform Snippet (4xx Client Error Policy)

```hcl
variable "client_error_4xx_ratio" {
  description = "Ratio threshold for 4xx client errors"
  type        = number
  default     = 0.10
}

variable "client_error_min_rate" {
  description = "Minimum request rate (req/s) to guard against low-volume noise"
  type        = number
  default     = 5.0
}

resource "google_monitoring_alert_policy" "client_error_4xx_surge" {
  for_each     = var.cloud_run_services
  project      = var.scoping_project_id
  display_name = "[Cloud Run] Client Error Surge (4xx > 10%) - ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = "4xx Error Ratio Exceeds 10% on ${each.key}"
    condition_prometheus_query_language {
      query = <<-EOT
        (
          sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="${each.key}", response_code_class="4xx"}[5m])) by (service_name, location)
          /
          sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="${each.key}"}[5m])) by (service_name, location)
          > ${var.client_error_4xx_ratio}
        )
        and
        (
          sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="${each.key}"}[5m])) by (service_name, location) > ${var.client_error_min_rate}
        )
      EOT
      duration = "300s"
    }
  }

  user_labels = {
    severity                    = "warning"
    service                     = each.key
    tier                        = "client_error"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}
```

---

### 6. Traffic & FinOps Anomalies

#### 6.1 Traffic Drop Anomaly
Detects sudden traffic drops compared to 1h moving average, guarded against scale-to-zero.

```promql
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (service_name, location)
  /
  (sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[1h])) by (service_name, location) + 0.001)
) < [DROP_THRESHOLD_RATIO]
and
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[1h])) by (service_name, location) > [MIN_HOURLY_RATE]
)
```

#### Terraform Snippet (Traffic Drop Policy)

```hcl
variable "traffic_drop_ratio" {
  description = "Ratio threshold vs 1h average for traffic drop anomaly"
  type        = number
  default     = 0.20 # 80% drop
}

variable "traffic_min_hourly_rate" {
  description = "Minimum hourly request rate (req/s) guard to prevent scale-to-zero false alarms"
  type        = number
  default     = 1.0 # 1 req/s
}

resource "google_monitoring_alert_policy" "traffic_drop_anomaly" {
  for_each     = var.cloud_run_services
  project      = var.scoping_project_id
  display_name = "[Cloud Run] Traffic Drop Anomaly (>80% Drop) - ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = "Traffic Dropped >80% vs 1h Average on ${each.key}"
    condition_prometheus_query_language {
      query = <<-EOT
        (
          sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="${each.key}"}[5m])) by (service_name, location)
          /
          (sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="${each.key}"}[1h])) by (service_name, location) + 0.001)
        ) < ${var.traffic_drop_ratio}
        and
        (
          sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="${each.key}"}[1h])) by (service_name, location) > ${var.traffic_min_hourly_rate}
        )
      EOT
      duration = "300s"
    }
  }

  user_labels = {
    severity                    = "warning"
    service                     = each.key
    tier                        = "traffic"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}
```

#### 6.2 Traffic Surge Anomaly
Detects sudden volumetric traffic spikes (e.g. DDoS or unexpected viral load) compared to the 1h moving average baseline (`> 3x` baseline with a minimum rate guard of `> 15 req/s`).

##### PromQL Query

```promql
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (service_name, location)
  /
  (sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[1h])) by (service_name, location) + 0.1)
  > 3.0
)
and
(
  sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (service_name, location) > 15.0
)
```

##### Terraform Snippet

```hcl
variable "traffic_surge_multiplier" {
  description = "Throughput spike multiplier vs 1h moving average baseline"
  type        = number
  default     = 3.0 # 3x surge
}

variable "traffic_surge_min_rate" {
  description = "Minimum 5m request rate guard (req/s) to suppress false alarms on low volume"
  type        = number
  default     = 15.0 # 15 req/s
}

resource "google_monitoring_alert_policy" "traffic_surge_anomaly" {
  for_each     = var.cloud_run_services
  project      = var.scoping_project_id
  display_name = "[Cloud Run] Traffic Surge Anomaly (>3x Baseline) - ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = "Traffic Surged >3x vs 1h Average on ${each.key}"
    condition_prometheus_query_language {
      query = <<-EOT
        (
          sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="${each.key}"}[5m])) by (service_name, location)
          /
          (sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="${each.key}"}[1h])) by (service_name, location) + 0.1)
          > ${var.traffic_surge_multiplier}
        )
        and
        (
          sum(rate(run_googleapis_com:request_count{monitored_resource="cloud_run_revision", service_name="${each.key}"}[5m])) by (service_name, location) > ${var.traffic_surge_min_rate}
        )
      EOT
      duration = "300s"
    }
  }

  user_labels = {
    severity                    = "warning"
    service                     = each.key
    tier                        = "traffic"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}
```

#### 6.3 Billable Instance Time Surge (FinOps Cost Control)
Monitors runaway autoscaling or unoptimized compute consumption by tracking surges in billable container instance time (`> 3x` 1-hour moving average baseline with a minimum billable instance-seconds guard `> 5.0`).

##### PromQL Query

```promql
(
  sum(rate(run_googleapis_com:container_billable_instance_time{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (service_name, location)
  /
  (sum(rate(run_googleapis_com:container_billable_instance_time{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[1h])) by (service_name, location) + 0.1)
  > 3.0
)
and
(
  sum(rate(run_googleapis_com:container_billable_instance_time{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[5m])) by (service_name, location) > 5.0
)
```

##### Terraform Snippet

```hcl
variable "billable_time_surge_multiplier" {
  description = "Billable compute time surge multiplier vs 1h moving average baseline"
  type        = number
  default     = 3.0 # 3x surge
}

variable "billable_time_min_rate" {
  description = "Minimum 5m billable instance-seconds rate guard to suppress alerts on low baseline usage"
  type        = number
  default     = 5.0 # 5 billable instance-seconds/s
}

resource "google_monitoring_alert_policy" "billable_instance_time_surge" {
  for_each     = var.cloud_run_services
  project      = var.scoping_project_id
  display_name = "[Cloud Run] FinOps Billable Instance Time Surge (>3x Baseline) - ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = "Billable Instance Time >3x vs 1h Average on ${each.key}"
    condition_prometheus_query_language {
      query = <<-EOT
        (
          sum(rate(run_googleapis_com:container_billable_instance_time{monitored_resource="cloud_run_revision", service_name="${each.key}"}[5m])) by (service_name, location)
          /
          (sum(rate(run_googleapis_com:container_billable_instance_time{monitored_resource="cloud_run_revision", service_name="${each.key}"}[1h])) by (service_name, location) + 0.1)
          > ${var.billable_time_surge_multiplier}
        )
        and
        (
          sum(rate(run_googleapis_com:container_billable_instance_time{monitored_resource="cloud_run_revision", service_name="${each.key}"}[5m])) by (service_name, location) > ${var.billable_time_min_rate}
        )
      EOT
      duration = "300s"
    }
  }

  user_labels = {
    severity                    = "warning"
    service                     = each.key
    tier                        = "finops"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}
```

---

### 7. Conditional Diagnostic: Cold Start Latency

```promql
histogram_quantile(0.95,
  sum(
    rate(run_googleapis_com:container_startup_latencies_bucket{monitored_resource="cloud_run_revision", service_name="[SERVICE_NAME]"}[30m])
  ) by (le, service_name, location)
) > [STARTUP_LATENCY_SECONDS]
```

---

## Telemetry Data Sources Reference

| Signal Indicator | Native Metric | SRE Observability Strategy |
| :--- | :--- | :--- |
| **Availability (5xx)** | `run.googleapis.com/request_count` | **SLO Fast Burn (1h/5m) & Slow Burn (3d/6h)** (`response_code_class="5xx"`) |
| **Request Latency (P50/P95/P99)** | `run.googleapis.com/request_latencies` | **SLO P95 & P99 Window Evaluation** (`request_latencies_bucket`) |
| **Performance / Client Errors (4xx)** | `run.googleapis.com/request_count` | **Client Error Spike Alert**: 4xx ratio $> 10\%$ with 5 req/s guard |
| **Container Instance Saturation** | `run.googleapis.com/container/instance_count` | **Scaling Ceiling Warning (0.80) & Critical (0.95)** vs `max_instances` |
| **Container Memory Utilization** | `run.googleapis.com/container/memory/utilizations` | **OOM Prevention Alert**: P95 memory utilization $\ge 90\%$ (Exit 137 guard) |
| **Container CPU Utilization** | `run.googleapis.com/container/cpu/utilizations` | **Compute Throttling Alert**: P95 CPU utilization $> 85\%$ |
| **Billable Instance Time** | `run.googleapis.com/container/billable_instance_time` | **FinOps Cost Spike Alert**: 5m rate $> 3\times$ 1h moving average baseline |
| **Container CPU Allocation** | `run.googleapis.com/container/cpu/allocation_time` | **Regional vCPU Quota Headroom Monitoring** |
| **Container Memory Allocation** | `run.googleapis.com/container/memory/allocation_time` | **Regional RAM Quota Headroom Monitoring** |
| **Traffic Drop Anomaly** | `run.googleapis.com/request_count` | **Guarded Anomaly Alert**: Drop vs. 1h baseline (`rate(...[1h]) > 1.0`) |
| **Cold Start Latency** | `run.googleapis.com/container/startup_latencies` | **Conditional Diagnostic**: P95 startup latency over 30m window |
