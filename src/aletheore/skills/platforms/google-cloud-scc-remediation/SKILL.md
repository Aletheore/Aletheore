---
name: google-cloud-scc-remediation
metadata:
  category: Security
  version: 1.0.0
description: Remediates Google Cloud Security Command Center findings, including IAM permission fixes, cloud resource misconfigurations, vulnerabilities, and Toxic Combinations. Use when asked to fix, remediate, or mitigate a Security Command Center finding or address attack paths. Don't use for general IAM policy querying without a Security Command Center finding. For runtime threat detections, this skill provides containment and investigation guidance rather than automated configuration fixes.
skill_id: platforms/google-cloud-scc-remediation
title: Google Cloud Scc Remediation
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-scc-remediation
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/google-cloud-scc-remediation", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-scc-remediation", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 3, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Google Cloud Security Command Center Remediation

## Overview

A unified remediation skill for Google Cloud Security Command Center findings.
It handles both single-domain findings and multi-domain Toxic Combinations.

## 1. Safety & Consent Gate

Security Command Center remediations modify infrastructure, access policies, or
workload deployments.

*   **Mandatory Consent Gate**: NEVER execute mutating `gcloud`, `terraform`, or
    API commands without first presenting a structured remediation plan and the
    exact command/config diff to the user. STOP after presenting the plan and
    ALWAYS ask: "Do you approve executing this remediation plan?" before
    execution. Automatic mutation of security policies or resource
    configurations can cause unintended outages, lockouts, or compliance
    violations.
*   **Confirm the Target Project**: Before executing, confirm that the active
    `gcloud` account and project match the project of the finding's resource.
    Every mutating command MUST specify the target explicitly, with `--project`
    or a full resource path, rather than relying on the default `gcloud`
    project.
*   **Verification & Rollback Required**: Every proposed remediation plan MUST
    explicitly include, for each mutating step:
    *   the mutating remediation command(s),
    *   the verification check command(s) (`gcloud ... describe` or `list`), and
    *   the rollback command(s) that undo the change, or an explicit statement
        that the step cannot be undone (for example, service account key
        deletion).

    Use the commands from the loaded playbook where they exist. If the playbook
    lacks a verification or rollback command for a step, derive the command and
    label it as not sourced from a playbook.
*   **Dependency & Impact Checks**: Every remediation plan MUST state what could
    break as a result of each change and ask the user to confirm nothing depends
    on it. For example:
    *   IAM role changes or revocations: you MUST explicitly advise the user to
        verify cross-project dependencies and CI/CD pipeline workflows that rely
        on the existing permissions before revoking a project-level role
        binding.
    *   Firewall rule changes: other traffic the same rule allows (such as web
        traffic on ports 80 or 443).
    *   External IP removal: inbound access and outbound internet access for the
        VM.
    *   Public Access Prevention on a bucket: sites or users that legitimately
        read it publicly.
*   **CRITICAL WORKFLOW RULE**: Do NOT execute any mutating commands in the same
    turn that you present the remediation plan. You MUST end your turn
    immediately after asking for approval and wait for the user's next message.
    *   If there is no interactive user (for example, a scheduled or pipeline
        run), stop at the plan and never execute.
    *   If the user approves only part of the plan, or the plan changes after
        approval, present the updated plan and ask for approval again before
        executing.
*   **Narrowly Scoped Changes**: Always propose the narrowest possible fix that
    resolves the finding. When more than one fix would work, prefer reversible
    changes over irreversible ones (for example, disable a key before deleting
    it), resource-level changes over project, folder, or organization-level
    changes, and changes that affect only the finding's resource over changes
    that affect other resources. Do not bundle fixes for unrelated issues
    noticed along the way; mention them to the user separately.

## 2. Intent Router & Progressive Disclosure

Do not load all reference playbooks into memory at once. Analyze the Security
Command Center finding's `category`, `findingClass`, or attack path, then read
ONLY the relevant reference document(s).

Match on the finding's `category` **first**, and fall back to `findingClass`
only when no category matches. Routing on `findingClass` alone misroutes IAM
findings: Security Health Analytics IAM findings are `MISCONFIGURATION` class,
and IAM recommender findings are `Vulnerability` class, so neither would ever
reach the IAM playbook.

