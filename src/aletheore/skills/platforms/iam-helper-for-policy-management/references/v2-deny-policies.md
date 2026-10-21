# IAM v2 Deny Policies

This reference guide details how to create, update, list, inspect, and delete
IAM v2 deny policies across Resource Manager attachment points (Organization,
Folder, Project) using the `gcloud` CLI.

--------------------------------------------------------------------------------

## Table of Contents

-   [1. IAM v2 Overview & Mechanics](#1-iam-v2-overview-mechanics)
-   [2. Deny Rule Structure & Specifications](#2-deny-rule-structure-specifications)
-   [3. Deny Policy Commands via gcloud CLI](#3-deny-policy-commands-via-gcloud-cli)
-   [4. Long-Running Operations (LRO) & Polling](#4-long-running-operations-lro-polling)
-   [5. Verification Plan](#5-verification-plan)

--------------------------------------------------------------------------------

## 1. IAM v2 Overview & Mechanics

IAM deny policies define guardrails that prevent designated principals from
using specified permissions, regardless of any roles granted by IAM allow
policies.

*   **Precedence**: Deny rules are evaluated **before** allow rules. If a
    permission is denied by a v2 policy, access is blocked even if an allow
    policy grants the role.
*   **Attachment Points**:
    *   **Organization**:
        `cloudresourcemanager.googleapis.com/organizations/ORG_ID` (numeric ID)
    *   **Folder**: `cloudresourcemanager.googleapis.com/folders/FOLDER_ID`
        (numeric ID)
    *   **Project**: `cloudresourcemanager.googleapis.com/projects/PROJECT_ID`
*   **Required Permissions**: `roles/iam.denyAdmin` (Deny Admin) on the target
    attachment point.

--------------------------------------------------------------------------------

## 2. Deny Rule Structure & Specifications

A deny policy contains rules specifying denied principals, exception principals,
denied permissions, and optional CEL conditions.

### Deny Policy File Specification (YAML)

```yaml
displayName: Block Public Deletion
rules:
- denyRule:
    deniedPrincipals:
    - "principalSet://goog/public:all"
    exceptionPrincipals:
    - "principal://goog/subject/admin@example.com"
    deniedPermissions:
    - "iam.googleapis.com/roles.delete"
    - "resourcemanager.projects.delete"
    denialCondition:
      title: "Block_Public_Access"
      expression: "request.time < timestamp('2030-01-01T00:00:00Z')"
```

#### Principal Identifiers in v2

*   `principalSet://goog/public:all` (All users on the internet)
*   `principal://goog/subject/USER_EMAIL` (Specific user)
*   `principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/serviceaccounts/SA_EMAIL`
    (Specific service account)
*   `principalSet://goog/group/GROUP_EMAIL` (Google Group)

#### Permission Identifiers in v2

v2 denied permissions take the format `SERVICE.googleapis.com/PERMISSION_NAME`,
for example:

*   `iam.googleapis.com/roles.delete`
*   `resourcemanager.projects.delete`
*   `storage.googleapis.com/buckets.delete`

--------------------------------------------------------------------------------

## 3. Deny Policy Commands via gcloud CLI

### A. Create Deny Policy

```bash
# Project Level
gcloud iam policies create POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/projects/PROJECT_ID" \
    --kind=denypolicies \
    --policy-file=deny_policy.yaml

# Folder Level
gcloud iam policies create POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/folders/FOLDER_ID" \
    --kind=denypolicies \
    --policy-file=deny_policy.yaml

# Organization Level
gcloud iam policies create POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/organizations/ORG_ID" \
    --kind=denypolicies \
    --policy-file=deny_policy.yaml
```

### B. Update Deny Policy

```bash
# Project Level
gcloud iam policies update POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/projects/PROJECT_ID" \
    --kind=denypolicies \
    --policy-file=updated_deny_policy.yaml

# Folder Level
gcloud iam policies update POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/folders/FOLDER_ID" \
    --kind=denypolicies \
    --policy-file=updated_deny_policy.yaml

# Organization Level
gcloud iam policies update POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/organizations/ORG_ID" \
    --kind=denypolicies \
    --policy-file=updated_deny_policy.yaml
```

### C. List & Get Deny Policies

```bash
# List Deny Policies
# Project Level
gcloud iam policies list \
    --attachment-point="cloudresourcemanager.googleapis.com/projects/PROJECT_ID" \
    --kind=denypolicies

# Folder Level
gcloud iam policies list \
    --attachment-point="cloudresourcemanager.googleapis.com/folders/FOLDER_ID" \
    --kind=denypolicies

# Organization Level
gcloud iam policies list \
    --attachment-point="cloudresourcemanager.googleapis.com/organizations/ORG_ID" \
    --kind=denypolicies

# Get Deny Policy Details
# Project Level
gcloud iam policies get POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/projects/PROJECT_ID" \
    --kind=denypolicies

# Folder Level
gcloud iam policies get POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/folders/FOLDER_ID" \
    --kind=denypolicies

# Organization Level
gcloud iam policies get POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/organizations/ORG_ID" \
    --kind=denypolicies
```

### D. Delete Deny Policy

```bash
# Project Level
gcloud iam policies delete POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/projects/PROJECT_ID" \
    --kind=denypolicies

# Folder Level
gcloud iam policies delete POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/folders/FOLDER_ID" \
    --kind=denypolicies

# Organization Level
gcloud iam policies delete POLICY_NAME \
    --attachment-point="cloudresourcemanager.googleapis.com/organizations/ORG_ID" \
    --kind=denypolicies
```

--------------------------------------------------------------------------------

## 4. Long-Running Operations (LRO) & Polling

Mutating deny policy commands (`create`, `update`, `delete`) execute
asynchronously as Long-Running Operations.

1.  **Automatic Wait**: `gcloud` CLI automatically polls and waits for
    operations to complete before returning.
2.  **Manual LRO Inspection**: To check the status of an operation explicitly,
    run the following command:

    ```bash
    gcloud iam operations describe OPERATION_ID
    ```

    Check that the returned status has `done: true` and contains no error
    payload.

--------------------------------------------------------------------------------

## 5. Verification Plan

Depending on which mutating deny policy operation was executed (`create`,
`update`, or `delete`), run **only** its corresponding verification step to
confirm the change:

1.  **After `create`**:

    *   **Action**: Run `gcloud iam policies get POLICY_NAME
        --attachment-point="<ATTACHMENT_POINT>" --kind=denypolicies`.
    *   **Verification**: Ensure the deny policy is returned with the specified
        rules and principal/permission bindings.

2.  **After `update`**:

    *   **Action**: Run `gcloud iam policies get POLICY_NAME
        --attachment-point="<ATTACHMENT_POINT>" --kind=denypolicies`.
    *   **Verification**: Verify that the rules and metadata reflect the updated
        configuration.

3.  **After `delete`**:

    *   **Action**: Run `gcloud iam policies list
        --attachment-point="<ATTACHMENT_POINT>" --kind=denypolicies`.
    *   **Verification**: Ensure the deleted policy name is no longer listed
        under the attachment point.

After verifying the mutating change, remind the user that IAM deny policy
changes take up to 7 minutes or longer to propagate across Google Cloud global
infrastructure.
