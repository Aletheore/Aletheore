# Workload Manager Public MCP Status

No public Workload Manager MCP server is currently documented. Do not write
examples that imply Workload Manager has a public MCP integration.

Use public client libraries or the REST API for production workflows.

## Current Recommendation

```mermaid
flowchart LR
    Request["User request"] --> Check["Need Workload Manager resource?"]
    Check --> ClientLib["Use Python or Go client libraries for evaluations"]
    Check --> REST["Use REST for uncovered resources"]
    ClientLib --> Verify["Verify operation and findings"]
    REST --> Verify
```

## Safety Rules

-   Require a project, location, evaluation ID, and explicit resource scope for
    mutating operations.
-   Default list operations to read-only roles.
-   Require confirmation before deleting evaluations or executions.
-   Surface BigQuery export destinations and CMEK key names before creating or
    updating evaluations.
