---
name: gke-golden-path
description: Provides GKE golden path configuration defaults, production readiness checklists, and cluster default patterns. Use when designing GKE clusters, verifying GKE production readiness, or checking configurations against GKE defaults. Don't use for setting up workload autoscaling specifically (use gke-workload-scaling instead).
metadata:
  version: 1.1.0
  category: Containers
skill_id: platforms/gke-golden-path
title: GKE Golden Path
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-golden-path
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-golden-path", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-golden-path", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Golden Path Configuration

The golden path is the recommended Autopilot configuration for production
clusters. It defines sensible defaults — when the user requests different
settings, apply them and note relevant trade-offs. For setting up autoscaling
specifically, use `gke-cluster-autoscaler` for node autoscaling or
`gke-workload-scaling` for workload autoscaling (HPA/VPA).

> **MCP Tools:** `get_cluster`, `create_cluster`, `update_cluster`

## Rules

1.  **Default to the golden path.** Use golden path values unless the user
    requests otherwise. When deviating, note trade-offs but respect the user's
    choice.
2.  **Day-0 vs Day-1.** Flag Day-0 decisions (networking, private nodes,
    subnets, IP allocation) prominently — they are hard/impossible to change
    after creation.
3.  **Tool preference: MCP > gcloud > kubectl.** MCP is preferred as it directly
    interfaces with GKE APIs with structured data, reducing shell syntax errors
    and parsing ambiguities. See the `gke-basics` skill's CLI reference for full
    coverage matrix and override options. If the user
    says "use gcloud" or "use kubectl", respect that for the session.
4.  **Document decisions and rationale**, especially for Day-0 choices and
    golden path deviations.

## Required Inputs

If the user is unsure, use golden path defaults.

-   **Project ID** (required)
-   **Region** (required, e.g., `us-central1`)
-   **Cluster name** (required)
-   **Environment type**: dev/test or production (defaults to production)
-   **Networking**: bring-your-own VPC/subnet or auto-create (default:
    auto-create)
-   **Scale expectations**: expected node/pod count, workload types
-   **Cost constraints**: Spot VM tolerance, budget considerations

## Always-Apply Defaults

Recommended best practices applied by default. If the user requests a different
setting, apply it and briefly note the security or operational trade-off.

Setting                                                            | Golden Path Value
------------------------------------------------------------------ | -----------------
`autopilot.enabled`                                                | `true`
`privateClusterConfig.enablePrivateNodes`                          | `true`
`masterAuthorizedNetworksConfig.privateEndpointEnforcementEnabled` | `true`
`secretManagerConfig.enabled` + `rotationInterval: 120s`           | `true`
`rbacBindingConfig.enableInsecureBinding*`                         | `false` (both)
`workloadIdentityConfig.workloadPool`                              | enabled
`networkConfig.datapathProvider`                                   | `ADVANCED_DATAPATH`
`networkConfig.dnsConfig.clusterDns`                               | `CLOUD_DNS`
`autoscaling.autoscalingProfile`                                   | `OPTIMIZE_UTILIZATION`
`verticalPodAutoscaling.enabled`                                   | `true`
`monitoringConfig` components                                      | SYSTEM_COMPONENTS, STORAGE, POD, DEPLOYMENT, STATEFULSET, DAEMONSET, HPA, JOBSET, CADVISOR, KUBELET, DCGM, APISERVER, SCHEDULER, CONTROLLER_MANAGER
`loggingConfig` components                                         | SYSTEM_COMPONENTS, WORKLOADS (enabled by default)
`advancedDatapathObservabilityConfig.enableMetrics`                | `true`
`nodeConfig.shieldedInstanceConfig.enableSecureBoot`               | `true`
`nodeConfig.workloadMetadataConfig.mode`                           | `GKE_METADATA`
`nodeConfig.gcfsConfig.enabled` / `gvnic.enabled`                  | `true` / `true`
`addonsConfig.statefulHaConfig.enabled`                            | `true`
Storage CSI drivers (Filestore, GCS FUSE, Parallelstore)           | enabled
Pod Security Standards                                             | `restricted` on production namespaces

