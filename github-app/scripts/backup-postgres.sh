#!/usr/bin/env bash
set -euo pipefail

# Backs up the Aletheore hosted-app PostgreSQL database via the running
# docker-compose postgres service. Intended to run on a schedule (e.g. a
# cron entry on the deployment host) from the github-app/ directory, next
# to docker-compose.yml.
#
# Usage:
#   ./scripts/backup-postgres.sh [backup-dir] [retention-count]
#
# Defaults: backup-dir=./backups, retention-count=14 (keep the 14 most
# recent backups; older ones are deleted after a successful backup).

BACKUP_DIR="${1:-./backups}"
RETENTION_COUNT="${2:-14}"
DB_NAME="aletheore_app"
DB_USER="aletheore"
TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
BACKUP_FILE="${BACKUP_DIR}/aletheore_app_${TIMESTAMP}.dump"

mkdir -p "${BACKUP_DIR}"

# Write to a .tmp path first so a crashed/interrupted dump never leaves a
# partial file at the final name for the pruning step or a restore to pick up.
docker compose exec -T postgres pg_dump -U "${DB_USER}" -d "${DB_NAME}" --format=custom \
  > "${BACKUP_FILE}.tmp"
mv "${BACKUP_FILE}.tmp" "${BACKUP_FILE}"

echo "Backup written to ${BACKUP_FILE}"

# Prune backups beyond the retention count, oldest first.
ls -1t "${BACKUP_DIR}"/aletheore_app_*.dump 2>/dev/null | tail -n "+$((RETENTION_COUNT + 1))" \
  | xargs -r rm -f

echo "Retained the ${RETENTION_COUNT} most recent backups in ${BACKUP_DIR}"
