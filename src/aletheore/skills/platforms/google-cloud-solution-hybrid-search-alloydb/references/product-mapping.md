# Product Mapping Guidance for Hybrid Search Solutions

Use the guidance in this file to do the following:

- Recommend appropriate products and features for the components of the hybrid
  search system
- Explain the rationale and trade-offs for each recommendation.

| Component | Product / Architecture Option | Line hints |
|-----------|--------------------------------|------------|
| [Database & Vector Search Store](#database-vector-search-store) | [AlloyDB for PostgreSQL (AlloyDB AI)](#alloydb-for-postgresql-alloydb-ai) (Primary) | Lines 24-39 |
| [Database & Vector Search Store](#database-vector-search-store) | [BigQuery Vector Search](#bigquery-vector-search) (Alt 1) | Lines 41-67 |
| [Database & Vector Search Store](#database-vector-search-store) | [Cloud SQL](#cloud-sql) (Alt 2) | Lines 69-86 |
| [Vector Embeddings Engine](#vector-embeddings-engine) | [Text embeddings on Gemini Enterprise Agent Platform](#text-embeddings-on-gemini-enterprise-agent-platform) (Primary) | Lines 90-98 |
| [Vector Embeddings Engine](#vector-embeddings-engine) | [Self-hosted embedding model](#self-hosted-embedding-model) (Alt 1) | Lines 100-109 |
| [Database Abstraction & Agentic Tooling](#database-abstraction-agentic-tooling) | [MCP Toolbox for Databases](#mcp-toolbox-for-databases) (Primary) | Lines 113-124 |
| [Database Abstraction & Agentic Tooling](#database-abstraction-agentic-tooling) | [Managed Google Cloud Remote MCP Servers](#managed-google-cloud-remote-mcp-servers) (Alt 1) | Lines 126-139 |
| [Database Abstraction & Agentic Tooling](#database-abstraction-agentic-tooling) | [Remote Custom MCP Servers Hosted on Cloud Run](#remote-custom-mcp-servers-hosted-on-cloud-run) (Alt 2) | Lines 141-152 |
| [Web Application Hosting](#web-application-hosting) | [Cloud Run](#cloud-run) (Primary) | Lines 156-165 |
| [Networking & Security Topology](#networking-security-topology) | [Regional External Application Load Balancer and Direct VPC Egress](#regional-external-application-load-balancer-and-direct-vpc-egress) (Primary) | Lines 169-177 |

## <a id="database-vector-search-store"></a>Database & Vector Search Store

### <a id="alloydb-for-postgresql-alloydb-ai"></a>Primary Recommendation: AlloyDB for PostgreSQL (AlloyDB AI)

*   **Description**: Fully managed, PostgreSQL-compatible relational database
    designed for demanding enterprise workloads, equipped with built-in ScaNN
    vector indexing and Gemini Enterprise Agent Platform integration using
    `google_ml_integration`.
*   **Advantages**:
    *   ScaNN index algorithm delivers up to 4x faster vector search queries
        than standard HNSW/IVFFlat at high recall.
    *   In-database vector embeddings (`embedding('text-embedding-005', ...)`),
        reranking (`ai.rank`), and LLM calls (`ml_predict_row`) eliminate data
        movement outside the database boundary.
    *   Integrated `evaluate_query_recall` function provides automated,
        in-database accuracy measurement.
    *   Single SQL query seamlessly combines vector similarity ordering with
        structured relational filters (`WHERE category = ANY(...)`).

### <a id="bigquery-vector-search"></a>Alternative 1: BigQuery Vector Search

*   **Description**: Enterprise data warehouse and lakehouse platform featuring
    native hybrid search (`AI.SEARCH` with `mode => 'HYBRID'`, and
    `VECTOR_SEARCH` with `lexical_search_columns`), batch embedding generation
    (`AI.GENERATE_EMBEDDING`), autonomous background embeddings (`AI.EMBED`),
    and ScaNN (`TREE_AH`) / IVF vector indexing (`CREATE VECTOR INDEX`).
*   **Advantages**:
    *   Scales to billions of rows across structured tables, unstructured
        documents (`ObjectRef`), and data lakehouses without infrastructure
        management.
    *   Provides native SQL hybrid search functions: `AI.SEARCH` (TVF combining
        vector similarity with lexical search on autonomous embedding tables)
        and `VECTOR_SEARCH` (with `lexical_search_columns`).
    *   Accelerates hybrid search with `CREATE VECTOR INDEX` supporting ScaNN
        (`TREE_AH`) and Inverted File (`IVF`) algorithms with `STORING` clauses
        for metadata pre-filtering and join elimination.
    *   Includes Gemini in BigQuery for AI-assisted data exploration,
        conversational analytics with custom data agents, automated data
        insights, and natural language SQL/Python code generation.
    *   Supports both batch embedding generation across very large tables via
        `AI.GENERATE_EMBEDDING` (TVF) and continuous autonomous maintenance via
        `AI.EMBED` stored generated columns.
    *   Ideal for gigabyte to petabyte-scale analytical RAG, log analytics,
        audience segmentation, and batch hybrid search entirely in standard SQL
        (vs low-latency transactional OLTP in AlloyDB).
*   **Resources** (Only put into context if the user selects this alternative
    product):
    *   `https://docs.cloud.google.com/bigquery/docs/vector-search-intro.md.txt`
    *   `https://docs.cloud.google.com/bigquery/docs/gemini-overview.md.txt`
    *   `https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-search.md.txt`
    *   `https://docs.cloud.google.com/bigquery/docs/vector-index.md.txt`

### <a id="cloud-sql"></a>Alternative 2: Cloud SQL

*   **Description**: Fully managed relational database service offering native
    vector search (`VECTOR` data type, ScaNN `TREE_SQ` indexing,
    `approx_distance` and `vector_distance` functions) for MySQL, and `pgvector`
    for PostgreSQL.
*   **Advantages**:
    *   Lower entry cost and simpler operational setup for small to medium
        workloads (less than 10M rows).
    *   Fully transactionally consistent, real-time ACID-compliant vector index
        updates during DML operations.
    *   Supports ScaNN algorithm and iterative filtering
        (`cloudsql_vector_iterative_filtering`) for ANN similarity queries in
        Cloud SQL for MySQL.
*   **Resources** (Only put into context if the user selects this alternative
    product):
    *   `https://docs.cloud.google.com/sql/docs/mysql/vector-search.md.txt`
    *   `https://docs.cloud.google.com/sql/docs/mysql/integrate-cloud-sql-with-vertex-ai.md.txt`

## <a id="vector-embeddings-engine"></a>Vector Embeddings Engine

### <a id="text-embeddings-on-gemini-enterprise-agent-platform"></a>Primary Recommendation: Text embeddings on Gemini Enterprise Agent Platform

*   **Description**: Managed text embedding model producing 768-dimensional
    dense vector representations optimized for semantic search and retrieval.
*   **Advantages**:
    *   Directly invocable from SQL inside AlloyDB, BigQuery, and Cloud SQL.
    *   High semantic accuracy across multi-lingual and domain-specific retail
        search queries.
    *   Fully managed scaling and zero model server maintenance.

### <a id="self-hosted-embedding-model"></a>Alternative 1: Self-hosted embedding model

*   **Description**: Custom-deployed open-source or fine-tuned text embedding
    model hosted on Cloud Run or Gemini Enterprise Agent Platform.
*   **Advantages**: Complete control over customized fine-tuned open-source models.
*   **Resources** (Only put into context if the user selects this alternative
    product):
    * `https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-garden/self-deployed-models.md.txt`
    * `https://docs.cloud.google.com/run/docs/deploying.md.txt`

## <a id="database-abstraction-agentic-tooling"></a>Database Abstraction & Agentic Tooling

### <a id="mcp-toolbox-for-databases"></a>Primary Recommendation: MCP Toolbox for Databases

*   **Description**: Model Context Protocol (MCP) Toolbox is an open source
    Model Context Protocol (MCP) server that connects your AI agents, IDEs, and
    applications directly to your enterprise databases.
*   **Advantages**:
    *   Decouples database execution and complex hybrid search SQL logic from
        application code.
    *   Exposes clean REST/MCP tool endpoints for AI agents and application
        services.
    *   Supports agentic tool integration out of the box with custom tools that
        enforce tenant isolation boundaries.

### <a id="managed-google-cloud-remote-mcp-servers"></a>Alternative 1: Managed Google Cloud Remote MCP Servers

*   **Description**: Fully managed remote MCP servers provided by Google Cloud
    (managed via Agent Registry) offering built-in IAM governance, Model Armor
    security scanning, and pre-packaged toolsets for Google Cloud services.
*   **Advantages**:
    *   Zero server infrastructure management; built-in IAM authentication,
        fine-grained access policies, and Model Armor prompt injection
        protection.
    *   Exposes tools, prompts, and resources via standardized MCP discovery
        (`tools/list`).
*   **Resources** (Only put into context if the user selects this alternative
    product):
    *   `https://docs.cloud.google.com/mcp/overview.md.txt`

### <a id="remote-custom-mcp-servers-hosted-on-cloud-run"></a>Alternative 2: Remote Custom MCP Servers Hosted on Cloud Run

*   **Description**: Custom MCP servers built using open-source SDKs
    (TypeScript, Python, Go, FastMCP) deployed on Cloud Run using streamable
    HTTP or Server-Sent Events (SSE) transport.
*   **Advantages**:
    *   Complete flexibility to implement custom tools, prompts, and resources
        tailored to hybrid search applications.
    *   Supports serverless auto-scaling, sidecar deployment alongside AI
        agents, and service-to-service IAM authentication (`roles/run.invoker`).
*   **Resources** (Only put into context if the user selects this alternative
    product):
    *   `https://docs.cloud.google.com/run/docs/host-mcp-servers.md.txt`

## <a id="web-application-hosting"></a>Web Application Hosting

### <a id="cloud-run"></a>Primary Recommendation: Cloud Run

*   **Description**: Fully managed serverless container platform that
    automatically scales compute instances based on incoming traffic.
*   **Advantages**:
    *   Scales to zero when idle; seamlessly handles traffic bursts.
    *   Native integration with Direct VPC Egress for secure private connection
        to AlloyDB.
    *   Supports any containerized web framework (Java Spring Boot, Python
        FastAPI, Node.js).

## <a id="networking-security-topology"></a>Networking & Security Topology

### <a id="regional-external-application-load-balancer-and-direct-vpc-egress"></a>Primary Recommendation: Regional External Application Load Balancer and Direct VPC Egress

*   **Description**: Edge load balancer providing Cloud Armor DDoS/WAF
    protection, routing traffic to Cloud Run services configured with Direct VPC
    Egress to communicate with AlloyDB over private IP ranges.
*   **Advantages**:
    *   Strict physical and network isolation for database tier.
    *   Cloud Armor protection against SQL injection and web threats.
