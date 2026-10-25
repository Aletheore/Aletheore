"""Example Python SDK configuration for HybridAgent with Egress Gateway."""

import vertexai
from vertexai import agent_engines
from vertexai import types

# Initialize Vertex AI client with v1beta1 API for Agent Engine & Identity
client = vertexai.Client(
    project="{project_id}",
    location="{region}",
    http_options=dict(api_version="v1beta1"),
)

agent_gateway_config = {
    "agent_to_anywhere_config": {
        "agent_gateway": (
            "projects/{project_number}/locations/{region}/"
            "agentGateways/agw-tools-a2a"
        )
    }
}

# Deploy the agent using the client.agent_engines.create surface
remote_app = client.agent_engines.create(
    agent=agent_engines.LangchainAgent(model="gemini-2.5-flash", tools=[...]),
    config={
        "display_name": "HybridAgent",
        "agent_gateway_config": agent_gateway_config,
        "identity_type": types.IdentityType.AGENT_IDENTITY,
        "staging_bucket": "gs://{staging_bucket}",
        "env_vars": {
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://telemetry.googleapis.com",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        },
    },
)
