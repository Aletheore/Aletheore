#!/bin/bash
#
# Searches for PAM entitlements across the resource hierarchy (project,
# ancestor folders, and organization) that the caller is eligible to request.
#
# Usage:
#   ./search_eligible_entitlements.sh --project=PROJECT_ID
#   ./search_eligible_entitlements.sh --folder=FOLDER_ID
#   ./search_eligible_entitlements.sh --organization=ORGANIZATION_ID
#

set -euo pipefail

LOCATION="global"
PROJECT_ID=""
FOLDER_ID=""
ORG_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project=*)
      PROJECT_ID="${1#*=}"
      shift
      ;;
    --project|-p)
      PROJECT_ID="$2"
      shift 2
      ;;
    --folder=*)
      FOLDER_ID="${1#*=}"
      shift
      ;;
    --folder|-f)
      FOLDER_ID="$2"
      shift 2
      ;;
    --organization=*)
      ORG_ID="${1#*=}"
      shift
      ;;
    --organization|-o)
      ORG_ID="$2"
      shift 2
      ;;
    --location=*)
      LOCATION="${1#*=}"
      shift
      ;;
    --location|-l)
      LOCATION="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      shift
      ;;
  esac
done

search_scope() {
  local scope_flag="$1"
  local scope_val="$2"
  gcloud pam entitlements search \
    --caller-access-type=grant-requester \
    --location="$LOCATION" \
    "$scope_flag=$scope_val" \
    --format=json 2>/dev/null || true
}

if [[ -n "$PROJECT_ID" ]]; then
  echo "=== Searching Project Scope: $PROJECT_ID ===" >&2
  search_scope "--project" "$PROJECT_ID"

  echo "=== Searching Ancestor Hierarchy for Project: $PROJECT_ID ===" >&2
  while read -r id type; do
    if [[ "$type" == "folder" && "$id" != "$PROJECT_ID" ]]; then
      echo "=== Searching Folder Scope: $id ===" >&2
      search_scope "--folder" "$id"
    elif [[ "$type" == "organization" && "$id" != "$PROJECT_ID" ]]; then
      echo "=== Searching Organization Scope: $id ===" >&2
      search_scope "--organization" "$id"
    fi
  done < <(gcloud projects get-ancestors "$PROJECT_ID" --format="value(id,type)" 2>/dev/null || true)

elif [[ -n "$FOLDER_ID" ]]; then
  echo "=== Searching Folder Scope: $FOLDER_ID ===" >&2
  search_scope "--folder" "$FOLDER_ID"

  current_folder="$FOLDER_ID"
  while [[ -n "$current_folder" ]]; do
    parent=$(gcloud resource-manager folders describe "$current_folder" --format="value(parent)" 2>/dev/null || true)
    if [[ "$parent" =~ ^folders/(.+) ]]; then
      current_folder="${BASH_REMATCH[1]}"
      echo "=== Searching Parent Folder Scope: $current_folder ===" >&2
      search_scope "--folder" "$current_folder"
    elif [[ "$parent" =~ ^organizations/(.+) ]]; then
      parent_org="${BASH_REMATCH[1]}"
      echo "=== Searching Parent Organization Scope: $parent_org ===" >&2
      search_scope "--organization" "$parent_org"
      break
    else
      break
    fi
  done

elif [[ -n "$ORG_ID" ]]; then
  echo "=== Searching Organization Scope: $ORG_ID ===" >&2
  search_scope "--organization" "$ORG_ID"

else
  echo "Error: Must specify one of --project=PROJECT_ID, --folder=FOLDER_ID, or --organization=ORGANIZATION_ID" >&2
  exit 1
fi
