#!/bin/bash
set -euo pipefail

# Register Cloud Run services in Agent Registry
MARKETING_URL="https://marketing-tool-private-${UNIQUE_SUFFIX}.a.run.app"
gcloud alpha agent-registry services create marketing-tool-service \
  --project="${PROJECT_ID}" \
  --location=us-east4 \
  --display-name="Marketing Tool Service" \
  --endpoint-spec-type=no-spec \
  --interfaces=url="${MARKETING_URL}",protocolBinding=HTTP_JSON

SALES_URL="https://sales-tool-private-${UNIQUE_SUFFIX}.a.run.app"
gcloud alpha agent-registry services create sales-tool-service \
  --project="${PROJECT_ID}" \
  --location=us-east4 \
  --display-name="Sales Tool Service" \
  --endpoint-spec-type=no-spec \
  --interfaces=url="${SALES_URL}",protocolBinding=HTTP_JSON

SUPPORT_URL="https://support-tool-private-${UNIQUE_SUFFIX}.a.run.app"
gcloud alpha agent-registry services create support-tool-service \
  --project="${PROJECT_ID}" \
  --location=us-east4 \
  --display-name="Support Tool Service" \
  --endpoint-spec-type=no-spec \
  --interfaces=url="${SUPPORT_URL}",protocolBinding=HTTP_JSON
