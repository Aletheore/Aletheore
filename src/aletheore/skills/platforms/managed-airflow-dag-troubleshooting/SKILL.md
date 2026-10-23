---
name: managed-airflow-dag-troubleshooting
description: Provides guidance for troubleshooting Apache Airflow DAGs (failed DAG runs and task instances) in Managed Service for Apache Airflow (MSAA; formerly Cloud Composer). Use when figuring out reasons for DAG run or task instance failures. Don't use when looking for overall recommendations for Managed Airflow environment performance.
metadata:
  version: 1.0.0
  category: BigDataAndAnalytics
skill_id: platforms/managed-airflow-dag-troubleshooting
title: Managed Airflow DAG Troubleshooting
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/managed-airflow-dag-troubleshooting
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/managed-airflow-dag-troubleshooting", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/managed-airflow-dag-troubleshooting", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Managed Service for Apache Airflow (formerly Cloud Composer) DAG troubleshooting guide

This skill provides instructions for troubleshooting Managed Airflow DAGs (DAG
runs and task instances), utilizing `gcloud composer`, `gcloud logging` and
`gcloud storage` commands to fetch remote logs and code.

## General rules

1.  Provide suggestions on how to troubleshoot the failed jobs. Provide only the
    steps that the user can actually take. Ground all troubleshooting advice in
    direct findings.
2.  When troubleshooting a failure, follow the following practices to always
    provide a deterministic diagnosis:

    *   **Fetch relevant logs**: Always fetch the logs for a task under
        investigation using `gcloud logging read`; check the logs for specific
        error patterns: Python tracebacks, API error codes (e.g., 400, 403, 404,
        500), or Airflow signals (e.g., `AirflowTaskTimeout`).
    *   **Fetch task metadata**: When troubleshooting a task, fetch the task
        state and metadata (execution state, try number, timestamps, and
        execution details) using:

        ```bash
        gcloud composer environments run {env_name} \
            --location {location} \
            tasks states-for-dag-run -- -d {dag_id} -r {run_id}
        ```

        or for an individual task instance:

        ```bash
        gcloud composer environments run {env_name} \
            --location {location} \
            tasks state -- {dag_id} {task_id} {execution_date}
        ```
    *   **Retrieve and compare DAG source code**: Download the remote DAG source
        code using `gcloud storage cp gs://{bucket_name}/dags/{dag_file}.py .`
        (find the environment bucket via `gcloud composer environments describe
        {env_name} --location {location}
        --format="value(config.dagGcsPrefix)"`). Compare the parameters in the
        code (e.g., table IDs, disk sizes, URI paths) against the error messages
        found in the task logs.
    *   **Explain code mistakes and potential fixes**: Explain mistakes in the
        code (if any are actually visible); suggest potential fixes (if they are
        very likely to be meaningful); discuss source code availability if
        needed - if some source code is unavailable (e.g. imported from a file
        other than the main source code file), mention this (you can mention the
        package name) - in such a case take into account most likely trigger
        rules if they are unknown.
    *   **Check for environment-level errors**: Query Cloud Logging with `gcloud
        logging read` to see if there are high-level environment issues or known
        platform errors correlating with the failure (see **Known issues**
        below). You MUST return ALL found issues.
    *   **Identify failing tasks in a DAG run**: When troubleshooting a failed
        DAG run, mention the task that caused a failure (use `tasks
        states-for-dag-run` or Cloud Logging to identify failed tasks). Provide
        a task instance name. If many tasks failed, mention which task was
        critical (mandatory for successful DAG run execution - look into task
        dependencies and trigger rules) and focus on this one.
    *   **Verify service configurations in code**: If logs suggest an issue with
        a specific service (e.g., BigQuery, Dataform, Compute Engine), use the
        log details to verify the configuration in the DAG source code.
    *   **Correlate logs with code**: E.g., if BigQuery returns a 404, verify
        the dataset ID or table ID in the DAG source code matches reality.
    *   **Prioritize known platform issues**: Check against **Known issues**
        below. If Cloud Logging queries return matching platform error signals,
        prioritize that diagnosis.

3.  **Summarize with Evidence (Deterministic Response):** Your response must be
    specific. Avoid general advice like 'check your permissions.' or 'check the
    logs.' Instead, say 'The service account is missing X permission.'

    *   **Problem:** State the specific root cause and the exact task instance
        ID. Identify if it is a code logic error, a configuration mismatch, or
        an environment timeout.
    *   **Evidence:** **Mandatory.** Provide the verbatim text from the log
        (`textPayload`) or the specific line of code from the DAG that caused
        the failure. Do not summarize the evidence; show the data.
    *   **Recommendation:** Provide an actionable fix. If it is a code error,
        provide the corrected Python snippet. If it is a resource issue, specify
        the exact configuration change needed.

