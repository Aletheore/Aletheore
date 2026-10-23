---
name: gke-productionize
metadata:
  version: 1.0.0
  category: Containers
description: Orchestrates comprehensive production readiness reviews and assessments for GKE clusters and workloads across scalability, security, reliability, observability, backup/DR, and cost optimization. Use when asked to productionize, prepare, assess, audit, or review a GKE cluster or workload before going live to production. Don't use for deep-dive single-domain implementation (use specific domain skills like gke-workload-scaling, gke-platform-security, gke-workload-security, gke-service-networking, gke-reliability instead).
skill_id: platforms/gke-productionize
title: GKE Productionize
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-productionize
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-productionize", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-productionize", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Productionize Skill

This skill acts as a high-level orchestrator for preparing a GKE cluster and its
workloads for production readiness.

> [!IMPORTANT]
> This is a **meta-skill** or **orchestrator skill**. You are
> expected to invoke and run many other specialized skills listed in this
> document as part of the overall productionization process. Do not attempt to
> implement all production readiness features directly within this skill;
> instead, use this skill to assess the environment and then delegate to the
> specific skills for each domain.

## Scope

This skill is adaptable to:

-   A single application (already on Kubernetes or not).
-   A set of applications.
-   A target cluster.

## Workflow

### 1. Discovery Phase

Before making recommendations, discover the current state of the environment.

#### Cluster Discovery

Run these commands to understand the cluster setup:

-   Check cluster details: `gcloud container clusters describe {cluster_name}
    --location {location} --project {project}`
-   Check for Autopilot vs Standard: Look for the following block in the
    describe output:

    ```yaml
    autopilot:
      enabled: true
    ```
-   Check release channel: Look for `releaseChannel`.

#### Workload Discovery

If a specific application is targeted, discover its configuration:

-   Get deployment/statefulset details: `kubectl get deployment {app_name} -n
    {namespace} -o yaml`
-   Check for dedicated namespace and labels: `kubectl get namespace {namespace}
    -o yaml` (Look for Pod Security Standards labels).
-   Check for dedicated service account usage: `kubectl get pods -n {namespace}
    -o
    custom-columns="NAME:.metadata.name,SERVICE_ACCOUNT:.spec.serviceAccountName"`
-   Check for resource requests and limits.
-   Check for liveness, readiness, and startup probes.
-   Check for HPA: `kubectl get hpa -n {namespace}`
-   Check for PDB: `kubectl get pdb -n {namespace}`
-   Check for NetworkPolicies: `kubectl get networkpolicy -n {namespace}`

### 2. Production Readiness Assessment

**Before implementation, you MUST run the skills for each relevant specialized
area listed below and incorporate its guidance into your assessment and plan.
Failure to do so will result in a non-compliant production configuration.**

#### A. App Onboarding (Pre-Kubernetes)

If the application is not yet running on GKE, you MUST run the
`gke-app-onboarding` skill for planning containerization, image building, and
basic deployment.

#### B. Scalability & Resource Management

Ensure workloads have appropriate resources and autoscaling.

-   **Action**: You MUST run the `gke-workload-scaling` skill for configuring
    HPA, VPA, and resource limits.

#### C. Observability

Ensure adequate logging and monitoring are in place.

-   **Action**: You MUST run the `gke-observability` skill for setting up Cloud
    Logging, Monitoring, and Managed Prometheus.

#### D. Reliability

Ensure high availability and graceful degradation.

-   **Action**: You MUST run the `gke-reliability` skill for configuring
    regional clusters, PDBs, and health probes.

#### E. Security

Harden the cluster and workloads.

-   **Action**: You MUST run the `gke-platform-security` and
    `gke-workload-security` skills for Workload Identity, Network Policies, and
    Shielded Nodes.
-   **Namespace Isolation**: Ensure workloads run in dedicated namespaces with
    Pod Security Standards (PSS) enforced via labels.
-   **Least Privilege**: Ensure workloads use dedicated ServiceAccounts instead
    of the `default` ServiceAccount.

#### F. Backup & Disaster Recovery

Ensure stateful data is protected.

-   **Action**: You MUST run the `gke-backup-dr` skill for configuring Backup
    for GKE and restore procedures.

#### G. Edge Security & Ingress

Secure external access.

-   **Action**: You MUST run the `gke-service-networking` skill for Gateway API,
    Ingress, and Cloud Armor.

#### H. Cost Optimization

Ensure efficient use of resources.

-   **Action**: You MUST run the `gke-cost-optimization` skill for strategies on
    rightsizing, quotas, and Spot VMs.

#### I. Upgrades & Maintenance Posture

Ensure a safe, predictable upgrade posture.

-   **Action**: You MUST run the `gke-upgrades` skill for release channel
    selection, maintenance windows/exclusions, and node pool upgrade strategy.

#### J. Golden Path Defaults Audit

Ensure the cluster configuration matches recommended defaults.

-   **Action**: You MUST run the `gke-golden-path` skill to compare the cluster
    against golden path defaults and report deviations with severity and
    remediation.

### 3. Production Readiness Scoring

After the assessment, provide a summary report with a RAG (Red, Amber, Green)
status for each area and an overall readiness score. This helps prioritize
remediation efforts.

Apply this rubric deterministically so repeated assessments of the same
environment produce the same result:

1.  **Per-domain criteria**: For each assessed domain (A-J), list the concrete
    checks performed (from the domain skill's guidance) and classify each check
    as **pass**, **fail-critical** (production-blocking, e.g., no resource
    requests, no backups for stateful data, public control plane in a locked
    down environment), or **fail-minor** (improvement, e.g., missing VPA
    recommendations, no Spot usage for batch).
2.  **RAG mapping (per domain)**:
    -   **Red** = one or more fail-critical checks.
    -   **Amber** = no fail-critical, but one or more fail-minor checks.
    -   **Green** = all checks pass.
3.  **Domain score**: Green = 100, Amber = 50, Red = 0.
4.  **Weighted overall score**: weight Security, Reliability, and Backup/DR at
    2x; all other assessed domains at 1x. Overall score = sum(domain score x
    weight) / sum(weights), rounded to the nearest integer. Exclude domains
    that are not applicable (e.g., Backup/DR for fully stateless workloads) from
    both sums and note the exclusion.
5.  **Readiness verdict**: >= 90 with no Red domains = "Production ready";
    70-89 with no Red domains = "Ready with follow-ups"; anything else =
    "Not production ready".

In the report, show the per-domain check lists, RAG status, weights, and the
computed overall score.

## Adaptability Guidelines

-   **Single App**: Focus on Health Probes, HPA, Resource Limits, PDB, and
    Workload Identity for that specific app.
-   **Cluster Wide**: Focus on Cluster Autoscaler, Multi-zonal setup, Release
    Channels, Maintenance Windows, and default Network Policies.
-   **Proactive Execution**: Proactively execute relevant skills (e.g.,
    observability, security, scaling, reliability) to assess and propose
    improvements, seeking user confirmation before applying state-changing
    implementations.
