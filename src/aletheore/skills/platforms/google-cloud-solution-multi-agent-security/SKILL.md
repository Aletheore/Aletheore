---
name: google-cloud-solution-multi-agent-security
metadata:
  version: 1.0.0
  category: MultiProductSolutions
description: Designs, deploys, and secures Google Cloud Agent Gateway solutions. Use when the user needs to configure multi-agent security, ingress (CLIENT_TO_AGENT), or egress (AGENT_TO_ANYWHERE) patterns involving Model Armor, IAP, and Agent Registry. Don't use for general Cloud Load Balancing or basic VPC setup not related to Agent Gateways.
skill_id: platforms/google-cloud-solution-multi-agent-security
title: Google Cloud Solution Multi Agent Security
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-solution-multi-agent-security
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/google-cloud-solution-multi-agent-security", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-solution-multi-agent-security", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": ["ignore-instructions"], "portability_flags": [], "companion_files": 22, "has_scripts": true}, "curated_on": "2026-10-18", "requires": ["mcp:private", "scripts"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Agent Gateway multi-agent security

## Critical Enforcement Rules & Rationale

*   **Gcloud Release Tracks**: Always use the exact release tracks specified in
    the commands (e.g., `gcloud beta network-services agent-gateways`). Omitting
    these prefixes causes commands to fail because Agent Gateway features are
    located in specialized, non-default namespaces.
*   **API Enablement**: Include `modelarmor.googleapis.com` in the API
    enablement list when setting up guardrails. Excluding it prevents Model
    Armor policies and filters from successfully attaching to the Gateway.
*   **Egress Verification**: Egress policy verification requires using the
    Python script
    ([scripts/verify_egress_policies.py](scripts/verify_egress_policies.py)),
    not `curl`. Egress gateways rely on runtime SDK lifecycle handling and JWT
    context that a standard curl command cannot simulate correctly.
*   **Model Armor Keys**: In `model-armor-config.yaml`, always include both
    `piAndJailbreakFilterSettings` and `sdpFilterSettings` (`filterEnforcement:
    ENFORCE`). Invalid or missing filters cause deployment validation failures
    or lead to silent bypasses of the guardrails.
*   **Subnet Private Access**: Any subnet hosting a Private Service Connect
    network attachment for Egress Gateways must have `private_ip_google_access =
    true` enabled in Terraform. Disabling this blocks connectivity to
    Google-managed endpoints, causing total routing failures for agents.
*   **Direct Delivery**: Immediately provide the requested architecture,
    configuration files, CLI commands, scripts, and diagrams in full. Do not
    stop at a planning phase, do not generate a plan artifact, and do not ask
    for user confirmation before delivering outputs.
*   **No Infrastructure Execution**: Do not attempt to run deployment or
    verification commands (such as `gcloud`, `kubectl`, `terraform`, or `curl`)
    against real cloud resources during design. You are generating plan
    configurations, not executing them.

> [!IMPORTANT] **Just-In-Time (JIT) Resource Loading Protocol:** Inspect
> template files in [assets/](assets/) and executable scripts in
> [scripts/](scripts/) using `view_file` as needed for extended configurations,
> deployment scripts, and test suites.

--------------------------------------------------------------------------------

## Quick Reference: Required Filenames

Always generate files with these exact names when requested:

1.  `agw-ingress-config.yaml`
    ([assets/agw-ingress-config.yaml](assets/agw-ingress-config.yaml))
2.  `agw-egress-config.yaml`
    ([assets/agw-egress-config.yaml](assets/agw-egress-config.yaml))
3.  `agw-authz-extension.yaml`
    ([assets/agw-authz-extension.yaml](assets/agw-authz-extension.yaml))
4.  `agw-authz-policy.yaml`
    ([assets/agw-authz-policy.yaml](assets/agw-authz-policy.yaml))
5.  `model-armor-config.yaml`
    ([assets/model-armor-config.yaml](assets/model-armor-config.yaml))
