---
name: infra-deployment-debugging
description: >-
  Troubleshoots infrastructure deployment failures.
  Use when analyzing deployment failures of ADC applications or direct/raw Terraform deployment errors.
  Don't use for design, local validation, plan assessment, or non-deployment troubleshooting.
license: Apache-2.0
metadata:
  version: v1
  publisher: google
  category: CloudInfrastructure
---

# Deployment Troubleshooting Skill

Use this skill to analyze and resolve deployment failures. This skill supports
troubleshooting:

1.  **ADC Applications**: High-level designs deployed in Application Design
    Center.
2.  **Raw Terraform (TF)**: Infrastructure configurations managed directly via
    Terraform HCL files.

## Constraints

-   **Use standard tools**: All interactions with GCP must be performed using
    standard commands (e.g., `gcloud`, `terraform`). Do not use custom internal
    backend APIs.
-   **No Real Deployments**: NEVER run mutating deployment commands (e.g.,
    `gcloud design-center spaces applications deploy` or `terraform apply`)
    under any circumstances, as this is a read-only validation and dry-run
    troubleshooting workflow.
-   **Read-Only Discovery**: Do not run mutating commands during discovery.
    Discovery must be strictly read-only.
-   **Strict Step Adherence**: You MUST execute all steps in the chosen
    troubleshooting template in sequence. Do NOT skip steps or jump directly to
    remediation, even if the error seems obvious. Every step (e.g., init,
    validate, plan, or describes/logs fetches) must be executed and their
    outputs verified.

## Troubleshooting Process (Progressive Disclosure)

To begin, you must determine the appropriate troubleshooting context based on
the user's input.

### Step 1: Determine the Entry Point

Evaluate the input provided by the user:

-   **Case A: ADC Application Deployment**:
    *   **Indicator**: The input contains an `application_uri` (e.g.,
        `projects/P/locations/L/spaces/S/applications/A`) or references an App
        Design Center context.
    *   **Act**: Read and follow the detailed instructions in
        [adc_application_troubleshooting.md](adc_application_troubleshooting.md)
        to complete the troubleshooting task.
-   **Case B: Raw Terraform Deployment**:
    *   **Indicator**: The input contains raw Terraform configuration code (HCL
        contents), `.tf` files, or direct Terraform plan/apply execution errors.
    *   **Act**: Read and follow the detailed instructions in
        [raw_terraform_troubleshooting.md](raw_terraform_troubleshooting.md)
        to complete the troubleshooting task.
