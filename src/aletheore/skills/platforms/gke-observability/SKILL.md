---
name: gke-observability
description: Configures GKE observability, including Cloud Logging, Cloud Monitoring, and managed Prometheus. Use when configuring GKE monitoring, setting up GKE logging, or configuring Prometheus metrics collection, and to troubleshoot Managed Service for Prometheus (GMP) issues such as missing metrics, unhealthy scrape targets, PodMonitoring misconfiguration, rule/alert evaluation failures, and monitoring permission errors. Don't use to configure local application logging frameworks or external APMs outside GKE.
metadata:
  version: 1.1.0
  category: CloudObservabilityAndMonitoring
skill_id: platforms/gke-observability
title: GKE Observability
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-observability
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-observability", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-observability", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["mcp"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Observability

This reference covers monitoring, logging, and metrics configuration for GKE.
The golden path enables comprehensive observability including control-plane
metrics.

> **MCP Tools:** `get_cluster`, `list_k8s_events`, `get_k8s_logs`,
> `get_k8s_cluster_info`, `describe_k8s_resource`. **CLI-only:** `gcloud
> container clusters update --monitoring=...`, `gcloud logging read`

## Golden Path Observability Defaults

Setting                                             | Golden Path Value                                                                                                                                   | Notes
--------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | -----
`loggingConfig` components                          | SYSTEM_COMPONENTS, WORKLOADS                                                                                                                        | Full workload logging
`monitoringConfig` components                       | SYSTEM_COMPONENTS, STORAGE, POD, DEPLOYMENT, STATEFULSET, DAEMONSET, HPA, JOBSET, CADVISOR, KUBELET, DCGM, APISERVER, SCHEDULER, CONTROLLER_MANAGER | Full suite including control-plane
`managedPrometheusConfig.enabled`                   | `true`                                                                                                                                              | Google-managed Prometheus
`advancedDatapathObservabilityConfig.enableMetrics` | `true`                                                                                                                                              | Dataplane V2 flow metrics
`loggingService`                                    | `logging.googleapis.com/kubernetes`                                                                                                                 | Cloud Logging
`monitoringService`                                 | `monitoring.googleapis.com/kubernetes`                                                                                                              | Cloud Monitoring

### Control-Plane Metrics (Golden Path Addition)

The golden path adds three control-plane monitoring components not present in
default clusters:

| Component            | What It Monitors                                                       |
| -------------------- | ---------------------------------------------------------------------- |
| `APISERVER`          | API server request latency, error rates, admission webhook performance |
| `SCHEDULER`          | Scheduling latency, pending pods, scheduling failures                  |
| `CONTROLLER_MANAGER` | Controller work queue depth, reconciliation latency                    |

These are critical for diagnosing cluster-level issues (slow API responses,
scheduling delays, stuck controllers).

## Enabling Full Monitoring

**Say this whenever you hand over a `--monitoring` command:**

1.  **Control-plane metrics are NOT enabled by default.** State this outright in
    your answer — do not leave it implied by the fact that you are supplying an
    enable command. `API_SERVER`, `SCHEDULER`, and `CONTROLLER_MANAGER` are off
    on every new cluster and collect nothing until explicitly turned on, and the
    same is true of `DCGM`, `CADVISOR`, `KUBELET`, and kube-state (`POD`,
    `DEPLOYMENT`, `STATEFULSET`, `DAEMONSET`, `HPA`, `STORAGE`, `JOBSET`).
    `SYSTEM` is the only package on by default. A user asking "why are there no
    API server metrics" has almost always simply never enabled them.
2.  **The flag replaces, it does not append.** The set supplied to `--monitoring`
    overrides the previous setting entirely, so omitting a component silently
    turns it off. Always pass the full desired list, and always include `SYSTEM`
    — it cannot be disabled while monitoring is on, and never on Autopilot.
3.  **These metrics bill per sample ingested** via Managed Service for
    Prometheus. Enabling the full suite on a large cluster is a real cost
    increase; mention it rather than presenting the list as free.

> **The gcloud flag and the API field use different spellings for the same
> components.** Do not copy names between them:
>
> Component        | `gcloud --monitoring=` | `monitoringConfig` API enum
> ---------------- | ---------------------- | ---------------------------
> System           | `SYSTEM`               | `SYSTEM_COMPONENTS`
> API server       | `API_SERVER`           | `APISERVER`
> Controller mgr   | `CONTROLLER_MANAGER`   | `CONTROLLER_MANAGER`
>
> The remaining components share a spelling. Using an API enum in the CLI flag
> (or the reverse) fails the command — this is a common and confusing error.

```bash
# Enable golden path monitoring suite
gcloud container clusters update <CLUSTER_NAME> --region <REGION> \
  --monitoring=SYSTEM,API_SERVER,SCHEDULER,CONTROLLER_MANAGER,STORAGE,POD,DEPLOYMENT,STATEFULSET,DAEMONSET,HPA,JOBSET,CADVISOR,KUBELET,DCGM \
  --quiet

# Enable Managed Prometheus
gcloud container clusters update <CLUSTER_NAME> --region <REGION> \
  --enable-managed-prometheus \
  --quiet

# Enable Dataplane V2 observability metrics
gcloud container clusters update <CLUSTER_NAME> --region <REGION> \
  --enable-dataplane-v2-flow-observability \
  --quiet
```

## Managed Prometheus

Golden path enables Google Managed Prometheus for metrics collection and
querying.

**Querying metrics:**

-   Use Cloud Monitoring Metrics Explorer in the console
-   Use PromQL via the Prometheus UI or API
-   Grafana dashboards via Managed Grafana

**Key GKE metrics:**

| Metric                                            | Source             | Use                    |
| -------------------------------------------------- | ------------------ | ---------------------- |
| `container_cpu_usage_seconds_total`                | cAdvisor           | Pod CPU usage          |
| `container_memory_working_set_bytes`               | cAdvisor           | Pod memory usage       |
| `kube_pod_status_phase`                            | kube-state-metrics | Pod lifecycle          |
| `apiserver_request_duration_seconds`               | API Server         | Control plane latency  |
| `scheduler_scheduling_attempt_duration_seconds`    | Scheduler          | Scheduling performance |
| `kubernetes.io/node/cpu/core_usage_time`           | Cloud Monitoring   | Node CPU               |
| `DCGM_FI_DEV_GPU_UTIL`                             | DCGM               | GPU utilization        |

## Live Resource Usage (kubectl-only)

No MCP or gcloud equivalent exists for live resource usage. Use `kubectl top`:

```bash
kubectl top pods --all-namespaces --sort-by=cpu
kubectl top nodes
kubectl top pods --containers -n <NAMESPACE>  # per-container breakdown
```

## Cloud Logging (gcloud-only)

**Querying cluster logs** (no MCP equivalent — use `gcloud logging read`):

```bash
# System component logs
gcloud logging read \
  'resource.type="k8s_cluster" AND resource.labels.cluster_name="<CLUSTER_NAME>"' \
  --project <PROJECT_ID> --limit 50 \
  --quiet

# Workload logs for a specific namespace
gcloud logging read \
  'resource.type="k8s_container" AND resource.labels.cluster_name="<CLUSTER_NAME>" AND resource.labels.namespace_name="<NAMESPACE>"' \
  --project <PROJECT_ID> --limit 50 \
  --quiet

# Audit logs (who did what)
gcloud logging read \
  'resource.type="k8s_cluster" AND logName:"cloudaudit.googleapis.com"' \
  --project <PROJECT_ID> --limit 50 \
  --quiet
```

## Diagnostic Settings

For security monitoring and troubleshooting, enable control-plane audit logs:

```bash
# View current logging config
gcloud container clusters describe <CLUSTER_NAME> --region <REGION> \
  --format="yaml(loggingConfig)" \
  --quiet
```

## Alerting

Set up alerts for critical conditions:

Condition               | Metric                                              | Threshold
----------------------- | --------------------------------------------------- | ---------
High API server latency | `apiserver_request_duration_seconds`                | P99 > 5s
Pod crash loops         | `kube_pod_container_status_restarts_total`          | > 5 in 10min
Node not ready          | `kube_node_status_condition`                        | condition=Ready, status!=True
High GPU utilization    | `DCGM_FI_DEV_GPU_UTIL`                              | > 95% sustained
PVC near capacity       | `kubelet_volume_stats_used_bytes / capacity`        | > 85%
Scheduling failures     | `scheduler_schedule_attempts_total{result="error"}` | > 0

> **Prerequisite:** The `kube_*` series above (e.g., `kube_pod_status_phase`,
> `kube_pod_container_status_restarts_total`, `kube_node_status_condition`)
> come from **kube-state-metrics**, which GKE does not collect by default.
> Deploy the Managed Prometheus kube-state-metrics package first.

### Proposing Dashboards & Alerts (Production Rules)

When designing or proposing alerting and dashboard strategies for GKE:

1.  **Always explicitly name Google Cloud Monitoring** as the platform to
    implement these alerts and dashboards.
2.  **Always include API server latency** (via
    `apiserver_request_duration_seconds` metric) on the dashboard as a critical
    indicator of control plane health, alongside node CPU/Memory and pod crash
    loops.

### Node Health (Production Rules)

A comprehensive assessment of node health relies on analyzing these two metrics together:

1.  **`kubernetes.io/node/status_condition`** (filtered by `status_condition="Ready"`): Use this to track healthy nodes. Note that it will only report values for nodes that have successfully bootstrapped.
2.  **`compute.googleapis.com/instance_group/size`** (filtered by `instance_group_name="gke-<cluster_name>-.*"`): Use this to track the total number of nodes in a specific cluster. Note that it does not differentiate between healthy and unhealthy nodes.

## Cost Considerations

Monitoring and logging have associated costs:

-   **Cloud Logging**: Charged per GiB ingested beyond free tier (50
    GiB/project/month)
-   **Cloud Monitoring**: Free for GKE system metrics; custom metrics charged
    per time series
-   **Managed Prometheus**: Charged per samples ingested

To reduce costs in non-production:

```bash
# Reduce to system-only monitoring
gcloud container clusters update <CLUSTER_NAME> --region <REGION> \
  --monitoring=SYSTEM \
  --quiet
```

## Distributed Tracing & Continuous Profiling (Recommended)

**Not golden path defaults** — recommended for production microservice
architectures and performance-sensitive workloads.

-   **Cloud Trace**: Add OpenTelemetry SDK to your app with the
    `opentelemetry-operations-go` (or equivalent) exporter. Traces appear in
    Cloud Trace console. Identifies cross-service latency bottlenecks.
-   **Cloud Profiler**: Add the Cloud Profiler agent to your app. Profiles CPU
    and memory usage in production with low overhead. Identifies hotspots and
    compares across versions.

**Recent additions:**

-   **Managed OpenTelemetry for GKE (Preview)**: Managed in-cluster OTLP
    endpoint plus auto-instrumentation for traces, metrics, and logs. Requires
    GKE 1.34.1-gke.2178000+; enable with `gcloud beta container clusters
    update ... --managed-otel-scope=COLLECTION_AND_INSTRUMENTATION_COMPONENTS`.
-   **PSI (Pressure Stall Information) metrics**: cAdvisor
    `container_pressure_{cpu,memory,io}_{waiting,stalled}_seconds_total` series
    (beta in Kubernetes 1.34) can be collected via a Managed Prometheus
    `ClusterNodeMonitoring` resource; GKE's documented collection path requires
    GKE 1.35+.

## LQL Query Examples

Common Logging Query Language patterns for GKE troubleshooting:

```
# Error logs for a specific container
resource.type="k8s_container" AND resource.labels.container_name="my-app" AND severity>=ERROR

# OOMKilled events
resource.type="k8s_event" AND jsonPayload.reason="OOMKilling"

# Pod scheduling failures
resource.type="k8s_event" AND jsonPayload.reason="FailedScheduling"

# Audit logs (who did what)
resource.type="k8s_cluster" AND logName:"cloudaudit.googleapis.com"
```

## Troubleshooting Managed Prometheus (GMP)

Diagnose GMP ingestion, rule, and query problems. Stay read-only (`kubectl get`
/ `describe` / `logs`) and propose config changes; do not mutate live resources
directly.

### First: split ingestion-side vs query-side

Before anything else, query the `up` metric in the **Metrics Explorer PromQL
tab** in Cloud Monitoring. If `up` returns data, ingestion works and the problem
is query-side (Grafana / PromQL / permissions). If `up` is empty, the problem is
ingestion-side (collectors, scrape config, or write permission).

### Ingestion-side

1.  **Check GMP system pods.** They run in `gmp-system` on Standard clusters and
    `gke-gmp-system` on Autopilot. Look for `gmp-operator`, `collector`
    (DaemonSet), and `rule-evaluator` not `Running` or with high restarts:

    ```bash
    kubectl get pods -n gmp-system            # gke-gmp-system on Autopilot
    kubectl logs -n gmp-system -l app.kubernetes.io/name=collector -c prometheus
    ```

    A collector in `CrashLoopBackOff` with `OOMKilled` usually means high metric
    cardinality - drop unneeded series/labels (see cost section below) or apply a
    VPA to the collector.

2.  **Check PodMonitoring / ClusterPodMonitoring.** The three classic mistakes:
    - `spec.selector.matchLabels` does not match the target Pod labels.
    - A `PodMonitoring` only discovers targets **in its own namespace** - use
      `ClusterPodMonitoring` for cluster-wide scope.
    - `spec.endpoints.port` must reference the **named** container port (e.g.
      `port: web`), not the port number.

3.  **Enable target status for scrape errors.** Propose patching
    `OperatorConfig` in `gmp-public` with `features.targetStatus.enabled: true`;
    once applied, `kubectl describe podmonitoring <name>` and read `Active Targets`,
    `Unhealthy Targets`, and `Last Error` (for example `connection refused`, HTTP 404,
    `context deadline exceeded`). Disable it again when done - it can OOM the
    operator on large clusters.

### Permissions (403 / no data written)

GMP components inherit the **node service account**. Ingestion needs
`roles/monitoring.metricWriter` (error `Permission monitoring.timeSeries.create
denied` in collector logs); the `rule-evaluator` and query paths need
`roles/monitoring.viewer` (403 / `PermissionDenied`). If a query app (like
Grafana) uses Workload Identity, the bound Google service account also needs
`roles/monitoring.viewer`.

### Rule and alert evaluation

Rule scope is decided by the resource kind: `Rules` (single namespace),
`ClusterRules` (whole cluster), and `GlobalRules` (all data in the metrics
scope). You **must** use `GlobalRules` to write rules against Cloud Monitoring
metrics - a `Rules`/`ClusterRules` resource silently returns no data for them.
Check `rule-evaluator` logs (`-c evaluator`) for parse/permission errors.

### Query-side (Grafana / PromQL)

-   **Data source** must point at the GMP frontend query proxy, not
    `localhost:9090`, and the HTTP **Method must be GET** - `POST` fails with
    `no match[] parameter provided`.
-   **Grafana template variables:** use the two-argument form
    `label_values(<metric>, <label>)`; the single-argument
    `label_values(<label>)` is not supported by the GMP API.
-   **Cloud Monitoring metrics** that exist for multiple resource types need a
    `monitored_resource` label matcher, otherwise the query fails with
    `series selector must specify a label matcher on monitored resource name`.

### Cost, cardinality, and quota

Use the Cloud Monitoring **Metrics Management** page to find the metrics driving
billable samples and high cardinality. Reduce them with `metricRelabeling` in
the `PodMonitoring` (`action: drop` for whole metrics, `action: labeldrop` for
unbounded labels like `user_id`/`request_id`) or by raising the scrape
`interval`. `429` / `RESOURCE_EXHAUSTED` errors mean you have hit the Cloud
Monitoring API ingestion or query quota - optimize first, then request a quota
increase.

## Supporting Links

-   [GKE system metrics](https://docs.cloud.google.com/monitoring/api/metrics_kubernetes)
-   [GKE Observability Documentation](https://cloud.google.com/kubernetes-engine/docs/concepts/observability)
-   [Google Cloud Managed Service for Prometheus](https://cloud.google.com/stackdriver/docs/managed-prometheus)
-   [Troubleshoot Managed Service for Prometheus](https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/troubleshooting.md.txt)
-   [Rule evaluation (Rules / ClusterRules / GlobalRules)](https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/rules-managed.md.txt)
-   [Cloud Logging Query Language (LQL)](https://cloud.google.com/logging/docs/view/logging-query-language)
-   [Google Cloud Monitoring Alerts](https://cloud.google.com/monitoring/alerts)
