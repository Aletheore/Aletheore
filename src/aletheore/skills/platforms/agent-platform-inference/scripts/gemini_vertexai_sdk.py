"""Example of using the Agent Platform SDK with Gemini on Vertex AI."""

import google.auth
import vertexai
from vertexai.generative_models import GenerativeModel

# Get default project ID from environment
_, project_id = google.auth.default()

vertexai.init(project=project_id, location="us-central1")

model = GenerativeModel("gemini-2.5-pro")
response = model.generate_content("Why is the sky blue?")
print(response.text)
