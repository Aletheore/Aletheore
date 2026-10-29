# Token savings meter implementation plan

**Goal:** Add a free, local `aletheore tokens meter` that reports honestly labelled savings by source, period, and estimation method. Give Aletheore a reusable A-mark mascot across the website and product; its future guide capabilities remain out of scope.

**Spec:** `docs/superpowers/specs/2026-10-28-token-savings-meter-design.md` (owner approved 2026-10-28).

**Scope boundary:** Do not modify Shield, enable the read guard by default, read transcripts, call a model, add network traffic, or report a profile application as token savings. The meter's normal read path changes no settings. The explicit `--enable-read-guard` option may add project-local hooks only after preview and typed consent; `--disable-read-guard` removes exactly those entries. Keep meter events local under an existing `.aletheore/` directory.

## Architecture

Add `src/aletheore/token_meter.py` for event persistence, validation, aggregation, and formatting data. Store each event as a uniquely named JSON file beneath `.aletheore/token_meter/events/`; write to a temporary file and atomically rename so concurrent hooks do not overwrite each other. Create neither `.aletheore/` nor meter state when the parent directory is absent. Events contain only timestamp, source, and integer size facts; no paths, payloads, or content. Ignore corrupt event files while surfacing a warning in the meter report. Keep measured source facts separate from token estimates, with explicit ratio constants and `ceil` rounding.

Instrument the enabled read-guard hook after it confirms and denies an unchanged full reread, recording known file bytes. Instrument both uses of the MCP output budget in `mcp_server.py` (evidence-query overviews and semantic code-search responses) only when a result is actually shortened and carries `_budget`, recording original and delivered TOON character counts. Do not record page requests or untrimmed results.

Add a `tokens meter` command in `tokens_cli.py`. CLI output remains text-based and explains measured and estimated values without ASCII mascot art; `--plain` emits one line suitable for scripts/status text. The reusable graphical mascot is implemented separately for the website and product surfaces. Both meter views expose today, this week (Monday start), and all-time values by source. Show exact measured omitted characters and approximate estimated tokens separately. When the read guard is disabled, say its savings are unmeasured. Show the lean tool profile as configured/not configured, and keep its token savings unmeasured. Normal meter reads are settings-free. The separate enable option previews and asks before editing project-local hooks.

## Tasks

### 1. Event store and aggregation

- Add immutable event data/validation and the source constants (`bytes / 4` for read-guard estimated tokens; `characters / 4` for MCP estimated tokens).
- Write unique event files atomically only when `.aletheore/` exists; recover from missing/corrupt files and concurrent writers.
- Aggregate by local calendar day, Monday-start week, and all time; injected `now` makes boundaries testable.
- Provide structured totals for separate measured and estimated quantities plus warnings/unmeasured sources.
- Tests: event validation, no parent-directory creation, exact sizes and estimates, aggregation across days/week/year boundaries, corrupt/missing state, and concurrent appends/listing.

### 2. Record source events safely

- Extend the denied reread decision path to record the known byte count only for an actual guard denial; ensure recorder exceptions cannot affect the hook decision or its exit-0 contract.
- Instrument actual trimmed evidence-query and semantic code-search responses in `mcp_server.py`; use the same TOON size function as the budgeter and record only when the response is shorter and includes `_budget`.
- Tests: guard disabled/allowed/denied cases, partial reads, recorder failure, MCP result under budget, actual trim, and paging calls. Add an integration fixture that verifies the measured omission is `original_chars - delivered_chars` and paging isn't counted as savings.

### 3. CLI and mascot (CLI-art portion superseded by the approved mascot plan)

- Add `aletheore tokens meter [--path PATH] [--plain]` using existing path validation.
- Display today/week/all-time for read guard, MCP budget, and lean profile; preserve measured vs estimated labels and methods in every view.
- Do not render terminal ASCII art. Preserve the concise explanatory text and measured/estimated values. Implement the original A-mark graphic and reusable motion states using `docs/superpowers/plans/2026-10-28-aletheore-mascot-implementation-plan.md`.
- Detect only the read-guard hook entries recorded in the project-local ownership sidecar; do not infer global/manual configuration. Report profile configuration without counting profile savings.
- Add `--enable-read-guard` preview + typed consent and `--disable-read-guard` exact removal. Preserve unrelated settings and hooks; refuse malformed configuration before writing.
- Tests: CLI invocation, path errors, normal/empty state, guard off, exact periods and sources, plain output shape, absence of ASCII mascot art, no subprocess/model/network call or settings write on a normal read, setup decline without changes, merge-safe install, partial-setup recovery, and exact removal.


### 4. User documentation

- Update README's token-tools entry and add a short `tokens meter` section with examples, event coverage, storage, ratios, measurement labels, read-guard opt-in, and limits.
- Ensure CLI help describes local-only behavior and the output flags.
- Document the approved mascot's asset and motion contract in its design spec. Keep the future guide direction outside v1 README claims.

### 5. Verification and review

Run focused tests for the meter, read guard, MCP budget and MCP server budget. Run the full Python test suite from `src/`; run the project's configured lint/format checks for modified Python files. Check CLI help and command output manually. Review the diff for Shield changes, settings writes, transcript reads, network calls, generated artifacts, or edits to pre-existing untracked files; none are in scope.

## Acceptance criteria

- Read-guard events are written only on actual opt-in denials and contain byte counts but no path/content.
- MCP events are written only on actual trimmed overview results and record exact original/delivered serialized character counts; calls that are within budget or intentionally paged are not savings events.
- Reports distinguish exact measured omitted characters from approximate estimated tokens and state the ratio. No unlabeled sum mixes them.
- Today, week, and all-time aggregate correctly by source, including local week boundaries.
- Guard-off output says unmeasured. Missing history is described as no recorded events yet; corrupt files produce a warning and never invent totals or crash hooks.
- Concurrent recording cannot overwrite another event, and a broken recorder never breaks hooks or MCP replies.
- `tokens meter --plain` is a single line; CLI output contains no ASCII mascot; normal reads make no model calls or settings changes. The site mascot uses the approved A-mark artwork and respects reduced motion. Setup requires explicit consent and is removable.
- Existing tests remain green; README accurately explains the meter and its limits.
