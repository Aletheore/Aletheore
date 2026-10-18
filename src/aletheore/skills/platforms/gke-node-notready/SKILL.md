---
name: gke-node-notready
metadata:
  version: 1.0.0
  category: Containers
description: Diagnoses GKE nodes reporting NotReady or Unknown status by inspecting node conditions, events, kubelet/containerd logs, and node metrics, then proposing safe remediations. Use when nodes show NotReady, when the kubelet stops posting node status, or when workloads are evicted or stuck Pending due to node health. Don't use for pod-level application failures (use gke-workload-troubleshooting), autoscaler scale-up/scale-down decisions (use gke-cluster-autoscaler), or non-GKE compute.
skill_id: platforms/gke-node-notready
title: Gke Node Notready
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-node-notready
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-node-notready", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-node-notready", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["slash"], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Node NotReady Troubleshooting Skill

Use this skill to systematically diagnose why one or more GKE nodes report a
`NotReady` (or `Ready: Unknown`) status and to propose safe remediations. A
`NotReady` status means the node's kubelet is not reporting to the control plane
correctly, so Kubernetes stops scheduling new Pods on the node, which can reduce
application capacity and cause downtime.

This skill operates **non-interactively** and enforces a **read-only diagnostics
boundary**: gather evidence first, then propose a fix (a `kubectl`/`gcloud`
command or a GitOps manifest change) for a human to apply. **Never** mutate the
cluster, drain, delete, or recreate nodes automatically.

> [!IMPORTANT]
> First rule out an **expected** `NotReady`: a node that is newly provisioning,
> upgrading, being repaired, cordoned, or scaling down will transiently report
> `NotReady`. Only treat it as a fault if it persists beyond the expected window.

## 🔍 Diagnostic Workflow

### Step 0: Context discovery & time window

1.  **Parameter extraction** — obtain `project_id`, `cluster_name`,
    `cluster_location`, and `node_name` non-interactively from the user prompt,
    active `SETTINGS.md`, or environment defaults (`kubectl config current-context`,
    `gcloud config get-value project`).
2.  **Credentials & fallback** — attempt
    `gcloud container clusters get-credentials {cluster_name} --location {cluster_location} --project {project_id}`.
    If the cluster is unreachable or commands fail (sandbox/dry-run/offline),
    present the exact diagnostic commands for a human to run and continue the
    analysis from the reported symptoms.
3.  **Time window** — determine `{issue_time}` (explicit, relative, or now) and
    center a 1-hour window around it (`start = issue_time - 30m`,
    `end = issue_time + 30m`) for all log/metric queries.

--------------------------------------------------------------------------------

### Step 1: Identify NotReady nodes and gather initial status

```bash
# List nodes and spot NotReady status, node IPs, and container-runtime version.
kubectl get nodes -o wide

# Inspect the affected node's Conditions and Events (the primary clues).
kubectl describe node "{node_name}"
```

Equivalent via Cloud Logging (preferred when kubectl access is limited or for
historical events). Open it as a **Logs Explorer deep link** — URL-encode the
query and append the project and Step 0 time window:
`https://console.cloud.google.com/logs/query;query={URL_ENCODED_QUERY};timeRange={start}%2F{end}?project={project_id}`
(encode `/` as `%2F`, or use `;duration=PT1H` for a rolling hour):

```
resource.type="k8s_node"
log_id("events")
resource.labels.node_name="{node_name}"
resource.labels.cluster_name="{cluster_name}"
resource.labels.location="{cluster_location}"
```

**Interpret the `Conditions` table:**

- `Ready: False` / `Ready: Unknown` with reason `KubeletNotReady` /
  `NodeStatusUnknown` ("Kubelet stopped posting node status") → kubelet or
  runtime problem; continue to Step 2.
- `MemoryPressure: True`, `DiskPressure: True`, `PIDPressure: True` → resource
  exhaustion; go to Step 4b.
