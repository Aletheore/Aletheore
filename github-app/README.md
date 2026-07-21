# Aletheore GitHub App

The hosted service backing the Aletheore GitHub App receives webhooks, runs
`aletheore scan` plus `aletheore diff` for pull requests, posts the result as a
comment, and exposes a JSON dashboard endpoint.

## Local development

```bash
cd prototype
pip install -e .

cd ../github-app
pip install -r requirements.txt

docker run -d --name aletheore-test-pg -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=aletheore_test -p 55433:5432 postgres:16

PGPASSWORD=test psql -h localhost -p 55433 -U postgres -d aletheore_test \
  -f migrations/001_initial_schema.sql

export TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test
export DATABASE_URL=$TEST_DATABASE_URL
python -m pytest tests/ -v
```

## Deploying on KVM4

1. Register the GitHub App with webhook URL `https://aletheore.com/webhook`.
2. Grant `contents: read`, `pull_requests: write`, and `checks: write`.
3. Subscribe to `pull_request` only - `installation`/`installation_repositories`
   are delivered automatically for any App with repository permissions and
   `marketplace_purchase` is tied to the separate Marketplace listing. For
   paid managed audits, also subscribe to `issue_comment`.
4. Copy `.env.example` to `.env` on the server and fill the GitHub App ID,
   webhook secret, and Postgres values.
5. Place the downloaded private key at `github-app/app-private-key.pem` -
   `docker-compose.yml` mounts it read-only and points
   `GITHUB_APP_PRIVATE_KEY_PATH` at it. Do not paste the key into `.env`
   directly: plain env-file values reject the real newlines in a PEM
   (confirmed empirically against docker run/compose --env-file).
6. Add the App's Client ID/Client Secret, a random `SESSION_SECRET`, and a real
   `ANTHROPIC_API_KEY` to `.env`.
7. Add `https://aletheore.com/auth/callback` as a Callback URL under GitHub App
   user authorization settings.
8. Point `aletheore.com` at the KVM4 server.
9. Apply paid-tier migration 002 to already-initialized databases:
   `docker compose exec -T postgres psql -U aletheore -d aletheore_app < migrations/002_paid_tier.sql`.
10. Apply health-monitoring migration 003 to already-initialized databases:
    `docker compose exec -T postgres psql -U aletheore -d aletheore_app < migrations/003_health_monitoring.sql`.
11. Run `docker compose up -d --build`.

## Backups

`scripts/backup-postgres.sh` runs `pg_dump` against the running `postgres`
service and writes a timestamped, compressed custom-format dump to
`./backups` (override with a first argument), pruning everything past the
14 most recent backups (override with a second argument). Run it from
`github-app/`, on the same host as `docker-compose.yml`.

Schedule it with cron on the deployment host, for example daily at 03:00 UTC:

```
0 3 * * * cd /path/to/github-app && ./scripts/backup-postgres.sh >> /var/log/aletheore-backup.log 2>&1
```

To restore, use `scripts/restore-postgres.sh <backup-file> [target-db-name]`.
It is destructive - it drops and recreates the target database - and asks
for interactive confirmation of the database name before doing so. Always
rehearse against a throwaway target first, never the live database:

```
./scripts/restore-postgres.sh ./backups/aletheore_app_2026-07-21T00-00-00Z.dump aletheore_app_restore_drill
```

Only once that succeeds and the data looks right would a real recovery use
`aletheore_app` as the target - and only after confirming the app and
worker are stopped or the restored data will immediately start changing
again.

Paid installations can configure endpoint health monitoring through
`PUT /admin/{org}/{repo}/health-check-url`. The route stores the base URL and
optional latency threshold per installation; the scheduled worker checks the
latest scanned endpoint evidence every three minutes and sends Slack-compatible
webhook alerts only when reachability or latency-threshold state changes.

The dashboard route is a JSON foundation endpoint at `/app/{org}/{repo}`. A
private-repository OAuth gate and rendered UI are deferred fast-follows; do not
install this hosted endpoint for private repositories until that gate exists.
