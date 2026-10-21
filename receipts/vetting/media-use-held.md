# Why video-media/media-use is not in the catalogue

Held back at export (see `vetting_held.json`: the secrets scan flagged `lib/telemetry.mjs`). Reading the skill:

- `scripts/lib/telemetry.mjs` sends usage events to a PostHog host with a `fetch(... /batch/ ... POST)` call.
- `references/telemetry-dashboard.md` (the upstream's own doc) says events attribute to the same person as the hyperframes CLI and studio via a shared install id, are stitched to the HeyGen account on sign-in, and that this is "not fully anonymous by design". Opt-out is by environment variable (`HYPERFRAMES_NO_TELEMETRY=1` or `DO_NOT_TRACK=1`).
- Source: https://github.com/heygen-com/hyperframes (skill at commit 76ba031dccef188a133c0f47806483c661151d68, path skills/media-use).

Decision: not bundled, because a skill whose script phones home with an account-linked id does not fit "curated, read locally".
