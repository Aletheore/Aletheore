# ComputeClass: Lifecycle, Drift & Updates

## Consolidation (Scale-down)

Controlled via `spec.autoscalingPolicy`.

-   `consolidationDelayMinutes`: Floor is **1 minute**.
-   `consolidationThreshold`: CPU utilization % (0 = always candidate).
-   `gpuConsolidationThreshold`: Accelerator utilization %.
-   *Note:* Maintenance windows do **not** block consolidation. Use PDBs to
    suppress disruption.
-   *Blockers:* Local storage, 'safe-to-evict: false', and bare pods block
    scale-down. **DaemonSets do NOT block scale-down by default.** If empty
    nodes aren't scaling down, look for other blocking system pods.
-   **System Pod Segregation**: Since non-DaemonSet system pods (e.g., in
    `kube-system` namespace) often have tight PDBs or single replicas that block
    scale-down, **always recommend segregating system pods into a dedicated
    system ComputeClass/node pool** (using `system-pool-compute-class.yaml`).
    This isolates the system pods and keeps them from blocking the consolidation
    of Spot/workload nodes.

## ActiveMigration (Drift Reconciliation)

Reconciles pods back to higher-priority rules (e.g. moving from fallback On-Demand back to preferred Spot or C3) when capacity becomes obtainable again.

-   `optimizeRulePriority: true`: Enables the drift controller.
-   **Drift Reconciliation Loop**: Periodically evaluates running pods against the ComputeClass `priorities[]` ladder. When higher-priority capacity becomes available (e.g. 5-minute stockout cooldown expires or new Spot capacity is energized), the controller provisions preferred nodes and initiates voluntary evictions to pull workloads up the ladder.
-   **Voluntary Disruption Contract**: Active Migration calls the Kubernetes Eviction API and strictly respects `PodDisruptionBudgets` (PDBs). Without a PDB, evictions occur as fast as new nodes become ready.
-   **Blockers to Active Migration**:
    -   Tight PDBs (`maxUnavailable: 0` or `minAvailable: 100%`) block eviction entirely.
    -   Non-DaemonSet system pods in `kube-system` without PDBs (DaemonSets are stripped via `podutils.FilterRecreatablePods` and do NOT block drain).
    -   `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` annotation blocks node removal.
    -   `cluster-autoscaler.kubernetes.io/safe-to-evict: "on-completion"` defers active migration until the pod terminates naturally.

### Blue-Green & Canary Rollout Protection Against Active Migration Churn

During phased blue-green or canary deployments (e.g., 10% -> 30% -> 60% -> 100% traffic shifts), `activeMigration` can cause severe rollout churn by voluntarily evicting newly scheduled Green pods to optimize node placement while the pods are warming up or receiving test traffic.

#### Why PDBs are Superior to Pod Template Annotations

-   **Pod Template Annotations (`safe-to-evict: false`)**: Adding or modifying `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` inside `spec.template.metadata.annotations` mutates the `PodTemplateSpec` hash. This triggers an **immediate rolling restart** of the entire Deployment, causing unwanted downtime.
-   **Rollout-Scoped PodDisruptionBudgets (`maxUnavailable: 0`)**: Managing a standalone PDB scoped specifically to the Green version (`matchLabels: version: green`) operates completely out-of-band via admission control in memory:
    -   Causes **zero pod restarts** (no modification to Deployment or ReplicaSet spec).
    -   Blocks voluntary `activeMigration` and node consolidation during the release window.
    -   Can be dynamically relaxed to standard production budgets (e.g., `maxUnavailable: 25%`) via `kubectl patch` once cutover reaches 100%.

#### 3-Stage Rollout Lifecycle Pattern

1.  **Stage 1: Deploy Green + Guard PDB**
    -   Apply Green rollout PDB with `maxUnavailable: 0` targeting `version: green`.
    -   Deploy `my-service-green` Deployment.
    -   Green pods schedule and initialize; `activeMigration` is blocked from evicting them.
2.  **Stage 2: Phased Traffic Shift (10% -> 30% -> 60% -> 100%)**
    -   Shift traffic incrementally across validation windows.
    -   Green pods remain stable on their provisioned nodes without eviction churn.
3.  **Stage 3: Cutover & Relax PDB**
    -   100% traffic routed to Green; Blue Deployment and Blue PDB decommissioned.
    -   Patch Green PDB to operational budget (`maxUnavailable: 25%`) to re-enable background `activeMigration` node shape optimization.

#### PDB Safety and Edge Cases

| Scenario | Behavior Under `maxUnavailable: 0` | Impact / Safety Mechanism |
|---|---|---|
| **Pod creation & scale-up** | ReplicaSet creates pods via the direct Pod Create API. | **No impact**: PDBs are not consulted during pod creation. |
| **ActiveMigration / Consolidation** | Cluster Autoscaler queries Eviction API; eviction rejected (`disruptionsAllowed: 0`). | **Desired**: Pods remain stable on nodes during rollout. |
| **Involuntary node loss (Spot preemption / host failure)** | Hypervisor reclaims VM (involuntary disruption, bypasses Eviction API). | **Self-healing**: ReplicaSet detects missing ready replicas and spawns replacements via Pod Create API immediately. |
| **Rollout abort / rollback** | CI/CD scales replicas to 0 or deletes Green Deployment. | **Safe**: Pod Delete API bypasses PDBs; pods terminate immediately. |
| **Node auto-upgrades** | GKE node drain queries Eviction API and pauses until drain timeout. | **Low risk**: Node upgrades wait for rollout completion. |

```yaml
# 1. Rollout Guard PDB (Stage 1)
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: my-service-green-pdb
  namespace: default
spec:
  maxUnavailable: 0
  selector:
    matchLabels:
      app: my-service
      version: green
---
# 2. Green Deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-service-green
  namespace: default
spec:
  replicas: 10
  selector:
    matchLabels:
      app: my-service
      version: green
  template:
    metadata:
      labels:
        app: my-service
        version: green
    spec:
      nodeSelector:
        cloud.google.com/compute-class: "general-workloads"
      containers:
      - name: app
        image: gcr.io/my-project/my-service:v2.0.0
        resources:
          requests:
            cpu: "2"
            memory: "4Gi"
```

> **CRITICAL K8S DISTINCTION:** PDBs and `safe-to-evict: false` ONLY protect against *voluntary* disruptions (ActiveMigration, scale-down, upgrades). They **DO NOT** prevent *involuntary* Spot VM preemptions. Spot nodes can be reclaimed at any time, regardless of PDBs.

## Updating a ComputeClass

-   **No Retroactive Change:** Updating a ComputeClass does **not** change existing nodes.
-   **New Nodes Only:** Only nodes created after the update use the new spec.
-   **Drift Behavior:**
    -   *Without ActiveMigration:* Old-spec nodes persist until rescheduled (rollout, drain, preemption).
    -   *With ActiveMigration:* Controller drifts pods toward nodes matching the updated (higher-priority) spec.
-   **Disruption-Sensitive:** For training/stateful roles, schedule updates for maintenance windows or drain manually.