4.  **DAGs Generated by Orchestration Pipelines:** Some DAGs may be generated by
    Orchestration Pipelines. A special requirement related to those DAGs is the
    need to explain the failure in terms of the logical actions defined in the
    pipeline YAML.

    *   **Determine if a DAG is generated by Orchestration Pipelines**:
        Orchestration Pipeline DAGs deployed by dedicated tools have
        `bundle_name`, `version_id`, and `pipeline_name` set in their DAG Run
        metadata (`DagRun.note` that contains JSON metadata). All of them (i.e.
        Orchestration Pipeline DAGs deployed by dedicated tools and created
        manually) have an `op:orchestration_pipeline` tag set (DAG properties,
        including tags, can be verified in the DAG source code or via `gcloud
        composer environments run {env_name} --location {location} dags list`).
    *   Orchestration Pipeline DAGs deployed by dedicated tools have
        additionally the following tags (information in those tags should be
        consistent with data in DAG Run attributes mentioned above):
        *   pipeline name - tag `op:pipeline`, e.g. `op:pipeline:xyz` indicates
            a name `xyz`
        *   bundle name - tag `op:bundle`
        *   version id - tag `op:version`
    *   **Retrieve the resolved pipeline YAML definition from the environment
        bucket**:
        *   Determine the YAML file location:
            1.  Retrieve the DAG source code from the environment bucket using
                `gcloud storage cp gs://{bucket_name}/dags/{dag_file}.py .` (or
                `gcloud storage cat gs://{bucket_name}/dags/{dag_file}.py`).
            2.  Inspect the source code for `generate` or `generate_dags`
                function calls:
                *   Scenario 1: `generate` call found. The first argument is the
                    path to the YAML file - relative to the `dags` folder in
                    environment's bucket.
                *   Scenario 2: `generate_dags` call found.
                    *   Extract the first argument - this is the data folder. If
                        it starts with `/home/airflow/gcs/`, remove this prefix
                        to get a path relative to the root of environment's
                        bucket.
                    *   Extract `bundle_name`, `version_id`, and `pipeline_name`
                        (as explained above).
                    *   Construct the path:
                        `{data_directory}/{bundle_name}/versions/{version_id}/{pipeline_name}.yml`
                        (or `.yaml`).
                *   Scenario 3: If neither call is found, default to the path:
                    `data/{bundle_name}/versions/{version_id}/{pipeline_name}.yml`
                    (or `.yaml`) in an environment's bucket.
            3.  Download the YAML file using `gcloud storage cp
                gs://{bucket_name}/{yaml_path} .` (or `gcloud storage cat
                gs://{bucket_name}/{yaml_path}`).
    *   Map the failed Airflow task back to the logical action name using task
        instance metadata/notes (e.g. `op_action_name` in task `note`).
    *   If the failure involves user assets (like Python scripts), check their
        path in the action definition. If they are in the environment bucket,
        download and read them to debug (`gcloud storage cp
        gs://{bucket_name}/{asset_path} .`). If they are in a custom artifact
        bucket (see GCS URIs in logs/config), note the limitation that they
        cannot be read directly but analyze based on available logs.

5.  You can assume that environment variables set by default (they can be used
    in DAG code, but are not visible in custom environment configuration), e.g.
    `GCS_BUCKET`, are correct - users cannot change them.

6.  "Not found" (404) errors from GCP APIs can be misleading. A "not found"
    error might be returned when a resource actually exists, but the caller does
    not have permissions to access or view it. If a resource is expected to
    exist, suggest verifying proper permissions.

### Important constraints & instructions

*   **Read-Only First**: Do NOT attempt to fix the code immediately. You must
    first prove the root cause using logs and remote code.
*   **No Speculation**: If logs are empty or code cannot be found, state this
    clearly. Always reference error messages as the are.
*   **Safety**: Be careful with secrets. If logs contain sensitive information
    (e.g. passwords), redact it in your analysis.

### Applying Fixes - only if explicitly requested

When the RCA is complete and a fix is ready:

1.  **Repository Check**: If the current workspace does not seem to be the
    source of truth for the Managed Airflow environment:
    *   Ask the user to **open the correct repository**.
    *   OR ask if they want to **download the remote DAG** to the current
        workspace to apply the fix (warning them about potential overwrites).

## Relevant gcloud commands

### Environment & DAG Discovery

*   **List composer environments:**

    ```bash
    gcloud composer environments list \
        --locations=us-central1 \
        --format="table(name,location,state)"
    ```
*   **Describe environment (get DAGs bucket and config):**

    ```bash
    gcloud composer environments describe {env_name} \
        --location {region} \
        --format="value(config.dagGcsPrefix)"
    ```
*   **List composer DAGs:**

    ```bash
    gcloud composer environments run {env_name} \
        --location {region} \
        dags list
    ```
*   **List composer DAG Runs:**

    ```bash
    gcloud composer environments run {env_name} \
        --location {region} \
        dags list-runs -- -d {dag_id} --no-backfill
    ```
*   **List task instance states for a DAG run:**

    ```bash
    gcloud composer environments run {env_name} \
        --location {region} \
        tasks states-for-dag-run -- -d {dag_id} -r {run_id}
    ```
*   **Get state of a specific task instance:**

    ```bash
    gcloud composer environments run {env_name} \
        --location {region} \
        tasks state -- {dag_id} {task_id} {execution_date}
    ```

### Log Retrieval

