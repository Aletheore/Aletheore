---
name: gke-workload-scaling-troubleshooting
metadata:
  version: 1.0.0
  category: Containers
description: Diagnoses GKE HorizontalPodAutoscaler (HPA) failures — metrics showing as <unknown>, FailedGetResourceMetric / FailedGetScale / FailedComputeMetricsReplicas events, missing Pod resource requests, custom/external metrics-pipeline breakage (FailedGetExternalMetric / FailedGetCustomMetric, unavailable metrics adapter, control-plane firewall blocking the adapter), HPA that won't scale up or down (tolerance / stabilization window / unavailable rate metrics), scale-to/from-zero problems, and slow HPA reaction on large clusters. Use when an HPA isn't scaling a workload as expected or reports metric errors. Don't use for configuring or authoring new HPA/VPA objects or scaling best practices (see the gke-workload-scaling skill), or for Cluster Autoscaler / node-pool sizing.
skill_id: platforms/gke-workload-scaling-troubleshooting
title: Gke Workload Scaling Troubleshooting
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-workload-scaling-troubleshooting
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-workload-scaling-troubleshooting", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-workload-scaling-troubleshooting", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["slash"], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Workload Scaling Troubleshooting Skill

Use this skill to systematically diagnose and resolve **HorizontalPodAutoscaler
(HPA) failures** on GKE — metrics reported as `<unknown>`, `FailedGet*` events,
missing resource requests, custom/external metrics-pipeline breakage, HPA that
refuses to scale up or down, scale-to/from-zero issues, and slow HPA reaction on
large clusters. This skill operates non-interactively and enforces a read-only
diagnostics boundary before proposing manifest or configuration corrections.

> For configuring HPA/VPA objects and scaling **best practices**, use the
> `gke-workload-scaling` skill instead. This skill focuses on **failure
> diagnosis**.

## 🔍 Diagnosis & Resolution Workflow

### Step 0: Non-Interactive Context Discovery & Dry-Run Fallback

1.  **Parameter Extraction**: Extract required context (`project_id`,
    `cluster_name`, `cluster_location`, `hpa_name`, `workload_name`,
    `workload_namespace`) non-interactively from the user prompt, active
    `SETTINGS.md`, or environment defaults:

    -   Default `workload_namespace` to `default` if omitted.
    -   Infer missing cluster parameters from the active environment (`kubectl
        config current-context` or `gcloud config get-value project`).

2.  **Cluster Credentials & Fallback Mode**:

    -   Attempt credential fetch: `gcloud container clusters get-credentials
        {cluster_name} --location {cluster_location} --project {project_id}`.
    -   **Fallback / Dry-Run Mode**: If the cluster is unreachable,
        non-existent, or live command execution fails (such as in sandboxed
        evaluations, dry-run mode, or offline analysis):
        -   Limit retry attempts to avoid resource exhaustion and context
            overflow.
        -   Immediately present the exact `kubectl` / `gcloud` diagnostic
            commands for the human operator to run.
        -   Synthesize the root-cause analysis and output the proposed GitOps
            correction based on the reported symptoms.

--------------------------------------------------------------------------------

### Step 1: Inspect the HPA and Classify the Symptom

