#!/bin/bash
# Runs the Aletheore app server on http://127.0.0.1:8010 with a throwaway Postgres and Redis, so `aletheore login`
# can be tested end to end against REAL GitHub without touching production.
#
# Only one real secret is needed: the Aletheore GitHub App's client secret (the server uses it to ask GitHub
# whether a login token was issued to the Aletheore app). Export it in the same terminal first:
#     export GITHUB_CLIENT_SECRET='...'      # never paste it into chat or commit it
# Everything else below is a dummy value that only matters for other server features.
set -euo pipefail
: "${GITHUB_CLIENT_SECRET:?export GITHUB_CLIENT_SECRET (the Aletheore GitHub App client secret) first}"

ROOT="${HALLBOOK_REPO:-$HOME/dev/Veridion/.claude/worktrees/feat+skill-hub}"
PG=/opt/homebrew/opt/postgresql@16/bin
WORK="$(mktemp -d /tmp/hb-live-XXXXXX)"
PGPORT_LIVE=55440
REDISPORT_LIVE=6391
export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

cleanup() {
  "$PG/pg_ctl" -D "$WORK/pg" stop -m fast >/dev/null 2>&1 || true
  redis-cli -p "$REDISPORT_LIVE" shutdown nosave >/dev/null 2>&1 || true
  rm -rf "$WORK"
  echo "stopped and cleaned up"
}
trap cleanup EXIT INT TERM

echo test > "$WORK/pw"
"$PG/initdb" -D "$WORK/pg" -U postgres --auth=md5 --pwfile="$WORK/pw" -E UTF8 >/dev/null
"$PG/pg_ctl" -D "$WORK/pg" -o "-p $PGPORT_LIVE -k $WORK" -l "$WORK/pg.log" -w start >/dev/null
PGPASSWORD=test "$PG/createdb" -h localhost -p "$PGPORT_LIVE" -U postgres hb_live
redis-server --port "$REDISPORT_LIVE" --daemonize yes --save "" --dir "$WORK" >/dev/null

export DATABASE_URL="postgresql://postgres:test@localhost:$PGPORT_LIVE/hb_live"
export REDIS_URL="redis://localhost:$REDISPORT_LIVE/0"
export GITHUB_CLIENT_ID="Iv23liGMhaWSkY927jgI"        # the id the CLI's device flow uses (src/aletheore/device_auth.py)
export GITHUB_APP_ID=1 GITHUB_APP_PRIVATE_KEY=dummy GITHUB_WEBHOOK_SECRET=dummy GITHUB_APP_SLUG=aletheore
export SESSION_SECRET=dummy-session-secret-at-least-32-characters-long
export AUDIT_SIGNING_PRIVATE_KEY="$(printf '11%.0s' {1..32})"
export PADDLE_WEBHOOK_SECRET=dummy PADDLE_CLIENT_TOKEN=dummy PUBLIC_BASE_URL=http://127.0.0.1:8010

cd "$ROOT/github-app"
python3 scripts/migrate.py
echo
echo "App server on http://127.0.0.1:8010  (Ctrl-C to stop and wipe everything)"
python3 -m uvicorn app_server.main:app --host 127.0.0.1 --port 8010
