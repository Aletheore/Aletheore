# Tunnel registry: deploying it on the existing server

Council's free tunnel pool is run from a registry service (`aletheore.council.registry_main`) behind a Cloudflare tunnel. This runbook is for the person
operating that registry. The signing key is the one thing that cannot be recreated: back it up off the server. Every step marked GO
changes something real and needs the operator's explicit go-ahead first.

Lessons from the first deploy: Docker secret files keep the host file's owner, so the files must be owned by the container user (10002 for the registry,
65532 for cloudflared) with mode 400; the sidecar waits for the registry's health check, so a first `up` can time out once and a second `up -d` finishes it.

## What it is
One small Python process (FastAPI, SQLite) that hands out free tunnel names (slots) to hosts, renews leases, keeps a queue, answers
"where is this host?" with a signed answer, and shows the public counter. It never sees room traffic. It holds one secret that matters:
the Cloudflare API token for `aletheorecouncil.com` (tunnel edit and DNS edit on that zone only).

## How it is isolated (tested locally on 2026-11-10 with dummy secrets)
- Own compose project (`council-registry`), own network, own volume. No shared volume, network or secret with any other stack.
- No published port. It is reached only through its own Cloudflare tunnel sidecar (`registry.aletheorecouncil.com`).
- Non-root user (10002), read-only root filesystem, all capabilities dropped (CapEff 0), `no-new-privileges`.
- Ceilings: 0.25 CPU, 256 MB memory (no swap), 64 processes, lowest CPU weight. The tunnel sidecar has 0.25 CPU and 128 MB.
- Secrets are files under `deploy/secrets/` mounted as Docker secrets; nothing secret is in the environment or on a command line.
- Verified in a local container: status endpoint answers, writes outside `/data` fail, signing key survives a restart (same public key),
  backup archive contains the database and the key.

## Capacity check (read-only, with the operator's go-ahead)
4 CPUs, load average 2.44 / 2.12 / 1.33 (two scan workers each using about one core); 16 GB RAM with about 12 GB available and 279 MB of 8 GB swap used;
154 GB of 193 GB disk free; Docker running 10 containers, Caddy listening on 80 and 443. The registry and its sidecar need under 400 MB and at most
half a core, publish no port and so cannot clash with anything. Verdict: it fits with a wide margin; CPU is the only resource worth watching, so the
CPU weight is set above the lowest value (128).

## Steps
1. **GO: read-only capacity check.** Run `deploy/headroom.sh` on the server (writes nothing). Expect plenty of room: the registry plus
   sidecar are under 400 MB and a quarter of a CPU each at most.
2. Create the registry's own Cloudflare resources. **GO**, from your own machine with the scoped token:
   `CloudflareProvider.create_service_tunnel("council-registry", "registry.aletheorecouncil.com", "http://registry:18700")` gives a tunnel
   id and its run token. Save the run token to `deploy/secrets/registry_tunnel_token` (chmod 600).
3. Create the secrets on the server (never in chat, never in git): `secrets/cf_token` (a NEW permanent scoped token, not the 7-day test
   token), `secrets/registry_admin` (a long random string), `secrets/registry_tunnel_token`. `chmod 600 secrets/*`.
4. Copy `registry.env.example` to `registry.env`. Leave `COUNCIL_REGISTRY_SLOTS=0` for the first start.
5. **GO:** `docker compose -f compose.registry.yml --env-file registry.env up -d`. Read the log line `REGISTRY PUBLIC KEY (pin this in
   clients)` and record it. Check `https://registry.aletheorecouncil.com/v1/status` shows `0 / 0`.
6. **Back up the signing key now** with `deploy/backup-registry.sh`, and store the archive off the server. A lost key means every client
   pin stops matching.
7. **GO (separate approval): create the pool.** Set `COUNCIL_REGISTRY_SLOTS=150` and restart. This creates 150 tunnels and 150 DNS
   records in the zone (the free zone allows 200 records in total, and two are already in use).
8. Put the registry URL and public key into the client defaults (`COUNCIL_REGISTRY`, `COUNCIL_REGISTRY_KEY`, then as constants), run a
   real host end to end from two different machines, and watch the counter move.

## Rollback
`docker compose -f compose.registry.yml down` stops it. Slots already leased keep working until their tunnels are rotated: the tunnels
live at Cloudflare, not in the container. To remove everything, delete each slot with `CloudflareProvider.delete_slot`.

## Known limits
- One registry process: if it is down, running rooms keep working; new hosts cannot claim and guests cannot look a host up by key (a link's
  embedded address still works while the slot stays the same).
- Slots are only ever added by the pool setting, never removed under a holder.
- The registry's SQLite file is small; the signing key file is the one thing that cannot be recreated.
