---
name: gke-storage-troubleshooting
metadata:
  category: Storage
  version: 1.1.0
description: Diagnoses GKE persistent-storage failures — volume attach/mount errors (Regional PD on optimized VMs, fsGroup mount timeouts), disk-performance and node storage-pressure issues, slow-disk Pod-creation failures, volume-expansion problems, Local SSD / Hyperdisk Storage Pool creation errors, and Cloud Storage FUSE OOM. Use when Pods are stuck in ContainerCreating, volumes fail to attach or mount, or nodes report storage pressure. Don't use for routine storage provisioning or StorageClass/PVC authoring (see the gke-storage skill).
skill_id: platforms/gke-storage-troubleshooting
title: GKE Storage Troubleshooting
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-storage-troubleshooting
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-storage-troubleshooting", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-storage-troubleshooting", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Storage Troubleshooting Skill

Use this skill to systematically diagnose and resolve **persistent-storage
failures** for workloads running on GKE — volume attach/mount errors, disk
performance and node storage pressure, volume expansion, storage-related
cluster/node-pool creation errors, and Cloud Storage FUSE memory issues. This
skill operates non-interactively and enforces a read-only diagnostics boundary
before proposing manifest or configuration corrections.

> For routine storage **provisioning** and StorageClass/PVC authoring, use the
> `gke-storage` skill instead. This skill focuses on **failure diagnosis**.

## 🔍 Diagnosis & Resolution Workflow

### Step 0: Non-Interactive Context Discovery & Dry-Run Fallback

1.  **Parameter Extraction**: Extract required context (`project_id`,
    `cluster_name`, `cluster_location`, `workload_name`, `workload_namespace`,
    `pod_name`, and the relevant `pvc_name` / `pv_name` / `node_name`)
    non-interactively from the user prompt, active `SETTINGS.md`, or environment
    defaults:

    -   Default `workload_namespace` to `default` if omitted.
    -   Infer missing cluster parameters from the active environment (`kubectl
        config current-context` or `gcloud config get-value project`).

2.  **Cluster Credentials & Fallback Mode**:

    -   Attempt credential fetch: `gcloud container clusters get-credentials
        {cluster_name} --location {cluster_location} --project {project_id}`.
    -   **Fallback / Dry-Run Mode**: If the cluster is unreachable,
        non-existent, or live command execution fails (such as in sandboxed
        evaluations, dry-run mode, or offline analysis):
        -   Limit retry attempts to avoid resource exhaustion and context
            overflow.
        -   Immediately present the exact `kubectl` / `gcloud` diagnostic
            commands for the human operator to run.
        -   Synthesize the root-cause analysis and output the proposed GitOps
            correction based on the reported symptoms.

--------------------------------------------------------------------------------

### Step 1: Classify the Storage Symptom

Gather the primary signals, then jump to the **matching branch under Step 2
(Resolution)** — you normally perform **only the one branch** that matches your
diagnosis, not all of them.

**Diagnostic Commands:**

```bash
kubectl describe pod {pod_name} -n {workload_namespace}
kubectl get pvc,pv -n {workload_namespace}
kubectl get events -n {workload_namespace} --sort-by='.metadata.creationTimestamp'
kubectl describe node {node_name}
```

-   **Pod stuck in `ContainerCreating` with an attach/mount event** → **Volume
    Attach & Mount Failures**.
-   **Node-level slowness, `PLEG is not healthy`, or `StoragePressureDetected`
    events** → **Disk Performance & Node Storage Pressure**.
-   **Cluster / node-pool creation or provisioning error** → **Storage
    Provisioning & Creation Failures**.
-   **A resized volume is not reflected inside the container** → **Volume
    Expansion Not Reflecting in the Container**.
-   **Cloud Storage FUSE Pod / sidecar OOM** → **Cloud Storage FUSE
    Out-Of-Memory (OOM) Events**.

--------------------------------------------------------------------------------

### Step 2: Resolution

Perform **only the branch that matches your Step 1 diagnosis**. These branches
are mutually exclusive alternatives, not sequential steps.

#### Volume Attach & Mount Failures

-   **`Error 400: Cannot attach RePD to an optimized VM`**: Regional persistent
    disks are restricted from being used with **memory-optimized** or
    **compute-optimized** machine types.

    -   If a regional PD is not a hard requirement, switch the workload to a
        **non-regional persistent disk** StorageClass.
    -   If a regional PD **is** required, use **taints and tolerations** so that
        Pods needing regional PDs are scheduled onto a node pool that does
        **not** use optimized machine types.

