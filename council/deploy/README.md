# Council room server: deployment runbook

This folder builds and runs the Council room server as **its own isolated stack**: one container for the server, one for its
Postgres, on their own networks, with hard CPU, memory and process limits and a lower scheduling priority than anything else on the
machine. Nothing here shares a network, volume or configuration with any other stack.

> **Nothing in this folder is run on the real server without the operator's explicit go-ahead, step by step.** The first step on any
> real machine is the read-only headroom check below; deploying and load-testing each need their own go-ahead.

## What is in here
| File | What it is |
|---|---|
| `Dockerfile` | Server-only image; non-root user, health check, no secrets baked in. |
| `compose.yml` | Server + Postgres. Limits: 1 CPU and 1 GB each to start (set from measurements, see `docs/loadtest.md`); no swap; process caps; lower CPU weight (disk weight is the optional `compose.io-weight.yml`); read-only server filesystem; all capabilities dropped; Postgres has no published port and is on an internal network. The server port is published on the machine's loopback address only. |
| `council.env.example` | Settings and the names of secrets. Copy to `council.env` (git-ignored, mode 600) and fill in. |
| `Caddyfile.snippet` | One site block to add to the existing Caddy. HTTPS only, security headers, a trustworthy `X-Forwarded-For`. |
| `headroom.sh` | **Read-only** look at CPU, load, memory, disk, running containers and busiest processes. |
| `backup.sh`, `restore.sh` | Nightly `pg_dump` (7 daily + 4 weekly); restore into a scratch database to prove a backup works. |

## Run it on your own machine first (no risk to anything else)
```sh
cd council/deploy
cp council.env.example council.env && chmod 600 council.env      # edit the CHANGE_ME values
echo '{"alice":"a-long-random-token","bob":"another-long-random-token"}' > council_tokens.json && chmod 600 council_tokens.json
docker compose --env-file council.env up -d --build
docker compose ps                                                  # both services should become healthy
curl -s http://127.0.0.1:8765/healthz                              # {"status":"ok"}
docker stats --no-stream                                           # confirm the limits are in effect
```

## Limits and why
- `cpus`, `mem_limit`, `memswap_limit` (equal to the memory limit: no swap), `pids_limit`: the stack cannot use more than this, however busy it gets.
- `cpu_shares: 128`: when the machine is busy, other workloads get CPU first.
- A low **disk** weight is optional (`compose.io-weight.yml`): some kernels, including Docker Desktop's, refuse to start a container that sets one, so it is not in the default file. Try it on the real server only after the headroom check shows it is supported.
- Limits are **starting values**. They are changed only from numbers recorded in `docs/loadtest.md`.

## On the real server (each step needs the operator's explicit go-ahead)
1. **Headroom check (read-only):** `sh headroom.sh > /tmp/headroom.txt` on the server. Read it with the owner; compare with the existing stack's normal load before anything else is decided.
2. **Baseline:** record the existing containers' CPU, memory and IO over at least a day of normal load, and agree stop thresholds (proposal: load average above 3.0 on 4 cores, available memory below 4 GB, or any existing container more than 25% above its baseline).
3. **Bring the stack up** with the limits above, on its own network. Do not touch existing containers, volumes or Caddy sites.
4. **Test through an SSH tunnel only** (nothing public): `ssh -L 8765:127.0.0.1:8765 server`, ramp load in steps, off-peak, with the watcher that stops the run and the container at a threshold.
5. **Only then** add the Caddy site block and the DNS name, after the capacity result and the hosting decision.

To undo everything: `docker compose down` (add `-v` to also delete the data volumes). Then remove the one Caddy site block.

## Backups
Run `./backup.sh` from cron (for example `17 3 * * *`). Prove a backup works with `./restore.sh backups/<file>.sql.gz`, which restores into a scratch database and prints row counts. Restoring over live data needs `--into-production` and `COUNCIL_CONFIRM_RESTORE=yes`.

## Caddy behind the proxy
The server believes `X-Forwarded-For` only from addresses in `COUNCIL_TRUSTED_PROXIES`. Set it to the address Caddy connects from (loopback if Caddy runs on the host; the Docker bridge range if Caddy is a container).
