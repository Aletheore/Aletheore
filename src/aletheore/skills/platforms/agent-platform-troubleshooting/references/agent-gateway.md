# Agent Gateway — Debugging Reference

Curated from Google Cloud's Gemini Enterprise Agent Platform architecture.
Focused on what a debugger needs to triage failed requests, routing issues, and
provisioning blocks on Agent Gateway and Agent Connectivity Templates (ACT).

## Table of Contents

-   [TL;DR for debugging](#tldr-for-debugging) (Lines 24-89)
-   [1. Architecture & Control Plane](#1-architecture--control-plane) (Lines 90-121)
-   [2. Deployment Modes: Standard vs. Standalone](#2-deployment-modes-standard-vs-standalone) (Lines 122-145)
-   [3. Agent Connectivity Template (ACT) & VPC-SC Support](#3-agent-connectivity-template-act--vpc-sc-support) (Lines 146-208)
-   [4. Dual-Registry Gateway Binding Rules](#4-dual-registry-gateway-binding-rules) (Lines 209-244)
-   [5. Cloud NAT & Egress IP Mechanics](#5-cloud-nat--egress-ip-mechanics) (Lines 245-286)
-   [6. Downstream Secure Web Proxy (SWP) & Policy-Based Routing (PBR)](#6-downstream-secure-web-proxy-swp--policy-based-routing-pbr) (Lines 287-496)
-   [7. Delegated Authorization & Service Extensions](#7-delegated-authorization--service-extensions) (Lines 497-543)
-   [8. Logging, Monitoring, & Tracing](#8-logging-monitoring--tracing) (Lines 544-573)
-   [9. Common Failure Modes & Troubleshooting Guide](#9-common-failure-modes--troubleshooting-guide) (Lines 574-610)
-   [10. Reference Gateway & ACT Specification](#10-reference-gateway--act-specification) (Lines 611-648)
-   [11. Cross-Project Centralized Governance Architecture](#11-cross-project-centralized-governance-architecture) (Lines 649-779)

--------------------------------------------------------------------------------

## TL;DR for debugging

1.  **Control Plane & Infrastructure**: Managed under Network Services
    (`networkservices.googleapis.com`) using Cloud Control Forwarding Engine
    (CCFE). Backing Envoy proxy infrastructure resides in Google Cloud tenant
    projects (`*-tp`) managed by Borg user `cloud-control2-networkservices-clh`.

    > [!IMPORTANT] **CRITICAL RULE**: Never attempt or recommend changes to
    > Google Cloud tenant projects (`*-tp`). All modifications, deployments, and
    > debugging must be confined strictly to consumer projects.

2.  **Native VPC-SC Support Mandates ACT in `ALL_TRAFFIC` Mode**: As of
    September 8, 2026, Agent Gateway creation inside a VPC-SC perimeter works
    natively out of the box without requiring manual control plane ingress
    policies, on the precondition that the gateway binds to an Agent
    Connectivity Template (ACT) configured with `vpcEgress: ALL_TRAFFIC`. In
    `ALL_TRAFFIC` mode, all outbound packets from runtime containers are
    captured and tunneled through the PSC-I Network Attachment into the consumer
    VPC, ensuring complete perimeter compliance.

3.  **The Cloud NAT Egress Prerequisite (Silent Black Hole)**: Under `vpcEgress:
    ALL_TRAFFIC`, external public Internet traffic exiting the PSC-I Network
    Attachment routes through the consumer VPC. The consumer VPC subnet **MUST**
    have Cloud NAT configured. Without Cloud NAT, outbound calls to external
    SaaS/REST APIs hang and timeout.

4.  **Google Front End (GFE) Bypass Exception**: Traffic directed to Google APIs
    or Cloud Run default domains (`*.run.app`) does NOT exit through Cloud NAT.
    Andromeda SDN intercepts Google IP ranges and routes them internally to GFE
    via Private Google Access.

5.  **Dual-Registry Invariant (1 Global + 1 Regional/Multi-Regional)**: Agent
    Gateway supports linking up to **two** Agent Registries. Network Services
    enforces:

    -   Exactly **one** registry **MUST be `global`**.
    -   The other can be either **`regional`** (e.g. `us-central1`) or
        **`multi-regional`** (e.g. `us`, `eu`).
    -   Two regional or two multi-regional registries without a global will be
        rejected with an invalid argument error.

6.  **CCFE Mutual Exclusion Lock (HTTP 409 ABORTED)**: CCFE enforces a
    resource-level mutation lock. Submitting concurrent PATCH, import, or update
    operations while an update LRO is active aborts immediately with `HTTP 409
    ABORTED: unable to queue the operation` until the in-flight operation
    finishes (~1.5–2 minutes).

7.  **Reasoning Engine Concurrency Constraint**: Only one active Vertex AI
    Reasoning Engine instance can bind to an Agent Gateway at a time.
    Deployments must delete prior instances in the region before binding a new
    engine.

8.  **Teardown Mechanics & Dangling ACT References**:

    -   AuthzPolicies bound to the gateway must be deleted and polled to
        completion *before* deleting the gateway; deleting in reverse order can
        orphan policy attachments.
    -   Deleting an AGW can leave a dangling reference
        (`FROM_AGENT_GATEWAY<ID>`) on the ACT. Deleting the ACT then fails with
        HTTP 400 `FAILED_PRECONDITION: Resource ... is already being used`.
        Clear the dangling reference via: `stubby call
        blade:network-services-prod-${REGION}
        google.internal.cloud.reference.References.DeleteReference`

--------------------------------------------------------------------------------

## 1. Architecture & Control Plane

Agent Gateway acts as the enterprise security gateway and Layer 7 policy
enforcement point for AI agent workloads:

-   **Client-to-Agent (ingress):** External clients reaching agents or tools on
    Google Cloud.
-   **Agent-to-Anywhere (egress):** Agents on Google Cloud reaching external
    services, APIs, MCP servers, or peer agents.

### Core Resource Types

| Type                                         | Purpose                     |
| :------------------------------------------- | :-------------------------- |
| `networkservices.agentGateways`              | The gateway resource        |
:                                              : governing agent             :
:                                              : communication.              :
| `networkservices.agentConnectivityTemplates` | Centralized network         |
:                                              : isolation, compute, and DNS :
:                                              : topology.                   :
| `networksecurity.authzPolicies`              | Attaches authorization      |
:                                              : policies (IAP, Model Armor) :
:                                              : to the gateway.             :
| `networkservices.authzExtensions`            | Custom or managed           |
:                                              : authorization callouts      :
:                                              : (`ext_authz`, `ext_proc`).  :
| `compute.networkAttachments`                 | PSC-Interface (PSC-I)       |
:                                              : attachment in consumer VPC  :
:                                              : for private egress.         :

--------------------------------------------------------------------------------

## 2. Deployment Modes: Standard vs. Standalone

Agent Gateway operates in one of two deployment modes:

### 2.1 Standard Mode (Enterprise Isolated)

-   Explicitly binds to an **Agent Connectivity Template
    (`agentConnectivityTemplate`)**, a consumer VPC **Network Attachment (via
    PSC-I)**, and one or two **Agent Registries**.
-   Governs both external Internet egress and private corporate VPC
    destinations.
-   Enables VPC-SC perimeter enforcement, private DNS peering, and deterministic
    egress IP anchoring via consumer Cloud NAT.

### 2.2 Standalone Mode (Lightweight Google-Managed)

-   Omits the ACT, consumer Network Attachment, and Cloud DNS peering.
-   Egresses directly to endpoints cataloged in the Agent Registry over
    Google-managed networking.
-   Useful for quick prototyping where private VPC routing or VPC-SC perimeters
    are not required.

--------------------------------------------------------------------------------

## 3. Agent Connectivity Template (ACT) & VPC-SC Support

The Agent Connectivity Template defines the compute model, network isolation,
and DNS topology for agent containers.

### 3.1 Egress Modes: `ALL_TRAFFIC` vs. `PRIVATE_RANGES_ONLY`

| Egress Mode               | Network Path      | VPC-SC        | Use Case     |
:                           :                   : Compatible?   :              :
| :------------------------ | :---------------- | :------------ | :----------- |
| **`ALL_TRAFFIC`**         | Outbound TCP/UDP  | **YES         | Enterprise   |
:                           : packets are       : (Mandatory)** : production   :
:                           : captured and      :               : environments :
:                           : tunneled through  :               : requiring    :
:                           : the PSC-I Network :               : VPC-SC       :
:                           : Attachment into   :               : perimeter    :
:                           : the consumer VPC. :               : compliance   :
:                           : External DNS      :               : and          :
:                           : queries resolve   :               : controlled   :
:                           : to synthetic PSC  :               : egress.      :
:                           : VIP               :               :              :
:                           : `240.0.0.2\:443`. :               :              :
| **`PRIVATE_RANGES_ONLY`** | Directs only RFC  | **NO**        | Dev/test     |
:                           : 1918 traffic      :               : workloads    :
:                           : (`10.0.0.0/8`,    :               : where public :
:                           : `172.16.0.0/12`,  :               : egress does  :
:                           : `192.168.0.0/16`) :               : not need VPC :
:                           : through PSC-I.    :               : inspection   :
:                           : Public internet   :               : or VPC-SC    :
:                           : traffic exits     :               : compliance.  :
:                           : directly from the :               :              :
:                           : Google runtime.   :               :              :

### 3.2 Consumer VPC Network Attachment (PSC-I) Requirements

The Network Attachment provides the data-plane bridge between the Google-managed
runtime and the consumer VPC:

-   **Subnet Configuration**:
    -   Must be `purpose: PRIVATE`.
    -   `privateIpGoogleAccess: true` must be enabled.
    -   **Subnet Sizing**: CIDR must be sized adequately (minimum `/26`,
        recommended `/24`). **Never use a `/28` subnet**, as concurrent rollouts
        and interface allocations quickly exhaust available IPs.
-   **Attachment Settings**: Requires `connectionPreference: ACCEPT_AUTOMATIC`.
-   **IAM Role Grants**: The subnet requires `roles/compute.networkUser` granted
    to **both** service agents:
    1.  `service-{PROJECT_NUMBER}@gcp-sa-agentgateway.iam.gserviceaccount.com`
    2.  `service-{PROJECT_NUMBER}@gcp-sa-dep.iam.gserviceaccount.com`

### 3.3 Cloud DNS Peering Configuration

Configured under `egressNetworkConfig.dnsPeeringConfig`:

-   Peers the runtime's internal DNS resolver to the consumer's VPC network
    (e.g., domain `corp.internal.` to `agw-consumer-vpc`).
-   Authorizes the GKE compute model to resolve private records managed in the
    consumer's Private Cloud DNS Zone (`corp-internal-zone`).
-   **IAM Role Grant**: Requires `roles/dns.peer` granted to:
    `service-{PROJECT_NUMBER}@gcp-sa-networkservices.iam.gserviceaccount.com`.

--------------------------------------------------------------------------------

## 4. Dual-Registry Gateway Binding Rules

Agent Gateway supports linking up to **two** Agent Registries in its
`registries` list:

```yaml
registries:
  - //agentregistry.googleapis.com/projects/PROJECT_NUMBER/locations/global
  - //agentregistry.googleapis.com/projects/PROJECT_NUMBER/locations/us-central1
```

### Network Services Validation Invariants

As codified in Network Services CLH (`agent_gateway.go`):

1.  **Cardinality**: Maximum of two registries are permitted (`len(registries)
    <= 2`).
2.  **Mandatory Global Invariant**: If two registries are configured, **exactly
    one of them MUST be `global`** (`globalRegistryCount == 1`).
3.  **Second Registry Scope**: The second registry can be either **`regional`**
    (e.g. `us-central1`, `europe-west2`) OR **`multi-regional`** (e.g. `us`,
    `eu`).
4.  **Disallowed Configurations**:
    -   Two regional registries (e.g., `us-central1` + `us-east4`): Rejected.
    -   Two multi-regional registries (e.g., `us` + `eu`): Rejected.
    -   One regional + one multi-regional (without `global`): Rejected.
    -   Two global registries: Rejected.

Validation error message on violation:

```text
maximum of two registries are supported. When it's two registries, one of them must be global, the other one can be either regional or multi-regional.
```

--------------------------------------------------------------------------------

## 5. Cloud NAT & Egress IP Mechanics

In `ALL_TRAFFIC` mode, outbound traffic behavior depends on the target domain:

```
[ Reasoning Engine ]
         │
         ▼ (Captured via PSC-I)
[ Consumer PSC-I Subnet ] ── (10.20.1.0/24)
         │
         ├─── Google APIs / *.run.app ──────► [ Andromeda SDN / PGA ] ──► [ GFE ]
         │
         └─── External Internet / SaaS ─────► [ Cloud Router + NAT ]  ──► [ Static IP: 104.198.107.35 ]
```

### 5.1 Routing Path

-   Packets exit the Network Attachment on the consumer subnet
    (`agw-psci-subnet`, e.g. `10.20.1.0/24`).
-   VPC route tables direct internet-bound CIDRs (`0.0.0.0/0`) through the
    default internet gateway via Cloud Router (`agw-nat-router`) and Cloud NAT
    (`agw-cloud-nat`).

### 5.2 Deterministic Static Egress IP

-   Outbound calls to external destinations (`api.weather.gov`, `api.ipify.org`,
    SaaS APIs) are Source-NAT'd (SNAT) to the Cloud NAT static regional IP:
    -   `us-west1`: `104.198.107.35`
    -   `europe-west2`: `34.105.226.120`
    -   `us-central1`: `34.70.242.40`
    -   `us-east7`: `34.161.2.27`

### 5.3 Google Front End (GFE) Bypass Exception

-   Traffic directed to Google APIs or Cloud Run default domains (`*.run.app`)
    does **not** exit through Cloud NAT.
-   Google's Andromeda SDN intercepts traffic matching Google IP ranges and
    routes it internally to GFE via Private Google Access, bypassing the Cloud
    NAT static external IP.

--------------------------------------------------------------------------------

## 6. Downstream Secure Web Proxy (SWP) & Policy-Based Routing (PBR)

When enterprise architecture requires deploying a downstream Secure Web Proxy
(SWP) behind an Agent Gateway Network Attachment (for Layer 7 URL filtering,
domain allowlisting, or egress security inspection), specific Google Cloud
networking designs and platform constraints apply:

### 6.1 Platform Constraints & Routing Incompatibilities

1.  **PSC Endpoints Cannot Be Route Next-Hops**: Google Cloud route tables (both
    static routes and Policy-Based Routes) do **not** permit Private Service
    Connect (PSC) forwarding rules (`--load-balancing-scheme=""`) or service
    attachments as next hops.
2.  **SWP via PSC Service Attachment Lacks Next-Hop Mode**: Publishing Secure
    Web Proxy behind a PSC Service Attachment operates strictly in **Explicit
    Proxy Mode** (`HTTP CONNECT`). Packets emerging from an Agent Gateway
    Network Attachment are Layer 3/Layer 4 transparent IP packets; they cannot
    reach an explicit proxy without client-side proxy configuration (which
    Vertex AI Reasoning Engine containers do not support).
3.  **Static Route Tag Defect**: Next-hop routing mode with static routes
    requires VM network instance tags (`--tags`). Network Attachments
    (`compute network-attachments`) are consumer network interfaces, not VM
    instances, and cannot bear network tags. Consequently, static routes cannot
    match or steer traffic originating from an Agent Gateway Network Attachment.

### 6.2 The Working Architecture: Next-Hop SWP + Policy-Based Routing (PBR)

The only functional pattern for intercepting transparent egress from an Agent
Gateway Network Attachment is deploying Next-Hop SWP directly in the consumer VPC
and steering traffic via Policy-Based Routing:

```
[ Reasoning Engine ]
         │
         ▼ (ACT vpcEgress: ALL_TRAFFIC)
[ Agent Gateway ]
         │
         ▼ (PSC-I / Network Attachment, e.g. 10.20.1.0/24)
[ Consumer Subnet ]
         │
         ├──► [ Policy-Based Route (PBR) ] ── (Matches source: 10.20.1.0/24, dest: 0.0.0.0/0)
         │          │
         │          ▼ (Priority 200: next-hop-ilb-ip = 10.20.1.250)
         │    [ Next-Hop SWP Gateway (SECURE_WEB_GATEWAY) ]
         │          │
         │          ▼ (Proxy-Only Subnet: REGIONAL_MANAGED_PROXY /26)
         │    [ GatewaySecurityPolicy (L7 CEL Rules) ]
         │          │
         │          ▼ (Outbound Internet SNAT)
         │    [ Cloud NAT (ENDPOINT_TYPE_SWG) ]
         │          │
         │          ▼
         │    [ External Internet / Public APIs ]
         │
         └──► [ Fallback PBR (Priority 1000) ] ──► DEFAULT_ROUTING
```

#### 1. SWP Gateway Deployment

SWP must be deployed directly in the consumer VPC with `type: SECURE_WEB_GATEWAY`
and `routingMode: NEXT_HOP_ROUTING_MODE`, listening on an internal IP on the
PSC-I subnet (e.g. `10.20.1.250`):

> [!IMPORTANT]
> The Cloud Control Forwarding Engine API requires `type: SECURE_WEB_GATEWAY`.
> Passing `type: SECURE_WEB_PROXY` is rejected by schema validation.

```yaml
# swp-gateway.yaml
name: projects/PROJECT_ID/locations/REGION/gateways/swp-gateway
type: SECURE_WEB_GATEWAY
routingMode: NEXT_HOP_ROUTING_MODE
addresses:
- 10.20.1.250
subnetwork: projects/PROJECT_ID/regions/REGION/subnetworks/agw-psci-subnet
gatewaySecurityPolicy: projects/PROJECT_ID/locations/REGION/gatewaySecurityPolicies/swp-policy
ports:
- 443
- 80
```

Import via GA gcloud CLI:

```bash
gcloud network-services gateways import swp-gateway \
  --source=swp-gateway.yaml \
  --location=REGION \
  --project=PROJECT_ID
```

#### 2. Envoy Proxy-Only Subnet Prerequisite

SWP runs on Envoy proxy instances managed within the consumer VPC. It requires a
dedicated proxy-only subnet in the target region:

```bash
gcloud compute networks subnets create swp-proxy-subnet \
  --purpose=REGIONAL_MANAGED_PROXY \
  --role=ACTIVE \
  --region=REGION \
  --network=VPC_NAME \
  --range=10.20.2.0/26 \
  --project=PROJECT_ID
```

#### 3. Policy-Based Routing (PBR) Configuration

Because Network Attachments cannot have VM network tags, routing must use PBR to
match traffic based on the source CIDR of the Network Attachment subnet:

-   **Primary PBR (`agw-psci-to-swp-pbr`, Priority 200)**: Matches source CIDR
    `10.20.1.0/24` (the PSC-I subnet where the Network Attachment resides) and
    destination `0.0.0.0/0`, steering traffic to the SWP internal IP:

    ```bash
    gcloud network-connectivity policy-based-routes create agw-psci-to-swp-pbr \
      --network=projects/PROJECT_ID/global/networks/VPC_NAME \
      --source-range=10.20.1.0/24 \
      --destination-range=0.0.0.0/0 \
      --next-hop-ilb-ip=10.20.1.250 \
      --priority=200 \
      --project=PROJECT_ID
    ```

-   **Default Fallback PBR (`agw-pbr-default-routing`, Priority 1000)**: Directs
    all non-matching or bypass traffic to default VPC routing:

    ```bash
    gcloud network-connectivity policy-based-routes create agw-pbr-default-routing \
      --network=projects/PROJECT_ID/global/networks/VPC_NAME \
      --destination-range=0.0.0.0/0 \
      --priority=1000 \
      --project=PROJECT_ID
    ```

#### 4. L7 Policy Enforcement (GatewaySecurityPolicy)

Security rules attached to the SWP Gateway evaluate outbound traffic using CEL
(Common Expression Language) expressions. Rules allow authorized domains and
deny unauthorized traffic with HTTP 403 or TLS connection drops:

```cel
inUrlList(host(), ['api.weather.gov', 'api.ipify.org', 'REGION-aiplatform.mtls.googleapis.com'])
```

Or explicit hostname equality:

```cel
host() == 'api.weather.gov' || host() == 'api.ipify.org' || host() == 'REGION-aiplatform.mtls.googleapis.com'
```

#### 5. Cloud NAT Interdependence (`ENDPOINT_TYPE_SWG`)

SWP Envoy proxy instances reside on the private proxy-only subnet and do not
possess external IP addresses. Outbound internet egress from SWP requires Cloud
NAT configured on the Cloud Router with:

```bash
gcloud compute routers nats update agw-cloud-nat \
  --router=agw-nat-router \
  --region=REGION \
  --endpoint-types=ENDPOINT_TYPE_VM,ENDPOINT_TYPE_SWG \
  --project=PROJECT_ID
```

> [!NOTE]
> In `NEXT_HOP_ROUTING_MODE`, the Google Cloud SWP control plane may
> autogenerate `swg-autogen-router-{network_id}` and `swg-autogen-nat` with
> `natIpAllocateOption: AUTO_ONLY` and `ENDPOINT_TYPE_SWG`. Outbound public IP
> queries (e.g. `api.ipify.org`) reflect the SWG NAT's auto-allocated public
> IPs rather than the VPC's manual Cloud NAT static IP, validating end-to-end
> traversal through SWP.

#### 6. The ADK Streaming Query Session Trap (HTTP 503)

In `ALL_TRAFFIC` ACT mode, Vertex AI Agent Development Kit (ADK) streaming
queries attempt to initialize session state via:

```text
POST https://<region>-aiplatform.mtls.googleapis.com/v1beta1/projects/.../locations/<region>/reasoningEngines/...:createSession
```

Because `ALL_TRAFFIC` forces all runtime egress through the Agent Gateway into
the Network Attachment, PBR intercepts and routes this session creation call to
SWP. If `<region>-aiplatform.mtls.googleapis.com` is omitted from the
GatewaySecurityPolicy allowlist, SWP rejects it (`default_denied`). This causes
ADK streaming queries to crash with:

```text
HTTP 503 Service Unavailable: remote connection failure
```

while direct, non-streaming `query()` calls (which do not create sessions)
continue to succeed. Always allowlist the regional aiplatform endpoint in SWP!

#### 7. Observability & Log Verification

SWP logs are emitted under resource type `networkservices.googleapis.com/Gateway`:

```
resource.type="networkservices.googleapis.com/Gateway"
resource.labels.gateway_name="swp-gateway"
resource.labels.location="REGION"
```

Verify `enforcedGatewaySecurityPolicy.matchedRules` with `action` (`ALLOWED` vs
`DENIED`), `tlsSniHostname`, `clientIp`, and `httpRequest.status`.

--------------------------------------------------------------------------------

## 7. Delegated Authorization & Service Extensions

Agent Gateway delegates authorization decisions to extensions via
`networksecurity.googleapis.com/v1/.../authzPolicies`.

### 7.1 API Namespace Requirement

Always use `authzPolicies`
(`networksecurity.googleapis.com/v1/.../authzPolicies`). The legacy
`authorizationPolicies` collection rejects `target`, `policyProfile`, and
`customProvider`.

### 7.2 IAP Unified Access Policy (UAP / Policy V2) Extension

To enable UAP Policy V2 evaluation:

-   The IAP Authz Extension (`iap.googleapis.com`) must set:

    ```yaml
    metadata:
      iapPolicyVersion: "V2"
      iamEnforcementMode: "DRY_RUN"  # or "ENFORCED"
    ```
-   Rules must use FQDN permission: `iap.googleapis.com/resources.egressViaIAP`.
-   CEL clauses must be parenthesized for Cloud Console parsing:
    `((destination.agent_registry.mcp_server.method == 'tools') &&
    (destination.agent_registry.mcp_server.tool.annotations.read_only_hint ==
    true))`.

### 7.3 Model Armor Content Authorization (`CONTENT_AUTHZ`)

Bound via AuthzPolicy with `policyProfile: CONTENT_AUTHZ` for `application/json`
and `text/*`:

-   Data-plane inspection calls execute regionally against:
    `modelarmor.REGION.rep.googleapis.com`.
-   Metadata passes JSON-encoded `request_template_id` and
    `response_template_id` to sanitize prompts and responses against injection,
    jailbreaks, and sensitive data.
-   Service agent `service-{PROJECT_NUMBER}@gcp-sa-dep.iam.gserviceaccount.com`
    requires:
    -   `roles/modelarmor.calloutUser` (gateway project)
    -   `roles/serviceusage.serviceUsageConsumer` (gateway project)
    -   `roles/modelarmor.user` (Model Armor template project)

--------------------------------------------------------------------------------

## 8. Logging, Monitoring, & Tracing

### 8.1 Monitored Resource

```sql
resource.type="networkservices.googleapis.com/Gateway"
resource.labels.location="REGION"
resource.labels.gateway_name="AGENT_GATEWAY_NAME"
```

### 8.2 Inspecting L7 Egress Decisions

```bash
# Pull recent gateway logs
gcloud logging read   'resource.type="networkservices.googleapis.com/Gateway" AND resource.labels.gateway_name="AGENT_GATEWAY_NAME"'   --limit=20 --freshness=1h --format=json

# Filter on 4xx/5xx errors
gcloud logging read   'resource.type="networkservices.googleapis.com/Gateway" AND resource.labels.gateway_name="AGENT_GATEWAY_NAME" AND httpRequest.status>=400'   --limit=20 --freshness=1h
```

### 8.3 Synthetic PSC VIP (`240.0.0.2:443`) in Logs

In environments using PSC and ACT `ALL_TRAFFIC`, the outer tunnel request
appears as `CONNECT 240.0.0.2:443`. The SWP proxy terminates TLS using the
regional CA cert (`Agent Gateway TLS Inspection CA ($REGION)`), extracts the
inner SNI hostname, and evaluates routing against Agent Registry entries. If the
inner hostname is unregistered, SWP drops the connection with `default_denied`.

--------------------------------------------------------------------------------

## 9. Common Failure Modes & Troubleshooting Guide

| Symptom                | Root Cause                                        | Remediation                                                  |
| :--------------------- | :------------------------------------------------ | :----------------------------------------------------------- |
| Gateway mutation fails | CCFE resource lock active from concurrent         | Wait 1.5–2 minutes for in-flight LRO to complete before      |
: with HTTP 409          : PATCH/import                                      : retrying.                                                    :
: `ABORTED\: unable to   :                                                   :                                                              :
: queue the operation`   :                                                   :                                                              :
| External outbound API  | Consumer VPC subnet lacks Cloud NAT               | Deploy Cloud Router and Cloud NAT on the PSC-I subnet.       |
: requests hang or       :                                                   :                                                              :
: timeout under ACT      :                                                   :                                                              :
| Gateway creation fails | Attempted 2 registries without a `global` entry,  | Ensure exactly one registry is `global` and the second is    |
: with validation error  : or 2 regionals                                    : `regional` or `multi-regional`.                              :
: on `registries`        :                                                   :                                                              :
| ACT deletion fails     | Dangling reference `FROM_AGENT_GATEWAY<ID>`       | Delete reference using stubby: `stubby call                  |
: with HTTP 400          : retained by Network Services                      : blade\:network-services-prod-${REGION}                       :
: `Resource ... is       :                                                   : google.internal.cloud.reference.References.DeleteReference`. :
: already being used`    :                                                   :                                                              :
| Reasoning Engine       | Existing active Reasoning Engine already bonded   | Delete existing bonded Reasoning Engine before creating a    |
: deployment fails with  : in project/region                                 : new one.                                                     :
: Code 13 or internal    :                                                   :                                                              :
: error                  :                                                   :                                                              :
| Reasoning Engine       | Active agent sessions exist on the engine         | Pass `?force=true` query parameter to the delete REST call.  |
: deletion fails with    :                                                   :                                                              :
: HTTP 400               :                                                   :                                                              :
: `FAILED_PRECONDITION`  :                                                   :                                                              :
| REST API egress        | CEL rule uses strict prefix matching without      | Expand CEL condition to explicitly permit `.json`, `/`, and  |
: returns 403 when       : accounting for `.json`                            : `?` query strings.                                           :
: requesting             :                                                   :                                                              :
: `/api/v2/tickets.json` :                                                   :                                                              :
| Gateway creation fails | Race condition in claiming prewarmed units        | Retry creation after tenant project policy propagation.      |
: in region with Code 13 : lacking                                           :                                                              :
: (NCC Regional Endpoint : `roles/networkconnectivity.regionalEndpointAdmin` :                                                              :
: permission)            :                                                   :                                                              :

--------------------------------------------------------------------------------

## 10. Reference Gateway & ACT Specification

### Agent Connectivity Template YAML

```yaml
name: projects/agw-vpc-sc-prod-testing/locations/us-west1/agentConnectivityTemplates/agw-all-traffic-template
accessPath: AGENT_TO_ANYWHERE
accessTypes:
  - PRIVATE
deploymentModel: CENTRALIZED
egressNetworkConfig:
  dnsPeeringConfig:
    domain: corp.internal.
    targetNetwork: projects/agw-vpc-sc-prod-testing/global/networks/agw-consumer-vpc
  networkAttachment: projects/agw-vpc-sc-prod-testing/regions/us-west1/networkAttachments/agw-network-attachment
  vpcEgress: ALL_TRAFFIC
```

### Agent Gateway YAML

````yaml
name: projects/agw-vpc-sc-prod-testing/locations/us-west1/agentGateways/agw-prod-gateway
agentConnectivityTemplate: projects/811052751994/locations/us-west1/agentConnectivityTemplates/agw-all-traffic-template
googleManaged:
  governedAccessPath: AGENT_TO_ANYWHERE
protocols:
  - MCP
registries:
  - //agentregistry.googleapis.com/projects/811052751994/locations/global
  - //agentregistry.googleapis.com/projects/811052751994/locations/us-west1
agentGatewayCard:
  mtlsEndpoint: projects/vda7b564705d49e44p-tp/regions/us-west1/serviceAttachments/unitkind1-swp-mtls-psc-sa
  tlsInspectionCA: Agent Gateway TLS Inspection CA (us-west1)
```\n
```

--------------------------------------------------------------------------------

## 11. Cross-Project Centralized Governance Architecture

Following the `EnableCrossProjectAgentGateway_Default_Launch` rollout, Google Cloud's Agent Platform officially supports cross-project bindings from Agent Runtimes (Vertex AI Reasoning Engines) in consumer projects to an Agent Gateway in a Centralized Governance Project.

### 11.1 Three-Tier Project Boundary Model

To separate policy authoring, network security, and runtime execution, enterprise deployments structure responsibilities across three distinct project tiers:

1.  **Centralized Governance Project (`AGW_PROJECT_ID`):**
    -   **Agent Registry (`agentregistry.googleapis.com`):** The authoritative catalog of vetted MCP tools, endpoints, and peer agents. Both Gateway and Registry **MUST** reside in this same project.
    -   **Agent Gateway (`networkservices.googleapis.com`):** The managed `AGENT_TO_ANYWHERE` gateway instance mediating all outbound agent traffic.
    -   **Model Armor Templates & IAP/UAP Policies:** Guardrails for request/response DLP inspection, prompt injection detection, and CEL-based authorization.
    -   **Network Infrastructure:** Egress VPC with a Private Service Connect (PSC-I) Network Attachment, Cloud NAT, and Private DNS peering.
2.  **Agent Runtime Projects (`AE_PROJECT_NUMBER`):**
    -   **Vertex AI Reasoning Engines:** Containerized agent execution environments deployed by product/application teams.
    -   **Cross-Project Gateway Binding:** Configured in `agentGatewayConfig.agentToAnywhereConfig.agentGateway` pointing directly to the central gateway resource.
    -   **No Local VPC Required:** Runtimes do not require local VPCs or interconnects; all private traffic tunnels into the governance project's network attachment.
3.  **MCP & Service Backend Projects (`MCP_PROJECT_ID`):**
    -   Host business logic, databases, or microservices (Cloud Run, GKE, Compute Engine). Services are explicitly registered into the central Agent Registry catalog.

### 11.2 Cross-Project IAM Requirements

To bind an Agent Runtime to an Agent Gateway in another project, permissions must be granted to both the deploying caller and the runtime project's service agent:

1.  **Deploying Principal (Developer EUC, Cloud Build, or CI/CD Service Account):**
    The caller invoking `client.agent_engines.create` or Vertex AI REST API must hold:
    -   `networkservices.agentGateways.get`
    -   `networkservices.agentGateways.use`
    (or role `roles/networkservices.viewer`) on `projects/AGW_PROJECT_ID/locations/REGION/agentGateways/GATEWAY_NAME`.
2.  **Vertex AI Service Agent:**
    Grant access to the runtime project's service agent on the governance project (`AGW_PROJECT_ID`):

    *Option A: Predefined Role (Network Services Viewer):*
    ```bash
    gcloud projects add-iam-policy-binding "AGW_PROJECT_ID" \
      --member="serviceAccount:service-AE_PROJECT_NUMBER@gcp-sa-aiplatform.iam.gserviceaccount.com" \
      --role="roles/networkservices.viewer"
    ```

    *Option B: Least-Privilege Custom Role:*
    ```bash
    gcloud iam roles create ae_agw_cross_project_sa \
      --project="AGW_PROJECT_ID" \
      --title="AE AGW Cross Project SA" \
      --description="Custom role for cross-project service agent to access Agent Gateways and Operations" \
      --permissions="networkservices.agentGateways.get,networkservices.operations.get" \
      --stage="GA"

    gcloud projects add-iam-policy-binding "AGW_PROJECT_ID" \
      --member="serviceAccount:service-AE_PROJECT_NUMBER@gcp-sa-aiplatform.iam.gserviceaccount.com" \
      --role="projects/AGW_PROJECT_ID/roles/ae_agw_cross_project_sa"
    ```

### 11.3 Agent Runtime Deployment Specification

Deploy the agent runtime referencing the central gateway and disabling bound token sharing via CAA opt-out:

```python
remote_agent = client.agent_engines.create(
    agent=local_agent,
    config={
        "agent_gateway_config": {
            "agent_to_anywhere_config": {
                "agent_gateway": (
                    "projects/AGW_PROJECT_ID/locations/REGION/agentGateways/AGENT_GATEWAY_TO_ANYWHERE_NAME"
                )
            },
        },
        "identity_type": types.IdentityType.AGENT_IDENTITY,
        "env_vars": {
            # Opt out of default Context-Aware Access (CAA) token binding to allow
            # token validation through cross-project gateway egress
            "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": False,
        },
    },
)
```

> [!WARNING]
> **Regional Colocation Invariant:** The Agent Runtime and the Agent Gateway **MUST reside in the exact same Google Cloud region** (e.g., `us-central1`). Cross-region bindings fail control plane validation with `INVALID_ARGUMENT`.

### 11.4 Two-Tier Registry Authorization Model

1.  **Tier 1: Baseline Google APIs Access (Coarse-Grained via `principalSet://`):**
    All runtimes require baseline access to Google Cloud APIs for discovery, telemetry, and platform operations. Register the consolidated `googleapis` endpoint in the central registry and authorize runtimes using project-level or trust-domain-wide principal sets:
    -   *Project-Level Scope:* `principalSet://TRUST_DOMAIN/attribute.platformContainer/aiplatform/projects/AE_PROJECT_NUMBER`
    -   *Trust-Domain-Wide Scope:* `principalSet://TRUST_DOMAIN/*`
    -   *Role:* `roles/iap.egressor` on the Google APIs endpoint.
    > [!IMPORTANT]
    > **Folder-Level Syntax Limitation:** `attribute.platformContainer/aiplatform/folders/FOLDER_NUMBER` is **not an indexed attribute** in IAM SPIFFE URI evaluation. Scoping must strictly use project-level syntax or Principal Access Boundary (PAB) policies.
2.  **Tier 2: Granular MCP Tools & Peer Agents (Fine-Grained via `principal://`):**
    For sensitive business tools, grant `roles/iap.egressor` on the specific MCP server resource directly to the individual Reasoning Engine:
    `principal://TRUST_DOMAIN/resources/aiplatform/projects/AE_PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID`
    Optionally restrict with CEL conditions:
    ```cel
    api.getAttribute('iap.googleapis.com/mcp.tool.isReadOnly', false) == true || api.getAttribute('iap.googleapis.com/mcp.toolName', '') == ''
    ```

### 11.5 Server-Generated Resource ID Extraction Pattern

In Agent Registry, all write operations use `gcloud agent-registry services create`. However, IAP authorization policies **must be bound to the server-generated resource ID** (`agentregistry-00000000-...`), NOT the friendly service name:

```bash
# 1. Fetch server-generated resource ID
REGISTRY_RESOURCE=$(gcloud agent-registry services describe MCP_SERVICE_NAME \
  --project="AGW_PROJECT_ID" \
  --location="REGION" \
  --format='value(registryResource)')

RESOURCE_ID=$(basename "$REGISTRY_RESOURCE")

# 2. Grant roles/iap.egressor on iap_web/agentRegistry/mcpServers/{RESOURCE_ID}
gcloud iap web add-iam-policy-binding \
  --project="AGW_PROJECT_ID" \
  --region="REGION" \
  --resource-type=agent-registry \
  --mcp-server="${RESOURCE_ID}" \
  --member="principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/AE_PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID" \
  --role="roles/iap.egressor"
```

### 11.6 VPC Service Controls (VPC-SC) Perimeter Bridging

If the Centralized Governance Project (`AGW_PROJECT_ID`) and the Agent Runtime Projects (`AE_PROJECT_NUMBER`) reside in different VPC-SC service perimeters:
1.  Establish a **Perimeter Bridge** connecting both perimeters, OR
2.  Configure explicit **Ingress and Egress Rules** on both perimeters permitting cross-perimeter communication for:
    -   `aiplatform.googleapis.com`
    -   `networkservices.googleapis.com`
    -   `agentregistry.googleapis.com`
````
