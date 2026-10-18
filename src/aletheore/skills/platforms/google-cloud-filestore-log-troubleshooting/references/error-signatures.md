<!-- disableFinding(LINE_OVER_80) -->
<!-- disableFinding(LIST_NO_LINE) -->
<!-- disableFinding(LINK_ID) -->

# Google Cloud Filestore Client Mount Error Signatures & Matrix

This reference maps common Filestore NFS mount errors, permission denials, and
connection timeouts to their underlying root causes, authoritative proofs, and
verified remediations.

## Table of Contents

*   [1. Error Signatures & Troubleshooting Matrix](#1-error-signatures--troubleshooting-matrix):
    Lines 21-52
*   [2. GKE Node vs. Pod IP Architectural Reality](#2-gke-node-vs-pod-ip-architectural-reality):
    Lines 55-92
*   [3. NFS Squash Modes & Permission Failures](#3-nfs-squash-modes--permission-failures):
    Lines 96-113

--------------------------------------------------------------------------------

## 1. Error Signatures & Troubleshooting Matrix

| Error Message / Exit Code      | Diagnostic Layer       | Root Cause                     | Authoritative Proof                         | Resolution                         |
| :----------------------------- | :--------------------- | :----------------------------- | :------------------------------------------ | :--------------------------------- |
| **`ETIMEDOUT`**<br>`mount.nfs: | **VPC Network &        | VPC ingress firewall does not  | `gcloud compute firewall-rules list` shows  | Create VPC ingress firewall rule   |
: Connection timed               : Firewall**             : permit TCP port `2049` (and    : no ingress rule allowing TCP 2049 from the  : allowing TCP/UDP 2049 and 111 from :
: out`<br>`mount.nfs\: Network   :                        : `111`), or VPC peering / PSA   : client CIDR to Filestore IP.                : client subnet to the target        :
: is unreachable`                :                        : route propagation is missing.  :                                             : network.                           :
| **`EACCES`**<br>`mount.nfs:    | **Export ACL           | Client IP (or GKE Node IP) is  | Comparing Client IP against `gcloud         | Update `nfsExportOptions` to       |
: access denied by server while  : (`nfsExportOptions`)** : not included in                : filestore instances describe` export        : append client subnet CIDR          :
: mounting`                      :                        : `nfsExportOptions[].ipRanges`. : options reveals no CIDR match.              : non-destructively without wiping   :
:                                :                        :                                :                                             : existing entries.                  :
| **`EROFS`**<br>`Read-only file | **Export Access Mode** | Share is exported with         | `nfsExportOptions` has `accessMode:         | Change `accessMode` to             |
: system`                        :                        : `accessMode\: READ_ONLY` while : READ_ONLY` for the client IP range.         : `READ_WRITE` in `nfsExportOptions` :
:                                :                        : client attempts write          :                                             : for the target CIDR.               :
:                                :                        : operations (`rw`).             :                                             :                                    :
| **`EPERM`**<br>`Permission     | **POSIX / Root         | Container/VM user is UID 0     | Client mount succeeds, but file             | Set `squashMode` to                |
: denied (Operation not          : Squash**               : (root) and `squashMode` is set : write/create fails inside the mounted       : `NO_ROOT_SQUASH` in export options :
: permitted)`                    :                        : to `ROOT_SQUASH`, or non-root  : volume.                                     : or adjust container                :
:                                :                        : user lacks POSIX permissions.  :                                             : `securityContext.fsGroup` / POSIX  :
:                                :                        :                                :                                             : directory permissions (`chmod 775` :
:                                :                        :                                :                                             : or `chown`).                       :
| **`RPC: Program not            | **Portmapper /         | TCP/UDP port `111` is blocked  | Port 2049 may be open, but port 111 shows   | Add port `111` to the VPC ingress  |
: registered`**                  : RPCbind**              : by VPC firewall, preventing    : closed/filtered via network inspection.     : firewall allow rule alongside port :
:                                :                        : NFSv3 port mapping             :                                             : `2049`.                            :
:                                :                        : negotiation.                   :                                             :                                    :
| **`EBUSY` / IOPS Quota         | **Capacity &           | Storage capacity is 100% full, | Cloud Monitoring metric                     | Scale up capacity via              |
: Stalls**                       : Throughput             : or client workload is          : `file.googleapis.com/nfs/server/used_bytes` : `google-cloud-filestore-autoscale` :
:                                : Saturation**           : saturating the IOPS /          : equals capacity, or write IOPS near tier    : or upgrade instance tier (e.g.     :
:                                :                        : throughput limits of the       : ceiling.                                    : Basic HDD to Basic SSD /           :
:                                :                        : instance tier.                 :                                             : Regional).                         :

--------------------------------------------------------------------------------

## 2. GKE Node vs. Pod IP Architectural Reality

Understanding Kubernetes mount behavior is essential for diagnosing Filestore
mounts in GKE:

```
┌────────────────────────────────────────────────────────┐
│ GKE Worker Node (Internal IP: 10.128.0.12)             │
│                                                        │
│  ┌───────────────────────┐   ┌──────────────────────┐  │
│  │ Pod A (10.4.1.15)     │   │ Pod B (10.4.1.16)    │  │
│  │ VolumeMount: /data    │   │ VolumeMount: /data   │  │
│  └───────────┬───────────┘   └──────────┬───────────┘  │
│              │                          │              │
│              └────────────┬─────────────┘              │
│                           ▼                            │
│           Host Kubelet / CSI Node Plugin               │
│           (NFS mount executed in host namespace)       │
└───────────────────────────┬────────────────────────────┘
                            │ Source IP: 10.128.0.12 (Node IP)
                            │ NOT Pod IP (10.4.1.x)
                            ▼
           ┌─────────────────────────────────┐
           │ Google Cloud Filestore Instance │
           │ (nfsExportOptions evaluation)   │
           └─────────────────────────────────┘
```

*   **Host-Level Mounting**: In Kubernetes, NFS PersistentVolumes are mounted
    directly on the **host GKE Node** by `kubelet` and the CSI node plugin
    (`gcp-filestore-driver`), which operate in the host root network namespace.
*   **Source IP Behavior**: The source IP seen by the Filestore NFS server is
    **always the GKE Node Internal IP** (from the node subnet), **never the Pod
    IP**.
*   **The Common Misconfiguration**: Allowlisting GKE Pod CIDRs (e.g.
    `10.4.0.0/14`) in `nfsExportOptions` causes `mount.nfs: access denied by
    server while mounting` (`EACCES`). You must allowlist the **GKE Node Subnet
    CIDR** (e.g. `10.128.0.0/20`).

--------------------------------------------------------------------------------

## 3. NFS Squash Modes & Permission Failures

Filestore supports two NFS export squash modes:

1.  **`NO_ROOT_SQUASH`**:
    *   Requests from UID 0 (root) on the client remain UID 0 on the Filestore
        share.
    *   Required for Docker / Kubernetes containers running as `root` that need
        to create root-owned files or initialize databases.
2.  **`ROOT_SQUASH`** (Default in many enterprise security profiles):
    *   Requests from UID 0 on the client are mapped to the unprivileged
        `nobody` account (`anonUid: 65534`).
    *   If the root directory of the share is owned by `root:root` with
        permissions `0755`, a client container running as root will receive
        `EACCES` or `EPERM` when trying to write to the root of the volume.
    *   **Resolution**: Set `squashMode: NO_ROOT_SQUASH` in `nfsExportOptions`,
        or configure non-root application users matching directory POSIX
        ownership.