-   **Pods stay `Pending` / `FailedScheduling` after a node pool is moved to a
    4th-generation (N4, N4A, N4D) machine series while the workload uses a
    Persistent Disk StorageClass**: N4/N4A/N4D machines **do not support
    Persistent Disk** (they support Hyperdisk only), so a PVC bound to a `pd-*`
    StorageClass cannot bind or schedule on those nodes. Events typically show
    `FailedScheduling` with a volume node-affinity / topology conflict.

    -   Switch the workload to a **Hyperdisk** StorageClass (for example `type:
        hyperdisk-balanced`) for the Gen4 node pool.
    -   For existing Persistent Disk volumes, migrate the data to a Hyperdisk
        volume; the original PD cannot be attached to a Gen4 node.
    -   If the workload must keep Persistent Disk, keep it on a PD-capable
        machine series (for example N2) via node selection. This is a
        machine-type/disk-type incompatibility, **not** a capacity problem, so
        increasing disk size or quota does not help.

-   **Hyperdisk Pods become unschedulable when a compute class falls back across
    VM generations (for example N4 priority, N2 fallback), or one StorageClass
    must serve mixed generations**: a single static disk type in the
    StorageClass is not compatible with every machine series in the fallback
    list, so Pods cannot bind their volume on the fallback nodes.

    -   Use **automated disk type selection**: set the StorageClass
        `parameters.type` to `dynamic` with `hyperdisk-type`, `pd-type`, and
        `disk-type-preference`, plus `use-allowed-disk-topology: "true"`, so GKE
        selects a compatible disk type per node and schedules Pods only onto
        nodes that support it. One dynamic StorageClass can then span multiple
        VM generations (requires the GKE versions noted in the docs).

        Example dynamic StorageClass (GKE 1.35.3-gke.1290000+):

        ```yaml
        apiVersion: storage.k8s.io/v1
        kind: StorageClass
        metadata:
          name: dynamic-volume
        provisioner: pd.csi.storage.gke.io
        volumeBindingMode: WaitForFirstConsumer
        allowVolumeExpansion: true
        parameters:
          type: dynamic
          pd-type: pd-balanced
          hyperdisk-type: hyperdisk-balanced
          # Preferred storage on nodes that support both PD and Hyperdisk;
          # defaults to hyperdisk-type when omitted.
          disk-type-preference: hyperdisk-type
          # Best practice: schedule Pods only onto nodes that support the disk type.
          use-allowed-disk-topology: "true"
        ```

-   **Mount stops responding due to the `fsGroup` setting**: A Pod configured
    with a `securityContext.fsGroup` on a volume that contains a **large number
    of files** makes the kubelet recursively change ownership on every file,
    which can time out the mount. The symptom is:

    ```
    Unable to attach or mount volumes for pod; skipping pod ... timed out waiting for the condition
    ```

    Confirm by checking the Pod logs for a `Setting volume ownership for ... and
    fsGroup set` entry, then apply one of:

    -   Reduce the number of files in the volume.
    -   Set `securityContext.fsGroupChangePolicy: OnRootMismatch` so ownership
        is only changed when the top-level permissions do not match.
    -   Stop using the `fsGroup` setting if it is not required.

#### Disk Performance & Node Storage Pressure

-   **Poor disk performance** (symptoms such as `task dockerd:... blocked for
    more than 300 seconds`, `PLEG is not healthy`, or slow `fs: disk usage`
    scans): the node boot disk is shared across the OS, container images, the
    overlay filesystem, and disk-backed `emptyDir` volumes, and performance is
    shared across all disks of the same type on the node.

    -   This commonly affects nodes using **standard persistent disks smaller
        than 200 GB**. Increase the disk size or switch to SSD, especially for
        production.
    -   Enable **Local SSD for ephemeral storage** on node pools whose workloads
        frequently use `emptyDir`.

