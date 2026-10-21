# Cloud Run Worker Pools Alert Configuration

Best-practice observability for Cloud Run Worker Pools.

--------------------------------------------------------------------------------

## Table of Contents

*   [Fast-Path Defaults](#fast-path-defaults)
*   [Default Constants & Override Protocol](#default-constants-override-protocol)
*   [SLO Delegation & Fallback Rule](#slo-delegation-fallback-rule)
*   [Workload Target Discovery](#workload-target-discovery)
    *   [Autonomous Parameter Discovery](#autonomous-parameter-discovery)
*   [PromQL & Terraform Query Templates](#promql-terraform-query-templates)
    *   [1. Task Success Rate SLO Burn Rate Queries](#1-task-success-rate-slo-burn-rate-queries)
    *   [2. Queue Backlog Drain Time (ETD)](#2-queue-backlog-drain-time-etd)
    *   [3. Oldest Unacked Message Age](#3-oldest-unacked-message-age)
    *   [4. Health Probe Success Rate SLO Burn Rate Queries](#4-health-probe-success-rate-slo-burn-rate-queries)
*   [Telemetry Data Sources Reference](#telemetry-data-sources-reference)

--------------------------------------------------------------------------------

## Fast-Path Defaults
When standard alerting is requested or thresholds are unspecified, suggest the following 4-policy standard suite:

1. **Task Success Rate SLO (Fast Burn)**: 99.9% target, 1h & 5m windows, $14.4\times$ burn rate, `duration = "300s"` (Critical/Paging).
2. **Task Success Rate SLO (Slow Burn)**: 99.9% target, 3d & 6h windows, $1.0\times$ burn rate, **omit `duration`** (Warning/Ticket).
3. **Queue Backlog Drain Time (ETD)**: Estimated time to drain queue exceeds processing SLA threshold (e.g., >30m) with `duration = "300s"` retest buffer (Warning/Paging).
4. **Oldest Unacked Message Age**: Oldest unacknowledged message age exceeds SLA threshold (Critical/Paging).

> [!NOTE]
> Unlike HTTP Services (where container saturation directly causes HTTP 504 errors), Worker Pools operating at `max_instances` are operating at maximum efficiency. As long as Queue Drain Time (ETD) and Message Age remain within SLA, instance saturation is expected and does not trigger alerts.

---

## Default Constants & Override Protocol

All generated Terraform alert policies use explicit default constants. Users can override any parameter in HCL variables or via prompt instruction:

| Constant / Parameter | Explicit Default | HCL Variable Name | Description & Override Option |
| :--- | :--- | :--- | :--- |
| `task_success_slo_target` | `0.999` (99.9%) | `var.task_success_slo_target` | Task processing success rate target. Override to 0.99 or 0.9999. |
| `health_probe_slo_target` | `0.999` (99.9%) | `var.health_probe_slo_target` | Process health probe attempt success target. |
| `max_drain_time_second` | `1800` (30m) | `var.max_drain_time_second` | Maximum allowed Queue Estimated Time to Drain (ETD) SLA. |
| `oldest_message_age_second`| `1800` (30m) | `var.oldest_message_age_second`| Maximum allowed unacknowledged message age SLA. |
| `retest_duration_buffer` | `"300s"` (5m) | `var.retest_duration_buffer` | Retest window for lookbacks $\le 25$h. Omitted for lookbacks $>25$h. |

---

## SLO Delegation & Fallback Rule

* **If `google-cloud-slo-alert-configuration` skill is available**: use it to inform SLO creation.
* **If `google-cloud-slo-alert-configuration` skill is NOT available**: Use the standard PromQL burn-rate templates provided directly in this reference guide as a complete, standalone solution.

---

## Workload Target Discovery

### Autonomous Parameter Discovery
Before proposing alerts, ensure the Google Cloud SDK is installed and configured properly. Then discover worker pool telemetry and queue bindings:

* **Queue Integration (Direct API & Config Scan)**:
  1. **Worker Pool API**: Run `gcloud run worker-pools describe [NAME] --format="json"` to check container environment variables (`spec.template.spec.containers[].env`) for `PUBSUB_SUBSCRIPTION`, `SUBSCRIPTION_ID`, or `TOPIC_ID`.
  2. **Service Account IAM Binding**: Extract `serviceAccountName` from the worker pool description and run `gcloud pubsub subscriptions list --format="json"` to check which subscriptions grant `roles/pubsub.subscriber` to that service account.
  3. **Terraform Scan**: Search `.tf` files for `google_pubsub_subscription` resources associated with the worker pool module.
  * *Outcome*: If a Pub/Sub subscription is identified, apply Task Success SLOs, Backlog Drain Time (ETD), and Message Age alerts.
* **Non-Pub/Sub & Alternative Consumer Workloads**:
  Worker pools can also operate as pull-based consumers for alternative queue systems (e.g., Apache Kafka, RabbitMQ) or run custom background event loops without Pub/Sub bindings. When not bound to Pub/Sub, the primary alerting signals to configure are container CPU/Memory utilization and process health probes (`run.googleapis.com/container/completed_probe_attempt_count`).

---

## PromQL & Terraform Query Templates

### 1. Task Success Rate SLO Burn Rate Queries

#### Fast Burn (1h & 5m Windows)

```promql
(
  (sum(rate(pubsub_googleapis_com:subscription_nack_requests{subscription_id="[SUBSCRIPTION_ID]"}[5m])) or vector(0))
  /
  (
    (sum(rate(pubsub_googleapis_com:subscription_ack_message_count{subscription_id="[SUBSCRIPTION_ID]"}[5m])) or vector(0))
    +
    (sum(rate(pubsub_googleapis_com:subscription_nack_requests{subscription_id="[SUBSCRIPTION_ID]"}[5m])) or vector(0))
    + 0.001
  )
  > (1 - [SLO_TARGET]) * 14.4
)
and
(
  (sum(rate(pubsub_googleapis_com:subscription_nack_requests{subscription_id="[SUBSCRIPTION_ID]"}[1h])) or vector(0))
  /
  (
    (sum(rate(pubsub_googleapis_com:subscription_ack_message_count{subscription_id="[SUBSCRIPTION_ID]"}[1h])) or vector(0))
    +
    (sum(rate(pubsub_googleapis_com:subscription_nack_requests{subscription_id="[SUBSCRIPTION_ID]"}[1h])) or vector(0))
    + 0.001
  )
  > (1 - [SLO_TARGET]) * 14.4
)
```

#### Slow Burn (3d & 6h Windows)
> [!WARNING]
> Lookback windows >25h (like `[3d]`) require **omitting `duration`** in HCL.

```promql
(
  (sum(rate(pubsub_googleapis_com:subscription_nack_requests{subscription_id="[SUBSCRIPTION_ID]"}[6h])) or vector(0))
  /
  (
    (sum(rate(pubsub_googleapis_com:subscription_ack_message_count{subscription_id="[SUBSCRIPTION_ID]"}[6h])) or vector(0))
    +
    (sum(rate(pubsub_googleapis_com:subscription_nack_requests{subscription_id="[SUBSCRIPTION_ID]"}[6h])) or vector(0))
    + 0.001
  )
  > (1 - [SLO_TARGET]) * 1.0
)
and
(
  (sum(rate(pubsub_googleapis_com:subscription_nack_requests{subscription_id="[SUBSCRIPTION_ID]"}[3d])) or vector(0))
  /
  (
    (sum(rate(pubsub_googleapis_com:subscription_ack_message_count{subscription_id="[SUBSCRIPTION_ID]"}[3d])) or vector(0))
    +
    (sum(rate(pubsub_googleapis_com:subscription_nack_requests{subscription_id="[SUBSCRIPTION_ID]"}[3d])) or vector(0))
    + 0.001
  )
  > (1 - [SLO_TARGET]) * 1.0
)
```

### 2. Queue Backlog Drain Time (ETD)
Monitors estimated queue drain processing time in seconds.
> [!IMPORTANT]
> PromQL conditions for ETD MUST specify `duration = "300s"` (5-minute retest buffer) in HCL. This buffer absorbs temporary ETD spikes caused by autoscaler scale-up lag during sudden bursty traffic.

#### PromQL Query

```promql
(
  (sum(pubsub_googleapis_com:subscription_num_undelivered_messages{subscription_id="[SUBSCRIPTION_ID]"}) or vector(0))
  /
  clamp_min((sum(rate(pubsub_googleapis_com:subscription_ack_message_count{subscription_id="[SUBSCRIPTION_ID]"}[5m])) or vector(0)), 0.05)
) > [MAX_DRAIN_TIME_SECONDS]
```

#### Terraform Snippet (ETD Alert Policy)

```hcl
variable "worker_pools_etd" {
  description = "Map of worker pools to subscription IDs and maximum drain time SLAs (in seconds)"
  type = map(object({
    subscription_id       = string
    max_drain_time_second = number
  }))
  default = {
    "order-processor" = {
      subscription_id       = "order-topic-sub"
      max_drain_time_second = 1800 # 30 minutes SLA
    }
  }
}

variable "retest_duration_buffer" {
  description = "Retest duration buffer for lookbacks <= 25h"
  type        = string
  default     = "300s"
}

resource "google_monitoring_alert_policy" "queue_backlog_etd" {
  for_each     = var.worker_pools_etd
  project      = var.scoping_project_id
  display_name = "[Worker Pool] Queue Backlog Estimated Time to Drain (ETD) Exceeded - ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = "Estimated drain time on ${each.value.subscription_id} exceeds ${each.value.max_drain_time_second}s"
    condition_prometheus_query_language {
      query = <<-EOT
        (
          (sum(pubsub_googleapis_com:subscription_num_undelivered_messages{subscription_id="${each.value.subscription_id}"}) or vector(0))
          /
          clamp_min((sum(rate(pubsub_googleapis_com:subscription_ack_message_count{subscription_id="${each.value.subscription_id}"}[5m])) or vector(0)), 0.05)
        ) > ${each.value.max_drain_time_second}
      EOT
      duration = var.retest_duration_buffer # 5m retest buffer to absorb autoscaling scale-up lag
    }
  }

  user_labels = {
    severity                    = "warning"
    worker                      = each.key
    tier                        = "saturation"
    "created-with-google-skill" = "cloud-run-alert-configuration"
  }

  alert_strategy { auto_close = "604800s" }
  notification_channels = var.notification_channels
}
```

### 3. Oldest Unacked Message Age

```promql
(
  (pubsub_googleapis_com:subscription_oldest_unacked_message_age{subscription_id="[SUBSCRIPTION_ID]"} or vector(0))
) > [AGE_THRESHOLD_SECONDS]
```

### 4. Health Probe Success Rate SLO Burn Rate Queries

#### Fast Burn (1h & 5m Windows)

```promql
(
  (sum(rate(run_googleapis_com:container_completed_probe_attempt_count{monitored_resource="cloud_run_worker_pool", worker_pool_name="[WORKER_POOL_NAME]", is_healthy="false"}[5m])) or vector(0))
  /
  sum(rate(run_googleapis_com:container_completed_probe_attempt_count{monitored_resource="cloud_run_worker_pool", worker_pool_name="[WORKER_POOL_NAME]"}[5m]))
  > (1 - [SLO_TARGET]) * 14.4
)
and
(
  (sum(rate(run_googleapis_com:container_completed_probe_attempt_count{monitored_resource="cloud_run_worker_pool", worker_pool_name="[WORKER_POOL_NAME]", is_healthy="false"}[1h])) or vector(0))
  /
  sum(rate(run_googleapis_com:container_completed_probe_attempt_count{monitored_resource="cloud_run_worker_pool", worker_pool_name="[WORKER_POOL_NAME]"}[1h]))
  > (1 - [SLO_TARGET]) * 14.4
)
```

#### Slow Burn (3d & 6h Windows)
> [!WARNING]
> Lookback windows >25h (like `[3d]`) require **omitting `duration`** in HCL.

```promql
(
  (sum(rate(run_googleapis_com:container_completed_probe_attempt_count{monitored_resource="cloud_run_worker_pool", worker_pool_name="[WORKER_POOL_NAME]", is_healthy="false"}[6h])) or vector(0))
  /
  sum(rate(run_googleapis_com:container_completed_probe_attempt_count{monitored_resource="cloud_run_worker_pool", worker_pool_name="[WORKER_POOL_NAME]"}[6h]))
  > (1 - [SLO_TARGET]) * 1.0
)
and
(
  (sum(rate(run_googleapis_com:container_completed_probe_attempt_count{monitored_resource="cloud_run_worker_pool", worker_pool_name="[WORKER_POOL_NAME]", is_healthy="false"}[3d])) or vector(0))
  /
  sum(rate(run_googleapis_com:container_completed_probe_attempt_count{monitored_resource="cloud_run_worker_pool", worker_pool_name="[WORKER_POOL_NAME]"}[3d]))
  > (1 - [SLO_TARGET]) * 1.0
)
```

---

## Telemetry Data Sources Reference

<!-- mdformat off -->
| Signal Indicator | Native Metric | SRE Strategy |
| :--- | :--- | :--- |
| **Task Availability** | `pubsub.googleapis.com/subscription/nack_requests` | **SLO Skill (`google-cloud-slo-alert-configuration`)**: Fast Burn (1h/5m) & Slow Burn (3d/6h) |
| **Process Health SLO** | `run.googleapis.com/container/completed_probe_attempt_count` | **SLO Skill (`google-cloud-slo-alert-configuration`)**: Probe Failure Ratio (`is_healthy="false"`) Fast & Slow Burn |
| **Queue Backlog ETD** | `pubsub.googleapis.com/subscription/num_undelivered_messages` & `pubsub.googleapis.com/subscription/ack_message_count` | **Estimated Time to Drain (ETD)**: Drain time > SLA threshold with `duration = "300s"` retest buffer |
| **Message Age** | `pubsub.googleapis.com/subscription/oldest_unacked_message_age` | **Threshold Alert**: Message age > SLA limit (e.g., 30m) |
<!-- mdformat on -->
