# Google Cloud CLI Commands Reference for Compliance Environment Assessment

This reference documents the read-only Google Cloud CLI (`gcloud`) commands and server-side field projection masks utilized by the `assuredworkloads-environment-assessor` skill.

## Table of Contents

* [1. Scope Resolution & Ancestry](#1-scope-resolution-ancestry)
* [2. Resource Telemetry Ingestion (Cloud Asset Inventory)](#2-resource-telemetry-ingestion-cloud-asset-inventory)
* [3. Assured Workloads Framework & Control Discovery](#3-assured-workloads-framework-control-discovery)
* [4. Organization Policy Inspection](#4-organization-policy-inspection)
* [5. Assured Workloads Provisioning Contracts (Console UI, v1 GA, and v2 Preview)](#5-assured-workloads-provisioning-contracts-console-ui-v1-ga-and-v2-preview)
  * [5.1 Google Cloud Console](#51-google-cloud-console)
  * [5.2 Assured Workloads v1 GA Provisioning Contract](#52-assured-workloads-v1-ga-provisioning-contract)
  * [5.3 Assured Workloads v2 Preview Contract](#53-assured-workloads-v2-preview-contract)

---

## 1. Scope Resolution & Ancestry

### Resolve Folder Parent & Organization
```bash
# Check direct parent of folder
gcloud resource-manager folders describe FOLDER_ID --format="value(parent)"
```
*If the parent is `folders/...`, recursively inspect until `organizations/{org_id}` is found.*

### Resolve Project Ancestry
```bash
# List all ancestors from project up to root Organization
gcloud projects get-ancestors PROJECT_ID --format="value(id,type)"
```

---

## 2. Resource Telemetry Ingestion (Cloud Asset Inventory)

### Pass 1: Macro Footprint & API Enumeration (100% Coverage Across Scope)
```bash
gcloud asset search-all-resources \
  --scope="folders/123456" \
  --page-size=500 \
  --format="json(name,assetType,location)" > macro_telemetry.json
```

### Pass 2: Conditional Targeted Deep Ingestion for CMEK Readiness (`kmsKeys`)
```bash
gcloud asset search-all-resources \
  --scope="folders/123456" \
  --asset-types="${TARGET_TYPES}" \
  --page-size=500 \
  --format="json(name,assetType,location,kmsKeys)" > deep_telemetry.json
```
*Note: `search-all-resources` is free of charge ($0 billing impact), but Google Cloud requires an administrative project with an active Cloud Billing account attached to allocate API rate limits (`gcloud config set project PROJECT_ID`).*

### Key Field Extraction
* **`name`**: Cloud resource URI (e.g. `//compute.googleapis.com/projects/.../instances/...`).
* **`assetType`**: Google Cloud resource type (e.g. `compute.googleapis.com/Instance`, `storage.googleapis.com/Bucket`).
* **`location`**: Region or zone string (e.g. `us-central1`, `europe-west3`, `us-east4-a`).
* **`kmsKeys`**: Customer-Managed Encryption Key (CMEK) URIs bound to the resource.

---

## 3. Assured Workloads Framework & Control Discovery

### List & Cache Built-in Assured Workloads Frameworks (Including Bound Parameters)
```bash
if [ ! -s frameworks.json ]; then
  gcloud compliance-manager frameworks list \
    --organization=ORGANIZATION_ID \
    --location=global \
    --filter="category:ASSURED_WORKLOADS" \
    --format="json(name,majorRevisionId,displayName,description,category,cloudControlDetails)" > frameworks.json
fi
```

### On-Demand Service Follow-Up: Describe a Specific Cloud Control
```bash
gcloud compliance-manager cloud-controls describe CONTROL_ID \
  --organization=ORGANIZATION_ID \
  --location=global \
  --format="json(name,displayName,description,supportedEnforcementModes,remediationSteps,parameterSpec)"
```

---

## 4. Organization Policy Inspection

### Describe Effective Universal Constraints on Scope (`38/38` Frameworks)
```bash
# Check effective resource locations constraint
gcloud org-policies describe gcp.resourceLocations \
  --folder=FOLDER_ID \
  --effective \
  --format="json(name,spec.rules)"

# Check effective service usage restriction (SUR) constraint
gcloud org-policies describe gcp.restrictServiceUsage \
  --folder=FOLDER_ID \
  --effective \
  --format="json(name,spec.rules)"

# Check effective TLS version restriction constraint
gcloud org-policies describe gcp.restrictTLSVersion \
  --folder=FOLDER_ID \
  --effective \
  --format="json(name,spec.rules)"
```

---

## 5. Assured Workloads Provisioning Contracts (Console UI, v1 GA, and v2 Preview)

### 5.1 Google Cloud Console
Direct the customer or cloud administrator to the Assured Workloads creation wizard in the console:
```
https://console.cloud.google.com/compliance/assuredworkloads?organizationId=ORGANIZATION_ID
```

### 5.2 Assured Workloads v1 GA Provisioning Contract
The public, generally available endpoint for creating a greenfield Assured
Workloads environment:

```
POST https://assuredworkloads.googleapis.com/v1/organizations/{organizationId}/locations/{location}/workloads
```

**Request Payload Structure (v1 GA):**
```json
{
  "displayName": "Sovereign-Workload-Enclave",
  "complianceRegime": "FEDRAMP_HIGH",
  "resourceSettings": [
    {
      "displayName": "Enclave-Folder",
      "resourceType": "CONSUMER_FOLDER"
    }
  ]
}
```

### 5.3 Assured Workloads v2 Preview Contract (In-Place Brownfield Adoption)
For environments binding an Assured Workloads framework directly to an existing
folder or project in Monitor Mode (`DETECTIVE`):

```
POST https://assuredworkloads.googleapis.com/v2/organizations/{organizationId}/locations/{location}/workloads
```

**Request Payload Structure (v2 Preview):**
```json
{
  "displayName": "Sovereign-Workload-Enclave",
  "resourceConfig": {
    "existingFolder": "folders/123456"
  },
  "framework": {
    "framework": "organizations/123456789/locations/global/frameworks/builtin-il5"
  },
  "cloudControlConfigs": [
    {
      "cloudControl": "organizations/123456789/locations/global/cloudControls/builtin-restrict-resource-locations",
      "cloudControlRevision": 1,
      "enforcementMode": "DETECTIVE"
    }
  ]
}
```
