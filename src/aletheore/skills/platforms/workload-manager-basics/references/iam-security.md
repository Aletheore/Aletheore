# Workload Manager IAM and Security

Use least privilege and start read-only. Evaluation creation and runs can scan
resource metadata across a project, folder, or organization, so scope and role
choice matter.

## Common Roles

| Role                                     | Use                               |
| ---------------------------------------- | --------------------------------- |
| `roles/workloadmanager.viewer`           | Read Workload Manager resources.  |
| `roles/workloadmanager.evaluationViewer` | Read evaluation resources and     |
:                                          : results.                          :
| `roles/workloadmanager.evaluationAdmin`  | Create, update, run, and delete   |
:                                          : evaluations and executions.       :
| `roles/workloadmanager.admin`            | Full Workload Manager             |
:                                          : administration.                   :
| `roles/workloadmanager.deploymentViewer` | Read deployment resources exposed |
:                                          : by the REST API.                  :
| `roles/workloadmanager.deploymentAdmin`  | Manage deployment resources       |
:                                          : exposed by the REST API.          :
| `roles/workloadmanager.insightWriter`    | Write or delete Workload Manager  |
:                                          : insights exposed by the REST API. :
| `roles/workloadmanager.workloadViewer`   | View workload resources and       |
:                                          : metadata.                         :
| `roles/workloadmanager.worker`           | Worker execution role for         |
:                                          : service-managed operations.       :
| `roles/workloadmanager.serviceAgent`     | Service agent role; do not grant  |
:                                          : to humans or general automation   :
:                                          : identities.                       :

## Role Selection

-   Listing rules, evaluations, executions, results, and scanned resources:
    `roles/workloadmanager.viewer` or `roles/workloadmanager.evaluationViewer`.
-   Creating or updating evaluations: `roles/workloadmanager.evaluationAdmin`.
-   Running evaluations: `roles/workloadmanager.evaluationAdmin`.
-   Full administration across Workload Manager resources:
    `roles/workloadmanager.admin`.
-   Folder or organization scope: grant roles at that scope only when project
    scope cannot answer the request.

## Data Handling

-   Results can include resource names, service accounts, labels, observed
    settings, violation messages, remediation commands, and documentation URLs.
-   BigQuery export datasets should have restricted dataset-level IAM.
-   Logs and client library debug output can include request metadata. Do not
    persist debug logs in broad-access locations.
-   Use a dedicated automation service account instead of user credentials for
    recurring evaluations.

## Workload Manager Service Agent & Service Account

To evaluate workloads or perform deployments, Workload Manager requires service
identities with appropriate metadata reading and resource actuation roles:

### 1. Google-Managed Service Agent (Evaluation/Validation)

When evaluations are executed, the Workload Manager Google-managed service agent
(`service-PROJECT_NUMBER@gcp-sa-workloadmanager.iam.gserviceaccount.com`) scans
GCP resources in the defined evaluation scope.

*   **Required Permissions**: The service agent must be granted roles that allow
    it to read resource configurations.
*   **Roles to Grant**:
    *   `roles/viewer` or `roles/browser` at the project, folder, or
        organization level of the evaluation scope to allow metadata scanning of
        Compute Engine, Cloud SQL, GKE, and other resources.
    *   For detailed role mappings, refer to the
        [Workload Manager Evaluation Roles documentation](https://docs.cloud.google.com/workload-manager/docs/evaluate/roles).

### 2. Deployment Service Account (Actuation/Deployments)

If you are using Workload Manager to automate deployments (e.g., deploying the
SAP agent or other enterprise software):

*   You must create a dedicated, customer-managed deployment Service Account in
    your project.
*   **Required Permissions**: The deployment service account requires
    permissions to write metadata and deploy agents on Compute Engine VM
    instances.
*   For setup steps, refer to the
    [Workload Manager Deployment Service Account Prerequisites](https://docs.cloud.google.com/workload-manager/docs/deploy/prerequisites#wlm-service-account).

## CMEK

`Evaluation.kms_key` accepts a key in this format:

```text
projects/PROJECT_ID/locations/LOCATION/keyRings/KEY_RING/cryptoKeys/KEY
```

Make sure the Workload Manager service agent has the needed KMS permissions
before creating a CMEK-backed evaluation.

## Deletion and Idempotency

-   Use `request_id` for create, update, run, and delete requests when
    available.
-   Use `force=true` on evaluation deletion only when child executions should be
    deleted as part of the same request.
-   Before deleting, list executions and confirm whether any results need to be
    retained or exported.
