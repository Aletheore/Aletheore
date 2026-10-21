---
name: gke-cost-analysis
metadata:
  version: 1.0.0
  category: CloudObservabilityAndMonitoring
description: Answer natural language questions and perform analysis on GKE cluster and workload costs using BigQuery billing exports, cost allocation data, and live cluster monitoring metrics. Use when querying GKE costs across projects, namespaces, or workloads, analyzing billing reports in BigQuery (`bq`), checking cluster cost budgets (`gcloud billing`), or diagnosing cost drivers like pod requests vs. actual utilization (`kubectl top`). Don't use for applying cost optimization changes, creating rightsizing manifests (VPA/MPA), or selecting ComputeClasses (use gke-cost-optimization instead).
skill_id: platforms/gke-cost-analysis
title: Gke Cost Analysis
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-cost-analysis
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-cost-analysis", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-cost-analysis", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Cost Analysis

This skill provides guidance on answering natural language questions about
GKE-related costs, billing reports, and utilization analysis.

## Overview

When users ask about GKE costs (e.g., "What are my costs across projects?",
"What's my most expensive namespace?", "Why is my cluster cost spiking?"), use
this skill to provide a structured and expert response using BigQuery billing
exports, cost allocation metadata, and live cluster metrics.

## Instructions

When handling a cost-related question:

1.  **Provide a Direct Answer**: Address the specific cost question or
    analytical request clearly and concisely.
2.  **Explain BigQuery Integration**: Explain how to query BigQuery for
    historical cost breakdown. Note that GKE costs originate from the GCP
    Billing Detailed BigQuery Export (`gcp_billing_export_resource_v1_*`).
3.  **Check & Verify Cost Allocation**: Explain that GKE Cost Allocation must be
    enabled on the cluster (`--enable-cost-allocation`) for namespace, label,
    and workload-level billing granularity. If queries return empty labels,
    provide the `gcloud` command to enable it.
4.  **Analyze Pricing Drivers & Utilization**: When diagnosing cost drivers,
    explain whether the cluster is in Autopilot (billed by requested pod
    CPU/memory) or Standard mode (billed by underlying VM node size + control
    plane fees), and compare live utilization (`kubectl top`) against
    provisioned requests.
5.  **Provide Actionable Commands/Queries**: Provide concrete BigQuery CLI (`bq
    query`) commands or read-only `gcloud`/`kubectl` inspection commands. Prefer
    `bq` over BigQuery Studio when available.

## Key Points & Pricing Drivers

-   **Data Source**: GKE costs come from GCP Billing Detailed BigQuery Export.
    The user must provide the full path to their BigQuery table (dataset name
    and table name containing the Billing Account ID).
-   **Granularity Requirement**: GKE Cost Allocation
    (`--enable-cost-allocation`) must be enabled on the cluster to populate
    `goog-k8s-cluster-name`, `k8s-namespace`, `k8s-workload-name`, and
    `k8s-workload-type` labels in BigQuery.
-   **Autopilot vs. Standard Cost Drivers**:
    -   **Autopilot Pricing**: Billed directly on pod resource requests
        (`requests.cpu`, `requests.memory`, ephemeral storage). Over-requested
        pods drive up billing regardless of whether the pod actively uses those
        CPU cycles or memory.
    -   **Standard Pricing**: Billed on provisioned node pool VMs (`e2`, `n4`,
        `c3`, etc.). Idle nodes or multiple low-utilization dev clusters drive
        excess infrastructure costs.
    -   **Cluster Management Fee**: ~$0.10/hour per cluster applies to BOTH
        Standard and Autopilot modes. The free tier waives it for one eligible
        cluster per billing account.
-   **Credits & Discounts Impact**: When analyzing `cost` versus
    `cost_before_credits`, note that Committed Use Discounts (CUDs) and Spot VMs
    appear as credits or reduced rate charges in the billing export.
-   **Tools & Syntax**: BigQuery CLI (`bq`) is preferred. When writing Standard
    SQL queries, use a dot (`.`) instead of a colon (`:`) to separate the
    project ID and dataset name (`{project_id}.{dataset_name}.{table_name}`).
-   **Defaults**: Assume last 30 days, row limit 10, ordering by cost descending
    (`ORDER BY cost DESC`), unless specified otherwise.

## Live Cluster & Cost Monitoring

Use read-only CLI commands to inspect current cluster budgets, node utilization,
and pod resource consumption vs. requests:

```bash
# View billing budgets for an account (requires Cost Management API)
gcloud billing budgets list --billing-account={billing_account} --quiet

# View live node resource utilization across the cluster
kubectl top nodes

# View pod resource usage across namespaces (compare against requested limits to diagnose waste)
kubectl top pods --all-namespaces --containers
```

> **Warning — cluster mutation, not read-only:** Enabling GKE cost allocation
> modifies the cluster. Get explicit user confirmation before running it, and
> note that namespace/workload labels populate in the billing export only from
> enablement onward (no historical backfill).
>
> ```bash
> gcloud container clusters update {cluster_name} \
>     --enable-cost-allocation \
>     --region {region}
> ```

## Applying Cost Optimizations

To apply rightsizing changes based on analysis (such as setting up `VPA`
recommendation mode, adjusting CPU/memory to `P95 * 1.2`, configuring Spot VMs
via `nodeSelector` or `ComputeClass`, enforcing `ResourceQuotas`, or selecting
machine types and CUDs), use the **`gke-cost-optimization`** skill.

## BigQuery Query Templates

Ready-to-adapt `bq query` templates — single workload cost, per-workload
per-cluster breakdown, per-namespace breakdown — with the placeholder policy
and defaults (30 days, `LIMIT 10`, `ORDER BY cost DESC`) are in
[references/billing-queries.md](references/billing-queries.md). All parameters
(dataset, table, project, cluster, etc.) must be replaced with user values.

Note: Checking that the `goog-k8s-cluster-name` label exists scopes the total
billing data specifically to GKE costs.
