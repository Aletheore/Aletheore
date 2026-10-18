# Agent Platform Policies — Debugging Reference

Reference for diagnosing IAM, IAP, and Agent Gateway policy denials in the
**Google Cloud Gemini Enterprise Agent Platform**. Read this when an agent gets
a 403, when an MCP/agent/endpoint call is silently dropped, or when a freshly
applied policy "doesn't take."

The Agent Platform supports two policy architectures:

1.  **IAM Unified Access Policy (UAP / Policy V2)**: The next-generation
    architecture utilizing decoupled, declarative `AccessPolicy` and
    `PolicyBinding` resources (`iam.googleapis.com/v3`) bound directly to the
    Resource Manager hierarchy (Organization, Folder, Project) and evaluated via
    IAP Authz Extension (`iapPolicyVersion: "V2"`).
2.  **Legacy IAM v1**: Monolithic per-resource policy bindings applied directly
    to Agent Registry resources or synthetic shadow resources (`iap_web`) using
    `roles/iap.egressor`.

## Table of Contents

-   [TL;DR for debugging](#tldr-for-debugging)
-   [1. UAP (Policy V2) vs. Legacy IAM v1 Architecture](#1-uap-policy-v2-vs-legacy-iam-v1-architecture)
-   [2. UAP Core Architectural Constructs](#2-uap-core-architectural-constructs)
-   [3. Runtime Evaluation Pipeline & Mathematical Invariants](#3-runtime-evaluation-pipeline--mathematical-invariants)
-   [4. Deep CEL Attribute Reference](#4-deep-cel-attribute-reference)
-   [5. The 3-Tier Organizational Security Model](#5-the-3-tier-organizational-security-model)
-   [6. Legacy IAM v1 Reference (Backward Compatibility)](#6-legacy-iam-v1-reference-backward-compatibility)
-   [7. Observability, Auditing, & Dry-Run Operations](#7-observability-auditing--dry-run-operations)
-   [8. Production Edge Cases & Architectural Gotchas](#8-production-edge-cases--architectural-gotchas)
-   [9. Quick Command Cheat Sheet](#9-quick-command-cheat-sheet)
-   [10. Debugging Flowchart](#10-debugging-flowchart)

--------------------------------------------------------------------------------

## TL;DR for debugging

1.  **Check Policy Architecture First (`V1` vs `V2`)**:

    -   In Gateway metadata: Check if the attached IAP Authz Extension specifies
        `iapPolicyVersion: "V2"`. Without this key, the gateway defaults to
        legacy V1 shadow-resource evaluation.
    -   In Cloud Audit Logs (`protoPayload.serviceName="iap.googleapis.com"`):
        Check `protoPayload.metadata.iapPolicyVersion`. If `"v2"`, diagnose UAP
        `AccessPolicy` and `PolicyBinding` resources. If absent or `"v1"`,
        diagnose legacy IAM v1 bindings on Agent Registry entries.

2.  **The UAP Permission MUST be FQDN**: In UAP access policy rules, the
    permission MUST be specified in full FQDN format:
    **`iap.googleapis.com/resources.egressViaIAP`**. Using the short form
    `iap.resources.egressViaIAP` causes the IAM policy compiler to reject the
    policy.

3.  **UAP Policy Bindings Target Resource Manager Nodes**: `PolicyBinding`
    targets must be canonical Resource Manager URIs using numeric IDs (e.g.
    `//cloudresourcemanager.googleapis.com/projects/${PROJECT_NUMBER}` or
    `//cloudresourcemanager.googleapis.com/folders/${FOLDER_ID}`). Passing
    gateway or registry resource names causes
    `INVALID_TARGET_FULL_RESOURCE_NAME`.

4.  **UAP Invariants: Absolute DENY Precedence & Additive ALLOW Aggregation**:

    -   **Absolute DENY Precedence**: If ANY matching rule across Org, Folder,
        or Project evaluates to DENY, the request is immediately rejected (HTTP
        403). There are NO deny exceptions.
    -   **Additive ALLOW Aggregation**: Permissions granted at Org/Folder level
        cannot be narrowed or revoked by Project developers.

5.  **Mandatory CEL Null-Dereference Guard**: When an agent calls non-tool MCP
    methods (`prompts/list`, `resources/read`), the `tool` object inside
    `mcp_server` is `null`. A CEL condition dereferencing `.tool.name` or
    `.tool.annotations.*` without verifying the method crashes Gatekeeper with a
    CEL evaluation error. Always prepend:
    `(destination.agent_registry.mcp_server.method == 'tools')`.

6.  **Avoid Blanket Unregistered DENY (Container Boot Crash)**: Never configure
    an Org or Folder-level rule with `(destination.is_registered == false)` and
    `effect: DENY`. Reasoning Engine containers boot by contacting public
    endpoints (`telemetry.mtls.googleapis.com`). An absolute DENY causes
    container boot crashes. Rely instead on Gateway default-deny.

7.  **Zendesk / REST Path Extensions (`.json`)**: Strict prefix matching in
    unregistered UAP rules (e.g. `.startsWith('/api/v2/tickets')`) leaves
    vulnerabilities to sibling paths (e.g. `/api/v2/tickets_export`), while
    exact matching on `/api/v2/tickets` breaks SDK clients that append `.json`
    (`/api/v2/tickets.json`). Match exact paths, `/` subpaths, `.json`, and `?`
    query strings explicitly.

8.  **Org Policy Blocker
    (`constraints/iam.managed.disableAccessPolicyBinding`)**: If creating a
    policy binding fails with `CUSTOM_ORG_POLICY_VIOLATION`, an Org Admin must
    set an Org Policy V2 override setting `enforce: false` at the target CRM
    level.

9.  **Terraform Binding Destruction Race**: Simultaneous destruction of
    `PolicyBinding` and `AccessPolicy` fails with `400 FAILED_PRECONDITION:
    Access policy is referenced by active policy binding(s)` due to IAM
    replication latency (10–30 s). Destroy scripts must poll GET the binding URL
    until HTTP 404 is confirmed before deleting the policy.

10. **Legacy IAM v1 Fallback**: In legacy v1, outbound agent traffic is
    authorized by granting `roles/iap.egressor` directly on the Agent Registry
    resource (`gcloud iap web set-iam-policy --resource-type=agent-registry`).
    Do NOT use `roles/iap.tunnelResourceAccessor` or web ingress roles.

--------------------------------------------------------------------------------

## 1. UAP (Policy V2) vs. Legacy IAM v1 Architecture

```
+-------------------------------------------------------------------------------+
| Legacy IAM v1 Model (Monolithic & Per-Resource)                               |
|                                                                               |
|   Resource (e.g. Storage Bucket, IAP Web Backend Shadow Resource)             |
|   └── IAM Policy                                                              |
|       └── Bindings: [Role: roles/iap.egressor, Members: [principal://...]]   |
|                                                                               |
|   Limitations:                                                                |
|   - Policy is physically glued to the target resource / shadow resource.      |
|   - Zero reusability across projects or folders.                              |
|   - Synthetic shadow resources: projects/123/iap_web/compute/services/...     |
|   - Coarse-grained network/L7 attributes.                                     |
+-------------------------------------------------------------------------------+

                                      │
                                      ▼

+-------------------------------------------------------------------------------+
| Unified Access Policy (UAP / Policy V2) Model (Decoupled & Hierarchical)      |
|                                                                               |
|      +──────────────────────────+          +───────────────────────────+      |
|      │   CRM Hierarchy Node     │          │       AccessPolicy        │      |
|      │ (Org, Folder, Project)   │          │ (Reusable Rule Definition)│      |
|      +─────────────┬────────────+          +─────────────┬─────────────+      |
|                    │                                     │                    |
|                    │               binds to              │                    |
|                    └──────────────► ◄────────────────────┘                    |
|                                     │                                         |
|                          +──────────┴──────────+                              |
|                          │    PolicyBinding    │                              |
|                          │ target: CRM Node    │                              |
|                          │ policy: AccessPolicy│                              |
|                          +─────────────────────+                              |
+-------------------------------------------------------------------------------+
```

### Architectural Comparison Matrix

| Feature        | Legacy IAM v1              | Unified Access Policy (UAP / Policy V2)                                        |
: Dimension      :                            :                                                                                :
| :------------- | :------------------------- | :----------------------------------------------------------------------------- |
| **Coupling**   | **Tightly Coupled**:       | **Decoupled**: Standalone first-class objects referenced by multiple bindings. |
:                : Policies live inside       :                                                                                :
:                : individual resource        :                                                                                :
:                : metadata.                  :                                                                                :
| **Attachment   | Concrete service resources | Canonical Resource Manager nodes:                                              |
: Target**       : or synthetic shadow        : `//cloudresourcemanager.googleapis.com/{organizations|folders|projects}/{ID}`. :
:                : resources (`iap_web`).     :                                                                                :
| **Rule         | Primitive                  | Explicit `effect: ALLOW` or `effect: DENY`, target FQDN permissions, and deep  |
: Capabilities** : Role-to-Principal bindings : L7 CEL conditions.                                                             :
:                : with basic contextual      :                                                                                :
:                : conditions.                :                                                                                :
| **Evaluation   | Unordered role evaluation; | Unified evaluation pipeline: Org $\rightarrow$ Folder $\rightarrow$ Project    |
: Order**        : Deny policies exist as a   : with **strict absolute DENY precedence** and **additive ALLOW aggregation**.   :
:                : separate API               :                                                                                :
:                : (`iam.googleapis.com/v2`). :                                                                                :
| **Enforcement  | Binary (always enforced).  | Dual-mode: **`DRY_RUN`** (audit log simulation) or **`ENFORCED`** (active      |
: Modes**        :                            : blocking).                                                                     :
| **Workload     | Static service accounts    | Extends natively to ephemeral **Workload Identity Federation for Agents**      |
: Targets**      : and human users.           : (`principal\://agents.global.org-...`).                                        :

--------------------------------------------------------------------------------

## 2. UAP Core Architectural Constructs

UAP is comprised of two core REST resources in the `iam.googleapis.com/v3` API:

### 2.1 AccessPolicy (`iam.googleapis.com/v3/.../accessPolicies`)

An `AccessPolicy` is a versioned container of authorization rules defining
*what* is permitted or forbidden, *for whom*, and *under what conditions*.

```json
{
  "name": "folders/123456789012/locations/global/accessPolicies/platform-egress-policy",
  "displayName": "Platform Egress Policy",
  "rules": [
    {
      "description": "Permit access to registered corporate tools",
      "effect": "ALLOW",
      "principals": [
        "principalSet://agents.global.org-112233445566.system.id.goog/attribute.platform/aiplatform"
      ],
      "operations": [
        {
          "permissions": [
            "iap.googleapis.com/resources.egressViaIAP"
          ]
        }
      ],
      "condition": {
        "title": "allow_registered_mcp",
        "description": "Matches registered corporate MCP servers",
        "expression": "(destination.is_registered == true) && (destination.agent_registry.resource_type == 'MCP_SERVER')"
      }
    }
  ]
}
```

#### Key Properties:

-   **`effect`**: Either `ALLOW` or `DENY`.
-   **`principals`**: SPIFFE/OIDC agent identity strings or principal sets.
-   **`operations.permissions`**: Fully Qualified Domain Name (FQDN) format:
    `iap.googleapis.com/resources.egressViaIAP`.
-   **`condition`**: Common Expression Language (CEL) boolean predicate.

### 2.2 PolicyBinding (`iam.googleapis.com/v3/.../policyBindings`)

A `PolicyBinding` attaches an `AccessPolicy` to a specific CRM node.

```json
{
  "name": "folders/123456789012/locations/global/policyBindings/bind-platform-egress",
  "displayName": "Bind Platform Egress to Engineering Folder",
  "target": {
    "resource": "//cloudresourcemanager.googleapis.com/folders/123456789012"
  },
  "policy": "folders/123456789012/locations/global/accessPolicies/platform-egress-policy",
  "policyKind": "ACCESS"
}
```

-   **`target.resource`**: Canonical Resource Manager URI using the numeric ID:
    -   Org: `//cloudresourcemanager.googleapis.com/organizations/${ORG_ID}`
    -   Folder: `//cloudresourcemanager.googleapis.com/folders/${FOLDER_ID}`
    -   Project:
        `//cloudresourcemanager.googleapis.com/projects/${PROJECT_NUMBER}` (must
        be numeric project number, not string project ID).
-   **`policyKind`**: Set to `ACCESS`.

--------------------------------------------------------------------------------

## 3. Runtime Evaluation Pipeline & Mathematical Invariants

```
+-----------------------------------------------------------------------------------+
| 1. Workload Egress                                                                |
|    Agent container issues outbound request (e.g. HTTP GET /api/v2/tickets.json)   |
|    Injected proxy environment directs traffic to Envoy / Agent Gateway.           |
+------------------------------------------┬----------------------------------------+
                                           │
                                           ▼
+-----------------------------------------------------------------------------------+
| 2. PEP Interception & Gatekeeper Resolution                                       |
|    - IAP Authz Extension intercepts the request (Envoy ext_authz).               |
|    - Gatekeeper verifies caller's SPIFFE/OIDC Agent Identity token.               |
|    - Gatekeeper queries Agent Registry:                                           |
|      * If target matches registry: sets destination.is_registered = true          |
|      * If target not cataloged:   sets destination.is_registered = false         |
|    - Populates request attributes into the destination CEL context.               |
+------------------------------------------┬----------------------------------------+
                                           │
                                           ▼
+-----------------------------------------------------------------------------------+
| 3. PDP Traversal: IAM CheckPolicy()                                               |
|    IAM traverses the CRM hierarchy top-down or bottom-up:                         |
|           Organization (organizations/112233445566)                              |
|                 │                                                                 |
|                 ▼                                                                 |
|           Folder (folders/123456789012)                                           |
|                 │                                                                 |
|                 ▼                                                                 |
|           Project (projects/9876543210)                                           |
|    - Evaluates all active PolicyBindings attached to all nodes in the path.       |
+------------------------------------------┬----------------------------------------+
                                           │
                                           ▼
+-----------------------------------------------------------------------------------+
| 4. Evaluation Logic Engine                                                        |
|    [Step A: Check for Absolute DENY]                                              |
|    Did ANY matching rule across Org, Folder, or Project evaluate to DENY?         |
|    ├── YES ──► REJECT IMMEDIATELY (HTTP 403 Forbidden / PERMISSION_DENIED)       |
|    └── NO                                                                         |
|         │                                                                         |
|         ▼                                                                         |
|    [Step B: Aggregate Additive ALLOWs]                                            |
|    ALLOW_effective = ALLOW_project ∪ ALLOW_folder ∪ ALLOW_org                     |
|    Did AT LEAST ONE rule match with effect: ALLOW?                                |
|    ├── YES ──► PERMIT REQUEST (Forward to backend target)                         |
|    └── NO  ──► DEFAULT-DENY (HTTP 403 Forbidden)                                  |
+-----------------------------------------------------------------------------------+
```

### The Two Mathematical Invariants of UAP

1.  **Absolute DENY Precedence**: $$ ext{Decision} = ext{DENY} \quad ext {if}

    \quad \exists \, r \in ( ext{Rules}_{ ext{org}} \cup ext{Rules}_{
    ext{folder}} \cup ext{Rules}_{ ext{project}}) ext{ such that } ext{Match}(r)
    \land r. ext{effect} = ext{DENY}$$ There are **no deny exceptions** in UAP.
    If an Org-level rule denies a destination, no project developer or platform
    admin can override or bypass that denial.

2.  **Additive ALLOW Aggregation**: $$ ext{ALLOW}_{ ext{effective}} =
    ext{ALLOW}_{ ext{org}} \cup ext{ALLOW}_{ ext{folder}} \cup ext{ALLOW}_{
    ext{project}}$$ Permissions granted at higher levels cannot be narrowed,
    scoped down, or revoked by lower tiers. If a Platform Admin allows all
    `*.corp.example.com` at the Folder level, an individual Project Developer
    cannot block an agent from accessing specific corporate subdomains.

--------------------------------------------------------------------------------

## 4. Deep CEL Attribute Reference

UAP evaluates CEL expressions within the `iap.googleapis.com` namespace rooted
at the `destination` object.

```
                              destination
                                   │
         ┌─────────────────────────┴─────────────────────────┐
         ▼                                                   ▼
   is_registered == true                               is_registered == false
         │                                                   │
   agent_registry                                       unregistered
   ├── resource_type                                    ├── host
   ├── location                                         ├── path
   ├── project_id                                       └── method
   ├── endpoint.name
   ├── mcp_server.name
   ├── mcp_server.method
   ├── mcp_server.tool.name
   ├── mcp_server.tool.annotations.read_only_hint
   ├── mcp_server.tool.annotations.destructive_hint
   └── agent.name
```

### 4.1 Registered Destinations (`destination.is_registered == true`)

Populated when the target is cataloged in the Gemini Enterprise **Agent
Registry**.

| Attribute                                                                 | Type   | Supported       | Purpose / Example                               |
:                                                                           :        : Operators       :                                                 :
| :------------------------------------------------------------------------ | :----- | :-------------- | :---------------------------------------------- |
| `destination.is_registered`                                               | bool   | `==`, `!=`      | Root guard. Must be verified before accessing   |
:                                                                           :        :                 : sub-attributes.                                 :
| `destination.agent_registry.resource_type`                                | string | `==`, `!=`,     | Resource type: `'ENDPOINT'`, `'MCP_SERVER'`,    |
:                                                                           :        : `in`            : `'AGENT'`, `'SKILL'`.                           :
| `destination.agent_registry.location`                                     | string | `==`, `!=`,     | Region of the registry resource (`'global'`,    |
:                                                                           :        : `.startsWith()` : `'us-central1'`).                               :
| `destination.agent_registry.project_id`                                   | string | `==`, `!=`,     | Host project of the registry entry              |
:                                                                           :        : `in`            : (`'prod-agent-hub'`).                           :
| `destination.agent_registry.endpoint.name`                                | string | `==`, `!=`,     | Full resource name:                             |
:                                                                           :        : `.endsWith()`   : `projects/{p}/locations/{l}/endpoints/{name}`.  :
| `destination.agent_registry.mcp_server.name`                              | string | `==`, `!=`,     | Resource name:                                  |
:                                                                           :        : `.startsWith()` : `projects/{p}/locations/{l}/mcpServers/{name}`. :
| `destination.agent_registry.mcp_server.method`                            | string | `==`, `!=`,     | MCP protocol method: `'tools'`, `'prompts'`,    |
:                                                                           :        : `in`            : `'resources'`.                                  :
| `destination.agent_registry.mcp_server.tool.name`                         | string | `==`, `!=`,     | Specific tool name being executed (e.g.         |
:                                                                           :        : `in`            : `'updateTicket'`).                              :
| `destination.agent_registry.mcp_server.tool.annotations.read_only_hint`   | bool   | `==`, `!=`      | `true` if tool is declared read-only by tool    |
:                                                                           :        :                 : author.                                         :
| `destination.agent_registry.mcp_server.tool.annotations.destructive_hint` | bool   | `==`, `!=`      | `true` if tool performs irreversible mutations. |
| `destination.agent_registry.agent.name`                                   | string | `==`, `!=`,     | Full resource name of target agent for          |
:                                                                           :        : `.endsWith()`   : Agent-to-Agent mesh egress.                     :

> [!CAUTION] **Null Dereference Guard Pattern**: When an agent calls a non-tool
> MCP method (such as `prompts/list` or `resources/read`), the `tool` message
> inside `mcp_server` is `null`. If a CEL rule dereferences `.tool.name` or
> `.tool.annotations.*` without checking the method, Gatekeeper throws a CEL
> evaluation error!
>
> **Mandatory pattern**:
>
> ```cel
> (destination.agent_registry.mcp_server.method == 'tools') && (destination.agent_registry.mcp_server.tool.annotations.read_only_hint == true)
> ```

### 4.2 Unregistered Destinations (`destination.is_registered == false`)

Populated when an agent calls an external IP, public URL, SaaS endpoint, or
uncataloged GCP service.

Attribute                         | Type   | Supported Operators                           | Purpose / Example
:-------------------------------- | :----- | :-------------------------------------------- | :----------------
`destination.unregistered.host`   | string | `==`, `!=`, `in`, `.endsWith()`, `.matches()` | Target host FQDN: `'acme.zendesk.com'`, `'api.github.com'`.
`destination.unregistered.path`   | string | `==`, `!=`, `.startsWith()`, `.contains()`    | Request URI path: `'/api/v2/tickets.json'`.
`destination.unregistered.method` | string | `==`, `!=`, `in`                              | HTTP verb: `'GET'`, `'POST'`, `'PUT'`, `'DELETE'`.

> [!IMPORTANT] **Tool Annotations Unavailable on Unregistered Endpoints**:
> Unregistered endpoints do NOT possess semantic annotations (`read_only_hint`,
> `destructive_hint`). The proxy only sees raw L7 wire data (host, path,
> method). Restricting read-only behavior on unregistered endpoints must be
> enforced via HTTP methods (`destination.unregistered.method == 'GET'`).

### 4.3 Cloud Console Parenthesization Rule

To ensure the Google Cloud Console UI parses and renders UAP rules as standard
interactive resources (Endpoint, MCP Server, Agent) rather than raw CEL text
fallbacks, **each CEL clause MUST be wrapped in parentheses**:

```cel
(destination.agent_registry.endpoint.name == 'projects/123/locations/us-central1/endpoints/my-ep') && (destination.agent_registry.location == 'us-central1')
```

### 4.4 Principal Identity Grammar

```cel
// 1. Single Agent:
principal://agents.global.org-${ORG_ID}.system.id.goog/resources/aiplatform/projects/${PROJECT_NUMBER}/locations/${LOCATION}/reasoningEngines/${ENGINE_ID}

// 2. All Agents across the entire Organization:
principalSet://agents.global.org-${ORG_ID}.system.id.goog/*

// 3. All Vertex AI / Reasoning Engine Agents in the Org:
principalSet://agents.global.org-${ORG_ID}.system.id.goog/attribute.platform/aiplatform

// 4. All Agents scoped to a specific consumer Project:
principalSet://agents.global.org-${ORG_ID}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}
```

--------------------------------------------------------------------------------

## 5. The 3-Tier Organizational Security Model

```
=================================================================================
TIER 1: SECURITY ADMIN (ORGANIZATION SCOPE)
Scope: organizations/${ORG_ID}
Goal: Enterprise Perimeter Guardrails & Telemetry Survival
---------------------------------------------------------------------------------
Rules:
  [ALLOW] Essential Container Telemetry (telemetry.mtls.googleapis.com)
  [DENY]  Unapproved Commercial LLMs (api.openai.com, api.anthropic.com)
=================================================================================
                                │
                                ▼
=================================================================================
TIER 2: PLATFORM ADMIN (FOLDER SCOPE)
Scope: folders/${FOLDER_ID}
Goal: Shared Corporate Defaults & Developer Autonomy Baselines
---------------------------------------------------------------------------------
Rules:
  [ALLOW] Registered Google Cloud APIs (.../endpoints/googleapis)
  [ALLOW] Corporate Intranet Apex & Subdomains (*.corp.example.com)
  [ALLOW] Central Read-Only MCP Tools (read_only_hint == true)
=================================================================================
                                │
                                ▼
=================================================================================
TIER 3: AGENT DEVELOPER (PROJECT SCOPE)
Scope: projects/${PROJECT_NUMBER}
Goal: Granular, Least-Privilege Workload Authorizations
---------------------------------------------------------------------------------
Rules:
  [ALLOW] Mutating Tool updateTicket on Registered Jira MCP Server
  [ALLOW] Strict Zendesk Ticket API (/api/v2/tickets.json, GET/POST/PUT)
  [ALLOW] Scoped GitHub Org Query (/repos/my-org/, GET/POST)
=================================================================================
```

### Concrete Terraform Implementation (`google-beta` provider)

```hcl
# ------------------------------------------------------------------------------
# TIER 1: Organization Guardrails
# ------------------------------------------------------------------------------
resource "google_iam_organization_access_policy" "org_guardrails" {
  provider         = google-beta
  organization     = var.org_id
  location         = "global"
  access_policy_id = "org-security-guardrails"
  display_name     = "Organization Security Guardrails"

  rules {
    description = "ALLOW: Essential telemetry for Reasoning Engine boot"
    principals  = ["principalSet://agents.global.org-${var.org_id}.system.id.goog/attribute.platform/aiplatform"]
    operations {
      permissions = ["iap.googleapis.com/resources.egressViaIAP"]
    }
    condition {
      title      = "allow_runtime_telemetry"
      expression = "(destination.is_registered == false) && (destination.unregistered.host in ['telemetry.mtls.googleapis.com', 'cloudlogging.googleapis.com', 'monitoring.googleapis.com'])"
    }
  }

  rules {
    description = "DENY: Prevent data exfiltration to unauthorized commercial LLMs"
    principals  = ["principalSet://agents.global.org-${var.org_id}.system.id.goog/*"]
    operations {
      permissions = ["iap.googleapis.com/resources.egressViaIAP"]
    }
    condition {
      title      = "deny_unapproved_llms"
      expression = "(destination.is_registered == false) && (destination.unregistered.host in ['api.openai.com', 'api.anthropic.com', 'api.cohere.ai'])"
    }
  }
}

# ------------------------------------------------------------------------------
# TIER 2: Folder Platform Defaults
# ------------------------------------------------------------------------------
resource "google_iam_folder_access_policy" "folder_defaults" {
  provider         = google-beta
  folder           = var.folder_id
  location         = "global"
  access_policy_id = "folder-platform-defaults"
  display_name     = "Folder Platform Defaults"

  rules {
    description = "ALLOW: Read-only tools on registered corporate MCP servers"
    principals  = ["principalSet://agents.global.org-${var.org_id}.system.id.goog/attribute.platform/aiplatform"]
    operations {
      permissions = ["iap.googleapis.com/resources.egressViaIAP"]
    }
    condition {
      title      = "allow_readonly_corporate_mcp_tools"
      expression = "(destination.is_registered == true) && (destination.agent_registry.resource_type == 'MCP_SERVER') && (destination.agent_registry.mcp_server.name.startsWith('projects/corp-tools-hub/')) && (destination.agent_registry.mcp_server.method == 'tools') && (destination.agent_registry.mcp_server.tool.annotations.read_only_hint == true)"
    }
  }
}

# ------------------------------------------------------------------------------
# TIER 3: Project Agent-Specific Workload Policy
# ------------------------------------------------------------------------------
resource "google_iam_access_policy" "project_workload_egress" {
  provider         = google-beta
  parent           = "projects/${var.project_number}"
  location         = "global"
  access_policy_id = "support-bot-egress"
  display_name     = "Support Bot Workload Egress Policy"

  rules {
    description = "ALLOW: Specific ticket operations with verb constraints"
    principals  = [
      "principal://agents.global.org-${var.org_id}.system.id.goog/resources/aiplatform/projects/${var.project_number}/locations/us-central1/reasoningEngines/customer-support-bot"
    ]
    operations {
      permissions = ["iap.googleapis.com/resources.egressViaIAP"]
    }
    condition {
      title      = "allow_zendesk_api_constrained"
      expression = "(destination.is_registered == false) && (destination.unregistered.host == 'acme.zendesk.com') && ((destination.unregistered.path == '/api/v2/tickets') || (destination.unregistered.path == '/api/v2/tickets.json') || (destination.unregistered.path.startsWith('/api/v2/tickets/')) || (destination.unregistered.path.startsWith('/api/v2/tickets?')) || (destination.unregistered.path.startsWith('/api/v2/tickets.json?'))) && (destination.unregistered.method in ['GET', 'POST', 'PUT'])"
    }
  }
}
```

--------------------------------------------------------------------------------

## 6. Legacy IAM v1 Reference (Backward Compatibility)

In deployments where the Gateway AuthzExtension does NOT specify
`iapPolicyVersion: "V2"`, IAP operates in legacy V1 mode:

### 6.1 Assigning `roles/iap.egressor`

Apply bindings using `gcloud iap web set-iam-policy`:

```bash
# Registry-wide scope
gcloud iap web set-iam-policy agents-iap-policy.json   --project=PROJECT_ID   --resource-type=agent-registry   --region=REGION

# Specific MCP Server scope
gcloud iap web set-iam-policy mcp-iap-policy.json   --project=PROJECT_ID   --region=REGION   --mcp-server=MCP_SERVER_NAME
```

### 6.2 Inspecting Legacy Policies

```bash
gcloud iap web get-iam-policy   --project=PROJECT_ID   --resource-type=agent-registry   --region=REGION
```

--------------------------------------------------------------------------------

## 7. Observability, Auditing, & Dry-Run Operations

### 7.1 Gateway AuthzExtension Configuration

In the Agent Gateway’s IAP Authz Extension metadata:

-   `iapPolicyVersion: "V2"`: **Mandatory** to activate UAP policy evaluation.
-   `iamEnforcementMode: "DRY_RUN"`: Evaluates complete hierarchy, logs
    decisions to Cloud Audit Logs, but permits traffic.
-   `iamEnforcementMode: "ENFORCED"`: Actively blocks unapproved traffic with
    HTTP 403 / gRPC `PERMISSION_DENIED`.

### 7.2 Cloud Audit Log Schema (UAP)

UAP emits structured audit logs under `audited_resource` type:

```sql
resource.type="audited_resource"
resource.labels.service="iap.googleapis.com"
protoPayload.metadata.iapPolicyVersion="v2"
```

Example audit log payload:

```json
{
  "protoPayload": {
    "@type": "type.googleapis.com/google.cloud.audit.AuditLog",
    "serviceName": "iap.googleapis.com",
    "methodName": "CheckPolicy",
    "metadata": {
      "iapPolicyVersion": "v2",
      "iamEnforcementMode": "DRY_RUN",
      "policyEvaluationResults": [
        {
          "policy": "organizations/112233445566/locations/global/accessPolicies/org-security-guardrails",
          "evaluationResult": "ALLOWED"
        },
        {
          "policy": "folders/123456789012/locations/global/accessPolicies/folder-platform-defaults",
          "evaluationResult": "ALLOWED"
        },
        {
          "policy": "projects/9876543210/locations/global/accessPolicies/support-bot-egress",
          "evaluationResult": "ALLOWED"
        }
      ],
      "finalDecision": "ALLOWED"
    }
  }
}
```

--------------------------------------------------------------------------------

## 8. Production Edge Cases & Architectural Gotchas

### 1. The Blanket Unregistered DENY Boot Crash

-   **Trap**: An Org Admin configures:

    ```cel
    (destination.is_registered == false) // effect: DENY
    ```
-   **Consequence**: All Reasoning Engine containers crash during startup.
    Reasoning Engines emit container boot telemetry to
    `telemetry.mtls.googleapis.com`, which is an unregistered public endpoint.
    Because DENY is absolute, the agent fails to initialize.
-   **Fix**: Rely on the Agent Gateway's built-in default-deny architecture.
    Never write a generic DENY on `destination.is_registered == false`.

### 2. Sibling Path Prefix Escapes vs. Query Strings

-   **Trap**: Restricting an external API with `.startsWith('/api/v2/tickets')`.
-   **Consequence**: An attacker or compromised agent can access
    `/api/v2/tickets_export` or `/api/v2/tickets_delete_all` because both
    strings start with that prefix.
-   **Fix**: Enforce strict boundary delimiters and query parameters:

    ```cel
    ((destination.unregistered.path == '/api/v2/tickets') ||
     (destination.unregistered.path == '/api/v2/tickets.json') ||
     (destination.unregistered.path.startsWith('/api/v2/tickets/')) ||
     (destination.unregistered.path.startsWith('/api/v2/tickets?')) ||
     (destination.unregistered.path.startsWith('/api/v2/tickets.json?')))
    ```

### 3. Confused Deputy via Suffix Matching

-   **Trap**: Matching an MCP server name with
    `endsWith('/mcpServers/jira-service')`.
-   **Consequence**: Any developer in any project within the organization who
    creates an MCP server named `jira-service` in their personal sandbox project
    inadvertently satisfies the policy.
-   **Fix**: Always anchor the resource name with the project number:

    ```cel
    destination.agent_registry.mcp_server.name.startsWith('projects/' + PROJECT_NUMBER + '/') &&
    destination.agent_registry.mcp_server.name.endsWith('/mcpServers/jira-service')
    ```

### 4. Intranet Subdomain Wildcard Masking

-   **Trap**: A Platform Admin allows `*.corp.example.com` (unregistered
    intranet) at the Folder tier, and creates an MCP server named
    `tools.corp.example.com`.
-   **Consequence**: If a developer forgets to catalog `tools.corp.example.com`
    in Agent Registry, Gatekeeper flags `destination.is_registered = false`. The
    request falls through to the unregistered intranet ALLOW rule, completely
    bypassing all tool-level semantic checks (`read_only_hint`) and granting
    ambient access!
-   **Fix**: Host corporate MCP servers on a distinct domain namespace outside
    the intranet wildcard, or ensure registration is verified before deployment.

### 5. IAM Policy Binding Destruction Race Condition

-   **Trap**: Deleting a `PolicyBinding` and its `AccessPolicy` in Terraform at
    the same time.
-   **Consequence**: IAM Policy Control Plane has a 10–30 second replication
    delay. Terraform issues `DELETE AccessPolicy` while the binding deletion is
    still replicating, returning `400 FAILED_PRECONDITION: Access policy is
    referenced by active policy binding(s)`.
-   **Fix**: Destroy provisioners must poll GET the binding URL until HTTP 404
    is confirmed before deleting the AccessPolicy.

### 6. Organization Policy Constraint Blocker

-   **Trap**: `gcloud iam policy-bindings create` fails with
    `CUSTOM_ORG_POLICY_VIOLATION`.
-   **Root Cause**: Many Google Cloud organizations enforce
    `constraints/iam.managed.disableAccessPolicyBinding` by default.
-   **Fix**: Apply an Org Policy V2 override setting `enforce: false` at the
    target CRM level:

    ```bash
    gcloud org-policies set-policy --project=${PROJECT_ID} override.yaml
    # override.yaml:
    # name: projects/${PROJECT_ID}/policies/iam.managed.disableAccessPolicyBinding
    # spec:
    #   rules:
    #     - enforce: false
    ```

--------------------------------------------------------------------------------

## 9. Quick Command Cheat Sheet

### UAP (Policy V2) Commands

```bash
# List policy bindings on a project
gcloud iam policy-bindings list   --target-resource="//cloudresourcemanager.googleapis.com/projects/${PROJECT_NUMBER}"   --location=global

# Describe an Access Policy
gcloud iam access-policies describe ${POLICY_ID}   --location=global

# Tail UAP Audit Logs in Cloud Logging
gcloud logging read   'resource.type="audited_resource" AND resource.labels.service="iap.googleapis.com" AND protoPayload.metadata.iapPolicyVersion="v2"'   --project=${PROJECT_ID}   --limit=20   --order=desc
```

### Legacy IAM v1 Commands

```bash
# Get IAM policy on Agent Registry
gcloud iap web get-iam-policy   --project=${PROJECT_ID}   --resource-type=agent-registry   --region=${LOCATION}

# Tail Legacy IAP Audit Logs
gcloud logging read   'protoPayload.serviceName="iap.googleapis.com"'   --project=${PROJECT_ID}   --limit=20   --order=desc
```

--------------------------------------------------------------------------------

## 10. Debugging Flowchart

````
                          ┌─────────────────────────────┐
                          │ Agent request denied (403)  │
                          └──────────────┬──────────────┘
                                         │
                                         ▼
                          ┌─────────────────────────────┐
                          │ Check IAP Audit Logs:       │
                          │ iapPolicyVersion == "v2"?   │
                          └───────┬─────────────┬───────┘
                              yes │             │ no (legacy v1)
                                  │             ▼
                                  │      ┌─────────────────────────────┐
                                  │      │ Check roles/iap.egressor    │
                                  │      │ on Agent Registry entry     │
                                  │      └─────────────────────────────┘
                                  ▼
         ┌─────────────────────────────────────────────────────────────┐
         │ Check policyEvaluationResults across Org, Folder, Project   │
         └──────────────────────────────┬──────────────────────────────┘
                                        │
                         ┌──────────────┴──────────────┐
                         ▼                             ▼
               Any rule evaluated to DENY?    No rules matched ALLOW?
                         │                             │
                         ▼                             ▼
             Identify denying rule         Verify destination.is_registered
             (Check for blanket DENY       and check CEL conditions
              or denylisted domain)        (Check null guard on .tool.*)
```
