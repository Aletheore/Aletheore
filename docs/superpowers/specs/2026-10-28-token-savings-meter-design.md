# Aletheore token savings meter and mascot

Status: approved and implemented, 2026-10-28.

Labels: **[Owner]** is explicit direction from the owner. **[My call]** is a design choice proposed here.
**[Verified]** was checked in the repository on 2026-10-28. **[Open]** needs owner direction before the
relevant implementation choice is fixed.

## Intent

**[Owner]** Add a free, local meter showing how much context Aletheore has saved a coding agent,
with an Aletheore mascot that is both a cute companion and explains the figures and caveats. The
attached image is a visual reference for a small, friendly pixel robot; it does not specify the
mascot's final appearance or require copying that character. The mascot can grow into an Aletheore
local guide: later it may explain scan findings and limits, surface relevant skills or next steps,
and offer small user-approved actions. It should stay grounded in local evidence, state uncertainty,
and remain quiet when it has nothing useful to add.

**[My call]** Put the meter at `aletheore tokens meter`, alongside the existing `tokens audit` and `tokens profile`
commands. Keep the main report compact and make the mascot a small, optional presentation element in
that report. Preserve a plain one-line output for scripts/status lines. The mascot should present the
same figures and caveats as the text report; it must not imply activity or savings that the meter cannot
observe.

## Goals

- Work after installing Aletheore, without an account, model call, hosted state, or new network
  traffic.
- Record local events where savings occur and aggregate today, this week, and all time, by source.
- Label every figure **measured** or **estimated**, with its method beside it. Never combine the two
  into an unlabelled total.
- Say when a source is unmeasured, especially when the read guard is off; offer setup only after
  showing the exact change and receiving consent.
- Keep event recording cheap and failure-tolerant: hooks always exit 0; missing, corrupt, or
  concurrently updated state never breaks the agent call.
- Keep v1 mascot behavior decorative and informative, not a separate agent, telemetry path, or new
  settings writer. The broader local-guide role is a future direction, not a v1 feature.

## Out of scope

- Shield or any Shield files, imports, status, or installation dependency.
- Transcript/session-history reading, private-content collection, accounts, telemetry, model calls,
  or new network traffic.
- Enabling or changing hooks/settings without explicit consent.
- A paid tier, autonomous/interactive character behavior, or a desktop pet in v1.

## Verified current state

- `read_guard.py` is opt-in with `ALETHEORE_READ_GUARD=1`. It recognizes unchanged full-file rereads
  within one session, and knows file bytes, but cannot observe the model's tokenization or whether
  the agent would actually have reused context.
- `mcp_budget.py` sees the original and bounded serialized MCP results and emits omission metadata.
  It can count omitted characters, but not model tokens.
- `tokens_cli.py` already provides `tokens audit` and `tokens profile`. Applying a profile edits
  Claude Code project settings after confirmation. Its audit invokes `claude` for a real request, so
  it is not a silent/local source of ongoing meter data.
- `network_plan.py` describes current scan network paths. This design adds none.
- `evidence_hook.py` sets the relevant precedent: stdlib-only hook work, per-session state, fast and
  silent when there is nothing to report, and always exit 0.
- Repository working tree already contains untracked work, including the Shield design and plan, the
  paid-tier design, and the supplied prompt. This proposal does not modify those files.

## Proposed v1 behavior

### Recording and figures

Use a dedicated meter state file under the existing `.aletheore/` directory when it exists, with no
directory creation solely to collect meter data. Store event timestamps, source, and integer size
facts only; do not store file contents, MCP payloads, or transcript text. Use atomic replacement and
tolerate unreadable or invalid state with a clear status and no invented totals. Retention and
locking details are left to implementation review; concurrent writes must not corrupt the history.

- **Read guard:** record a full unchanged reread denial and the file byte size. Report an
  **estimated** token saving using a stated byte-to-token ratio, because the hook does not know the
  model tokenizer or whether prior context remains usable. If the guard is off, show “unmeasured
  (read guard off)”, not zero.
