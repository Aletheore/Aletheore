"""Example Python SDK configuration for multi-agent Cloud Run egress."""

from google.adk.agents import llm_agent
from google.adk.integrations import agent_registry
import vertexai
from vertexai import agent_engines
from vertexai import types

# Initialize Vertex AI client with v1beta1 API for Agent Engine & Identity
client = vertexai.Client(
    project="{project_id}",
    location="us-east4",
    http_options=dict(api_version="v1beta1"),
)

# 1. Resolve MCP Toolset from Agent Registry
registry = agent_registry.AgentRegistry(
    project_id="{project_id}", location="us-east4"
)
servers = registry.list_mcp_servers(
    filter_str='displayName="Marketing Tool Service"'
)["mcpServers"]
server_name = servers[0]["name"]
marketing_toolset = registry.get_mcp_toolset(server_name)

# 2. Define ADK Agent
agent = llm_agent.LlmAgent(
    model="gemini-2.5-flash",
    name="marketing_agent",
    instruction="You are a marketing assistant.",
    tools=[marketing_toolset],
)
agent_instance = agent_engines.AdkApp(agent=agent)

# 3. Configure Egress Gateway routing
agent_gateway_config = {
    "agent_to_anywhere_config": {
        "agent_gateway": (
            "projects/{project_number}/locations/us-east4/"
            "agentGateways/agw-tools-run"
        )
    }
}

# 4. Deploy Agent Engine via client.agent_engines.create
remote_app = client.agent_engines.create(
    agent=agent_instance,
    config={
        "display_name": "MarketingAgent",
        "agent_gateway_config": agent_gateway_config,
        "identity_type": types.IdentityType.AGENT_IDENTITY,
        "staging_bucket": "gs://{staging_bucket}",
        "requirements": [
            "google-cloud-aiplatform[adk,agent_engines]",
            "google-adk[mcp]==2.4.0",
        ],
    },
)
