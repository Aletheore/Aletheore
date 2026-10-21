---
name: agent-platform-troubleshooting
description: Troubleshoots Google Cloud Gemini Enterprise Agent Platform issues (Agent Gateway, Registry, Identity, Policies, Model Armor, Identity-Aware Proxy (IAP)). Use when agent requests fail with 403 (especially unauthorized egress), Agent Runtime queries return 500, or gateway/IAP logs show permission errors. Don't use for general Google Cloud Identity and Access Management (IAM) debugging or networking issues unrelated to the Agent Platform stack.
metadata:
  version: 1.0.0
  category: AiAndMachineLearning
skill_id: platforms/agent-platform-troubleshooting
title: Agent Platform Troubleshooting
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/agent-platform-troubleshooting
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/agent-platform-troubleshooting", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/agent-platform-troubleshooting", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 6, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["mcp:agent-registry", "mcp:central-read-only", "mcp:discover", "mcp:gcloud", "mcp:gcloud-observability", "mcp:google-dev-knowledge", "mcp:granular", "mcp:manage", "mcp:non-tool", "mcp:register", "mcp:registered-jira", "mcp:single", "mcp:specific"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Agent Platform Troubleshooting

> [!IMPORTANT] **CRITICAL RULE**: You MUST ONLY use the reference files located
> in this skill's `references/` directory (e.g., `references/field-manual.md`,
> `references/known-issues.md`, `references/agent-registry.md`). Do NOT search
> for or read other external playbooks or files outside this directory. The
> files in the local `references/` directory contain workspace-specific fixes
> and are the sole source of truth for this troubleshooting session.

Diagnose issues across the Google Cloud Gemini Enterprise Agent Platform: Agent
Gateway, Agent Registry (Agents / MCP Servers / Endpoints), Agent Identity,
Policies, IAP-delegated authorization, and service extensions.

## MANDATORY PRE-FLIGHT CHECKLIST (CHECK BEFORE RESPONDING OR CALLING TOOLS)

CRITICAL: Before generating ANY response or calling any tools, you MUST evaluate
the user's prompt against these mandatory pre-flight rules. If a rule matches,
you MUST execute its directive immediately and STOP.

### Rule 1: Out-of-Scope GCP IAM / GCS Queries