-   **Slow disk operations cause Pod creation failures**: on affected node
    versions (GKE 1.18–1.23 before the fixed patch releases), the `k8s_node
    container-runtime` logs show `failed to reserve container name ... is
    reserved for ...` (containerd issue #4604).

    -   Mitigate with `restartPolicy: Always` or `OnFailure` in the PodSpec, and
        increase boot-disk IOPS (larger disk or a faster disk type).
    -   The permanent fix is containerd 1.6.0+; upgrade to a GKE version that
        includes it.

-   **`StoragePressureDetected` (high node storage pressure)**: node condition
    `StoragePressureRootFileSystem` becomes `True` (for example, `Disk
    /dev/nvme0n1 usage 89% exceeds threshold 85%`), caused by excessive
    `emptyDir` writes, large image pulls, or accumulating logs.

    -   Identify usage with `df -h` on the affected node (focus on
        `/mnt/stateful_partition` and ephemeral mounts).
    -   Remediate by using larger boot disks, adding Local SSDs for ephemeral
        storage, setting appropriate `ephemeral-storage` requests/limits, and
        cleaning up unused files/images/logs.

#### Storage Provisioning & Creation Failures

-   **`The selected machine type ... has a fixed number of local SSD(s)`**: the
    Local SSD count specified in `EphemeralStorageLocalSsdConfig` /
    `LocalNvmeSsdBlockConfig` does not match the fixed count included with the
    machine type.

    -   Specify a Local SSD count that matches the machine type. For
        **third-generation** machine series, omit the Local SSD `count` flag and
        the correct value is configured automatically.

-   **Hyperdisk Storage Pools: cluster or node-pool creation fails** with
    `ZONE_RESOURCE_POOL_EXHAUSTED` (or similar Compute Engine resource errors):
    the target zone lacks capacity for the requested Hyperdisk Balanced disks or
    machine type.

    -   Select a new zone in the same region that has capacity and where
        Hyperdisk Balanced Storage Pools are available. Because storage pools
        are **zonal**, delete and recreate the pool in the new zone, then create
        the cluster/node pool there.

#### Volume Expansion Not Reflecting in the Container

Volume expansion must always be driven through the **PersistentVolumeClaim**.
Editing the PersistentVolume directly can leave the container filesystem on the
old size.

1.  Keep the modified PersistentVolume object as it is.
2.  Edit the **PersistentVolumeClaim** and set `spec.resources.requests.storage`
    to a value **higher** than the current PersistentVolume size.
3.  The kubelet then resizes the PV, PVC, and container filesystem
    automatically. Verify inside the Pod:

    ```bash
    kubectl exec {pod_name} -n {workload_namespace} -- df -h
    ```

#### Cloud Storage FUSE Out-Of-Memory (OOM) Events

If Pods experience high memory use or OOM kills related to the Cloud Storage
FUSE CSI driver:

1.  Enable CPU/memory snapshots by configuring **Cloud Profiler** on the Cloud
    Storage FUSE CSI driver sidecar container.
2.  Locate the OOM event in **Cloud Logging**, filtering by Pod:

    ```
    jsonPayload.involvedObject.name="{pod_name}"
    jsonPayload.involvedObject.kind="Pod"
    OOMKilled
    ```

    If the sidecar mounter or GCSFuse process OOMs, the Pod name is the workload
    Pod's name; if the node driver OOMs, it is `gcsfusecsi-node-*`.
3.  Extract the Pod UID (`jsonPayload.involvedObject.uid`) and timestamp, then
    analyze the matching snapshot in **Cloud Profiler** using the
    `{pod_name}_{pod_uid}` Service Version at the OOM timestamp.

--------------------------------------------------------------------------------

### Step 3: Propose the GitOps Correction

Enforce the read-only diagnostics boundary: **do not** apply live mutations with
`kubectl edit`, `kubectl patch`, or `kubectl apply`. Instead, present the
corrected StorageClass, PersistentVolumeClaim, PodSpec (`securityContext`,
`restartPolicy`), or node-pool configuration as a reviewable patch to be applied
through the user's GitOps pipeline (for example, Config Sync, Argo CD, or Flux).

## References

-   [Troubleshoot storage in GKE](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/storage.md.txt)
-   [Regional persistent disk restrictions](https://docs.cloud.google.com/compute/docs/disks.md.txt#restrictions_2)
-   [Configure ephemeral storage with local SSDs](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/local-ssd.md.txt)
-   [Cloud Storage FUSE CSI driver sidecar (enable Profiler)](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cloud-storage-fuse-csi-driver-sidecar.md.txt#enable-profiler)
-   [Hyperdisk Storage Pools](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/hyperdisk-storage-pools.md.txt)
-   [N4 machine series storage support (Persistent Disk not supported)](https://docs.cloud.google.com/compute/docs/general-purpose-machines.md.txt)
-   [Hyperdisk machine type support](https://docs.cloud.google.com/compute/docs/disks/hyperdisks.md.txt)
-   [Hyperdisk automated disk type selection (dynamic StorageClass)](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/hyperdisk.md.txt#automated_disk_type_selection)
