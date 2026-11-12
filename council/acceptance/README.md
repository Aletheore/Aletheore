# Real-network acceptance test (not part of pytest)

Runs the real product end to end: `council serve` behind the real free tunnel and registry (aletheorecouncil.com), a real invitation, a real
guest on another machine, and a scripted conversation: join with safety words, exact messages (unicode, long, tabs, over-limit cut with a
marker), end to end encrypted file transfer with the daily file allowance, an outage (server frozen, tunnel killed), a hard crash and restart,
and removing the guest. It prints PASS or FAIL per check on both sides and releases the registry slot at the end.

Files: `acceptance_host.py` (host side) and `acceptance_guest.py` (guest side, takes the invitation link). Start the host on one machine,
copy the invitation link it prints, and run the guest on the other.

Host options (environment): `ACC_CRYPTO=e2e|none` (sealed or plain room), `ACC_SCENARIO=tunnel|recycled|local` (real tunnel; the link's
address has been given to a stranger so the guest must use the registry; no tunnel at all), `ACC_CLOUDFLARED=<path>` (a host machine without
cloudflared on its PATH), `ACC_SRC=<the checkout's src folder>` (defaults to this checkout).

Needs: a working registry, `cloudflared` on the host (or ACC_CLOUDFLARED), `psutil`, and the guest machine able to reach the internet.
It uses a real slot from the pool for a few minutes per run.
