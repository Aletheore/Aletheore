"""Script to verify Agent Gateway egress tool execution policies."""

import subprocess
import requests

token = (
    subprocess.check_output(["gcloud", "auth", "print-identity-token"])
    .decode("utf-8")
    .strip()
)
url = (
    "https://us-central1-aiplatform.googleapis.com/v1beta1"
    "/projects/{project_id}/locations/us-central1"
    "/agentGateways/{egress_gateway_name}:query"
)
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}
payload = {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {"name": "get_account_balance", "arguments": {}},
    "id": 1,
}
response = requests.post(url, headers=headers, json=payload)
print("Status:", response.status_code)
print("Response:", response.text)
assert response.status_code == 200
