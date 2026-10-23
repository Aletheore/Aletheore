---
name: iam-helper-for-policy-management
description: Streamlines the creation, modification, and management of IAM allow policies (v1) and deny policies (v2). Manages access control across Resource Manager resources (Organization, Folder, Project) and individual resources. Use when creating, updating, listing, or deleting IAM allow policies or deny policies. Don't use for access denial troubleshooting (use iam-helper-for-troubleshooting), temporary privileged access (use iam-helper-for-privileged-access-management), configuring VPC Service Controls, or managing network firewall rules.
metadata:
  version: 1.0.0
  category: Security
skill_id: platforms/iam-helper-for-policy-management
title: IAM Helper for Policy Management
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/iam-helper-for-policy-management
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/iam-helper-for-policy-management", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/iam-helper-for-policy-management", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 2, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# IAM Helper for Policy Management

Orchestrates the lifecycle and management of IAM allow and deny policies across
IAM v1 (allow policies) and IAM v2 (deny policies).

--------------------------------------------------------------------------------

## Core Concepts & Paradigms

IAM operates across two policy paradigms:

1.  **IAM v1 (Allow Policies)**: Grants roles to principals (users, service
    accounts, groups, domains) on specific resources. Supports Resource Manager
    resources (organizations, folders, projects) as well as individual resources
    across supported Google Cloud services.
2.  **IAM v2 (Deny Policies)**: Sets explicit organization-, folder-, or
    project-level guardrails that prevent specified principals from using
    designated permissions, regardless of any allow policies granted. Evaluated
    before allow policies.

--------------------------------------------------------------------------------

## Workflow & Decision Tree

When receiving a policy management request, determine whether the operation is
**Read-Only** or **Mutating**, and whether it targets **IAM v1 (Allow
Policies)** or **IAM v2 (Deny Policies)**:

### 1. Read-Only Operations (Autonomous Execution)

Read-only actions include the following:

-   **IAM v1 Allow Policies**: `get-iam-policy` on project/folder/organization,
    or `gcloud iam list-testable-permissions
    //cloudresourcemanager.googleapis.com/projects/PROJECT_ID`.
-   **IAM v2 Deny Policies**: `gcloud iam policies list` or `gcloud iam policies
    get` with `--attachment-point` and `--kind=denypolicies`.

For read-only actions, execute the command autonomously to inspect state, and
present the query results clearly to the user.

### 2. Mutating Operations (Plan & Confirm Protocol)

Mutating operations include the following:

-   **IAM v1 Allow Policies**: `add-iam-policy-binding`,
    `remove-iam-policy-binding`, or `set-iam-policy` across project, folder,
    organization, or resource levels (see
    [references/v1-allow-policies.md](references/v1-allow-policies.md)).
-   **IAM v2 Deny Policies**: `create`, `update`, or `delete` deny policies on
    attachment points
    (`cloudresourcemanager.googleapis.com/projects/PROJECT_ID`,
    `cloudresourcemanager.googleapis.com/folders/FOLDER_ID`, or
    `cloudresourcemanager.googleapis.com/organizations/ORG_ID`) using YAML/JSON
    policy files (see
    [references/v2-deny-policies.md](references/v2-deny-policies.md)).

For mutating operations, follow the **Plan & Confirm Protocol** below. **DO
NOT** execute mutating commands autonomously without prior user approval.

--------------------------------------------------------------------------------

## Execution & Safety Protocol

-   **Plan and Confirm (No Autonomous Mutation)**: Mutating allow and deny
    policy changes modify live security perimeters and access controls. You MUST
    NOT execute mutating `gcloud` commands directly via tool calls without
    explicit prior confirmation from the user. When asked to apply a mutating
    change, do the following:
    1.  **Formulate the Command**: Generate the exact, fully constructed
        `gcloud` command (including all parameters such as `--member`, `--role`,
        `--attachment-point`, `--kind=denypolicies`, and `--policy-file`).
    2.  **Warn of Impact & Propagation**: Issue a general warning that the
        change could impact access in a live environment and takes time to
        propagate across Google Cloud global infrastructure.
    3.  **Request User Confirmation**: Prompt the user for approval before
        applying the changes to the live environment.
-   **Post-Execution Verification**: After the user approves and the mutating
    policy change is executed, run the corresponding verification command (see
    [references/v1-allow-policies.md](references/v1-allow-policies.md) and
    [references/v2-deny-policies.md](references/v2-deny-policies.md) for exact
    verification steps) to verify that the active state matches expectations
    before reporting completion.
-   **Security Guardrail (Public & Blanket Access Refusal)**: Never grant
    `allUsers` or `allAuthenticatedUsers` basic roles (`roles/owner`,
    `roles/editor`, `roles/viewer`, `roles/admin`, `roles/writer`, and
    `roles/reader`) or broad permissions. Explicitly refuse blanket public
    access requests, explain the severe security risks of public project
    ownership/access, and propose scoped, least-privileged role bindings for
    specific authenticated identities instead.

--------------------------------------------------------------------------------

## Supporting Links

-   [IAM Overview](https://cloud.google.com/iam/docs/overview)
-   [IAM Deny Policies](https://cloud.google.com/iam/docs/deny-overview)
