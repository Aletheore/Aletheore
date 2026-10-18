#!/bin/bash
set -euo pipefail

# Verify Ingress with OAuth / IAP Identity Token
REGION_NAME="${REGION:-us-central1}"
ENDPOINT="https://${REGION_NAME}-aiplatform.googleapis.com/v1beta1"
RESOURCE="projects/${PROJECT_ID}/locations/${REGION_NAME}"
URL="${ENDPOINT}/${RESOURCE}/reasoningEngines/${ENGINE_ID}:query"

curl -v -X POST "${URL}" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"input": {"prompt": "Hello"}}'