6.  `sgp-policy.yaml` ([assets/sgp-policy.yaml](assets/sgp-policy.yaml))
7.  `iap-policy.json` ([assets/iap-policy.json](assets/iap-policy.json))
8.  `model-armor-payload.json`
    ([assets/model-armor-payload.json](assets/model-armor-payload.json))

--------------------------------------------------------------------------------

## 1. Dual Ingress & Egress Architecture Design (`dual_ingress_egress_architecture_design`)

-   **Ingress Pattern**: `CLIENT_TO_AGENT` fronted by Ingress Control Plane
    (Agent Gateway, Model Armor).
-   **Egress Pattern**: `AGENT_TO_ANYWHERE` utilizing Egress Control Plane
    (Agent Gateway, `roles/iap.egressor` CEL policies, Cloud DNS) and Egress
    Data Plane (PSC Interface, Cloud Run, PSC Google APIs Global Endpoint),
    coordinated via Agent Registry & Agent Engine runtime.
-   **Mermaid Diagram**:

    ```mermaid
    graph TD
        Client["External Clients"] -->|HTTPS / MCP| GLB["Global Load Balancer"]
        GLB --> Ingress["Ingress Agent Gateway (CLIENT_TO_AGENT)"]
        Ingress --> MA["Model Armor (CONTENT_AUTHZ)"]
        MA --> Agent["Agent Engine Agents (BillingAgent, SupportAgent, FraudAgent)"]
        Agent --> Egress["Egress Agent Gateway (AGENT_TO_ANYWHERE)"]
        Egress --> PSC["Private Service Connect Network Attachment"]
        PSC --> Tools["Private MCP Tool Backends"]
    ```

--------------------------------------------------------------------------------

## 2. Ingress & Egress Guardrail Policy Config (`ingress_and_egress_guardrail_policy_config`)

When requested for Ingress & Egress guardrail policy configs, you MUST generate
and create all required files in the workspace:

-   `agw-ingress-config.yaml`
    ([assets/agw-ingress-config.yaml](assets/agw-ingress-config.yaml)): Declares
    `governedAccessPath: CLIENT_TO_AGENT` with protocols `HTTP` and `MCP`.
-   `agw-egress-config.yaml`
    ([assets/agw-egress-config.yaml](assets/agw-egress-config.yaml)): Declares
    `governedAccessPath: AGENT_TO_ANYWHERE` with protocol `MCP`.
-   `agw-authz-extension.yaml`
    ([assets/agw-authz-extension.yaml](assets/agw-authz-extension.yaml)):
    Configures AuthzExtension service for IAP authorization.
-   `agw-authz-policy.yaml`
    ([assets/agw-authz-policy.yaml](assets/agw-authz-policy.yaml)): Configures
    `AuthzPolicy` action `ALLOW` targeting both Ingress and Egress gateways.
-   `iap-policy.json` ([assets/iap-policy.json](assets/iap-policy.json)): Binds
    `roles/iap.egressor` with CEL condition checking
    `iap.googleapis.com/mcp.toolName == 'get_account_balance' &&
    iap.googleapis.com/mcp.tool.isReadOnly == true`.
-   `model-armor-config.yaml`
    ([assets/model-armor-config.yaml](assets/model-armor-config.yaml)): Enables
    `piAndJailbreakFilterSettings` and `sdpFilterSettings` with
    `filterEnforcement: ENFORCE`.
-   `sgp-policy.yaml` ([assets/sgp-policy.yaml](assets/sgp-policy.yaml)):
    Implements Natural Language Constraints blocking transactions > $1000 and
    sanitizing PII.

--------------------------------------------------------------------------------

## 3. Ingress & Egress Infrastructure Deployment (`ingress_and_egress_infrastructure_deployment`)

Inspect and provide the step-by-step `gcloud` CLI commands from
[scripts/deploy_infrastructure.sh](scripts/deploy_infrastructure.sh):

1.  **Enable Required APIs**: `compute`, `networkservices`, `networksecurity`,
    `modelarmor`, `iap`, `agentregistry`, `serviceextensions`, and `aiplatform`.
