# Access Provisioning Guardrails & Approval Policy

This reference defines the safety boundaries and Human-in-the-Loop (HITL) approval policy for access resolution and role provisioning in the **Resolver Flow**.

> [!TIP]
> **Customization Notice for Organizations:**
> When adopting or cloning this skill for your organization, **customize this file** (`references/guardrails.md`) to define your specific security policies, trusted role tiers, sensitive role denylists, and approval workflows.

---

## 1. Role Approval Tiers

Before applying any IAM policy binding or creating a PAM entitlement, evaluate the target `ROLE_NAME` against the following approval tiers:

| Tier | Classification | Matching Criteria / Examples | Execution Mode |
| :--- | :--- | :--- | :--- |
| **Read-Only (Low Risk)** | Viewer, Reader, Read-only access | Role names ending in `Viewer` or `Reader`, or roles containing only read-only permissions (`*.get`, `*.list`). Examples: `roles/viewer`, `roles/bigquery.dataViewer`, `roles/storage.objectViewer`, `roles/browser`. | **Human-in-the-Loop (HITL)** (Requires explicit confirmation prompt or ticket approval before execution). |
| **Mutating / Write (Medium Risk)** | Creator, Writer, Editor access | Roles containing create, update, or write permissions (`*.create`, `*.update`, `*.write`, `*.publish`). Examples: `roles/bigquery.dataEditor`, `roles/storage.objectUser`, `roles/pubsub.publisher`. | **Human-in-the-Loop (HITL)** (Requires explicit confirmation prompt or ticket approval before execution). |
| **Destructive / Admin (High Risk)** | Admin, Deletion, Security, IAM | Roles with delete permissions (`*.delete`), administrative authority (`roles/*.admin`, `roles/owner`), or IAM/security controls (`roles/resourcemanager.*Admin`, `roles/iam.*`, `*.setIamPolicy`). | **Human-in-the-Loop (HITL)** (Requires explicit confirmation with high-risk warning). |

---

## 2. Human-in-the-Loop (HITL) Execution Protocol

When a candidate role is selected:

1. **Do NOT apply the binding or entitlement automatically.**
2. **Present the Planned Change to the Human Operator:**
   ```
   [APPROVAL REQUIRED]: Role 'ROLE_NAME' grants permissions for 'PRINCIPAL_EMAIL' on 'RESOURCE_URI'.
   - Role scope: Read-Only / Write / Admin
   - Resource: RESOURCE_URI
   - Target Principal: PRINCIPAL_EMAIL
   Do you approve granting this access? (Yes/No)
   ```
3. **If Approved ('Yes'):** Proceed with the role binding or PAM entitlement creation.
4. **If Rejected ('No'):** Terminate the provisioning flow and record the rejection in the resolution summary.

---

## 3. General Safety Rules

* **Standard Roles Preferred:** Prioritize standard service-level roles (for example, `roles/compute.admin`) over hyper-granular least-privileged roles, while avoiding overly broad basic roles like `roles/editor` or `roles/owner`.
* **Deny Overrides Allow:** An IAM v2 deny policy unconditionally overrides any allow policy or PAM grant. If a deny policy blocks access, granting a role in an allow policy will not work until a deny exemption is applied under `exceptionPrincipals`.
* **No Task Usurpation (Resolver Guardrail):** When operating in Resolver mode, only resolve the permission barrier. In human-in-the-loop flows, advise the user to retry the task. In autonomous agent flows, simply retry the task.
* **Anti-Looping:** If an API call or binding command fails due to invalid parameters or permission errors, halt immediately and report the error instead of repeatedly retrying in a loop.
