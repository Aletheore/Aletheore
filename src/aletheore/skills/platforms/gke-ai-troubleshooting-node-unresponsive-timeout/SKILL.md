---
name: gke-ai-troubleshooting-node-unresponsive-timeout
description: Diagnose and mitigate GKE TPU or GPU nodes stuck in NotReady / NodeStatusUnknown ("Kubelet stopped posting node status") due to host kernel panics, hardware lockups, or disabled node auto-repair. Use when nodes stop heartbeating beyond the node auto-repair threshold and pods remain stuck in Terminating. Don't use for healthy nodes, pod-only application crashes, or routine GKE upgrades.
metadata:
  version: 1.0.0
  category: Containers
skill_id: platforms/gke-ai-troubleshooting-node-unresponsive-timeout
title: GKE AI Troubleshooting Node Unresponsive Timeout
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-node-unresponsive-timeout
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-ai-troubleshooting-node-unresponsive-timeout", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-ai-troubleshooting-node-unresponsive-timeout", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "GKE AI/TPU troubleshooting (niche)", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Troubleshoot unresponsive GKE TPU and GPU nodes (`NodeStatusUnknown`)

When the Compute Engine host of a TPU or GPU node has a fatal hardware error,
kernel panic, or non-maskable interrupt (NMI) lockup, the guest OS stops
responding. The kubelet can no longer send heartbeats, so the node `Ready`
condition becomes `Unknown` with `Reason: NodeStatusUnknown` (`Kubelet stopped
posting node status.`). If node auto-repair is disabled on the node pool, GKE
doesn't repair the node. The node can stay `NotReady`, and pods on it can stay
in `Terminating`, which blocks multi-host `JobSet` workloads from recovering.

## Prerequisites

