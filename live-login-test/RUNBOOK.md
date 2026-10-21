# Live sign-in test for Hallbook (real GitHub, your own server, nothing touches production)

Why: the one thing mocked tests cannot prove is that GitHub's "check a token" call accepts the login token that the
Aletheore GitHub App's device flow hands out. If it does not, every `aletheore login` would get a 401 after deploy.
This test runs the real device flow against real GitHub, with the server on your laptop.

Where to run it: **your own Terminal**, because it needs (1) the GitHub App client secret and (2) you approving a
code in the browser. Do not paste the secret into chat or a file.

Needs: Homebrew `postgresql@16` and `redis` (both already installed here), the GitHub App's **client secret**
(GitHub, Settings, Developer settings, GitHub Apps, Aletheore, "Client secrets"; generate a new one if you do not
have it and delete it afterwards), and a browser signed in to the GitHub account you want to test with.

## 0. Start the server (Terminal 1)

```bash
cd ~/dev/Veridion/docs/skill-hub-curation/live-login-test
export GITHUB_CLIENT_SECRET='paste-the-client-secret-here'
./run_local_server.sh
```

Leave it running. It builds a throwaway Postgres and Redis, applies the migrations and serves
`http://127.0.0.1:8010`. Ctrl-C stops it and wipes everything. Watch this terminal: a line
`GitHub check-a-token returned <status>` is the signal to look for (see test 1).

## Two simulated machines (Terminals 2 and 3)

Each "machine" is a separate HOME, so its credentials and custom skills are separate and your real config is never touched.

```bash
# Terminal 2 = machine A
export HOME=/tmp/hb-machine-a; mkdir -p $HOME
export ALETHEORE_API_URL=http://127.0.0.1:8010 ALETHEORE_LOGIN_LABEL=machine-a

# Terminal 3 = machine B
export HOME=/tmp/hb-machine-b; mkdir -p $HOME
export ALETHEORE_API_URL=http://127.0.0.1:8010 ALETHEORE_LOGIN_LABEL=machine-b
```

(`ALETHEORE_LOGIN_LABEL` is only for this test: two machines normally have different hostnames. The server treats an
identical label as "the same machine logging in again" and replaces its token.) Login prints a yellow
"Using ALETHEORE_API_URL=..." line when the override is active: if you do not see it, stop, you are talking to production.

## The tests

Record each result in the table at the bottom.

| # | Where | Do | Expect |
|---|---|---|---|
| 1 | A | `aletheore login`, approve the code on github.com/login/device | Logged in, "Custom skills you add sync to your Aletheore account...". Terminal 1 shows **no** `check-a-token returned` warning. **If it shows 404:** GitHub does not recognise the App's token via that endpoint (the risk). **If 401/403:** the client secret or id is wrong. |
| 2 | any | `curl -s -X POST http://127.0.0.1:8010/v1/personal-tokens -H "Authorization: Bearer $(gh auth token)" -H 'content-type: application/json' -d '{"label":"x"}'` | HTTP 401 "not issued to Aletheore" (a valid GitHub token from another app is refused). |
| 3 | A | `echo '# demo' > /tmp/demo.md; aletheore hallbook add demo-skill /tmp/demo.md` | "Saved". It is pushed to the server right away. |
| 4 | B | `aletheore login` (same GitHub account) | The demo skill appears: `aletheore hallbook list` on B shows `custom/demo-skill`. |
| 5 | B | `aletheore hallbook remove demo-skill` | Removed locally; a `demo-skill.md.deleted-<time>.bak` is kept next to it. |
| 6 | A | `aletheore login` again | A's token is replaced (same label); the skill is now gone from A too, with a `.deleted-<time>.bak` copy; Terminal 1 has a "deleted from your account on another machine" log line on A's side. |
| 7 | A | Add `keep-skill`, then `aletheore logout --all-devices` | Exit 0, "Logged out". |
| 8 | B | `aletheore hallbook add b-skill /tmp/demo.md` | Succeeds locally; the push fails quietly (token revoked); a later `aletheore login` re-authenticates and uploads it. |
| 9 | B | While logged out (`aletheore logout`), `aletheore hallbook remove b-skill`, then `aletheore login` | b-skill is NOT pulled back from the account. |
| 10 | A | `aletheore logout --all-devices` when already logged out | "Not logged in to Hallbook - nothing to revoke." and says any managed-audit login was left alone. |
| 11 | A | (optional, needs a second GitHub account) log in as the other account | A's custom skills move to `~/.config/aletheore/custom_skills-other-account-<time>.bak/`; none are uploaded to the other account. |
| 12 | A | Check nothing reached production: the yellow banner showed on every login, and `aletheore status` shows your normal state in your real HOME (open a fresh terminal) | No change to your real credentials. |

Handy checks on the throwaway database while the server runs (password `test`):

```bash
PGPASSWORD=test /opt/homebrew/opt/postgresql@16/bin/psql -h localhost -p 55440 -U postgres hb_live \
  -c "select i.github_login, t.label, t.revoked_at is not null as revoked from personal_api_tokens t join personal_identities i on i.id=t.personal_identity_id order by t.id;" \
  -c "select skill_name, length(content) from custom_skills;"
```

## Clean up

Ctrl-C Terminal 1 (wipes the database). Then `rm -rf /tmp/hb-machine-a /tmp/hb-machine-b`. If you generated a client
secret just for this, delete it in the GitHub App settings.

## Results log

| # | Result (pass / fail) | Notes |
|---|---|---|
