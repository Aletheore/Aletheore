---
name: gke-ai-troubleshooting-tpu-performance-degradation
description: Diagnose GKE Cloud TPU training throughput drops and step-time regressions (15%+ TPU duty-cycle drop) using ML Diagnostics Workload Monitoring (`gcloud alpha mldiagnostics monitored-events` / `hypercomputecluster.googleapis.com/v1alpha`) and 1-minute Cloud Monitoring system metrics (`kubernetes.io/node/accelerator/*`). Distinguishes hardware and network fabric throttling from workload resource bottlenecks (HBM capacity, host memory, or host CPU saturation). Use when TPU training throughput or duty cycle drops without crashing pods, when `PERFORMANCE_DEGRADATION` monitored events fire, or when triaging slow multi-slice training steps. Don't use for complete multi-slice XLA execution stalls with `HANG_DETECTED` logs (use gke-ai-troubleshooting-tpu-mxla-hang) or pod eviction/interruption restarts (use gke-ai-troubleshooting-jobset-interruption).
metadata:
  version: 1.0.0
  category: AiAndMachineLearning
skill_id: platforms/gke-ai-troubleshooting-tpu-performance-degradation
title: Gke Ai Troubleshooting Tpu Performance Degradation
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-performance-degradation
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-ai-troubleshooting-tpu-performance-degradation", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-performance-degradation", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "GKE AI/TPU troubleshooting (niche)", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 4, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Troubleshoot GKE TPU performance degradation with ML Diagnostics Workload Monitoring

Diagnose and mitigate Cloud TPU training throughput drops and step-time
regressions (`15%+` drop in TPU duty cycle) on Google Kubernetes Engine (GKE) by
correlating **ML Diagnostics Workload Monitoring** `MonitoredEvent` analyzer
reports with **1-minute Cloud Monitoring system metrics** and GKE node topology
labels.

---

## Prerequisites

