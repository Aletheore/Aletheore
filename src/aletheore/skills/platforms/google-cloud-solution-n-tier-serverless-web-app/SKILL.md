---
name: google-cloud-solution-n-tier-serverless-web-app
metadata:
  version: 1.0.0
  category: MultiProductSolutions
description: Assists in designing and implementing secure n-tier serverless web applications and microservices on Google Cloud. Use when users need architecture designs, security checklists, Terraform code, or deployment guidance for multi-tier serverless apps, regional data residency / European sovereignty compliance, zero-trust private VPC networking, or Private Service Connect. Don't use for VM, GKE, or non-Google Cloud architectures.
skill_id: platforms/google-cloud-solution-n-tier-serverless-web-app
title: Google Cloud Solution N Tier Serverless Web App
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-solution-n-tier-serverless-web-app
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/google-cloud-solution-n-tier-serverless-web-app", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/google-cloud-solution-n-tier-serverless-web-app", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 4, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["mcp:developer-knowledge", "mcp:optional", "subagents"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

<!-- disableFinding(all) -->
<!-- mdlint off -->

# Secure n-tier serverless web application with strict private application tiers

This skill guides agents through the workflow of designing and implementing a
secure serverless web application with as many architectural design layers as
specified by the user. It uses Cloud Run for the serverless layers and Cloud SQL
for PostgreSQL as the data layer. A three-tier web application might be
represented in three architectural layers: a Cloud Run presentation layer, a
Cloud Run application layer, and a Cloud SQL for PostgreSQL database layer.

The architecture enforces strict physical and network isolation across all tiers (T1 to TN):

*   **Tier 1 presentation tier (frontend / reverse proxy)**: Public-facing UI rendering/gateway service (Cloud Run). Exposes the entry point via Cloud Load Balancing and routes requests downstream to internal tiers privately via Direct VPC Egress.
*   **Tier 2..N application tier (internal microservices / business logic)**: Private application services (Cloud Run). 100% isolated from the internet (Ingress: VPC-internal, `INGRESS_TRAFFIC_INTERNAL_ONLY`), reachable exclusively via upstream VPC routing (`egress = "ALL_TRAFFIC"` with Private Google Access on the subnet for `*.run.app` URLs).
*   **Data tier**: Private Cloud SQL for persistent data and Memorystore for
    Redis for caching, reachable exclusively from authorized application tiers.

## General guidance to the LLM

### 1. Direct Resource Map (Zero-Search File Access)
All necessary reference architectures, HCL templates, and checklists are co-located in this skill. Use exact relative paths from this skill folder:

| Asset Path | Purpose & Usage |
| :--- | :--- |
| `assets/main.tf` | **Single Source of Truth for Terraform (HCL)**. Contains all security boundaries, Cloud Run v2 configs, PSC endpoints, DNS private zones, and firewall rules. |
| `assets/output-template.md` | Standardized Solution Architecture report markdown structure. |
| `references/non-negotiable-architectural-rules.md` | Non-negotiable security rules, audit checklist, and product mappings. |
| `references/related-guidance.md` | Supplemental deep reference (do NOT read for standard design or IaC tasks; read only if specialized edge-case troubleshooting is explicitly required). |

- **No Directory Crawling**: Do NOT run `list_dir` chains down workspace directories to discover these files.
- **No Search Thrashing on Local Files**: Do NOT run `code_search` or `find_by_name` queries to look inside `assets/main.tf`. Read the file directly using `view_file` once and reuse the context.
- **No Redundant Skill Searches**: Do NOT call `skill_search` for serverless or n-tier architecture skills while executing this skill.

### 2. Direct Inline Generation (No Subagent Delegation)
- Perform all architecture compilation, Terraform drafting, `gcloud` command assembly, and validation script generation **directly in the primary conversation**.
- Do **NOT** invoke subagents (`invoke_subagent`) to research external GitHub Terraform modules, probe environment configs, or draft reports. All required patterns are fully contained in `assets/main.tf` and `references/`.

### 3. One-Shot Clean Artifact Writing
- Generate complete, fully-rendered, and valid HCL blocks and Markdown reports in a single `write_to_file` call.
- Avoid leaving placeholders or malformed code fences that require multi-turn `replace_file_content` and `grep_search` patch loops.
- **No Unpopulated Placeholders**: When embedding code or scripts inside architecture reports (e.g., Section 6 of `assets/output-template.md`), always inline the actual complete Terraform code, gcloud commands, and validation script code. Never output literal template placeholder comments (e.g., `# [Paste of main.tf file contents]`).
- **In-Response Direct Rendering (Mandatory)**: Whenever Terraform code, deployment scripts, or architecture reports are requested or generated (e.g., "provide a design and Terraform code", "generate IaC"), you **MUST print the complete generated ```terraform ... ``` HCL code block and full solution report directly in your chat response text**, in addition to writing them to files on disk. Never output only an architectural design summary or file links when code is requested; automated evaluation frameworks (such as Yardstick) evaluate the raw response text and fail all code assertions if the ```terraform``` code block is missing from the message.

### 4. Technical Completeness Checklist
- When providing a concise architecture summary or security checklist (e.g., when instructed not to generate full IaC), you MUST explicitly include the following technical specifications:
    - For regional load balancer deployments: regional proxy-only subnet purpose (`REGIONAL_MANAGED_PROXY`) and `network` parameter on regional forwarding rules.
    - Cloud SQL PostgreSQL version (`POSTGRES_18`), Edition (`Enterprise Edition`), High Availability (`Regional HA`), and Private Service Connect (`psc_enabled = true`).
    - Cloud NGFW Firewall Policies:
        - MUST configure explicit Cloud NGFW network firewall policies (`google_compute_network_firewall_policy`, `google_compute_network_firewall_policy_association`, and `google_compute_network_firewall_policy_rule` with `enable_logging = var.enable_monitoring`) rather than legacy `google_compute_firewall`.
        - Enforce default egress deny (`0.0.0.0/0`).
        - Allow frontend egress to backend / PGA VIPs.
        - Allow backend database egress explicitly permitting TCP port `443` to Private Google Access VIPs (`199.36.153.4/30 / 199.36.153.8/30`) in addition to TCP port `5432` so the Cloud SQL Auth Proxy sidecar can query `sqladmin.googleapis.com` on startup for IAM certificate exchange.

## Workflow

> [!TIP]
> **Optional MCP Server Integration**: If your AI coding client supports the **Model Context Protocol (`MCP`)**, you can connect the [Google Developer Knowledge MCP Server](https://developers.google.com/knowledge/mcp) (`npx -y @google/mcp-developer-knowledge-server`) to dynamically query real-time Google Cloud documentation (`cloud.google.com/docs`) alongside this skill's offline knowledge base (`references/related-guidance.md`).

The solution design and implementation workflow is divided into the following
phases:

*   **Phase 1: Requirements discovery and analysis**: Analyze the workload's
    requirements, constraints, dependencies, and current state.
*   **Phase 2: Solution design & IaC drafting**: Build a technology stack, architecture, and deployment configuration for the workload. **IMPORTANT: You should offer to generate the complete Terraform code (based on `assets/main.tf` and adhering to all Phase 3 specifications) alongside the solution architecture during this phase.** This allows the user to immediately review and iteratively modify the code as the conversation continues. However, if the user explicitly states they do not want code, do not generate it yet.
*   **Phase 3: Implementation plan & iterative refinement**: Modify and refine the generated design and deployment instructions as the conversation and user feedback evolve.
*   **Phase 4: Solution validation**: Validate that the deployment meets the
    requirements of the workload.

--------------------------------------------------------------------------------

### Phase 1: Requirements discovery and analysis

To prevent multi-turn interview fatigue and maintain trajectory determinism across evaluations, adopt an **opinionated 80% default golden path** unless the user explicitly requests deviations:

1.  **Default Golden Path Configuration (`80% Baseline`)**:
    *   **Architecture**: Secure 3-tier serverless pipeline (`frontend` Cloud Run -> `backend application` Cloud Run -> `Cloud SQL PostgreSQL`).
    *   **Region**: `us-central1`.
    *   **Database**: Cloud SQL for PostgreSQL (`POSTGRES_18`) Enterprise Edition via Private Service Connect (`psc_enabled = true`).
    *   **Edge Protection**: Global external Application Load Balancer with Cloud Armor WAF (`sqli-v33-stable`) and Cloud CDN (`enable_cdn = true`).
    *   **Domain & SSL Mode**: If the user specifies a domain (e.g., `app.mycompany.com`), configure `var.domain_name` with a Google-managed certificate (`use_self_signed_cert = false`) and provide DNS `A` record instructions. If testing in a sandbox without a domain, enable self-signed mode (`use_self_signed_cert = true`) for immediate testability.
    *   **Networking & Security**: Direct VPC Egress (`ALL_TRAFFIC`), `run.app.` Cloud DNS private zone, least-privilege Cloud NGFW egress firewall policies (`TCP 5432, 443`), and Cloud SQL Auth Proxy sidecar (`DB_SOCKET_PATH` with IAM Auth).

2.  **Disambiguation Protocol (`Optional Clarification Questions`)**:
    If the user's initial prompt leaves requirements open-ended (and is not fast-forwarding with exact specs), do **not** present a multi-topic questionnaire. Only ask concise clarifying questions as needed before confirmation:
    1.  **Load Balancer & Residency Topology**: Do you require a **Global Application Load Balancer with Cloud CDN** (default for worldwide users), or a **Regional Application Load Balancer without CDN** (for strict EU/regional data residency compliance)?
    2.  **Custom Domain vs. Sandbox Testing**: Do you have a **registered domain name** to configure with a Google-managed certificate, or should we configure **self-signed testing mode** (`use_self_signed_cert = true`) for immediate sandbox testing over IP?
    3.  **In-Memory Caching Tier**: Should we provision an optional **Memorystore for Redis** caching tier (`Private Services Access`) alongside Cloud SQL to accelerate read queries?

3.  **Verify & Confirm**: Present the confirmed 3-tier golden path decomposition to the user and request confirmation before proceeding to Phase 2 (or fast-forward automatically when instructed).

### Phase 2: Solution design

1.  **Retrieve Architectural Guidance Efficiently**:
    *   **Architecture Design & Security Checklist Requests**: Retrieve ONLY the 9 architectural security boundaries and audit checklist from `references/non-negotiable-architectural-rules.md`. Do **NOT** retrieve `references/related-guidance.md` or `assets/main.tf` when only high-level design/checklists are requested without full Terraform code.
    *   **Terraform Implementation Requests**: Retrieve `references/non-negotiable-architectural-rules.md` and `assets/main.tf` (Single Source of Truth for exact HCL). Do **NOT** retrieve `references/related-guidance.md` unless specialized edge-case troubleshooting is explicitly required.

2.  **Map components to Google Cloud products**: Map your confirmed decomposition directly to Google Cloud products using these mandatory product mapping specifications:
    *   **Public Ingress & WAF**: global or regional external Application Load Balancer (`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`), Cloud Armor (`sqli-v33-stable`), Cloud CDN (if global Application Load Balancer; note that Cloud CDN is NOT supported on regional Application Load Balancers). Note: When deploying a regional external Application Load Balancer, an explicit proxy-only subnet (`purpose = "REGIONAL_MANAGED_PROXY"`) is required in the VPC and `network` must be specified on the regional forwarding rule.
    *   **Internal compute tiers (T1 to TN)**: Cloud Run microservices (`INGRESS_TRAFFIC_INTERNAL_ONLY`), Direct VPC Egress configured with `egress = "ALL_TRAFFIC"`, Private Google Access enabled on the subnet, and a Cloud DNS Managed Private Zone (`google_dns_managed_zone`) for `run.app.` bound to `vpc_network` mapping `*.run.app` directly to Private Google Access VIPs (`199.36.153.4/30 / 199.36.153.8/30`) when calling internal `*.run.app` URLs, deployed from specified/placeholder container image.
    *   **Private data tier**: Cloud SQL for PostgreSQL (`POSTGRES_18`) via Private Service Connect + IAM DB Auth. If database caching was selected, add Memorystore Redis via Private Services Access. Depending on reliability requirements, specify either "Cloud SQL for PostgreSQL Enterprise edition instance" or a "Cloud SQL for PostgreSQL Enterprise Plus edition instance".
    *   **Secrets, Registry, & Security**: Secret Manager, Artifact Registry, Cloud NGFW global/regional network firewall policies (`google_compute_network_firewall_policy` + `google_compute_network_firewall_policy_rule` explicitly permitting outbound TCP port 5432 to Cloud SQL PSC IP and TCP port 443 to Private Google Access VIPs `199.36.153.4/30, 199.36.153.8/30` on `allow_backend_db_egress`), and optional VPC Service Controls.

3.  **Create architecture diagram**: Create a clean Mermaid format architecture diagram (`assets/output-template.md`) illustrating the multi-tier request and data flow across entry point, public reverse proxy, private microservice compute tiers, and private database/caching endpoints.

4.  **Draft solution architecture and generate Terraform & `gcloud` CLI code**:
    Compile the requirements, technical decomposition, product mapping,
    architecture diagram, design recommendations, AND the complete
    Infrastructure as Code (`Terraform based on assets/main.tf alongside a
    self-contained sequence of gcloud CLI deployment commands adhering to all
    Phase 3 mandatory specifications`) into a single Markdown file structured
    strictly per the standardized Google Cloud Solution Architecture output
    When saving or outputting the report artifact, append an ISO 8601 UTC timestamp and ensure the filename strictly ends with the `.md` extension (`e.g., workload_name_architecture_report-20260701T212820Z.md`). Verify that all 9 security boundaries from `assets/main.tf` are documented cleanly in your report and all template placeholders are replaced with actual complete code blocks. In your response, provide the executive architecture overview, the 9 key security & network boundaries enforced, the complete deploy-ready Terraform code block (` ```terraform ... ``` `), and step-by-step deployment instructions with links to the generated artifacts (always inlining the complete Terraform code directly in the response when code is requested, rather than only providing file links).
5.  **Request review and iterate**: Present the solution architecture (and Terraform code, `gcloud` script, or validation script if generated) to the user and request feedback. Modify and refine both the architecture and code iteratively as the conversation continues.

--------------------------------------------------------------------------------

### Phase 3: Implementation plan

1.  **Retrieve relevant building block templates from the `assets/` directory**.
    *Important*: Use the code in `assets/main.tf` as the
    foundation for your Terraform implementation plan (`assets/output-template.md` for architecture structure).

2.  **Identify deployment prerequisites**:

    *   Required Google Cloud APIs (`run.googleapis.com`,
        `sqladmin.googleapis.com`, `redis.googleapis.com`,
        `servicenetworking.googleapis.com`, `secretmanager.googleapis.com`,
        `monitoring.googleapis.com`, `dns.googleapis.com`).
    *   Required IAM permissions (Project Editor, Security Admin, etc.).

3.  **Generate Infrastructure as Code (IaC) (`Architectural Specifications`)**: 
    Retrieve relevant architectural and hierarchy guidance from
    `references/non-negotiable-architectural-rules.md`. Base code strictly on the building blocks in
    `assets/main.tf` (`Section 1.5` for Cloud NGFW firewall policies, `Section 5.1` for Tier 1, `Section 5.2` for Tiers 2..N), ensuring `database_version = "POSTGRES_18"` is preserved exactly. Ensure firewall policies strictly use Cloud NGFW resources (`google_compute_network_firewall_policy`, `google_compute_network_firewall_policy_association`, and `google_compute_network_firewall_policy_rule` with `enable_logging = var.enable_monitoring`). NEVER generate legacy `google_compute_firewall` resources or revert to older database versions like `POSTGRES_15`.

4.  **Write deployment instructions & README.md**: Draft comprehensive step-by-step deployment instructions (or a complete `README.md` artifact), ensuring you include:
    *   Instructions to initialize and apply Terraform (`terraform init`, `terraform apply`). **Zero-Install Environment Recommendation**: Explicitly recommend running `terraform` commands and your generated automated validation script inside **Google Cloud Shell** (`https://shell.cloud.google.com`), where `python3`, `gcloud`, and `terraform` are 100% pre-installed and authenticated out of the box so developers without local SDKs can deploy and validate immediately.
    *   **Step-by-Step `gcloud` CLI Deployment Commands (`Bottom-Up Wiring`)**: In Section 6.3 ("Step-by-step gcloud CLI deployment commands") inside `assets/output-template.md`, provide a complete, self-contained sequence of `gcloud` CLI commands required to deploy this exact architecture without Terraform. These commands must enforce reverse/bottom-up order (`VPC/subnets -> data tiers -> internal microservices -> public gateway -> load balancer`), specify `--database-version=POSTGRES_18` when provisioning Cloud SQL, and extract downstream container URLs (`gcloud run services describe... --format='value(status.url)'`) into shell variables to dynamically pass them via `--update-env-vars` into upstream services.
    *   Explanation of all Terraform variables, **explicitly including and documenting the `enable_vpc_sc` and `use_self_signed_cert` variables** (explaining how setting `use_self_signed_cert = true` enables immediate sandbox verification via `curl -k https://<LB_IP>/` without waiting for DNS propagation).
    *   **Dedicated VPC Service Controls Guidance Section**: Provide specific instructions and gcloud commands for implementing an Org-level VPC-SC service perimeter around Cloud Run (`run.googleapis.com`), Cloud SQL (`sqladmin.googleapis.com`), and Secret Manager (`secretmanager.googleapis.com`) when `enable_vpc_sc = true`.
    *   Container image deployment strategies (specifying pre-existing image URLs or placeholder bootstrapping followed by CI/CD).
    *   Cloud DNS Managed Private Zone (`google_dns_managed_zone`) configuration for `run.app.` bound to `vpc_network`, mapping `*.run.app` directly to Private Google Access VIPs (`199.36.153.4/30 / 199.36.153.8/30`), alongside external DNS records and database schema initialization.

5.  **Request review**: Present the implementation plan to the user for
    approval. Iterate as needed.

--------------------------------------------------------------------------------

### Phase 4: Solution validation

1.  **Define solution verification steps & requirements**: During validation planning, mandate these 5 verification steps across any deployed environment:
    *   **SSL Provisioning**: Verify that the Google-managed SSL certificate (`google_compute_managed_ssl_certificate`) becomes `ACTIVE` (checking `--global` status or regional equivalents).
    *   **Frontend Ingress Block**: Verify that direct internet access targeting the Tier 1 Frontend's default `*.run.app` URL is blocked (`HTTP 403 Forbidden` from edge screening).
    *   **Backend Ingress Block**: Verify that direct internet access targeting internal compute tiers' `*.run.app` URLs (`INGRESS_TRAFFIC_INTERNAL_ONLY`) is blocked across all internal microservice tiers (`HTTP 404 Not Found` or `HTTP 403 Forbidden`).
    *   **Frontend Public Access via Application Load Balancer**: Verify that accessing the custom domain routes successfully to the presentation tier via the Application Load Balancer (`HTTP 200` to `399`).
    *   **Edge WAF Protection**: Verify that a simulated SQL injection request (`/?id=1%20OR%201=1` on the custom domain) is intercepted and blocked (`HTTP 403 Forbidden` from Cloud Armor).
    *   **Private Server-to-Server Connectivity**: Verify via Cloud Run application logs (`Logs Explorer`) and database connection pooling telemetry (`Cloud SQL Query Insights`) that tier 1 -> tier 2 -> data tier queries succeed over private VPC fiber (`Direct VPC Egress` + `Private Service Connect` / `Private Services Access`).

2.  **Generate tailored automated validation script**: Rather than relying on a static pre-packaged script, **generate a custom automated validation script** (e.g., self-contained Python validation script using standard built-in `urllib` / `subprocess` libraries, or a cross-platform bash/PowerShell script) customized precisely to the user's deployed domain, SSL certificate name, and exact multi-tier `*.run.app` URIs.

3.  **Provide cross-platform execution guidance**: Explain how the user can execute the generated script across their target OS (`macOS`, `Linux`, `Windows PowerShell`, or zero-install **Google Cloud Shell** (`https://shell.cloud.google.com`)).

4.  **Compile validation report**: Document the validation checks, the generated verification script code, execution commands, and expected outcomes in Section 6.4 ("Solution verification guide and custom automated validation script") inside `assets/output-template.md`.

5.  **Conduct validation and finalize**: Assist the user in running the generated verification script, inspecting logs, and troubleshooting any DNS or WAF propagation issues. Request final approval.