2.  **Import Agent Gateways**: Ingress (`agw-ingress-config.yaml`) and Egress
    (`agw-egress-config.yaml`) via `gcloud alpha network-services agent-gateways
    import`.
3.  **Import Authz Extension**: `agw-authz-extension.yaml` via `gcloud beta
    service-extensions authz-extensions import`.
4.  **Import Authz Policy**: `agw-authz-policy.yaml` via `gcloud beta
    network-security authz-policies import`.

--------------------------------------------------------------------------------

## 4. Ingress & Egress Security Validation (`ingress_and_egress_security_validation`)

When validating security for Ingress and Egress:

1.  **Ingress 403 Unauthenticated Test**: Provide the copy-pasteable
    verification curl command from
    [scripts/validate_ingress_unauth.sh](scripts/validate_ingress_unauth.sh)
    sending an unauthenticated POST request to the Reasoning Engine endpoint
    expecting HTTP 403 Forbidden.
2.  **Python Egress Verification Script (MUST use Python script snippet, NOT
    curl)**: Provide the Python verification script snippet from
    [scripts/verify_egress_policies.py](scripts/verify_egress_policies.py)
    sending JSON-RPC `tools/call` requests (`get_account_balance`) through the
    Egress Gateway to verify HTTP 200 for allowed tools.
3.  **Model Armor Test Payload**: Generate `model-armor-payload.json`
    ([assets/model-armor-payload.json](assets/model-armor-payload.json))
    containing prompt injection/jailbreak instructions.

--------------------------------------------------------------------------------

## 5. Troubleshooting Ingress & Egress Failures (`troubleshooting_ingress_and_egress_failures`)

-   **Ingress 403 (Client-to-Agent)**:

    -   **Root Cause**: Unauthenticated client requests or missing/invalid OAuth
        2.0 / IAP identity tokens.
    -   **OAuth Configuration Steps**:
        1.  Configure OAuth 2.0 Client ID credentials in Google Cloud Console.
        2.  Grant the client identity / service account
            `roles/iap.httpsResourceAccessor` permission.
        3.  Exchange credentials with Google OAuth to acquire an OIDC / OAuth ID
            token.
        4.  Pass the token in the `Authorization: Bearer <TOKEN>` header.
    -   **Verification Command**: Provide the curl command from
        [scripts/verify_ingress_auth.sh](scripts/verify_ingress_auth.sh).

-   **Egress 403 (Agent-to-Anywhere)**:

    -   **Root Cause**: Missing `roles/iap.egressor` IAM bindings on the Agent
        Identity, malformed principal ID, or mismatched CEL condition on tool
        metadata.
    -   **Fix Command**: Provide the exact `gcloud` command from
        [scripts/fix_egress_iap.sh](scripts/fix_egress_iap.sh).

--------------------------------------------------------------------------------

## 6. Hybrid VPN Connectivity & Egress Routing (`hybrid_vpn_connectivity_egress_routing`)

-   **Terraform HCL**: Refer to baseline Terraform config in
    [assets/main.tf](assets/main.tf) for VPC, subnets
    (`private_ip_google_access = true`), PSC network attachment, Cloud DNS
    private forwarding for `aws.internal.`, and HA VPN gateway/router.
-   **Egress Gateway Config (`agw-egress-config.yaml`)**: Generate configuration
    declaring `governedAccessPath: AGENT_TO_ANYWHERE`, pointing to the PSC
    network attachment, and referencing `aws.internal.` in `dnsPeeringConfig`
    (see [assets/agw-egress-config.yaml](assets/agw-egress-config.yaml)).
-   **Python SDK Deployment Script**: Refer to
    [scripts/hybrid_vpn_agent.py](scripts/hybrid_vpn_agent.py) for the complete
    script initializing Vertex AI with `agent_to_anywhere_config` referencing
    the Egress Gateway, enabling telemetry, and deploying `HybridAgent` using
    `types.IdentityType.AGENT_IDENTITY`.

--------------------------------------------------------------------------------

## 7. Private Egress GKE Internal Load Balancer (`private_egress_gke_internal_load_balancer`)

