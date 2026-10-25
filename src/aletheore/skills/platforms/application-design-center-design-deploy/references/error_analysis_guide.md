# ADC Deployment Error Analysis Guide

This guide provides heuristics and examples for analyzing deployment errors in
Application Design Center (ADC).

## 1. Identifying the Failing Component

Deployment failures usually manifest as Terraform errors in the Cloud Build
logs. To fix the issue, you must first identify which ADC component corresponds
to the failing Terraform resource.

*   **Naming Convention**: ADC generates modular Terraform. In error logs,
    resources are typically referenced by their module path:
    `module.<component_name>.<resource_type>.<resource_internal_name>`.
    *   Example: In `module.sql-postgresql-1.google_sql_database_instance.db`,
        the component name is `sql-postgresql-1`.
    *   *Note for Composite Templates*: Native components may be grouped under a
        single module named `native-components` (e.g.,
        `module.native-components.google_compute_instance.vm`). For these, you
        must identify the component by matching resource parameters (like names
        or labels) back to the SAT.
*   **Mapping**: Locate the component in the Serialized Application Template
    (SAT) where the `uri` ends with `.../components/<component_name>`.
    *   Example:
        `projects/P/locations/L/spaces/S/applicationTemplates/A/components/sql-postgresql-1`.

## 2. Analyzing the Component Schema

For **Configuration** errors, you must analyze the component schema (fetched in
`SKILL.md` Step 3) to identify invalid parameters.

*   **Key Schema Sections**:
    *   `connectionDefinitions`: Details the parameters that connect components.
    *   `properties`: Defines the allowed parameters, their types, descriptions,
        and constraints.
    *   `required`: Lists parameters that must be specified.
*   **Analysis Method**: Compare the parameters in the application configuration
    (from Step 1) with the schema definitions to identify mismatches (e.g.,
    invalid values, missing required fields, or incorrect types).

## 3. Error Classification & Remediation Heuristics

Analyze the error message (from `deploymentMetadata.error` or Cloud Build logs)
and classify it to determine the remediation path.

Error Pattern (Substring)                                                     | Error Category        | Root Cause                                                 | Remediation Path                                                                   | Example Remediation
:---------------------------------------------------------------------------- | :-------------------- | :--------------------------------------------------------- | :--------------------------------------------------------------------------------- | :------------------
`incompatible`, `must be one of`, `constraint violation`, `validation failed` | **Configuration**     | Invalid or conflicting parameters in component definition. | **Step 4 (Config Change)**: Update the invalid parameter in the SAT.               | Update `edition` to `ENTERPRISE_PLUS` if `tier` requires it.
`duplicate`, `already exists`, `Conflict`, `409`                              | **Resource Conflict** | Resource name is already in use in the target project.     | **Step 4 (Config Change)**: Update the `name` or `id` parameter to a unique value. | Change `name` of SQL instance from `my-db` to `my-db-unique-1`.
`Permission denied`, `403`, `caller does not have permission`                 | **IAM Permission**    | Deployment service account lacks required GCP permissions. | **Step 5 (gcloud IAM)**: Grant the required role to the SA.                        | `gcloud projects add-iam-policy-binding ... --role=roles/bigquery.dataEditor`
`API not enabled`, `has not been used in project`, `disabled`                 | **API Disabled**      | Required GCP service API is not enabled in the project.    | **Step 5 (gcloud API)**: Enable the API.                                           | `gcloud services enable compute.googleapis.com`

## 4. Generic Examples

### Example 1: Incompatible Configuration (Config Error)

*   **Generic Error Pattern**:

    ```
    Error: ... [PARAMETER_A] [VALUE_A] is incompatible with [PARAMETER_B] [VALUE_B].
    ```

    *   *Example*: `Instance edition ENTERPRISE is incompatible with tier
        db-perf-optimized-N-8.`

*   **Analysis**: Two parameters of the same component have conflicting values.
    The component schema or provider validation defines compatibility rules that
    are violated.

*   **Remediation**: Identify the component from the resource name in the log
    (e.g., `google_sql_database_instance.<component_name>`). Update one of the
    conflicting parameters in the SAT to a compatible value (refer to the
    component schema fetched in Step 3).

### Example 2: Duplicate Configuration (Config Error)

*   **Generic Error Pattern**:

    ```
    Error: Duplicate [FIELD] definition '[VALUE]' for [PARENT] '[PARENT_VALUE]' in [PARAMETER_NAME].
    ```

    *   *Example*: `Duplicate path definition '/*' for host 'example.com' in
        host_path_mappings.`

*   **Analysis**: A parameter that expects unique keys (or unique combinations
    of fields) contains duplicates.

*   **Remediation**: Locate the component. Update the parameter in the SAT to
    remove the duplicate entry.

### Example 3: Missing Permission (IAM Error)

*   **Generic Error Pattern**:

    ```
    Error: googleapi: Error 403: Permission '[PERMISSION_NAME]' denied on resource '[RESOURCE_URI]' (or it may not exist).
    ```

    *   *Example*: `Error: googleapi: Error 403: Permission
        'bigquery.datasets.create' denied on resource 'projects/my-project'`

*   **Analysis**: The deployment service account (principal) lacks the
    permission `[PERMISSION_NAME]` required to create or modify
    `[RESOURCE_URI]`.

*   **Remediation**:

    1.  Identify the service account used for deployment (typically found in the
        application details fetched in Step 1).
    2.  Determine the GCP role that grants `[PERMISSION_NAME]`.
    3.  Construct a `gcloud` command to grant that role to the service account.
    4.  *Template*: `gcloud projects add-iam-policy-binding <PROJECT_ID>
        --member='serviceAccount:<SA_EMAIL>' --role='<ROLE>'`

### Example 4: Resource Conflict (Conflict Error)

*   **Generic Error Pattern**:

    ```
    Error: ... Error 409: The [RESOURCE_TYPE] already exists.
    ```

    *   *Example*: `Error: Error creating Instance: googleapi: Error 409: The
        instance already exists., alreadyExists`

*   **Analysis**: A resource with the same name/identifier already exists in the
    target GCP project/location.

*   **Remediation**: Locate the component in the SAT. Update its name or
    identifier parameter to a new, unique value.