Start every investigation with `kubectl describe hpa`, then route to the matching
branch. The three key sections are **`Metrics`** (an `<unknown>` value means the
HPA hasn't fetched the metric or the pipeline is broken), **`Conditions`**
(`AbleToScale`, `ScalingActive`, `ScalingLimited` — a `False` status marks a
failure), and **`Events`** (specific reasons such as `FailedGetScale` or
`FailedGetResourceMetric`).

**Diagnostic Commands:**

```bash
kubectl describe hpa {hpa_name} -n {workload_namespace}
kubectl get hpa {hpa_name} -n {workload_namespace} -o yaml
```

For historical events, query Cloud Logging (the HPA events survive after the live
`Events` list rolls over):

```
resource.type="k8s_cluster"
resource.labels.cluster_name="{cluster_name}"
resource.labels.location="{cluster_location}"
logName="projects/{project_id}/logs/events"
jsonPayload.involvedObject.kind="HorizontalPodAutoscaler"
```

Route by signal:

-   **`FailedGetScale`, `FailedComputeMetricsReplicas`, `Error 400 ... label
    is not allowed`, or fluctuating replicas from competing HPAs** → **Branch A**
    (Configuration Errors).
-   **`FailedGetResourceMetric`, `unable to fetch pod metrics`, or `multiple
    services selecting the same target`** → **Branch B** (Workload & Service
    Errors).
-   **`<unknown>` custom/external metric, `FailedGetExternalMetric` /
    `FailedGetCustomMetric`, or `no known available metric versions found`** →
    **Branch C** (Metrics API & Data Availability).
-   **Conditions all `True` / no errors but the workload won't scale up or down**
    → **Branch D** (Healthy but Unexpected Scaling).
-   **Workload configured with `minReplicas: 0` won't scale to or from zero** →
    **Branch E** (Scale To / From Zero).
-   **Correct HPA but slow reaction on a cluster with many HPA objects** →
    **Branch F** (Slow Recalculation on Large Clusters).

--------------------------------------------------------------------------------

### Step 2: Resolution — Route to the Matching Branch

Based on the signal you classified in Step 1, jump to **one** of the
mutually-exclusive branches below (A–F). These are alternatives — you do **not**
run them in sequence. After applying the branch's fix, go to Step 3 to present it
as a reviewable GitOps change.

#### Branch A: HorizontalPodAutoscaler Configuration Errors

-   **`FailedGetScale` — `unable to get the target's current scale: ... "TARGET"
    not found`**: the `scaleTargetRef` doesn't resolve to an existing scalable
    workload.

    -   Verify the `scaleTargetRef` `name`, `kind`, and `apiVersion` exactly
        match the target workload's metadata.
    -   Confirm the target workload exists **in the same namespace** as the HPA
        (a missing `-n` puts objects in `default`, causing a mismatch).
    -   The target must be a scalable kind (Deployment, StatefulSet,
        ReplicaSet) — you **cannot** autoscale a DaemonSet.

-   **`FailedComputeMetricsReplicas` — `invalid metrics (1 invalid out of 1)`**:
    the metric `type` and `target` don't match.

    -   If `type: Utilization`, the target must be `averageUtilization`.
    -   If `type: AverageValue`, the target must be `averageValue`.

-   **`unable to fetch metrics from external metrics API: googleapi: Error 400:
    Metric label: 'LABEL' is not allowed`**: an invalid key in
    `metric.selector.matchLabels`.

    -   Remove or correct the disallowed label; find valid filterable labels in
        the Cloud Monitoring metric documentation.

-   **Replica count fluctuates / contradictory `SuccessfulRescale` events from
    different HPAs**: more than one HPA targets the same workload via
    `spec.scaleTargetRef`, and they compete. There is no dedicated condition for
    this — confirm with `kubectl get hpa -n {workload_namespace} -o yaml` and
    look for duplicate `scaleTargetRef` values.

    -   Consolidate all metrics into **one** HPA object (it takes the highest of
        its `spec.metrics`) and delete the duplicates.

--------------------------------------------------------------------------------

#### Branch B: Workload & Service Errors

-   **`ScalingActive: False`, reason `FailedGetResourceMetric`, message `unable
    to compute the replica count`** (or a persistent `unable to fetch pod
    metrics`): the HPA computes utilization as a percentage of the container
    **resource request**, but at least one container in the Pod is missing a
    `resources.requests` entry for the scaled resource (`cpu` or `memory`).

    -   Add `resources.requests` for the scaled resource to **every** container
        in the Pod spec (including sidecars). A brief `unable to fetch pod
        metrics` right after the metrics server starts is normal and self-heals.

-   **`multiple services selecting the same target of HPA_NAME: SERVICE`**:
    traffic-based autoscaling requires a **one-to-one** Service↔workload
    relationship, but more than one Service's selector matches the workload's
    Pods.

    -   Make the intended Service's selector unique (add a distinct label to the
        workload and to that one Service), or tighten the other Services'
        selectors so they no longer match the workload's Pods.

--------------------------------------------------------------------------------

#### Branch C: Metrics API & Data Availability (Custom / External Metrics)

