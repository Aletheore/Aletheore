---
name: gke-ai-troubleshooting-tpu-partition-overlap
description: Diagnose and resolve GKE TPU slice creation failures (SliceCreationFailed) caused by partition overlap conflicts where requested TPU partitions or their nodes are already used by another slice or JobSet. Use when diagnosing GKE TPU slice creation failures with SliceCreationFailed partition or node conflicts. Don't use for general slice lifecycle states or slice provisioning failures without node overlap (use gke-ai-troubleshooting-tpu-dynamic-slices-monitoring), quota exhaustion, or unresponsive TPU nodes.
metadata:
  version: 1.0.0
  category: Containers
skill_id: platforms/gke-ai-troubleshooting-tpu-partition-overlap
title: Gke Ai Troubleshooting Tpu Partition Overlap
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-partition-overlap
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-ai-troubleshooting-tpu-partition-overlap", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-tpu-partition-overlap", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "GKE AI/TPU troubleshooting (niche)", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Troubleshoot TPU slice creation failures caused by partition overlap

A dynamic slice fails to form with the status reason `SliceCreationFailed` when,
among other documented causes, any of its nodes are already used by another
slice. This skill confirms the failure, rules out the other documented causes,
finds the slice that uses the same nodes, and routes the fix through GKE.

## Prerequisites

- **Tools**:
  - For installing and initializing `gcloud` (which covers authentication and
    project selection), follow
    [Before you begin](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt).
  - For installing `kubectl` and required plugins, follow
    [Install kubectl](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt)
    and
    [Install required plugins](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt).
