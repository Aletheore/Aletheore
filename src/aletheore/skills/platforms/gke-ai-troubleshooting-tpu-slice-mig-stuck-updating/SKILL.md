---
name: gke-ai-troubleshooting-tpu-slice-mig-stuck-updating
description: Diagnose and resolve GKE TPU Dynamic Slice creation and JobSet scheduling failures blocked by underlying Compute Engine managed instance groups (MIGs) stuck in an updating or unstable state (isStable=False). Use when TPU slice creation fails with a SliceCreationFailed status reason, when JobSets remain stuck in pending, or when MIG status reflects isStable=False or versionTarget.isReached=False during TPU slice formation. Don't use for SliceCreationFailed causes unrelated to MIG stability (use gke-ai-troubleshooting-tpu-dynamic-slices-monitoring) or general pod scheduling failures unrelated to TPU slices.
metadata:
  version: 1.0.0
  category: Containers
skill_id: platforms/gke-ai-troubleshooting-tpu-slice-mig-stuck-updating
title: GKE AI Troubleshooting TPU Slice Mig Stuck Updating
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-slice-mig-stuck-updating
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-ai-troubleshooting-tpu-slice-mig-stuck-updating", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-slice-mig-stuck-updating", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "GKE AI/TPU troubleshooting (niche)", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Troubleshoot GKE TPU slice creation blocked by an unstable MIG

This skill helps diagnose GKE TPU Dynamic Slice creation failures and JobSet
scheduling delays when the Compute Engine managed instance groups (MIGs) that
back the TPU node pool aren't stable (`status.isStable: false`).

A `SliceCreationFailed` status reason means that slice creation failed because
prerequisites weren't met (for example, required Compute Engine resources are
missing) or preflight checks failed (see
[Use dynamic slicing with a custom scheduler](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)).
A MIG that is still creating, updating, or repairing instances is one cause to
check. This skill checks the slice status first, then the stability of the
backing MIGs.

## Prerequisites

- **Google Cloud SDK & Tools**:
  - For installing and initializing `gcloud` (project selection and
    authentication) and enabling the GKE API, follow
    [Before you begin](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt).
  - For installing `kubectl` and configuring cluster credentials, follow the
    "Install kubectl", "Install required plugins", and "Store cluster
    information for kubectl" sections of
    [Install kubectl and configure cluster access](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt).
- **API Enablement & Billing**:
  - Verify that the project has an active Cloud Billing account.
  - Enable APIs: `container.googleapis.com`, `compute.googleapis.com`, and
    `logging.googleapis.com`.
- **Required IAM Roles**:
  - Logs Viewer (`roles/logging.viewer`): Inspect Cloud Audit Logs for MIG
    errors.
  - Compute Viewer (`roles/compute.viewer`): Check MIG stability (`isStable`)
    and instance progress.
  - Kubernetes Engine Viewer (`roles/container.viewer`): Inspect Slice custom
    resources (`slices.accelerator.gke.io`), nodes, and cluster operations.
