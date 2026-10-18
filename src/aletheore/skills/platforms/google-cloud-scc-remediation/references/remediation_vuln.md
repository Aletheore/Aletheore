# Software Vulnerability Remediation Playbook (`remediation_vuln.md`)

Use this playbook when remediating Security Command Center findings related to
`VULNERABILITY`, such as unpatched OS packages, vulnerable container base
images, or outdated GKE node pools.

**Usage and consent**: This playbook is a reference for the
`google-cloud-scc-remediation` skill and inherits that skill's consent gate,
even if you reached this file directly. Every mutating command below MUST be
presented to the user as part of a remediation plan, together with its
verification command, and MUST NOT be executed until the user answers "Do you
approve executing this remediation plan?". Never execute a mutating command in
the same turn you propose it.

## Table of Contents

*   [1. GKE Node Pool Vulnerabilities](#1-gke-node-pool-vulnerabilities)
*   [2. Compute Engine VM OS Package Vulnerabilities](#2-compute-engine-vm-os-package-vulnerabilities)
*   [3. Container Image & Artifact Registry Vulnerabilities](#3-container-image--artifact-registry-vulnerabilities)

## 1. GKE Node Pool Vulnerabilities

When a Security Command Center vulnerability finding affects a GKE node pool
running an outdated or vulnerable Kubernetes version / OS image:

### A. Check Current Node Pool Version

```bash
gcloud container node-pools describe {NODE_POOL_NAME} \
    --project={PROJECT_ID} \
    --cluster={CLUSTER_NAME} \
    --region={REGION} \
    --format="get(version,config.imageType)"
```

### B. Remediation: Upgrade GKE Node Pool

*Note: GKE node pools cannot be upgraded to a Kubernetes version higher than the
cluster control plane. If the target patch version requires a newer minor
version, upgrade the control plane (`--master`) first.*

```bash
# Upgrade the node pool to the latest supported patch version
gcloud container clusters upgrade {CLUSTER_NAME} \
    --project={PROJECT_ID} \
    --node-pool={NODE_POOL_NAME} \
    --region={REGION}
```

A node pool upgrade cannot be rolled back: GKE does not support downgrading a
node pool to an earlier version. Treat this step as irreversible and confirm the
target version with the user before executing.

### C. Verification

Verify that the node pool has successfully upgraded to the patched version:

```bash
gcloud container node-pools describe {NODE_POOL_NAME} \
    --project={PROJECT_ID} \
    --cluster={CLUSTER_NAME} \
    --region={REGION} \
    --format="get(version)"
```

## 2. Compute Engine VM OS Package Vulnerabilities

When a Security Command Center finding identifies CVEs in OS packages (e.g.,
`openssl`, `kernel`, `glibc`) on Compute Engine VMs:

### A. Remediation using OS Config Patch Management (Preferred for Production)

```bash
# Execute an on-demand OS patch job for the target instance.
# --instance-filter-names requires the URI form; a bare instance name matches
# zero instances and the patch job silently succeeds without patching anything.
gcloud compute os-config patch-jobs execute \
    --project={PROJECT_ID} \
    --instance-filter-names="zones/{ZONE}/instances/{INSTANCE_NAME}" \
    --display-name="patch-{CVE_ID}"
```

Applying OS package updates cannot be undone by a rollback command. If a patch
must be reverted, the user has to restore the VM from a disk snapshot taken
before the patch job, so confirm a snapshot exists before executing.

### B. Verification

Verify patch completion status:

```bash
gcloud compute os-config patch-jobs list \
    --project={PROJECT_ID} \
    --filter="displayName:patch-{CVE_ID}" \
    --limit=1
```

## 3. Container Image & Artifact Registry Vulnerabilities

When a vulnerability is detected in a container image stored in Artifact
Registry:

### A. Remediation: Rebuild and Redeploy Container Image

1.  Identify the affected base image layer or third-party dependency from the
    finding's CVE details.
2.  Recommend bumping the base image tag in the workload's `Dockerfile` (e.g.,
    from `debian:11-slim` to `debian:12-slim` or the latest security patch
    release).
3.  Rebuild and publish the container image, then trigger a rolling restart of
    the deployment.

### B. Verification

After rebuilding and publishing the updated container image, verify the newly
published image tag in Artifact Registry:

```bash
# List recent container images in the repository to verify the updated tag/digest
gcloud artifacts docker images list {LOCATION}-docker.pkg.dev/{PROJECT_ID}/{REPOSITORY}/{IMAGE} \
    --limit=5
```

If the updated container image tag is published and deployed without the
reported CVE, the remediation is successful.