The custom/external pipeline is: HPA controller → Kubernetes metrics API server
→ metrics adapter (for example `custom-metrics-stackdriver-adapter`) → metric
source (Cloud Monitoring / Prometheus). Symptoms are `<unknown>` metric values or
`FailedGetExternalMetric` / `FailedGetCustomMetric` events.

1.  **Is the adapter registered and available?**

    ```bash
    kubectl get apiservice | grep -E 'NAME|metrics.k8s.io'
    ```

    Expect `v1beta1.custom.metrics.k8s.io` and/or
    `v1beta1.external.metrics.k8s.io` with `AVAILABLE: True`. If `False`/missing,
    the adapter is crashed or misconfigured — inspect its Pod logs in the
    `custom-metrics` or `kube-system` namespace for permission, connectivity, or
    "metric not found" errors.

2.  **Query the metrics API directly** (bypasses the HPA to test the whole
    pipeline; `jq` optional):

    ```bash
    kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/{workload_namespace}/{metric_name}" | jq .
    kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/{workload_namespace}/pods/*/{metric_name}" | jq .
    ```

3.  **Interpret the result:**

    -   **Valid JSON with a value** → the pipeline works; the fault is in the HPA
        manifest (metric-name typo or wrong `matchLabels`).
    -   **`Error from server (Service Unavailable)`** → network isolation is
        blocking the control plane from reaching the adapter. Add the adapter's
        `targetPort` to the **control-plane firewall rule** (in addition to the
        existing `tcp:443` and `tcp:10250`). Identify the rule with
        `gcloud compute firewall-rules list --filter="name~gke-{cluster_name}-[0-9a-z]*-master"`,
        and also confirm no NetworkPolicy blocks ingress to the adapter Pods.
    -   **Empty list `[]`** → the adapter runs but can't retrieve the metric.
        Inspect the adapter Pod logs, and confirm in Metrics Explorer that the
        metric actually exists in the source with the expected name and labels.

-   **`unable to fetch metrics from custom metrics API: no known available metric
    versions found`**: a communication breakdown (control plane briefly
    unavailable during an upgrade/repair, or the adapter Pods are unhealthy or
    not registered) — **not** a problem at the metric source. Check control-plane
    health/notifications, confirm the adapter Pods are `Running` with no restarts
    (`kubectl get pods -n custom-metrics,kube-system -o wide`), and re-verify the
    APIServices are `AVAILABLE: True`. Often transient.

-   **`googleapi: Error 400: The supplied filter ... will not return any time
    series`**: the query is valid but no data matched (different from a value of
    `0`) — the application wasn't writing the metric during the window. Verify the
    metric name/labels match what the app emits, confirm the app had permission
    and was active, and check the app logs for metric-emission errors.

--------------------------------------------------------------------------------

#### Branch D: Healthy but Unexpected Scaling Behavior

The HPA's conditions are `True` and it shows no errors, but scaling doesn't
happen as expected.

-   **Won't scale up** — check, in order:
    -   **Replica limits**: `currentReplicas` is already at `minReplicas` /
        `maxReplicas` (see the `ScalingLimited` condition); adjust the bounds.
    -   **Tolerance window**: Kubernetes ignores changes while the
        current/target ratio stays within `0.9`–`1.1` (default **10%
        tolerance**). Example: target 85% CPU, current 93% → ratio ≈ 1.094 < 1.1,
        so no scale-up. Wait for the metric to move outside the band, or
        configure a different tolerance.
    -   **Unready Pods**: `Pending`/not-`Ready` Pods are excluded from the
        calculation — resolve the underlying scheduling/probe issue.
    -   **Sync delay**: a 15–30s delay between threshold crossing and action is
        normal.

-   **Won't scale down** — check, in order:
    -   **Multiple metrics**: the HPA uses the metric demanding the **most**
        replicas, so it won't scale down unless **all** metrics agree.
    -   **Unavailable metric halts scale-down**: if any metric goes `<unknown>`
        the HPA conservatively refuses to scale down. Common with **rate-based**
        custom metrics that stop reporting at zero traffic. Prefer **gauge**
        metrics (for example `num_undelivered_messages`) or make the source
        publish `0` during inactivity rather than sending no data.
    -   **Scale-down stabilization window**: the default
        `behavior.scaleDown.stabilizationWindowSeconds` is **300s (5 min)**.
        Lower it if scale-down must be faster.

