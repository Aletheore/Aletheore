#!/usr/bin/env bash
set -euo pipefail

# Restores the Aletheore hosted-app PostgreSQL database from a backup taken
# by scripts/backup-postgres.sh.
#
# DESTRUCTIVE: drops and recreates the target database before restoring.
# Only run this against a database you intend to overwrite - a dedicated
# restore-drill database, or a real recovery. Run from the github-app/
# directory, next to docker-compose.yml.
#
# Usage:
#   ./scripts/restore-postgres.sh <backup-file> [target-db-name]
#
# Restore drills: pass a target-db-name other than aletheore_app (e.g.
# aletheore_app_restore_drill) to rehearse a restore without touching the
# live database.

if [ $# -lt 1 ]; then
  echo "usage: $0 <backup-file> [target-db-name]" >&2
  exit 1
fi

BACKUP_FILE="$1"
DB_NAME="${2:-aletheore_app}"
DB_USER="aletheore"

if [ ! -f "${BACKUP_FILE}" ]; then
  echo "error: backup file not found: ${BACKUP_FILE}" >&2
  exit 1
fi

echo "About to DROP and recreate database '${DB_NAME}', then restore ${BACKUP_FILE} into it."
read -r -p "Type the database name to confirm: " CONFIRM
if [ "${CONFIRM}" != "${DB_NAME}" ]; then
  echo "Confirmation did not match - aborting." >&2
  exit 1
fi

docker compose exec -T postgres dropdb -U "${DB_USER}" --if-exists "${DB_NAME}"
docker compose exec -T postgres createdb -U "${DB_USER}" "${DB_NAME}"
docker compose exec -T postgres pg_restore -U "${DB_USER}" -d "${DB_NAME}" --no-owner < "${BACKUP_FILE}"

echo "Restore of ${BACKUP_FILE} into '${DB_NAME}' complete."
