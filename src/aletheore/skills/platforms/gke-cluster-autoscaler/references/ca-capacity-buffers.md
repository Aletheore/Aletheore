# Cluster Autoscaler: Capacity Buffers (Pre-warm)

**Launch stage: Preview** (Pre-GA Offerings Terms). Version gates: active buffers
require GKE **1.35.2-gke.1842000+**; standby buffers require GKE
**1.36.0-gke.2253000+**.

## `CapacityBuffer` (CRD)

- **API:** `autoscaling.x-k8s.io/v1beta1`, `kind: CapacityBuffer`. **Namespace-scoped.**
- Targets a specific `ComputeClass` via `nodeSelector` in the `podTemplateRef`;
  works with node pool auto-creation (buffers honor the class's `priorities[]`).
- **Two `provisioningStrategy` values** (do not confuse the domains):
  - **Active:** `buffer.x-k8s.io/active-capacity` (default) — placeholder pods
    hold fully **running** warm nodes; real workloads evict them instantly. You
    pay full node price while idle.
  - **Standby:** `buffer.gke.io/standby-capacity` (June 2026) — nodes are fully
    initialized (DaemonSets started, images pulled), then **suspended**.

## Standby buffers (`buffer.gke.io/standby-capacity`)

- **Cost model:** while suspended you pay only **persistent disk + IP address**
  (node state is stored to disk; compute/memory charges stop). Resume takes
  **~30s** — 2-3x faster than provisioning a fresh node.
- **Requires:** GKE **1.36.0-gke.2253000+**; **Standard** clusters with node
  auto-provisioning / node pool auto-creation enabled. ComputeClass targeting
  works as with active buffers.
- **Annotations (on the CapacityBuffer):**
  - `buffer.gke.io/standby-capacity-init-time` (default `"5m"`): how long a
    (re)created buffer node stays active before suspending.
  - `buffer.gke.io/standby-capacity-refresh-frequency` (default `"24h"`;
    `"never"` disables): how often standby nodes are recreated.
- **Detection:** suspended nodes carry node condition **`Suspended`**
  (`True` = suspended, `False` = resumed, absent = never suspended).
- **Hybrid pattern:** small active buffer + larger standby buffer — GKE refills
  the active buffer by resuming standby nodes (~30s) and backfills standby in
  the background.
- **Exclusions:** no GPU/TPU nodes, Local SSD, CMEK, or Confidential GKE Nodes;
  Compute Engine suspend/resume limits apply (e.g., >208 GB memory nodes).
  Accelerator pre-warming must use **active** buffers.

## Sizing Modes

- **Fixed:** `replicas: 3`. Always keep N units warm.
- **Dynamic:** `percentage: 20` + `scalableRef: <Deployment>`. Headroom scales with workload.
- **Resource limits:** `limits: {cpu, memory}` caps total buffered capacity.

## Why use Buffers?

- **Bursty Serving:** Pod-pending SLOs can't tolerate 60-120s node pool auto-creation delay.
- **HPA outpaces cluster autoscaler:** Workload scales faster than nodes can arrive.
- **Pre-warming:** Warm GPUs/TPUs before known traffic windows (active buffers only).
- **Standby = cheap headroom:** overprovisioned warm capacity at disk+IP cost
  instead of full node cost, if workloads tolerate the ~30s resume delay.

*Note:* Replaces the "dumb" floor of `--min-nodes` with shape-aware, class-targeted warm capacity.

*Related:* node pool auto-creation itself is up to **85% faster** from GKE
**1.34.1-gke.1829001** (multiple node pools created concurrently; no
configuration needed).
