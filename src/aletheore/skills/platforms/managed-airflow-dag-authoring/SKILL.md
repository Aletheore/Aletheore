---
name: managed-airflow-dag-authoring
description: Provides guidance for authoring Apache Airflow DAGs in Managed Service for Apache Airflow (MSAA; formerly Cloud Composer). Covers environment context discovery, Airflow 2 vs 3 compatibility, authoring best practices, and local/remote validation processes. Use when creating or extending an Airflow DAG. Don't use when authoring Python code unrelated to Airflow DAGs.
metadata:
  version: 1.0.0
  category: BigDataAndAnalytics
skill_id: platforms/managed-airflow-dag-authoring
title: Managed Airflow Dag Authoring
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/managed-airflow-dag-authoring
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/managed-airflow-dag-authoring", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/managed-airflow-dag-authoring", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GCP Managed Airflow DAG Authoring Guide

This skill guides you through authoring and validating Apache Airflow DAGs for
Managed Service for Apache Airflow (MSAA; formerly Cloud Composer) environments.

--------------------------------------------------------------------------------

## Phase 1: Context Discovery

Before writing any DAG code, you MUST understand the constraints (e.g. version
of Airflow) and capabilities of your target environment if user is willing to
provide them.

### 1.1 Identify Target Environment & Access

Determine if you have direct access to the target Managed Airflow environment,
local development environment or if you are working offline (only changing local
files without validation).

*   **If environment access is available:** Use `gcloud` to inspect the
    environment (see Section 1.3).
*   **If offline:** Rely on user provided details.

### 1.2 Identify Development Environment

Determine if a local development environment is available.

*   Check if `composer-dev` CLI is installed.
*   Check if a local Python environment with `airflow` is available.

### 1.3 Inspect Target Environment (if available and requested)

Run the following commands to discover version constraints:

1.  **Get Airflow/Image Version:**

    ```bash
    gcloud composer environments describe {env_name} \
        --location {region} \
        --format="value(config.softwareConfig.imageVersion)"
    ```

2.  **Get Installed Packages (Versions):**

    ```bash
    gcloud composer environments describe {env_name} \
        --location {region} \
        --format="value(config.softwareConfig.pypiPackages)"
    ```

3.  **Get DAGs GCS Bucket:**

    ```bash
    gcloud composer environments describe {env_name} \
        --location {region} \
        --format="value(config.dagGcsPrefix)"
    ```

--------------------------------------------------------------------------------

## Phase 2: DAG Authoring Best Practices

### 2.1 General Airflow Best Practices

*   **Idempotency:** Every task SHOULD be idempotent. Running it multiple times
    with the same inputs (e.g., execution date) SHOULD produce the same result
    and not duplicate data.
*   **No Top-Level Code Execution:** Do NOT execute database queries, external
    API calls, or heavy computations at the top level of the DAG file (outside
    of tasks/operators). This code runs every few seconds during DAG parsing and
    will degrade performance.
*   **Explicit Catchup:** Always set `catchup=False` in the DAG definition
    unless historical backfilling is explicitly required.
*   **Use Airflow Variables/Connections:** Never hardcode credentials or
    environment-specific configs. Use `Variable.get()` (with
    `deserialize_json=True` if applicable) and `BaseHook.get_connection()`.
    Access variables via Jinja templates (e.g., `{{ var.value.my_var }}`) to
    avoid database calls during DAG parsing.

### 2.2 Airflow 2 vs Airflow 3 Compatibility

Use managed-airflow-migrations skill to navigate adjusting the code to
specific target Airflow version.

--------------------------------------------------------------------------------

## Phase 3: Validation Process

You MUST validate DAGs before concluding your task.

### 3.1 Local Validation (Offline/Pre-deployment)

#### 3.1.1 Static Analysis & Linting

Use `ruff` or `pylint` if available.

```bash
ruff check path/to/dag.py
```

*   If targeting Airflow 3, check with Airflow 3 rules if rulesets are
    available.

#### 3.1.2 Local Dev Environment (`composer-dev`)

If the user has `composer-dev` configured:

1.  Copy the DAG to the local directory with DAGs:

    ```bash
    cp path/to/dag.py $(composer-dev describe {local_env} --format="value(dags_directory)")
    ```

2.  Verify parsing:

    ```bash
    composer-dev run-airflow-cmd {local_env} dags list-import-errors
    ```

### 3.2: Target Environment Validation

Only perform these steps if you have GCP access and are authorized to deploy to
a target environment.

### 3.2.1 Deploy to GCS

Upload the DAG to the target environment's GCS bucket:

```bash
gcloud storage cp path/to/dag.py gs://{target_bucket}/dags/
```

### 3.2.2 Verify via Airflow CLI

Wait 1-2 minutes for the scheduler to parse the file, then run:

1.  **Check for Import Errors:**

    ```bash
    gcloud composer environments run {env_name} \
        --location {region} \
        dags list-import-errors
    ```

*Pass Criteria:* Output should be "No data found" or empty.

2.  **Verify DAG is Listed:**

    ```bash
    gcloud composer environments run {env_name} \
        --location {region} \
        dags list | grep {dag_id}
    ```

### 3.2.3 Monitor Cloud Logging

Check for runtime parsing errors in Cloud Logging:

```query
resource.type="cloud_composer_environment"
resource.labels.environment_name="{env_name}"
log_id("airflow-scheduler")
severity>=ERROR
textPayload:"{dag_file_name}"
```

--------------------------------------------------------------------------------

## Definition of Done

*   DAG code adheres to Airflow version constraints of the target environment.
*   DAG code follows best practices (no top-level execution, idempotent if
    possible).
*   DAG parses locally without import errors.
*   (If environment is available) DAG is deployed to the target environment and
    verified to have no import errors.
