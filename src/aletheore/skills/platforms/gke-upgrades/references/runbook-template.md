# Runbook Command Templates

Standard command sequences for GKE upgrades. Replace placeholders: `CLUSTER_NAME`, `ZONE`, `TARGET_VERSION`, `NODE_POOL_NAME`.

## Table of Contents
- [Pre-flight](#pre-flight) (Line 12-31)
- [Control plane upgrade](#control-plane-upgrade) (Line 32-47)
- [Node pool upgrade (Standard only)](#node-pool-upgrade-standard-only) (Line 48-72)
- [Maintenance window configuration](#maintenance-window-configuration) (Line 73-110)
- [Rollback/Downgrade guidance](#rollbackdowngrade-guidance) (Line 111-151)

## Pre-flight

```bash
# Current versions
gcloud container clusters describe CLUSTER_NAME \
  --zone ZONE \
  --format="table(name, currentMasterVersion, nodePools[].version)"

# Available versions for channel
gcloud container get-server-config --zone ZONE \
  --format="yaml(channels)"

# Deprecated API usage
kubectl get --raw /metrics | grep apiserver_request_total | grep deprecated

# Cluster health
kubectl get nodes
kubectl get pods -A | grep -v Running | grep -v Completed
```

## Control plane upgrade

```bash
gcloud container clusters upgrade CLUSTER_NAME \
  --zone ZONE \
  --master \
  --cluster-version TARGET_VERSION

# Verify (wait ~10-15 min)
gcloud container clusters describe CLUSTER_NAME \
  --zone ZONE \
  --format="value(currentMasterVersion)"

kubectl get pods -n kube-system
```

## Node pool upgrade (Standard only)

```bash
# Configure surge settings
gcloud container node-pools update NODE_POOL_NAME \
  --cluster CLUSTER_NAME \
  --zone ZONE \
  --max-surge-upgrade MAX_SURGE \
  --max-unavailable-upgrade MAX_UNAVAILABLE

# Upgrade (note: node pool upgrades use `clusters upgrade --node-pool`;
# there is no `gcloud container node-pools upgrade` command)
gcloud container clusters upgrade CLUSTER_NAME \
  --zone ZONE \
  --node-pool NODE_POOL_NAME \
  --cluster-version TARGET_VERSION

# Monitor progress
watch 'kubectl get nodes -o wide -L cloud.google.com/gke-nodepool'

# Verify
gcloud container node-pools list --cluster CLUSTER_NAME --zone ZONE
kubectl get pods -A | grep -v Running | grep -v Completed
```

## Maintenance window configuration

```bash
# Set recurring maintenance window
gcloud container clusters update CLUSTER_NAME \
  --zone ZONE \
  --maintenance-window-start YYYY-MM-DDTHH:MM:SSZ \
  --maintenance-window-end YYYY-MM-DDTHH:MM:SSZ \
  --maintenance-window-recurrence "FREQ=WEEKLY;BYDAY=SA"

# Add maintenance exclusion (up to 90 days)
gcloud container clusters update CLUSTER_NAME \
  --zone ZONE \
  --add-maintenance-exclusion-name="EXCLUSION_NAME" \
  --add-maintenance-exclusion-start=START_TIME \
  --add-maintenance-exclusion-end=END_TIME

# Add persistent maintenance exclusion (until End of Support)
gcloud container clusters update CLUSTER_NAME \
  --zone ZONE \
  --add-maintenance-exclusion-name="EXCLUSION_NAME" \
  --add-maintenance-exclusion-start=START_TIME \
  --add-maintenance-exclusion-until-end-of-support \
  --add-maintenance-exclusion-scope=no_upgrades

# Add node pool level exclusion (during creation)
gcloud container node-pools create NODE_POOL_NAME \
  --cluster CLUSTER_NAME \
  --zone ZONE \
  --add-maintenance-exclusion-until-end-of-support

# Add node pool level exclusion (existing pool)
gcloud container node-pools update NODE_POOL_NAME \
  --cluster CLUSTER_NAME \
  --zone ZONE \
  --add-maintenance-exclusion-until-end-of-support
```

## Rollback/Downgrade guidance

- **Control Plane Patches**: Can be downgraded by running the upgrade command with the target older patch version.
- **Control Plane Minors**: Rollback is only available during the first step of the 2-step upgrade process.
- **Node Pools (Minor & Patch)**: Can be downgraded directly by running the node pool upgrade command targeting the older version, OR by creating a new pool at the old version and migrating workloads (safer).

### Downgrade Control Plane (Patch or Step-1 Minor)
```bash
gcloud container clusters upgrade CLUSTER_NAME \
  --master \
  --zone ZONE \
  --cluster-version TARGET_PREVIOUS_VERSION
```

### Downgrade Node Pool (Direct)
```bash
gcloud container clusters upgrade CLUSTER_NAME \
  --zone ZONE \
  --node-pool NODE_POOL_NAME \
  --cluster-version TARGET_PREVIOUS_VERSION
```

### Downgrade Node Pool (Safe migration - recommended)
```bash
# Create replacement node pool at previous version
gcloud container node-pools create NODE_POOL_NAME-rollback \
  --cluster CLUSTER_NAME \
  --zone ZONE \
  --cluster-version PREVIOUS_VERSION \
  --num-nodes NUM_NODES \
  --machine-type MACHINE_TYPE

# Cordon old pool
kubectl cordon -l cloud.google.com/gke-nodepool=NODE_POOL_NAME

# Record current PDB state BEFORE touching anything — this file is how you put
# it back. Do not skip; a relaxed PDB left in place is an outage waiting for
# the next voluntary disruption.
kubectl get pdb -A -o yaml > /tmp/pdb-backup-$(date +%s).yaml
kubectl get pdb -A   # ALLOWED DISRUPTIONS = 0 will block the drain

# Drain old pool to migrate workloads (respects PDBs)
kubectl drain -l cloud.google.com/gke-nodepool=NODE_POOL_NAME \
  --ignore-daemonsets --delete-emptydir-data
```

If a PDB blocks the drain, relax it **temporarily** and restore it as a required
step of the same runbook — never as a follow-up someone may forget:

```bash
# 1. Relax the blocking PDB (only after confirming the replacement pool is
#    Ready and can accept the workload)
kubectl patch pdb PDB_NAME -n NAMESPACE \
  --type merge -p '{"spec":{"maxUnavailable":"100%"}}'

# 2. Drain, then verify workloads are Running on the replacement pool
kubectl get pods -o wide --field-selector spec.nodeName!='' -A | grep NODE_POOL_NAME-rollback

# 3. RESTORE the original PDB — mandatory, not optional. Re-apply from the
#    backup rather than retyping the values.
kubectl apply -f /tmp/pdb-backup-TIMESTAMP.yaml

# 4. Confirm the restore took effect
kubectl get pdb -A   # ALLOWED DISRUPTIONS should match the pre-drain values
```

> A rollback runbook that relaxes PDBs without restoring them has silently
> removed the cluster's disruption protection. Always include steps 3 and 4.
