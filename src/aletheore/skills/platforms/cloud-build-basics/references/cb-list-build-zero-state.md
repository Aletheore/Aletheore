---
name: cb-list-build-zero-state
description: Manages the Cloud Build zero-state experience, including running sample builds, creating triggers, and navigating regional or jurisdictional limitations like DRZ.
---

## Table of Contents
- [Preconditions](#preconditions) (Lines 22-37)
- [Inputs](#inputs) (Lines 39-55)
- [Execution Steps](#execution-steps) (Lines 57-163)
  - [Step 1: Authentication and Project Selection](#step-1-authentication-and-project-selection) (Lines 59-67)
  - [Step 2: Verify Project Zero State](#step-2-verify-project-zero-state) (Lines 69-82)
  - [Step 3: Option A: Run Sample Build (Manual)](#step-3-actionable-onboarding---option-a-run-sample-build-manual) (Lines 84-96)
  - [Step 4: Option B: Create Trigger (Continuous Deployment)](#step-4-actionable-onboarding---option-b-create-trigger-continuous-deployment) (Lines 98-112)
  - [Step 5: Option C: Inline Configuration](#step-5-actionable-onboarding---option-c-inline-configuration) (Lines 114-126)
  - [Step 6: Advanced Settings and Constraints](#step-6-advanced-settings-and-constraints) (Lines 128-151)
  - [Step 7: Monitoring Build Execution](#step-7-monitoring-build-execution) (Lines 153-163)
- [Outputs](#outputs) (Lines 165-174)
- [Related Skills](#related-skills) (Lines 176-181)

## Preconditions

-   **Authentication**: Run the following commands to authenticate:
    -   `gcloud auth login`
    -   `gcloud auth application-default login`
-   **Project ID**: Prompt the user for the `projectId` if it is not already
    known.
-   **Enabled APIs**: Ensure the Cloud Build API is enabled:
    -   `cloudbuild.googleapis.com`
-   **Permissions**: Ensure you have the following IAM permissions:
    -   `cloudbuild.builds.list`
    -   `cloudbuild.builds.get`
    -   `cloudbuild.builds.create`
    -   `roles/cloudbuild.builds.editor` (Recommended)

## Inputs

Name             | Type   | Description                                                                                                             | Required/Optional | Default
:--------------- | :----- | :---------------------------------------------------------------------------------------------------------------------- | :---------------- | :------
`projectId`      | String | The ID of the GCP project.                                                                                              | Required          | -
`region`         | String | Regional location for builds/triggers (e.g., `us-central1`). **Note: Region cannot be changed after trigger creation.** | Optional          | `global`
`triggerName`    | String | Name for the new build trigger.                                                                                         | Optional          | -
`repoOwner`      | String | Owner of the GitHub/Bitbucket repository (e.g., `GoogleCloudBuild`).                                                    | Optional          | -
`repoName`       | String | Name of the repository (e.g., `cloud-console-sample-build`).                                                            | Optional          | -
`branchPattern`  | String | Regex for branches to trigger on (e.g., `^main$`).                                                                      | Optional          | `^main$`
`substitutions`  | Map    | Key-value pairs for build substitutions (e.g., `_KEY=VALUE`).                                                           | Optional          | -
`serviceAccount` | String | Service account email to execute builds.                                                                                | Optional          | Default GCB SA

> [!IMPORTANT] **Sample builds** are not supported in Jurisdictional (DRZ) or
> BYOID environments. **1st-gen repository connections** are disabled in DRZ
> regions (US, EU, Saudi Arabia).

## Execution steps

### Step 1: Authentication and Project Selection

Authenticate and set the project context for all subsequent commands.

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project <PROJECT_ID>
```

### Step 2: Verify Project Zero State

Confirm that no builds or triggers currently exist in the target region.

```bash
# List recent builds to verify empty state
gcloud builds list --region=<REGION> --limit=5

# List existing triggers
gcloud builds triggers list --region=<REGION>
```

Validation: If the project is in a zero state, these commands should return no
results or an empty list.

> [!IMPORTANT]
> **Region Immutability**: Region settings for triggers and builds are immutable after creation and must be chosen deliberately during zero-state initialization.

### Step 3: Actionable Onboarding - Option A: Run Sample Build (Manual)

Use this step to try Cloud Build with sample code. This mimics the "Run sample
build" CTA in the console.

```bash
# Clone the sample repository
git clone https://github.com/GoogleCloudBuild/cloud-console-sample-build && \
cd cloud-console-sample-build

# Submit the build to Cloud Build
gcloud builds submit --config cloudbuild.yaml --region=<REGION>
```

### Step 4: Actionable Onboarding - Option B: Create Trigger (Continuous Deployment)

Create a trigger to automatically start builds on code changes.

```bash
gcloud builds triggers create github \
    --name=<TRIGGER_NAME> \
    --repo-name=<REPO_NAME> \
    --repo-owner=<REPO_OWNER> \
    --branch-pattern=<BRANCH_PATTERN> \
    --build-config="cloudbuild.yaml" \
    --region=<REGION> \
    --require-approval
```

### Step 5: Actionable Onboarding - Option C: Inline Configuration

Create a trigger using an inline build configuration instead of a file in the
repository.

```bash
gcloud builds triggers create github \
    --name=<TRIGGER_NAME> \
    --repo-name=<REPO_NAME> \
    --repo-owner=<REPO_OWNER> \
    --branch-pattern=<BRANCH_PATTERN> \
    --inline-config="local-config.yaml" \
    --region=<REGION>
```

### Step 6: Advanced Settings and Constraints

Apply specific configurations for security, networking, or identity.

**Identity and Substitutions**

```bash
# Update trigger with a specific service account and substitutions
gcloud builds triggers update github <TRIGGER_NAME> \
    --service-account="projects/<PROJECT_ID>/serviceAccounts/<SA_EMAIL>" \
    --update-substitutions="_VERSION=v1,_ENV=prod" \
    --region=<REGION>
```

**Handling Regional Outages**

If a region is unreachable (e.g., `us-east4`), you will see a warning. You can
still list builds from other locations.

```bash
# Check status of unreachable locations
gcloud builds list --region=global
```

### Step 7: Monitoring Build Execution

After triggering or submitting a build, monitor its progress.

```bash
# List builds with the sample tag
gcloud builds list --filter='tags="gcp-cloud-build-sample-build"' --region=<REGION>

# View logs for a specific build
gcloud builds log <BUILD_ID> --region=<REGION>
```

## Outputs

Name        | Type   | Description
:---------- | :----- | :----------------------------------------------------
`buildId`   | String | The unique identifier of the executed build.
`triggerId` | String | The unique identifier of the created trigger.
`status`    | String | Final status of the build (e.g., SUCCESS, FAILURE).
`imageUri`  | String | The URI of the built container image (if applicable).

## Related skills

-   `cb-connect-repo`
-   [`cb-run-trigger`](./cb-run-trigger.md)
-   `artifact-registry-manage-images`