- **MCP budget:** record original and delivered serialized character counts at the point of
  trimming. Report omitted characters as **measured** and token savings as **estimated** using a
  stated character-to-token ratio. Keep the paging path visible so trimming is not presented as
  information permanently discarded.
- **Tool profile:** report whether a profile is configured. Do not count a token saving merely
  because settings were changed: a per-turn token delta needs a baseline and the existing audit uses
  an external model request. Any profile savings remain unmeasured unless a future, explicitly run
  comparison supplies a defensible baseline.

In the summary, show per-source totals separately by label and method. A compact example shape
(illustrative, not a commitment to exact wording):
`This week: ~1,240 tokens estimated (Read guard, file bytes ÷ 4); 3,100 chars measured omitted (MCP budget); profile savings unmeasured.`
Dollar conversion is excluded pending owner direction on pricing and staleness.

### Mascot and output

**[Owner-approved amendment, 2026-10-28]** Use the Aletheore A-mark mascot as a reusable brand
character across the website and product. The approved artwork and 4-by-4 pose sheet are the source
assets. Keep its A silhouette, face placement, proportions, outline, and ink/paper/rust/green
palette consistent; states may change limbs, expression, and small task props. Provide reusable
idle, typing, work, explain, inspect, read, caution, success, wave, walk, and jump states. The
website may use restrained CSS motion (idle, typing, and a three-pose jump); animate only the intact
artwork through transforms or discrete sprite-state changes. Respect `prefers-reduced-motion`. Put a
small neutral mascot with a gentle idle motion in the shared site header so it appears across site
pages; show the typing loop beside the homepage's evidence headline. The component and poses are
available for later product surfaces. Keep the CLI report text-based; remove the ASCII box character
rather than pretend terminal glyph art is the brand mascot. The character explains only facts
already present in the meter and does not speak, animate independently, or read other local data.

### CLI and consent

`aletheore tokens meter` reads and summarizes local meter state without changing settings. It says
when read-guard savings are unmeasured and offers `--enable-read-guard`; that option previews the
exact project-local hook entries and files, then requires a typed yes (or an explicit `--yes` for
unattended setup). `--disable-read-guard` removes only entries recorded in Aletheore's ownership
sidecar. No setting changes on decline.

### Documentation and acceptance

README and CLI documentation explain the event sources, estimation ratios, limits, state location,
opt-in read guard, and mascot behavior. Acceptance should cover: known fixture sizes and date
aggregation; distinct measured/estimated output; guard-off status; corrupt/missing/concurrent state;
and hook exit-0 behavior. A fixture with a known MCP result and unchanged reread must produce the
expected character/byte facts and estimated tokens without claiming model-token measurement.

## Decisions

### Initial proposed calls (superseded only where stated below)

- **[My call]** Keep v1 local, deterministic, and independent from Shield.
- **[My call]** Keep the read guard opt-in; do not change agent behavior for existing users as a side
  effect of installing the meter.
- **[My call]** No dollar conversion in v1 until the owner selects pricing semantics and accepts that
  model prices change.
- **[My call]** Do not read agent transcripts; only count events observed directly by Aletheore hooks/tools.
- **[My call, superseded by owner amendment]** Keep the mascot static and scoped to the meter report.

### Approved v1 scope

The owner approved the token-meter direction and later approved the A-mark mascot concept and pose
set. Keep the read guard opt-in; show tokens and measured omitted characters without dollar
conversion; defer badges/cards; do not inspect transcripts; and reserve scan explanations, skill
suggestions, and consent-based actions for later guide iterations. The mascot is a reusable visual
identity across the website and future product surfaces. It may use the approved poses and
restrained, reduced-motion-aware site animations; the CLI remains text-based. It is not an
interactive desktop pet or a separate agent.

## Approval gate

The owner approved this design on 2026-10-28 and authorized planning and implementation. Keep future
guide capabilities outside v1 code.
