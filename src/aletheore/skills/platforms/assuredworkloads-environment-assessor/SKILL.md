---
name: assuredworkloads-environment-assessor
metadata:
  version: 1.0.0
  category: SecurityAndCompliance
description: 'Discover Google Cloud resource telemetry and effective Org Policies, evaluate brownfield workloads against sovereign data boundary and compliance standards (EU Data Boundary with Support, FedRAMP Moderate/High, DoD IL4/IL5, ITAR, CJIS, US/EU Regional Data Boundaries), and recommend the optimal Assured Workloads Framework. Use when asked to: "assess my environment for compliance", "which Assured Workloads framework should I use?", "check if my folder is ready for EU sovereignty", "evaluate compliance posture for folders/123 or projects/xyz", or "discover compliance gaps before deployment". Do NOT use for live mutating policy deployments, generating deep audit reports via Cloud Audit Manager, generating remediation scripts, or authoring custom Assured Workloads frameworks.'
skill_id: platforms/assuredworkloads-environment-assessor
title: Assuredworkloads Environment Assessor
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/assuredworkloads-environment-assessor
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/assuredworkloads-environment-assessor", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/assuredworkloads-environment-assessor", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 3, "has_scripts": true}, "curated_on": "2026-10-18", "requires": ["scripts"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Compliance Environment Assessor Skill

This skill performs zero-risk, non-mutating compliance discovery, physical telemetry profiling, and data boundary framework recommendation across Google Cloud Folders (`folders/{folder_id}`) and Projects (`projects/{project_id}`).

## Safety & Execution Tier (CRITICAL)

* **Tier R (Strictly Read-Only)**:
  All discovery operations execute read-only queries
  (`gcloud asset search-all-resources`,
  `gcloud compliance-manager frameworks list`,
  `gcloud compliance-manager cloud-controls describe`, and
  `gcloud org-policies describe --effective`).
  You MUST NEVER execute mutating commands (`gcloud compliance-manager framework-deployments create`,
  `gcloud org-policies set-policy`, or `gcloud resource-manager ...`).

---

## Grounding Guardrails: Google Cloud & Assured Workloads Precedence (CRITICAL)

To prevent hallucinations, incorrect compliance assessments, and false blockers derived from generic LLM pre-training, the agent is governed by strict **Grounding Guardrails**:

1. **Strict Prohibition on Generic LLM Pre-Training Priors**:
   * You MUST NOT derive compliance rules, API semantics, or deployment constraints from generic web pre-training (e.g., generic NIST/FedRAMP certification audit checklists, AWS/Azure architectures, or legacy on-prem IT auditor mindsets).
   * In traditional IT audits, non-compliance is a binary go/no-go gate that
     blocks deployment. In Google Cloud Assured Workloads v2, **Monitor Mode
     deploys detective controls without mutating existing Organization
     Policies**, allowing continuous observability without halting Day 0
     rollout. You MUST NOT confuse these paradigms.
2. **Authoritative Google Cloud API Contracts & Scope Grounding**:
   * All API assumptions MUST be grounded in published Google Cloud API contracts:
     - **Assured Workloads v1 GA API** (`assuredworkloads.googleapis.com/v1`): Greenfield-only. Contract only creates a brand-new folder (`resourceType: CONSUMER_FOLDER`). It CANNOT adopt or bind to existing brownfield folders (`folders/...`) or projects in-place.
     - **Assured Workloads v2 API** (`assuredworkloads.googleapis.com/v2`): Brownfield-native. Contract binds directly to existing resource hierarchies (`existingFolder`, `existingProject`) and supports non-mutating Monitor Mode (`enforcementMode: DETECTIVE`).
3. **Client-Side Org Policy Readiness vs. Server-Side CES Boundary**:
   * **Client-Side Scope**: Pre-flight evaluation via
     `scripts/evaluate_control_rules.py` assesses **ONLY the 3 high-confidence
     Organization Policy parameter sets** bound in
     `Framework.cloudControlDetails[].parameters`:
     1. Resource Locations (`builtin-restrict-resource-locations` /
        `constraints/gcp.resourceLocations`)
     2. Service Usage Restrictions / SUR
        (`builtin-enforce-service-usage-restrictions` /
        `constraints/gcp.restrictServiceUsage`)
     3. CMEK Encryption Requirements (`builtin-require-cmek` /
        `constraints/gcp.restrictNonCmekServices`)
   * **Server-Side CES Scope**: Complex Continuous Evaluation Service
     (`ces_rule`) and REST API rules (`rest_api_measurement`) are evaluated
     **server-side by the Continuous Evaluation Service (CES)** after the
     framework is deployed.
   * **Terminology Invariant**: You MUST frame client-side findings as
     **Pre-Flight Org Policy Readiness Recommendations / Potential Drift
     Indicators** (`orgPolicyDriftIndicators`), NEVER as definitive compliance
     "violations" or "deployment blockers" that could contradict an actual
     post-deployment CES run.
4. **Static Built-In Catalog, Session Caching, Support Routing & KAJ Flow**:
   * Built-in Frameworks (`type: BUILT_IN`) returned by
     `gcloud compliance-manager frameworks list` are static configurations
     uniform across organizations. Fetch `frameworks.json` once per session and
     reuse the cached file across all candidate evaluations and follow-up
     queries.
   * **Personnel Support Model**: A framework requires a **Paid
     Sovereign/Regulated Support Subscription** when
     `builtin-restrict-technical-support-services` is present in its
     `cloudControlDetails` (as reflected in its `displayName` and
     `description`). Frameworks without
     `builtin-restrict-technical-support-services` (e.g.,
     `builtin-us-data-boundary`, `builtin-eu-data-boundary`) use **Standard
     Commercial Support** ($0 support uplift).
   * **Key Access Justifications (KAJ) & EKM**: Recommend
     `-with-access-justifications` framework variants only when the customer
     **explicitly requests KAJ/EKM**, or when disambiguating between matching
     regional variants in `frameworks.json`.
5. **Deterministic Tool Gating**:
   * You MUST NOT guess compatibility scores ($$S_{\text{composite}}$$) or resource inventories.
   * You MUST execute `scripts/evaluate_control_rules.py` with `--frameworks-file=frameworks.json` and `scripts/validate_workload_payload.py` with `--frameworks-file=frameworks.json`.

## Prerequisites

Before executing environment assessment, ensure your local or Cloud Shell environment meets the following prerequisites:

1. **Google Cloud SDK**:
   * Install and initialize the latest Google Cloud CLI (`gcloud`): https://docs.cloud.google.com/sdk/docs/install.md.txt
2. **Project Setup & Target Selection**:
   * Select or configure an administrative project for running assessment queries:
     ```bash
     gcloud config set project PROJECT_ID
     ```
3. **Region Configuration & Availability**:
   * Configure a well-supported default region (e.g. `us-central1`):
     ```bash
     gcloud config set compute/region us-central1
     ```
   * Consult official region availability documentation: [Google Cloud Locations](https://cloud.google.com/about/locations). Note that preview models or APIs are often restricted to `global` or `us-central1`.
4. **Required API Enablement**:
   * Enable the required Google Cloud APIs on the administrative project:
     ```bash
     gcloud services enable \
       cloudasset.googleapis.com \
       orgpolicy.googleapis.com \
       assuredworkloads.googleapis.com \
       cloudsecuritycompliance.googleapis.com
     ```
5. **Authentication Setup**:
   * Authenticate your user or service account identity and establish application default credentials:
     ```bash
     gcloud auth login
     gcloud auth application-default login
     ```
6. **Billing & Quota Project Setup (Zero Billing Impact)**:
   * **Zero-Cost Assessment Guarantee**: Running this assessment incurs **$0 in
     Google Cloud infrastructure and API billing charges**. All discovery
     operations (`gcloud asset search-all-resources`,
     `gcloud compliance-manager frameworks list`, and
     `gcloud org-policies describe --effective`) are free read-only metadata
     queries.
   * **Quota Project Requirement**: Google Cloud Service Infrastructure requires an administrative project with an active Cloud Billing account attached to allocate API rate limits and meter service usage (sent via the `X-Goog-User-Project` header), even for free APIs.
   * **Pre-Flight Verification**:
     ```bash
     # 1. Verify active Cloud Billing account is attached to administrative project
     gcloud billing projects describe PROJECT_ID --format="value(billingEnabled)"

     # 2. If using a shared project, ensure your user identity has permission to consume project quota
     gcloud projects add-iam-policy-binding PROJECT_ID \
       --member="user:USER_EMAIL" \
       --role="roles/serviceusage.serviceUsageConsumer"

     # 3. Synchronize Application Default Credentials quota project
     gcloud auth application-default set-quota-project PROJECT_ID
     ```
7. **Commands & Payloads Reference**:
   * Consult the [Google Cloud CLI Commands Reference](references/gcloud-commands.md) for full server-side projection masks, field filters, and API payload definitions.

---

## Step-by-Step Operating Workflow

### Step 1: Ingest Scope & Resolve Organization ID

1. Accept or prompt for the target scope (`folders/{folder_id}` or `projects/{project_id}`). If the user provides an open-ended scope without stating a regulatory regime, DO NOT block on broad questionnaires. Proceed immediately to ingest telemetry.
2. Automatically resolve the parent Organization ID from the scope to drive downstream catalog queries:
   * **For Folders**:
     ```bash
     gcloud resource-manager folders describe FOLDER_ID --format="value(parent)"
     ```
     *(If the parent is another folder, traverse up ancestors until `organizations/{org_id}` is found)*
   * **For Projects**:
     ```bash
     gcloud projects get-ancestors PROJECT_ID --format="value(id,type)"
     ```

### Step 2: Ingest Asset Telemetry & Effective Org Policies via Two-Pass Telescoping Protocol

To guarantee 100% coverage across the scope hierarchy while keeping payloads compact, telemetry ingestion executes in two telescoping passes (see [Google Cloud CLI Commands Reference](references/gcloud-commands.md)):

1. **Pass 1: Macro Footprint & API Enumeration (100% Scope Coverage)**:
   ```bash
   gcloud asset search-all-resources \
     --scope="folders/FOLDER_ID" \
     --page-size=500 \
     --format="json(name,assetType,location)" > macro_telemetry.json
   ```
   * *Pagination & Scope Semantics*: `--page-size=500` fetches 500 cloud assets
     per HTTP API page (the maximum batch size permitted by Cloud Asset
     Inventory), automatically paginating through all assets strictly inside
     `folders/FOLDER_ID` and its descendant projects.
   * *Full Location & SUR Coverage*: `macro_telemetry.json` captures 100% of
     active `location` and `assetType` values required to evaluate both
     `builtin-restrict-resource-locations` (`allowedLocations`) and
     `builtin-enforce-service-usage-restrictions` (`allowedServices` / SUR).
   * If `PERMISSION_DENIED` is returned:
     - If the error states `USER_PROJECT_DENIED` or references `roles/serviceusage.serviceUsageConsumer`, the error is on the **administrative quota project** (it lacks an attached billing account or caller lacks `serviceUsageConsumer`). Follow the Quota Project Recovery playbook and stop running further commands.
     - If the error references the target scope (`folders/...` or `projects/...`), HALT ALL TOOL CALLS IMMEDIATELY (do not run Step 2.2, Step 3, or any further commands) and inform the user they require `roles/cloudasset.viewer` and `roles/orgpolicy.policyViewer` on that scope.
   * If 0 resources are returned, inform the user the target scope is empty.

2. **Inspect Effective Universal Org Policies on Scope (`38/38` Frameworks)**:
   ```bash
   gcloud org-policies describe gcp.resourceLocations \
     --folder=FOLDER_ID \
     --effective \
     --format="json(name,spec.rules)" > effective_locations.json

   gcloud org-policies describe gcp.restrictServiceUsage \
     --folder=FOLDER_ID \
     --effective \
     --format="json(name,spec.rules)" > effective_services.json

   gcloud org-policies describe gcp.restrictTLSVersion \
     --folder=FOLDER_ID \
     --effective \
     --format="json(name,spec.rules)" > effective_tls.json
   ```

### Step 3: Fetch & Cache Built-In Framework Catalog and Filter by Location + SUR

1. **Fetch & Cache Built-In Framework Catalog (with Bound Parameters)**:
   ```bash
   if [ ! -s frameworks.json ]; then
     gcloud compliance-manager frameworks list \
       --organization=ORGANIZATION_ID \
       --location=global \
       --filter="category:ASSURED_WORKLOADS" \
       --format="json(name,majorRevisionId,displayName,description,category,cloudControlDetails)" > frameworks.json
   fi
   ```
   * *Session Caching*: Because built-in `ASSURED_WORKLOADS` frameworks are
     static definitions uniform across organizations, cache the output in
     `frameworks.json` and reuse it for all candidate evaluations, payload
     validations, and follow-up questions.
2. **Filter Candidates by Location + SUR Footprint & Clarify Intent**:
   * Exclude location-less control-plane management metadata
     (`cloudresourcemanager.googleapis.com/*`, `orgpolicy.googleapis.com/*`,
     `cloudasset.googleapis.com/*`, `accesscontextmanager.googleapis.com/*`, and
     `location: "global"` hierarchy nodes) from data-plane location checks.
   * Match active data-plane regions (`location`) and active service prefixes
     (`assetType`) from `macro_telemetry.json` against each framework's
     `builtin-restrict-resource-locations` (`allowedLocations`) and
     `builtin-enforce-service-usage-restrictions` (`allowedServices` / SUR)
     parameters in `frameworks.json`:
     * **US-Only Regions (`us-central1`, `us-east4`)**: Candidates include DoD
       IL5 (`builtin-il5`, adjudicated support), ITAR (`builtin-itar`, US
       persons support), FedRAMP High/Moderate, or US Regional Data Boundary
       (`builtin-us-data-boundary`, standard commercial support).
     * **EU-Only Regions (`europe-west3`, `europe-west1`)**: Candidates include
       EU Data Boundary and Support (`builtin-eu-data-boundary-and-support`) vs.
       standard EU Regional Boundary (`builtin-eu-data-boundary`).
   * **Targeted Disambiguation**:
     * If multiple frameworks match the physical footprint and user regulatory
       intent is unstated (e.g., an open-ended request with no regime specified),
       DO NOT finalize a single primary recommendation yet — present the matching
       candidate frameworks and STOP to ask the user a targeted clarifying
       question about their regulatory intent (e.g., EU Sovereignty, US
       FedRAMP/IL5) and personnel support model (Standard Commercial Support vs.
       Paid Sovereign/Regulated Support).
     * Recommend `-with-access-justifications` (KAJ/EKM) framework variants only
       if the customer explicitly asks for KAJ/EKM or when disambiguating
       between matching regional variants.
     * **Out-of-Scope / Non-Catalog Compliance Standards (e.g., CMMC, UK
       GDPR)**:
       If the user asks to evaluate a compliance standard or legal certification
       that is not present as a built-in `ASSURED_WORKLOADS` framework in
       `frameworks.json` (such as CMMC, UK GDPR, SOC 2, or PCI-DSS), DO NOT
       fabricate or pass non-existent framework IDs (e.g., `builtin-cmmc` or
       `builtin-uk-gdpr`) to `evaluate_control_rules.py` or
       `validate_workload_payload.py`. Explicitly state that the requested
       standard is not a standalone Assured Workloads Data Boundary framework,
       explain the catalog scope boundary (Data Boundary enforces regional data
       residency, SUR, CMEK, and sovereign support routing rather than legal
       privacy or third-party certification audits), and offer the closest
       adjacent `ASSURED_WORKLOADS` frameworks from `frameworks.json` that match
       their active regional footprint (e.g., `builtin-il5` or
       `builtin-fedramp-high` for US defense/CUI workloads, or
       `builtin-eu-data-boundary-and-support` / `builtin-eu-data-boundary` /
       `builtin-uk-data-boundary` for European/UK regional residency).

### Step 4: Conditional Pass 2 CMEK Ingestion & Org Policy Evaluation

1. **Pass 2: Conditional Targeted Deep Ingestion for CMEK Resource Types**:
   Derive `TARGET_TYPES` (the intersection of active CAI `assetType`s in
   `macro_telemetry.json` and CMEK-governed services in `builtin-require-cmek`
   from `frameworks.json`). Only 6 of the 38 Data Boundary frameworks include
   `builtin-require-cmek`; if `TARGET_TYPES` is non-empty, fetch `kmsKeys`:
   ```bash
   TARGET_TYPES=$(python3 scripts/evaluate_control_rules.py \
     --get-target-asset-types \
     --framework=FRAMEWORK_ID \
     --telemetry-file=macro_telemetry.json \
     --frameworks-file=frameworks.json)

   if [ -n "${TARGET_TYPES}" ]; then
     gcloud asset search-all-resources \
       --scope="folders/FOLDER_ID" \
       --asset-types="${TARGET_TYPES}" \
       --page-size=500 \
       --format="json(name,assetType,location,kmsKeys)" > deep_telemetry.json
   fi
   ```
2. **Execute High-Confidence Org Policy Evaluator**:
   Run `scripts/evaluate_control_rules.py` over `macro_telemetry.json` (and
   optional `deep_telemetry.json`) with `frameworks.json` to compute the 3
   quantitative scores (`locationScore`, `serviceScore`, `cmekScore`,
   `compositeScore`) and `orgPolicyDriftIndicators` (when `cmekRequired` is
   `false`, always display the CMEK Readiness column as `N/A` in the scorecard):
   ```bash
   python3 scripts/evaluate_control_rules.py \
     --telemetry-file=macro_telemetry.json \
     --deep-telemetry-file=deep_telemetry.json \
     --framework=FRAMEWORK_ID \
     --frameworks-file=frameworks.json
   ```
3. **Evaluate Operational & Governance Impact**:
   * Review `cloudControlDetails` in `frameworks.json` for parameterized and
     operational controls (`builtin-restrict-kms-protection-levels`,
     `builtin-enforce-allowed-cmek-crypto-key-projects`,
     `builtin-restrict-cloud-function-generations`,
     `builtin-restrict-cloudstorage-auth-types`,
     `builtin-iam-disable-service-account-key-creation`,
     `builtin-disable-ssh-in-browser`, `builtin-enable-axt`, and
     `*-regulatory-settings` controls like
     `builtin-cloud-logging-regulatory-settings`) and summarize their workflow
     impact in the final report.
4. **Validate Turnkey Assured Workloads Payload**:
   ```bash
   python3 scripts/validate_workload_payload.py \
     --payload-json='{"displayName":"Sovereign-Workload-Enclave","resourceConfig":{"existingFolder":"folders/FOLDER_ID"},"framework":{"framework":"organizations/ORGANIZATION_ID/locations/global/frameworks/FRAMEWORK_ID"}}' \
     --org-id=ORGANIZATION_ID \
     --frameworks-file=frameworks.json
   ```

### Step 5: Render Executive Assessment Report (Minto Pyramid Structure)

Present an executive report structured around the **Minto Pyramid Principle**
(Bottom Line Up Front -> Core Value Pillars -> Supporting Evidence -> Action
Plan):

```markdown
# Compliance Environment Assessment Report: [Scope URI]

## 1. Executive Conclusion & Recommendation (Bottom Line Up Front)
* **Primary Recommendation**: [Framework Name] (`organizations/{org_id}/locations/global/frameworks/{framework_id}`)
* **Org Policy Readiness Alignment**: [Composite Fit %]
* **Value & Safety Invariant**: In-place brownfield adoption via Assured Workloads v2 in **Monitor Mode** (`enforcementMode: DETECTIVE`).
  * **Zero Production Outage Risk**: Mutates zero Organization Policies on Day 0.
  * **Continuous Server-Side CES Observability**: Activates real-time Continuous Evaluation Service (CES) monitoring across all framework controls.
  * **No Migration Required**: Protects existing resources in-place without provisioning separate folders.

---

## 2. Core Value Drivers: Why Assured Workloads Fits This Scope
1. **Residency & Boundary Alignment (`gcp.resourceLocations`)**: Active regional workloads are evaluated against allowed regions ([e.g. 100% US-contained]).
2. **Service & API Compatibility (`gcp.restrictServiceUsage` / SUR)**: Active service APIs are evaluated against accredited service allowlists ([e.g. 100% approved services]).
3. **Non-Disruptive Posture Hardening**: Enrolling in Monitor Mode provides server-side CES posture evaluation while allowing your team to triage Org Policy readiness safely.

---

## 3. Objective Assessment Scorecard & Org Policy Readiness Triage

### Quantitative Org Policy Readiness Scorecard
| Candidate Framework | Location Residency (`locationScore`) | Service Usage / SUR (`serviceScore`) | CMEK Readiness (`cmekScore`) | Composite Alignment | Personnel Support Model |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **[Framework 1]** | [XX%] | [XX%] | [XX% or N/A] | **[XX%]** | [Support Model] |
| **[Framework 2]** | [XX%] | [XX%] | [XX% or N/A] | **[XX%]** | [Support Model] |

### Active Resource & Effective Org Policy Baseline
* **Discovered Inventory**: [Total Assets count] across [Active resource types and regions].
* **Effective Org Policy Baseline**: [Effective `gcp.resourceLocations`, `gcp.restrictServiceUsage`, and `gcp.restrictTLSVersion` posture].

### Pre-Flight Org Policy Readiness Recommendations (Prior to Enforced Mode)
*Pre-flight drift indicators do NOT block Day 0 deployment in Monitor Mode. Address these prior to enabling preventive policy enforcement (authoritative CES and REST control evaluations run server-side after deployment):*
* **[Priority 1]**: [e.g. Configure CMEK keys on unencrypted resources before toggling `builtin-require-cmek` to `PREVENTIVE`].
* **[Priority 2]**: [e.g. Operational impact of bound controls such as `builtin-restrict-kms-protection-levels` or `builtin-cloud-logging-regulatory-settings`].

---

## 4. Deployment Action Plan & Commercial Advisory

### Deploy via Google Cloud Console (Recommended)
Review and deploy the recommended framework directly in the Google Cloud Console:
👉 **[Open Assured Workloads Console](https://console.cloud.google.com/compliance/assuredworkloads?organizationId=ORGANIZATION_ID)**
*(The Console deployment wizard binds composing controls in Monitor Mode with zero infrastructure mutations).*

### Commercial & Licensing Advisory
* Assured Workloads compliance regimes may carry support routing or licensing entitlements depending on whether `builtin-restrict-technical-support-services` is bound (standard commercial support vs. paid sovereign/regulated support).
* Review full pricing details and entitlement tiers at: https://cloud.google.com/assured-workloads/pricing
```

### Step 6: Handling Customer Follow-Up Questions on Specific Services

When a customer follows up with a specific question about how a recommended or
candidate framework affects a particular Google Cloud service (e.g., Cloud Run,
Cloud Storage, Spanner, GKE, Cloud SQL, Vertex AI, Cloud Logging):
1. **Inspect Cached `frameworks.json` Locally (or Fetch via Step 3 `frameworks list` if Not Cached)**:
   * If `frameworks.json` is not yet present in the current working directory, run the Step 3 `gcloud compliance-manager frameworks list` command first to populate `frameworks.json`.
   * Check whether `<service>.googleapis.com` is permitted in
     `builtin-enforce-service-usage-restrictions` (`allowedServices`) and
     whether CMEK is required in `builtin-require-cmek` (`services`).
   * Identify all service-specific `cloudControlDetails[].name` entries bound in
     that framework (e.g., `builtin-run-disable-ephemeral-disk`,
     `builtin-restrict-cloudstorage-auth-types`,
     `builtin-disable-spanner-multi-region`,
     `builtin-cloud-logging-regulatory-settings`) and any bound `parameters`.
2. **Fetch Authoritative Control Details On Demand**:
   * Because `Framework.cloudControlDetails` omits control descriptions,
     enforcement modes, and remediation steps, run `cloud-controls describe` for
     the relevant control ID(s):
     ```bash
     gcloud compliance-manager cloud-controls describe CONTROL_ID \
       --organization=ORGANIZATION_ID \
       --location=global \
       --format="json(name,displayName,description,supportedEnforcementModes,remediationSteps,parameterSpec)"
     ```
   * Extract the exact underlying Organization Policy constraint (e.g.,
     `run.managed.disableEphemeralDisk`, `storage.restrictAuthTypes`),
     `supportedEnforcementModes` (`PREVENTIVE` vs. `DETECTIVE`), and
     `remediationSteps` from the response.
3. **Check Effective Folder/Project Policy If Requested**:
   * If the customer asks whether their folder already enforces that
     service-specific Org Policy constraint, run:
     ```bash
     gcloud org-policies describe CONSTRAINT_NAME \
       --folder=FOLDER_ID \
       --effective \
       --format="json(name,spec.rules)"
     ```

---

## Failure Modes & Error Recovery Playbook

| Error Condition | Immediate Action | User Recovery Guidance |
| :--- | :--- | :--- |
| **`USER_PROJECT_DENIED` or `serviceusage.serviceUsageConsumer`** | HALT discovery. Do not confuse with target scope permissions. Check administrative project billing status and caller quota permissions. | *"The administrative project `PROJECT_ID` cannot be used for API quota. 1) Verify a Cloud Billing account is attached (`gcloud billing projects describe PROJECT_ID`), 2) Grant `roles/serviceusage.serviceUsageConsumer` on `PROJECT_ID` to your identity, or 3) Switch to a project you own: `gcloud config set project <PROJECT_ID>`."* |
| **`PERMISSION_DENIED` on CAIS** | HALT discovery immediately. Do not speculate or guess resources. | *"To analyze this environment, grant `roles/cloudasset.viewer` on `folders/123` to your active identity."* |
| **`PERMISSION_DENIED` on Org Policies** | HALT policy inspection. Do not guess effective constraints. | *"To inspect effective organization policies, grant `roles/orgpolicy.policyViewer` on `folders/123`."* |
| **Empty Scope (0 Resources)** | HALT evaluation. Inform user the scope contains no active assets. | *"No active resources found in `folders/123`. Provision resources or select an active scope."* |
| **Conflicting Regulatory Intent** | Engage user in focused clarification probe. | Present distinct requirements (e.g. DoD IL5 Adjudicated Support vs. ITAR US Persons) and ask for business requirement. |
| **Zero Catalog Match** | Detail multi-regional drift indicator. | Explain why single-region sovereign data boundary frameworks were ruled out (e.g. multi-continental resource footprint spanning unapproved regions). |
| **Out-of-Scope / Non-Catalog Standard (e.g., CMMC, UK GDPR)** | Do not invent framework IDs (`builtin-cmmc`, `builtin-uk-gdpr`). Explain catalog boundary and map to adjacent `ASSURED_WORKLOADS` candidates. | *"Assured Workloads does not include a standalone `[CMMC / UK GDPR]` framework in `frameworks.json`. However, based on your active resources, we can evaluate an adjacent Assured Workloads Data Boundary framework such as `builtin-il5` / `builtin-fedramp-high` (for US defense/federal workloads) or `builtin-eu-data-boundary-and-support` / `builtin-uk-data-boundary` (for European/UK regional residency)."* |
