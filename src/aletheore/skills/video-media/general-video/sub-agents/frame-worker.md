# Frame worker — general-video delta

> The shared law is the core contract above (the packet builder prepends `../../hyperframes/references/frame-worker-core.md` to this file as `_role.md`) — read the two as one role. This file carries only what's specific to a general-video scene; each worker receives the **2–3 scene packets** the orchestrator assigns; build every assigned packet, one at a time. Tempted to add a generic GSAP / timeline rule here? Wrong home — it belongs in the core contract or `hyperframes-core`.

## Your scene is invented, not captured

There is no product capture pipeline: your packet's storyboard block plus the design truth file named in Project inputs are your complete input. Invent elements from those two sources only — use exactly the media paths the block itself names, and never resolve or fetch new media (the orchestrator staged everything your block cites before dispatch).

## Design truth

Project inputs names the design file (resolution order `frame.md` → `design.md` → `DESIGN.md`). It is brand truth for tokens, type, palette, and treatment; the storyboard block owns content. When the block and the design file disagree on content, the block wins; on style, the design file wins.

## Output contract — composition + motion sidecar

For each assigned packet, write exactly two files. Stop only after every assigned packet has both:

1. The packet's exact `src` (e.g. `compositions/frames/<frame_id>.html`) — the sub-composition, a bare fragment per the core contract.
2. The sidecar `<frame_id>.motion.json` in the same directory (e.g. `compositions/frames/<frame_id>.motion.json`) — one JSON object the orchestrator merges into the project's motion ledger:

```json
{
  "scene": "<frame_id>",
  "duration_s": 0.0,
  "rules": ["<rule ids you actually used>"],
  "exit": { "vector": "<direction + px/s at your last frame>", "still_moving": true },
  "entry": { "vector": "<direction + px/s at your first frame>", "from_rest": false }
}
```

Report what you actually authored — measured values from your timeline, not the plan's hopes; where the doctrine chain is installed, a numeric seam gate verifies exits and entries downstream and a wrong sidecar fails loudly there instead of silently here.

## Boundaries

Audio is orchestrator-owned: never author `<audio>` in a scene. Seams between scenes are stamped by the orchestrator from the ledger — author your entry/exit motion inside your own timeline, and never reach into a neighbor scene's file.
