# ADC Application Troubleshooting Process

Use this template to analyze and resolve deployment failures in Application
Design Center (ADC). This process relies exclusively on standard `gcloud`
commands and does not use any custom backend tools.

## Objective & Goals

Analyze a deployment error for a given ADC application and provide the minimal,
safest change required to eliminate the reported error.

## Constraints

-   **Use gcloud**: All interactions with ADC and Google Cloud must be performed
    using standard `gcloud` commands.
-   **No Real Deployments**: Do not execute `gcloud design-center spaces
    applications deploy`.
-   **Read-Only Discovery**: Do not run any mutating commands (e.g., modifying
    IAM policies, enabling APIs, or updating configurations) during the
    discovery phase (Steps 1 & 2). Discovery must be strictly read-only.

## Troubleshooting Process

Follow these steps in sequence to troubleshoot the deployment failure:

### Step 1: Fetch the Debugging Context

*   **Input**: An `application_uri` (e.g.,
    `projects/P/locations/L/spaces/S/applications/A`).
*   **Act**: Parse the URI to extract Project ID, Location, Space, and
    Application Name. Run the following command to fetch details:

    ```bash
    gcloud design-center spaces applications describe A --space=S --location=L --project=P
    ```

*   **Analyze**:

    *   Verify that the application `state` is `FAILED`. This debugging workflow
        is designed for failed deployments. If the state is
        `UPDATING_DEPLOYMENT`, you may need to wait or check if it eventually
        fails.
    *   Look for error messages in `deploymentMetadata.error` and identify the
        build ID in `deploymentMetadata.build`. This data is typically only
        present when the state is `FAILED`.
    *   If both `deploymentMetadata.error` and `deploymentMetadata.build` are
        missing, the debugging context is insufficient. Proceed directly to Step
        8 and report failure.

### Step 2: Fetch the Cloud Build Logs

*   **Act**: If a build ID was identified in Step 1, retrieve the raw deployment
    execution logs. The region is typically the same as the location `L`
    extracted in Step 1:

    ```bash
    gcloud builds log BUILD_ID --project=P --region=L
    ```

    *   **Guidance**: To avoid overwhelming your context, consider fetching the
        logs incrementally if they are very large. You can start by reading the
        last 20 to 50 lines (e.g., by piping to `tail`) and increase the amount
        if more context is needed.
    *   **Note**: If the command fails with `NOT_FOUND`, verify the region in
        `deploymentMetadata.error.deploymentFailureResolutionInfo.resolutionLink`
        from Step 1.

*   If no build ID is present, proceed to Step 3 using available context.

### Step 3: Analyze the Error and Determine Remediation

*   **Act**: Analyze the error message from Step 1 and the logs from Step 2 to
    determine the root cause and identify the failing component.
*   **Guidance**: Refer to the detailed
    [Error Analysis Guide](error_analysis_guide.md) for heuristics on:
    *   **Identifying the Component**: Mapping Terraform resource names in logs
        to ADC component names.
    *   **Classifying the Error**:
        *   **Config Issues** (validation/compatibility): Fetch the component
            schema (see below) and then proceed to Step 4.
        *   **Resource Conflicts** (already exists): Proceed to Step 4 (to
            update the resource name).
        *   **IAM/API Issues** (permission denied/API disabled): Proceed to
            Step 5.
*   **Fetch Component Schema (Conditional)**: If you identified a configuration
    issue, fetch the schema for the failing component to understand its
    supported parameters.

    *   **Command**:

        ```bash
        gcloud design-center spaces shared-templates describe SHARED_TEMPLATE_NAME --space=SPACE_ID --location=LOCATION --project=PROJECT_ID
        ```

    *   **Note**: The `SHARED_TEMPLATE_NAME`, `SPACE_ID`, `LOCATION`, and
        `PROJECT_ID` must be extracted from the `sharedTemplateRevisionUri`
        found in the application details in Step 1. The URI typically follows
        the format:
        `projects/PROJECT_ID/locations/LOCATION/spaces/SPACE_ID/sharedTemplates/SHARED_TEMPLATE_NAME/revisions/REVISION`.

*   Choose only one remediation path.

### Step 4: Remediation via Configuration Changes

*   **Guidance**: Refer to the [Remediation Guide](remediation_guide.md) for
    details on constructing configuration changes.
*   **Reason**: Determine the necessary changes to the application
    configuration.
*   **Act**: Construct the suggested changes. Since you cannot apply them
    directly via a tool, you must output them in the final response.
*   Proceed to Step 6 to generate output.

### Step 5: Remediation via gcloud Commands (IAM/API)

*   **Guidance**: Refer to the [Remediation Guide](remediation_guide.md) for
    details on constructing `gcloud` commands.
*   **Reason**: Determine missing permissions or disabled APIs.
*   **Act**: Generate the specific `gcloud` commands to fix the issue.
*   **Validate**: Ensure commands are syntactically correct. Use placeholders
    like `<PRINCIPAL>` if the member cannot be determined.
*   Proceed to Step 7 to generate output.

### Step 6: Generate FINAL Output for Configuration Changes

Construct the complete JSON response object strictly matching this schema:

```json
{
  "overall_status": {
    "agent_failed": false,
    "reason": ""
  },
  "troubleshooting_result": {
    "summary": "Concise summary of the error and fix.",
    "troubleshooting_steps": [
      {
        "description": "Description of the change.",
        "component_parameters": [
          {
            "component_uri": "projects/P/locations/L/spaces/S/applicationTemplates/A/components/C",
            "parameters": [
              { "key": "param_key", "value": "param_value" }
            ]
          }
        ]
      }
    ]
  }
}
```

### Step 7: Generate FINAL Output for gcloud Commands

Construct the complete JSON response object strictly matching this schema:

```json
{
  "overall_status": {
    "agent_failed": false,
    "reason": ""
  },
  "troubleshooting_result": {
    "summary": "Concise summary of the error and fix.",
    "troubleshooting_steps": [
      {
        "description": "Description of the command.",
        "gcloud_command": "gcloud command here"
      }
    ]
  }
}
```

### Step 8: Failure Output Generation

If resolution could not be determined:

```json
{
  "overall_status": {
    "agent_failed": true,
    "reason": "Reason for failure"
  },
  "troubleshooting_result": {
    "summary": "",
    "troubleshooting_steps": []
  }
}
```
