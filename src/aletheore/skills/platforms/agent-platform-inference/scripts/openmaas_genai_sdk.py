"""Example of using the GenAI SDK with OpenMaaS on Vertex AI."""

from google import genai
import google.auth

# Get default project ID from environment
_, project_id = google.auth.default()

client = genai.Client(
    enterprise=True,
    project=project_id,
    location="global",  # OpenMaaS models are often global
)

# Note: For GenAI SDK/Vertex with OpenMaaS, you MUST use the full path:
# `publishers/PUBLISHER/models/MODEL`
response = client.models.generate_content(
    model="publishers/zai-org/models/glm-5-maas",
    contents="Explain quantum computing in simple terms.",
)
print(response.text)