- **Documentation**:
  - [Use dynamic slicing with a custom scheduler](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
    (Sections: "Before you begin", "Create a Slice custom resource", "Monitor
    the status of the Slice custom resource", "Verify the status of the nodes
    and the partitions", "Upgrade your node pools")
  - [Install kubectl and configure cluster access](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt)
    (Sections: "Install kubectl", "Install required plugins", "Store cluster
    information for kubectl")
  - [Add and manage node pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-pools.md.txt)
    (Sections: "View node pools in a Standard cluster")
  - [View information about MIGs and managed instances](https://docs.cloud.google.com/compute/docs/instance-groups/getting-info-about-migs.md.txt)
    (Sections: "Check whether a MIG is stable", "Check the status of managed
    instances", "List instance errors")
  - [Automatically apply VM configuration updates in a MIG](https://docs.cloud.google.com/compute/docs/instance-groups/rolling-out-updates-to-managed-instance-groups.md.txt)
    (Sections: "Verify whether an update rollout is complete")
  - [Manually upgrade a cluster's control plane or node pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/upgrading-a-cluster.md.txt)
    (Sections: "Check node pool upgrade status", "Cancel a node pool upgrade",
    "Resume a node pool upgrade", "Roll back a node pool upgrade")
  - [Auto-repair nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
    (Sections: "Get information about recent automated repair events")
  - [Troubleshoot GKE Standard node pools](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-pools.md.txt)
    (Sections: "Issue: Node pool creation fails due to insufficient resources")
  - [Troubleshoot TPUs in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/tpus.md.txt)
    (Sections: "Insufficient quota to satisfy the TPU request", "Insufficient
    TPU resources to satisfy the TPU request")
  - [REST Resource: projects.locations.clusters.nodePools](https://docs.cloud.google.com/kubernetes-engine/docs/reference/rest/v1/projects.locations.clusters.nodePools.md.txt)
    (Sections: "Resource: NodePool")
  - [Schedule dynamic slices with Kueue and TAS](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt)
    (Sections: "Monitor the status of the slice")
  - [About TPUs in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/tpus.md.txt)
  - [Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
    (Sections: "Before you contact support")

> **Read-only rule**: Run read-only diagnostic commands only. Never drain,
> delete, or re-create nodes, or run any other command that changes the cluster.
> Give the user any fix to apply themselves.

When you recommend a fix, link the doc section that describes it.

---

## Diagnostic workflow

### Step 0: Collect context and set the investigation window `[Low Risk]`

Collect the necessary contextual parameters from the user prompt or environment:
- `{project_id}`, `{cluster_name}`, `{location}`, `{nodepool_name}`, and
  `{issue_time}`.
- `{mig_name}` and `{zone}`: Name and zone of the managed instance group that
  backs `{nodepool_name}`, from the node pool's `instanceGroupUrls` field (see
  [View node pools in a Standard cluster](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-pools.md.txt)
  and
  [Resource: NodePool](https://docs.cloud.google.com/kubernetes-engine/docs/reference/rest/v1/projects.locations.clusters.nodePools.md.txt)).
  If `{nodepool_name}` or `{mig_name}` isn't already known from a failing
  `Slice`, find the affected partition, nodes, and backing MIG by following
  [Verify the status of the nodes and the partitions](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt).
- **Time window**: By default, query a 60-minute window `[{issue_time} - 30m,
  {issue_time} + 30m]` (`{start_time}` to `{end_time}`).

---

### Step 1: Detect slice creation failures and MIG errors `[Low Risk]`

1. **Inspect Slice status and partition nodes**: Check the `Slice` custom
   resource `Status.Conditions` for a `SliceCreationFailed` status reason and
   identify the affected partitions, nodes, and `{mig_name}` by following the
   "Monitor the status of the Slice custom resource" and "Verify the status of
   the nodes and the partitions" sections of
   [Use dynamic slicing with a custom scheduler](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt),
   and
   [Monitor the status of the slice](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt).
2. **Query Cloud Logging for MIG errors**: Once `{mig_name}` is identified (or
   by narrowing the results to `{mig_name}` once known), run this Cloud Logging
   query to detect errors on the backing instance group manager across
   `[{start_time}, {end_time}]`:
   ```
   resource.type="gce_instance_group_manager"
   resource.labels.instance_group_manager_name="{mig_name}"
   severity>=ERROR
   timestamp >= "{start_time}" AND timestamp <= "{end_time}"
   ```
- **Findings**:
  - If the `Slice` status reports `SliceCreationFailed` or the logs show
    instance group errors, proceed to Step 2.
  - If no error logs match and the TPU `Slice` status is `ACTIVE`, rule out MIG
    instability and check workload configuration.

---

### Step 2: Check MIG stability (`status.isStable`) `[Low Risk]`

Follow
[Check whether a MIG is stable](https://docs.cloud.google.com/compute/docs/instance-groups/getting-info-about-migs.md.txt)
to inspect the stability (`status.isStable`) and version target status
(`status.versionTarget.isReached`) of `{mig_name}`:

- **What the fields mean**:
  - `status.isStable: false`: Changes are active or pending, or the MIG itself
    is being modified. Proceed to Step 3.
  - `status.versionTarget.isReached: false`: At least one instance isn't running
    the target version yet (see
    [Automatically apply VM configuration updates in a MIG](https://docs.cloud.google.com/compute/docs/instance-groups/rolling-out-updates-to-managed-instance-groups.md.txt)).
    Proceed to Step 3.
  - `status.isStable: true` and `status.versionTarget.isReached: true`: No
    instances are undergoing or scheduled for changes (`currentAction` is `NONE`
    for all instances), and the MIG itself isn't being modified. This rules out
    MIG instability. If the `Slice` is still in `SliceCreationFailed`, hand off
    to `gke-ai-troubleshooting-tpu-dynamic-slices-monitoring`, or follow the
    "Create a Slice custom resource" and "Verify the status of the nodes and the
    partitions" sections of
    [Use dynamic slicing with a custom scheduler](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt).

---

### Step 3: Find failed or stalled instance operations `[Low Risk]`

Direct the user to the "Check the status of managed instances" and "List
instance errors" sections of
[View information about MIGs and managed instances](https://docs.cloud.google.com/compute/docs/instance-groups/getting-info-about-migs.md.txt)
(include both section names and the document title) to inspect individual
instance states (`currentAction` and `instanceStatus`) and list the most recent
errors in `{mig_name}`:

- If the errors show capacity or quota failures such as
  `ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS` or `QUOTA_EXCEEDED`, go to Step 5,
  item 2.
- Otherwise, proceed to Step 4 to check for node pool upgrade or auto-repair
  operations.

---

### Step 4: Check node pool operations `[Low Risk]`

A MIG that is still changing instances can be the result of a node pool upgrade
or repair:

1. **Check node pool upgrades**: Follow
   [Check node pool upgrade status](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/upgrading-a-cluster.md.txt)
   to list the cluster operations and find upgrade operations targeting
   `{nodepool_name}` in `[{start_time}, {end_time}]`. Also review
   [Upgrade your node pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
   for dynamic slicing upgrade requirements (required surge settings and
   deleting active super-slices before upgrading).
   - If an upgrade operation is still running, go to Step 5, item 1 or item 3.
   - If an upgrade operation was cancelled, failed, or is incomplete, go to Step
     5, item 4.
2. **Check automated node repairs**: Follow
   [Get information about recent automated repair events](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
   to check for repair operations on `{nodepool_name}`.
   - If a node auto-repair operation is running, go to Step 5, item 1.
3. **If no operation or error explains the unstable MIG**: Go to Step 5, item 5
   to contact Cloud Customer Care.

---

### Step 5: Resolution `[High Risk]`

> **Guardrail**: Don't modify GKE-managed instance groups or VMs directly
> through Compute Engine. Manage node pools through GKE.

1. **Wait for an ongoing update or auto-repair to finish**: If a node pool
   upgrade or automated repair is still making progress, the user can wait until
   the MIG is stable (`status.isStable: true`) by following
   [Check whether a MIG is stable](https://docs.cloud.google.com/compute/docs/instance-groups/getting-info-about-migs.md.txt).
2. **Resolve capacity or quota errors**: When Step 3 shows
   `ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS` or `QUOTA_EXCEEDED`, rolling back
   an upgrade won't resolve the shortage. Follow
   [Issue: Node pool creation fails due to insufficient resources](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-pools.md.txt)
   and the "Insufficient quota to satisfy the TPU request" and "Insufficient TPU
   resources to satisfy the TPU request" sections of
   [Troubleshoot TPUs in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/tpus.md.txt).
3. **Cancel an in-progress node pool upgrade**: If the user wants to stop an
   upgrade that is still running, follow
   [Cancel a node pool upgrade](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/upgrading-a-cluster.md.txt).
   For dynamic slicing node pools, also check
   [Upgrade your node pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
   for the required surge settings and deleting active super-slices before
   upgrading.
4. **Resume or roll back a cancelled, failed, or incomplete node pool upgrade**:
   - Follow the "Resume a node pool upgrade" or "Roll back a node pool upgrade"
     sections of
     [Manually upgrade a cluster's control plane or node pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/upgrading-a-cluster.md.txt).
     As documented there, rollback applies if an in-progress upgrade was
     cancelled, the upgrade failed, or the upgrade is incomplete due to a
     maintenance window timing out (you can't roll back node pools once they
     have been successfully upgraded); alternatively, a cancelled or failed,
     partially completed upgrade can be resumed.
   - Afterwards, verify that the MIG is stable by following
     [Check whether a MIG is stable](https://docs.cloud.google.com/compute/docs/instance-groups/getting-info-about-migs.md.txt).
5. **Escalate unexplained MIG instability**: If no node pool upgrade, repair
   operation, or capacity/quota error explains why `{mig_name}` remains
   unstable, direct the user to Cloud Customer Care by following
   [Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt).
6. **Verify node pool and slice recovery**: After the instance group is stable
   (`status.isStable: true`), follow the "Monitor the status of the Slice custom
   resource" and "Create a Slice custom resource" sections of
   [Use dynamic slicing with a custom scheduler](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
   to verify that the `Slice` reaches the `ACTIVE` state. If the `Slice` stays
   in `SliceCreationFailed` after the MIG stabilizes, check whether the retry
   annotation documented in "Create a Slice custom resource" is set on the
   `Slice`.
