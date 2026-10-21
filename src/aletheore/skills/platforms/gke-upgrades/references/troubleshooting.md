# Troubleshooting GKE Upgrade Issues

## Diagnostic flowchart

## Table of Contents
- [Diagnostic flowchart](#diagnostic-flowchart) (Line 3-21)
- [1. PDB blocking drain (most common)](#1-pdb-blocking-drain-most-common) (Line 23-44)
- [2. Resource constraints (no room for pods)](#2-resource-constraints-no-room-for-pods) (Line 46-65)
- [3. Bare pods blocking drain](#3-bare-pods-blocking-drain) (Line 67-75)
- [4. Admission webhooks rejecting pod creation](#4-admission-webhooks-rejecting-pod-creation) (Line 77-97)
- [5. PVC attachment issues](#5-pvc-attachment-issues) (Line 99-107)
- [6. Long termination grace periods](#6-long-termination-grace-periods) (Line 119-126)
- [7. Upgrade operation stuck at GKE level](#7-upgrade-operation-stuck-at-gke-level) (Line 119-126)
- [8. Stockout during critical upgrades (e.g. cert expiration)](#8-stockout-during-critical-upgrades-eg-cert-expiration) (Line 128-154)
- [9. GPU node upgrade regressions (CrashLoopBackOff, driver issues)](#9-gpu-node-upgrade-regressions-crashloopbackoff-driver-issues) (Line 156-195)
- [10. Upgrade paused or partially completed (check auto-upgrade status)](#10-upgrade-paused-or-partially-completed-check-auto-upgrade-status) (Line 197-228)
- [11. Upgrade too slow or too disruptive (tune blue-green soak)](#11-upgrade-too-slow-or-too-disruptive-tune-blue-green-soak) (Line 230-248)
- [12. New nodes fail to become Ready / control plane unhealthy](#12-new-nodes-fail-to-become-ready--control-plane-unhealthy) (Line 250-266)
- [Validation after applying a fix](#validation-after-applying-a-fix) (Line 268-279)

When an upgrade is stuck or failing, work through these checks in order. Each section has the diagnosis command, what to look for, and the fix.

## 1. PDB blocking drain (most common)

**Diagnose:**
```bash
kubectl get pdb -A -o wide
# Look for ALLOWED DISRUPTIONS = 0
kubectl describe pdb PDB_NAME -n NAMESPACE
```

**Fix — temporarily relax the PDB:**
```bash
# Option A: Allow all disruptions temporarily
kubectl patch pdb PDB_NAME -n NAMESPACE \
  -p '{"spec":{"minAvailable":null,"maxUnavailable":"100%"}}'

# Option B: Back up and edit
kubectl get pdb PDB_NAME -n NAMESPACE -o yaml > pdb-backup.yaml
# Edit minAvailable/maxUnavailable, then:
kubectl apply -f pdb-backup.yaml
```

Restore original PDB after upgrade completes.

## 2. Resource constraints (no room for pods)

**Diagnose:**
```bash
kubectl get pods -A | grep Pending
kubectl get events -A --field-selector reason=FailedScheduling
kubectl top nodes
kubectl describe nodes | grep -A 5 "Allocated resources"
```

**Fix — increase surge capacity:**
```bash
gcloud container node-pools update NODE_POOL_NAME \
  --cluster CLUSTER_NAME \
  --zone ZONE \
  --max-surge-upgrade 2 \
  --max-unavailable-upgrade 0
```

Or scale down non-critical workloads temporarily.

## 3. Bare pods blocking drain

**Diagnose:**
```bash
kubectl get pods -A -o json | \
  jq -r '.items[] | select(.metadata.ownerReferences | length == 0) | "\(.metadata.namespace)/\(.metadata.name)"'
```

**Fix:** Delete bare pods (they won't reschedule anyway) or wrap in Deployments.

## 4. Admission webhooks rejecting pod creation

**Diagnose:**
```bash
kubectl get validatingwebhookconfigurations
kubectl get mutatingwebhookconfigurations
# Check for webhooks matching broad API groups
kubectl describe validatingwebhookconfigurations WEBHOOK_NAME
```

**Fix — temporarily disable problematic webhook:**
```bash
# Back up the webhook configuration first
kubectl get validatingwebhookconfigurations WEBHOOK_NAME -o yaml > webhook-backup.yaml

# Then delete temporarily
kubectl delete validatingwebhookconfigurations WEBHOOK_NAME

# Re-create after upgrade
kubectl apply -f webhook-backup.yaml
```

## 5. PVC attachment issues

**Diagnose:**
```bash
kubectl get pvc -A | grep -v Bound
kubectl get events -A --field-selector reason=FailedAttachVolume
```

**Fix:** Check if volumes are zone-locked. For regional clusters, PVs may need to be in the same zone as the new node. Consider migrating workloads to already-upgraded nodes.

## 6. Long termination grace periods

**Diagnose:**
```bash
kubectl get pods -A -o json | \
  jq '.items[] | select(.spec.terminationGracePeriodSeconds > 120) | {ns:.metadata.namespace, name:.metadata.name, grace:.spec.terminationGracePeriodSeconds}'
```

**Fix:** Reduce `terminationGracePeriodSeconds` in the workload spec if possible. GKE waits up to 1 hour for pod eviction during surge upgrades.

## 7. Upgrade operation stuck at GKE level

**Diagnose:**
```bash
gcloud container operations list --cluster CLUSTER_NAME --zone ZONE --filter="operationType=UPGRADE_NODES"
```

**Fix:** If the operation shows no progress for >2 hours after resolving pod-level issues, contact GKE support with cluster name, zone, and operation ID.

## 8. Stockout during critical upgrades (e.g. cert expiration)

**Diagnose:**
Upgrade is failing with `ZONE_RESOURCE_POOL_EXHAUSTED` or `QUOTA_EXCEEDED` errors, and the cluster has a critical pending deadline (e.g., control plane certificate expiring soon).

**Fix:**
1. **Change Upgrade Strategy**: Modify the node pool to use a rolling in-place upgrade (no surge) to bypass quota limits:
   ```bash
   gcloud container node-pools update NODE_POOL_NAME \
     --cluster CLUSTER_NAME \
     --zone ZONE \
     --max-surge-upgrade 0 \
     --max-unavailable-upgrade 1
   ```
2. **Open Support Case**: Immediately open a P1/P2 Google Cloud Support case, citing urgent certificate expiration and stockout.
3. **Retry in Different Zone/Region**: If the cluster is regional or multi-zonal, check if you can retry the upgrade in a different zone that might have capacity, or add a temporary node pool in a different zone/region to migrate workloads.
4. **Credential Rotation**: Perform a control plane credential rotation to renew certificates without upgrading the GKE version.
   ```bash
   gcloud container clusters update CLUSTER_NAME --start-credential-rotation --zone ZONE
   # Follow standard GKE documentation to complete the rotation.
   ```
5. **Enable DNS Endpoint**: If client connectivity is failing or at risk due to expired client certificates, enable the DNS-based control plane endpoint to allow IAM-based authentication.
   ```bash
   gcloud container clusters update CLUSTER_NAME --enable-dns-access --zone ZONE
   # Get credentials using DNS endpoint:
   gcloud container clusters get-credentials CLUSTER_NAME --dns-endpoint --zone ZONE
   ```

## 9. GPU node upgrade regressions (CrashLoopBackOff, driver issues)

**Diagnose:**
GPU nodes upgrade successfully, but ML pods are stuck in `CrashLoopBackOff` with `SIGSEGV` or driver initialization errors.

1. **Compare Node Metadata**: Check if the OS image, kernel version (`uname -r`), or NVIDIA driver version changed and differs between working (old) and non-working (new) nodes.
2. **Verify Driver Installer Logs**: Check logs of the `nvidia-driver-installer` container in the `nvidia-gpu-device-plugin` pod on the new node.
3. **Test GPU Access**: Deploy a simple test pod to verify if the GPU is accessible with the current driver:
   ```yaml
   apiVersion: v1
   kind: Pod
   metadata:
     name: gpu-test-vectoradd
   spec:
     containers:
     - name: vectoradd
       # Pin a tag whose CUDA version matches your node's driver
       image: nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda11.7.1-ubuntu20.04
       resources:
         limits:
           nvidia.com/gpu: 1
     restartPolicy: Never
   ```

**Fix:**
1. **Pin Driver Version**: If the default driver version changed, update your node pool configuration to pin to the previous working driver version (e.g., `R535`):
   ```bash
   gcloud container node-pools update NODE_POOL_NAME \
     --cluster CLUSTER_NAME \
     --zone ZONE \
     --accelerator type=GPU_TYPE,count=COUNT,gpu-driver-version=DRIVER_VERSION
   ```
2. **Update Workload Dependencies**: Rebuild container images with a CUDA version compatible with the new driver.
3. **Rollback Node Pool**: If production is blocked, roll back the node pool to the previous GKE version:
   ```bash
   gcloud container node-pools upgrade NODE_POOL_NAME \
     --cluster CLUSTER_NAME \
     --zone ZONE \
     --cluster-version PREVIOUS_VERSION
   ```

## 10. Upgrade paused or partially completed (check auto-upgrade status)

Before assuming a bug, check whether GKE has intentionally **paused** the upgrade.

**Diagnose:**
```bash
# Cluster-level upgrade status and paused reason
gcloud container clusters get-upgrade-info CLUSTER_NAME --location LOCATION

# Per node pool (Standard clusters)
gcloud container node-pools get-upgrade-info POOL_NAME --cluster CLUSTER_NAME --location LOCATION
```

**Auto-upgrade status values:**
- `ACTIVE` — upgrades proceeding normally.
- `MINOR_UPGRADE_PAUSED` — minor-version upgrades are paused.
- `UPGRADE_PAUSED` — all automatic upgrades are paused.

**Common paused reasons:**
- `MAINTENANCE_WINDOW` — a maintenance window is preventing upgrades.
- `MAINTENANCE_EXCLUSION_*` — a maintenance exclusion is blocking upgrades (suffix = scope, e.g. `MAINTENANCE_EXCLUSION_NO_UPGRADES`).
- `CLUSTER_DISRUPTION_BUDGET` / `CLUSTER_DISRUPTION_BUDGET_MINOR_UPGRADE` — a post-operation cooldown protecting cluster stability.
- `SYSTEM_CONFIG` — temporarily paused by GKE for technical or business reasons. **Do not force a manual upgrade unless it is required.**

**Fix — resume a canceled/partially-completed node pool upgrade** by re-issuing the same upgrade:
```bash
gcloud container clusters upgrade CLUSTER_NAME \
  --node-pool=NODE_POOL_NAME \
  --location=LOCATION \
  --cluster-version VERSION
```
A partially-upgraded node pool runs mixed versions until you resume it or roll it back.

## 11. Upgrade too slow or too disruptive (tune blue-green soak)

Blue-green upgrades add batch/soak controls that surge upgrades don't have. Use them when an upgrade churns nodes too aggressively, or when the soak wait is longer than needed.

**Parameters:**
- `BATCH_NODE_COUNT` / `BATCH_PERCENT` — how many blue nodes drain per batch (default `BATCH_NODE_COUNT=1`; set either to `0` to skip the batched drain phase).
- `BATCH_SOAK_DURATION` — wait after each batch drain (default `0s`).
- `NODE_POOL_SOAK_DURATION` — wait after all batches drain, before the blue pool is deleted (default `3600s`).

**Update an existing node pool:**
```bash
gcloud container node-pools update NODE_POOL_NAME \
  --cluster CLUSTER_NAME --location LOCATION \
  --enable-blue-green-upgrade \
  --standard-rollout-policy=batch-node-count=2,batch-soak-duration=10s \
  --node-pool-soak-duration=600s
```

**Maintenance-window interaction:** surge upgrades **pause** when they run past the maintenance window and resume in the next one; blue-green upgrades **continue to completion** even past the window, and the extra pool keeps workloads available — prefer blue-green when a window is too short to finish.

## 12. New nodes fail to become Ready / control plane unhealthy

An upgrade can stall because replacement nodes never reach `Ready`, or because the control plane itself is unhealthy.

**Diagnose:**
```bash
kubectl get nodes -o wide          # look for NotReady / SchedulingDisabled
kubectl describe node NODE_NAME    # Conditions + Events
```

**Common node-level causes of an incomplete upgrade:** new nodes failing to register, IP address exhaustion in the pod/node ranges, or insufficient resource quota. For a node stuck `NotReady`, follow the dedicated node-NotReady flow (kubelet / container-runtime / networking).

**Control plane:** during a control-plane upgrade GKE re-creates the API server; a firewall or webhook that blocks the new control plane can fail the upgrade, and a control plane that stays unhealthy causes the operation to fail (often transient — GKE retries). Confirm control-plane/node **version skew** stays within two minor versions:
```bash
gcloud container clusters describe CLUSTER_NAME --location LOCATION \
  --format="value(currentMasterVersion,currentNodeVersion)"
```

## Validation after applying a fix

```bash
# Monitor node upgrade progress
watch 'kubectl get nodes -o wide | grep -E "NAME|CURRENT_VERSION|TARGET_VERSION"'

# Check no pods stuck
kubectl get pods -A | grep -E "Terminating|Pending"

# Confirm upgrade resuming
gcloud container operations list --cluster CLUSTER_NAME --zone ZONE --limit=1
```
