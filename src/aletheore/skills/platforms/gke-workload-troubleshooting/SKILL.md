---
name: gke-workload-troubleshooting
metadata:
  version: 1.0.0
  category: Containers
description: Diagnoses GKE workload failures (CrashLoopBackOff, OOMKilled, ImagePullBackOff, Pending, etc.) via logs and events. Use when pods fail to start or crash repeatedly. Don't use for GKE cluster infrastructure provisioning, node pool creation, or non-Kubernetes Google Cloud services.
skill_id: platforms/gke-workload-troubleshooting
title: GKE Workload Troubleshooting
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-workload-troubleshooting
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-workload-troubleshooting", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-workload-troubleshooting", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Workload Troubleshooting Skill

Use this skill to systematically diagnose and resolve failures in application
workloads deployed in GKE clusters. This skill operates non-interactively and
enforces a read-only diagnostics boundary: it only **proposes** fixes — whether
Kubernetes manifest/config patches or Google Cloud changes (for example `gcloud`
IAM bindings or node-pool recreation) — and never executes live mutations
itself.

## 🔍 Diagnostic Workflow

### Step 0: Non-Interactive Context Discovery & Time Window Definition

1.  **Parameter Extraction**: Extract required context (`project_id`,
    `cluster_name`, `cluster_location`, `workload_name`, `workload_namespace`)
    non-interactively from the user prompt, active `SETTINGS.md`, or active
    environment defaults:

    -   Default `workload_namespace` to `default` if omitted.
    -   Infer missing cluster parameters from active environment (`kubectl
        config current-context` or `gcloud config get-value project`).
    -   Prioritize non-interactive context discovery from prompts and
        environment defaults to ensure autonomous execution flow.

2.  **Cluster Credentials & Fallback Mode**:

    -   Attempt credential fetch: `gcloud container clusters get-credentials
        {cluster_name} --region/--zone {cluster_location}`
    -   **Fallback / Dry-Run Mode**: If the cluster is unreachable,
        non-existent, or live command execution fails (such as in sandboxed
        evaluations, dry-run mode, or offline analysis):
        -   Limit retry attempts to avoid resource exhaustion and context
            overflow in unreachable cluster scenarios.
        -   Immediately present the exact sequence of `kubectl` diagnostic
            commands for the human operator to run.
        -   Synthesize the root cause analysis and output the proposed GitOps
            manifest fix based on the reported symptoms.

3.  **Time Handling & Fallbacks**:

    -   **Determine Issue Timestamp ({issue_time})**:
        -   **Specific Time Provided**: If the user provides a specific
            timestamp, use it as `{issue_time}`.
        -   **Relative Time Provided (e.g., "5 minutes ago")**: Dynamically
            calculate the corresponding UTC timestamp based on current system
            time, and use it as `{issue_time}`.
        -   **No Time Provided (Default)**: Use current system time as
            `{issue_time}`.
    -   **Window Calculation**: Center a 1-hour query window around
        `{issue_time}` (`start_time` = `{issue_time} - 30m`, `end_time` =
        `{issue_time} + 30m`).

--------------------------------------------------------------------------------

### Step 1: Analyze Pod Status and Conditions

Inspect the workload's active pod states and controller status.

**Diagnostic Commands:**

```bash
# 1. Inspect the deployment's actual selector labels:
kubectl get deployment {workload_name} -n {workload_namespace} -o jsonpath='{.spec.selector.matchLabels}'
# 2. Query the pods using the returned labels, for example:
kubectl get pods -l {selector_labels} -n {workload_namespace}
kubectl get deploy/{workload_name} -n {workload_namespace} -o yaml
```

#### Diagnostic Decision Tree:

-   **Phase: Pending**:
    -   The Pod cannot schedule on any node. Proceed directly to **Step 2 (Query
        Namespace Events)**.
