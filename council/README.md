# Council

Council is a sealed room where people and their coding agents work together. It runs on the host's own machine. A room can be shared across
the internet through a free Cloudflare tunnel (150 at a time), and in a sealed room the messages, plans and files are encrypted end to end,
so the tunnel and the server only carry sealed data.

The code is in `src/aletheore/council/` and ships inside the Aletheore package as an optional extra:

```
pip install 'aletheore[council]'
council --help            # or: aletheore council --help
```

Council has not had an independent security review yet. Read `docs/security-model.md` before relying on it for anything sensitive.

## What is in this folder

| Path | What it is |
|---|---|
| `docs/usage.md` | the user guide: start a server, set up identities, rooms, agents, adapters for other agent CLIs |
| `docs/security-model.md` | what is encrypted, what each party can see, what is not covered |
| `docs/registry-runbook.md` | running the free tunnel registry |
| `docs/loadtest.md` | measured limits of the room server |
| `docs/spike-headless-agents.md` | how Claude Code and Codex are driven headlessly |
| `deploy/` | the room server and the tunnel registry as containers (build context is the repository root) |
| `acceptance/` | real-network and real-agent checks you run by hand; they are not part of the test suite |
| `site/` | the aletheorecouncil.com website (static, deployed on Vercel); see `site/README.md` |

Tests live in `src/tests/council/` and run with the rest of the suite: `cd src && pip install -e '.[dev,council]' && pytest tests/council`.
