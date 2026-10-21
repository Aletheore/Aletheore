Use these documents to help inform the creation of a Google Cloud hybrid search
architecture and deployment:

## Architecture and design guidance

- [Secure agent interactions with MCP Toolbox for Databases](https://docs.cloud.google.com/alloydb/docs/ai/secure-agent-interactions-mcp.md.txt):
  Information on configuring security, connection management, and agent
  interaction boundaries using MCP Toolbox for Databases.
- [RAG infrastructure for generative AI using Agent Platform and AlloyDB for PostgreSQL](https://docs.cloud.google.com/architecture/rag-capable-gen-ai-app-using-vertex-ai.md.txt):
  An architecture that stores vector embeddings alongside your operational
  data in a fully managed database like AlloyDB for PostgreSQL.
- [Best practices for tuning ScaNN index in AlloyDB](https://docs.cloud.google.com/alloydb/docs/ai/best-practices-tuning-scann.md.txt):
  Best practices for tuning ScaNN index parameters like num_leaves,
  num_leaves_to_search, and pre_reordering_num_neighbors for high recall and
  fast query performance.
- [Choose a connectivity option for AlloyDB](https://docs.cloud.google.com/alloydb/docs/choose-alloydb-connectivity.md.txt):
  Compares AlloyDB connectivity methods, including AlloyDB Auth Proxy, Private
  Service Connect (PSC), and direct VPC peering.
- [AlloyDB security best practices](https://docs.cloud.google.com/alloydb/docs/security-best-practices.md.txt):
  Recommendations to secure AlloyDB instances using IAM database
  authentication, Private Service Connect, and VPC perimeters.

## Deployment

- [Hybrid search on Cloud Run codelab](https://codelabs.developers.google.com/hybrid-search-on-cloudrun#0):
  A step-by-step hands-on tutorial for building and deploying a hybrid search
  application using AlloyDB, Gemini Enterprise Agent Platform, and Cloud Run.
- [Run hybrid vector similarity search in AlloyDB](https://docs.cloud.google.com/alloydb/docs/ai/run-hybrid-vector-similarity-search.md.txt):
  Guidance to construct and execute hybrid search queries combining vector
  similarity ordering with relational SQL filtering in AlloyDB
- [Evaluate semantic queries using AI operators in AlloyDB](https://docs.cloud.google.com/alloydb/docs/ai/evaluate-semantic-queries-ai-operators.md.txt):
  Guidance to use in-database AI functions such as ml_predict_row and ai.rank
  for semantic reranking and LLM validation.
- [Cloud Run Direct VPC Egress](https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc.md.txt):
  Instructions to set up Direct VPC Egress for Cloud Run services to access
  private resources like AlloyDB within a VPC network.