*   **Fetch error logs for a DAG / Task:**

    ```bash
    gcloud logging read 'resource.type="cloud_composer_environment" AND resource.labels.environment_name="{env_name}" AND labels.dag_id="{dag_id}" AND severity>=ERROR' \
        --limit=25 \
        --format="table(timestamp,severity,labels.task_id,textPayload)"
    ```
*   **Fetch scheduler logs for environment failures:**

    ```bash
    gcloud logging read 'resource.type="cloud_composer_environment" AND resource.labels.environment_name="{env_name}" AND log_id("airflow-scheduler") AND severity>=ERROR' \
        --limit=25 \
        --format="table(timestamp,severity,textPayload)"
    ```

### Code & Asset Retrieval

*   **Download DAG code from GCS:**

    ```bash
    gcloud storage cp gs://{bucket_name}/dags/{dag_file}.py .
    ```
*   **Download pipeline YAML definition or script from GCS:**

    ```bash
    gcloud storage cp gs://{bucket_name}/{path_to_file} .
    ```

## Known issues related to DAG runs and task instances

Use `gcloud logging read` with the queries below to identify specific known
platform failure modes:

### 1. DAG_RUN_TIMEOUT

*   **Issue summary:** The task instance execution was interrupted because a
    timeout for a DAG was exceeded. Unfinished tasks were marked as 'SKIPPED' or
    failed.
*   **Cloud Logging Query:**

    ```bash
    gcloud logging read 'resource.type="cloud_composer_environment" AND resource.labels.environment_name="{env_name}" AND log_id("airflow-scheduler") AND textPayload=~"Run .* of .* has timed-out"' --limit=10
    ```

### 2. TASK_QUEUED_TIMEOUT

*   **Issue summary:** Task failed because it remained queued longer than the
    maximum allowed queue time.
*   **Cloud Logging Query:**

    ```bash
    gcloud logging read 'resource.type="cloud_composer_environment" AND resource.labels.environment_name="{env_name}" AND log_id("airflow-scheduler") AND textPayload=~"Task requeue attempts exceeded max; marking failed"' --limit=10
    ```
*   **Remediation:** Consider increasing worker resources (CPU, memory, worker
    count) or adjusting `[celery]worker_concurrency`.

### 3. TASK_STUCK_IN_QUEUE

*   **Issue summary:** Task reached DAG run timeout because task was stuck in
    queue for too long.
*   **Cloud Logging Query:**

    ```bash
    gcloud logging read 'resource.type="cloud_composer_environment" AND resource.labels.environment_name="{env_name}" AND log_id("airflow-scheduler") AND textPayload=~"Task stuck in queued; will try to requeue"' --limit=10
    ```
*   **Remediation:** Consider increasing the timeout or reducing the load on the
    environment.

### 4. BIGQUERY_JOB_FAILED

*   **Issue summary:** Task failed because of a BigQuery job failure inside a
    BigQuery operator.
*   **Cloud Logging Query:**

    ```bash
    gcloud logging read 'resource.type="cloud_composer_environment" AND resource.labels.environment_name="{env_name}" AND (log_id("airflow-worker") OR log_id("airflow-k8s-worker")) AND textPayload:"airflow/providers/google/cloud/operators/bigquery.py" AND textPayload:"Task failed with exception" AND severity=ERROR' --limit=10
    ```
*   **Remediation:** Inspect the worker logs for the BigQuery Job ID (`Job ID:
    ...`) to diagnose the underlying query error or permissions issue.

### 5. DETECTED_ZOMBIE

*   **Issue summary:** The task instance was revoked by the executor due to
    missing heartbeats. Task instances send heartbeats periodically (every
    `job_heartbeat_sec`, 5 seconds by default) and if heartbeats are missing for
    `scheduler_zombie_task_threshold` (300 seconds by default), the task is
    considered a zombie and marked as failed or up for retry.
*   **Cloud Logging Query:**

    ```bash
    gcloud logging read 'resource.type="cloud_composer_environment" AND resource.labels.environment_name="{env_name}" AND log_id("airflow-scheduler") AND (textPayload:"Detected zombie job:" OR textPayload:"Detected a task instance without a heartbeat:")' --limit=10
    ```
*   **Remediation:** This can happen when a worker is overloaded (CPU/memory
    starvation) and unable to send heartbeats on time, a worker was terminated
    with unfinished tasks (OOM kill/eviction), or the metadata database is
    overloaded. Check worker metrics and consider scaling worker CPU/memory.

### 6. WORKER_OUT_OF_POD_STORAGE

*   **Issue summary:** Task instance failed because a worker is running out of
    pod storage (ephemeral disk space reached or pod evicted due to storage
    limits).
*   **Cloud Logging Query:**

    ```bash
    gcloud logging read 'resource.type="cloud_composer_environment" AND resource.labels.environment_name="{env_name}" AND (log_id("airflow-worker") OR log_id("airflow-k8s-worker")) AND textPayload:"Pod ephemeral local storage usage exceeds the total limit of containers"' --limit=10
    ```
*   **Remediation:** Update the worker storage configuration according to the
    amount of data being stored or clean up temporary files created during task
    execution.
