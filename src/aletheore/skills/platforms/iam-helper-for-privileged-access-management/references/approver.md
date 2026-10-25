# Mode 3: Approver Workflow (Grants)

Follow these steps when an Approver needs to review, approve, or reject pending
grant requests raised by requesters.

*(Rule: Select the scope flag matching where the entitlement/grant is located:
pass `--project=PROJECT_ID`, `--folder=FOLDER_ID`, or
`--organization=ORGANIZATION_ID`).*

## Table of Contents

*   [Prerequisites & Permissions](#prerequisites)
*   [Step 1: Search Pending Grants](#step-1)
*   [Step 2: Approve Pending Grant](#step-2)
*   [Step 3: Deny Pending Grant](#step-3)

### Prerequisites & Permissions {#prerequisites}

*   **Approver Authorization:** To become an approver, the user simply must be
    added to the `approvers` list in the entitlement's `approvalWorkflow`
    configuration. No explicit IAM roles are required.
*   **Discover Grants to Approve:** Approvers can directly search for grants
    they are authorized to approve across a project, folder, or organization
    using `gcloud pam grants search --caller-relationship=can-approve` without
    needing the `roles/privilegedaccessmanager.viewer` role or knowing specific
    entitlement IDs beforehand.

### Step 1: Search Pending Grants {#step-1}

Approvers can search directly for pending grants awaiting their decision across
a project, folder, or organization:

```bash
gcloud pam grants search \
    --caller-relationship=can-approve \
    --location=global \
    --project=PROJECT_ID
```

*(Or pass `--folder=FOLDER_ID`, `--organization=ORGANIZATION_ID` matching the
target resource hierarchy).*

You can also list all grants for a specific entitlement if the entitlement ID is
already known:

```bash
gcloud pam grants list \
    --entitlement=ENTITLEMENT_ID \
    --location=global \
    --project=PROJECT_ID
```

*(Note: Read-only search/list operations run autonomously without user prompt).*

### Step 2: Approve Pending Grant {#step-2}

When the user asks to approve a pending PAM grant:

1.  Verify grant existence and state using `gcloud pam grants describe` or
    `search`.
2.  Prompt the user to provide a justification string for the approval.
3.  Prompt the user for explicit approval under Plan-Validate-Execute rules:
    *"You are about to approve PAM Grant `GRANT_ID` under entitlement
    `ENTITLEMENT_ID` with reason: 'USER_PROVIDED_REASON'. Do you approve? (Yes/No)"*
4.  Execute approval:

```bash
gcloud pam grants approve GRANT_ID \
    --entitlement=ENTITLEMENT_ID \
    --location=global \
    --project=PROJECT_ID \
    --reason="USER_PROVIDED_REASON"
```

### Step 3: Deny Pending Grant {#step-3}

When the user asks to deny or reject a pending PAM grant:

1.  Verify grant existence and state using `gcloud pam grants describe` or
    `search`.
2.  Prompt the user to provide a justification string for the denial.
3.  Prompt the user for explicit approval under Plan-Validate-Execute rules:
    *"You are about to deny PAM Grant `GRANT_ID` under entitlement
    `ENTITLEMENT_ID` with reason: 'USER_PROVIDED_REASON'. Do you approve? (Yes/No)"*
4.  Execute denial:

```bash
gcloud pam grants deny GRANT_ID \
    --entitlement=ENTITLEMENT_ID \
    --location=global \
    --project=PROJECT_ID \
    --reason="USER_PROVIDED_REASON"
```
