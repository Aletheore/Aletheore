#!/bin/bash
set -euo pipefail

# Curl PATCH command to enforce SGP on Authz Extension
API_ROOT="https://networkservices.googleapis.com/v1beta1"
RESOURCE="projects/${PROJECT_ID}/locations/${REGION}/authzExtensions"
QUERY="updateMask=metadata.sgpEnforcementMode"

curl -X PATCH \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {"sgpEnforcementMode": "ENFORCE"},
    "spec": {"sgpEnforcementMode": "ENFORCE"}
  }' \
  "${API_ROOT}/${RESOURCE}/${EXTENSION_ID}?${QUERY}"
