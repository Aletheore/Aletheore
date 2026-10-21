---
name: gke-ai-troubleshooting-tpu-reservation-stockout
description: Diagnose and resolve cluster-wide GKE TPU node pool creation or scale-up failures caused by Compute Engine reservation stockouts (GCE_STOCKOUT, ZONE_RESOURCE_POOL_EXHAUSTED) when existing unhealthy or NotReady TPU node pools in the cluster have node auto-repair disabled and hold reserved TPU capacity. Use when TPU node pool creation or scale-up fails with GCE_STOCKOUT, ZONE_RESOURCE_POOL_EXHAUSTED, or ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS despite having a specific Compute Engine TPU reservation. Don't use for general pod scheduling without a TPU reservation or workload-level container crashes.
metadata:
  version: 1.0.0
  category: Containers
skill_id: platforms/gke-ai-troubleshooting-tpu-reservation-stockout
title: Gke Ai Troubleshooting Tpu Reservation Stockout
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-reservation-stockout
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-ai-troubleshooting-tpu-reservation-stockout", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-reservation-stockout", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "GKE AI/TPU troubleshooting (niche)", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Troubleshoot GKE TPU reservation stockouts caused by leaked node pool capacity

Use this skill to diagnose and resolve GKE TPU node pool creation or scale-up
failures that fail with `GCE_STOCKOUT`, `ZONE_RESOURCE_POOL_EXHAUSTED`, or
`ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS` (or queue due to lack of capacity)
even though they target a specific Compute Engine TPU reservation.

