# IAM Remediation Playbook (`remediation_iam.md`)

Use this playbook when remediating Security Command Center findings related to
over-privileged service accounts, basic role bindings, or unrotated service
account keys. This covers the Security Health Analytics categories
`PRIMITIVE_ROLES_USED`, `OVER_PRIVILEGED_SERVICE_ACCOUNT_USER`,
`ADMIN_SERVICE_ACCOUNT`, `SERVICE_ACCOUNT_ROLE_SEPARATION`,
`KMS_ROLE_SEPARATION`, `USER_MANAGED_SERVICE_ACCOUNT_KEY`, and
`SERVICE_ACCOUNT_KEY_NOT_ROTATED`, plus the IAM recommender categories
`IAM_ROLE_HAS_EXCESSIVE_PERMISSIONS`, `UNUSED_IAM_ROLE`,
`SERVICE_AGENT_GRANTED_BASIC_ROLE`, and
`SERVICE_AGENT_ROLE_REPLACED_WITH_BASIC_ROLE`.

**Usage and consent**: This playbook is a reference for the
`google-cloud-scc-remediation` skill and inherits that skill's consent gate,
even if you reached this file directly. Every mutating command below MUST be
presented to the user as part of a remediation plan, together with its
verification command, and MUST NOT be executed until the user answers "Do you
approve executing this remediation plan?". Never execute a mutating command in
the same turn you propose it.

## Table of Contents

*   [1. Core Principles](#1-core-principles)
*   [2. Inspecting Current IAM Bindings](#2-inspecting-current-iam-bindings)
*   [3. Standard Remediation Patterns](#3-standard-remediation-patterns)
    *   [A. Revoking Overly Broad Basic Roles](#a-revoking-overly-broad-basic-roles)
    *   [B. Remediating Default Service Account Overuse](#b-remediating-default-service-account-overuse)
    *   [C. User-Managed Service Account Key Management & Rotation](#c-user-managed-service-account-key-management--rotation)
    *   [D. Separation of Duties & Sensitive IAM Roles](#d-separation-of-duties--sensitive-iam-roles)
    *   [E. Scoping Service Account Impersonation](#e-scoping-service-account-impersonation)
*   [4. Verification Check](#4-verification-check)

## 1. Core Principles

*   **Principle of Least Privilege**: Replace basic roles — `roles/admin`,
    `roles/writer`, `roles/reader`, and the legacy `roles/owner`,
    `roles/editor`, `roles/viewer` — or other overly broad roles with predefined
    or custom roles scoped strictly to required API actions.
*   **Check Cross-Project Dependencies**: You shouldn't try to determine this
    yourself. Before revoking a binding from a service account, ask the user to
    confirm whether other services or CI/CD pipelines rely on those permissions,
    and tell them which breakages to look for. Never assert that a revocation is
    safe.

## 2. Inspecting Current IAM Bindings

Before drafting a fix, inspect the current IAM policy bindings for the affected
resource:

```bash
# Check project-level IAM policy for a specific member
gcloud projects get-iam-policy {PROJECT_ID} \
    --flatten="bindings[].members" \
    --format="table(bindings.role)" \
    --filter="bindings.members:{MEMBER_EMAIL}"
```

## 3. Standard Remediation Patterns

### A. Revoking Overly Broad Basic Roles

When a principal holds a basic role (`roles/admin`, `roles/writer`, or the
legacy `roles/owner`, `roles/editor`), revoke the basic role after identifying
the specific replacement role required:

```bash
# 1. Grant the least-privilege replacement role first (to prevent outage)
gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{SERVICE_ACCOUNT_EMAIL}" \
    --role="{LEAST_PRIVILEGE_ROLE}"

# 2. Remove the troublesome basic role
gcloud projects remove-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{SERVICE_ACCOUNT_EMAIL}" \
    --role="{BASIC_ROLE}"
```

### B. Remediating Default Service Account Overuse

If a workload is using the default Compute Engine or App Engine service account
with editor permissions:

1.  Recommend creating a dedicated user-managed service account.
2.  Grant only the minimal required IAM roles to the new service account.
3.  Update the workload to run as the new service account.

### C. User-Managed Service Account Key Management & Rotation

When a finding reports unrotated or expired user-managed service account keys
(`USER_MANAGED_SERVICE_ACCOUNT_KEY`, `SERVICE_ACCOUNT_KEY_NOT_ROTATED`):

1.  **Prefer Workload Identity / Short-Lived Credentials**: Recommend migrating
    workloads to Workload Identity Federation or attached service accounts to
    eliminate exported keys.
2.  **Disable the Stale Key** (reversible):

    ```bash
    # List user-managed keys. The default table includes a DISABLED column, so
    # this doubles as the verification command for the disable step below.
    gcloud iam service-accounts keys list \
        --iam-account="{SERVICE_ACCOUNT_EMAIL}" \
        --managed-by=user

    # Disable the unrotated or expired key
    gcloud iam service-accounts keys disable {KEY_ID} \
        --iam-account="{SERVICE_ACCOUNT_EMAIL}"
    ```

3.  **Verify**: re-run the `keys list` command above and confirm the key shows
    `True` in the `DISABLED` column.
4.  **Roll Back if Needed**: disabling is reversible while the key still exists.
    If workloads break, restore access immediately with:

    ```bash
    gcloud iam service-accounts keys enable {KEY_ID} \
        --iam-account="{SERVICE_ACCOUNT_EMAIL}"
    ```

5.  **Delete Only After Confirming Health** (NOT reversible): once workloads
    have been confirmed healthy with the key disabled, delete it:

    ```bash
    gcloud iam service-accounts keys delete {KEY_ID} \
        --iam-account="{SERVICE_ACCOUNT_EMAIL}"
    ```

### D. Separation of Duties & Sensitive IAM Roles

When a finding flags conflicting administrative and operational roles assigned
to the same principal (e.g. `KMS_ROLE_SEPARATION`):

1.  Identify the operational role (e.g.,
    `roles/cloudkms.cryptoKeyEncrypterDecrypter`) vs the administrative role
    (`roles/cloudkms.admin`).
2.  Revoke the administrative role from the workload principal, ensuring
    management and data plane operations are segregated across separate
    identities.

### E. Scoping Service Account Impersonation

When a finding reports overly broad `roles/iam.serviceAccountTokenCreator` or
`roles/iam.serviceAccountUser` granted at the project level:

1.  Remove the project-level role binding.
2.  Grant the role only on the specific service account resource that the
    principal needs to impersonate.

## 4. Verification Check

After applying the IAM policy change, verify that the troublesome binding has
been removed:

```bash
gcloud projects get-iam-policy {PROJECT_ID} \
    --flatten="bindings[].members" \
    --format="table(bindings.role)" \
    --filter="bindings.members:{MEMBER_EMAIL} AND bindings.role:{TROUBLESOME_ROLE}"
```

If the command returns no rows, the remediation is successful. The `--flatten`
flag is required: without it, the filter matches whenever *any* binding has the
member and *any* binding has the role, yielding a false positive on an
already-remediated policy.
