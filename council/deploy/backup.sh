#!/bin/sh
# Backup of the Council database (run from cron). Keeps 7 daily dumps and 4 weekly ones. A backup is complete or absent, never partial.
# Run from this folder; the postgres service must be up. Credentials stay inside the container; nothing secret is printed.
set -eu
cd "$(dirname "$0")"
DIR="${COUNCIL_BACKUP_DIR:-./backups}"
mkdir -p "$DIR/weekly"
STAMP=$(date -u +%Y%m%d-%H%M)
OUT="$DIR/council-$STAMP.sql.gz"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' | gzip > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
if [ "$(date -u +%u)" = "7" ]; then cp "$OUT" "$DIR/weekly/"; fi
find "$DIR" -maxdepth 1 -name 'council-*.sql.gz' -mtime +7 -delete
find "$DIR/weekly" -name 'council-*.sql.gz' -mtime +28 -delete
echo "backup written: $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