- **Tools**: Install the
  [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud` with
  `alpha` component for `gcloud alpha mldiagnostics`) and `kubectl`.
- **Cloud Billing & Project Configuration**: Verify an active billing account is
  linked (`gcloud billing projects describe {project_id}`), authenticate
  (`gcloud auth login`), set the target project (`gcloud config set project
  {project_id}`), and ensure `container.googleapis.com`,
  `monitoring.googleapis.com`, and `hypercomputecluster.googleapis.com` are
  enabled.
- **Supported workloads and GKE versions**: Google Cloud ML Diagnostics only
  supports JAX on TPUs (see
  [ML Diagnostics platform](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/overview.md.txt)).
  Workload Monitoring is enabled by default, supports the `jobset` and `job` GKE
  job types, and is compatible with GKE versions `1.36.0-gke.4681000` and later,
  as stated in
  [Configure GKE for ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/gke.md.txt).
  If a workload uses another framework (such as PyTorch) or another custom
  resource type, `gcloud alpha mldiagnostics` won't list ML runs or monitored
  events for it. On-demand profiling (Step 4 Path A) additionally requires the
  cluster setup described in that document.
- **Required IAM Roles**:
  - Cluster Director Editor (`roles/hypercomputecluster.editor`), the role
    listed in the "IAM permissions" section of
    [ML Diagnostics platform](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/overview.md.txt),
    for the ML Diagnostics CLI and API calls in this skill (ML runs, monitored
    events, and on-demand profiler sessions)
  - Monitoring Viewer (`roles/monitoring.viewer`) for the PromQL queries in Step
    2
  - Kubernetes Engine Viewer (`roles/container.viewer`) for the `kubectl get
    nodes` query in Step 3
  - For remediation (`[High Risk]` steps): Kubernetes Engine Cluster Admin
    (`roles/container.clusterAdmin`)
- **Reference Documentation**:
  - [ML Diagnostics platform](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/overview.md.txt)
    (Sections: "IAM permissions")
  - [Configure GKE for ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/gke.md.txt)
    (Sections: "Set up with gcloud CLI, Google Cloud console, or Terraform",
    "Manual installation", "Connection-operator")
  - [Workload monitoring with ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt)
    (Sections: "Workload Monitoring and analyzers", "HBM Capacity Analyzer",
    "Host Memory Utilization Analyzer", "CPU Utilization Analyzer", "Access
    Workload Monitoring information through the API", "List all monitored
    events", "System Metrics")
  - [Get started with the ML Diagnostics CLI](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt)
    (Sections: "List machine learning runs", "Monitored-events commands", "List
    profiler targets", "Capture on-demand profiler sessions")
  - [Scale container resource requests and limits](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/vertical-pod-autoscaling.md.txt)
    (Sections: "Identify workloads without resource requests or limits")
  - [Auto-repair nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
    (Sections: "Repair criteria", "Verify node auto-repair is enabled for a
    Standard node pool", "Enable auto-repair for an existing Standard node
    pool", "Node auto repair in TPU slice nodes")
  - [Deploy TPU workloads in GKE Standard](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
    (Sections: "Configure auto repair for TPU slice nodes")
  - [Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
    (Sections: "Before you contact support")

> **Read-only rule**: Run read-only diagnostic commands only. Never drain,
> delete, or re-create nodes, or run any other command that changes the cluster.
> Give the user any fix to apply themselves.

When you recommend a fix, link the doc section that describes it.

---

## Analyzer routing overview

By default, Workload Monitoring treats a 15% drop in TPU duty cycle as a
performance degradation, raises a `MonitoredEvent` (`type:
PERFORMANCE_DEGRADATION`), and runs its analyzers. Consult the
[Workload Monitoring and analyzers](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt)
section in the official Cloud TPU documentation for the complete analyzer
definitions, detection criteria, and subsections, and route remediation by
category:

- **Category A: workload resource bottlenecks (never cordon or replace nodes)**:
  - When the firing analyzer (`detectionState: "DETECTED"`) indicates a workload
    memory or host CPU capacity bottleneck (see the "HBM Capacity Analyzer",
    "Host Memory Utilization Analyzer", and "CPU Utilization Analyzer" sections
    of
    [Workload monitoring with ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt)),
    route to
    [Path A: Workload resource bottlenecks](references/path-a-workload-bottlenecks.md)
    (workload optimization and profiling).
- **Category B: infrastructure and network fabric throttling (culprit nodes)**:
  - When the firing analyzer (`detectionState: "DETECTED"`) indicates an
    interconnect, thermal/power throttling, memory bandwidth, or Top-of-Rack
    (ToR) network fault on specific instances (see
    [Workload Monitoring and analyzers](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt)),
    route to
    [Path B: Infrastructure or network fabric throttling](references/path-b-infrastructure-throttling.md)
    (map the culprit Compute Engine instance IDs to GKE nodes, handle node
    repair through GKE, and escalate to Google Cloud Support).
- **Category C: `PERFORMANCE_DEGRADATION` event fired, but no analyzer reports
  `detectionState: "DETECTED"`**:
  - When a `PERFORMANCE_DEGRADATION` event exists (as in the sample output in
    [List all monitored events](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt),
    where `analyzerReports` entries have `detectionState: "NOT_DETECTED"`),
    route to
    [Path C: Event fired with no DETECTED analyzer](references/path-c-no-detected-analyzer.md).

---

## Diagnostic workflow

### Step 0: Collect context and set the investigation window `[Low Risk]`

Collect the target parameters. By default, query a 60-minute window `[T - 30m, T
+ 30m]` around `{issue_time}`:

- `{project_id}`: Google Cloud project ID
- `{location}`: Google Cloud region where the ML run and GKE cluster reside (for
  example, `us-central1`)
- `{cluster_name}`: GKE cluster name
- `{workload_name}` / `{ml_run_id}`: JobSet / workload name or ML Diagnostics
  run ID
- `{issue_time}`: Timestamp when throughput degradation was observed (`T`,
  ISO-8601 UTC)
- `{start_time}`: `T - 30m`
- `{end_time}`: `T + 30m`

---

### Step 1: Query ML runs and `monitoredEvents` `[Low Risk]`

1. **List active or recent ML runs**: Give the user `gcloud alpha mldiagnostics
   machine-learning-run list` with a link to
   [List machine learning runs](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt)
   in the ML Diagnostics CLI reference, and locate `{ml_run_id}` matching
   `{workload_name}`.
2. **List performance degradation events**: Give the user `gcloud alpha
   mldiagnostics monitored-events list` with a link to
   [Monitored-events commands](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt),
   or the `hypercomputecluster.googleapis.com/v1alpha` API with a link to
   [Access Workload Monitoring information through the API](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt),
   to check for `PERFORMANCE_DEGRADATION` events during `[{start_time},
   {end_time}]`.
3. **Describe the `MonitoredEvent`**: Give the user `gcloud alpha mldiagnostics
   monitored-events describe` with a link to the same
   [Monitored-events commands](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt)
   section, and inspect the `analyzerReports` array (`analyzer`,
   `detectionState`, `details`, and `recommendedActions`).
   - If a `PERFORMANCE_DEGRADATION` event fired, run Step 2 to corroborate with
     the 1-minute system metrics; if none of its `analyzerReports` entries has
     `detectionState: "DETECTED"`, follow
     [Path C: Event fired with no DETECTED analyzer](references/path-c-no-detected-analyzer.md).
   - If no `PERFORMANCE_DEGRADATION` event exists and duty cycle is steady in
     Step 2, **rule out** TPU performance degradation by following
     [Path D: Healthy telemetry](references/path-d-healthy-telemetry.md).

---

### Step 2: Correlate with 1-minute Cloud Monitoring system metrics `[Low Risk]`

Consult the
[System Metrics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt)
section in the Workload Monitoring documentation for the 1-minute Cloud
Monitoring metrics exported for TPU and host devices, and run read-only PromQL
queries over `[{start_time}, {end_time}]` to corroborate the analyzer report:

```promql
# 1. Node TPU duty cycle (look for the drop on the affected nodes)
kubernetes_io:node_accelerator_duty_cycle{
  monitored_resource="k8s_node",
  project_id="{project_id}",
  cluster_name="{cluster_name}"
}

# 2. HBM utilization ratio by node (around 0.90 is approaching the limit)
sum by (node_name) (
  kubernetes_io:node_accelerator_memory_used{
    monitored_resource="k8s_node",
    project_id="{project_id}",
    cluster_name="{cluster_name}"
  }
)
/
sum by (node_name) (
  kubernetes_io:node_accelerator_memory_total{
    monitored_resource="k8s_node",
    project_id="{project_id}",
    cluster_name="{cluster_name}"
  }
)

# 3. Host memory and CPU allocatable utilization
kubernetes_io:node_memory_allocatable_utilization{
  monitored_resource="k8s_node",
  project_id="{project_id}",
  cluster_name="{cluster_name}"
}

kubernetes_io:node_cpu_allocatable_utilization{
  monitored_resource="k8s_node",
  project_id="{project_id}",
  cluster_name="{cluster_name}"
}
```

---

### Step 3: Map culprit instance IDs to GKE nodes and topology `[Low Risk]`

When an infrastructure analyzer reports culprit numeric Compute Engine instance
IDs in `details` or `recommendedActions`, map those numeric instance IDs to GKE
`Node` names and physical topology blocks using this read-only `kubectl` query
inspecting `container.googleapis.com/instance_id`:

```bash
kubectl get nodes -l cloud.google.com/gke-tpu-accelerator \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\tinstance_id="}{.metadata.annotations.container\.googleapis\.com/instance_id}{"\tblock="}{.metadata.labels.cloud\.google\.com/gce-topology-block}{"\tsubblock="}{.metadata.labels.cloud\.google\.com/gce-topology-subblock}{"\thost="}{.metadata.labels.cloud\.google\.com/gce-topology-host}{"\n"}{end}'
```

---

### Step 4: Remediation by analyzer category

Load and follow only the reference file that matches the analyzer category from
Step 1:

- **Path A (workload resource bottlenecks — HBM, host memory, or host CPU
  utilization)**: Read
  [Path A: Workload resource bottlenecks](references/path-a-workload-bottlenecks.md).
- **Path B (infrastructure, thermal, ICI, memory bandwidth, or network fabric
  throttling)**: Read
  [Path B: Infrastructure or network fabric throttling](references/path-b-infrastructure-throttling.md).
- **Path C (`PERFORMANCE_DEGRADATION` event fired with all analyzers
  `NOT_DETECTED`)**: Read
  [Path C: Event fired with no DETECTED analyzer](references/path-c-no-detected-analyzer.md).
- **Path D (no `PERFORMANCE_DEGRADATION` events and steady telemetry)**: Read
  [Path D: Healthy telemetry](references/path-d-healthy-telemetry.md).

---

## Guardrails

1. **Never change GKE-managed instance groups or VMs through Compute Engine**:
   Don't run `gcloud compute instance-groups managed` commands, such as
   `delete`, on a node pool's managed instance group. Handle nodes through GKE,
   as described in
   [Path B: Infrastructure or network fabric throttling](references/path-b-infrastructure-throttling.md).
2. **Never cordon nodes for workload resource saturation**: If only the HBM
   capacity, host memory, or host CPU utilization analyzers detected an issue,
   don't cordon or replace nodes.
