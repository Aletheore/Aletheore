#!/bin/bash
set -euo pipefail

# Enable required APIs (including modelarmor and aiplatform)
gcloud services enable \
  compute.googleapis.com \
  networkservices.googleapis.com \
  networksecurity.googleapis.com \
  modelarmor.googleapis.com \
  iap.googleapis.com \
  agentregistry.googleapis.com \
  serviceextensions.googleapis.com \
  aiplatform.googleapis.com \
  --project="${PROJECT_ID}"

# Import Ingress & Egress Agent Gateways using gcloud alpha
gcloud alpha network-services agent-gateways import agw-ingress-config.yaml \
  --project="${PROJECT_ID}" \
  --location="${REGION}"
gcloud alpha network-services agent-gateways import agw-egress-config.yaml \
  --project="${PROJECT_ID}" \
  --location="${REGION}"

# Import Authz Extension using beta
gcloud beta service-extensions authz-extensions import \
  agw-authz-extension.yaml \
  --project="${PROJECT_ID}" \
  --location="${REGION}"

# Import Authz Policy
gcloud beta network-security authz-policies import agw-authz-policy.yaml \
  --project="${PROJECT_ID}" \
  --location="${REGION}"