-   **State: CrashLoopBackOff / Error**:

    -   The container boots but exits repeatedly; the `kubelet` restarts it with
        an increasing back-off delay of up to five minutes. First read the
        terminated **reason** and **exit code**:

    ```bash
    kubectl describe pod {pod_name} -n {workload_namespace}
    kubectl get pod {pod_name} -n {workload_namespace} -o jsonpath='{.status.containerStatuses[*].lastState.terminated}'
    ```

    -   **Reason: OOMKilled (Exit Code 137)**: The container's memory limit was
        reached. Proceed to **Step 3 (Inspect Logs) → OOM Analysis** to classify
        container-level vs node-level, then **Step 5** to propose fixes.
    -   **Exit Code 0 (successful exit)**: Unexpected for a long-running
        Deployment/StatefulSet — `restartPolicy: Always` restarts the finished
        process, creating the loop. Common causes: the `command`/`entrypoint`
        does not start a persistent process, a worker exits on an empty queue,
        or a missing/invalid config (e.g., an unattached or mis-keyed
        `ConfigMap` volume) makes the app exit cleanly. Proceed to **Step 3
        (Inspect Logs)**.
    -   **Exit Code 128**: Invalid `command`/`entrypoint` — the executable path
        is wrong or absent in the image. Verify the container command in the
        manifest.
    -   **Exit Code 1 or other non-zero**: The application crashed —
        configuration errors, missing/invalid env vars or config files,
        unreachable dependencies, or auth failures (`401`/`403`) on Google Cloud
        calls (check the Pod's IAM / Workload Identity Federation). Proceed
        directly to **Step 3 (Inspect Logs)**.
    -   If the exit code looks healthy but the container keeps restarting,
        suspect a **liveness probe failure** (see Step 3).

-   **State: ImagePullBackOff / ErrImagePull**:

    -   The kubelet cannot pull the container image. `ImagePullBackOff` means it
        keeps retrying with back-off; `ErrImagePull` is a general,
        non-recoverable pull error. Related statuses: `InvalidImageName`,
        `RegistryUnavailable`, `SignatureValidationFailed`, `ImageInspectError`.
        Proceed to **Step 2 (Query Namespace Events)** to read the exact pull
        error message.

-   **State: ContainerCreating**:

    -   The container is blocked during volume mount, networking setup, or image
        pulling. Proceed directly to **Step 2 (Query Namespace Events)**.

--------------------------------------------------------------------------------

### Step 2: Query Namespace Events

Look for infrastructure, volume, image, or scheduling alerts in GKE.

**Diagnostic Command:**

```bash
kubectl get events -n {workload_namespace} --sort-by='.metadata.creationTimestamp'
# Or query Cloud Logging for historical GKE events within the time window:
gcloud logging read "resource.type=\"k8s_cluster\" AND logName=\"projects/{project_id}/logs/events\" AND jsonPayload.involvedObject.namespace=\"{workload_namespace}\"" --start-time="{start_time}" --end-time="{end_time}" --project="{project_id}"
# Or query specifically for image pull failures within the time window:
gcloud logging read 'log_id("events") AND resource.type="k8s_pod" AND resource.labels.cluster_name="{cluster_name}" AND jsonPayload.message=~"Failed to pull image"' --project="{project_id}"
```

*Note: Retrieve the sorted events list and manually inspect the event timestamps
(CreationTimestamp/LastSeen) to identify failures occurring within the
`{start_time}` and `{end_time}` window.*

#### Signature Identifiers:

-   **`FailedScheduling`**: Node resource exhaustion. Look for messages like
    `0/3 nodes are available: 3 Insufficient memory.` or missing node affinity
    tolerations (e.g. Spot VM taints).
-   **`FailedMount`**:
    -   Missing PersistentVolumeClaim (`PVC`).
    -   Missing Secret (`Secret "{secret_name}" not found`).
    -   Missing ConfigMap (`ConfigMap "{configmap_name}" not found`).
-   **`Failed` / `BackOff` (Image Pull)**: First read the exact event message
    (`Failed to pull image "IMAGE": ...`) and triage by what it actually says.
    Do **not** jump to IAM / node service-account investigation unless the
    message is genuinely a permission or authentication error.

    -   **Wrong image name/tag — start here** (`not found`, `manifest unknown`,
        `InvalidImageName`): the most common cause — the tag or path is wrong,
        or the image was deleted, frequently introduced by a recent deployment
        change.
        *   Identify the failing container image name and the invalid tag.
        *   Check the Git history for the last known working image tag: `git log
            -p -S "{image_name}" -- {manifest_file_path}` (or run `git log` on
            the folder containing manifests).
        *   Propose reverting the image tag to the last working version (or
            correcting the tag) in the manifest patch.
    -   **Permission / authentication errors only** (the message contains `403
        Forbidden` / `denied`, or `401 Unauthorized` / `unauthorized`): the node
        cannot authorize or authenticate to the registry. Pursue the checks
        below **only** when the message matches.

        *   **`403 Forbidden` (authorization)** — the node pool service account
            (or the imagePullSecret's service account) is missing registry read
            access. **Suggest** granting it by presenting the following command
            for the user to review and run; do not execute it. For **Artifact
            Registry**:

            ```bash
            gcloud artifacts repositories add-iam-policy-binding {repository} \
              --location={repo_location} \
              --member="serviceAccount:{node_service_account_email}" \
              --role="roles/artifactregistry.reader"
            ```

            For **Container Registry (`gcr.io`)**, grant
            `roles/storage.objectViewer` on the backing bucket (or the Artifact
            Registry role if `gcr.io` was migrated). Also check that any **VPC
            Service Controls** perimeter allows Artifact Registry.
        *   **`401 Unauthorized` (authentication)** — the node service account
            is disabled or the node lacks the required OAuth scope:

            ```bash
            gcloud container clusters describe {cluster_name} --location={cluster_location} \
              --format="table(nodePools.name,nodePools.config.serviceAccount)"
            gcloud iam service-accounts list \
              --filter="email:{node_service_account_email} AND disabled:true" --project={project_id}
            gcloud compute instances describe {node_name} --zone={node_zone} \
              --format="flattened(serviceAccounts[].scopes)"
            ```

            Scopes must include `devstorage.read_only` or `cloud-platform`
            (provided by `gke-default`). Nodes are immutable, so **suggest**
            recreating the node pool with `--scopes="gke-default"` if the scope
            is missing — present it as a proposed command for the user to run,
            do not execute it.
        *   **Private / self-hosted registry**: ensure a valid `imagePullSecret`
            exists and is referenced by the Deployment.
    -   **Other statuses**: `RegistryUnavailable` / `i/o timeout` / DNS `server
        misbehaving` → registry network path (DNS, firewall egress, Google API
        connectivity); `exec format error` or a deprecated schema-1 image →
        architecture/schema mismatch.

--------------------------------------------------------------------------------

### Step 3: Inspect Application Logs

Extract exceptions and stack traces from the application runtime.

**Diagnostic Commands:**

```bash
# Check current active log stream (handles multi-container pods)
kubectl logs {pod_name} -n {workload_namespace} --all-containers --tail=100

# Check logs from previously terminated container instances (handles multi-container pods)
kubectl logs {pod_name} -n {workload_namespace} --all-containers -p --tail=100
```

#### Signature Identifiers:

-   **Out-of-Memory (OOM) Analysis**: First confirm and classify the kill.

    -   **Container-level OOM** (most common): `kubectl describe pod` shows
        `Last State: Terminated`, `Reason: OOMKilled`, `Exit Code: 137`. The
        container exceeded its cgroup memory limit. Differentiate an
        **application memory leak/loop** (unbounded growth in logs and startup
        command) from an **infrastructure capacity mismatch** (legitimate demand
        exceeding `resources.limits.memory`).
    -   **Node-level (system) OOM**: the entire node ran out of memory; look for
        evicted Pods and node-pressure eviction. The combined memory of all Pods
        exceeded node capacity.
    -   **"Invisible" OOM** (`cgroup v1`): a child process is killed but the
        main process (PID 1) keeps running, so Kubernetes never marks
        `OOMKilled`. Search node logs in Cloud Logging:

        ```bash
        gcloud logging read 'resource.type="k8s_node" AND resource.labels.cluster_name="{cluster_name}" AND jsonPayload.MESSAGE:("TaskOOM event" OR "ContainerDied")' --project="{project_id}"
        ```

        A `TaskOOM` entry confirms an OOM kill; match its container ID to the
        `ContainerDied` entry to find the affected Pod. On the node, `journalctl
        -k` distinguishes container-level kills (`memory cgroup`, `memcg`) from
        system-level kills (`Out of memory: Killed process`).
    -   Do not rely solely on sampled memory metrics — they often miss the spike
        that triggers the kill. Then proceed to **Step 5** to propose fixes
        (raise limits, fix the leak, or right-size the node pool).
-   **Liveness Probe Failure (CrashLoop with no application error)**: if the
    container restarts but its logs show no crash, the `kubelet` may be killing
    it on failed liveness probes (default `failureThreshold: 3`). Confirm in
    Cloud Logging:

    ```bash
    gcloud logging read 'resource.type="k8s_node" AND log_id("kubelet") AND jsonPayload.MESSAGE:"failed liveness probe, will be restarted" AND resource.labels.cluster_name="{cluster_name}"' --project="{project_id}"
    ```

    Common fixes: correct the probe type/path/port, raise `initialDelaySeconds`
    or `timeoutSeconds`/`failureThreshold` for slow starts, or relieve CPU/disk
    I/O contention causing probe timeouts. Keep probe commands lightweight.
-   **Stack Trace / Unhandled Exception**: Look for language-specific stack
    traces (e.g., `panic:`, `NullPointerException`, `Traceback (most recent
    call)`). This indicates an application bug.
-   **Egress Network Timeout**: Look for connection timeouts (e.g., `Connection
    timed out`, `dial tcp: i/o timeout`). Proceed to **Step 4 (Verify
    Connectivity)**.
-   **Permission Errors (ReadOnlyRootFilesystem)**: Look for write errors (e.g.,
    `Read-only file system`, `Permission denied` when writing to `/tmp` or
    `/var/log`). Propose adding an `emptyDir` volume mount to that directory in
    the manifest.

--------------------------------------------------------------------------------

### Step 4: Verify Service Connectivity and Network Policies

Troubleshoot connection drops to other services.

**Diagnostic Commands:**

```bash
# Verify target endpoint is active
kubectl get endpoints {target_service_name} -n {target_namespace}

# Query network policies inside namespace
kubectl get networkpolicies -n {workload_namespace} -o yaml
```

#### Logic & Dry-Run Fallback:

1.  **Live Cluster Mode**:

    -   If `kubectl get endpoints` returns an empty list, the target
        microservice itself is failing to schedule or boot (troubleshoot target
        service).
    -   If endpoints exist but logs show timeouts, analyze `NetworkPolicy`
        egress blocks to verify if egress traffic to the target service's
        IP/port is allowed.

2.  **Sandboxed / Dry-Run Mode**:

    -   If live `kubectl` queries fail or cluster connection is unavailable, do
        NOT retry live cluster access or enter repetitive connection attempts.
    -   Immediately inspect the application source code (e.g. `worker.py`,
        `app.go`, DB connection strings) or Deployment manifests to identify the
        target service hostname (e.g. `account-db`) and destination port (e.g.
        `5432`).
    -   Present the exact `kubectl get endpoints` and `kubectl get
        networkpolicies` commands for the user, and synthesize the required
        `NetworkPolicy` egress patch allowing traffic to the target service and
        port.

--------------------------------------------------------------------------------

### Step 5: Propose GitOps Correction

Following the GitOps boundary, **do not apply changes directly** — this includes
both cluster manifest/config patches and any Google Cloud mutations (for example
`gcloud` IAM bindings or node-pool recreation). Present every change as a
reviewable suggestion: a manifest patch / PR, or a command for the user to run.

1.  Synthesize the root cause analysis for the human operator (e.g.
    *"payment-api is failing with exit code 137 because its memory limit is set
    to 256Mi while actual usage spiked to 270Mi"*).
2.  Generate the corrected YAML manifest patch (e.g. increase memory limits, add
    missing Secret mounts, or add tolerations for Spot nodes).
3.  Check if a branch or Pull Request (PR) already exists for this
    workload/failure. If so, update the existing branch/PR or notify the user
    instead of creating a duplicate. Otherwise, create a branch, commit the
    change, open a Pull Request (PR) on GitHub, and conclude the workflow (do
    not wait for human merge).

--------------------------------------------------------------------------------

## References

-   [Troubleshoot OOM events](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/oom-events.md.txt)
-   [Troubleshoot image pulls](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/image-pulls.md.txt)
-   [Troubleshoot CrashLoopBackOff events](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/crashloopbackoff-events.md.txt)
-   [Troubleshoot deployed workloads](https://docs.cloud.google.com/kubernetes-engine/docs/troubleshooting/deployed-workloads.md.txt)
-   [Artifact Registry access control with GKE](https://docs.cloud.google.com/artifact-registry/docs/access-control.md.txt#gke)
