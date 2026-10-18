---
name: gke-ai-troubleshooting-tpu-mxla-hang
description: Diagnose GKE Cloud TPU multi-slice training hangs (Megascale `HANG_DETECTED` logs and hang monitored events) using the ML Diagnostics `Megascale XLA (MXLA) Hang Analyzer` (`gcloud alpha mldiagnostics monitored-events`) and 1-minute Cloud Monitoring multi-slice latency metrics (`kubernetes.io/container/multislice/*`). Distinguishes XLA compiler/HLO launch divergence and host data-input stalls from TPU chip, SparseCore, ICI, or network fabric faults. Use when multi-slice TPU training jobs freeze without progressing steps, emit `HANG_DETECTED`, or stall in collective operations. Don't use for gradual step-time throughput drops without hangs (use gke-ai-troubleshooting-tpu-performance-degradation) or pod preemption/eviction restarts (use gke-ai-troubleshooting-jobset-interruption).
metadata:
  version: 1.0.0
  category: AiAndMachineLearning
skill_id: platforms/gke-ai-troubleshooting-tpu-mxla-hang
title: Gke Ai Troubleshooting Tpu Mxla Hang
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-mxla-hang
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-ai-troubleshooting-tpu-mxla-hang", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-mxla-hang", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "GKE AI/TPU troubleshooting (niche)", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 5, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Troubleshoot GKE TPU multi-slice hangs with the MXLA Hang Analyzer

Diagnose Cloud TPU multi-slice training hangs on Google Kubernetes Engine (GKE)
by correlating Megascale `HANG_DETECTED` logs and **ML Diagnostics Workload
Monitoring** `Megascale XLA (MXLA) Hang Analyzer` reports with **1-minute
multi-slice latency metrics** (`kubernetes.io/container/multislice/*`) and GKE
node topology labels.

---

## Prerequisites

