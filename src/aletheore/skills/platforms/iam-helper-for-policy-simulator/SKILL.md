---
name: iam-helper-for-policy-simulator
description: Safely simulates and applies Identity and Access Management (IAM) v1 (Allow) policy changes on Google Cloud. Uses the Policy Simulator to replay historical access logs against proposed policies to prevent breaking active workloads before applying the changes. Use when simulating or applying IAM v1 allow policies across Projects, Folders, or Organizations. Don't use for analyzing IAM v2 deny policies, VPC Service Controls, or performing general policy troubleshooting.
metadata:
  version: 1.0.1
  category: Security
skill_id: platforms/iam-helper-for-policy-simulator
title: IAM Helper for Policy Simulator
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/iam-helper-for-policy-simulator
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/iam-helper-for-policy-simulator", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/iam-helper-for-policy-simulator", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": true}, "curated_on": "2026-10-18", "requires": ["scripts"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# IAM Policy Simulator (v1 Allow)

You are an advanced security assistant helping users safely modify IAM
policies. You must NEVER apply a modifying policy change without first
running a Policy Simulation to ensure existing workloads are not disrupted. You
must only use standard public gcloud commands.

## Core Concepts & Prerequisites

*   **IAM v1 (Allow Policies)**: Specifies who has access (a role) to a
    resource.
*   **Policy Simulator**: Replays the last 90 days of access logs against a
    proposed policy to verify if any historical access would be blocked by the
    change.
*   **Required Permissions**: The execution environment must have
    `roles/policysimulator.admin`, `roles/cloudasset.viewer`, and the
    appropriate IAM Admin roles for the target resource.
*   **Resource Scope**: Changes can target Projects, Folders, or Organizations.

## Execution Workflow: Plan, Simulate, Analyze, Apply

### Step 1: Retrieve Current Policy (Plan)

Fetch the baseline IAM v1 policy for the target resource (Project, Folder, or
Organization) and save it to the `/tmp/` directory:

**For Projects:**

```bash
gcloud projects get-iam-policy TARGET_PROJECT_ID --format=json > /tmp/current_policy.json
```

**For Folders:**

```bash
gcloud resource-manager folders get-iam-policy TARGET_FOLDER_ID --format=json > /tmp/current_policy.json
```

**For Organizations:**

```bash
gcloud organizations get-iam-policy TARGET_ORG_ID --format=json > /tmp/current_policy.json
```

**CRUCIAL SAFETY GATE:** Verify that the policy was successfully retrieved. If
the command fails or the resulting JSON is empty, you MUST terminate the
workflow immediately and inform the user. Do not proceed to prepare or simulate
an empty or partial policy.

### Step 2: Prepare Proposed Policy

Create a `/tmp/proposed_policy.json` file. Modify `/tmp/current_policy.json` by
adding or removing role bindings in the `bindings` array to match the requested
change.

**CRUCIAL NO-OP CHECK:** Compare the proposed policy to the current policy. If
no changes were actually made (e.g., you are trying to remove a role the user
doesn't hold, or add a role they already have), you MUST inform the user that no
changes are necessary and **terminate the workflow immediately**. Do not run a
simulation.

### Step 3: Run Policy Simulation

Run the simulator to replay the last 90 days of access logs against the proposed
policy change. Execute the exact command for your resource type:

**For Projects:**

```bash
gcloud iam simulator replay-recent-access //cloudresourcemanager.googleapis.com/projects/TARGET_PROJECT_ID /tmp/proposed_policy.json --project=TARGET_PROJECT_ID --format=json > /tmp/simulation_results.json
```

**For Folders:**

```bash
gcloud iam simulator replay-recent-access //cloudresourcemanager.googleapis.com/folders/TARGET_FOLDER_ID /tmp/proposed_policy.json --format=json > /tmp/simulation_results.json
```

**For Organizations:**

```bash
gcloud iam simulator replay-recent-access //cloudresourcemanager.googleapis.com/organizations/TARGET_ORG_ID /tmp/proposed_policy.json --format=json > /tmp/simulation_results.json
```

*(Note: If the Policy Simulator API is not enabled, it will prompt you to enable
it. Select Yes. Do not use placeholders verbatim; replace TARGET_PROJECT_ID,
TARGET_FOLDER_ID, or TARGET_ORG_ID with the actual resource ID).*

**CRUCIAL SAFETY GATE:** Verify the command exited successfully. If the
simulator command crashes, times out, or returns a non-zero exit code, you MUST
NOT treat the failure as a "safe" result. Terminate the workflow immediately and
report the simulator failure to the user.

### Step 4: Analyze Simulation Results

Analyze the contents of `/tmp/simulation_results.json` using the provided helper
script. Do not write custom scripts on the fly. You MUST execute the following
command:

```bash
python3 scripts/analyze_simulation.py
```

*   **SAFE (No Breakage):** If the script outputs `REVOKED_COUNT=0`, the change
    is safe.
*   **UNSAFE (Breakage):** If the script outputs `REVOKED_COUNT` > 0 (meaning
    the logs contain `ACCESS_REVOKED` or `ACCESS_MAYBE_REVOKED`):
    *   Identify the `principal`, `permission`, and `fullResourceName` from the
        printed JSON.
    *   **Do NOT apply the policy.**
    *   The change will break an active workload. Inform the user of the
        specific disrupted accesses.

### Step 5: Apply Policy (Only if Safe)

If and only if the simulation in Step 4 was SAFE (No Breakage), prompt the user:
*"The simulation showed no disrupted access. Do you want to apply this policy
change? (Yes/No)"*.

*   **If Yes:** Apply the policy using the correct command for the resource
    type:

**For Projects:**

```bash
gcloud projects set-iam-policy TARGET_PROJECT_ID /tmp/proposed_policy.json
```

**For Folders:**

```bash
gcloud resource-manager folders set-iam-policy TARGET_FOLDER_ID /tmp/proposed_policy.json
```

**For Organizations:**

```bash
gcloud organizations set-iam-policy TARGET_ORG_ID /tmp/proposed_policy.json
```

*   **If No:** Terminate the workflow.

### Step 6: Cleanup (Always Run)

After applying the policy, declining the prompt, or terminating early due to a
NO-OP/failure, always delete the temporary files to prevent cross-contamination
in future runs:

```bash
rm -f /tmp/current_policy.json /tmp/proposed_policy.json /tmp/simulation_results.json
```
