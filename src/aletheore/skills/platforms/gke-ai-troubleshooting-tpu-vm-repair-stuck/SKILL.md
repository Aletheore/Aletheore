---
name: gke-ai-troubleshooting-tpu-vm-repair-stuck
description: 'Diagnose GKE TPU VM instances stuck in Compute Engine repair (REPAIRING state) after host errors, maintenance events, or faulty host reports in single-host, multi-host, dynamic slicing, and TPU All Capacity mode node pools. Use when TPU instances in a slice node pool remain in REPAIRING, host error events appear in audit logs, or TPU slices are unschedulable due to a VM in repair. Don''t use for nodes reporting Ready: Unknown when no VM is in REPAIRING (use gke-ai-troubleshooting-node-unresponsive-timeout), other NotReady nodes when no VM is in REPAIRING (use gke-node-notready), Pending Pods when all nodes are Ready (use gke-workload-troubleshooting), JobSet preemption events (use gke-ai-troubleshooting-jobset-interruption), or Slice custom resource lifecycle failures (use gke-ai-troubleshooting-tpu-dynamic-slices-monitoring).'
metadata:
  version: 1.0.0
  category: Containers
skill_id: platforms/gke-ai-troubleshooting-tpu-vm-repair-stuck
title: GKE AI Troubleshooting TPU VM Repair Stuck
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-vm-repair-stuck
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-ai-troubleshooting-tpu-vm-repair-stuck", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-vm-repair-stuck", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "GKE AI/TPU troubleshooting (niche)", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Troubleshoot GKE TPU VMs stuck in Compute Engine repair (`REPAIRING`)

This skill checks the VM status, the host events, and the GKE repair status, and
routes recovery through GKE instead of direct Compute Engine changes.

## Prerequisites

- **Tools**:
  - For installing and initializing `gcloud` (which covers authentication and
    project selection), follow
    [Before you begin](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt).
  - For installing `kubectl` and required plugins, follow
    [Install kubectl](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt)
    and
    [Install required plugins](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt).
