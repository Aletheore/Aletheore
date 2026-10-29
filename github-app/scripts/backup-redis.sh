#!/usr/bin/env bash
set -euo pipefail

# Backs up the Aletheore hosted-app Redis (queue state and the companion
# budget ledger) via the running docker-compose redis service. Intended to
# run on a schedule (e.g. a cron entry on the deployment host) from the
# github-app/ directory, next to docker-compose.yml.
#
# Usage:
#   ./scripts/backup-redis.sh [backup-dir] [retention-count]
#
# Defaults: backup-dir=./backups, retention-count=14 (keep the 14 most
# recent backups; older ones are deleted after a successful backup).
#
# The dump is streamed with `redis-cli --rdb -` (a replication-style sync), so
# it is a consistent point-in-time RDB without touching the live AOF files.

# Backups hold queue payloads and the budget ledger: owner-only permissions.
umask 077

BACKUP_DIR="${1:-./backups}"
RETENTION_COUNT="${2:-14}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
BACKUP_FILE="${BACKUP_DIR}/aletheore_redis_${TIMESTAMP}.rdb"

mkdir -p "${BACKUP_DIR}"

# Write to a .tmp path first so an interrupted dump never leaves a partial
# file at the final name for the pruning step or a restore to pick up.
docker compose exec -T redis redis-cli --rdb - > "${BACKUP_FILE}.tmp"

# An RDB starts with the magic "REDIS"; refuse to keep anything else (an empty
# file or an error message) as a backup.
if [ "$(head -c 5 "${BACKUP_FILE}.tmp")" != "REDIS" ]; then
  rm -f "${BACKUP_FILE}.tmp"
  echo "Redis backup failed: output is not an RDB file" >&2
  exit 1
fi
mv "${BACKUP_FILE}.tmp" "${BACKUP_FILE}"

echo "Backup written to ${BACKUP_FILE}"

# Prune backups beyond the retention count, oldest first. Only files this
# script wrote match the glob, so manual dumps are never touched.
ls -1t "${BACKUP_DIR}"/aletheore_redis_*.rdb 2>/dev/null | tail -n "+$((RETENTION_COUNT + 1))" \
  | xargs -r rm -f

echo "Retained the ${RETENTION_COUNT} most recent backups in ${BACKUP_DIR}"
