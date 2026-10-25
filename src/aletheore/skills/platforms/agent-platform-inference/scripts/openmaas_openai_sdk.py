"""Example of using the OpenAI SDK with OpenMaaS on Vertex AI."""

import google.auth
import google.auth.transport.requests
import openai


def get_gcp_access_token():
  creds, _ = google.auth.default()
  creds.refresh(google.auth.transport.requests.Request())
  return creds.token


# Get default project ID from environment
_, project_id = google.auth.default()

client = openai.OpenAI(
    base_url=f"https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/global/endpoints/openapi",
    api_key=get_gcp_access_token(),
)

# NOTE: For OpenMaaS models, you MUST use the format: `publisher/model`
# Example: `deepseek-ai/deepseek-v3.2-maas`, `zai-org/glm-5-maas` etc.

response = client.chat.completions.create(
    model="zai-org/glm-5-maas",
    messages=[{
        "role": "user",
        "content": "Explain quantum computing in simple terms.",
    }],
)

print(response.choices[0].message.content)
