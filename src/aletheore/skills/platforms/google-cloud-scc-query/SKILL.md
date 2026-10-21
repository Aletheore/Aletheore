---
name: google-cloud-scc-query
metadata:
  version: 1.0.1
  category: Security
description: Queries and retrieves active security findings, external exposures, toxic combinations, vulnerabilities, threats, and sensitive data risks from Security Command Center on Google Cloud. Use when retrieving details for a security finding by its name, validating finding scope (e.g., verifying findingClass is TOXIC_COMBINATION, VULNERABILITY, EXTERNAL_EXPOSURE, or THREAT), or fetching finding details for triage. Don't use to draft remediations, apply patches, or execute configurations.
skill_id: platforms/google-cloud-scc-query
title: Google Cloud Scc Query
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-scc-query
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/google-cloud-scc-query", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-scc-query", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Security Command Center Query Skill on Google Cloud

Provides guidelines and read-only `gcloud` CLI command patterns for querying and
retrieving security findings, external exposures, toxic combinations,
vulnerabilities, threats, and sensitive data risks from Security Command Center
on Google Cloud.

> [!IMPORTANT] There is NO `gcloud scc findings describe` command (`Invalid
> choice: 'describe'`). To retrieve details for a specific finding by its name,
> always use `gcloud scc findings list` with a filter on `name`.

--------------------------------------------------------------------------------

## Core Execution Rules

1.  **Read-Only & Zero-Speculation (Parent Scope Required)**: Keep all
    executions strictly read-only. Every `gcloud scc findings list` or `group`
    command strictly requires an explicit `{parent}` scope
    (`organizations/{id}`, `projects/{id}`, or `folders/{id}`). If the parent
    scope is missing from the prompt and cannot be inferred from a full finding
    name, **DO NOT run any `gcloud` commands** (do not execute queries without
    parent, and never inspect `gcloud config`). Halt immediately before
    executing commands and ask the user for the parent resource scope.
2.  **Bounded Execution & No Runaway Loops**:
    -   Limit tool calls to what is strictly necessary to complete the query
        (typically 1 call for direct queries, or 2 calls for List → Deep Dive
        workflows).
    -   If a command fails due to permission/auth errors, or if a specific
        finding query returns `[]`, halt immediately. Do not attempt blind
        brute-force retries with different flags, and never search the local
        workspace for credentials.
3.  **Immediate Halt on Errors**: If any command fails with `PERMISSION_DENIED`,
    `IAM_PERMISSION_DENIED`, credential expiration, or network timeouts, halt
    immediately and report the verbatim error message. Do not search the
    workspace for credentials or run diagnostic loops.
4.  **Ambiguous or Multiple Findings**: If multiple finding names are provided
    when a single finding report is requested, or if listing returns multiple
    findings, do not investigate all of them or unilaterally pick one. Halt
    immediately without running queries and ask the user to clarify which
    specific finding name they want details for. If zero findings are returned
    from a query, report that no active findings exist and halt immediately.
5.  **Do Not Query Attack Path Resources**: Analyze only the data present in the
    Security Command Center finding JSON payload. Do not run commands to
    describe, verify, or query underlying Google Cloud resources (such as VMs,
    Cloud Storage buckets, service accounts, or IAM policies).
6.  **Parent Scope Resolution**:
    -   For listing and grouping, format the parent resource path as
        `organizations/{org_id}`, `projects/{project_id}`, or
        `folders/{folder_id}`.
    -   For deep dive queries on a specific finding name, extract the `{parent}`
        resource prefix before `/sources/...`:
        -   `organizations/{org_id}/sources/...` → `{parent}` is
            `organizations/{org_id}`
        -   `folders/{folder_id}/sources/...` → `{parent}` is
            `folders/{folder_id}`
        -   `projects/{project_id}/sources/...` → `{parent}` is
            `projects/{project_id}` Extract the parent prefix regardless of
            whether the finding resource name is global (4-segment) or
            location-qualified (5-segment with `/locations/{location}/`).
            Execute the deep dive query using the extracted `{parent}`. Do not
            reject or halt on project- or folder-level findings.

--------------------------------------------------------------------------------

## Data Residency & Regional Endpoints

When Data Residency (DRZ) is enabled, findings are stored and accessible only
within their designated regional location (`us`, `eu`, or `me-central2`).
Queries across different locations do not return findings from other regions.

### 1. Location Parameterization

All `gcloud scc findings` commands require specifying the target location via
`--location={location}`:

-   **Default**: `global` (used when data residency is not enabled or for global
    findings).
-   **Supported Regional Locations**:
    -   `us` (United States multi-region)
    -   `eu` (European Union multi-region)
    -   `me-central2` (Kingdom of Saudi Arabia regional location)

### 2. API Endpoint Overrides

When data residency (DRZ) is enabled for an organization in a regional location
(`us`, `eu`, or `me-central2`), configure the regional API endpoint override
before executing finding queries:

```bash
gcloud config set api_endpoint_overrides/securitycenter https://securitycenter.{LOCATION}.rep.googleapis.com/
```

Example for the European Union (`eu`) region:

```bash
gcloud config set api_endpoint_overrides/securitycenter https://securitycenter.eu.rep.googleapis.com/
```

To reset the endpoint back to default global routing:

```bash
gcloud config unset api_endpoint_overrides/securitycenter
```

### 3. Location-Qualified Finding Resource Names

Regional finding resource names include the `/locations/{location}/` path
segment:

-   Organization-level:
    `organizations/{org_id}/sources/{source_id}/locations/{location}/findings/{finding_id}`
-   Folder-level:
    `folders/{folder_id}/sources/{source_id}/locations/{location}/findings/{finding_id}`
