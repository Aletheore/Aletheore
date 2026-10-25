---
name: gke-backup-dr
description: 'Configures Backup for GKE: the BackupRestore cluster addon, BackupPlan and RestorePlan resources, restore workflows, and CMEK-encrypted backups. Use for backup policies, disaster recovery, or GKE cluster restores. Don''t use for database backups.'
metadata:
  version: 1.0.0
  category: Storage
skill_id: platforms/gke-backup-dr
title: GKE Backup Dr
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-backup-dr
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-backup-dr", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-backup-dr", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Backup & Disaster Recovery

Protects stateful GKE workloads using Backup for GKE. Backup for GKE can capture
both Kubernetes resource metadata (manifests, configurations, and secrets) and
the underlying persistent volume (PV) data — but volume data and secrets are
**only** captured when the backup plan explicitly enables them (see the flags
below).

## CLI Reference

```bash
# Enable the BackupRestore addon (Slow cluster-level update)
gcloud container clusters update {cluster_name} \
  --update-addons=BackupRestore=ENABLED --location={location} --quiet

# Create Backup Plan
gcloud beta container backup-restore backup-plans create {plan_name} \
  --project={project_id} --location={location} \
  --cluster=projects/{project_id}/locations/{location}/clusters/{cluster_name} \
  --all-namespaces \
  --include-volume-data --include-secrets \
  --backup-retain-days={days} --cron-schedule="{cron}" --quiet

# Trigger Manual Backup
gcloud beta container backup-restore backups create {backup_name} \
  --backup-plan={plan_name} --location={location} --quiet

# Create Restore Plan
gcloud beta container backup-restore restore-plans create {restore_plan_name} \
  --location={location} \
  --cluster=projects/{project_id}/locations/{location}/clusters/{target_cluster_name} \
  --backup-plan=projects/{project_id}/locations/{location}/backupPlans/{source_backup_plan_name} \
  --all-namespaces \
  --cluster-resource-conflict-policy=use-existing-version \
  --namespaced-resource-restore-mode=fail-on-conflict --quiet

# Execute Restore
gcloud beta container backup-restore restores create {restore_name} \
  --restore-plan={restore_plan_name} --location={location} \
  --backup=projects/{project_id}/locations/{location}/backupPlans/{source_backup_plan_name}/backups/{backup_name} \
  --quiet

# Verify Restore Status
gcloud beta container backup-restore restores describe {restore_name} \
  --restore-plan={restore_plan_name} --location={location}
```

> [!WARNING] **`--include-volume-data` and `--include-secrets` BOTH DEFAULT TO
> FALSE.** If you omit them, the backup plan silently produces **config-only
> backups** with no persistent volume snapshots and no Secrets. Always pass both
> flags explicitly when the goal is full workload protection.

Notes:

-   The `backup-restore` command group requires the `gcloud beta` component
    (`gcloud components install beta`).
-   `--cluster` requires the full resource path
    `projects/{project_id}/locations/{location}/clusters/{cluster_name}` (or
    `projects/{project_id}/zones/{zone}/clusters/{cluster_name}` for zonal
    clusters), not a bare cluster name.
-   Restore plans require exactly one namespaced-resource scope flag:
    `--all-namespaces`, `--selected-namespaces={ns1},{ns2}`,
    `--excluded-namespaces=...`, `--selected-applications=...`, or
    `--no-namespaces`.

## Restore Safety (CRITICAL)

A restore writes into a **live cluster** and, depending on the conflict policy,
can overwrite or delete existing resources:

-   `--cluster-resource-conflict-policy=use-existing-version` keeps existing
    cluster-scoped resources (safe default); `use-backup-version` **deletes**
    the existing version first — deleting a CRD deletes all of its CRs.
-   `--namespaced-resource-restore-mode=fail-on-conflict` aborts on any conflict
    (safe default); `merge-skip-on-conflict` skips conflicting resources;
    `merge-replace-on-conflict` and `merge-replace-volume-on-conflict`
    **overwrite** existing resources or volumes; `delete-and-restore` **deletes
    entire conflicting namespaces** (and all resources in them) before
    restoring.