- `NetworkUnavailable: True` → networking/CNI problem; go to Step 4d.

--------------------------------------------------------------------------------

### Step 2: Scan kubelet logs for error signatures

Open these kubelet logs as a **Logs Explorer deep link** using the same
`logs/query;query={URL_ENCODED_QUERY};timeRange=...?project=...` pattern as Step 1.

```
resource.type="k8s_node"
resource.labels.node_name="{node_name}"
resource.labels.cluster_name="{cluster_name}"
resource.labels.location="{cluster_location}"
log_id("kubelet")
severity>=WARNING
```

Also review the node's serial-console logs (`log_id("serialconsole.googleapis.com/serial_port_1_output")`
or the `resource.type="gce_instance"` serial logs) for kernel `TaskHung`,
OOM-killer, or disk I/O errors that correlate with the kubelet failures.

--------------------------------------------------------------------------------

### Step 3: Map the signature to a root cause (decision table)

| Kubelet / event signature | Likely root cause | Go to |
| --- | --- | --- |
| `runtime is down`, `Container runtime not ready`, errors on `/run/containerd/containerd.sock` (connection refused / DeadlineExceeded) | Container runtime (`containerd`) down or unresponsive | Step 4a |
| `Got sys oom event from cadvisor` / kernel OOM-killer in serial logs | System (node-level) OOM killed critical processes | Step 4b |
| `PLEG is not healthy` | PLEG stalled, usually node overload (CPU/disk) | Step 4c |
| `TaskHung` for `containerd`/`kubelet`, high disk latency | Disk throttling / I/O starvation | Step 4b |
| `failed to ensure lease`, `leases.coordination.k8s.io ... namespace kube-node-lease ... terminating` | `kube-node-lease` termination → NotReady flapping | Step 4f |
| Kubelet cannot reach API server, TLS/dial timeouts | Kubelet ↔ control-plane connectivity | Step 4d |
| `NetworkPluginNotReady`, `cni plugin not initialized`, `NetworkUnavailable` | CNI plugin failure | Step 4d |
| Node-critical DaemonSet Pods (CNI, kube-proxy, metadata) blocked from admission | Admission webhook interference | Step 4e |
| Only generic `NodeNotReady`, no other signature | Cause unclear — widen to Step 4d, then escalate | Escalation |

--------------------------------------------------------------------------------

### Step 4: Branch investigations

#### 4a. Container runtime (`containerd`) down

