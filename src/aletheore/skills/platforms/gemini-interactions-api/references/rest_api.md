# Accessing the Interactions API via REST

For shell-based scripts, debugging, or non-Python/JS environments, you can communicate with the stateful Interactions API directly using raw HTTP/REST requests via `curl`.

## Table of Contents

- [1. REST Endpoint](#1-rest-endpoint): Lines 14-25
- [2. Set up Variables & Authentication Header](#2-set-up-variables--authentication-header): Lines 26-36
- [3. Single-Turn Interaction Payload](#3-single-turn-interaction-payload): Lines 37-86
- [4. Multi-Turn Stateful Interaction Payload](#4-multi-turn-stateful-interaction-payload): Lines 88-108
- [5. Streaming Output Payload](#5-streaming-output-payload): Lines 110-135
- [6. Managed Agent Background Payload](#6-managed-agent-background-payload): Lines 136-156

## 1. REST Endpoint

The REST API endpoint for interactions is:

```http
POST https://aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{LOCATION}/interactions
```

*   **LOCATION**: Use `global` (or custom region if required).
*   **PROJECT_ID**: Your Google Cloud Project ID.
*   **Express Mode (API Key)**: Use `POST https://aiplatform.googleapis.com/v1beta1/locations/global/interactions` with `-H "x-goog-api-key: YOUR_API_KEY"`.

## 2. Set up Variables & Authentication Header

Set your Google Cloud project ID, target model ID (e.g., `gemini-3.8-flash`) or managed/custom agent ID (e.g., `deep-research-preview-04-2026`), and access token generated from Application Default Credentials:

```bash
PROJECT_ID="your-project-id"  # Or: PROJECT_ID=$(gcloud config get-value project)
MODEL_ID="gemini-3.8-flash"
AGENT_ID="deep-research-preview-04-2026"
ACCESS_TOKEN=$(gcloud auth print-access-token)
```

## 3. Single-Turn Interaction Payload

Send a request to start an interaction:

```bash
curl -X POST "https://aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/global/interactions" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"${MODEL_ID}"'",
    "input": [{
      "type": "user_input",
      "content": [{
        "type": "text",
        "text": "Explain serverless computing in one sentence."
      }]
    }]
  }'
```

### Response Example

A synchronous POST request returns a JSON object containing the conversation step details and unique identifiers:

```json
{
  "id": "your-interaction-id",
  "status": "completed",
  "steps": [
    {
      "type": "model_output",
      "content": [
        {
          "type": "text",
          "text": "Serverless computing is a cloud execution model where the cloud provider dynamically manages the allocation and provisioning of servers, charging customers based on actual usage rather than pre-purchased capacity."
        }
      ]
    }
  ],
  "usage": {
    "total_tokens": 24751,
    "total_input_tokens": 23894,
    "total_output_tokens": 857
  },
  "created": "2026-05-08T10:44:43Z",
  "updated": "2026-05-08T10:44:43Z",
  "environment_id": "your-environment-id",
  "object": "interaction"
}
```

## 4. Multi-Turn Stateful Interaction Payload

To continue an existing conversation statefully, specify the `previous_interaction_id` in the JSON payload:

```bash
curl -X POST "https://aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/global/interactions" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"${MODEL_ID}"'",
    "store": true,
    "previous_interaction_id": "YOUR_PREVIOUS_INTERACTION_ID",
    "input": [{
      "type": "user_input",
      "content": [{
        "type": "text",
        "text": "Can you elaborate on that?"
      }]
    }]
  }'
```

## 5. Streaming Output Payload

To stream updates in real time (Server-Sent Events format), pass `"stream": true` in the payload (using `"model"` or `"agent"`):

```bash
curl -X POST "https://aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/global/interactions" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"${MODEL_ID}"'",
    "stream": true,
    "input": [{
      "type": "user_input",
      "content": [{
        "type": "text",
        "text": "Write a long story about space travel."
      }]
    }]
  }'
```

The endpoint will return a chunked stream where each event begins with `data: ` containing JSON updates with the `event_type` and step contents.

> **How `curl` handles streaming:**
> By default, when `"stream": true` is passed, the server responds with `Transfer-Encoding: chunked` and `Content-Type: text/event-stream` (Server-Sent Events). `curl` will automatically keep the connection open and print the incoming data chunks to `stdout` in real time as they are pushed by the server. The user does not need to poll or pull further; the complete sequence of events streams continuously until completion.

## 6. Managed Agent Background Payload

For long-running tasks, target a managed agent asynchronously by setting `"background": true` and `"environment": "remote"`:

```bash
curl -X POST "https://aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/global/interactions" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "'"${AGENT_ID}"'",
    "environment": "remote",
    "background": true,
    "input": [{
      "type": "user_input",
      "content": [{
        "type": "text",
        "text": "Analyze competitive positioning for commercial solar energy providers."
      }]
    }]
  }'
```
