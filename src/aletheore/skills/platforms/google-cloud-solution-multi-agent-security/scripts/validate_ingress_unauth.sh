#!/bin/bash
set -euo pipefail

# Test Ingress rejection without credentials (HTTP 403 Forbidden)
REGION_NAME="${REGION:-us-central1}"
ENDPOINT="https://${REGION_NAME}-aiplatform.googleapis.com/v1beta1"
RESOURCE="projects/${PROJECT_ID}/locations/${REGION_NAME}"
URL="${ENDPOINT}/${RESOURCE}/reasoningEngines/${ENGINE_ID}:query"

curl -v -X POST "${URL}" \
  -H "Content-Type: application/json" \
  -d '{"input": {"prompt": "Hello"}}'
