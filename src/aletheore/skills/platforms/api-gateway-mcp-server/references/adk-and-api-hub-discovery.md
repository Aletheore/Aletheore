# Google ADK Integration and API Hub / Agent Registry Discovery Reference

- [Google ADK (McpToolset) Connection to API Gateway](#google-adk-mcptoolset-connection-to-api-gateway): Lines 6-59
- [Automatic Discovery in Apigee API Hub and Agent Registry](#automatic-discovery-in-apigee-api-hub-and-agent-registry): Lines 61-100

## Google ADK (`McpToolset`) Connection to API Gateway

Google's Agent Development Kit (ADK) connects natively to an API Gateway `/mcp` endpoint over `StreamableHTTP` using `McpToolset` and `StreamableHTTPConnectionParams`.

1. Install the Google ADK Python package with the MCP extra, pinned to the 2.x major version:
   ```bash
   pip install "google-adk[mcp]>=2.0.0,<3.0.0"
   ```
2. Retrieve the gateway's `defaultHostname`:
   ```bash
   gcloud api-gateway gateways describe {gateway_id} \
     --location={region} \
     --project={project_id} \
     --format="value(defaultHostname)"
   ```
3. Configure `McpToolset` with `StreamableHTTPConnectionParams` pointing to `https://{default_hostname}/mcp` (keep `/mcp` at the host root, even when a `servers` base path such as `/v1` is configured with `x-google-endpoint`). If `tools-list.security` is set, pass its credential: a JWT in `Authorization: Bearer {jwt_token}`, or an API key in `x-api-key`. For `tools/call`, pass the credentials that the underlying REST operation requires:

```python
import os
from google.adk.agents import Agent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

gateway_host = os.environ["APIGW_DEFAULT_HOSTNAME"]
headers = {}
if os.environ.get("APIGW_API_KEY"):
    headers["x-api-key"] = os.environ["APIGW_API_KEY"]
if os.environ.get("APIGW_JWT_TOKEN"):
    headers["Authorization"] = f"Bearer {os.environ['APIGW_JWT_TOKEN']}"

gateway_mcp_tools = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=f"https://{gateway_host}/mcp",
        headers=headers,
    )
)

root_agent = Agent(
    model="gemini-3.8-flash",  # Use a current Gemini model.
    name="order_support_agent",
    instruction="Use the API Gateway MCP tools to look up orders and process approved returns.",
    tools=[gateway_mcp_tools],
)
```

   Google ID tokens are short-lived. For a long-running agent, mint a new token before the old one expires, and give the toolset the new `Authorization` header.

4. **Structure OpenAPI `x-google-mcp-tool` descriptions and schemas for reliable ADK tool calling:**
   - Every exposed MCP operation MUST resolve to a non-empty description (`x-google-mcp-tool.description`, operation `description`, or `summary`); operations without a resolvable description are rejected at API config creation time.
   - Write `x-google-mcp-tool.description` to state **when and why** the LLM should invoke the tool, not only what data the endpoint returns. Recommendation: name the read tools to call first, if the tool needs their output.
   - API Gateway rejects complex nested schemas when you create the API config, but a simple nested object body works. If `gcloud api-gateway api-configs create` fails with a schema error, simplify that schema. Recommendations: prefer shallow request schemas for new tools, give each `requestBody` property a `description`, and check the tool's `inputSchema` in `tools/list` after you deploy. API Gateway does not emit MCP tool annotations (`destructiveHint`, `readOnlyHint`) in Public Preview.

References:
- [Turn your REST APIs into MCP tools with Google Cloud API Gateway](https://developers.googleblog.com/turn-your-rest-apis-into-mcp-tools-with-google-cloud-api-gateway/) (the source of the ADK sample). This post predates the September 30, 2026 release that added API key support for `tools/list`. See the [API Gateway release notes](https://docs.cloud.google.com/api-gateway/docs/release-notes.md.txt).
- [ADK Model Context Protocol tools](https://google.github.io/adk-docs/tools-custom/mcp-tools/)

## Automatic Discovery in Apigee API Hub and Agent Registry

When you connect API Gateway to Apigee API hub, API hub publishes an MCP-enabled gateway as an MCP server with MCP-specific metadata, and the server also appears in **Google Cloud Agent Registry**. These catalogs let developers and agents find MCP servers and their tools without knowing each gateway URL. No separate registration step is needed, so do not write a registration script or upload tool schemas.

1. **Complete the API hub prerequisites:** provision API hub in a host project (`{hub_project_id}`, location `{hub_location}`), and grant the roles that connect API Gateway with API hub, such as `roles/apihub.runTimeProjectAttachmentsEditor` in the API Gateway project. For the steps, see [Connect API Gateway with API hub](https://docs.cloud.google.com/api-gateway/docs/api-hub-connect.md.txt).
2. **Attach the API Gateway project to API hub, and turn on metadata sync** (request human approval first):
   ```bash
   gcloud apihub runtime-project-attachments create {project_id} \
     --project={hub_project_id} \
     --location={hub_location} \
     --runtime-project=projects/{project_id}

   gcloud apihub plugins instances create {plugin_instance_id} \
     --plugin=system-api-gateway \
     --project={hub_project_id} \
     --location={hub_location} \
     --display-name="API Gateway metadata sync" \
     --source-project-id={project_id} \
     --actions=actionId=sync-metadata
   ```
   If the project is already attached, these commands return `ALREADY_EXISTS`, and you can continue. In the Google Cloud console, the same steps are in **API hub** > **Settings** > **Project associations** > **Attach runtime project**. If you select to import API Gateway assets when you attach the project in the console, API hub creates the API Gateway plugin instance for real-time metadata ingestion.
3. **Find the MCP server in API hub.** API hub adds a second entry named `{your_api} (MCP)` next to the REST API entry:
   ```bash
   gcloud apihub apis list \
     --project={hub_project_id} \
     --location={hub_location} \
     --filter='displayName~"\(MCP\)$"'
   ```
4. **Find the MCP server in Agent Registry.** Enable the Agent Registry API, and list the MCP servers in the API hub location. The display name of the entry is `{region}/{gateway_id}`. If the list is empty, run the same command with `--project={project_id}`.
   ```bash
   gcloud services enable agentregistry.googleapis.com --project={hub_project_id}

   gcloud agent-registry mcp-servers list \
     --project={hub_project_id} \
     --location={hub_location}
   ```

References:
- [Model Context Protocol overview - Discovery through API Hub and Agent Registry](https://docs.cloud.google.com/api-gateway/docs/mcp-overview.md.txt)
- [Connect API Gateway with API hub](https://docs.cloud.google.com/api-gateway/docs/api-hub-connect.md.txt)
