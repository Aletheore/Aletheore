---
name: gke-basics
metadata:
  version: 1.1.1
  category: Containers
description: Manages core GKE cluster provisioning, credentials, Autopilot vs Standard selection, and workload deployment. Use when creating GKE clusters, fetching kubectl credentials, or deciding between Autopilot and Standard modes. Don't use for Workload Identity (use gke-workload-identity), GKE networking (use gke-networking), security hardening (use gke-platform-security or gke-workload-security), or cluster upgrades (use gke-upgrades).
skill_id: platforms/gke-basics
title: Gke Basics
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-basics
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-basics", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-basics", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 5, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["mcp:all", "mcp:default-use", "mcp:developer-knowledge", "mcp:gke", "mcp:neither", "mcp:skip"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Basics & Critical Gotchas

> **Routing Note:** For Workload Identity KSA/GSA setup, open `gke-workload-identity/SKILL.md`. For cluster security flags (`--database-encryption-key`, `--security-posture`, RBAC, Shielded Nodes, Binary Authorization), open `gke-platform-security/SKILL.md`. For cluster upgrades or maintenance windows, open `gke-upgrades/SKILL.md`. For generating Kubernetes YAML manifests (`Deployment`, `StatefulSet`, `Service`, `HTTPRoute`, `PodDisruptionBudget`), open `gke-manifest-generation/SKILL.md`.

Managed Kubernetes platform on Google Cloud. Defaults to Autopilot mode unless Standard is explicitly required.

## Key Selection Rules: Autopilot vs. Standard

* **Default to Autopilot** for almost all workloads.
* **Use Standard ONLY if:**
  * Custom node OS kernel parameters (`sysctl`) are required.
  * Custom node taints or specific hardware node pools are required.
  * DaemonSets require raw `hostPath` mounts to the host OS filesystem.
* When explaining why Standard is required over Autopilot, explicitly cite all matching restrictions (e.g., custom sysctls and custom node taints).
* *For advanced cluster architecture or complex node pool creation planning, refer to `gke-cluster-creation`.*

## Critical Gotchas & Best Practices

1. **Private Autopilot Clusters:**
   * Use `--enable-private-nodes` for private node IP addresses.
   * Use `--enable-private-endpoint` to disable public IP access to the control plane.
   * Restrict control plane access with `--enable-master-authorized-networks` and `--master-authorized-networks=CIDR_BLOCK`:
     ```bash
     gcloud container clusters create-auto CLUSTER_NAME --region=REGION \
       --enable-private-nodes \
       --enable-private-endpoint \
       --enable-master-authorized-networks \
       --master-authorized-networks=CIDR_BLOCK
     ```

2. **Workload Identity (IAM Binding):**
   * Never mount raw GCP Service Account JSON keys in Pods; open `gke-workload-identity/SKILL.md` for KSA/GSA setup.

3. **Autopilot Resource Requests:**
   * In Autopilot, CPU requests must be specified in increments of 250m (0.25 vCPU). If an unaligned CPU request (e.g., 300m) is requested, round up to the nearest 250m increment (500m / 0.5 vCPU).
   * Resource requests equal limits automatically. Omit `limits` to allow Autopilot to set defaults matching `requests`.

4. **Cluster Credentials:**
   * Always explicitly specify `--region` (for regional clusters) or `--zone` (for zonal clusters) when fetching credentials:
     ```bash
     gcloud container clusters get-credentials CLUSTER_NAME --region=REGION --quiet
     ```

## Reference Directory

-   [Core Concepts](references/core-concepts.md): Architecture, cluster modes (Autopilot vs Standard), networking, scaling, and security model.

-   [CLI Usage & Tool Reference](references/cli-reference.md): Tool preference hierarchy (MCP vs gcloud vs kubectl), `gcloud container` commands, and user preference overrides.

-   [Client Libraries](references/client-library-usage.md): Official Kubernetes and Google Cloud Container client libraries in Python, Go, Node.js, and Java.

-   [MCP Usage](references/mcp-usage.md): Connecting to and using the 23 structured GKE MCP tools for cluster management, K8s resources, and diagnostics.

-   [Infrastructure as Code](references/iac-usage.md): Terraform examples for `google_container_cluster` (Autopilot), Kubernetes provider resources, and YAML samples.
