#!/bin/bash
set -euo pipefail

# Apply IAP policy bindings for Agent Registry egress
gcloud beta iap web set-iam-policy iap-policy.json \
  --project="${PROJECT_ID}" \
  --resource-type=agent-registry \
  --region="${REGION}"
