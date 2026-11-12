#!/bin/sh
# Restore a backup. By default it restores into a SCRATCH database (council_restore_test) to prove the backup works, and never
# touches the live data. Restoring over the live database needs --into-production and COUNCIL_CONFIRM_RESTORE=yes.
set -eu
cd "$(dirname "$0")"
FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then echo "usage: restore.sh BACKUP.sql.gz [--into-production]" >&2; exit 2; fi
TARGET=council_restore_test
if [ "${2:-}" = "--into-production" ]; then
  if [ "${COUNCIL_CONFIRM_RESTORE:-}" != "yes" ]; then
    echo "refusing: restoring over the live database also needs COUNCIL_CONFIRM_RESTORE=yes" >&2
    exit 2
  fi
  TARGET=""
fi
if [ -n "$TARGET" ]; then
  docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -q -c 'DROP DATABASE IF EXISTS $TARGET' -c 'CREATE DATABASE $TARGET'"
  gunzip -c "$FILE" | docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d $TARGET -q -v ON_ERROR_STOP=1" >/dev/null
  docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d $TARGET -tA -c \"select 'restored: ' || (select count(*) from rooms) || ' rooms, ' || (select count(*) from events) || ' events'\""
else
  gunzip -c "$FILE" | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -v ON_ERROR_STOP=1' >/dev/null
  echo "restored into the live database"
fi
