---
name: gke-ai-troubleshooting-tpu-dynamic-slices-monitoring
description: Monitors, troubleshoots, and manages GKE TPU Dynamic Slices custom resources. Use when checking TPU slice lifecycle states, troubleshooting slice provisioning failures, validating single-slice or multi-slice (JobSet) workload manifests, or safely patching stuck finalizers and disabling the slice controller. Don't use for generic GKE cluster node pool creation or standard non-TPU workload management (use gke-basics or gke-cluster-creation instead).
metadata:
  version: 1.0.0
  category: Containers
skill_id: platforms/gke-ai-troubleshooting-tpu-dynamic-slices-monitoring
title: Gke Ai Troubleshooting Tpu Dynamic Slices Monitoring
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-dynamic-slices-monitoring
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-ai-troubleshooting-tpu-dynamic-slices-monitoring", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-dynamic-slices-monitoring", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "GKE AI/TPU troubleshooting (niche)", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 2, "has_scripts": true}, "curated_on": "2026-10-18", "requires": ["scripts"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE TPU Dynamic Slices Monitoring & Management

Monitors the status of TPU Slice custom resources, troubleshoots provisioning
failures, validates workload manifests on dynamic slices, and performs cleanups.

## Prerequisites

-   Cloud Logging enabled for the project.
-   `kubectl` and `gcloud` CLIs configured to access the GKE cluster.

## Diagnostic Workflow

### Step 0: Context Acquisition & Time Window Definition

Gather project, cluster, and slice context using cluster tools or the following
parameters:

-   **Project ID**: `{project_id}` (e.g., `my-gcp-project`)
-   **Cluster Name**: `{cluster_name}` (e.g., `tpu-cluster`)
-   **Region/Zone**: `{location}` (e.g., `us-central1-a`)
-   **Slice Name**: `{slice_name}` (e.g., `test-slice`)
-   **Issue Time**: `{timestamp}` (Optional; default to the last 30 minutes
    window `[T - 30m]` to `[T + 30m]`)

--------------------------------------------------------------------------------

### Step 1: Describe the Slice Custom Resource [Low Risk]

When asked to inspect, troubleshoot, or check a slice status, immediately execute `kubectl describe slice {slice_name}` using available cluster tools to perform the inspection. Parse the resulting `Status.Conditions` output against the condition table below to diagnose the exact state and provide concrete recommendations.

-   **Command**:

    ```bash
    kubectl describe slice {slice_name}
    ```

#### State & Reason Analysis

Analyze the `Status.Conditions` (especially `Type: Ready` and its `Reason` and
`Status`):

| Lifecycle State / Reason | Meaning | Recommended Action |
| :--- | :--- | :--- |
| **`SliceNotCreated`** | GKE Slice Controller is initializing the slice and performing resource checks. | Wait a few minutes and re-check slice status. |
| **`SliceCreationFailed`** | Prerequisites validation failed (e.g., selected nodes don't exist, nodes are already used by another slice, or the topology doesn't match the number of partitions). | Verify selected nodes exist, are unallocated, and topology matches partition count. |
| **`ACTIVATING`** | GKE is actively forming and provisioning the TPU slice. | Monitor node provisioning. |
| **`ACTIVE`** | The TPU slice is successfully formed and ready to host workloads. | Proceed to deploy or check workloads. |
| **`ACTIVE_DEGRADED`** | The slice is usable, but one or more sub-blocks are degraded. | Monitor workload logs for interconnect or device errors. Check faulty node VMs. |
| **`FAILED`** | GKE failed to form the TPU slice (e.g., selected nodes are not part of the same reservation block). | Ensure all selected nodes belong to the same reservation block. |
| **`DEACTIVATING`** | The slice is dismantling (triggered by user deletion or a critical systemic failure). | Wait for dismantling to finish, or patch finalizers if stuck. |
| **`INCOMPLETE`** | The terminal phase before the Slice CR is deleted from the cluster. | No action required; the resource will be removed shortly. |

#### Provisioning Failure Troubleshooting Checklist

When investigating slice creation or provisioning failures (`SliceCreationFailed` or `FAILED`), perform the following verification steps:

1. **Node Existence & Allocation Check**: Verify that the selected TPU nodes exist in the cluster and are not already allocated to another slice (`kubectl get nodes -l cloud.google.com/gke-tpu-slice`, `kubectl get slice -A`).
2. **Topology Alignment**: Confirm that the partition count matches the requested topology dimensions (e.g. topology `2x2` requires 4 nodes).
3. **Reservation Block Alignment Check**: Confirm that all selected TPU nodes belong to the same reservation and reservation block.

--------------------------------------------------------------------------------

### Step 2: Verify Workload Specification [Low Risk]

Ensure workload manifests are configured correctly to target the dynamic slice.

#### 1. Single-Slice Workload Requirements

Check that the Pod template contains the following annotations and selectors:

-   **Annotations**:
    -   `cloud.google.com/gke-tpu-slice-topology: "{topology}"` (e.g.,
        `"4x4x4"`)
-   **NodeSelector**:
    -   `cloud.google.com/gke-tpu-topology: "{topology}"` (e.g., `"4x4x4"`)
    -   `cloud.google.com/gke-tpu-accelerator: "{accelerator_type}"` (e.g.,
        `"tpu7x"`)
    -   `cloud.google.com/gke-tpu-slice: "{slice_name}"` (e.g., `"test-slice"`)

#### 2. Multi-Slice (JobSet) Workload Requirements

If deploying a multi-slice JobSet, verify:

-   **JobSet Annotation**:
    -   `alpha.jobset.sigs.k8s.io/exclusive-topology:
        cloud.google.com/gke-tpu-slice`
-   **Pod Template Annotations**:
    -   `cloud.google.com/gke-tpu-slice-topology: "{topology}"`
-   **Pod Template NodeSelector**:
    -   `cloud.google.com/gke-tpu-topology: "{topology}"`
    -   `cloud.google.com/gke-tpu-accelerator: "{accelerator_type}"`
    -   *Note: Do NOT manually specify `cloud.google.com/gke-tpu-slice` in the
        nodeSelector; JobSet handles slice assignment automatically.*

--------------------------------------------------------------------------------

## Resolution & Management Workflow

### Resolution 1: Force Delete a Stuck Slice [High Risk]

If a slice is stuck in `DEACTIVATING` or deletion hangs indefinitely due to stuck finalizers:

1. **Identify Cause**: Explain that finalizers on the slice resource (`metadata.finalizers`) are preventing Kubernetes from completing resource deletion.
2. **Propose Resolution**: Propose removing finalizers from the metadata path (`/metadata/finalizers`) using a JSON patch operation:

    ```bash
    kubectl patch slice {slice_name} --type json -p='[{"op": "remove", "path": "/metadata/finalizers"}]'
    ```

3. **Provide Warning**: Explicitly warn the user that removing finalizers bypasses standard controller dismantling and may leave underlying VM, network, or accelerator resources uncleaned or orphaned.
4. **CRITICAL SAFETY MANDATE**: The response MUST explicitly ask the user for confirmation (e.g. *"Removing finalizers on `/metadata/finalizers` via JSON patch is a high-risk operation that may leave orphaned resources. Do you confirm you want to apply this patch to slice `{slice_name}`?"*) and pause for user confirmation before applying or executing the patch.

--------------------------------------------------------------------------------

### Resolution 2: Disable and Clean Up Slice Controller [High Risk]

If dynamic slicing needs to be disabled:

1.  **Check for existing Slices**:

    ```bash
    kubectl get slice -A
    ```

    Ensure all slices are deleted before disabling the controller.

2.  **Disable Slice Controller via gcloud**:

    ```bash
    gcloud container clusters update {cluster_name} \
        --location={location} \
        --no-enable-slice-controller
    ```

3.  **Delete the Slice CRD**:

    ```bash
    kubectl delete crd slices.accelerator.gke.io
    ```

4.  **Clean up Node Labels**: Remove GKE TPU Slice labels from all nodes in the
    cluster:

    ```bash
    kubectl label nodes --all cloud.google.com/gke-tpu-slice- cloud.google.com/gke-tpu-slice-topology-
    ```

-   **Safety Rule**: Propose the exact commands and confirm before executing
    disabling or destructive cleanup steps.