-   Project-level:
    `projects/{project_id}/sources/{source_id}/locations/{location}/findings/{finding_id}`

When performing a Deep Dive on a location-qualified finding name:

1.  Extract the `{parent}` scope (the prefix before `/sources/...`, e.g.,
    `organizations/{org_id}`).
2.  Extract the `{location}` from `/locations/{location}/` (e.g., `eu`, `us`,
    `me-central2`). If not present in the finding name, default to `global` (or
    the user-specified location).
3.  Execute the query with `--location={location}` and
    `--filter="name=\"{finding_name}\""`.

--------------------------------------------------------------------------------

## Intent-Based Query Strategies

### 1. Deep Dive (Specific Finding Details)

**Intent**: User provides a specific finding name or explicitly asks to retrieve
all details for one finding. \
**Action**: Execute `gcloud scc findings list` with a strict filter on `name`
and NO `--field-mask` to retrieve the complete JSON payload. Specify
`--location={location}` (default `global` unless a regional location is
indicated or present in the finding name).

```bash
gcloud scc findings list {parent} \
  --location={location} \
  --filter="name=\"{finding_name}\"" \
  --format="json" --limit=1
```

### 2. Listing (Filtered Projection)

**Intent**: User wants to list active findings matching criteria without pulling
full nested payloads. \
**Action**: Use `--field-mask` projection to restrict output size. Specify
`--location={location}` (default `global` unless querying a specific region).

```bash
gcloud scc findings list {parent} \
  --location={location} \
  --filter="{filter_expression}" \
  --field-mask="finding.name,finding.parentDisplayName,finding.findingClass,finding.category,finding.state,finding.eventTime,finding.severity,finding.resourceName" \
  --format="json" --order-by="severity,event_time desc" --limit=100
```

| Intent / Target Finding Class | `--filter` Expression                      |
| :---------------------------- | :----------------------------------------- |
| **All Active Findings**       | `state="ACTIVE"`                           |
| **Vulnerabilities**           | `state="ACTIVE" AND                        |
:                               : findingClass="VULNERABILITY"`              :
| **Misconfigurations**         | `state="ACTIVE" AND                        |
:                               : findingClass="MISCONFIGURATION"`           :
| **Toxic Combinations**        | `state="ACTIVE" AND                        |
:                               : findingClass="TOXIC_COMBINATION"`          :
| **External Exposures**        | `state="ACTIVE" AND                        |
:                               : findingClass="EXTERNAL_EXPOSURE"`          :
| **Threats**                   | `state="ACTIVE" AND findingClass="THREAT"` |
| **Observations**              | `state="ACTIVE" AND                        |
:                               : findingClass="OBSERVATION"`                :
| **Sensitive Data Risks**      | `state="ACTIVE" AND                        |
:                               : findingClass="SENSITIVE_DATA_RISK"`        :
| **Chokepoints**               | `state="ACTIVE" AND                        |
:                               : findingClass="CHOKEPOINT"`                 :
| **Posture Violations**        | `state="ACTIVE" AND                        |
:                               : findingClass="POSTURE_VIOLATION"`          :
| **Secrets**                   | `state="ACTIVE" AND findingClass="SECRET"` |
| **SCC Errors**                | `state="ACTIVE" AND                        |
:                               : findingClass="SCC_ERROR"`                  :
| **Specific Category**         | `state="ACTIVE" AND category="{category}"` |

### 3. Discovery & Aggregation (Grouping)

**Intent**: User wants high-level counts or landscape overview (e.g., "What are
the most common findings?", "Show me a summary by category"). \
**Action**: Use `gcloud scc findings group`. Specify `--location={location}`
(default `global` unless querying a specific region). Allowed fields for
`--group-by` are strictly: `resource_name`, `category`, `state`, `parent`.

```bash
gcloud scc findings group {parent} \
  --location={location} \
  --group-by="{group_by_field}" \
  --filter="state=\"ACTIVE\"" \
  --format="json"
```

--------------------------------------------------------------------------------

## Payload Analysis & Handoff

Once the finding JSON payload is retrieved:

*   **For `TOXIC_COMBINATION` Findings**:
    1.  Verify the `attackExposure` field is present and has a `score > 0`.
    2.  Inspect the attack path nodes, edges, or referenced
        `attackExposureResult` to identify exposed resources and attack
        trajectories.
*   **For `VULNERABILITY` Findings**:
    1.  Extract CVSS scores, exploit signals (`exploitationActivity`,
        `observedInTheWild`, `zeroDay`), upstream fix status
        (`upstreamFixAvailable`), and affected package details from the
        `vulnerability` object to evaluate risk:
        -   `vulnerability.cve.id`
        -   `vulnerability.cve.cvssv3.baseScore`
        -   `vulnerability.cve.cvssv3.attackVector`
        -   `vulnerability.cve.exploitationActivity`
        -   `vulnerability.cve.observedInTheWild`
        -   `vulnerability.cve.zeroDay`
        -   `vulnerability.cve.upstreamFixAvailable`
        -   `vulnerability.offendingPackage.packageName`
        -   `vulnerability.offendingPackage.packageVersion`
        -   `vulnerability.fixedPackage.packageVersion`
        -   `vulnerability.securityBulletin.suggestedUpgradeVersion`
*   **Handoff**: Do not draft remediation plans, patch resources, or execute
    configuration commands. Pass the extracted finding payload to the
    appropriate remediation or IAM analyzer skill to manage the remediation
    action loop.

--------------------------------------------------------------------------------

## Reference Schema

See [finding_schema.md](references/finding_schema.md) for the JSON structure of
a Security Command Center finding.