- **Cloud Billing & Project Configuration**: Ensure the project has an active
  Cloud Billing account. Follow
  [Before you begin](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
  to initialize `gcloud` and enable the Google Kubernetes Engine API
  (`container.googleapis.com`), ensure the Compute Engine API
  (`compute.googleapis.com`) and Cloud Logging API (`logging.googleapis.com`)
  are enabled, and follow
  [Store cluster information for kubectl](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt)
  to configure `kubectl` cluster credentials.
- **Required IAM Roles**:
  - Kubernetes Engine Viewer (`roles/container.viewer`)
  - Compute Viewer (`roles/compute.viewer`)
  - Logs Viewer (`roles/logging.viewer`)
  - For the user who enables node auto-repair in Step 5, item 1: Kubernetes
    Engine Cluster Admin (`roles/container.clusterAdmin`)
- **Documentation**:
  - [View information about MIGs and managed instances](https://docs.cloud.google.com/compute/docs/instance-groups/getting-info-about-migs.md.txt)
    (Sections: "List MIGs", "Check the status of managed instances")
  - [Compute Engine instance lifecycle](https://docs.cloud.google.com/compute/docs/instances/instance-lifecycle.md.txt)
    (Sections: "`REPAIRING` state")
  - [Troubleshooting VM shutdowns and reboots](https://docs.cloud.google.com/compute/docs/troubleshooting/troubleshooting-reboots.md.txt)
    (Sections: "Querying Cloud Audit Logs", "Reviewing Cloud Audit Logs")
  - [Configure cluster access for kubectl](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt)
    (Sections: "Install kubectl", "Install required plugins", "Store cluster
    information for kubectl")
  - [Add and manage node pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-pools.md.txt)
    (Sections: "View node pools in a Standard cluster", "Deploy a Pod to a
    specific node pool")
  - [Troubleshoot NodeNotReady state](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-notready.md.txt)
    (Sections: "Check the node's status and conditions", "Confirm node
    preemption")
  - [About TPUs in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/tpus.md.txt)
    (Sections: "Multi-host TPU slice node pools")
  - [Use dynamic slicing with a custom scheduler](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
    (Sections: "Requirements", "Verify the status of the nodes and the
    partitions", "Monitor the status of the Slice custom resource", "Hardware
    maintenance and failures")
  - [About GKE dynamic slicing](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dynamic-slicing.md.txt)
    (Sections: "Key characteristics of dynamic slicing configurations")
  - [Deploy TPUs in All Capacity mode in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpu-all-capacity-mode.md.txt)
    (Sections: "Manage node failures", "Node failure and recovery")
  - [Report and repair faulty TPU hosts in All Capacity mode](https://docs.cloud.google.com/tpu/docs/all-capacity-report-faulty-hosts.md.txt)
    (Sections: "Understand the faulty host report process", "Review report
    faulty host operations")
  - [Auto-repair nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
    (Sections: "Settings for Autopilot and Standard", "Repair criteria",
    "Node repair process", "Verify node auto-repair is enabled for a Standard
    node pool", "Enable auto-repair for an existing Standard node pool", "Get
    information about recent automated repair events", "Node auto repair in TPU
    slice nodes")
  - [Deploy TPU workloads in GKE Standard](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
    (Sections: "Before you begin", "Configure auto repair for TPU slice nodes")
  - [Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
    (Sections: "Before you contact support", "Recommendations for efficient
    support")
  - [REST Resource: projects.locations.clusters.nodePools](https://docs.cloud.google.com/kubernetes-engine/docs/reference/rest/v1/projects.locations.clusters.nodePools.md.txt)
    (Sections: "Resource: NodePool")

> **Read-only rule**: Run read-only diagnostic commands only. Never drain,
> delete, or re-create nodes, or run any other command that changes the cluster.
> Give the user any fix to apply themselves.

When you recommend a fix, link the doc section that describes it.

---

## Diagnostic workflow

### Step 0: Collect context `[Low Risk]`

Collect the target parameters:

- `{project_id}`: Google Cloud project ID
- `{cluster_name}`: GKE cluster name
- `{location}`: Cluster region or zone
- `{cluster_mode}`: Cluster mode (`Autopilot` or `Standard`)
- `{tpu_version}`: TPU version (such as Ironwood `tpu7x`, TPU v6e, v5p, v5e, or
  v4) and whether the node pool is a single-host or multi-host TPU slice node
  pool
- `{nodepool_name}`: TPU node pool name
- `{group_name}` and `{zone}`: Name and zone of the managed instance group (MIG)
  that backs the node pool:
  - In a **Standard** cluster, follow
    [View node pools in a Standard cluster](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-pools.md.txt)
    to describe `{nodepool_name}` and read its `instanceGroupUrls` field (see
    [Resource: NodePool](https://docs.cloud.google.com/kubernetes-engine/docs/reference/rest/v1/projects.locations.clusters.nodePools.md.txt)).
  - In an **Autopilot** cluster (or if `{nodepool_name}` isn't known), follow
    [List MIGs](https://docs.cloud.google.com/compute/docs/instance-groups/getting-info-about-migs.md.txt)
    to list the managed instance groups in `{project_id}` and find
    `{group_name}` and `{zone}`.
- `{instance_name}`: Affected TPU VM name, if known. If the user doesn't know
  `{instance_name}`, Step 1 finds the instances in `REPAIRING`.
- `{issue_time}`: Incident timestamp in RFC3339 UTC, if known

---

### Step 1: Check the VM status in the managed instance group `[Low Risk]`

To list the instances in `{group_name}` with their status (`STATUS` /
`instanceStatus`) and current action (`ACTION` / `currentAction`), follow the
instructions in
[Check the status of managed instances](https://docs.cloud.google.com/compute/docs/instance-groups/getting-info-about-migs.md.txt).

- **Analysis**:
  - **`STATUS` (`instanceStatus`) is `REPAIRING`**: Compute Engine is repairing
    the VM because it encountered an internal error or its host server is
    unavailable due to maintenance. The VM can't be used during the repair. See
    [`REPAIRING` state](https://docs.cloud.google.com/compute/docs/instances/instance-lifecycle.md.txt).
    Record `{instance_name}` and proceed to Step 2.
  - **No instance is in `REPAIRING`** (every instance reports `RUNNING` with
    `ACTION` `NONE`, or an instance reports another status or action such as
    `ACTION` `RECREATING`, which means "The instance is being replaced" per
    [Check the status of managed instances](https://docs.cloud.google.com/compute/docs/instance-groups/getting-info-about-migs.md.txt)):
    Check each node's status and conditions following
    [Check the node's status and conditions](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-notready.md.txt)
    and hand off to the matching sibling skill:
    - If a node's `Ready` condition is `Unknown` (`NodeStatusUnknown`), hand off
      to `gke-ai-troubleshooting-node-unresponsive-timeout`.
    - If a node isn't `Ready` for another reason, hand off to
      `gke-node-notready`.
    - If every node is `Ready` (per
      [Repair criteria](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt),
      "A node reporting a Ready status is considered healthy") but the
      workload's Pods are still `Pending`, hand off to
      `gke-workload-troubleshooting`.

---

### Step 2: Check Compute Engine system events `[Low Risk]`

To list the system events for `{instance_name}` around `{issue_time}`, follow
the instructions in the section
[Querying Cloud Audit Logs](https://docs.cloud.google.com/compute/docs/troubleshooting/troubleshooting-reboots.md.txt).
Compare the `method` field with the table in the
[Reviewing Cloud Audit Logs](https://docs.cloud.google.com/compute/docs/troubleshooting/troubleshooting-reboots.md.txt)
section of the same document:

- **`compute.instances.hostError`,
  `compute.instances.terminateOnHostMaintenance`, or
  `compute.instances.automaticRestart`**: Record the matching system event log
  entries and timestamps, and continue to Steps 3 and 4.
- **`compute.instances.preempted`**: Compute Engine preempted a Spot VM or
  preemptible VM. If the affected workload is a `JobSet`, hand off to
  `gke-ai-troubleshooting-jobset-interruption`. Otherwise, follow
  [Confirm node preemption](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-notready.md.txt).
- **No method from the table, or another method**: Per the note in
  [Reviewing Cloud Audit Logs](https://docs.cloud.google.com/compute/docs/troubleshooting/troubleshooting-reboots.md.txt),
  if a VM rebooted and none of the listed methods appears in the query results,
  the reboot likely happened because the VM's operating system initiated it. If
  the VM is still in `REPAIRING` in Step 1 (or a method such as
  `compute.instances.reportHostAsFaulty` appears), continue to Steps 3 and 4.

---

### Step 3: Check the node status and slice impact `[Low Risk]`

Follow
[Check the node's status and conditions](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-notready.md.txt)
to check the status and conditions of the nodes in the cluster and match
`{instance_name}` using the node's `ProviderID` field. To filter nodes to
`{nodepool_name}`, use the `cloud.google.com/gke-nodepool` node label documented
in
[Deploy a Pod to a specific node pool](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-pools.md.txt).

- If the node for `{instance_name}` reports `Ready` (per
  [Repair criteria](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt),
  "A node reporting a Ready status is considered healthy") and the VM is no
  longer in `REPAIRING`, proceed to Step 5, item 4.
- Otherwise, determine how the unhealthy node affects the node pool based on
  the node pool configuration:
  - **Single-host TPU slice node pool**: Per
    [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt),
    "In a single-host TPU slice node pool, only the unhealthy TPU node is
    auto-repaired."
  - **Dynamic slicing node pool**: To check whether `{nodepool_name}` uses
    dynamic slicing, see
    [Requirements](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt):
    dynamic slicing requires a Standard cluster, Ironwood (`tpu7x`), and All
    Capacity mode reservations. For a Standard cluster with Ironwood (`tpu7x`),
    check whether the node has `cloud.google.com/gke-accelerator-topology-mode:
    PROVISION_ONLY` as shown in step 2 of
    [Verify the status of the nodes and the partitions](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
    (per
    [Key characteristics of dynamic slicing configurations](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dynamic-slicing.md.txt),
    "dynamic slicing uses incremental provisioning"). Per
    [Hardware maintenance and failures](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt),
    "If you trigger customer-initiated maintenance or if a hardware failover
    occurs, only the targeted nodes are affected rather than the entire node
    pool." For an active dynamic sub-slice, the `Slice` custom resource
    transitions to `FAILED` and the scheduler attempts to re-create the slice on
    other available healthy nodes. Check the `Slice` status following
    [Monitor the status of the Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt);
    if the `Slice` custom resource is in `FAILED` or another non-active state,
    hand off `Slice` troubleshooting to
    `gke-ai-troubleshooting-tpu-dynamic-slices-monitoring` alongside checking
    the VM repair in Step 4.
  - **TPU All Capacity mode node pool**: Per
    [Manage node failures](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpu-all-capacity-mode.md.txt)
    and
    [Node failure and recovery](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpu-all-capacity-mode.md.txt),
    in TPU All Capacity mode, if a VM fails due to issues like hardware
    failure, Google Cloud "attempts to recover the VM on the same host
    (in-place repair)", failed nodes have a status of `NotReady`, and the node
    pool's status doesn't change to `ERROR`.
  - **Multi-host TPU slice node pool (without dynamic slicing or All Capacity
    mode)**: Per the section
    [Multi-host TPU slice node pools](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/tpus.md.txt),
    the VMs in a multi-host TPU slice node pool are treated as a single atomic
    unit. When a node in the slice requires repairing, GKE shuts down all VMs in
    the TPU slice, forcing eviction of all the Kubernetes Pods in the workload,
    and the Pods can be scheduled again only after all VMs in the slice are up
    and running.

---

### Step 4: Check node auto-repair status and faulty host reports `[Low Risk]`

Find out whether GKE or Compute Engine is repairing the node:

1. **Check whether node auto-repair is enabled**:
   - **Autopilot clusters**: Autopilot always repairs nodes, and you can't turn
     this off (see
     [Settings for Autopilot and Standard](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)).
   - **Standard clusters**: Follow
     [Verify node auto-repair is enabled for a Standard node pool](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
     to check whether `autoRepair` is enabled on `{nodepool_name}`.
2. **Check the GKE repair operation history**: Per
   [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt),
   check the repair status, including any failure reason, in the operation
   history described in
   [Get information about recent automated repair events](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt):
   - If a repair operation failed because of insufficient quota, tell the user
     to contact their Google Cloud account representative to increase the quota.
   - If `autoRepair` is enabled and no repair operation has appeared yet, check
     how long the node has been unhealthy (from `{issue_time}` or the `Ready`
     condition's `LastTransitionTime` in Step 3). Per
     [Repair criteria](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt),
     GKE initiates a repair when a node reports a `NotReady` status, or doesn't
     report any status at all, over a given time threshold of approximately 10
     minutes. If the node has reported `NotReady` (or no status) for less than
     approximately 10 minutes, check the operation history again after that
     threshold has passed. If it has been longer than approximately 10 minutes
     and there is still no repair operation (and no All Capacity mode faulty
     host report in item 3), escalate in Step 5, item 3.
3. **For TPU All Capacity mode node pools, check for a faulty host report**:
   Per
   [Understand the faulty host report process](https://docs.cloud.google.com/tpu/docs/all-capacity-report-faulty-hosts.md.txt),
   after a user reports a faulty host, the TPU VM is stopped, its state changes
   to `REPAIRING`, and "The host repair process can take between 3 and 14 days,
   or longer." Before escalating a VM in `REPAIRING` in an All Capacity mode
   reservation, follow
   [Review report faulty host operations](https://docs.cloud.google.com/tpu/docs/all-capacity-report-faulty-hosts.md.txt)
   to check for a `compute.instances.reportHostAsFaulty` operation on
   `{instance_name}`.

---

### Step 5: Resolution `[High Risk]`

> **Guardrails**:
> - **Never** change GKE-managed instance groups or VMs directly through Compute
>   Engine, for example with `gcloud compute instances delete` or `gcloud
>   compute instance-groups managed` commands that change the group. Manage the
>   node pool through GKE.
> - **Never** run `kubectl delete node` on nodes in the node pool. Per
>   [Settings for Autopilot and Standard](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt),
>   if you use `kubectl` to delete an unhealthy node when auto-repair is
>   disabled, the underlying VM might become orphaned from the cluster, and you
>   would continue to be billed for it.

1. **Enable node auto-repair (Standard clusters only)**:
   - Autopilot clusters always auto-repair nodes, so skip this step.
   - Before the user enables auto-repair on a multi-host TPU slice node pool
     that doesn't use dynamic slicing or All Capacity mode, tell them that GKE
     re-creates the entire node pool when a node in it needs repair (see item 2
     of this step).
   - If `autoRepair` is disabled on `{nodepool_name}`, link the user (who needs
     the `Kubernetes Engine Cluster Admin` role, `roles/container.clusterAdmin`)
     to
     [Enable auto-repair for an existing Standard node pool](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
     and
     [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
     so they can apply the change themselves.
2. **Let GKE or All Capacity mode repair the node or slice**:
   - **Single-host TPU slice node pool**: Per
     [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt),
     "In a single-host TPU slice node pool, only the unhealthy TPU node is
     auto-repaired." Per
     [Node repair process](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt),
     for a single node repair, GKE drains the node and waits one hour for the
     drain to complete; if the drain doesn't complete, GKE shuts the node down
     and creates a new node.
   - **Multi-host TPU slice node pool (without dynamic slicing or All Capacity
     mode)**: Per
     [Node auto repair in TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
     and
     [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt),
     if a TPU slice node in a multi-host TPU slice node pool is unhealthy and
     requires auto repair, the entire node pool is re-created.
   - **Dynamic slicing node pool**: Per
     [Hardware maintenance and failures](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt),
     only the targeted nodes are affected rather than the entire node pool, and
     for dynamic sub-slicing the scheduler attempts to re-create failed slices
     on other available healthy nodes. If `Slice` custom resources fail to form
     or remain `FAILED`, hand off to
     `gke-ai-troubleshooting-tpu-dynamic-slices-monitoring`.
   - **TPU All Capacity mode with a faulty host report**: If Step 4, item 3
     found a `compute.instances.reportHostAsFaulty` operation for
     `{instance_name}` (tracked via
     [Review report faulty host operations](https://docs.cloud.google.com/tpu/docs/all-capacity-report-faulty-hosts.md.txt)),
     do not escalate the VM as stuck. Tell the user that per
     [Understand the faulty host report process](https://docs.cloud.google.com/tpu/docs/all-capacity-report-faulty-hosts.md.txt),
     the VM stops during the repair and restarts on the same host after the
     repair is complete, and "The host repair process can take between 3 and 14
     days, or longer."
3. **Contact Cloud Customer Care if the repair doesn't start or complete**:
   - If the VM stays in `REPAIRING` (and isn't waiting on a 3-to-14-day All
     Capacity mode faulty host repair), if the node has been `NotReady` (or
     reported no status) for longer than the ~10-minute threshold in
     [Repair criteria](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
     with `autoRepair` enabled and no repair operation in Step 4, or if the
     operation history shows a failed repair that isn't caused by quota, direct
     the user to
     [Before you contact support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
     and
     [Recommendations for efficient support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
     to contact Cloud Customer Care. In addition to the details listed in
     [Recommendations for efficient support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt),
     include the node pool name, instance name, the system event log entries
     from Step 2, and the repair operation name from Step 4.
4. **Verify recovery**:
   - Repeat Step 1 and Step 3. When recovery is complete, every instance in
     `{group_name}` reports `RUNNING` with action `NONE`, and every node in
     `{nodepool_name}` is `Ready`.
   - If the instances report `RUNNING` with action `NONE` (or another status or
     action) and a node still isn't `Ready`, follow the handoffs in Step 1
     (`gke-ai-troubleshooting-node-unresponsive-timeout` or
     `gke-node-notready`) or escalate to Cloud Customer Care in item 3 of this
     step.
