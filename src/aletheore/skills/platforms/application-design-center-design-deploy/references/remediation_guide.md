# ADC Deployment Remediation Guide

This guide provides instructions on how to construct remediation plans for ADC
deployment failures, mapping analysis results to the required output formats.

## 1. Configuration Remediations (Step 4 & Step 6)

When the error is classified as a **Configuration Issue** or **Resource
Conflict**, the remediation involves suggesting changes to the application's
component parameters.

### 1.1. Constructing Component Parameters

You must identify the exact component and the parameter that needs modification.

*   **Component URI**: Must be the full resource name of the component, NOT just
    the display name.
    *   Format:
        `projects/<PROJECT_ID>/locations/<LOCATION>/spaces/<SPACE_ID>/applicationTemplates/<APP_TEMPLATE_ID>/components/<COMPONENT_NAME>`
    *   Example:
        `projects/my-project/locations/us-central1/spaces/my-space/applicationTemplates/my-app/components/sql-postgresql-1`
*   **Parameter Key**: Use the top-level parameter name as defined in the
    component schema.
    *   **CRITICAL RULE**: Do **NOT** use dotted notation (e.g. `settings.tier`
        or `backup_configuration.enabled`) for nested parameters. The engine
        does not support modifying nested sub-fields via dotted paths. You must
        always target the top-level parameter key (e.g., `settings` or
        `backup_configuration`).
    *   Example: `settings` (Correct) vs `settings.tier` (Incorrect).
*   **Parameter Value**: Specify the new value. Ensure it conforms to the type
    and constraints defined in the component schema.
    *   **Structured Parameters (Objects/Arrays)**: Because updating a parameter
        completely overwrites its existing value, you **MUST** provide the
        complete object or array with all of its fields.
        *   Inspect the original value of the parameter in the input template.
        *   Copy all unchanged sub-fields and values.
        *   Modify only the specific sub-field that requires remediation.

### 1.2. Example: Configuration Fix

**Scenario**: Cloud Run service deployment failed because CPU throttling is
enabled when using GPUs. To resolve this, CPU throttling must be disabled by
setting `cpu_idle` to `false`.

**Remediation JSON Component Parameters**:

```json
[
  {
    "component_uri": "projects/my-project/locations/us-central1/spaces/my-space/applicationTemplates/my-application-template/components/cloud-run-1",
    "parameters": [
      {
        "key": "cpu_idle",
        "value": "false"
      }
    ]
  }
]
```

## 2. gcloud Remediations (Step 5 & Step 7)

When the error is classified as an **IAM Permission** or **API Disabled** issue,
the remediation involves generating `gcloud` commands.

### 2.1. IAM Permission Fixes

Use the minimum required privilege.

*   **Command Template**:

    ```bash
    gcloud projects add-iam-policy-binding PROJECT_ID --member="serviceAccount:SA_EMAIL" --role="roles/ROLE_NAME"
    ```

*   **Identifying the Principal (SA_EMAIL)**:

    *   Look for the service account in the error message (e.g.,
        `service-12345678@gcp-sa-adc.iam.gserviceaccount.com` or the deployment
        SA).
    *   If the service account cannot be determined, use the placeholder
        `<SERVICE_ACCOUNT_EMAIL>`.

*   **Identifying the Role**:

    *   Use the error message to determine what permission is missing (e.g.,
        `compute.instances.create`).
    *   Map the permission to a standard GCP role (e.g., `roles/compute.admin`
        or a more specific role if known).

### 2.2. API Activation Fixes

*   **Command Template**:

    ```bash
    gcloud services enable SERVICE_API_NAME --project=PROJECT_ID
    ```

    *   Example: `gcloud services enable sqladmin.googleapis.com
        --project=my-project`

### 2.3. Combined Example

If multiple commands are needed (e.g., enable API AND grant permission), list
them as separate steps in the final output.

```json
"troubleshooting_steps": [
  {
    "description": "Enable the Cloud SQL Admin API.",
    "gcloud_command": "gcloud services enable sqladmin.googleapis.com --project=my-project"
  },
  {
    "description": "Grant Cloud SQL Editor role to the deployment service account.",
    "gcloud_command": "gcloud projects add-iam-policy-binding my-project --member='serviceAccount:deploy-sa@my-project.iam.gserviceaccount.com' --role='roles/cloudsql.editor'"
  }
]
```
