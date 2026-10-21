# IAM v1 Allow Policies

This reference guide details how to manage IAM v1 allow policies across Resource
Manager hierarchy levels (Organization, Folder, Project) and individual
resources using `gcloud` CLI.

--------------------------------------------------------------------------------

## Table of Contents

-   [1. Overview](#1-overview)
-   [2. Managing Allow Policies via gcloud CLI](#2-managing-allow-policies-via-gcloud-cli)
-   [3. Verification Plan](#3-verification-plan)

--------------------------------------------------------------------------------

## 1. Overview

IAM v1 allow policies define who (principals) has what access (roles) on which
Google Cloud resources.

### Principals (Who Is Granted Access)

The following are examples of principal types that are supported in allow
policies (see
[Principal Identifiers Overview](https://docs.cloud.google.com/iam/docs/principals-overview)
for a comprehensive list):

*   **Users**: Google accounts (e.g., `user:alice@example.com`).
*   **Service Accounts**: Application identities (e.g.,
    `serviceAccount:my-sa@PROJECT_ID.iam.gserviceaccount.com`).
*   **Google Groups**: Groups of users (e.g.,
    `group:security-team@example.com`).
*   **Domains**: Google Workspace or Cloud Identity domains (e.g.,
    `domain:example.com`).
*   **Special Identifiers**: `allUsers` (public) and `allAuthenticatedUsers`
    (authenticated Google accounts).

### Resources (Where Policies Apply)

*   **Resource Manager Resources**: Applies at the organization, folder, and
    project resource levels. Permissions inherit down the resource hierarchy.
*   **Service-specific Resources**: Applies to individual resources across
    supported services. These allow policies complement policies inherited from
    ancestor resources (projects, folders, and organizations).

### Roles (What Is Granted)

*   **Predefined Roles**: Managed by Google Cloud, providing curated collections
    of permissions for common use cases.
*   **Custom Roles**: User-defined collections of granular permissions tailored
    to specific least-privilege requirements.

--------------------------------------------------------------------------------

## 2. Managing Allow Policies via gcloud CLI

### A. Resource Manager Resources

#### 1. Project Level

```bash
# Add Policy Binding
gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="user:alice@example.com" \
    --role="roles/viewer"

# Remove Policy Binding
gcloud projects remove-iam-policy-binding PROJECT_ID \
    --member="user:alice@example.com" \
    --role="roles/viewer"

# Set Policy (bulk replacement)
gcloud projects set-iam-policy PROJECT_ID policy.json

# Get Policy
gcloud projects get-iam-policy PROJECT_ID --format=json

# List Testable Permissions for Resource
gcloud iam list-testable-permissions //cloudresourcemanager.googleapis.com/projects/PROJECT_ID
```

#### 2. Folder Level

```bash
# Add Policy Binding
gcloud resource-manager folders add-iam-policy-binding FOLDER_ID \
    --member="serviceAccount:my-sa@PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/resourcemanager.folderViewer"

# Set Policy (bulk replacement)
gcloud resource-manager folders set-iam-policy FOLDER_ID policy.json

# Get Policy
gcloud resource-manager folders get-iam-policy FOLDER_ID --format=yaml

# Remove Policy Binding
gcloud resource-manager folders remove-iam-policy-binding FOLDER_ID \
    --member="serviceAccount:my-sa@PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/resourcemanager.folderViewer"
```

#### 3. Organization Level

```bash
# Add Policy Binding
gcloud organizations add-iam-policy-binding ORG_ID \
    --member="group:security-team@example.com" \
    --role="roles/iam.securityReviewer"

# Set Policy (bulk replacement)
gcloud organizations set-iam-policy ORG_ID policy.json

# Get Policy
gcloud organizations get-iam-policy ORG_ID --format=json

# Remove Policy Binding
gcloud organizations remove-iam-policy-binding ORG_ID \
    --member="group:security-team@example.com" \
    --role="roles/iam.securityReviewer"
```

--------------------------------------------------------------------------------

### B. Service-specific Resources

For services supporting allow policies for service-specific resources, the
command structure follows a consistent pattern across Google Cloud services:

```bash
# Add role binding for a service-specific resource
gcloud <service> <resource-type> add-iam-policy-binding <RESOURCE_ID> \
    --member="<MEMBER>" \
    --role="<ROLE>"

# Get allow policy for a service-specific resource
gcloud <service> <resource-type> get-iam-policy <RESOURCE_ID>

# Set allow policy for a service-specific resource
gcloud <service> <resource-type> set-iam-policy <RESOURCE_ID> policy.json

# Remove role binding for a service-specific resource
gcloud <service> <resource-type> remove-iam-policy-binding <RESOURCE_ID> \
    --member="<MEMBER>" \
    --role="<ROLE>"
```

--------------------------------------------------------------------------------

## 3. Verification Plan

Depending on which mutating allow policy operation was executed
(`add-iam-policy-binding`, `remove-iam-policy-binding`, or `set-iam-policy`),
run **only** its corresponding verification step before reporting completion:

1.  **After `add-iam-policy-binding`**:

    *   **Action**: Run `get-iam-policy` on the target resource (project,
        folder, organization, or resource).
    *   **Verification**: Ensure the target principal is listed under the
        assigned role in the policy bindings.

2.  **After `remove-iam-policy-binding`**:

    *   **Action**: Run `get-iam-policy` on the target resource.
    *   **Verification**: Ensure the target principal is no longer listed under
        the role in the policy bindings (or that the binding is removed if no
        other members remain).

3.  **After `set-iam-policy`**:

    *   **Action**: Run `get-iam-policy` on the target resource.
    *   **Verification**: Verify that the returned policy matches the full
        intended policy state defined in the applied policy file.

After verifying the mutating change, remind the user that IAM allow policy
changes take up to 7 minutes or longer to propagate across Google Cloud global
infrastructure.
