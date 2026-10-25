#!/bin/bash
set -euo pipefail

# Map private DNS record (gke-tools.example.com) to GKE ILB IP (10.0.1.50)
gcloud dns record-sets create gke-tools.example.com. \
  --zone="${DNS_ZONE:-example-private-zone}" \
  --type=A \
  --ttl=300 \
  --rrdatas=10.0.1.50 \
  --project="${PROJECT_ID}"
