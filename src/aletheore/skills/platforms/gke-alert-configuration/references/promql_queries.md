# PromQL Queries Reference for Kubernetes Alerting

This document provides best-practice PromQL queries for monitoring Kubernetes
clusters and services, organized by the Golden Signals and key Cluster Health
metrics.

All queries are designed to be used with **Google Cloud Managed Service for
Prometheus (GMP)** and can be deployed via Cloud Monitoring alerting policies
using `condition_prometheus_query_language`.

--------------------------------------------------------------------------------

## Table of Contents

*   [Latency Alerts (P95 Response Time)](#latency-alerts-p95-response-time)
    (~line 47)
    *   [Query (Standard Prometheus HTTP Metrics)](#query-standard-prometheus-http-metrics)
        (~line 51)
    *   [Query (OpenTelemetry Semantic Conventions)](#query-opentelemetry-semantic-conventions)
        (~line 66)
*   [Traffic Alerts (Request Rate and Outage Detection)](#traffic-alerts-request-rate-and-outage-detection)
    (~line 90)
    *   [Query (Request Rate QPS)](#query-request-rate-qps) (~line 95)
    *   [Traffic Drop and Outage Detection (absent() or default 0)](#traffic-drop-and-outage-detection-absent-or-default-0)
        (~line 108)
*   [Error Alerts (Multi-Window Multi-Burn-Rate SLO)](#error-alerts-multi-window-multi-burn-rate-slo)
    (~line 179)
    *   [Fast Burn Rate Alert (14.4x over 1 hour and 5 minutes)](#fast-burn-rate-alert-144x-over-1-hour-and-5-minutes)
        (~line 188)
    *   [Medium Burn Rate Alert (6.0x over 6 hours and 30 minutes)](#medium-burn-rate-alert-60x-over-6-hours-and-30-minutes)
        (~line 243)
    *   [Slow Burn Rate Alert (1.0x over 3 days and 6 hours)](#slow-burn-rate-alert-10x-over-3-days-and-6-hours)
        (~line 296)
*   [Saturation Alerts (Memory Limit Utilization)](#saturation-alerts-memory-limit-utilization)
    (~line 352)
    *   [Query (cAdvisor Memory Limit Saturation)](#query-cadvisor-memory-limit-saturation)
        (~line 362)
*   [Cluster and Workload Health Alerts](#cluster-and-workload-health-alerts)
    (~line 392)
    *   [Pod CrashLooping and Frequent Restarts](#pod-crashlooping-and-frequent-restarts)
        (~line 396)
    *   [Node NotReady Condition](#node-notready-condition) (~line 416)
    *   [API Server Request Errors](#api-server-request-errors) (~line 432)

--------------------------------------------------------------------------------

## Latency Alerts (P95 Response Time)

Monitors the 95th percentile of HTTP request duration to detect slow responses.

### Query (Standard Prometheus HTTP Metrics)

```promql
histogram_quantile(0.95,
  sum(
    rate(
      http_request_duration_seconds_bucket{
        cluster="${var.cluster_name}",
        namespace="${var.namespace}"
      }[5m]
    )
  ) by (le, service, ingress)
) > 2.0
```

### Query (OpenTelemetry Semantic Conventions)

If your application is instrumented with OpenTelemetry, it might use
`http_server_request_duration_milliseconds`:

```promql
histogram_quantile(0.95,
  sum(
    rate(
      http_server_request_duration_milliseconds_bucket{
        cluster="${var.cluster_name}",
        namespace="${var.namespace}"
      }[5m]
    )
  ) by (le, service)
) > 2000
```

*   **Threshold**: `2.0` seconds (or `2000` ms). Adjust based on latency target.
*   **Buffer**: Use `duration = "300s"` in the alert policy for instantaneous
    percentiles or `duration = "0s"` when multi-window smoothing is applied.

--------------------------------------------------------------------------------

## Traffic Alerts (Request Rate and Outage Detection)

Monitors the volume of requests to detect sudden drops (indicating outage) or
overload spikes.

### Query (Request Rate QPS)

```promql
sum(
  rate(
    http_requests_total{
      cluster="${var.cluster_name}",
      namespace="${var.namespace}"
    }[5m]
  )
) by (service)
```

### Traffic Drop and Outage Detection (`absent()` or `default 0`)

> [!WARNING] **The `== 0` Metric Disappearance Gotcha**: When a service
> experiences a total traffic outage, HTTP requests stop entirely and Prometheus
> stops emitting the `http_requests_total` metric series. Evaluating
> `rate(...) == 0` returns an **empty vector** (no data) rather than a `0`
> value, so the alert will **fail to fire**.

To reliably detect a complete traffic loss, use **`default 0`** or
**`absent()`**:

#### Option A: `default 0` Anomaly Comparison (Recommended)

Fires if current 5m traffic is zero when historical traffic 1 week ago was
active (> 1 QPS):

```promql
(
  sum(
    rate(
      http_requests_total{
        cluster="${var.cluster_name}",
        namespace="${var.namespace}"
      }[5m]
    )
  ) by (service) default 0
) == 0
and
(
  sum(
    rate(
      http_requests_total{
        cluster="${var.cluster_name}",
        namespace="${var.namespace}"
      }[5m] offset 1w
    )
  ) by (service)
) > 1
```

#### Option B: Complete Metric Disappearance (`absent()`)

Fires if the `http_requests_total` time series disappears entirely from the
cluster or namespace:

```promql
absent(
  http_requests_total{
    cluster="${var.cluster_name}",
    namespace="${var.namespace}"
  }
) == 1
```

#### Option C: Instantaneous Zero Traffic (`default 0`)

```promql
(
  sum(
    rate(
      http_requests_total{
        cluster="${var.cluster_name}",
        namespace="${var.namespace}"
      }[5m]
    )
  ) by (service) default 0
) == 0
```

--------------------------------------------------------------------------------

## Error Alerts (Multi-Window Multi-Burn-Rate SLO)

Multi-Window Multi-Burn-Rate (MWMBR) alerting evaluates error budget consumption
across two simultaneous lookback windows (short-window and long-window). Both
windows must exceed the burn rate threshold for the alert to fire, eliminating
false alerts from short bursts while rapidly catching critical outages.

Formula: `Burn Rate = (Errors / Total Requests in Lookback) / (1 - SLO Target)`

### Fast Burn Rate Alert (14.4x over 1 hour and 5 minutes)

*   **Severity**: Critical (Page on-call). Consumes 2% of 30-day budget in 1
    hour (100% budget consumed in 2 days).
*   **Duration**: Set `duration = "0s"` (lookback windows already smooth
    spikes).

```promql
(
  (
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}",
          status=~"5.."
        }[5m]
      )
    ) by (service)
    /
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}"
        }[5m]
      )
    ) by (service)
  ) > (1 - ${var.slo_target}) * 14.4
)
and
(
  (
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}",
          status=~"5.."
        }[1h]
      )
    ) by (service)
    /
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}"
        }[1h]
      )
    ) by (service)
  ) > (1 - ${var.slo_target}) * 14.4
)
```

### Medium Burn Rate Alert (6.0x over 6 hours and 30 minutes)

*   **Severity**: High (Page / Ticket). Consumes 5% of 30-day budget in 6 hours.
*   **Duration**: Set `duration = "0s"`.

```promql
(
  (
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}",
          status=~"5.."
        }[30m]
      )
    ) by (service)
    /
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}"
        }[30m]
      )
    ) by (service)
  ) > (1 - ${var.slo_target}) * 6.0
)
and
(
  (
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}",
          status=~"5.."
        }[6h]
      )
    ) by (service)
    /
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}"
        }[6h]
      )
    ) by (service)
  ) > (1 - ${var.slo_target}) * 6.0
)
```

### Slow Burn Rate Alert (1.0x over 3 days and 6 hours)

*   **Severity**: Warning (Ticket / Email). Consumes 10% of 30-day budget in 3
    days.
*   **Duration**: Set `duration = "0s"`.

```promql
(
  (
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}",
          status=~"5.."
        }[6h]
      )
    ) by (service)
    /
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}"
        }[6h]
      )
    ) by (service)
  ) > (1 - ${var.slo_target}) * 1.0
)
and
(
  (
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}",
          status=~"5.."
        }[3d]
      )
    ) by (service)
    /
    sum(
      rate(
        http_requests_total{
          cluster="${var.cluster_name}",
          namespace="${var.namespace}"
        }[3d]
      )
    ) by (service)
  ) > (1 - ${var.slo_target}) * 1.0
)
```

--------------------------------------------------------------------------------

## Saturation Alerts (Memory Limit Utilization)

Monitors how close workloads are to their allocated memory resource limits.

> [!NOTE] **Memory Saturation versus CPU Saturation**: In Kubernetes, CPU is
> compressible and throttled by CFS quotas. Memory is uncompressible and causes
> immediate `OOMKilled` termination. For cluster alerting suites, only **Memory
> Saturation** is included. CPU saturation alerts and
> `container_cpu_usage_seconds_total` are excluded.

### Query (cAdvisor Memory Limit Saturation)

```promql
sum(
  container_memory_working_set_bytes{
    cluster="${var.cluster_name}",
    namespace="${var.namespace}",
    container!=""
  }
) by (pod, container)
/
sum(
  container_spec_memory_limit_bytes{
    cluster="${var.cluster_name}",
    namespace="${var.namespace}",
    container!=""
  }
) by (pod, container) > 0.90
```

*   **Metric**: `container_memory_working_set_bytes` divided by
    `container_spec_memory_limit_bytes`.
*   **Threshold**: `0.90` (90% limit utilization).
*   **Duration**: `duration = "300s"` in Terraform alert policy.
*   **Mandatory User Warning**: Memory limits must be explicitly configured in
    the container pod specifications (`resources.limits.memory`) for this PromQL
    expression to resolve (and avoid `NaN`).

--------------------------------------------------------------------------------

## Cluster and Workload Health Alerts

Alerts that monitor the foundational health of nodes and pods.

### Pod CrashLooping and Frequent Restarts

Detects containers restarting repeatedly (CrashLoopBackOff).

```promql
sum(
  increase(
    kube_pod_container_status_restarts_total{
      cluster="${var.cluster_name}",
      namespace="${var.namespace}"
    }[15m]
  )
) by (pod, container) > 3
```

*   **Window**: `[15m]` lookback with threshold `> 3`.
*   **Duration**: **`duration = "0s"`** (or `"60s"`). The `[15m]` window already
    provides time smoothing; adding `duration = "300s"` unnecessarily delays
    alerts.

### Node NotReady Condition

Detects nodes that have transitioned to a NotReady state.

```promql
kube_node_status_condition{
  cluster="${var.cluster_name}",
  condition="Ready",
  status="true"
} == 0
```

*   **Condition**: `condition="Ready", status="true" == 0`
*   **Duration**: `duration = "300s"` to prevent alerting during transient node
    restarts or scheduled cluster upgrades.

### API Server Request Errors

Detects elevated error rates on the Kubernetes API Server control plane.

```promql
sum(
  rate(
    apiserver_request_total{
      job="apiserver",
      code=~"5.."
    }[1m]
  )
) by (instance, job)
/
sum(
  rate(
    apiserver_request_total{
      job="apiserver"
    }[1m]
  )
) by (instance, job) * 100 > 3
```