If node auto-repair is disabled on an existing TPU node pool, GKE doesn't repair
its nodes when they become `NotReady`. While their VMs are still running, they
keep consuming slots in the reservation (`inUseCount` is the number of running
instances that consume the reservation). Per
[Ensure reservation availability](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt),
the reservation must have sufficient available TPU chips at the time of node
pool creation, and per
[Multi-host TPU slice node pools](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/tpus.md.txt),
if GKE is unable to deploy one node in a slice, no nodes in the TPU slice node
pool are deployed. When the free slots in the reservation
(`specificReservation.count - specificReservation.inUseCount`; see
[Consumable instances in a reservation](https://docs.cloud.google.com/compute/docs/instances/reservations-view.md.txt))
are fewer than the number of nodes that the new TPU node pool needs, creation
fails with `GCE_STOCKOUT` or `ZONE_RESOURCE_POOL_EXHAUSTED` (or queues due to
lack of capacity).

## Prerequisites

- **Required IAM Roles**:
  - Logs Viewer (`roles/logging.viewer`): Query Cloud Audit Logs in Cloud
    Logging.
  - Monitoring Viewer (`roles/monitoring.viewer`): Query GKE node readiness
    metrics in Cloud Monitoring.
  - Kubernetes Engine Viewer (`roles/container.viewer`): Inspect GKE node pools
    (`management.autoRepair`), cluster operations, and node conditions.
  - Compute Viewer (`roles/compute.viewer`): Inspect Compute Engine reservation
    utilization (`specificReservation.inUseCount` vs
    `specificReservation.count`), consuming instances, and VM labels (including
    in the reservation's owner project if using a shared reservation).
  - For remediation (`[High Risk]` steps): Kubernetes Engine Cluster Admin
    (`roles/container.clusterAdmin`)
- **API Enablement & Billing**:
  - Ensure the target project has an active Google Cloud billing account
    attached: `gcloud billing projects describe {project_id}`.
  - Enable required APIs: `container.googleapis.com`, `compute.googleapis.com`,
    `logging.googleapis.com`, and `monitoring.googleapis.com`.
- **CLI & Authentication Setup**:
  - Install Google Cloud SDK (`gcloud`) and `kubectl`:
    https://cloud.google.com/sdk/docs/install.
- **Cluster mode**: This skill applies to Standard clusters. Autopilot clusters
  always auto-repair nodes, and you can't turn this off (see
  [Settings for Autopilot and Standard](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)).
- **Reference Documentation**:
  - [Auto-repair nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
    (Sections: "Settings for Autopilot and Standard", "Repair criteria", "Node
    repair process", "Verify node auto-repair is enabled for a Standard node
    pool", "Node auto repair in TPU slice nodes", "Enable auto-repair for an
    existing Standard node pool", "Get information about recent automated repair
    events")
  - [Troubleshoot GKE Standard node pools](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-pools.md.txt)
    (Sections: "Issue: Node pool creation fails due to insufficient resources")
  - [Troubleshoot TPUs in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/tpus.md.txt)
    (Sections: "How GKE handles capacity issues", "Insufficient TPU resources to
    satisfy the TPU request")
  - [About TPUs in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/tpus.md.txt)
    (Sections: "Multi-host TPU slice node pools")
  - [Consuming reserved zonal resources](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/consuming-reservations.md.txt)
    (Sections: "Consuming a specific shared reservation")
  - [About reservations](https://docs.cloud.google.com/compute/docs/instances/reservations-overview.md.txt)
    (Sections: "How reservations work")
  - [View reservations or future reservation requests](https://docs.cloud.google.com/compute/docs/instances/reservations-view.md.txt)
    (Sections: "Limitations", "View reservations", "Consumable instances in a
    reservation", "View a list of instances that are consuming a reservation")
  - [Organize resources using labels](https://docs.cloud.google.com/compute/docs/labeling-resources.md.txt)
    (Sections: "View labels")
  - [Create and manage cluster and node pool labels](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/creating-managing-labels.md.txt)
    (Sections: "Automatically applied labels")
  - [Deploy TPU workloads in GKE Standard](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
    (Sections: "Ensure reservation availability", "Monitor health metrics for
    TPU nodes and node pools", "Configure auto repair for TPU slice nodes")
  - [Add and manage node pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-pools.md.txt)
    (Sections: "Delete a node pool")

> **Read-only rule**: Run read-only diagnostic commands only. Never drain,
> delete, or re-create nodes, or run any other command that changes the cluster.
> Give the user any fix to apply themselves.

When you recommend a fix, link the doc section that describes it.

---

## Diagnostic workflow

### Step 0: Collect context and set the investigation window

Identify the following environment parameters from the user prompt or cluster
context:
- `{project_id}`, `{cluster_name}`, `{location}`, `{failing_nodepool_name}`,
  `{reservation_name}`, `{reservation_project_id}` (the project that owns
  `{reservation_name}`, which defaults to `{project_id}` for a single-project
  reservation or is the owner project for a shared reservation; see
  [Consuming a specific shared reservation](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/consuming-reservations.md.txt)),
  `{zone}` (the zone of the reservation), and `{issue_time}`.
- **Time window**: By default, query a 60-minute window `[{issue_time} - 30m,
  {issue_time} + 30m]` (`{start_time}` to `{end_time}`).

---

### Step 1: Confirm the stockout error and reservation utilization `[Low Risk]`

1. **Check Cloud Audit Logs and GKE operations for stockout or capacity
   errors**:
   - Run the query in
     [Issue: Node pool creation fails due to insufficient resources](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-pools.md.txt)
     across `[{start_time}, {end_time}]` and look for the documented error
     messages, such as `ZONE_RESOURCE_POOL_EXHAUSTED` or
     `ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS`.
   - Per
     [Insufficient TPU resources to satisfy the TPU request](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/tpus.md.txt),
     TPU errors can also contain `GCE_STOCKOUT`.
   - Per
     [How GKE handles capacity issues](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/tpus.md.txt),
     GKE sometimes queues a TPU node pool creation request instead of failing it
     immediately, surfacing a `... cannot be created ... due to lack of
     capacity` message instead of `GCE_STOCKOUT`. List the node pool creation
     operation with `gcloud container operations list`
     (`--filter="operationType=CREATE_NODE_POOL"`).
2. **Check Compute Engine reservation utilization**:
   - Per
     [Limitations](https://docs.cloud.google.com/compute/docs/instances/reservations-view.md.txt),
     query shared reservations in the owner project where they were created
     (`{reservation_project_id}`).
   - Follow the
     [View reservations](https://docs.cloud.google.com/compute/docs/instances/reservations-view.md.txt)
     section to run `gcloud compute reservations describe {reservation_name}
     --zone={zone} --project={reservation_project_id}` or `gcloud compute
     reservations list --project={reservation_project_id}`, and compare
     `specificReservation.inUseCount` against `specificReservation.count`.

#### Interpret the results
- Don't suggest deleting any node pool until you have identified which existing
  VMs and node pools hold the reserved capacity (Step 2).
- **Stockout confirmed**: Per
  [Ensure reservation availability](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
  and
  [Multi-host TPU slice node pools](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/tpus.md.txt),
  if GKE can't deploy one node in a multi-host slice, no nodes in the slice are
  deployed. If a stockout or lack-of-capacity message is present and the free
  slots (`specificReservation.count - specificReservation.inUseCount`; see
  [Consumable instances in a reservation](https://docs.cloud.google.com/compute/docs/instances/reservations-view.md.txt))
  are fewer than the number of nodes that `{failing_nodepool_name}` needs,
  proceed to Step 2.

---

### Step 2: Find which VMs and node pools hold the reservation `[Low Risk]`

1. **Check which VMs are consuming `{reservation_name}`**:
   - Per
     [How reservations work](https://docs.cloud.google.com/compute/docs/instances/reservations-overview.md.txt),
     a specifically targeted reservation can be used by VMs in the reservation's
     project (`{reservation_project_id}`) or, for a shared reservation, in any
     project that it's shared with.
   - Follow
     [View a list of instances that are consuming a reservation](https://docs.cloud.google.com/compute/docs/instances/reservations-view.md.txt)
     to list the VMs that use `{reservation_name}`.
   - Follow
     [View labels](https://docs.cloud.google.com/compute/docs/labeling-resources.md.txt)
     to view each VM's labels, and check the GKE labels in
     [Automatically applied labels](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/creating-managing-labels.md.txt)
     (`goog-k8s-cluster-name`, `goog-k8s-cluster-location`, and
     `goog-k8s-node-pool-name`) to find each VM's cluster and node pool.
2. **Check node readiness in `{cluster_name}`**:
   - Query the Cloud Monitoring metric `kubernetes_io:node_status_condition`
     (`monitored_resource="k8s_node"`, `cluster_name="{cluster_name}"`,
     `condition="Ready"`) described in
     [Monitor health metrics for TPU nodes and node pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt).
     The `status` label value is `True`, `False`, or `Unknown` (capitalized);
     use `status!="True"` to find nodes whose `Ready` condition is `False` or
     `Unknown`.
   - List TPU nodes and their node pool labels with `kubectl get nodes -l
     cloud.google.com/gke-tpu-accelerator -L cloud.google.com/gke-nodepool`.
3. **Exclude the failing node pool**: Leave `{failing_nodepool_name}` out of the
   unhealthy list. It's the node pool that couldn't get capacity, not the one
   holding it.

#### Interpret the results
- **VMs outside `{cluster_name}`**: If some of the VMs consuming
  `{reservation_name}` aren't nodes in `{cluster_name}` (for example, their
  labels show another GKE cluster or they are Compute Engine VMs without GKE
  labels), tell the user which VMs or other clusters hold those reservation
  slots.
- **Fewer listed VMs than `specificReservation.inUseCount`**: Per
  [View a list of instances that are consuming a reservation](https://docs.cloud.google.com/compute/docs/instances/reservations-view.md.txt),
  the list only includes VMs in one project. If the list has fewer VMs than
  `specificReservation.inUseCount`, tell the user that VMs in other projects
  that share `{reservation_name}` are consuming the remaining slots.
- **Unhealthy node pool in `{cluster_name}` (`{leaking_nodepool_name}`)**: If
  the VMs consuming `{reservation_name}` belong to an existing `NotReady` node
  pool in `{cluster_name}` (`{leaking_nodepool_name}`), proceed to Step 3.
- **Healthy reservation (rule out a leak)**: If the reserved slots are held by
  healthy, in-use node pools, there is no leak. Explain that healthy node pools
  hold the reserved slots, so too few slots are free for
  `{failing_nodepool_name}`. Tell the user not to delete or restart those
  healthy node pools, and point the user to the consumption options in
  [Insufficient TPU resources to satisfy the TPU request](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/tpus.md.txt).

---

### Step 3: Verify node auto-repair on the leaking node pool `[Low Risk]`

Follow
[Verify node auto-repair is enabled for a Standard node pool](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
for `{leaking_nodepool_name}`:
- **Auto-repair is disabled (`management.autoRepair: false`)**: GKE doesn't
  repair the `NotReady` nodes in `{leaking_nodepool_name}`. Their running VMs
  keep counting toward `specificReservation.inUseCount` in `{reservation_name}`,
  so `{failing_nodepool_name}` can't get reserved capacity. Proceed to Step 4.
- **Auto-repair is enabled (`management.autoRepair: true`), but nodes are still
  `NotReady`**:
  - Check whether the nodes have been `NotReady` for less than the repair time
    threshold (approximately 10 minutes), per
    [Repair criteria](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt).
  - Check the repair status and the failure reason in the operation history, per
    [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
    and
    [Get information about recent automated repair events](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt).

---

### Step 4: Recover or release the reservation capacity `[High Risk]`

> **Guardrail**: Don't modify GKE-managed instance groups or VMs directly
> through Compute Engine. Manage node pools through GKE. Never delete healthy
> node pools that run active jobs.

1. **If the user wants to repair `{leaking_nodepool_name}` in place, enable node
   auto-repair (doesn't free slots for `{failing_nodepool_name}`)**:
   - Per
     [Node repair process](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt),
     GKE drains and re-creates an unhealthy node.
   - Per
     [Node auto repair in TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
     and
     [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt),
     GKE re-creates the entire multi-host TPU slice node pool when a node in it
     needs repair. The re-created VMs still consume `{reservation_name}`, so
     `specificReservation.inUseCount` doesn't drop: enabling auto-repair makes
     the nodes in `{leaking_nodepool_name}` healthy again, **but
     `{failing_nodepool_name}` still can't get their slots**.
   - Link the user to
     [Enable auto-repair for an existing Standard node pool](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
     to turn on auto-repair for `{leaking_nodepool_name}`.
2. **If `{leaking_nodepool_name}` is stale or the user wants to free its slots
   for `{failing_nodepool_name}`, delete it through GKE**: Link the user to
   [Delete a node pool](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-pools.md.txt)
   to delete `{leaking_nodepool_name}`, which frees the reserved slots its VMs
   use.
3. **After the user deletes `{leaking_nodepool_name}`, verify that enough
   reservation slots are free**: Follow
   [View reservations](https://docs.cloud.google.com/compute/docs/instances/reservations-view.md.txt)
   to run `gcloud compute reservations describe {reservation_name} --zone={zone}
   --project={reservation_project_id}` and confirm that
   `specificReservation.inUseCount` drops so the free slots
   (`specificReservation.count - specificReservation.inUseCount`) are at least
   the number of nodes that `{failing_nodepool_name}` needs before retrying
   `{failing_nodepool_name}`.