**Rules:**

1.  Validate the restore in a non-production target cluster first.
2.  Prefer the safe defaults (`use-existing-version` + `fail-on-conflict`)
    unless the user explicitly needs to revert live resources.
3.  **Always obtain explicit user confirmation before executing a restore into a
    production cluster**, and state which conflict policy is in effect and what
    it may overwrite or delete.

## Best Practices

1.  **CMEK Encryption**: Encrypt backup plans using Customer-Managed Encryption
    Keys:
    `--encryption-key=projects/{project_id}/locations/{location}/keyRings/{ring}/cryptoKeys/{key}`.
2.  **Scope**: Prefer backing up specific namespaces rather than the entire
    cluster: `--selected-namespaces={ns1},{ns2}` (instead of
    `--all-namespaces`).
3.  **Application Consistency**: Recommend quiescing the database or pausing
    application writes (e.g. using pre-backup hooks or database-specific tools)
    prior to backups to ensure data integrity.
4.  **CSI Volume Snapshots**: Ensure that stateful backups utilize GKE's CSI
    (Container Storage Interface) driver for volume snapshots to capture
    persistent volume data.
5.  **Service Terminology**: Always explicitly refer to the service as **Backup
    for GKE** in your response. This distinguishes it from the broader (but
    complementary) Google Cloud **Backup and Disaster Recovery (DR)
    Service**, ## Golden Path Backup Defaults

The recommended production golden path configuration for Backup for GKE:

-   **Addon**: BackupRestore addon enabled
    (`--update-addons=BackupRestore=ENABLED`).
-   **Volume Inclusion**: `--include-volume-data` explicitly passed (enabled,
    since the service default is false).
-   **Secret Inclusion**: `--include-secrets` explicitly passed (enabled, since
    the service default is false).
-   **Retention**: Defined retention period (e.g. 30 days via
    `--backup-retain-days=30`).
-   **Encryption**: CMEK enabled (`--encryption-key=...`).

## Recent Changes

-   **Cross-project backup and restore (GA)**: Backup plans can store backups in
    a different project than the source cluster, and restore plans can target
    clusters in a third project. Enables centralized backup projects (with
    immutability/retention managed by a platform team) and cross-project
    environment seeding without granting access to the source project.
-   **Pricing change (effective 2026-03-02)**: The backup management fee moved
    from **pod-based** to **NAMESPACE-based** pricing — charged per non-system
    namespace in the most recent successful backup of each plan (system
    namespaces like `kube-system` are excluded). Existing committed use discount
    (CUD) holders keep pod-based management pricing until their commitment ends;
    everyone else moves to the new model. See
    https://cloud.google.com/products/backup-for-gke/pricing-changes.
-   **Smart Scheduling**: RPO-driven backup scheduling as an alternative to
    fixed cron schedules — pass `--target-rpo-minutes={minutes}` instead of
    `--cron-schedule` when creating the backup plan (optionally with RPO
    exclusion windows via `--exclusion-windows-file`).
-   **Hyperdisk support**: Backup and restore of **Hyperdisk ML** and
    **Hyperdisk Balanced High Availability** volumes is supported on GKE
    clusters running **1.33.1-gke.1959000 and later** (Hyperdisk throughput,
    extreme, and balanced types are also supported).

## Troubleshooting & Common Pitfalls (CRITICAL)

> [!IMPORTANT] **Slow Operations**: Enabling the BackupRestore addon
> (`--update-addons=BackupRestore=ENABLED`) triggers a slow Google Cloud control
> plane cluster update that takes several minutes. * **Rule**: **Do not run a
> terminal loop waiting for the GKE Backup addon to become active.** *
> **Action**: Provide the command to enable the addon, explain that the
> operation will proceed in the background, and immediately proceed to write the
> backup plan configs. Do not block.