-   Expose GKE internal MCP tool server via an Internal Load Balancer (ILB) at
    literal IP `10.0.1.50`, connecting via Agent Gateway PSC Interface + Cloud
    DNS Private zone.
-   **Cloud DNS Record Mapping**: Provide the command from
    [scripts/create_gke_dns_record.sh](scripts/create_gke_dns_record.sh) mapping
    the private domain to GKE's private ILB IP `10.0.1.50`.
-   **Explicit TLS Warning**: Agent Gateway egress **does not natively trust
    self-signed certificates or private enterprise CAs**. You **must** use
    publicly trusted TLS certificates signed by a trusted Certificate Authority
    (e.g., Let's Encrypt).

--------------------------------------------------------------------------------

## 8. Governance Controls Model Armor SGP (`governance_controls_model_armor_sgp`)

When configuring dual safety layers with Model Armor on Ingress and SGP on
Egress:

1.  **Model Armor Config**: Generate `model-armor-config.yaml`
    ([assets/model-armor-config.yaml](assets/model-armor-config.yaml)) with
    `piAndJailbreakFilterSettings` and `sdpFilterSettings` (`filterEnforcement:
    ENFORCE`).
2.  **Semantic Governance Policy**: Generate `sgp-policy.yaml`
    ([assets/sgp-policy.yaml](assets/sgp-policy.yaml)) with Natural Language
    Constraints blocking transactions > $1000 and sanitizing PII.
3.  **Curl PATCH Command**: Provide the curl command from
    [scripts/enforce_sgp_patch.sh](scripts/enforce_sgp_patch.sh) to update
    `authzExtensions` with `sgpEnforcementMode` set to `ENFORCE`.

--------------------------------------------------------------------------------

## 9. Multi-Agent Cloud Run Egress Routing (`multi_agent_cloud_run_egress_routing`)

Do NOT produce a plan artifact or stop at planning. When configuring multi-agent
Cloud Run egress routing, you MUST directly provide and generate ALL required
components:

1.  **Egress Gateway Config (`agw-egress-config-run.yaml`)**: Generate
    configuration declaring `governedAccessPath: AGENT_TO_ANYWHERE`, PSC network
    attachment, and DNS peering for `*.run.app` (see
    [assets/agw-egress-config-run.yaml](assets/agw-egress-config-run.yaml)).
2.  **Register Cloud Run Services in Agent Registry**: Provide the registration
    commands from
    [scripts/register_cloud_run_services.sh](scripts/register_cloud_run_services.sh)
    registering all 3 Cloud Run services (`marketing-tool-service`,
    `sales-tool-service`, `support-tool-service`) in the `us-east4` Agent
    Registry.
3.  **`iap-policy.json` (Multi-Agent)**: Generate `iap-policy.json`
    ([assets/iap-policy-multi-agent.json](assets/iap-policy-multi-agent.json))
    containing all 3 `principal://` bindings in the `members` list under
    `roles/iap.egressor`.
4.  **Python SDK Deployment Script**: Refer to
    [scripts/multi_agent_cloud_run.py](scripts/multi_agent_cloud_run.py) for the
    complete GenAI SDK deployment script.

--------------------------------------------------------------------------------

## 10. Advanced Model Armor Filtering (`advanced_model_armor_filtering`)

For custom keyword matching, configure `userDefinedFilterSettings` (see
[assets/model-armor-advanced.yaml](assets/model-armor-advanced.yaml)).

--------------------------------------------------------------------------------

## 11. Known Traps & Gotchas (`known_traps_and_gotchas`)

*   **`network_attachment` is `ForceNew`**: Enabling Semantic Governance
    Policies (SGP) or modifying network attachments after the initial Terraform
    apply will force-recreate the gateway resource. If not managed carefully,
    this can cause dependency deadlocks during destroy operations. Plan
    infrastructure sequencing accordingly.
*   **Authz Policy Limit**: An Agent Gateway allows at most **4 custom
    authorization policies** attached concurrently. Ensure your security posture
    consolidates rules within this limit.
