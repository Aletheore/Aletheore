# Raw Terraform Troubleshooting Process

Use this template when troubleshooting direct/raw Terraform deployment errors.

## Troubleshooting Process

Follow these steps in sequence to analyze and resolve the Terraform deployment
failure:

### Step 1: Gather Input Terraform Code

*   **Act**: Identify all Terraform configuration files in the workspace (e.g.,
    `main.tf`, `variables.tf`, `outputs.tf`, `providers.tf`).
*   **Verify**: Read and understand their contents.

### Step 2: Initialize Terraform

*   **Act**: Run the following command in the workspace directory containing the
    HCL files to initialize Terraform and download required modules and
    providers:

    ```bash
    terraform init
    ```

*   **Observe**:

    *   If it fails, analyze the error message.
    *   If it succeeds, proceed to Step 3.

### Step 3: Run Validation

*   **Act**: Run the following command to check if the configuration is
    syntactically valid and internally consistent:

    ```bash
    terraform validate
    ```

*   **Observe**:

    *   If it fails, capture the error message and proceed to Step 5 (Analyze
        and Fix).
    *   If it succeeds, proceed to Step 4.

### Step 4: Run Dry-Run Plan

*   **Act**: Run the following command to create a execution plan and verify
    against the cloud provider's schemas and constraints:

    ```bash
    terraform plan
    ```

*   **Observe**:

    *   If it fails, capture the error message and proceed to Step 5 (Analyze
        and Fix).
    *   If it succeeds, and you have made modifications that resolved previous
        errors, proceed to Step 6 (Generate Output).

### Step 5: Analyze and Fix

*   **Analyze the Error**:
    *   If the error message references a file under `.terraform/modules/`
        (e.g., `.terraform/modules/secret-manager-1/variables.tf`), it means the
        error is due to a mismatch between your configuration and the module's
        expected inputs.
    *   **Act**: Read the referenced module file in `.terraform/modules/` using
        `view_file` to inspect the variable definition, its type constraints,
        default values, and required attributes.
    *   If the error is a Terraform HCL syntax error (e.g., duplicate keys,
        invalid type), locate the line in your local files.
*   **Apply the Fix**:
    *   Modify your local Terraform files (do NOT modify files inside
        `.terraform/modules/`) to fix the error. For example:
        *   Add missing required attributes to variables or module inputs.
        *   Change attribute types to match module schemas.
        *   Remove duplicate block definitions or keys.
*   **Format Files**:
    *   Run `terraform fmt <file_name>` on every Terraform file you modified to
        ensure consistent formatting (spacing, alignment, etc.).
*   **Re-Verify**:
    *   After applying the fix and formatting the files, run `terraform
        validate` (Step 3).
    *   If validation passes, run `terraform plan` (Step 4).
    *   Repeat this loop (Fix -> Format -> Validate -> Plan) until both commands
        succeed without errors.

### Step 6: Generate Output

Construct the complete JSON response object containing the final, fixed
Terraform files and a summary of the troubleshooting.

*   **CRITICAL Constraint**: NEVER run `terraform apply` or any other mutating
    commands under any circumstances. Only use `terraform init`, `terraform
    validate`, and `terraform plan` for dry-run analysis.
*   **Format**: The output must strictly follow the schema below.

```json
{
  "overall_status": {
    "agent_failed": false,
    "reason": ""
  },
  "troubleshooting_result": {
    "summary": "Detailed summary explaining the root cause of the error and how the configuration was fixed.",
    "terraform_files": [
      {
        "name": "main.tf",
        "content": "..."
      },
      {
        "name": "variables.tf",
        "content": "..."
      }
    ]
  }
}
```

If you are unable to resolve the error after multiple attempts (e.g. because of
missing credentials or invalid schemas that cannot be fixed), generate a failure
response:

```json
{
  "overall_status": {
    "agent_failed": true,
    "reason": "Explain why the agent failed to fix the error (e.g., 'terraform init failed with network error')."
  },
  "troubleshooting_result": {
    "summary": "",
    "terraform_files": []
  }
}
```
