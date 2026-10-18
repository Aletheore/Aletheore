# GKE Configuration & Telemetry Prerequisites for Alerting Policies

This document details the Google Kubernetes Engine (GKE) and Google Cloud
configurations required to support the 16 critical alerting policies defined in
the reference catalog. It provides cluster operators with the setup commands,
configuration parameters, and verification queries to ensure telemetry flows
correctly for each alert.

--------------------------------------------------------------------------------

## Table of Contents

*   [Telemetry Ingestion Architecture](#telemetry-ingestion-architecture)
    (~line 38)
*   [Prerequisite Matrix by Alert](#prerequisite-matrix-by-alert) (~line 64)
*   [GKE Control Plane Metrics (Tier 1)](#gke-control-plane-metrics-tier-1)
    (~line 90)
    *   [Enable Control Plane Metrics](#enable-control-plane-metrics) (~line 96)
    *   [Verification Query](#verification-query) (~line 112)
*   [Kube-State-Metrics (KSM) (Tier 2)](#kube-state-metrics-ksm-tier-2)
    (~line 122)
    *   [KSM Scrape Configuration](#ksm-scrape-configuration) (~line 127)
    *   [Filtered PodMonitoring Resource (Cost-Optimized)](#filtered-podmonitoring-resource-cost-optimized)
        (~line 134)
*   [Workload Instrumentation (Golden Signals)](#workload-instrumentation-golden-signals)
    (~line 160)
    *   [Prometheus Metrics Exporter](#prometheus-metrics-exporter) (~line 165)
    *   [Workload PodMonitoring Configuration](#workload-podmonitoring-configuration)
        (~line 173)
*   [Google Cloud IAM and API Prerequisites](#google-cloud-iam-and-api-prerequisites)
    (~line 195)
    *   [Required APIs](#required-apis) (~line 200)
    *   [Required IAM Roles](#required-iam-roles) (~line 211)
*   [Verification Runbook](#verification-runbook) (~line 221)

--------------------------------------------------------------------------------

## Telemetry Ingestion Architecture

The following diagram illustrates how metrics for each alert category are
scraped from the cluster and ingested into Google Cloud Monitoring / Managed
Service for Prometheus (GMP).

```mermaid
graph TD
    subgraph GKE Cluster
        subgraph Master Node (Google Managed)
            APIServer[Kubernetes API Server] -->|Exposes apiserver_* & rest_*| GMP_CP[GKE Control Plane Metrics collector]
        end
        subgraph Worker Nodes
            subgraph Kubelet
                cAdvisor[cAdvisor] -->|Exposes container_*| GMP_Operator[GMP Operator / PodMonitoring]
                KubeletVol[Volume Stats] -->|Exposes kubelet_volume_*| GMP_Operator
            end
            KSM[kube-state-metrics pod] -->|Exposes kube_*| GMP_Operator
        end
    end
    GMP_CP -->|Writes to| Google_Cloud_Monitoring[Google Cloud Monitoring / GMP]
    GMP_Operator -->|Writes to| Google_Cloud_Monitoring
```

--------------------------------------------------------------------------------

## Prerequisite Matrix by Alert

This table lists the GKE and Google Cloud prerequisites and configuration
requirements for each of the 16 critical alerts.

Alert Name                                     | Cost Tier       | Required Metrics                                        | GKE Cluster Requirement       | Workload/Resource Requirement
:--------------------------------------------- | :-------------- | :------------------------------------------------------ | :---------------------------- | :----------------------------
**Kubernetes Node not ready**                  | Tier 1 (Native) | `kube_node_status_condition`                            | System Monitoring Enabled     | GKE Nodes running standard Kubelet
**Kubernetes Node memory pressure**            | Tier 1 (Native) | `kube_node_status_condition`                            | System Monitoring Enabled     | GKE Nodes running standard Kubelet
**Kubernetes Node disk pressure**              | Tier 1 (Native) | `kube_node_status_condition`                            | System Monitoring Enabled     | GKE Nodes running standard Kubelet
**Kubernetes Node network unavailable**        | Tier 1 (Native) | `kube_node_status_condition`                            | System Monitoring Enabled     | GKE Nodes running standard Kubelet
**Kubernetes Volume full in four days**        | Tier 1 (Native) | `kubelet_volume_stats_available_bytes`                  | System Monitoring Enabled     | Pods with attached volumes (CSI Driver supported)
**Kubernetes API server errors**               | Tier 1 (Native) | `apiserver_request_total`                               | Control Plane Metrics Enabled | GKE Master nodes active
**Kubernetes API client errors**               | Tier 1 (Native) | `rest_client_requests_total`                            | Control Plane Metrics Enabled | API Clients communicating with API Server
**Kubernetes client certificate expires soon** | Tier 1 (Native) | `apiserver_client_certificate_expiration_seconds_count` | Control Plane Metrics Enabled | Client certificate authentication enabled
**Kubernetes CronJob failing**                 | Tier 2 (KSM)    | `kube_cronjob_status_*`, `kube_cronjob_spec_*`          | `kube-state-metrics` Scraped  | Active CronJobs in cluster
**Kubernetes PersistentVolume error**          | Tier 2 (KSM)    | `kube_persistentvolume_status_phase`                    | `kube-state-metrics` Scraped  | PersistentVolume resources configured
**Kubernetes StatefulSet down**                | Tier 2 (KSM)    | `kube_statefulset_*`                                    | `kube-state-metrics` Scraped  | StatefulSets deployed
**Kubernetes Pod not healthy**                 | Tier 2 (KSM)    | `kube_pod_status_phase`                                 | `kube-state-metrics` Scraped  | Pods running in target namespaces
**Kubernetes Deployment generation mismatch**  | Tier 2 (KSM)    | `kube_deployment_*`                                     | `kube-state-metrics` Scraped  | Deployments deployed
**Kubernetes StatefulSet generation mismatch** | Tier 2 (KSM)    | `kube_statefulset_*`                                    | `kube-state-metrics` Scraped  | StatefulSets deployed
**Kubernetes DaemonSet misscheduled**          | Tier 2 (KSM)    | `kube_daemonset_*`                                      | `kube-state-metrics` Scraped  | DaemonSets deployed
**Kubernetes Job slow completion**             | Tier 2 (KSM)    | `kube_job_*`                                            | `kube-state-metrics` Scraped  | Jobs deployed

--------------------------------------------------------------------------------

## GKE Control Plane Metrics (Tier 1)

The control plane alerts (`KubernetesAPIServerErrors`,
`KubernetesAPIClientErrors`, and `KubernetesClientCertificateExpiresSoon`)
require API server and API client telemetry.

### Enable Control Plane Metrics

By default, GKE master metrics are not scraped. You must explicitly enable them
in the GKE cluster configuration using `gcloud`:

```bash
gcloud container clusters update CLUSTER_NAME \
    --zone=COMPUTE_ZONE \
    --monitoring=SYSTEM,API_SERVER,CONTROLLER_MANAGER,SCHEDULER
```

*   **API_SERVER**: Exposes API server request volumes, error rates, and client
    request metrics.
*   **CONTROLLER_MANAGER & SCHEDULER**: (Optional but recommended) Exposes
    scheduler queue metrics and controller manager execution states.

### Verification Query

Verify that API server telemetry is flowing to Cloud Monitoring:

```promql
sum(rate(apiserver_request_total[5m])) by (code)
```

--------------------------------------------------------------------------------

## Kube-State-Metrics (KSM) (Tier 2)

All workload, replica mismatch, and CronJob alerts require cluster state metrics
(`kube_*` metrics).

### KSM Scrape Configuration

To collect KSM metrics in Google Cloud Managed Service for Prometheus (GMP):

1.  Deploy `kube-state-metrics` to the `kube-system` namespace.
2.  Deploy a GMP `PodMonitoring` custom resource targeting the KSM deployment.

### Filtered PodMonitoring Resource (Cost-Optimized)

To avoid high sample ingestion charges, use `metricRelabeling` to drop all
unused metrics:

```yaml
apiVersion: monitoring.googleapis.com/v1
kind: PodMonitoring
metadata:
  name: kube-state-metrics-filtered
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: kube-state-metrics
  endpoints:
  - port: http-metrics
    interval: 30s
    metricRelabeling:
    - action: keep
      sourceLabels: [__name__]
      regex: "^(kube_deployment_.*|kube_statefulset_.*|kube_daemonset_.*|kube_cronjob_.*|kube_job_.*|kube_pod_status_phase|kube_pod_info|kube_pod_container_status_.*|kube_persistentvolume_status_phase)$"
```

--------------------------------------------------------------------------------

## Workload Instrumentation (Golden Signals)

For Latency, Traffic, Error Rates, and Saturation monitoring, target workloads
must expose application and runtime telemetry.

### Prometheus Metrics Exporter

Ensure application containers expose standard Prometheus metrics endpoints (such
as `/metrics` on port 8080 or 9090) emitting:

*   `http_requests_total` (Traffic & Errors)
*   `http_request_duration_seconds_bucket` (Latency)

### Workload PodMonitoring Configuration

Create a `PodMonitoring` resource in the workload's namespace:

```yaml
apiVersion: monitoring.googleapis.com/v1
kind: PodMonitoring
metadata:
  name: app-metrics-monitoring
  namespace: default
spec:
  selector:
    matchLabels:
      app: my-app
  endpoints:
  - port: http-metrics
    path: /metrics
    interval: 15s
```

--------------------------------------------------------------------------------

## Google Cloud IAM and API Prerequisites

To deploy and evaluate Terraform alerting policies, the following Google Cloud
prerequisites are required:

### Required APIs

Enable the required Google Cloud APIs:

```bash
gcloud services enable \
    monitoring.googleapis.com \
    container.googleapis.com \
    cloudresourcemanager.googleapis.com
```

### Required IAM Roles

The service account or user deploying the Terraform configuration requires:

*   `roles/monitoring.alertPolicyEditor` (or `roles/monitoring.editor`)
*   `roles/monitoring.viewer` (for validating existing policies)
*   `roles/container.viewer` (for querying cluster metadata)

--------------------------------------------------------------------------------

## Verification Runbook

After deploying alerting policies, run the following verification steps:

1.  **Validate Terraform Syntax**:

    ```bash
    terraform init
    terraform validate
    ```

2.  **Validate PromQL Rules**:

    ```bash
    python3 scripts/validate_config.py --file alerts.tf
    ```

3.  **Inspect Cloud Monitoring Alert Policies**:

    ```bash
    gcloud alpha monitoring policies list \
        --filter="displayName ~ '^\[K8s\]'" \
        --format="table(name,displayName,enabled)"
    ```
