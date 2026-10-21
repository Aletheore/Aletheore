# Field manual: Agent Gateway / Registry / Identity / IAP

Practical debugging knowledge for the Google Cloud Gemini Enterprise Agent
Platform, distilled from real incident triage. Read this *before* diving into
the official docs — it covers the gotchas that the docs don't surface.

> [!TIP] **Check Known Issues First**: If the symptom matches a known pattern
> (e.g., startup failure, VPC-SC block, Cloud Run egress 403), jump straight to
> `references/known-issues.md` — it indexes the recurring failure modes by
> symptom.

## Table of Contents

-   [The mental model](#the-mental-model) (Lines 20-88)
-   [The single biggest gotcha: hostname permutations](#the-single-biggest-gotcha-hostname-permutations)
    (Lines 89-120)
-   [Recommended Registry Structure: Consolidated Google APIs](#recommended-registry-structure-consolidated-google-apis)
    (Lines 121-157)
-   [Metric Queries & Visualization](#metric-queries-visualization) (Lines
    158-194)
-   [Diagnostic playbook](#diagnostic-playbook) (Lines 195-525)
-   [Quick reference: the checks in order](#quick-reference-the-checks-in-order)
    (Lines 526-546)

## The mental model

The Agent Platform runs as **default-deny egress**. An agent (typically an Agent
Runtime instance) cannot talk to *anything* outside itself unless every layer
permits it:

1.  **Agent Registry** — the destination must be registered as an Endpoint, MCP
    Server, or Agent.
2.  **Agent Gateway** — intercepts the request (it sits in front of the agent's
    egress). Note: an `authz_policy` must explicitly target the gateway
    resource, otherwise the authz extension won't actually run.
3.  **Service-extension (delegated authz)** — the gateway calls IAP to make an
    allow/deny decision.
4.  **IAP / IAM Policy Evaluation** — the agent's identity must be authorized by
    IAP:
    *   **IAM v1 (Legacy Policy Model)**: Evaluated when `iapPolicyVersion:
        "V1"` (or unset). The agent identity must have the **IAP egressor role**
        (`roles/iap.egressor`, display name "IAP-secured Egressor") directly
        bound on the registered resource shadow resource in IAP.
    *   **Unified Access Policy (UAP / Policy V2 / IAM v3)**: Evaluated when the
        gateway's IAP Authz Extension metadata specifies `iapPolicyVersion:
        "V2"`. Next-gen access control bound directly to Resource Manager
        hierarchy nodes (Organizations, Folders, Projects) via `PolicyBinding`
        (`iam.googleapis.com/v3`). Evaluates `AccessPolicy` rules with CEL
        expressions on deep attributes (`destination.agent_registry.*` or
        `destination.unregistered.*`). Follows strict evaluation invariants:
        absolute DENY precedence (cannot be overridden by downstream projects),
        and additive ALLOW aggregation across the CRM tree.
5.  **Principal Access Boundary (PAB)** — even with the IAM/UAP binding correct,
    a PAB policy on the principal set can restrict which resources it can reach.
    **PAB takes precedence over IAM Allow** — a correct egressor binding does
    nothing if a PAB scopes the principal away from the target.

**Implementation note.** Agent Gateway is built on a Google-managed Secure Web
Proxy instance that provides the egress-proxy capabilities. Customers don't
configure the proxy directly — its rules are derived from registry entries and
authz policies. Denials *can* originate at this proxy layer before IAP runs (no
IAP audit entry exists for those calls); those show up in load-balancer logs
(see Step 3b).

**Proxy Routing, TLS SNI & Agent Connectivity Templates (ACT):** The proxy
performs TLS inspection and looks inside the HTTPS call for the SNI hostname. In
Private Service Connect (PSC) environments, the client resolves APIs to internal
VIPs, so the outer tunnel request is naturally logged as `CONNECT
240.0.0.2:443`. This is expected. The proxy intercepts this and evaluates
routing based on the inner SNI hostname. If the corresponding hostname (e.g.,
`us-central1-aiplatform.googleapis.com`) is NOT registered, the proxy cannot
route the traffic, resulting in a `default_denied` action on the `CONNECT`
request before IAP is ever reached.

Under **VPC Service Controls (VPC-SC)**, Agent Gateway requires an **Agent
Connectivity Template (ACT)** configured with `vpcEgress: ALL_TRAFFIC`:

-   Traffic destined for external public APIs (e.g. `api.ipify.org`,
    `api.weather.gov`) resolves to the synthetic PSC VIP `240.0.0.2:443`,
    traverses the gateway, and egresses through the consumer VPC PSC-I Network
    Attachment subnet via **Cloud NAT** (`gateway-nat-gateway`) using static
    IPs. If Cloud NAT is missing or misconfigured, external traffic hangs or
    resets silently.
-   Traffic destined for Google APIs (e.g., Vertex AI, Cloud Run default domains
    `*.run.app`) routes directly through Andromeda/Private Google Access to
    Google Front Ends (GFE), bypassing Cloud NAT.

### Don't confuse `roles/iap.egressor` with other IAP roles

The display name "IAP-secured Egressor" maps to **`roles/iap.egressor`** —
that's the role for Agent Gateway egress. Other IAP roles exist that are easy to
mistake for it but are unrelated:

| Role ID                            | Purpose            | Use for Agent      |
:                                    :                    : Gateway?           :
| :--------------------------------- | :----------------- | :----------------- |
| `roles/iap.egressor`               | Agent egress       | **Yes — this one** |
:                                    : through Agent      :                    :
:                                    : Gateway            :                    :
| `roles/iap.tunnelResourceAccessor` | TCP/SSH tunneling  | No                 |
:                                    : through IAP to a   :                    :
:                                    : VM                 :                    :
| `roles/iap.httpsResourceAccessor`  | Access to          | No                 |
:                                    : IAP-protected web  :                    :
:                                    : apps (ingress)     :                    :
| `roles/iap.tunnelDestGroupUser`    | Member of an IAP   | No                 |
:                                    : tunnel destination :                    :
:                                    : group              :                    :

Don't substitute. The IAP authz check explicitly looks for
`iap.webServiceVersions.egressViaIAP`, which only `roles/iap.egressor` grants
for the Agent Gateway path.

If any layer says no, the agent gets back a 403:

```
{'code': 403, 'message': "403 Forbidden. {'message': 'Egress request is not authorized.', 'status': 'Forbidden'}"}
```

## The single biggest gotcha: hostname permutations

A Google API like `aiplatform.googleapis.com` is reachable through *many*
hostnames. The agent might call any of them depending on the SDK version,
regional client config, or whether mTLS is in play:

Form                       | Example
:------------------------- | :--------------------------------------------
Base                       | `aiplatform.googleapis.com`
Base + mTLS                | `aiplatform.mtls.googleapis.com`
Locational                 | `us-central1-aiplatform.googleapis.com`
Locational + mTLS          | `us-central1-aiplatform.mtls.googleapis.com`
Regional REP (public)      | `aiplatform.us-central1.rep.googleapis.com`
Regional REP (private/PSC) | `aiplatform.us-central1.p.rep.googleapis.com`

**The gateway matches hostnames exactly.** If you registered only
`aiplatform.googleapis.com` but the SDK actually called
`us-central1-aiplatform.googleapis.com`, the request gets denied — even though
"the API is registered." When investigating a 403, *always* establish what
hostname the agent actually called, then verify that *exact* hostname is in the
registry.

A registration script typically looks like this (note all five permutations):

```bash
reg_svc "${id}"                   "${name}"                "https://${id}.googleapis.com"
reg_svc "${id}-mtls"              "${name} mTLS"           "https://${id}.mtls.googleapis.com"
reg_svc "${LOCATION}-${id}"       "${name} Locational"     "https://${LOCATION}-${id}.googleapis.com"
reg_svc "${LOCATION}-${id}-mtls"  "${name} Locational mTLS" "https://${LOCATION}-${id}.mtls.googleapis.com"
reg_svc "${id}-${LOCATION}-rep"   "${name} Regional (REP)" "https://${id}.${LOCATION}.rep.googleapis.com"
```

## Recommended Registry Structure: Consolidated Google APIs

To simplify management and reduce resource overhead, it is recommended to
register all Google APIs under a single `googleapis` service entry in the Agent
Registry using **multiple interfaces** (one for each required API hostname).

### Recommended Base Interfaces (Consolidated `googleapis` Service)

Your base `googleapis` service should include the following interfaces:

-   `https://agentregistry.googleapis.com`
-   `https://aiplatform.mtls.googleapis.com`
-   `https://cloudresourcemanager.mtls.googleapis.com`
-   `https://iamcredentials.mtls.googleapis.com`
-   `https://telemetry.mtls.googleapis.com`
-   `https://{region}-aiplatform.mtls.googleapis.com`
-   `https://{region}-aiplatform.googleapis.com`
-   `https://aiplatform.{region}.rep.googleapis.com`

*(Replace `{region}` with your actual region, e.g., `us-central1`)*

### Other Services

If you are not using the consolidated model, or are using additional services
(like custom MCPs or separate engines), make sure they are registered:

-   `discoveryengine`
-   `logging`
-   `monitoring`
-   `oauth2`
-   `trace`
-   `iap`
-   `modelarmor`

Missing any required API hostnames is the most common "agent works in dev, fails
in prod" cause.

## Metric Queries & Visualization

When diagnosing performance issues, latency spikes, or intermittent failures,
you can query and visualize relevant time-series metrics if you have monitoring
access.

If the user reports "slowness" or "intermittent errors", visualize the trend
using a Mermaid `xychart-beta` chart in your diagnostic report.

### 1. Agent Gateway Egress QPS & Error Rate

Query the Secure Web Proxy metrics for the gateway to see traffic volume and
error distribution:

-   **Metric**: `networkservices.googleapis.com/gateway/request_count`
-   **Breakby**: `response_code_class`, `gateway_name`

### 2. Agent Runtime Latency (p50/p90/p99)

Query the Agent Runtime latency to identify performance degradation:

-   **Metric**: `aiplatform.googleapis.com/reasoning_engine/query_latency`
-   **Breakby**: `reasoning_engine_id`, `location`

### 3. Visualizing with Mermaid

When presenting latency or error trends in the report, format them as a Mermaid
chart:

```mermaid
xychart-beta
    title "Gateway Latency (p90) last 24h"
    x-axis [00:00, 04:00, 08:00, 12:00, 16:00, 20:00]
    y-axis "Latency (ms)" 0 --> 1000
    line [120, 150, 850, 900, 140, 130]
```

## Diagnostic playbook

```
        ┌─────────────────────────────────────┐
        │ 0. Establish context & Verify access│
        └────────────────┬────────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────────┐
        │ 1. Pull agent logs — confirm error  │
        │    (403 vs Connection vs Crash)     │
        └────────────────┬────────────────────┘
                         │
        ┌────────────────┴────────────────────┐
        │ Is it a Connection / Network error? │
        └───────┬──────────────────────┬──────┘
            no  │                  yes │
                │                      ▼
                │             ┌────────────────────────────────┐
                │             │ 3c. PSC Subnet Exhaustion &    │
                │             │ 3d. ACT ALL_TRAFFIC / Cloud NAT│
                │             └────────────────────────────────┘
                ▼
        ┌─────────────────────────────────────┐
        │ Is it a Container Crash / Python    │
        │ Exception?                          │
        └───────┬──────────────────────┬──────┘
            no  │                  yes │
            (403)                      ▼
                │             ┌────────────────────────────────┐
                │             │ 1b. Runtime Health Check       │
                │             │     (Python dependencies,      │
                │             │      stderr logs, crash codes) │
                │             └────────────────────────────────┘
                ▼
        ┌─────────────────────────────────────┐
        │ 2. Pull gateway logs — find the     │
        │    EXACT hostname being called      │
        └────────────────┬────────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────────┐
        │ 3. Pull IAP logs — Check V1 vs V2,  │
        │    DRY_RUN vs enforced, allow/deny? │
        └────────────────┬────────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────────┐
        │ 4. Is the EXACT hostname in the     │
        │    registry (any of agents /        │
        │    mcp-servers / endpoints)?        │
        └────────┬────────────────────┬───────┘
            no   │                yes │
                 ▼                    ▼
       ┌──────────────────┐   ┌─────────────────────────────┐
       │ Root cause:      │   │ 5. Check IAM / UAP Policies:│
       │ unregistered     │   │    - V1: roles/iap.egressor │
       │ hostname permu-  │   │    - V2: AccessPolicy & CRM │
       │ term. Recommend  │   │      PolicyBinding (CEL)    │
       │ registering all  │   └────────┬────────────────────┘
       │ five forms.      │            │
       └──────────────────┘            ▼
                            ┌─────────────────────────────┐
                            │ 6. Authz extension wired?   │
                            │    iapPolicyVersion V1/V2?  │
                            │    Dual-registry valid?     │
                            └────────┬────────────────────┘
                                     ▼
                            ┌─────────────────────────────┐
                            │ 7. Agent identity baseline  │
                            │    roles (Agent Runtime User, │
                            │    Registry Viewer, logs)   │
                            └────────┬────────────────────┘
                                     ▼
                            ┌─────────────────────────────┐
                            │ 8. PrincipalSet flakiness?  │
                            │    Recommend 1:1 binding    │
                            │    test                     │
                            └─────────────────────────────┘
```

### Step 0 — Establish context & Disable prompts

Before running any diagnostics, ensure the gcloud CLI is installed (see
[Google Cloud SDK Installation](https://cloud.google.com/sdk/docs/install)),
your environment is configured correctly, and prompts are disabled to prevent
commands from hanging:

1.  **Disable CLI Prompts**:

    ```bash
    gcloud config set core/disable_prompts True
    ```

2.  **Verify Project Access**:

    ```bash
    gcloud projects describe $PROJECT_ID
    ```

    If this fails, you cannot proceed with log gathering. Prompt the user for
    correct permissions or coordinates.

3.  **Discover the Correct Project (if resources are missing)**: If the active
    project (configured in `gcloud`) does not contain the expected Agent
    Gateways, Agent Runtime instances, or Registry entries, it may be the wrong
    project.

    > [!IMPORTANT] **Check Active Project First**: Always check the active
    > project first before scanning others:
    >
    > ```bash
    > ACTIVE_PROJ=$(gcloud config get-value project)
    > # Check for gateways
    > gcloud network-services agent-gateways list --location=us-central1 --project=$ACTIVE_PROJ
    > # Check for agents
    > gcloud agent-registry agents list --location=us-central1 --project=$ACTIVE_PROJ
    > ```
    >
    > If you find the target resources in the active project, **proceed
    > immediately** and do NOT scan other projects.

    If resources are not in the active project, check prioritized dev projects:
    `bash for proj in duncanjames-tf-dev duncanjames-agw-tf; do # Check gateways
    res_gw=$(gcloud network-services agent-gateways list --location=us-central1
    --project=$proj 2>&1) if [[ "$res_gw" == *"NAME"* ]]; then echo "FOUND
    Gateways in project: $proj" echo "$res_gw" fi # Check agents res_ag=$(gcloud
    alpha agent-registry agents list --location=us-central1 --project=$proj
    2>&1) if [[ "$res_ag" == *"NAME"* ]]; then echo "FOUND Registry in project:
    $proj" echo "$res_ag" fi done`

    If you cannot find the target resource (e.g. the stuck gateway or the
    failing agent) in the default project:

    1.  **Do not scan more than 2 other projects** or spend more than 2 turns
        trying to locate it.
    2.  **Do not list operations, logs, or subnets across multiple regions or
        projects in loops.** If the region is not specified, check ONLY the
        default region (usually `us-central1`).
    3.  If you find candidate resources in other projects, **prioritize those in
        `duncanjames-tf-dev` or `duncanjames-agw-tf`**, especially if they have
        `tf-test` or `re-agent` in their name (e.g.,
        `ag-client-tf-test-re-agent-*` or `ag-anywhere-tf-test-re-agent-*`).
        **You MUST treat these as the target resources** and immediately proceed
        to describe them and calculate subnet IPs. Do not describe or scan
        gateways in other projects (like `next26-*` or `agw-showcase-*`) if
        these prioritized candidates are present.
    4.  If you cannot find any candidate resource within these limits, **stop
        searching** and proceed with writing the diagnostic report using
        placeholders for names, explaining the general troubleshooting steps
        (including the PSC subnet exhaustion calculation formulas).

    Once the project containing the resources is identified, use that
    `PROJECT_ID` for all subsequent steps.

### Step 0.4 — Special Handling for Telemetry (telemetry.mtls.googleapis.com) Issues

If the symptom or error logs involve **telemetry.mtls.googleapis.com** (e.g.,
connection reset, handshake error, or `certificate verify failed` during
startup/`set_up()`):

1.  This is a known issue that can be caused by Egress Gateway blocking (BKI 23)
    OR SSL CA trust issues (BKI 17).
2.  In your diagnostic report, **you MUST address both possibilities and
    unconditionally recommend the following actions (even if resources appear to
    be already configured in the dirty environment)**:
    *   **Verify/Perform Endpoint Registration**: Explain that
        `telemetry.mtls.googleapis.com` must be registered in the Agent
        Registry. **You MUST also explicitly suggest checking whether other
        required monitoring/tracing endpoints (such as
        `monitoring.googleapis.com` or `trace.mtls.googleapis.com`) are
        registered as Endpoints**, and provide the `gcloud agent-registry
        endpoints create` commands for them.
    *   **Verify/Create Authorization Policy**: Explain that an
        `AuthorizationPolicy` must allow the agent's principal set to access the
        telemetry endpoint (by binding a `REQUEST_AUTHZ` policy with the IAP
        extension to the gateway). **You MUST recommend creating or updating
        this policy and provide the exact gcloud/Terraform configuration for
        it**, even if you see a policy already bound in the project.
    *   **Verify CA Trust**: Recommend configuring the agent's environment
        variables (`REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, and
        `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` pointing to
        `/etc/ssl/certs/ca-certificates.crt`) to ensure the Python/gRPC runtime
        trusts the gateway's TLS inspection CA.

### Step 0.5 — Mismatched Symptoms (Dirty Environments)

In shared evaluation or development projects, the logs you retrieve might
contain errors from previous, unrelated runs (e.g., finding an SSL `certificate
verify failed` error when the user is asking about IAP 403 errors).

If the logs you find do **not** match the symptom described in the user's prompt
(e.g., the user complains about "403 errors... logs mention IAP" but you find
"SSLError" and "no IAP logs"):

1.  **Do not ignore the user's prompt.** Do not assume your log findings are the
    only issue.
2.  In your diagnostic report, you MUST address the user's described symptom.
3.  Explain that while you found a different error in the active logs (and
    diagnose it), for any logs or findings where the agent is getting a 403 to
    an MCP server or endpoint, you must **check the IAP Egressor bindings on the
    matching resource in the registry first**:
    *   If there is no registry entry for the MCP server or endpoint, ensure
        there is one created.
    *   Ensure that the agent has the `roles/iap.egressor` role bound to the
        registry entry. If there are no permissions matching, tell the user to
        grant `roles/iap.egressor` against the registry entry.
    *   Verify that an `AuthorizationPolicy` is correctly bound to the Gateway.
4.  Include these prioritized troubleshooting steps for IAP denials (checking
    registry entries and `roles/iap.egressor` bindings first, checking IAP audit
    logs, and verifying Gateway policies) as recommended fixes, even if you
    couldn't verify them in the current log state.

### Step 1 — Confirm the symptom in agent logs

Check the Agent Runtime logs for the error (403, SSL Handshake Timeout, or
Connection Reset):

```
resource.type="aiplatform.googleapis.com/ReasoningEngine"
resource.labels.location=$LOCATION
resource.labels.reasoning_engine_id=$AGENT_ID
(textPayload:"403" OR textPayload:"handshake operation timed out" OR textPayload:"Connection reset by peer" OR textPayload:"Network is unreachable")
```

**Alternative (CLI Fallback):**

```bash
# Note: --limit=10 limits output volume to capture recent error logs while preventing context overflow and command timeouts.
gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine" AND resource.labels.location="'$LOCATION'" AND resource.labels.reasoning_engine_id="'$AGENT_ID'" AND (textPayload:"403" OR textPayload:"handshake operation timed out" OR textPayload:"Connection reset by peer" OR textPayload:"Network is unreachable")' --project=$PROJECT_ID --limit=10
```

The error payload tells you *that* it failed.

-   If it is a **403**, continue with the standard Gateway/IAP flow (Step 2).
-   If it is an **SSL Handshake Timeout** or **Connection Reset/Network
    Unreachable**, suspect a PSC provisioning issue or subnet exhaustion. Skip
    to **Step 3c**.
-   If it is a **container crash**, **startup error**, or **Python exception**,
    proceed to **Step 1b**.

### Step 1b — Debugging Agent Runtime Crashes (Container Crashes & Python Exceptions)

If the agent logs (Step 1) show that the Agent Runtime container failed to start
or crashed during execution (instead of a 403 or network timeout), follow this
checklist:

#### 1. Pull stderr logs for Python exceptions

Query the stderr logs specifically to find traceback info:

```
resource.type="aiplatform.googleapis.com/ReasoningEngine"
resource.labels.location=$LOCATION
resource.labels.reasoning_engine_id=$AGENT_ID
logName="projects/$PROJECT_ID/logs/aiplatform.googleapis.com%2Freasoning_engine_stderr"
```

**Alternative (CLI Fallback):**

```bash
# Note: --limit=20 fetches a sufficient recent log sample to identify startup stack traces without flooding output or timing out.
gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine" AND resource.labels.location="'$LOCATION'" AND resource.labels.reasoning_engine_id="'$AGENT_ID'" AND logName="projects/'$PROJECT_ID'/logs/aiplatform.googleapis.com%2Freasoning_engine_stderr"' --project=$PROJECT_ID --limit=20
```

Look for common Python startup failures:

-   `ModuleNotFoundError` or `ImportError`: The uploaded agent code requires a
    package that was not specified in the deployment requirements.
-   `SyntaxError`: Syntax error in the uploaded Python files.
-   Initialization crashes in the `__init__` method of the agent class.

#### 2. Check for Resource Limit Exceeded (OOM)

If the container dies silently without a Python traceback, it may have been
killed by the runtime due to Out of Memory (OOM) or CPU limits.

-   Check if the requested resource limits (CPU/Memory) in `DeploymentSpec` are
    sufficient.
-   Look for container termination status if visible in the logs (e.g., exit
    code `137` usually indicates OOMKilled).

### Step 2 — Find the failing hostname in gateway logs

```
resource.type="networkservices.googleapis.com/Gateway"
resource.labels.location="REGION"
resource.labels.gateway_name="AGENT_GATEWAY_NAME"
```

**Alternative (CLI Fallback):**

```bash
# Note: --limit=50 retrieves recent gateway access entries for log inspection without triggering command timeouts.
gcloud logging read 'resource.type="networkservices.googleapis.com/Gateway" AND resource.labels.location="REGION" AND resource.labels.gateway_name="AGENT_GATEWAY_NAME"' --project=PROJECT_ID --limit=50
```

The gateway log entry shows the actual hostname the request was for. Write it
down — you'll need it in step 4.

**Model Armor Special Case (High-Confidence Indicator):** If the gateway log
contains `serviceExtensionInfo` with `grpcStatus: "PERMISSION_DENIED"`,
`backendTargetType: "BACKEND_SERVICE"`, and `backendTargetName` matching
`modelarmor.*`, this is a definitive, high-confidence indicator of a Model Armor
integration permission issue. Refer to **known-issues.md §13 (BKI 13)**
immediately for the exact role requirements and fix commands.

### Step 3 — Check IAP allow/deny decision

> [!IMPORTANT] **Data Access Logs Prerequisite**: IAP authorization decisions
> for the data-plane are logged to **Data Access audit logs** (`DATA_READ`
> type), which are **disabled by default** in Google Cloud. If the log query
> returns **no results**, you must verify if IAP audit logging is enabled. Check
> with:
>
> ```bash
> gcloud projects get-iam-policy $PROJECT_ID \
>   --filter="auditConfigs.service:iap.googleapis.com" \
>   --format="yaml(auditConfigs)"
> ```
>
> If the output is empty or missing `DATA_READ` for `iap.googleapis.com`, you
> must enable it in the Google Cloud console under **IAM -> Audit Logs**.

Narrow the noise by scoping to the egress permission and excluding base-protocol
MCP method noise:

```
protoPayload.serviceName="iap.googleapis.com"
protoPayload.authorizationInfo.permission="iap.webServiceVersions.egressViaIAP"
-protoPayload.metadata.mcp_attributes.base_protocol_method="true"
```

**Alternative (CLI Fallback):**

```bash
# Note: --limit=50 captures enough audit logs for trace analysis while limiting response size.
gcloud logging read 'protoPayload.serviceName="iap.googleapis.com" AND protoPayload.authorizationInfo.permission="iap.webServiceVersions.egressViaIAP" AND -protoPayload.metadata.mcp_attributes.base_protocol_method="true"' --project=PROJECT_ID --limit=50
```

What to read out of each entry:

-   **`protoPayload.authorizationInfo[].granted`** — `true` or `false`. The
    bottom-line allow/deny.
-   **`protoPayload.authenticationInfo.principalSubject`** — the SPIFFE /
    `principal://...` URI of the caller.
-   **`protoPayload.authorizationInfo[].resource`** — the registered resource
    the call resolved to.
-   **`labels."iap.googleapis.com/audited_resource_name"`** — if this is
    `unregisteredResource`, the destination hostname isn't in the registry. Go
    to Step 4.
-   **The policy version & enforcement mode** — check `iapPolicyVersion` and
    `iamEnforcementMode`:

    ```yaml
    service: iap.googleapis.com
    failOpen: true
    timeout: 1s
    metadata:
      iamEnforcementMode: "DRY_RUN"  # or "ENFORCED"
      iapPolicyVersion: "v2"        # "v2" for UAP Policy V2; "V1" for legacy IAM v1
    ```

    *   **In `DRY_RUN` mode**, denials are logged but the request proceeds. If
        your agent is failing with a real 403 *and* IAP is in dry-run, the
        denial is coming from somewhere else — most often the gateway's
        underlying egress proxy (see Step 3b) or the destination service itself.
    *   **Under UAP (`iapPolicyVersion: "v2"`)**, check
        `protoPayload.metadata.policyEvaluationResults[]` and audit log entries.
        UAP logs will display the evaluation outcomes across the Resource
        Manager hierarchy (Organization, Folder, Project).
        *   If an **explicit DENY** matched anywhere in the hierarchy (e.g., at
            Org or Folder level), the request is rejected immediately, even if a
            project-level policy allows it.
        *   If **no ALLOW matched**, the request is denied by default-deny.
        *   Check for CEL attribute mismatches (e.g., calling an unregistered
            host when policies only allow `destination.agent_registry.*`, or
            path mismatches like missing `.json` extensions in REST API paths).

### Step 3b — If there's no IAP audit entry for the failing call, pull the gateway proxy load-balancer log

The gateway's underlying egress proxy can deny a request before IAP runs. The
denial is in the proxy's load-balancer log. The `SECURE_WEB_GATEWAY` label here
refers to the proxy implementation under the hood:

```
jsonPayload.@type="type.googleapis.com/google.cloud.loadbalancing.type.LoadBalancerLogEntry"
resource.labels.gateway_type="SECURE_WEB_GATEWAY"
```

**Alternative (CLI Fallback):**

```bash
# Note: --limit=50 limits output volume when querying load balancer log entries.
gcloud logging read 'jsonPayload.@type="type.googleapis.com/google.cloud.loadbalancing.type.LoadBalancerLogEntry" AND resource.labels.gateway_type="SECURE_WEB_GATEWAY"' --project=PROJECT_ID --limit=50
```

Key fields to inspect:

-   `httpRequest.status`: Look for `403`.
-   `jsonPayload.enforcedGatewaySecurityPolicy.hostname`: Look for
    `240.0.0.2:443` (expected under PSC).
-   `jsonPayload.enforcedGatewaySecurityPolicy.matchedRules`: Look for
    `default_denied`.
-   `jsonPayload.mtls.clientCertChainVerified`: Often `false` if connection
    dropped early.

If you see `240.0.0.2:443` under `hostname` and `default_denied` under
`matchedRules`, this is a routing drop. The proxy decrypted the tunnel but
failed to match the *inner* SNI hostname against the Agent Registry. Make sure
all required hostname permutations are registered.

### Step 3c — Diagnosing PSC Subnet Exhaustion

If you observe **SSL Handshake Timeouts** or **Connection Reset** in the agent
logs, or if the Agent Gateway deployment is failing/stuck, the Private Service
Connect (PSC) subnet might be out of IP addresses.

> [!NOTE] **CLIENT_TO_AGENT Gateways**: Both ingress (`CLIENT_TO_AGENT`) and
> egress (`AGENT_TO_ANYWHERE`) gateways can be stuck in provisioning. Ingress
> gateways do NOT populate the `agentGatewayCard` even when active, so you must
> check if they are stuck by describing their network attachment and calculating
> subnet IPs.

1.  **Find the Agent Gateway Name** (if not provided): List the gateways in the
    project to find the one that is stuck or relevant:

    ```bash
    gcloud network-services agent-gateways list --location=$LOCATION --project=$PROJECT_ID
    ```

2.  **Identify the Network Attachment**: Describe the Agent Gateway to find the
    Network Attachment in use:

    ```bash
    gcloud network-services agent-gateways describe AGENT_GATEWAY_NAME --location=$LOCATION --project=$PROJECT_ID
    ```

    Look for `networkConfig.egress.networkAttachment` or
    `networkConfig.ingress.networkAttachment` (depending on gateway type).

3.  **Describe the Network Attachment**:

    ```bash
    gcloud compute network-attachments describe NETWORK_ATTACHMENT_NAME --region=$LOCATION --project=$PROJECT_ID
    ```

    Note the `subnetwork` field.

4.  **Inspect the Subnet**: Describe the subnetwork to check its IP range:

    ```bash
    gcloud compute networks subnetworks describe SUBNET_NAME --region=$LOCATION --project=$PROJECT_ID
    ```

    Check the `ipCidrRange` (e.g., a `/28` subnet only has 16 IP addresses, and
    Google Cloud reserves 4, leaving only 12 for resources).

5.  **Check IP Usage and Calculate Free IPs**:

    *   Find the number of allocated IPs by looking at the `connectionEndpoints`
        list in the network attachment description from Step 2. Each endpoint in
        that list consumes one IP address.
    *   Calculate the number of usable IPs in the subnet based on its
        `ipCidrRange` (e.g., a `/28` subnet has 16 IPs, minus 4 reserved by
        Google Cloud = 12 usable IPs; a `/16` has 65536 IPs, minus 4 = 65532
        usable).
    *   Explicitly state the calculation in your report: `Usable IPs - Allocated
        IPs = Free IPs`.
    *   **Flag `/28` Exhaustion Risk**: Even if the current subnet has free IPs
        (or is larger, like `/16`), if you are diagnosing a stuck/failing
        gateway deployment, you **MUST** explicitly mention in your report that
        a `/28` subnet is too small and easily exhausted, and recommend
        expanding the CIDR range to at least `/26` as a best practice.

### Step 3d — Diagnosing Agent Connectivity Template (ACT) & Network Routing

Under VPC Service Controls (VPC-SC), Agent Gateway relies on an **Agent
Connectivity Template (ACT)**. If the agent experiences timeouts, silent
connection hangs, or connection drops when attempting to reach public internet
endpoints or internal services, check the ACT and consumer network
configuration:

1.  **Inspect the Agent Connectivity Template**:

    ```bash
    gcloud network-services agent-connectivity-templates describe ACT_NAME \
      --location=$LOCATION --project=$PROJECT_ID
    ```

    Verify the `vpcEgress` mode:

    -   `vpcEgress: ALL_TRAFFIC`: Intercepts and routes all traffic (Google APIs
        and public Internet) into the consumer VPC network attachment.
    -   `vpcEgress: DEFAULT`: Only Private Google Access traffic routes through
        the template.

2.  **Verify Consumer Cloud NAT for External Endpoints (BKI 28)**: When
    `vpcEgress: ALL_TRAFFIC` is enabled, external internet traffic (e.g.,
    `api.ipify.org`, `api.weather.gov`, third-party SaaS) resolves via synthetic
    PSC VIP `240.0.0.2:443`, traverses the gateway, and exits through the
    consumer VPC PSC-I subnet.

    -   **Cloud NAT Requirement**: The consumer VPC **MUST** have a Cloud NAT
        gateway configured that covers the PSC-I subnet range (e.g.,
        `gateway-nat-gateway` with static IP).
    -   If Cloud NAT is missing or does not cover the PSC-I subnet, TCP
        connections to external internet endpoints will **hang silently or
        reset** without any proxy error log!
    -   Verify NAT status:

        ```bash
        gcloud compute routers nats list --router=ROUTER_NAME --region=$LOCATION --project=$PROJECT_ID
        ```

3.  **Verify Cloud DNS Peering**: Ensure the consumer VPC has Cloud DNS peering
    configured so that Google API hostnames and registered domains correctly
    resolve to the synthetic PSC VIP (`240.0.0.2:443`).

4.  **Dangling ACT References on Teardown (BKI 30)**: If deleting an Agent
    Connectivity Template fails with `FAILED_PRECONDITION: Resource ... is
    already being used by resource(s) .../agentGateways/...` after the gateway
    was already deleted, Network Services retained a dangling reference
    (`FROM_AGENT_GATEWAY<ID>`). Clear it using Stubby:

    ```bash
    stubby call blade:network-services-prod-${REGION} google.internal.cloud.reference.References.DeleteReference \
      "{name: 'projects/${PROJECT_NUMBER}/locations/${REGION}/references/FROM_AGENT_GATEWAY${GW_ID}'}"
    ```

### Step 4 — Verify the hostname is registered (in the form the agent used)

List registry entries — pick the right resource type for the destination:

```bash
gcloud agent-registry endpoints list      --project=$PROJECT_ID --location=$LOCATION
gcloud agent-registry mcp-servers list    --project=$PROJECT_ID --location=$LOCATION
gcloud agent-registry agents list         --project=$PROJECT_ID --location=$LOCATION
```

Grep the output for the exact hostname from step 2. If it's missing, that's the
root cause.

### Step 5 — Verify IAM / UAP bindings on the registered resource

Depending on whether your gateway uses **IAM v1** or **Unified Access Policy
(UAP / Policy V2)** (determined by `iapPolicyVersion` in the Authz Extension),
verify the corresponding authorization bindings:

#### Option A: Unified Access Policy (UAP / Policy V2 / IAM v3)

In UAP, access policies are bound directly to the **Google Cloud Resource
Manager (CRM) hierarchy** (Project, Folder, or Organization) rather than IAP
shadow resources.

1.  **List Policy Bindings on the Target Project**:

    ```bash
    gcloud iam policy-bindings list \
      --target="//cloudresourcemanager.googleapis.com/projects/${PROJECT_ID}" \
      --location=global
    ```

2.  **List Policy Bindings on Parent Folders / Organization**:

    ```bash
    # Folder level
    gcloud iam policy-bindings list \
      --target="//cloudresourcemanager.googleapis.com/folders/${FOLDER_ID}" \
      --location=global

    # Organization level
    gcloud iam policy-bindings list \
      --target="//cloudresourcemanager.googleapis.com/organizations/${ORGANIZATION_ID}" \
      --location=global
    ```

3.  **Inspect the Referenced AccessPolicy**:

    ```bash
    gcloud iam access-policies get ACCESS_POLICY_ID --location=global
    ```

    *   Verify the rule uses the full FQDN permission:
        `iap.googleapis.com/resources.egressViaIAP`.
    *   Verify the agent's principal or principal set matches
        `rules[].principals`.
    *   Inspect CEL conditions: check for null dereferences, missing `.json`
        extensions in REST API paths, or overly broad
        `destination.is_registered == false` DENY rules that crash
        unauthenticated agent boot routines.

4.  **Check for Org Policy Constraint Blockers (BKI 24)**: If `gcloud iam
    policy-bindings create` failed with `CUSTOM_ORG_POLICY_VIOLATION`, check
    whether `constraints/iam.managed.disableAccessPolicyBinding` is enforced:

    ```bash
    gcloud org-policies describe constraints/iam.managed.disableAccessPolicyBinding --project=$PROJECT_ID
    ```

    To unblock, apply an override disabling the constraint (`enforce: false`).

5.  **REST API Fallback for UAP Bindings**:

    ```bash
    curl -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
      "https://iam.googleapis.com/v3/projects/${PROJECT_ID}/locations/global/policyBindings"
    ```

#### Option B: IAM v1 (Legacy Policy Model)

In IAM v1, `roles/iap.egressor` is bound directly to the IAP shadow resource at
the registry or endpoint level:

##### Registry-level IAM policy

```bash
curl -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -d '{}' \
  -X POST "https://iap.googleapis.com/v1/projects/${PROJECT_NUMBER}/locations/${LOCATION}/iap_web/agentRegistry:getIamPolicy" \
  -H "Content-Type: application/json"
```

##### Per-endpoint IAM policy

```bash
curl -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -d '{}' \
  -X POST "https://iap.googleapis.com/v1/projects/${PROJECT_NUMBER}/locations/${LOCATION}/iap_web/agentRegistry/endpoints/${ENDPOINT_ID}:getIamPolicy" \
  -H "Content-Type: application/json"
```

##### Same call, but for global registry:

```bash
curl -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -d '{"options": {"requestedPolicyVersion": 3}}' \
  -X POST "https://iap.googleapis.com/v1/projects/${PROJECT_NUMBER}/locations/global/iap_web/agentRegistry:getIamPolicy" \
  -H "Content-Type: application/json"
```

When reading the returned policy, look for a binding that matches the agent's
service account or principal set with role `roles/iap.egressor`.

### Step 6 — Inspect the gateway / authz extension wiring

#### List authz extensions

```bash
gcloud service-extensions authz-extensions list \
  --location=$LOCATION --project=$PROJECT_ID

gcloud service-extensions authz-extensions describe RESOURCE_NAME \
  --location=$LOCATION --project=$PROJECT_ID
```

#### Verify AuthorizationPolicy & AuthzExtension Wiring

1.  **Check `iapPolicyVersion` in Authz Extension**: Describe the Authz
    Extension and inspect the `metadata` map:

    ```bash
    gcloud service-extensions authz-extensions describe RESOURCE_NAME \
      --location=$LOCATION --project=$PROJECT_ID --format="yaml(metadata)"
    ```

    -   If using **UAP (Policy V2)**: `iapPolicyVersion` MUST be explicitly set
        to `"V2"`.
    -   If using **IAM v1**: `iapPolicyVersion` is `"V1"` or unset.
    -   Check `iamEnforcementMode`: `"DRY_RUN"` logs evaluations without
        blocking; `"ENFORCED"` actively blocks.

2.  **Verify AuthorizationPolicy Binding**: Verify that the
    `AuthorizationPolicy` is correctly bound to your `Gateway`. The policy must
    target the gateway resource. If it is not bound, the authorization logic
    will not be applied to the gateway traffic.

    -   Inspect the `AuthorizationPolicy` resource.
    -   Ensure the policy's target matches the gateway's name and location.

3.  **Verify Multi-Registry Gateway Constraints (BKI 26)**: Inspect the Agent
    Gateway description:

    ```bash
    gcloud network-services agent-gateways describe AGENT_GATEWAY_NAME \
      --location=$LOCATION --project=$PROJECT_ID --format="yaml(registries)"
    ```

    -   Network Services permits at most **2 registries** bound to an Agent
        Gateway.
    -   When 2 registries are configured, **exactly ONE must be `global`** and
        the second must be **`regional` or `multi-regional`**.
    -   Configuring two regional, two multi-regional, or two global registries
        will be rejected with HTTP 400 validation failure.

#### List authz policies, agent gateways, and authz extensions via raw API

```bash
# authzPolicies
curl -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  "https://networksecurity.googleapis.com/v1alpha1/projects/${PROJECT_ID}/locations/${LOCATION}/authzPolicies"

# agentGateways
curl -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  "https://networkservices.googleapis.com/v1alpha1/projects/${PROJECT_ID}/locations/${LOCATION}/agentGateways"

# authzExtensions
curl -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  "https://serviceextensions.googleapis.com/v1alpha1/projects/${PROJECT_ID}/locations/${LOCATION}/authzExtensions"
```

### Step 7 — Verify the agent identity has the baseline roles

The agent identity itself needs enough permissions to function on the source
side:

-   `roles/aiplatform.user` (Agent Runtime User) — to run the ReasoningEngine
-   Agent Registry viewer role — to know what's registered
-   `roles/logging.logWriter`, `roles/monitoring.metricWriter`, telemetry roles
    — observability
-   `roles/browser` — needed for `resourcemanager.projects.get` during SDK init.
    Without it, the ReasoningEngine fails startup with `Failed to convert
    project number to project ID` (see known-issues §1).
-   `roles/iap.httpsGatewayUser` (IAP-secured Web App User) and
    `roles/iap.egressor` (IAP-secured Egressor) — to ensure the agent is
    authorized to egress through IAP. **You MUST recommend verifying both roles
    and the AuthorizationPolicy binding in your report's fix/debugging
    section**:
    1.  `roles/iap.httpsGatewayUser` is typically required on the Gateway
        resource.
    2.  `roles/iap.egressor` is required on the target Registry/Endpoint.
    3.  Verify that the `AuthorizationPolicy` is correctly bound to the Gateway
        (ensure the policy targets the gateway resource). Do NOT omit these
        recommendations even if you believe they are already correctly
        configured, as they are essential verification steps for the user.

### Step 7b — Check Principal Access Boundary (PAB) policies

A PAB policy can override IAM Allow and restrict the resources the principal set
can reach.

```bash
# List org-wide PAB policies
gcloud iam principal-access-boundary-policies list \
  --organization="${ORGANIZATION_ID}" --location=global

# Find what's bound to the agent's principal set
gcloud iam policy-bindings search-target-policy-bindings \
  --project="${PROJECT_ID}" --target="${PRINCIPAL_SET}"
```

### Step 7c — Cloud Run / Functions Egress Authentication

If the destination is Cloud Run or Cloud Functions, direct OIDC authentication
using the default Agent Identity (`principalSet://...`) is not natively
supported by Cloud Run. The agent must use Service Account impersonation.

**You MUST recommend the following in your report if the destination is Cloud
Run:**

1.  Explain that direct Agent Identity to Cloud Run OIDC auth is unsupported.
2.  Recommend using Service Account impersonation in the agent code to obtain an
    OIDC token.
3.  Specify that the Agent Identity needs `roles/iam.serviceAccountTokenCreator`
    on the target Service Account (which in turn needs `roles/run.invoker` on
    the Cloud Run service). Refer to `references/known-issues.md` BKI 21 for
    details and code examples.

### Step 8 — PrincipalSet vs Principal

If permissions seem flaky, suspect the **PrincipalSet** binding. Move to a **1:1
Principal binding** (bind the specific service account directly) to verify.

### Step 9 — Cross-Project Runtime-to-Gateway Binding Verification

When an Agent Runtime (Reasoning Engine) in Project A (`AE_PROJECT_NUMBER`)
binds to an Agent Gateway in Centralized Governance Project B
(`AGW_PROJECT_ID`):

1.  **Check Deployer Caller Permissions:** Verify the deploying identity has
    `roles/networkservices.viewer` (or `networkservices.agentGateways.get` and
    `networkservices.agentGateways.use`) on the gateway in Project B:

    ```bash
    gcloud network-services agent-gateways describe GATEWAY_NAME \
      --project=AGW_PROJECT_ID \
      --location=REGION
    ```
2.  **Check Vertex AI Service Agent IAM in Gateway Project:** Ensure Project A's
    service agent is authorized in Project B:

    ```bash
    gcloud projects get-iam-policy AGW_PROJECT_ID \
      --flatten="bindings[].members" \
      --filter="bindings.members:service-AE_PROJECT_NUMBER@gcp-sa-aiplatform.iam.gserviceaccount.com" \
      --format="table(bindings.role)"
    ```

    Must have `roles/networkservices.viewer` or custom role
    `ae_agw_cross_project_sa`
    (`networkservices.agentGateways.get,networkservices.operations.get`).
3.  **Validate Regional Colocation Invariant:** Both the runtime and the gateway
    **MUST** be deployed in the exact same region (e.g. `us-central1`).
    Cross-region bindings fail control plane validation with `INVALID_ARGUMENT`.
4.  **Verify Context-Aware Access (CAA) Token Sharing Opt-Out:** Verify that the
    agent runtime was deployed with:
    `"GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": False` Without
    this, bound token enforcement drops credentials during cross-project egress,
    returning `401 Context-Aware Access requirements are not met`.
5.  **Validate IAP Policy Target (Server-Generated Resource ID vs Friendly
    Name):** Ensure IAP policies (`gcloud iap web add-iam-policy-binding`)
    target the server-generated resource ID (`agentregistry-00000000-...`
    extracted via `registryResource`), NOT the friendly service name:

    ```bash
    gcloud agent-registry services describe MCP_SERVICE_NAME \
      --project=AGW_PROJECT_ID \
      --location=REGION \
      --format='value(registryResource)'
    ```
6.  **Verify Cross-Project VPC Service Controls (VPC-SC):** If Project A and
    Project B are in separate perimeters, ensure a perimeter bridge or
    ingress/egress rules cover `aiplatform.googleapis.com`,
    `networkservices.googleapis.com`, and `agentregistry.googleapis.com`.

### Step 10 — Diagnose Secure Web Proxy (SWP) and Policy-Based Routing (PBR) Egress

If the consumer VPC employs a downstream Secure Web Proxy (SWP) behind an Agent
Gateway Network Attachment for Layer 7 inspection:

1.  **Verify Next-Hop SWP Deployment Mode**: Confirm SWP is deployed directly in
    the consumer VPC with `type: SECURE_WEB_GATEWAY` and
    `routingMode: NEXT_HOP_ROUTING_MODE`. Do NOT attempt to use PSC Service
    Attachments (which only support explicit `HTTP CONNECT` proxies) or static
    routes (which fail because Network Attachments cannot bear VM instance tags).
2.  **Verify Policy-Based Routing (PBR) Matching**:
    Ensure a PBR exists with priority 200 matching source CIDR of the PSC-I subnet
    (`10.20.1.0/24`) and destination `0.0.0.0/0` routing to `--next-hop-ilb-ip=<SWP_IP>`:

    ```bash
    gcloud network-connectivity policy-based-routes create agw-psci-to-swp-pbr \
      --network=projects/PROJECT_ID/global/networks/VPC_NAME \
      --source-range=10.20.1.0/24 \
      --destination-range=0.0.0.0/0 \
      --next-hop-ilb-ip=10.20.1.250 \
      --priority=200 \
      --project=PROJECT_ID
    ```

    Ensure a default fallback PBR exists with priority 1000 routing to `DEFAULT_ROUTING`.
3.  **Verify Cloud NAT `ENDPOINT_TYPE_SWG` on Proxy Subnet**:
    Ensure the Cloud NAT covering the proxy-only subnet (`REGIONAL_MANAGED_PROXY`)
    includes `--endpoint-types=ENDPOINT_TYPE_VM,ENDPOINT_TYPE_SWG`. Without this,
    SWP proxy backends cannot establish outbound connections to the internet.
4.  **Verify GatewaySecurityPolicy CEL Allowlist**:
    Check SWP logs for `DENIED` decisions under `networkservices.googleapis.com/Gateway`.
    Verify that both external target domains and the regional AI Platform session
    endpoint (`<region>-aiplatform.mtls.googleapis.com`) are allowed in CEL rules:

    ```cel
    inUrlList(host(), ['api.weather.gov', 'api.ipify.org', 'REGION-aiplatform.mtls.googleapis.com'])
    ```

    Omission of the regional AI Platform endpoint breaks ADK streaming queries
    with HTTP 503 (`remote connection failure`).

## Quick reference: the checks in order

When triaging a 403 or connection failure, walk these in order:

1.  Confirm the error in the agent log (403 vs SSL Handshake Timeout vs
    Connection Reset).
2.  Find the *exact hostname* in the gateway log (if 403).
3.  Check IAP audit log: decision, principal, policy version
    (`iapPolicyVersion="v2"` vs `"V1"`), `iamEnforcementMode`, and watch for
    `unregisteredResource`.

    -   3b. If no IAP audit entry, pull gateway proxy load-balancer log.
    -   3c. If SSL Handshake Timeout or Connection Reset, run the PSC Subnet
        Exhaustion Check.
    -   3d. If timeouts or silent connection drops to public internet under
        VPC-SC, verify Agent Connectivity Template (`vpcEgress: ALL_TRAFFIC`)
        and Consumer Cloud NAT routing.

4.  Confirm hostname is registered.

5.  Check IAM / UAP Policies on the target:

    -   IAM v1: Check `roles/iap.egressor` on the registry/endpoint shadow
        resource.
    -   UAP (Policy V2): Check CRM `PolicyBinding` and `AccessPolicy` across
        Project, Folder, and Org. Verify CEL rules and check for Org Policy
        blocker `constraints/iam.managed.disableAccessPolicyBinding`.

6.  Check authz extensions (`iapPolicyVersion: "V2"` vs `"V1"`),
    AuthorizationPolicy targets, and multi-registry constraints (max 2: 1
    global + 1 regional/multi-regional).

7.  Confirm agent identity has baseline roles. 7b. Check PAB policies. 7c. Check
    Cloud Run egress auth (impersonation).

8.  If behavior is flaky, switch to direct Principal bindings.

9.  If cross-project runtime binding (Project A -> Gateway in Project B), verify
    deployer and service agent viewer permissions on Project B, regional
    colocation, CAA opt-out
    (`GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES: False`), and
    server-generated resource ID in IAP bindings.

10. If routing egress through a downstream Secure Web Proxy (SWP), verify Next-Hop
    SWP mode (NOT PSC endpoint/service attachment), PBR source-range matching
    on the PSC-I subnet (priority 200) to next-hop ILB IP, Cloud NAT with
    `ENDPOINT_TYPE_SWG` on the proxy-only subnet, and GatewaySecurityPolicy CEL
    allowlist for external domains and regional aiplatform sessions.
