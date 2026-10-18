---
name: cloud-run-alert-configuration
metadata:
  version: 1.0.1
  category: Serverless
description: Configures best-practice, high-signal alerting policies for Cloud Run resources on Google Cloud (services, jobs, and worker pools) based on seasoned SRE practices. Use when analyzing, recommending, writing, or deploying Terraform PromQL alerting policies to monitor Cloud Run error rates (4xx/5xx), request latency, container instance saturation (warning/critical), container CPU/memory utilization and allocation, billable instance time, job execution status, and worker pool queue backlog. Don't use for GKE workloads (use gke-alert-configuration) or Compute Engine VMs.
allowed-tools:
- terraform
- gcloud
skill_id: platforms/cloud-run-alert-configuration
title: Cloud Run Alert Configuration
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/cloud-run-alert-configuration
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/cloud-run-alert-configuration", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/cloud-run-alert-configuration", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 3, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Cloud Run Alert Configuration

Production-grade observability for Cloud Run using Terraform and PromQL
(Cloud Monitoring). Grounded in SRE practices, this skill focuses strictly on
actionable user impact and scaling bounds.

--------------------------------------------------------------------------------

## CRITICAL RULES

*   **Prompt-First Fast Path (Skip Discovery When Named)**:
    *   **If the user prompt explicitly specifies the target Cloud Run service, job, or worker pool name** (e.g., `'video-encoder'`, `'nightly-reconciliation'`, `'web-frontend'`, `'catalog-service'`, `'api-gateway'`, `'order-processor'`), **SKIP all workspace `.tf` file scanning (`find_by_name`, `code_search`, `list_dir`) and `gcloud` CLI discovery commands entirely**.
    *   Do NOT run `gcloud`, `terraform`, or file search tools when the target name is already provided in the prompt. Instead, parameterize the project ID (`variable "scoping_project_id" { default = "my-gcp-project" }`) and target resource name in Terraform variables and proceed immediately to **Step 2 (Configure Alerts)**.
*   **Autonomous Discovery (Only When Target Name is Omitted)**:
    *   **Never Scan Root Monorepo or Unbounded Directories**: Never run `find_by_name` or `ls` across root workspace directories.
    *   **Config First**: Only if the prompt omits the resource name, check `.tf` files in the immediate working directory for `google_cloud_run_v2_service`, `google_cloud_run_service`, or `google_cloud_run_v2_job`.
    *   **CLI Second (Graceful Fallback)**: Only if unconfigured in prompt or local `.tf` files, attempt `gcloud config get-value project` and `gcloud run services list`. If any `gcloud` command fails (e.g., auth or metadata errors) or `terraform` is missing, immediately stop running CLI commands and output parameterized HCL using explicit variable defaults.
*   **Workload Routing**: Always classify the workload target and follow its
    specific reference guide:
    *   For HTTP Services follow [services.md](references/services.md)
    *   For Cloud Run Jobs follow [jobs.md](references/jobs.md)
    *   For Worker Pools follow [worker_pools.md](references/worker_pools.md)
*   **Explicit Defaults & User Overrides**:
    *   Always use explicit defaults for all constants specified in
        the target workload's reference file (SLO targets, latency thresholds,
        SLAs, saturation ceilings, rate guards).
    *   State the defaults being applied in the final summary output and clearly
        notify the user that any default constant can be customized or
        overridden via Terraform variables or prompt input.
*   **Metric Scope Centralization**: Parameterize `project = var.scoping_project_id` in all Terraform `google_monitoring_alert_policy` resources so the policy can target either a single project or a centralized Cloud Monitoring Metrics Scope.
*   **PromQL `duration` (Retest Window) Rules**:
    *   **Lookbacks $\le$ 25h**: Set `duration = "300s"` (5m buffer) to absorb
        transient blips and scale-up lag (except immediate job failure alerts
        which use `duration = "0s"`).
    *   **Lookbacks $> 25$h** (e.g. 3d/7d Slow Burn): **Omit `duration`
        entirely** (or set to `0s`). Cloud Monitoring rejects PromQL queries
        with `duration` set on lookbacks >25h (`INVALID_ARGUMENT`).
*   **Terraform Standards & Mandatory Labels**:
    *   Output clean, complete `.tf` configurations using `google_monitoring_alert_policy` and `condition_prometheus_query_language` directly in your response.
    *   **Mandatory User Labels**: Every `google_monitoring_alert_policy` resource MUST include a `user_labels` block containing:
        ```hcl
        user_labels = {
          created-with-google-skill = "cloud-run-alert-configuration"
        }
        ```
    *   Include `alert_strategy { auto_close = "604800s" }` and parameterize `notification_channels = var.notification_channels`.

## WORKFLOW STEPS

### 1. Discovery & Target Identification

*   **Fast Path (Target Named in Prompt)**: If the user prompt names the target Cloud Run service, job, or worker pool, **skip all discovery commands and file searches** and proceed directly to **Step 2**.
*   **Discovery Fallback (Target Unnamed)**: Only if no resource name is provided in the prompt, check local `.tf` files or run `gcloud` to identify the target workload type and name. If `gcloud` auth fails, fall back immediately to default Terraform variables (`var.scoping_project_id`).

### 2. Configure Alerts

*   Route to the corresponding guide to generate the alert policies:
    *   **HTTP Services**: Open [services.md](references/services.md). Apply the
        requested alerting policy or standard suite covering availability SLOs (5xx), request
        latency (P95/P99), client errors (4xx), container instance saturation,
        container CPU/memory utilization, traffic anomalies (drop/surge), and billable
        instance time.
    *   **Batch Jobs**: Open [jobs.md](references/jobs.md). Apply immediate job
        execution failure alerts (`duration = "0s"`).
    *   **Worker Pools**: Open [worker_pools.md](references/worker_pools.md).
        Apply the 4-policy standard suite (Task Success SLO Fast/Slow Burn,
        Backlog ETD, Message Age SLA).

### 3. Terraform Generation & Review

*   Provide the complete HCL configuration in your response with explicitly parameterized defaults and the mandatory `user_labels` block (`created-with-google-skill = "cloud-run-alert-configuration"`).
*   State the applied defaults and remind the user of their ability to override
    any constant.
*   Provide a clear plain-English breakdown of the PromQL logic and triggering
    thresholds.

--------------------------------------------------------------------------------

## Additional Resources

*   [Cloud Run Documentation](https://docs.cloud.google.com/run/docs/overview/what-is-cloud-run.md.txt)
*   [Cloud Monitoring PromQL Documentation](https://docs.cloud.google.com/monitoring/promql/promql-in-monitoring.md.txt)
*   [Google Cloud Alerting Policies in Terraform](https://docs.cloud.google.com/monitoring/alerts/terraform.md.txt)
*   [Google SRE Workbook: Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
