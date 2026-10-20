"""Example of using the Agent Platform SDK with OpenMaaS on Vertex AI."""

import google.auth
import vertexai
from vertexai.generative_models import GenerativeModel

# Get default project ID from environment
_, project_id = google.auth.default()

vertexai.init(project=project_id, location="global")

# Important: Use the full resource path: `publishers/PUBLISHER/models/MODEL`
model = GenerativeModel("publishers/zai-org/models/glm-5-maas")
response = model.generate_content("Explain quantum computing.")
print(response.text)