## Customer-Configurable Settings

These have golden path defaults but customers may deviate with valid
justification. **Ask before changing.**

Setting                                  | Default                             | Why Deviate
---------------------------------------- | ----------------------------------- | -----------
`dnsEndpointConfig.allowExternalTraffic` | `true`                              | Restrict if cluster only accessed from within VPC
`autoIpamConfig` / `createSubnetwork`    | `true` / `true`                     | Customer has pre-existing VPC/subnets
`maxPodsPerNode`                         | `48` (this golden path's choice)    | Halves per-node IP consumption (/25 instead of /24). Not a GKE default (Standard defaults to `110`, Autopilot to `32`); raise for high pod-density at the cost of more CIDR space
`subnetwork`                             | auto-created                        | Customer brings existing subnets
Release channel + maintenance windows    | `REGULAR` channel with a recurring maintenance window | Add targeted maintenance exclusions (keep under ~6 months) only for critical freezes — see the `gke-upgrades` skill
`nodeConfig.bootDisk.diskType`           | `pd-balanced`                       | `pd-ssd` for I/O-intensive, `pd-standard` for cost

> **Note**: Autopilot selects node machine types automatically (e.g.,
> `ek-standard-8` may appear in describe output); the machine type is not
> customer-configurable in Autopilot. Steer workload placement via
> ComputeClasses instead.

## Guardrails

-   Do not request or output secrets (tokens, keys, service account JSON).
-   Resolve project/cluster context from the conversation, MCP tools, or
    `gcloud config get-value project`; ask the user only if it cannot be
    resolved.
-   For Day-0 decisions, always ask clarifying questions before proceeding.
-   For Day-1 features, propose golden path defaults with trade-offs and let the
    customer confirm.
-   Do not promise zero downtime — see Upgrade Disruption below for what to
    advise instead.
-   When auditing existing clusters, compare against golden path and report
    deviations with severity and remediation.

## Upgrade Disruption

**Never promise zero downtime for node upgrades, on any configuration.** Node
upgrades cordon and drain nodes, which evicts Pods. Draining honors
PodDisruptionBudgets and `terminationGracePeriodSeconds` for **up to one hour**,
after which GKE forcefully evicts the remaining Pods so the upgrade can proceed.
A PDB narrows the window; it cannot veto the upgrade. Say so plainly rather than
implying the disruption can be eliminated.

What to recommend, all four — not a subset:

-   **PodDisruptionBudgets** with `minAvailable` set so eviction cannot take the
    last healthy replica. A PDB that can never be satisfied stalls the drain for
    an hour and then loses anyway.
-   **At least 2 replicas**, spread across zones with topology spread
    constraints. A single-replica Deployment has downtime by definition.
-   **Readiness probes** that reflect real serving health, so traffic drains
    before the Pod dies.
-   **Surge upgrade settings** on the node pool. Surge is the default strategy;
    the default is `maxSurge=1`, `maxUnavailable=0` — one extra node is created
    and made ready before an old one is drained.

    Setting          | Controls                                            | Default
    ---------------- | --------------------------------------------------- | -------
    `maxSurge`       | Additional nodes added per zone during the upgrade  | `1`
    `maxUnavailable` | Nodes simultaneously unavailable per zone           | `0`

    Nodes upgraded at once is the **sum** of the two, capped at 20 (Autopilot)
    and 100 (Standard). Multi-zone node pools upgrade one zone at a time. Raising
    `maxUnavailable` trades availability for speed; raising `maxSurge` trades
    cost for availability.

> **Caveat**: `externalTrafficPolicy: Local` does not work with parallel node
> drains, so it constrains aggressive surge configurations.

For rollback procedures and maintenance windows, see the `gke-upgrades` skill.

## Golden Path Config

See [golden-path-autopilot.yaml](./assets/golden-path-autopilot.yaml) for the
full cluster-level policy settings.