- **Cloud Billing & Project Configuration**: Ensure the project has an active
  Cloud Billing account. Follow
  [Before you begin](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
  to initialize `gcloud` and enable the Google Kubernetes Engine API
  (`container.googleapis.com`), ensure the Cloud Monitoring API
  (`monitoring.googleapis.com`) is enabled, and follow
  [Store cluster information for kubectl](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt)
  to configure `kubectl` cluster credentials.
- **Required IAM Roles**:
  - Kubernetes Engine Viewer (`roles/container.viewer`)
  - Monitoring Viewer (`roles/monitoring.viewer`)
- **Documentation**:
  - [Use dynamic slicing with a custom scheduler](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
    (Sections: "Before you begin", "Target your node pool to belong to a block
    or a sub-block", "Verify the status of the nodes and the partitions",
    "Create a Slice custom resource", "Monitor the status of the Slice custom
    resource", "Delete the slice")
  - [Configure cluster access for kubectl](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl.md.txt)
    (Sections: "Install kubectl", "Install required plugins", "Store cluster
    information for kubectl")
  - [Slice](https://docs.cloud.google.com/kubernetes-engine/docs/reference/crds/slice.md.txt)
    (Sections: "Slice status", "Slice specification")
  - [Schedule dynamic slices with Kueue and TAS](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt)
    (Sections: "Target your node pool to belong to a block or a sub-block",
    "Create a dynamic slice with Kueue and TAS", "Define the partition health
    selection", "Optimize workload scheduling with slice reuse", "Enable Slice
    reuse", "Enforce slice deletion per workload", "Monitor the status of the
    slice", "Monitor the metrics of the slice", "Clean up")
  - [GKE system metrics](https://docs.cloud.google.com/monitoring/api/metrics_kubernetes.md.txt)
    (Sections: "Kubernetes metrics")
  - [Monitored resource types](https://docs.cloud.google.com/monitoring/api/resources.md.txt)
    (Sections: "k8s_node_pool")
  - [About GKE dynamic slicing](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dynamic-slicing.md.txt)
    (Sections: "Dynamic slicing configurations")
  - [Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
    (Sections: "Before you contact support", "Recommendations for efficient
    support")

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
- `{slice_name}`: Name of the Slice custom resource that failed to form
- `{node_pool_name}`: Name of the TPU node pool (or node pools) backing the
  partitions in `{slice_name}`, if known. If the user doesn't know
  `{node_pool_name}`, after Step 1 run the Cloud Monitoring query in Step 3
  for the partition IDs in `{partition_ids}` and read the `node_pool_name`
  label on the
  [k8s_node_pool](https://docs.cloud.google.com/monitoring/api/resources.md.txt)
  monitored resource.
- `{issue_time}`: Incident timestamp in RFC3339 UTC, if known. If the user
  doesn't know `{issue_time}`, use the `Last Transition Time`
  (`lastTransitionTime`, "A timestamp of the most recent status change" in
  [Slice status](https://docs.cloud.google.com/kubernetes-engine/docs/reference/crds/slice.md.txt))
  of the `Ready` condition from Step 1. By default, query Cloud Monitoring over
  a 60-minute window around `{issue_time}`.

---

### Step 1: Confirm the `SliceCreationFailed` status `[Low Risk]`

To describe `{slice_name}`, follow the instructions in the section
[Monitor the status of the Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt).

- In `Status` > `Conditions`, check that the `Reason` of the `Ready` condition
  is `SliceCreationFailed`, and record its `Last Transition Time` (to use as
  `{issue_time}` if needed).
- Record the IDs in `Spec` > `Partition Ids` as `{partition_ids}`, the
  `Topology`, and any `Labels` and `Annotations` on `{slice_name}`.
- If the `Ready` reason isn't `SliceCreationFailed`, this skill doesn't apply.
  See
  [Slice status](https://docs.cloud.google.com/kubernetes-engine/docs/reference/crds/slice.md.txt)
  for what each reason means, and hand off to
  `gke-ai-troubleshooting-tpu-dynamic-slices-monitoring`.

---

### Step 2: Rule out the other documented causes `[Low Risk]`

Per
[Create a Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt),
slice creation also fails with `SliceCreationFailed` when:

- The selected nodes don't exist.
- The topology doesn't match the number of partitions. A dynamic sub-slice lists
  exactly one partition ID. A dynamic super-slice lists `(A × B × C) / 64`
  partition IDs.

If either of those causes applies, hand off to
`gke-ai-troubleshooting-tpu-dynamic-slices-monitoring`.

Otherwise, follow
[Verify the status of the nodes and the partitions](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
on `{node_pool_name}`:

- Verify that the nodes exist and carry each ID in `{partition_ids}`.
- On the nodes that carry `{partition_ids}`, record **every** partition ID of
  **every** shape (`4x4x4`, `2x4x4`, `2x2x4`, `2x2x2`, and `2x2x1`) from the
  `cloud.google.com/gke-tpu-partition-.*-id` labels. Per
  [Dynamic slicing configurations](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dynamic-slicing.md.txt),
  dynamic sub-slicing partitions a `4x4x4` sub-block into smaller topologies on
  the same physical node pool, so partitions of different shapes share the same
  nodes.
- Read each partition's `cloud.google.com/gke-tpu-partition-.*-state` label. Per
  [Verify the status of the nodes and the partitions](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
  and
  [Define the partition health selection](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt),
  count `HEALTHY` partitions and `DEGRADED` `4x4x4` partitions ("still usable
  for dynamic slice formation"; "Smaller topologies don't have a degraded
  state") as available. Partitions in `UNHEALTHY` ("can't be used to form a
  slice"), `UNSET`, or `INCOMPLETE` aren't available. If a requested partition
  is in `UNHEALTHY`, `UNSET`, or `INCOMPLETE`, suggest an available partition
  whose nodes no other slice uses (Step 4, item 2), hand off to
  `gke-ai-troubleshooting-tpu-dynamic-slices-monitoring`, or escalate to Cloud
  Customer Care (Step 4, item 4).

---

### Step 3: Find the slice that uses the same nodes `[Low Risk]`

Per
[Create a Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt),
slice creation fails with `SliceCreationFailed` when any nodes on the custom
resource are used by another slice. Because partitions of different shapes on
the same sub-block share nodes (see
[Dynamic slicing configurations](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dynamic-slicing.md.txt)),
another slice conflicts with `{slice_name}` if it lists **any** of the partition
IDs (of any shape) recorded on `{slice_name}`'s nodes in Step 2.

1. List the `Slice` custom resources in the cluster (as shown in
   [Delete the slice](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt))
   and describe each one following
   [Monitor the status of the Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
   to read `Spec` > `Partition Ids`, `Metadata` > `Labels`, and `Status` >
   `Conditions` (`Ready` reason):
   - Treat any slice other than `{slice_name}` whose `Spec` > `Partition Ids`
     includes **any** partition ID recorded on `{slice_name}`'s nodes in Step 2
     (whether the exact ID in `{partition_ids}` or another shape's partition ID
     on the same nodes) as conflicting. Record its name, its `Ready` reason
     (such as `ACTIVE`), and whether Kueue manages it (Step 4, item 1).
2. To check partition-to-slice associations in Cloud Monitoring (including
   around `{issue_time}`), query the `kubernetes.io/accelerator/partition/state`
   metric listed in
   [Monitor the metrics of the slice](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt).
   Per
   [Kubernetes metrics](https://docs.cloud.google.com/monitoring/api/metrics_kubernetes.md.txt),
   this metric "is 1 if the partition state is detected, 0 otherwise", and its
   labels include `partition_id`, `slice_id` ("ID of the associated slice"),
   and `state` ("Health of the partition (e.g., HEALTHY, UNHEALTHY)"). Run the
   query for each `{partition_id}` recorded on `{slice_name}`'s nodes in Step 2:

```promql
kubernetes_io:accelerator_partition_state{
  monitored_resource="k8s_node_pool",
  cluster_name="{cluster_name}",
  partition_id="{partition_id}"
}
```

   - Read only time series whose value is `1` (where `state="HEALTHY"` means the
     partition is healthy and functional, per
     [Verify the status of the nodes and the partitions](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)).
   - Because
     [Kubernetes metrics](https://docs.cloud.google.com/monitoring/api/metrics_kubernetes.md.txt)
     defines `slice_id` only as "ID of the associated slice", always confirm the
     conflicting slice from the `Slice` custom resources in the cluster rather
     than from `slice_id` alone: list the `Slice` custom resources as shown in
     [Delete the slice](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt),
     describe each one following
     [Monitor the status of the Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt),
     and check which slice's `Spec` > `Partition Ids` includes the partition ID
     (or any other partition ID of any shape on the same nodes from Step 2).
3. **Next steps based on Step 3 results**:
   - **Another slice uses the nodes now**: Proceed to Step 4.
   - **No other slice uses the nodes now, but the metric (value `1`) showed an
     associated `slice_id` around `{issue_time}`**: The conflict may already be
     over. Proceed to Step 4, item 1 (to re-create or retry `{slice_name}`) and
     then Step 4, item 4.
   - **Neither the current `Slice` custom resources nor the metric around
     `{issue_time}` shows another slice**: Node overlap isn't confirmed. Hand
     off to `gke-ai-troubleshooting-tpu-dynamic-slices-monitoring` or escalate
     to Cloud Customer Care (Step 4, item 4).

---

### Step 4: Resolution `[High Risk]`

> **Guardrails**:
> - **Never** delete or change the other slice, or stop its workload, to free
>   the nodes. Deleting a `Slice` custom resource starts dismantling that slice
>   (`DEACTIVATING`, see
>   [Monitor the status of the Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)).
>   The owner of that slice decides when to release it.
> - **Never** manually modify or delete `Slice` custom resources created by
>   Kueue. Per
>   [Target your node pool to belong to a block or a sub-block](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt),
>   "Avoid manually modifying Slice custom resources created by Kueue."
> - **Never** change GKE-managed instance groups or VMs directly through Compute
>   Engine, and never delete node pools to free a partition. Manage node pools
>   and slices through GKE.

First, check whether Kueue manages `{slice_name}` or the conflicting slice:

- Per
  [Monitor the status of the slice](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt),
  when a slice is created or actively used by a workload, the slice controller
  attaches the `kueue.gke.io/tpu-slice-used-by-namespace` and
  `kueue.gke.io/tpu-slice-used-by-workload` labels.
- Per
  [Optimize workload scheduling with slice reuse](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt)
  and
  [Enable Slice reuse](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt),
  when slice reuse is enabled, "Kueue marks the slice as idle at the end of a
  job instead of deleting it", and "Reusable slices are named dynamically with
  an `s-<topology>` prefix, decoupling them from specific workload names."

Then apply the matching option:

1. **Wait for the conflicting slice to release the nodes and retry**:
   - **If Kueue manages the conflicting slice** (it has the `kueue.gke.io/...`
     labels or an `s-<topology>` name prefix): Do not suggest deleting the
     `Slice` custom resource directly. Point the workload owner to
     [Clean up](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt)
     (deleting the `JobSet` triggers Kueue to delete the associated `Slice`
     custom resources) and, if slice reuse keeps an idle `s-<topology>` slice
     allocated after a job ends, to
     [Enforce slice deletion per workload](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt)
     (`kueue.gke.io/tpu-enforce-slice-deletion: "true"`).
   - **If a custom scheduler or user created the conflicting slice directly**:
     Its owner can delete it when finished, as described in
     [Delete the slice](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt).
   - **How `{slice_name}` retries**: Check whether `{slice_name}` already has
     `slice.gke.io/retry-on-failure: "true"` in `Metadata` > `Annotations`. Per
     [Create a Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt),
     when a `Slice` custom resource is created with this annotation, "If the
     slice isn't created because of the `SliceCreationFailed` status reason, the
     controller will retry until the slice is successfully formed." Because the
     documentation only shows configuring
     `slice.gke.io/retry-on-failure: "true"` in the `Slice` manifest when
     creating a `Slice` custom resource (and doesn't state that adding the
     annotation to an existing slice already in `SliceCreationFailed` starts a
     retry), if `{slice_name}` wasn't created by Kueue and lacks the annotation,
     tell the user to delete `{slice_name}` following
     [Delete the slice](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
     and re-create it with `slice.gke.io/retry-on-failure: "true"` in its
     manifest following
     [Create a Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt).
2. **Use available partitions whose nodes no other slice uses**: If the user
   manages `Slice` custom resources directly (not through Kueue) and can't wait,
   direct them to
   [Verify the status of the nodes and the partitions](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
   and
   [Create a Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt)
   so they can pick available partitions (`HEALTHY` partitions, or `DEGRADED`
   `4x4x4` partitions) whose nodes aren't used by any other slice (across all
   partition shapes on those nodes) and create the `Slice` custom resource with
   them.
3. **Schedule dynamic slices with Kueue and TAS instead of creating `Slice`
   custom resources directly**: Per
   [Target your node pool to belong to a block or a sub-block](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt)
   and
   [Create a dynamic slice with Kueue and TAS](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/use-gke-dynamic-slicing.md.txt),
   instead of creating `Slice` custom resources directly, teams can schedule
   GKE workloads with Kueue and Topology Aware Scheduling (TAS), where "Kueue
   automatically handles the creation and deletion of Slice custom resources"
   and admits a workload after its slice becomes active.
4. **Verify or escalate**: Repeat Step 1 following
   [Monitor the status of the Slice custom resource](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/create-dynamic-slices.md.txt):
   - **`ACTIVE`**: The slice is fully formed, healthy, and ready to execute
     workloads.
   - **`ACTIVE_DEGRADED`**: The slice is formed, and workloads can run, but
     performance might be impacted.
   - **Still `SliceCreationFailed` after no other slice uses the nodes, or
     `FAILED`**: Hand off to
     `gke-ai-troubleshooting-tpu-dynamic-slices-monitoring`, or advise the user
     to contact Cloud Customer Care following
     [Before you contact support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
     and
     [Recommendations for efficient support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt).