- **Tools**: Install the
  [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud`) and
  `kubectl`.
- **Cloud Billing & Project Configuration**: Verify an active billing account is
  linked (`gcloud billing projects describe {project_id}`), authenticate
  (`gcloud auth login`), set the target project (`gcloud config set project
  {project_id}`), and ensure `container.googleapis.com`,
  `compute.googleapis.com`, `logging.googleapis.com`, and
  `monitoring.googleapis.com` are enabled.
- **Required IAM Roles**:
  - Kubernetes Engine Viewer (`roles/container.viewer`)
  - Compute Viewer (`roles/compute.viewer`)
  - Logs Viewer (`roles/logging.viewer`)
  - Monitoring Viewer (`roles/monitoring.viewer`)
  - For remediation (`[High Risk]` steps): Kubernetes Engine Cluster Admin
    (`roles/container.clusterAdmin`)
- **Documentation**:
  - [Troubleshoot nodes with the NotReady status in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-notready.md.txt)
    (Sections: "Check the node's status and conditions", "Confirm node
    preemption", "Verify that the node has recovered")
  - [Deploy TPU workloads in GKE Standard](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
    (Sections: "Monitor health metrics for TPU nodes and node pools", "Configure
    auto repair for TPU slice nodes")
  - [Troubleshoot OOM events](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/oom-events.md.txt)
  - [View GKE logs](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/view-logs.md.txt)
    (Sections: "System logs")
  - [Viewing serial port output](https://docs.cloud.google.com/compute/docs/troubleshooting/viewing-serial-port-output.md.txt)
  - [Troubleshoot Linux VM boot issues due to kernel panic](https://docs.cloud.google.com/compute/docs/troubleshooting/kernel-panic.md.txt)
  - [Auto-repair nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
    (Sections: "Settings for Autopilot and Standard", "Verify node auto-repair
    is enabled for a Standard node pool", "Get information about recent
    automated repair events", "Enable auto-repair for an existing Standard node
    pool", "Repair criteria", "Node repair process", "Node auto repair in TPU
    slice nodes")
  - [Troubleshooting VM shutdowns and reboots](https://docs.cloud.google.com/compute/docs/troubleshooting/troubleshooting-reboots.md.txt)
    (Sections: "Querying Cloud Audit Logs", "Reviewing Cloud Audit Logs")

> **Read-only rule**: Run read-only diagnostic commands only. Never drain,
> delete, or re-create nodes, or run any other command that changes the cluster.
> Give the user any fix to apply themselves.

When you recommend a fix, link the doc section that describes it.

---

## Diagnostic workflow

### Step 0: Collect context and set the investigation window `[Low Risk]`

Collect the target parameters. By default, query a 60-minute window `[T - 30m, T
+ 30m]` around `{issue_time}`:

- `{project_id}`: Google Cloud project ID
- `{cluster_name}`: GKE cluster name
- `{location}`: Cluster region or zone
- `{nodepool_name}`: Target TPU or GPU node pool name
- `{node_name}`: Unresponsive GKE node name (and its Compute Engine `{zone}`)
- `{issue_time}`: Incident timestamp in RFC3339 UTC
- `{start_time}`: `{issue_time} - 30m`
- `{end_time}`: `{issue_time} + 30m`

---

### Step 1: Verify the `NodeStatusUnknown` heartbeat timeout `[Low Risk]`

1. **Check Kubernetes node conditions**: To inspect the node status and verify
   whether the `Ready` condition is `Unknown` with `Reason: NodeStatusUnknown`
   (`Kubelet stopped posting node status.`), follow the instructions in the
   section
   [Check the node's status and conditions](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-notready.md.txt).
2. **Query Cloud Logging (read-only LQL)**: Query `k8s_node` and `k8s_cluster`
   logs across `[{start_time}, {end_time}]` to confirm when the control plane
   lost heartbeat contact with `{node_name}`:

```
(resource.type="k8s_node" OR resource.type="k8s_cluster")
resource.labels.cluster_name="{cluster_name}"
("{node_name}" AND ("NodeNotReady" OR "NodeStatusUnknown" OR "Kubelet stopped posting node status"))
timestamp >= "{start_time}" AND timestamp <= "{end_time}"
```

3. **Query Cloud Monitoring (read-only PromQL)**: Correlate the duration of the
   `Unknown` state using the GKE system metric
   `kubernetes.io/node/status_condition` (`kubernetes_io:node_status_condition`,
   GKE `1.32.1-gke.1357001+`) documented in
   [Monitor health metrics for TPU nodes and node pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt),
   filtered by `condition="Ready"` and `status="Unknown"`:

```promql
kubernetes_io:node_status_condition{
  monitored_resource="k8s_node",
  cluster_name="{cluster_name}",
  node_name="{node_name}",
  condition="Ready",
  status="Unknown"
}
```

- **Decision logic**:
  - If the node `Ready` condition is `True` and `NodeStatusUnknown` is absent,
    **rule out** an unresponsive node timeout and pivot to workload-level
    troubleshooting (for example,
    [Troubleshoot OOM events](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/oom-events.md.txt))
    rather than repairing or draining the node.
  - If `Ready` is `Unknown` (`NodeStatusUnknown`), proceed to Step 2.

---

### Step 2: Inspect serial port output for a kernel panic `[Low Risk]`

The guest OS can't send logs after a fatal kernel freeze, so check the serial
port output of the node's VM:

- **Cloud Logging**: If serial port logging is enabled (i.e. VM metadata
  `serial-port-logging-enable` is set to `true`), GKE system logs in Cloud
  Logging include the node's serial port output. See the section
  [System logs](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/view-logs.md.txt).
  Use Cloud Logging when the VM is stopped or has already been replaced by
  auto-repair, or when you need more than the most recent output.
- **Running VM**: Follow
  [Viewing serial port output](https://docs.cloud.google.com/compute/docs/troubleshooting/viewing-serial-port-output.md.txt)
  to retrieve the serial port 1 output (`gcloud compute instances
  get-serial-port-output` with `--port=1`) for `{node_name}` in `{zone}`. This
  method returns only the most recent 1 MB of output per port.

Consult
[Troubleshoot Linux VM boot issues due to kernel panic](https://docs.cloud.google.com/compute/docs/troubleshooting/kernel-panic.md.txt)
to identify documented kernel panic and hardware crash patterns (such as `Fatal
Machine check`, `hung_task: blocked tasks`, or `NMI: Not continuing`) in the
serial port output.

---

### Step 3: Check node auto-repair status `[Low Risk]`

Find out why GKE hasn't repaired the unresponsive node:

- **Autopilot clusters**: Autopilot always repairs nodes, and you can't turn
  this off (see
  [Settings for Autopilot and Standard](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)).
  Skip the configuration check and check the repair history.
- **Standard clusters**: Follow
  [Verify node auto-repair is enabled for a Standard node pool](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
  to check whether `autoRepair` is enabled on `{nodepool_name}`.
- **Repair history (both modes)**: Auto-repair can be enabled and the repair can
  still fail. Per
  [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt),
  check the repair status, including the failure reason, in the operation
  history described in
  [Get information about recent automated repair events](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt).
  If the failure is caused by insufficient quota, tell the user to contact their
  Google Cloud account representative to increase the quota.

---

### Step 4: Check Compute Engine system events `[Low Risk]`

To list the system events for `{node_name}` around `{issue_time}`, follow the
instructions in the section
[Querying Cloud Audit Logs](https://docs.cloud.google.com/compute/docs/troubleshooting/troubleshooting-reboots.md.txt).
Compare the `method` field with the table in the "Reviewing Cloud Audit Logs"
section of the same document, and look for:

- `compute.instances.hostError`: a hardware or software issue on the physical
  host caused the VM to crash.
- `compute.instances.preempted`: Compute Engine preempted a Spot VM or
  preemptible VM. For preempted nodes, also see
  [Confirm node preemption](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-notready.md.txt).
- `compute.instances.automaticRestart`: Compute Engine restarted the VM after a
  `hostError` or `terminateOnHostMaintenance` event.
- `compute.instances.guestTerminate`: the VM's operating system initiated the
  shutdown.

---

### Step 5: Resolution `[High Risk]`

> **Guardrails**:
> - **Never** force-delete stuck `Terminating` pods on an unresponsive node.
>   Force deletion doesn't wait for the kubelet to confirm that the pod has
>   stopped, so a replacement pod can start while the old one is still running.
>   See
>   [Force Delete StatefulSet Pods](https://kubernetes.io/docs/tasks/run-application/force-delete-stateful-set-pod/#force-deletion).
> - **Never** delete GKE-managed Compute Engine VM instances directly (`gcloud
>   compute instances delete`). Instead, check how long the node has reported
>   `NodeStatusUnknown` (Step 1), check the serial console output (Step 2), and
>   rely on node auto-repair, as described in the following steps.

1. **Enable node auto-repair (Standard clusters only)**:
   - Autopilot clusters always auto-repair nodes, so skip this step.
   - Before the user enables auto-repair on a multi-host TPU slice node pool,
     tell them that GKE re-creates the entire node pool when a node in it needs
     repair (see step 2).
   - If `autoRepair` is disabled on `{nodepool_name}`, link the user to
     [Enable auto-repair for an existing Standard node pool](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
     and
     [Configure auto repair for TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/tpus.md.txt)
     so they can apply the change themselves.
2. **Let GKE repair the node**:
   - Explain that GKE repairs a node that reports `NotReady` or no status for
     the documented time threshold by draining and re-creating it, and link the
     "Repair criteria" and "Node repair process" sections of
     [Auto-repair nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt).
     GKE waits one hour for the drain to complete. If the drain doesn't
     complete, GKE shuts the node down and creates a new node. Tell the user to
     expect this one-hour drain wait before GKE re-creates the node.
   - For multi-host TPU slice node pools, note per
     [Node auto repair in TPU slice nodes](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair.md.txt)
     that the entire node pool is re-created.
3. **Verify node recovery**:
   - After the repair completes, follow the instructions in the section
     [Verify that the node has recovered](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/node-notready.md.txt)
     to verify that the repaired node returns to `Ready` status, then confirm
     that the `JobSet` pods are running again.