Confirm the kubelet cannot talk to containerd (socket errors above). Check for
`containerd` restarts/crashes in serial logs. **Remediation (propose, don't run):**
recreate/repair the node (`kubectl drain` then let the node pool recreate it, or
`gcloud container clusters upgrade`/node auto-repair); if it recurs across nodes,
suspect a node image or custom DaemonSet interfering with containerd.

#### 4b. Resource pressure & OOM

```bash
# Node allocatable vs. usage.
kubectl describe node "{node_name}" | sed -n '/Allocated resources/,/Events/p'
```
Cloud Monitoring metrics to inspect (read-only): `kubernetes.io/node/memory/used_bytes`,
`kubernetes.io/node/cpu/core_usage_time`, `kubernetes.io/node/ephemeral_storage/used_bytes`.
- **DiskPressure / disk throttling**: full boot disk or slow PD → increase disk
  size / use a faster PD type; reduce image/log churn.
- **System OOM**: node memory exhausted → set/raise Pod memory `requests`/`limits`,
  reduce over-commit, or use larger machine types. Distinguish **system OOM**
  (node-wide, kills kubelet/runtime) from **cgroup OOM** (single container).
- **PIDPressure**: too many processes → cap Pod PIDs / reduce workload density.

#### 4c. PLEG is not healthy

`PLEG is not healthy` almost always means the node is overloaded (CPU saturation,
disk latency, or too many Pods/containers per node) so the runtime can't relist
in time. Correlate with 4b metrics. **Remediation:** reduce node density, add
CPU/disk headroom, or spread workloads.

#### 4d. Networking

```bash
# Are node-critical networking Pods healthy on this node?
kubectl get pods -n kube-system -o wide --field-selector spec.nodeName={node_name}
```
- **Kubelet ↔ control-plane**: dial/TLS timeouts to the API server → check
  firewall rules, Private Google Access, authorized networks, and route/NAT
  changes.
- **CNI failure** (`NetworkPluginNotReady`): the CNI DaemonSet
  (`netd`/`calico`/dataplane) is not running on the node → inspect those Pods'
  logs/events.

#### 4e. Admission webhook interference

A misconfigured/failing validating or mutating webhook with a broad scope can
block node-critical system Pods from being admitted, keeping the node NotReady.

```bash
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations
```
Look for webhooks that intercept `kube-system` / node-critical objects with
`failurePolicy: Fail`. **Remediation (propose):** scope the webhook out of
`kube-system`/node-critical namespaces or set an appropriate `namespaceSelector`.

#### 4f. `kube-node-lease` termination flapping

If the node flaps NotReady with `leases.coordination.k8s.io ... namespace
kube-node-lease ... is being terminated`, the `kube-node-lease` namespace was
deleted/terminating. **Remediation (propose):** do not delete the
`kube-node-lease` namespace; if terminating, identify the finalizer/actor holding
it and restore the namespace.

--------------------------------------------------------------------------------

### Step 5: Remediation boundary & escalation

- Present the **root cause + evidence** (the exact conditions, events, log lines,
  or metrics observed). Provide **Cloud Logging deep links** (and Cloud Monitoring
  links for the Step 4b metrics) to the supporting entries — using the deep-link
  pattern from Steps 1-2 — so a human can open the evidence directly.
- Propose the fix as a **command or GitOps manifest change** for a human to apply
  — never apply, drain, or recreate nodes automatically.
**When to escalate (do this instead of proposing more self-service diagnostics):**

Escalate when either:

- the relevant logs are **unavailable** — excluded by a logging filter, or older
  than the log bucket's retention (the `_Default` bucket defaults to 30 days, so
  incidents older than that are permanently deleted); or
- the kubelet/event signature is **not in the Step 3 table** and the root cause
  remains **undetermined** after the branch investigations.

In those cases, do all three:

1.  **State the limitation plainly** (for example, "kubelet logs for that date are
    past the 30-day `_Default` retention window and are permanently deleted").
2.  **Summarize the findings you did gather** (node conditions, events, metrics,
    and any Admin Activity audit logs still in the `_Required` bucket, default
    400-day retention).
3.  **Route to GKE support / engineering escalation with those findings.** Do
    **not** keep proposing further self-service investigation, and do **not**
    fabricate a diagnosis when the evidence is missing.

--------------------------------------------------------------------------------

## References

This skill is derived from public Google Cloud documentation:

- [Troubleshoot nodes with the NotReady status](https://cloud.google.com/kubernetes-engine/docs/troubleshooting/node-notready)
  — node conditions and the kubelet / PLEG / system-OOM / containerd /
  `kube-node-lease` / CNI / admission-webhook signatures and their remediations.
- [Troubleshoot node registration](https://cloud.google.com/kubernetes-engine/docs/troubleshooting/node-registration)
  — Node Registration Checker for nodes that never finish registering.
- [View GKE logs](https://cloud.google.com/kubernetes-engine/docs/how-to/view-logs)
  and [Cloud Logging routing overview](https://cloud.google.com/logging/docs/routing/overview)
  — log queries (`resource.type="k8s_node"`, `log_id("kubelet")`) and log-bucket
  retention (`_Default` 30 days, `_Required` 400 days).
- [Logs Explorer interface](https://cloud.google.com/logging/docs/view/logs-explorer-interface)
  — building and sharing a query by URL (the `logs/query;query=...` deep-link
  format used above).