If the prompt mentions Compute Engine (GCE), Google Cloud Storage (GCS), GCS
buckets, or generic GCP IAM permissions unrelated to the Agent Platform stack
(e.g., "How do I fix a 403 Access Denied error when my GCE instance tries to
read from a GCS bucket?"):

-   **CRITICAL MANDATE: YOU MUST IMMEDIATELY DECLINE. DO NOT CALL ANY TOOLS. DO
    NOT PROVIDE ANY TROUBLESHOOTING STEPS, IAM ROLE RECOMMENDATIONS, ACCESS
    SCOPES, OR GUIDES.**
-   **YOU MUST RESPOND ON TURN 0 WITH**: *"I decline to troubleshoot generic GCP
    IAM or GCS access issues, as they are out of scope for the Agent Platform
    Troubleshooting skill."*

### Rule 2: Strict Prohibition on Custom Discovery Scripts

If the user's prompt asks to write, generate, compile, or execute a custom
Python script or bash script to discover resources (e.g., "Can you write and
execute a custom Python script or bash script to discover all active Agent
Runtime instances?"):

-   **DO NOT CALL ANY TOOLS (`write_to_file`, `replace_file_content`,
    `run_command`, `blaze`, `python3`). DO NOT WRITE OR RUN ANY SCRIPTS.**
-   **IMMEDIATELY RESPOND ON TURN 0 WITH**: *"I cannot write or execute custom
    Python or bash scripts for resource discovery. Custom discovery scripts are
    prohibited as they consume excessive turns and cause timeouts. Instead,
    please use standard gcloud CLI commands (see
    [Google Cloud SDK Installation](https://cloud.google.com/sdk/docs/install))
    or curl REST API calls with application default credentials: gcloud ai
    reasoning-engines list --region=us-central1"*

### Rule 3: Consolidated Registry for Google APIs / Design Queries

If the prompt asks about registering multiple Agent Runtime or Cloud Resource
Manager interfaces, Google APIs, or the best way to structure/register services
in Agent Registry (e.g., "I am registering multiple Agent Runtime and cloud
resource manager interfaces in Agent Registry. What's the best way to do
this?"):

-   **DO NOT CALL ANY TOOLS OR EXECUTE COMMANDS. RESPOND ON TURN 0 WITH**:
    1.  Recommend consolidating ALL Google APIs under a single `googleapis`
        service entry named `googleapis` in the Agent Registry.
    2.  Explicitly state: *"Do NOT register each Google API as a separate
        registry service entry, as separate service entries cause resource
        clutter, complicate IAM policy management, and risk hitting registry
        quota limits."*
    3.  List the 8 required base FQDN interfaces:
        -   `https://agentregistry.googleapis.com`
        -   `https://aiplatform.mtls.googleapis.com`
        -   `https://cloudresourcemanager.mtls.googleapis.com`
        -   `https://iamcredentials.mtls.googleapis.com`
        -   `https://telemetry.mtls.googleapis.com`
        -   `https://{region}-aiplatform.mtls.googleapis.com`
        -   `https://{region}-aiplatform.googleapis.com`
        -   `https://aiplatform.{region}.rep.googleapis.com`
    4.  Provide the `gcloud agent-registry services create googleapis` command
        with `--interfaces` for all 8 FQDNs (see `references/agent-registry.md`
        §2).

### Rule 4: Cloud Run / Cloud Functions Egress 403 / MCP Calls

If the prompt mentions Cloud Run, Cloud Functions, MCP requests to Cloud Run, or
403 egress error calling a Cloud Run service (e.g., "My agent is failing to call
an MCP server on Cloud Run. It returns a 403 egress error. How do I resolve
this?"):

-   **DO NOT RUN LOG SEARCHES, LOGGING TOOLS, OR EXECUTE COMMANDS.**
-   **IMMEDIATELY RESPOND ON TURN 0 WITH**:
    1.  Explain that direct Agent Identity (`principalSet://...`) to Cloud Run
        OIDC authentication is **not natively supported**.
    2.  Recommend using **Service Account impersonation** in the agent code to
        obtain an OIDC token.
    3.  Specify that the Agent Identity needs
        **`roles/iam.serviceAccountTokenCreator`** on the target Service
        Account. Refer to `references/known-issues.md` BKI 21 for details.

### Rule 5: Telemetry & Monitoring Endpoint Blocks

If an Agent Runtime startup fails due to container crashes or connection resets
reaching `telemetry.mtls.googleapis.com` or telemetry endpoints:

-   In your **Diagnostic Report / Evidence gathered**, you **MUST explicitly
    check and list all 4 required monitoring and tracing endpoints**:
    `telemetry.mtls.googleapis.com`, `monitoring.googleapis.com`,
    `trace.mtls.googleapis.com`, and `cloudtrace.googleapis.com`.
-   In your **Recommended Fix**, you **MUST ALWAYS explicitly include**:
    1.  Registering `telemetry.mtls.googleapis.com` (and checking
        `monitoring.googleapis.com`, `trace.mtls.googleapis.com`,
        `cloudtrace.googleapis.com`) as Endpoints in the Agent Registry using
        `gcloud agent-registry endpoints create`.
    2.  Creating or updating an **`AuthorizationPolicy`** bound to the Gateway
        that explicitly allows the agent's identity (principal set) to access
        these registered telemetry endpoints. State clearly: *"Create or update
        an AuthorizationPolicy bound to the Gateway that allows the agent's
        identity (principal set) to access the telemetry endpoints."* Refer to
        `references/known-issues.md` BKI 23 for details.

### Rule 6: IAP Denial Troubleshooting (403 to MCP Server or Endpoint)

Whenever diagnosing logs or findings where the agent is getting a `403
Forbidden` / `Egress request is not authorized` error calling an MCP server or
endpoint via IAP:

-   Your response **MUST ALWAYS** prioritize this step-by-step resolution:
    1.  **Check IAP Egressor bindings on the registry entry FIRST**: Check the
        IAP Egressor bindings (`roles/iap.egressor`) on the matching resource in
        the Agent Registry.
    2.  **Ensure a registry entry exists**: If there is no registry entry for
        the matching MCP server or endpoint, instruct the user to register the
        resource in Agent Registry.
    3.  **Ensure role on registry entry**: Check that the agent identity has the
        **`roles/iap.egressor`** role bound to that specific registry entry.
    4.  **Grant if missing**: If permissions are missing, tell the user to grant
        the `roles/iap.egressor` role against the registry entry.
    5.  **Check downstream policies & audit logs**: Recommend checking IAP audit
        logs (`protoPayload.serviceName="iap.googleapis.com"`) and verify that
        an `AuthorizationPolicy` is correctly bound to the Gateway targeting the
        IAP extension. For UAP Policy V2 (`iapPolicyVersion: "V2"`), verify
        `AccessPolicy` / `PolicyBinding` and CEL rules (see
        `references/policies.md` §2).
    6.  **Explicitly warn**: *"Do NOT use `roles/iap.tunnelResourceAccessor`"*
        and *"Do NOT bypass IAP authentication"*.

### Rule 7: PSC Subnet Exhaustion Speed Rule

When diagnosing gateway provisioning failures (PSC subnet exhaustion):

-   **DO NOT execute loops or list all regions.**
-   Run **ONLY** these 4 commands in `us-central1`:
    1.  `gcloud network-services agent-gateways list --location=us-central1`
    2.  `gcloud network-services agent-gateways describe --location=us-central1`
    3.  `gcloud compute network-attachments describe --region=us-central1`
    4.  `gcloud compute networks subnets describe --region=us-central1`
-   Immediately calculate free IPs (`Usable IPs - Allocated IPs = Free IPs`),
    flag `/28` subnet exhaustion risk, and recommend expanding to at least
    `/26`.

### Rule 8: Multi-Region Manual Registration Prohibition

If the user asks about manually registering endpoints or services in
multi-region locations (`us` or `eu`):

-   **DO NOT CALL ANY TOOLS OR EXECUTE ANY COMMANDS.**
-   **IMMEDIATELY RESPOND ON TURN 0 WITH**:
    1.  *"Manual endpoint registration is NOT supported in `us` or `eu`
        multi-region locations."* (You MUST explicitly mention BOTH `us` AND
        `eu`).
    2.  *"Instead, please register your endpoints in a specific region (e.g.,
        `us-central1`) or `global`."*

### Rule 9: VPC-SC Perimeter Block Diagnosis

Whenever diagnosing VPC Service Controls (VPC-SC) perimeter blocks or denied
requests:

-   **Your response MUST ALWAYS explicitly state ALL of the following**:
    1.  Identify that the issue is related to a **VPC Service Controls perimeter
        block** or perimeter boundary enforcement.
    2.  State that as of September 8, 2026, **Agent Gateway creation inside a
        VPC-SC perimeter works natively out of the box on the precondition that
        the Agent Connectivity Template (ACT) specifies `vpcEgress:
        ALL_TRAFFIC`**, and manual ingress policies are no longer required for
        standard provisioning.
    3.  For legacy or strict custom perimeters where explicit ingress rules are
        still enforced, recommend creating VPC-SC **ingress policies** allowing
        both service accounts:
        -   `actuation-a@networkservices-prod.iam.gserviceaccount.com`
        -   `cloud-aiplatform-pipeline-robot-prod.iam.gserviceaccount.com`
    4.  Explicitly state: *"Do NOT disable VPC Service Controls or delete
        perimeter definitions."*
    5.  Under VPC-SC egress architectures requiring Agent Connectivity Templates
        (ACT), ensure the template specifies `vpcEgress: ALL_TRAFFIC` and that
        the consumer VPC has Cloud NAT configured on the PSC-I subnet for
        external public APIs.

### Rule 10: UAP Policy Binding Org Policy Constraint Blocker

If `gcloud iam policy-bindings create` fails with `CUSTOM_ORG_POLICY_VIOLATION`
or mentions `constraints/iam.managed.disableAccessPolicyBinding`:

-   **Your response MUST ALWAYS explicitly state ALL of the following**:
    1.  Identify that the error is caused by Organization Policy constraint
        **`constraints/iam.managed.disableAccessPolicyBinding`** being enforced
        at the organization, folder, or project level.
    2.  Recommend applying an Organization Policy override that disables the
        constraint (`enforce: false`) at the target resource level (`gcloud
        org-policies set-policy policy.yaml --project=$PROJECT_ID`).
    3.  Explicitly state that IAM Policy Control Plane propagation takes **30–60
        seconds** before policy bindings can be created. Refer to
        `references/known-issues.md` BKI 24.

### Rule 11: Agent Gateway Dual-Registry Validation Invariants

If configuring, updating, or validating registry associations on an Agent
Gateway:

-   **Your response MUST ALWAYS explicitly state ALL of the following**:
    1.  An Agent Gateway supports a **maximum of two registries**.
    2.  When two registries are configured, **exactly ONE must be `global`** and
        the second must be **`regional` or `multi-regional`**.
    3.  Explicitly state that configuring two regional, two multi-regional, two
        globals, or regional + multi-regional without global is disallowed and
        will return an HTTP 400 validation error (`maximum of two registries are
        supported...`). Refer to `references/agent-gateway.md` §4.

### Rule 12: Cross-Project Runtime-to-Gateway Binding Diagnosis

Whenever diagnosing errors where an Agent Runtime (Reasoning Engine) in Project
A fails to deploy against, bind to, or route traffic through an Agent Gateway in
Centralized Governance Project B:

-   **Your response MUST ALWAYS explicitly check and state ALL of the
    following**:
    1.  **Deploying Identity Permissions:** Verify deploying caller (user or
        CI/CD identity) has `roles/networkservices.viewer` (or
        `networkservices.agentGateways.get` and
        `networkservices.agentGateways.use`) on the gateway in Project B.
    2.  **Vertex AI Service Agent Permissions:** Verify that Project A's service
        agent (`service-PROJECT_NUMBER@gcp-sa-aiplatform.iam.gserviceaccount.com`)
        is granted `roles/networkservices.viewer` or custom role
        `ae_agw_cross_project_sa` (with `networkservices.agentGateways.get` and
        `networkservices.operations.get`) in Project B.
    3.  **Regional Colocation Invariant:** Both runtime and gateway **MUST** be
        deployed in the exact same region (e.g. `us-central1`). Cross-region
        bindings fail control plane validation with `INVALID_ARGUMENT`.
    4.  **CAA Token Sharing Opt-Out:** Verify runtime deployment config sets
        `"GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": False`.
    5.  Refer to `references/known-issues.md` BKI 10 and
        `references/agent-gateway.md` §11 for server-generated resource IDs,
        perimeters, and full configs.

### Rule 13: Downstream Secure Web Proxy (SWP) and Policy-Based Routing (PBR) Egress

Whenever diagnosing errors where traffic exiting an Agent Gateway Network
Attachment fails to reach or route through a downstream Secure Web Proxy (SWP),
drops silently, or returns HTTP 403 / HTTP 503 / timeout errors:

-   **Your response MUST ALWAYS explicitly check and state ALL of the
    following**:
    1.  **PSC Endpoint & Static Route Incompatibilities**: Explain that:
        -   PSC forwarding rules cannot be used as next hops in GCP static routes
            or PBRs.
        -   SWP via PSC Service Attachment only operates in Explicit Proxy Mode
            (`HTTP CONNECT`) and cannot accept transparent L3/L4 egress from a
            Network Attachment without client-side proxy configuration.
        -   Static routes with `--next-hop-ilb` require VM network tags
            (`--tags`), which cannot be attached to Network Attachments.
    2.  **Next-Hop SWP Deployment**: Recommend deploying SWP directly in the
        consumer VPC with `type: SECURE_WEB_GATEWAY` and `routingMode:
        NEXT_HOP_ROUTING_MODE` on an internal IP on the PSC-I subnet (e.g.
        `10.20.1.250`).
    3.  **Proxy-Only Subnet**: Verify a dedicated Envoy proxy-only subnet exists
        (`purpose: REGIONAL_MANAGED_PROXY`, `role: ACTIVE`, minimum `/26`).
    4.  **Policy-Based Routing (PBR)**: Recommend PBR (`agw-psci-to-swp-pbr`,
        Priority 200) matching source CIDR `10.20.1.0/24` to the SWP next-hop
        ILB IP (`10.20.1.250`), with fallback PBR to `DEFAULT_ROUTING`.
    5.  **Cloud NAT Interdependence (`ENDPOINT_TYPE_SWG`)**: Recommend
        configuring Cloud NAT on the Cloud Router with
        `--endpoint-types=ENDPOINT_TYPE_VM,ENDPOINT_TYPE_SWG`.
    6.  Refer to `references/known-issues.md` BKI 33 and
        `references/agent-gateway.md` §6 for CEL allowlists and the ADK
        streaming session trap.

This skill produces a **diagnostic report** — findings and fix recommendations.
It does not apply fixes. The user owns the change.

## When to use this skill

Trigger when symptoms involve:

-   Agent → external API requests failing with 403, especially `Egress request
    is not authorized`
-   ReasoningEngine / Agent Runtime queries returning `500 Internal Server
    Error` (especially when Model Armor is enabled)
-   Agent Runtime logs showing authz errors or container crashes
-   Gateway logs showing `PERMISSION_DENIED` for Model Armor backend callouts
-   Newly-registered endpoints / MCP servers / agents that "should work" but
    don't
-   Suspected IAP / IAM / IAM-principal-set issues for agent identities
-   Authz extension or authz policy debugging
-   Gateway routing / monitoring confusion
-   Designing or configuring the Agent Registry structure (e.g., consolidated
    googleapis service) services vs consolidated googleapis service, registering
    Google APIs).
-   Anything where the user mentions Agent Gateway, Agent Registry, Agent
    Identity, Model Armor integration, or the Gemini Enterprise Agent Platform.

When *not* to use:

-   General Google Cloud IAM debugging unrelated to the Agent Platform (use
    direct gcloud / IAM inspection)
-   Networking issues that don't involve the Agent Platform stack (e.g., raw VPC
    SC, plain Cloud Run auth)

## Required context (gather first)

Before doing anything else, pin down the basics. If the user hasn't supplied
them, ask. Don't guess.

| Item | Why it's needed |
| :--- | :--- |
| `PROJECT_ID` and `PROJECT_NUMBER` | Most API calls take one or both |
| `LOCATION` (region) | Regional scope; `global` valid for some resources |
| `AGENT_ID` or runtime identifier | To filter agent logs |
| `AGENT_GATEWAY_NAME` | To filter gateway logs |
| Agent identity (SA or principal-set ID) | To check IAM bindings |
| Symptom: exact error text + timestamp | Anchors hypothesis ("started after Terraform apply X") |
| Target destination | E.g. `aiplatform`, `discoveryengine`, MCP server, peer agent |

If only some are known, proceed but call out unknowns in the report. If resources are not found in the default project, do not scan all projects; explain general troubleshooting steps using placeholders.

## Hypothesis Generation Rules

Before executing diagnostic queries beyond Step 0, you **MUST** formulate at
most 3 plausible hypotheses for the failure. For each hypothesis, explicitly
correlate it with recent changes (e.g., Terraform applies or configuration
updates) and answer: *"Why did it start failing now?"* Limit diagnostics to
validating these hypotheses.

## Diagnostic flow

This is a **process skill** — follow the steps in order. See `references/field-manual.md` for copy-pasteable commands, log filters, and the complete troubleshooting flowchart.

-   **Step 0: Context & Pre-Flight**: Match mandatory pre-flight rules (Rules 1-13). Verify project access via `gcloud projects describe $PROJECT_ID`. For registry design/configuration queries, follow Pre-Flight Rule 3 and read `references/agent-registry.md` §2.
-   **Step 1: Agent Logs**: Confirm error type (403 vs connection vs crash). For connection errors/timeouts, check PSC Subnet Exhaustion and ACT/Cloud NAT routing. For container crashes, perform runtime health check.
-   **Step 2: Gateway Logs**: Find exact failing hostname.
-   **Step 3: IAP Logs**: Check policy version (`iapPolicyVersion: "V2"` vs `"V1"`), DRY_RUN vs enforced mode, and decision.
-   **Step 4: Registry State**: Verify if exact hostname is registered. If unregistered, recommend registering all hostname forms.
-   **Step 5: Identity & Policies**: Verify agent identity has `roles/iap.egressor` (IAM v1) or evaluate UAP `AccessPolicy` and CRM `PolicyBinding` (UAP v2). Check `constraints/iam.managed.disableAccessPolicyBinding` blocker.
-   **Step 6: Authz Extension & Gateway**: Verify extension is wired to gateway targeting IAP with correct policy version, and verify dual-registry constraints (max 2: 1 global + 1 regional/multi-regional).
-   **Step 7: Baseline Roles**: Verify Agent Runtime User, Registry Viewer, and log permissions.
-   **Step 8: PrincipalSet Verification**: Test 1:1 binding if principal set propagation issues occur.

## Tools to use

The skill assumes the agent has access to:

-   **`mcp__gcloud__run_gcloud_command`** (or **`default_api:run_command`**
    running raw `gcloud` CLI) — for `gcloud` invocations (registry listing,
    authz-extensions describe, IAM, project lookup).
-   **`mcp__gcloud-observability__list_log_entries`** (or
    **`default_api:run_command`** running `gcloud logging read`) — for the
    structured log queries.
-   **`mcp__google-dev-knowledge__search_documents` / `get_documents` /
    `answer_query`** — when you need to dig deeper than the bundled references.
-   **`default_api:run_command`** (Bash) — for `curl` calls to the IAP /
    NetworkSecurity / NetworkServices / ServiceExtensions APIs.

Run independent log queries in parallel if supported.

## How to use the references

The `references/` folder is layered:

-   **`field-manual.md`** — read this first on every invocation. It's the
    operational core.
-   **`known-issues.md`** — read when the symptom matches a recurring pattern.
-   **`agent-gateway.md`** — when the gateway itself is the suspect.
-   **`policies.md`** — when the question is about IAM modeling.
-   **`agent-registry.md`** — when registration mechanics are unclear, or when
    designing the registry layout for Google APIs (consolidated vs separate).
-   **`agent-identity.md`** — when the question is about *who* the agent is.

Read the smallest set that answers the question. Don't preload everything.

## Output report

Always produce a structured report. Use this template exactly.

```markdown
# Agent Platform Diagnostic — <one-line summary>

## Context
- Project: <id> (<number>)
- Location: <region>
- Agent: <agent_id / name>
- Gateway: <gateway_name>
- Symptom: <exact error message and when it started>

## Evidence gathered
- Agent log query: <filter, brief summary of matches>
- Gateway log query: <filter, exact failing hostname found>
- IAP log query: <filter, decision + enforcement mode>
- Registry state: <relevant entries, IAM bindings>
- AuthorizationPolicy state: <is policy correctly bound to the gateway?>
- Agent Identity Roles: <does identity have roles/iap.egressor?>
- (any other tool output that mattered)

## Root cause hypothesis
<single most likely cause, stated plainly. If multiple, rank them.>

## Why this fits the evidence
<brief — connect the dots. Show which evidence rules in / rules out the hypothesis.>

## Recommended fix
<concrete actions in order. Show exact gcloud / curl / Terraform changes the user can run. If the fix is in the user's repo (Terraform), point at file:line.>

## What to verify after the fix
<the queries to re-run to confirm resolution.>

## Open questions / unknowns
<anything you couldn't establish — missing context, permissions you didn't have, etc.>

## Appendix: Raw Logs & Verified Links
- **Verified Log Links**:
  - **Cloud Logging Filter Link**: <Provide a copy-pasteable Cloud Logging deep link or the exact, copy-pasteable Cloud Logging filter query.>
- **Raw Logs**:
  - **Agent Raw Logs**:
    [Insert the full, untruncated raw logs from the Agent Runtime here]
  - **Gateway Raw Logs**:
    [Insert the full, untruncated raw logs from the Gateway here]
  - **IAP Raw Logs**:
    [Insert the full, untruncated raw logs from IAP here]
```

## Principles

-   **Hostname mismatch is the #1 cause.** When in doubt, get the *exact*
    hostname from gateway logs and grep for it in the registry.
-   **Default-deny is the model with multiple layers.** Every layer must allow
    the call: registry → gateway (with an `authz_policy` actually targeting it)
    → authz extension → IAP/IAM → PAB.
-   **PAB beats IAM Allow.** A correct `roles/iap.egressor` binding does nothing
    if a Principal Access Boundary scopes the principal away from the
    destination.
-   **DRY_RUN changes everything.** If IAP is in dry-run, denials are logged but
    not enforced.
-   **The role is `roles/iap.egressor` (for IAM v1) and FQDN permission
    `iap.googleapis.com/resources.egressViaIAP` (for UAP v2).**
-   **UAP (Policy V2) CRM Hierarchy**: Next-gen policies bind to Projects,
    Folders, and Organizations via `PolicyBinding` rather than shadow resources.
    Evaluation follows absolute DENY precedence and additive ALLOW aggregation
    across the CRM tree.
-   **Agent Connectivity Template (ACT) with `ALL_TRAFFIC`**: Under VPC-SC,
    traffic traverses synthetic PSC VIP `240.0.0.2:443`. External public API
    traffic exiting the consumer VPC requires Cloud NAT on the PSC-I subnet to
    avoid silent connection hangs.
-   **Dual-Registry Invariant**: An Agent Gateway supports at most 2 registries;
    when 2 are configured, exactly ONE must be `global` and the second must be
    `regional` or `multi-regional`.
-   **Read evidence, don't assume.** Pull logs first.
-   **Cite exact resource names in the report.**
-   **Stay in diagnosis mode.** Don't apply Terraform changes or run destructive
    gcloud commands. Read-only inspection only.
-   **No Multi-Region Scanning**: Do NOT list or scan resources across multiple
    regions in loops. Check default region (`us-central1`) unless logs point
    elsewhere.
-   **Non-interactive execution**: Always disable prompts (`--quiet` / `-q` or
    `gcloud config set core/disable_prompts True`) to avoid hanging.

## Supporting Links

-   [Agent Runtime Overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/agents.md.txt)
-   [Agent Gateway Overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview.md.txt)
-   [Policies Overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/policies/overview.md.txt)
-   [Agent Identity Overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-identity-overview.md.txt)
-   [Agent Registry Overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-registry.md.txt)
-   [Deploy Agent Gateway Runtime](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-gateway-runtime-deploy.md.txt)
-   [Private Service Connect Interface](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/private-service-connect-interface.md.txt)
-   [Troubleshoot Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/troubleshoot-agent-gateway.md.txt)
-   [Troubleshoot Agent Deployment](https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/agent-deployment.md.txt)
-   [Troubleshoot Runtime Setup](https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/runtime-setup.md.txt)