- **Tools**: Install the
  [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud` with
  `alpha` component for `gcloud alpha mldiagnostics`) and `kubectl`.
- **Cloud Billing & Project Configuration**: Verify an active billing account is
  linked (`gcloud billing projects describe {project_id}`), authenticate
  (`gcloud auth login`), set the target project (`gcloud config set project
  {project_id}`), and ensure `container.googleapis.com`,
  `logging.googleapis.com`, `monitoring.googleapis.com`, and
  `hypercomputecluster.googleapis.com` are enabled.
- **Supported workloads and versions**: Google Cloud ML Diagnostics only
  supports JAX on TPUs (see
  [ML Diagnostics platform](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/overview.md.txt)).
  Workload Monitoring is enabled by default, supports the `jobset` and `job` GKE
  job types, and is compatible with GKE versions `1.36.0-gke.4681000` and later
  ([Configure GKE for ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/gke.md.txt),
  which also covers the cluster setup needed for on-demand profiling in Step 4
  Path B). If a workload uses another framework (such as PyTorch) or another
  custom resource type, `gcloud alpha mldiagnostics` won't list ML runs or
  monitored events for it. The Megascale XLA hang analyzer and Megascale XLA
  metrics require LibTPU `0.40.0` or later (see "Get started" in
  [Workload monitoring with ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt)).
- **Required IAM Roles**:
  - Cluster Director Editor (`roles/hypercomputecluster.editor`), the role
    listed in the "IAM permissions" section of
    [ML Diagnostics platform](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/overview.md.txt),
    for the ML Diagnostics CLI and API calls in this skill (ML runs, monitored
    events, and on-demand profiler sessions)
  - Monitoring Viewer (`roles/monitoring.viewer`) for the PromQL queries in Step
    2
  - Logs Viewer (`roles/logging.viewer`) for the Cloud Logging query in Step 1
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
    (Sections: "Get started", "Megascale XLA (MXLA) Hang Analyzer", "Access
    Workload Monitoring information through the API", "System Metrics")
  - [Get started with the ML Diagnostics CLI](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt)
    (Sections: "List machine learning runs", "Monitored-events commands", "List
    profiler targets", "Capture on-demand profiler sessions")
  - [Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
    (Sections: "Before you contact support")
  - [Auto-repair nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
    (Sections: "Repair criteria", "Verify node auto-repair is enabled for a
    Standard node pool", "Enable auto-repair for an existing Standard node
    pool", "Node auto repair in TPU slice nodes")
  - [Deploy TPU workloads in GKE Standard](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
    (Sections: "Configure auto repair for TPU slice nodes")
  - [Dump HLO Computations (OpenXLA)](https://openxla.org/xla/hlo_dumps)

> **Read-only rule**: Run read-only diagnostic commands only. Never drain,
> delete, or re-create nodes, or run any other command that changes the cluster.
> Give the user any fix to apply themselves.

When you recommend a fix, link the doc section that describes it.

---

## MXLA Hang Analyzer routing overview

A Megascale hang occurs when a multi-slice worker has waited on a Megascale
communication operation for a set timeout period. The TPU logs then show a
Megascale `HANG_DETECTED` message. `HANG_DETECTED` is a catch-all signal that
the workload isn't progressing, and the cause can be in software or in hardware.
When a hang occurs, ML Diagnostics runs the `Megascale XLA (MXLA) Hang
Analyzer`, which reports the likely cause as a code. Consult the
[Megascale XLA (MXLA) Hang Analyzer](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt)
section for the definition and recommended action of each code, and route by
category:

- **Category A: compiler or HLO divergence (never cordon or replace nodes)**:
  - When the analyzer reports different HLO modules, inconsistent HLO
    compilation, or an inconsistent launch order across VMs (such as
    `FINGERPRINT_MISMATCH`), route to
    [Path A: Compiler or HLO divergence](references/path-a-compiler-hlo-divergence.md).
- **Category B: host program queueing or data input stall (never cordon or
  replace nodes)**:
  - When the analyzer reports that VMs aren't queuing programs to the TPU or
    that data input stalled (such as `DATA_INPUT_STALL`), route to
    [Path B: Host program queueing or data input stall](references/path-b-program-queueing-input-stall.md).
- **Category C: hardware or network faults on specific instances**:
  - When the analyzer attributes the hang to a TPU chip, SparseCore, ICI, or DCN
    networking issue, or to an unrecoverable error on specific instances, route
    to
    [Path C: Hardware or network faults](references/path-c-hardware-network-faults.md).
- **Category D: hang signal or event exists, but the analyzer is `NOT_DETECTED`
  or reports `UNKNOWN`**:
  - When `HANG_DETECTED` logs or a hang monitored event fired, but the analyzer
    report has `detectionState: "NOT_DETECTED"` or reports `UNKNOWN` ("The MXLA
    hang was detected but a potential cause is not determined"), route to
    [Path D: No DETECTED analyzer or UNKNOWN cause](references/path-d-no-detected-or-unknown.md).

---

## Diagnostic workflow

### Step 0: Collect context and set the investigation window `[Low Risk]`

Collect the target parameters. By default, query a 60-minute window `[T - 30m, T
+ 30m]` around `{issue_time}`:

- `{project_id}`: Google Cloud project ID
- `{location}`: Google Cloud region where the ML run and GKE cluster reside (for
  example, `us-central1`)
- `{cluster_name}`: GKE cluster name
- `{namespace}` / `{workload_name}`: Kubernetes namespace and JobSet/Pod prefix
- `{ml_run_id}`: ML Diagnostics run ID
- `{issue_time}`: Timestamp when the hang occurred (`T`, ISO-8601 UTC)
- `{start_time}`: `T - 30m`
- `{end_time}`: `T + 30m`

---

### Step 1: Query `HANG_DETECTED` logs and hang events `[Low Risk]`

1. **Check GKE container logs for `HANG_DETECTED` (read-only Cloud Logging
   LQL)**: Query `k8s_container` logs over `[{start_time}, {end_time}]` to
   confirm `HANG_DETECTED` and identify the first stalled pods:

```sql
resource.type="k8s_container"
resource.labels.project_id="{project_id}"
resource.labels.cluster_name="{cluster_name}"
"HANG_DETECTED"
timestamp >= "{start_time}" AND timestamp <= "{end_time}"
```

2. **List active or recent ML runs**: Follow the
   [List machine learning runs](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt)
   section of the ML Diagnostics CLI reference (`gcloud alpha mldiagnostics
   machine-learning-run list`) to identify `{ml_run_id}`.
3. **List and describe hang monitored events**: Follow the
   [Monitored-events commands](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt)
   section (`gcloud alpha mldiagnostics monitored-events list` and `gcloud alpha
   mldiagnostics monitored-events describe`) or
   [Access Workload Monitoring information through the API](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt)
   to inspect the `Megascale XLA (MXLA) Hang Analyzer` report (`detectionState`,
   `details`, and `recommendedActions`).
   - If `HANG_DETECTED` logs or a hang monitored event fired, run Step 2 to
     correlate with the 1-minute metrics; if no analyzer reports
     `detectionState: "DETECTED"` or the analyzer reports `UNKNOWN`, follow
     [Path D: No DETECTED analyzer or UNKNOWN cause](references/path-d-no-detected-or-unknown.md).
   - If no `HANG_DETECTED` log or hang monitored event exists and multi-slice
     latencies are normal in Step 2, **rule out** an MXLA hang by following
     [Path E: Healthy telemetry](references/path-e-healthy-telemetry.md).

---

### Step 2: Correlate with 1-minute multi-slice and TPU metrics `[Low Risk]`

Consult the
[System Metrics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt)
section of the Workload Monitoring guide for the 1-minute multi-slice network
(`kubernetes.io/container/multislice/network/*`), multi-slice accelerator
(`kubernetes.io/container/multislice/accelerator/*`), and node duty-cycle
(`kubernetes.io/node/accelerator/duty_cycle`) metrics, and run read-only PromQL
queries over `[{start_time}, {end_time}]`:

```promql
# 1. P95 multi-slice collective end-to-end latency by pod
histogram_quantile(
  0.95,
  sum by (pod_name, le) (
    rate(kubernetes_io:container_multislice_network_collective_end_to_end_latencies_bucket{
      monitored_resource="k8s_container",
      project_id="{project_id}",
      cluster_name="{cluster_name}"
    }[5m])
  )
)

# 2. P95 multi-slice DCN transfer latency by pod
histogram_quantile(
  0.95,
  sum by (pod_name, le) (
    rate(kubernetes_io:container_multislice_network_dcn_transfer_latencies_bucket{
      monitored_resource="k8s_container",
      project_id="{project_id}",
      cluster_name="{cluster_name}"
    }[5m])
  )
)

# 3. P95 host-to-device transfer latency by pod
histogram_quantile(
  0.95,
  sum by (pod_name, le) (
    rate(kubernetes_io:container_multislice_accelerator_host_to_device_transfer_latencies_bucket{
      monitored_resource="k8s_container",
      project_id="{project_id}",
      cluster_name="{cluster_name}"
    }[5m])
  )
)

# 4. Node TPU duty cycle (Workload Monitoring detects a hang as a prolonged
#    period of minimal to no TPU activity)
kubernetes_io:node_accelerator_duty_cycle{
  monitored_resource="k8s_node",
  project_id="{project_id}",
  cluster_name="{cluster_name}"
}
```

---

### Step 3: Map culprit Compute Engine instance IDs to GKE nodes `[Low Risk]`

When the `Megascale XLA (MXLA) Hang Analyzer` reports culprit numeric Compute
Engine instance IDs in `details` or `recommendedActions`, map those numeric IDs
to GKE `Node` names and physical topology blocks using this read-only `kubectl`
query inspecting `container.googleapis.com/instance_id`:

```bash
kubectl get nodes -l cloud.google.com/gke-tpu-accelerator \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\tinstance_id="}{.metadata.annotations.container\.googleapis\.com/instance_id}{"\tblock="}{.metadata.labels.cloud\.google\.com/gce-topology-block}{"\tsubblock="}{.metadata.labels.cloud\.google\.com/gce-topology-subblock}{"\thost="}{.metadata.labels.cloud\.google\.com/gce-topology-host}{"\n"}{end}'
```

---

### Step 4: Remediation by root-cause category

Load and follow only the reference file that matches the root-cause category
from Step 1:

- **Path A (compiler or HLO divergence — different HLO modules, inconsistent HLO
  compilation, or inconsistent launch order across VMs, such as
  `FINGERPRINT_MISMATCH`)**: Read
  [Path A: Compiler or HLO divergence](references/path-a-compiler-hlo-divergence.md).
- **Path B (host program queueing or data input stall — `PROGRAM_NOT_QUEUED` or
  `DATA_INPUT_STALL`)**: Read
  [Path B: Host program queueing or data input stall](references/path-b-program-queueing-input-stall.md).
- **Path C (hardware, SparseCore, ICI, DCN networking faults, or
  `UNRECOVERABLE_ERROR` on specific instances)**: Read
  [Path C: Hardware or network faults](references/path-c-hardware-network-faults.md).
- **Path D (`HANG_DETECTED` logs or hang event fired, but the analyzer is
  `NOT_DETECTED` or reports `UNKNOWN`)**: Read
  [Path D: No DETECTED analyzer or UNKNOWN cause](references/path-d-no-detected-or-unknown.md).
- **Path E (no `HANG_DETECTED` logs or hang events, and steady telemetry)**:
  Read [Path E: Healthy telemetry](references/path-e-healthy-telemetry.md).

---

## Guardrails

1. **Never change GKE-managed instance groups or VMs through Compute Engine**:
   Don't run `gcloud compute instance-groups managed` commands, such as
   `delete`, on a node pool's managed instance group. Handle nodes through GKE,
   as described in
   [Path C: Hardware or network faults](references/path-c-hardware-network-faults.md).
2. **Never cordon or replace nodes for compiler divergence or input stalls**: If
   the analyzer reports a compiler or HLO divergence, a program queueing issue,
   or a data input stall, don't cordon or replace TPU nodes. Follow
   [Path A: Compiler or HLO divergence](references/path-a-compiler-hlo-divergence.md)
   or
   [Path B: Host program queueing or data input stall](references/path-b-program-queueing-input-stall.md)
   instead.
