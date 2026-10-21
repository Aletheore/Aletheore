# Google Cloud Organization Policies Reference

This reference document details the baseline Organization Policies enforced by
the `google-cloud-recipe-foundation-builder` recipe. It provides the specific
constraint names, whether they are Boolean or List types, their target
configurations, and standard YAML templates for deployment.

## Table of Contents

-   [Overview of Constraints](#overview-of-constraints) (L20-L24)
-   [Enforced Boolean Constraints](#enforced-boolean-constraints) (L26-L101)
-   [List Constraints](#list-constraints) (L102-L173)
    -   [VM External IP Access](#vm-external-ip-access-deny-all) (L108-L120)
    -   [Restrict Protocol Forwarding Creation](#restrict-protocol-forwarding-creation-allow-internal)
        (L121-L136)
    -   [Allowed Policy Member Domains](#allowed-policy-member-domains-allow-customer-id)
        (L137-L155)
    -   [Allowed Contact Domains](#allowed-contact-domains-allow-org-domain)
        (L156-L171)
-   [Deployment Directive](#deployment-directive) (L174-L186)

--------------------------------------------------------------------------------

## Overview of Constraints

The baseline security posture enforces Boolean constraints (set to Enforced) and
List constraints (restricting allowed or denied values).

--------------------------------------------------------------------------------

## Enforced Boolean Constraints

These policies are binary and are set to `enforce: true`.

### Target YAML Structure

For all Boolean constraints, use the following YAML template. Replace
`[ORGANIZATION_ID]` and `[CONSTRAINT_NAME]` accordingly.

```yaml
name: organizations/[ORGANIZATION_ID]/policies/[CONSTRAINT_NAME]
spec:
  rules:
  - enforce: true
```

### List of Boolean Constraints

1.  **Disable Automatic IAM Grants for Default Service Accounts**
    *   **Constraint:** `iam.automaticIamGrantsForDefaultServiceAccounts`
    *   **Description:** Prevents the default App Engine and Compute Engine
        service accounts from automatically being granted the Editor role on
        your projects.
2.  **Public Access Prevention**
    *   **Constraint:** `storage.publicAccessPrevention`
    *   **Description:** Enforces public access prevention on all Cloud Storage
        buckets in the organization, blocking public access via IAM policies.
3.  **Set New Project Default to Zonal DNS Only**
    *   **Constraint:** `compute.setNewProjectDefaultToZonalDNSOnly`
    *   **Description:** Ensures that new projects use only zonal DNS names for
        Compute Engine instances, preventing internal DNS name leakage across
        zones.
4.  **Uniform Bucket-Level Access**
    *   **Constraint:** `storage.uniformBucketLevelAccess`
    *   **Description:** Enforces uniform bucket-level access on all Cloud
        Storage buckets, disabling fine-grained ACLs and relying solely on IAM.
5.  **Restrict Public IP Access for Cloud SQL**
    *   **Constraint:** `sql.restrictPublicIp`
    *   **Description:** Prevents Cloud SQL instances from being created with
        public IP addresses, enforcing private IP connectivity.
6.  **Disable Service Account Key Creation**
    *   **Constraint:** `iam.disableServiceAccountKeyCreation`
    *   **Description:** Prevents users from creating new external service
        account keys, reducing the risk of credential leakage.
7.  **Disable Serial Port Access**
    *   **Constraint:** `compute.disableSerialPortAccess`
    *   **Description:** Disables serial port access to all Compute Engine
        virtual machine instances, securing the console access.
8.  **Restrict Shared VPC Project Lien Removal**
    *   **Constraint:** `compute.restrictXpnProjectLienRemoval`
    *   **Description:** Restricts the removal of liens on host projects for
        Shared VPC, preventing accidental deletion of critical networking
        projects.
9.  **Disable VPC External IPv6**
    *   **Constraint:** `compute.disableVpcExternalIpv6`
    *   **Description:** Prevents the allocation of external IPv6 addresses to
        VPC subnets and resources, limiting external exposure.
10. **Disable Nested Virtualization**
    *   **Constraint:** `compute.disableNestedVirtualization`
    *   **Description:** Disables hardware-assisted nested virtualization on
        Compute Engine VMs, mitigating side-channel attack vectors.
11. **Disable Service Account Key Upload**
    *   **Constraint:** `iam.disableServiceAccountKeyUpload`
    *   **Description:** Prevents users from uploading public keys to service
        accounts, blocking external key association.
12. **Require OS Login**
    *   **Constraint:** `compute.requireOsLogin`
    *   **Description:** Enforces OS Login on all Compute Engine instances,
        linking VM SSH access to the user's Google identity.
13. **Restrict Authorized Networks for Cloud SQL**
    *   **Constraint:** `sql.restrictAuthorizedNetworks`
    *   **Description:** Blocks the addition of non-private IP ranges to the
        authorized networks list of Cloud SQL instances.

--------------------------------------------------------------------------------

## List Constraints

List constraints define specific allowed or denied values. Use the specific
templates below for each policy.

### VM External IP Access (Deny All)

*   **Constraint:** `compute.vmExternalIpAccess`
*   **Description:** Prevents Compute Engine VMs from being assigned external
    (public) IP addresses.
*   **YAML Template:**

    ```yaml
    name: organizations/[ORGANIZATION_ID]/policies/compute.vmExternalIpAccess
    spec:
      rules:
      - denyAll: true
    ```

### Restrict Protocol Forwarding Creation (Allow INTERNAL)

*   **Constraint:** `compute.restrictProtocolForwardingCreationForTypes`
*   **Description:** Restricts protocol forwarding rule creation to internal
    targets only.
*   **YAML Template:**

    ```yaml
    name: organizations/[ORGANIZATION_ID]/policies/compute.restrictProtocolForwardingCreationForTypes
    spec:
      rules:
      - values:
          allowedValues:
          - INTERNAL
    ```

### Allowed Policy Member Domains (Allow Customer ID)

*   **Constraint:** `iam.allowedPolicyMemberDomains`
*   **Description:** Restricts the domain identities that can be added to IAM
    policies to your specific Google Workspace or Cloud Identity customer ID.
*   **YAML Template:**

    ```yaml
    name: organizations/[ORGANIZATION_ID]/policies/iam.allowedPolicyMemberDomains
    spec:
      rules:
      - values:
          allowedValues:
          - [DIRECTORY_CUSTOMER_ID] # Replace with your numeric customer ID (e.g., C01234567)
    ```

    *(Note: Alternatively, you can use the principal set format:
    `principalSet://iam.googleapis.com/organizations/[ORGANIZATION_ID]`)*

### Allowed Contact Domains (Allow Org Domain)

*   **Constraint:** `essentialcontacts.allowedContactDomains`
*   **Description:** Restricts the domains that can be registered as essential
    contacts for the organization to your official domain.
*   **YAML Template:**

    ```yaml
    name: organizations/[ORGANIZATION_ID]/policies/essentialcontacts.allowedContactDomains
    spec:
      rules:
      - values:
          allowedValues:
          - @[YOUR_DOMAIN] # Replace with your organization's domain (e.g., @example.com)
    ```

--------------------------------------------------------------------------------

## Deployment Directive

Apply these policies sequentially using the `gcloud org-policies set-policy`
command:

```bash
gcloud org-policies set-policy [POLICY_FILE_NAME].yaml
```

> [!CAUTION] Applying `iam.allowedPolicyMemberDomains` first can lock out the
> deployment identity if it resides in an unallowed domain. Ensure the
> deployment identity is safe before enforcing this policy.