| Match On                | Values                                    | Target Reference Playbook                                                    |
| :---------------------- | :---------------------------------------- | :--------------------------------------------------------------------------- |
| `category`              | `PRIMITIVE_ROLES_USED`,                   | [`references/remediation_iam.md`](references/remediation_iam.md)             |
: (Security Health        : `OVER_PRIVILEGED_SERVICE_ACCOUNT_USER`,   :                                                                              :
: Analytics)              : `ADMIN_SERVICE_ACCOUNT`,                  :                                                                              :
:                         : `SERVICE_ACCOUNT_ROLE_SEPARATION`,        :                                                                              :
:                         : `KMS_ROLE_SEPARATION`,                    :                                                                              :
:                         : `USER_MANAGED_SERVICE_ACCOUNT_KEY`,       :                                                                              :
:                         : `SERVICE_ACCOUNT_KEY_NOT_ROTATED`         :                                                                              :
| `category`              | `IAM_ROLE_HAS_EXCESSIVE_PERMISSIONS`,     | [`references/remediation_iam.md`](references/remediation_iam.md)             |
: (IAM recommender)       : `UNUSED_IAM_ROLE`,                        :                                                                              :
:                         : `SERVICE_AGENT_GRANTED_BASIC_ROLE`,       :                                                                              :
:                         : `SERVICE_AGENT_ROLE_REPLACED_WITH_BASIC_ROLE` :                                                                          :
| `category`              | `PUBLIC_BUCKET_ACL`, `PUBLIC_IP_ADDRESS`, | [`references/remediation_misconfig.md`](references/remediation_misconfig.md) |
: (Security Health        : `OPEN_FIREWALL`, `OPEN_SSH_PORT`,         :                                                                              :
: Analytics)              : `OPEN_RDP_PORT`, `KMS_KEY_NOT_ROTATED`   :                                                                              :
| `category`              | `OS_VULNERABILITY`, `SOFTWARE_VULNERABILITY`, | [`references/remediation_vuln.md`](references/remediation_vuln.md)           |
:                         : `GKE_RUNTIME_OS_VULNERABILITY` (CVEs, OS  :                                                                              :
:                         : patch, container base image upgrade,      :                                                                              :
:                         : vulnerable package, GKE node pool upgrade). :                                                                            :
:                         : Note: Web Security Scanner (WSS) findings :                                                                              :
:                         : fall through to *no match* below.         :                                                                              :
| `findingClass`          | `TOXIC_COMBINATION` — attack path         | **Sequential Load**: Identify each exposed facet in the attack path and load |
:                         : exposure, multi-domain attack vector      : the matching reference playbooks sequentially.                               :
| *no match*              | Any other category or finding class       | See **Findings Without Dedicated Playbooks** below. Do not force a finding   |
:                         :                                           : into a playbook that does not cover it.                                      :

### Findings Without Dedicated Playbooks & Runtime Threat Detections

When presented with Security Command Center findings that do not have automated
configuration remediation playbooks—such as runtime threat detections (e.g.,
Cloud Run Threat Detection, Agent Platform Threat Detection, Event Threat
Detection, Container Threat Detection):

1.  **Do NOT execute automated mutation commands**: Runtime threat detections
    represent active alerts or behavioral anomalies rather than static resource
    misconfigurations.
2.  **Provide Documentation & Containment Guidance**:
    *   Refer the user to the relevant Security Command Center documentation
        (e.g.,
        [Cloud Run Threat Detection](https://docs.cloud.google.com/security-command-center/docs/cloud-run-threat-detection-overview.md.txt#detectors),
        [Agent Platform Threat Detection](https://docs.cloud.google.com/security-command-center/docs/agent-platform-threat-detection-overview.md.txt#detectors)).
    *   Recommend manual investigation steps: analyzing audit logs in Cloud
        Logging, identifying compromised service accounts or API keys, and
        isolating affected workloads.
    *   Advise the user on containment options (e.g., revoking active
        credentials, blocking malicious IPs at Cloud Armor/firewall).

## 3. Standard Remediation Workflow

1.  **Inspect Finding Details & Intentionality**:
    *   If the finding's details are not already in the conversation, retrieve
        them using the `google-cloud-scc-query` skill. Verify the finding name,
        affected resource (`resourceName`), category, and attack exposure score.
    *   Before planning a fix, ask the user whether the flagged configuration is
        intentional (for example, a bucket serving a public website, or a VM that
        must accept public traffic). If it is, do not propose a remediation.
        Suggest muting the finding with a documented justification instead;
        muting changes the finding's state, so it also requires the user's
        approval.
2.  **Route & Load Reference**: Read the matching reference playbook(s) from the
    routing table above.
3.  **Formulate Least-Privilege Remediation**: Before drafting, check whether the
    resource is managed by infrastructure as code (ask the user, and check for
    signals such as the `goog-terraform-provisioned` label). If it is, draft the
    change to the Terraform configuration rather than a `gcloud` command.
    Otherwise, draft the exact CLI (`gcloud`) or IAM binding change needed to
    close the exposure without disrupting business workloads.
4.  **Present Plan & Seek Consent**: Show the user the plan required by the
    Safety & Consent Gate: for each step, the remediation command, the
    verification command, the rollback command, and what could break. STOP
    execution here and ALWAYS ask the explicit question: "Do you approve
    executing this remediation plan?"
5.  **Execute on Approval**: ONLY after receiving explicit user confirmation,
    execute the remediation commands and run the verification commands to confirm
    the resource is fixed. If a command fails, stop and report the error; do not
    try a different command without presenting it and getting approval again.
    Tell the user that the finding's state in Security Command Center updates on
    the detector's next scan, which may take some time, and do not manually mark
    the finding as resolved.
