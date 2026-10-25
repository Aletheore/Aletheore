# Developer & Access Requester Flow

This guide details how developers and access requesters handle IAM access denials, ranging from self-service just-in-time (JIT) elevations and local project remediation to logging structured tickets.

---

## Step 1: Capture Error Context

Extract the necessary parameters from the failed operation:

* **Option A (Error ID - Preferred):** Extract the base64-encoded `error_info_id` / `ERROR_ID` (for example, `ZGVuaWVkX2J5X2RlbmlhbF9wb2xpY3k...`) from the 403 API error response.
* **Option B (Manual Parameters):**
  * `PRINCIPAL_EMAIL`: The identity executing the task.
  * `IAM_PERMISSION`: The exact missing permission (for example, `bigquery.datasets.create`).
  * `RESOURCE_URI`: The target resource URI (for example, `//cloudresourcemanager.googleapis.com/projects/PROJECT_ID`).

---

## Step 2: Diagnostic Check (Attempt Self-Diagnosis)

Attempt to verify why access was denied using available tools:

1. Call the Policy Troubleshooter MCP tool (`troubleshoot_access` or `troubleshoot_iam_error_id`) to analyze the full Resource Manager hierarchy.
   * **Fallback:** If MCP tools are unavailable, use the fallback scripts or `gcloud` CLI:
     * For Error IDs: `ERROR_ID="YOUR_ERROR_ID" python3 scripts/troubleshooting_error_id.py`
     * For manual checks: Extract the project ID from `RESOURCE_URI` (if available) and pass `--billing-project` and `--format=json`:
       ```bash
       gcloud policy-troubleshoot iam //RESOURCE_URI \
           --principal-email="PRINCIPAL_EMAIL" \
           --permission="IAM_PERMISSION" \
           --billing-project="PROJECT_ID" \
           --format=json
       ```
2. **If troubleshooting returns `PERMISSION_DENIED`:**
   * The identity lacks the required viewer rights (for example, missing `resourcemanager.projects.getIamPolicy`). Recognize that troubleshooting failed due to caller permissions. Proceed directly to **Step 5 (Log Bug / Escalate to Admin)** to escalate the permission error to an administrator using the captured error context.
3. **If troubleshooting succeeds:**
   * **CASE A (`accessState: GRANTED`):** Access is correctly configured and granted (including via inherited organization or folder allow policies). Conclude that access is already granted and the IAM permission configuration is correct. **Terminate the flow immediately.** Do NOT search for roles, suggest role queries, or propose role bindings.
   * **CASE B (`accessState` or `bindingState` is `UNKNOWN` or `UNKNOWN_INFO`):** Access evaluation returned unknown/inconclusive information (typically due to missing permissions to expand group memberships, which requires `roles/browser`). Inform the user that access could not be fully confirmed because of missing permissions to expand group memberships (`roles/browser`).
   * **CASE C (`accessState: NOT_GRANTED`):** Access is absent or blocked. Check `denyPolicyExplanation` for explicitly blocked access, Principal Access Boundary policy evaluations, and `allowPolicyExplanation` for absent allow bindings.

---

## Step 3: Self-Service & Elevated Developer Checks

Evaluate the diagnosis against your available permissions:

### Branch A: Blocked by an IAM deny policy

* Modifying deny policies requires organization-level `roles/iam.denyAdmin`.
* Developers cannot self-remediate deny policies. Advise contacting the Security or Organization Administrator and proceed directly to **Step 5 (Log Bug / Escalate to Admin)**, including the blocking `POLICY_ID`.

### Branch B: Missing allow policy (No deny policy blocking)

Evaluate the following tiers in order:

* **Tier 1: Self-Service PAM Entitlement Grants (Pre-approved Entitlement Exists)**
  1. Check if an active PAM entitlement covers this permission (`gcloud pam entitlements list`).
  2. If found, request a time-bound PAM entitlement grant:
     ```bash
     gcloud pam grants create \
         --entitlement=ENTITLEMENT_ID \
         --location=global \
         --project=PROJECT_ID \
         --requested-duration="1h"
     ```
  3. Once the grant is active, proceed to **Step 4 (Retry Troubleshooter Check)**.

* **Tier 2: Elevated Developer (Local Project IAM Admin Rights)**
  1. If no PAM entitlement exists, check if you hold `roles/resourcemanager.projectIamAdmin` on the target project.
  2. If permitted, explain the findings to the user and **prompt the user for permission** before running any role queries or discovery scripts:
     > *"There is no allow policy granting permission 'IAM_PERMISSION', and no deny policy blocking access. Would you like me to find candidate roles granting this permission? (Yes/No)"*
  3. **Only after receiving user confirmation**, search for candidate roles using `gcloud iam roles list --filter="includedPermissions:IAM_PERMISSION"` or [`scripts/least_privileged_role.py`](../scripts/least_privileged_role.py).
  4. Evaluate the returned `ROLE_NAME` against the guardrails in [`references/guardrails.md`](guardrails.md). **Prompt the user for approval** before applying the project-level IAM binding:
     ```bash
     gcloud projects add-iam-policy-binding PROJECT_ID \
         --member="user:PRINCIPAL_EMAIL" \
         --role="ROLE_NAME"
     ```
  5. Proceed to **Step 4 (Retry Troubleshooter Check)**.

* **Tier 3: Unprivileged Developer (No PAM Entitlement & No Local Admin Rights)**
  * Proceed to **Step 5 (Log Bug / Escalate to Admin)**.

---

## Step 4: Retry Troubleshooter Check

Whenever access is granted (via PAM entitlement activation or local role binding), **immediately re-run the policy troubleshooter check** to confirm access before advising any task retries.

---

## Step 5: Log Bug / Escalate to Admin

When self-remediation is not possible, construct a structured escalation report or ticket:

### Ticket / Escalation Template

```text
Title: [Access Request] Missing permission <IAM_PERMISSION> on <RESOURCE_URI>
Principal: <PRINCIPAL_EMAIL>
Target Resource: <RESOURCE_URI>
Missing Permission: <IAM_PERMISSION>
Error ID: <ERROR_ID>

Diagnostic Details:
- Deny policy blocking: [Yes: <POLICY_ID> / None]
- Principal Access Boundary policy blocking: [Yes / No]
- Allow policy missing: [Yes / No]
- Suggested least-privileged role: <ROLE_NAME> (if diagnosed)

Resolution Options for Admin:
- If deny policy is blocking: Add exemption for <PRINCIPAL_EMAIL> on <POLICY_ID>.
- If allow policy is missing: Create a time-bound PAM entitlement or grant role binding <ROLE_NAME>.
- Reference: See https://docs.cloud.google.com/iam/docs/resolve-permission-errors for more comprehensive information.
```

**Agent Instruction:** (In multi-agent setups, output the summary above and escalate to a human administrator).