--------------------------------------------------------------------------------

#### Branch E: Scale To / From Zero (GKE 1.37+)

Scaling a workload to and from zero replicas with HPA (`minReplicas: 0`) is
supported on **GKE 1.37 or later**.

-   **Won't scale to zero**:
    -   `minReplicas: 0` must be set.
    -   The HPA **cannot** scale to zero using only `Resource` (CPU/memory)
        metrics — configure at least one `External` or `Object` metric.
    -   With multiple metrics, all must evaluate to zero.
    -   GKE waits the 5-minute scale-down stabilization window at zero demand
        before going from 1→0.
    -   Any metric showing `<unknown>` pauses scale-down (see Branch D).

-   **Won't scale up from zero**: run `kubectl describe hpa {hpa_name}` and check:
    -   **Metrics**: the value must be `> 0` and not `<unknown>`; if missing,
        confirm the external source (for example Pub/Sub) is publishing and Cloud
        Monitoring is receiving.
    -   **Conditions**: a healthy idle state shows `AbleToScale: True`,
        `ScalingActive: True`, `ScaledToZero: True`. If `ScalingActive: False`
        with reason `ScalingDisabledExternalScaleToZero` or
        `ScalingDisabledReplicaCountZero`, the workload was **manually** scaled to
        zero (for example `kubectl scale`), which pauses autoscaling. Resume it by
        scaling the Deployment back to `--replicas=1`.
    -   Cold-start latency from node provisioning can add delay; Capacity Buffers
        keep standby capacity ready.

--------------------------------------------------------------------------------

#### Branch F: Slow HPA Recalculation on Large Clusters

If HPAs are correct but react slowly, the cluster may exceed the HPA object count
the standard controller keeps within a 15-second recalculation period.

-   Standard controller: within 15s for up to **300 HPA objects** (GKE 1.22+).
-   **Performance HPA profile**: within 15s for up to **1,000 HPA objects** (GKE
    1.31+) or **5,000 HPA objects** (GKE 1.33+, where it is enabled by default on
    eligible clusters).

Enable it on an eligible cluster (this is a cluster mutation — present it for the
operator to run, don't execute it):

```bash
gcloud container clusters update {cluster_name} \
    --location {cluster_location} --project {project_id} \
    --hpa-profile=performance
```

Scaling on many metrics per HPA and slow (>~50 ms) custom-metric adapters also
lengthen the recalculation period. For visibility into scaling decisions, enable
HPA event logging and review the structured HPA decision logs.

--------------------------------------------------------------------------------

### Step 3: Propose the GitOps Correction

Enforce the read-only diagnostics boundary: **do not** apply live mutations. This
includes cluster/manifest changes (`kubectl edit`, `kubectl patch`, `kubectl
apply`, `kubectl scale`, `kubectl delete`) **and** Google Cloud / gcloud changes
(cluster updates such as `--hpa-profile`, and `gcloud compute firewall-rules`
updates). Instead, present the corrected HorizontalPodAutoscaler, PodSpec
(`resources.requests`), Service selector, workload autoscaling
configuration, or the `gcloud` command as a reviewable patch/command to be
applied through the user's GitOps pipeline (for example Config Sync, Argo CD, or
Flux) or by an authorized operator.

## References

-   [Troubleshoot horizontal Pod autoscaling in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/horizontal-pod-autoscaling.md.txt)
-   [Horizontal Pod autoscaling (concepts, Performance HPA profile & limits)](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/horizontalpodautoscaler.md.txt)
-   [Configure horizontal Pod autoscaling (hpa-profile)](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/horizontal-pod-autoscaling.md.txt)
-   [View horizontal Pod autoscaling events](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/view-horizontalpodautoscaling-events.md.txt)
-   [Scale to and from zero using HPA](https://docs.cloud.google.com/kubernetes-engine/docs/tutorials/scale-to-from-zero-hpa.md.txt)
-   [Kubernetes HorizontalPodAutoscaler (tolerance & scaling policies)](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
