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
10. Run `docker compose up -d --build`.

The dashboard route is a JSON foundation endpoint at `/app/{org}/{repo}`. A
private-repository OAuth gate and rendered UI are deferred fast-follows; do not
install this hosted endpoint for private repositories until that gate exists.
