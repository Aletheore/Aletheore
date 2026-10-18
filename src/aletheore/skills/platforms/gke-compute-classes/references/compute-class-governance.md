# Restricting ComputeClass Access (Governance)

Three **independent** layers — each protects something the other can't. Use all
three for full governance.

Layer           | Protects                                 | Mechanism                   | Asset
--------------- | ---------------------------------------- | --------------------------- | -----
**CRUD**        | who can create/modify the CC **object**  | RBAC `ClusterRole`          | `computeclass-rbac-editor.yaml`
**Consumption** | who can **request** a CC from a workload | `ValidatingAdmissionPolicy` | `restrict-computeclass-usage-vap.yaml`
**Scale-Up Cap**| **how much** capacity the CC can autoscale | `CapacityQuota` CRD         | `capacity-quota-spillover.yaml`

**Key point:** RBAC cannot restrict consumption. Referencing a CC from a Pod
isn't a CRUD verb on the CC object — it's a field in the Pod/Deployment spec.
RBAC governs the object; admission validation governs the spec; CapacityQuota
governs the physical autoscaler footprint.

**No native consumption field — don't hallucinate one.** The ComputeClass spec
has **no** `namespacePolicy`/`allowedNamespaces`/`allowedNamespacesPolicy` field
(or any field) that restricts which namespaces may *consume* the class.
Consumption control is **admission-only** (the VAP below).

## 1. CRUD safeguard — RBAC

-   ComputeClass is a **cluster-scoped CRD** → use `ClusterRole` +
    `ClusterRoleBinding`, **not** a namespaced `Role`.
-   `apiGroups: ["cloud.google.com"]`, `resources: ["computeclasses"]`.
-   Tutorial verbs: `create`, `update`. **For a full lockdown also grant `patch`
    and `delete`** — otherwise a non-creator can still patch/delete an existing
    CC.
-   Bind to a **Google Group** (`kind: Group`, `name: ...@<GROUP_DOMAIN>`) for
    centralized membership over per-user bindings.
-   Verify: `kubectl auth can-i create computeclasses.cloud.google.com
    --as=<USER>` (run for a member and a non-member).

## 2. Consumption safeguard — ValidatingAdmissionPolicy (VAP)

Native K8s admission (in-process CEL, **no webhook**). Policy = the CEL rules;
Binding = scope + actions.

**Three access paths — the CEL must close ALL of them, or it leaks:**

1.  `nodeSelector` → `cloud.google.com/compute-class: <NAME>`.
2.  `nodeAffinity` → `matchExpressions` key `cloud.google.com/compute-class`,
    `In [<NAME>]`.
3.  `tolerations` → tolerating the CC's `NoSchedule` taint, **including the
    wildcard** (`operator: Exists` with **no key**) which tolerates *every*
    taint and thus the restricted CC. A nodeSelector-only policy is the classic
    bypass.

**Match every workload kind, not just Pods+Deployments.** The tutorial's
`matchConstraints` lists only `pods` (core/v1) and `deployments` (apps/v1) —
leaving `statefulsets`/`daemonsets`/`replicasets` (apps), `jobs`/`cronjobs`
(batch) as bypasses. Controllers carry the spec at `spec.template.spec`
(CronJob: `spec.jobTemplate.spec.template.spec`); bare Pods at `spec`.

**Binding:**

-   `validationActions: ["Deny","Audit"]` — Deny rejects; Audit logs to the K8s
    audit log. Run **Audit-only first** to find existing violators, then add
    Deny.
-   `failurePolicy: Fail` — fail closed.
-   Scope with `namespaceSelector.matchLabels` (e.g.
    `kubernetes.io/metadata.name: <NS>`).
-   Apply: `kubectl apply -f restrict-computeclass-usage-vap.yaml`.

Denial surfaces as: `admission webhook ... denied the request: This namespace
cannot request ComputeClass <NAME> ...`.

## 3. Scale-up Safeguard — CapacityQuota (GKE 1.36.2+)

A **CapacityQuota** (`autoscaling.x-k8s.io/v1beta1`) restricts the physical
infrastructure footprint (CPU, memory, GPUs, node count) that workloads consuming
a ComputeClass can provision via the Cluster Autoscaler.

-   **Class-wide Capping:** Target a class via `selector.matchLabels`:
    ```yaml
    selector:
      matchLabels:
        cloud.google.com/compute-class: <CC_NAME>
    limits:
      resources:
        cpu: 32
    ```
-   **Priority Fallback / CUD Exhaustion Spillover Pattern:** Combine
    `compute-class` with `cloud.google.com/machine-family: <PRIMARY_FAMILY>` in
    `matchLabels`. This caps the primary preferred machine family (e.g., `n4`
    capped at 100 CPU for a 100-core Committed Use Discount) without restricting
    secondary fallback priorities in the class (`n4d`, `c4`). When the primary
    family hits the CUD/quota limit, the Cluster Autoscaler rejects further
    scale-up of that family (`noScaleUp` event:
    `exceeded quota: "CapacityQuota/<NAME>", resources: cpu`) and automatically
    spills over excess demand to the uncapped fallback families.

-   **Observability:**
    -   Check validity: `status.conditions[type="cluster-autoscaler.kubernetes.io/valid"]` (`True` means valid and enforced).
    -   Track capacity: `status.used.resources.<resource>` shows physical usage on matched nodes after successful scale-up.
-   **Limitations:**
    -   Do **not** specify `node.kubernetes.io/instance-type` in a CapacityQuota
        selector (GKE rejects it; use `ComputeClass.spec.priorities[].machineType` rules instead).
    -   Enforced only by Cluster Autoscaler (manual cluster scaling can exceed limits).
