# Google Cloud Administrative IAM & Permissions Reference

This reference document details the administrative IAM structure and remediation
strategy used by the `google-cloud-recipe-foundation-builder` recipe. It
outlines the 23 recommended administrative roles across 4 core admin groups,
maps key permissions to their respective roles, and explains the lazy role
remediation strategy.

## Table of Contents

-   [Core Administrative Groups & Roles](#core-administrative-groups--roles)
    (L20-L59)
    -   [1. Organization Admin Group](#1-organization-admin-group-9-roles)
        (L24-L35)
    -   [2. Billing Admin Group](#2-billing-admin-group-3-roles) (L36-L41)
    -   [3. Logging/Monitoring Admin Group](#3-loggingmonitoring-admin-group-2-unique-roles)
        (L42-L46)
    -   [4. Security Admin Group](#4-security-admin-group-9-unique-roles)
        (L47-L59)
-   [Permission-to-Role Mapping Table](#permission-to-role-mapping-table)
    (L60-L89)
-   [Remediation](#remediation) (L90-L135)
    -   [1. Organization Admin Group](#1-organization-admin-group-9-roles-1)
        (L94-L103)
    -   [2. Billing Admin Group](#2-billing-admin-group-3-roles-1) (L104-L115)
    -   [3. Logging/Monitoring Admin Group](#3-loggingmonitoring-admin-group-2-roles)
        (L116-L125)
    -   [4. Security Admin Group](#4-security-admin-group-9-roles) (L126-L135)

--------------------------------------------------------------------------------

## Core Administrative Groups & Roles

The recipe aligns with Google Cloud's enterprise setup recommendations by
verifying roles belonging to **4 core administrative groups** (Org Admin,
Billing Admin, Logging/Monitoring Admin, and Security Admin):

### 1. Organization Admin Group (9 Roles)

Responsible for broad administrative controls, folder management, project
creation, and billing link permissions:

-   `roles/resourcemanager.organizationAdmin` (Organization Administrator)
-   `roles/resourcemanager.folderAdmin` (Folder Administrator)
-   `roles/resourcemanager.projectCreator` (Project Creator)
-   `roles/billing.user` (Billing Account User)
-   `roles/iam.organizationRoleAdmin` (IAM Organization Role Administrator)
-   `roles/orgpolicy.policyAdmin` (Organization Policy Administrator)
-   `roles/securitycenter.admin` (Security Center Administrator)
-   `roles/cloudsupport.admin` (Support Account Administrator)
-   `roles/pubsub.admin` (Pub/Sub Publisher/Subscriber Administrator)

### 2. Billing Admin Group (3 Roles)

Manages billing accounts, organization billing creators, and views
organizational assets:

-   `roles/billing.admin` (Billing Account Administrator)
-   `roles/billing.creator` (Billing Account Creator)
-   `roles/resourcemanager.organizationViewer` (Organization Viewer)

### 3. Logging/Monitoring Admin Group (2 Unique Roles)

Configures global logging policies, audit log exports, and centralized metrics
monitoring:

-   `roles/logging.admin` (Logging Administrator)
-   `roles/monitoring.admin` (Monitoring Administrator)

### 4. Security Admin Group (9 Unique Roles)

Audits compliance, sets up security command center, manages keys, and reviews
service accounts:

-   `roles/iam.securityAdmin` (Security Administrator)
-   `roles/iam.securityReviewer` (Security Reviewer)
-   `roles/iam.serviceAccountCreator` (Service Account Creator)
-   `roles/iam.organizationRoleViewer` (IAM Organization Role Viewer)
-   `roles/resourcemanager.folderIamAdmin` (Folder IAM Administrator)
-   `roles/logging.privateLogViewer` (Private Log Viewer)
-   `roles/logging.configWriter` (Log View Config Writer)
-   `roles/container.viewer` (Kubernetes Engine Viewer)
-   `roles/compute.viewer` (Compute Viewer)

## Permission-to-Role Mapping Table

If any permissions are missing from the command outputs, they map directly to
specific roles that should be sequentially granted to the deployment identity:

Resource            | Missing Permission                           | Recommended Role to Grant
:------------------ | :------------------------------------------- | :------------------------
**Organization**    | `resourcemanager.organizations.setIamPolicy` | `roles/resourcemanager.organizationAdmin` (Org Admin)
**Organization**    | `resourcemanager.folders.create`             | `roles/resourcemanager.folderAdmin` (Folder Admin)
**Organization**    | `resourcemanager.projects.create`            | `roles/resourcemanager.projectCreator` (Project Creator)
**Organization**    | `iam.roles.create`                           | `roles/iam.organizationRoleAdmin` (IAM Org Role Admin)
**Organization**    | `orgpolicy.policy.set`                       | `roles/orgpolicy.policyAdmin` (Org Policy Admin)
**Organization**    | `securitycenter.notificationConfigs.create`  | `roles/securitycenter.admin` (Security Center Admin)
**Organization**    | `support.tickets.create`                     | `roles/cloudsupport.admin` (Support Admin)
**Organization**    | `pubsub.topics.create`                       | `roles/pubsub.admin` (Pub/Sub Admin)
**Organization**    | `billing.accounts.create`                    | `roles/billing.creator` (Billing Creator)
**Organization**    | `resourcemanager.organizations.get`          | `roles/resourcemanager.organizationViewer` (Org Viewer)
**Organization**    | `logging.sinks.create`                       | `roles/logging.admin` (Logging Admin)
**Organization**    | `monitoring.services.create`                 | `roles/monitoring.admin` (Monitoring Admin)
**Organization**    | `resourcemanager.organizations.getIamPolicy` | `roles/iam.securityReviewer` (Security Reviewer)
**Organization**    | `iam.serviceAccounts.create`                 | `roles/iam.serviceAccountCreator` (SA Creator)
**Organization**    | `iam.roles.get`                              | `roles/iam.organizationRoleViewer` (IAM Org Role Viewer)
**Organization**    | `resourcemanager.folders.setIamPolicy`       | `roles/resourcemanager.folderIamAdmin` (Folder IAM Admin)
**Organization**    | `logging.privateLogs.list`                   | `roles/logging.privateLogViewer` (Private Log Viewer)
**Organization**    | `container.clusters.list`                    | `roles/container.viewer` (GKE Viewer)
**Organization**    | `compute.instances.list`                     | `roles/compute.viewer` (Compute Viewer)
**Billing Account** | `billing.resourceAssociations.create`        | `roles/billing.user` (Billing Account User)
**Billing Account** | `billing.accounts.update`                    | `roles/billing.admin` (Billing Account Administrator)

--------------------------------------------------------------------------------

## Remediation

When a command fails with a `Permission Denied` error, identify the associated
role from the mapping table and determine its **Administrative Group**. Attempt
to sequentially grant **all** roles in that group to the deployment identity.

### 1. Organization Admin Group (9 roles)

If organization, folder, project, or policy creation fails:

```bash
for role in roles/resourcemanager.organizationAdmin roles/resourcemanager.folderAdmin roles/resourcemanager.projectCreator roles/billing.user roles/iam.organizationRoleAdmin roles/orgpolicy.policyAdmin roles/securitycenter.admin roles/cloudsupport.admin roles/pubsub.admin; do
    gcloud organizations add-iam-policy-binding [ORGANIZATION_ID] \
        --member="user:[YOUR_ACCOUNT_EMAIL]" \
        --role="$role"
done
```

### 2. Billing Admin Group (3 roles)

If billing project link fails, grant at the billing account level:

```bash
for role in roles/billing.admin roles/billing.creator roles/resourcemanager.organizationViewer; do
    gcloud billing accounts add-iam-policy-binding [BILLING_ACCOUNT_ID] \
        --member="user:[YOUR_ACCOUNT_EMAIL]" \
        --role="$role"
done
```

And also ensure the active identity has `roles/billing.user` (which is part of
the Organization Admin Group) at the organization level.

### 3. Logging/Monitoring Admin Group (2 roles)

If logging configuration or metrics scope linking fails:

```bash
for role in roles/logging.admin roles/monitoring.admin; do
    gcloud organizations add-iam-policy-binding [ORGANIZATION_ID] \
        --member="user:[YOUR_ACCOUNT_EMAIL]" \
        --role="$role"
done
```

### 4. Security Admin Group (9 roles)

If security reviews or folder IAM failures occur:

```bash
for role in roles/iam.securityAdmin roles/iam.securityReviewer roles/iam.serviceAccountCreator roles/iam.organizationRoleViewer roles/resourcemanager.folderIamAdmin roles/logging.privateLogViewer roles/logging.configWriter roles/container.viewer roles/compute.viewer; do
    gcloud organizations add-iam-policy-binding [ORGANIZATION_ID] \
        --member="user:[YOUR_ACCOUNT_EMAIL]" \
        --role="$role"
done
```
