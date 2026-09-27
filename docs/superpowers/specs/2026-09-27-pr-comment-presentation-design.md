# PR comment presentation: rank surfaced, and a standalone "what changed" comment

**Status:** Draft for review (nothing built)
**Date:** 2026-09-27
**Owner:** Arihant Kaul
**Related:** PR #793 (rank + severity, merged 2026-09-24), `docs/superpowers/specs/2026-09-13-*` change-diagram idea (superseded in spirit by this doc's file-overview piece), `github-app/scan_worker/jobs.py`, `github-app/scan_worker/blast_radius_summary.py`, `src/aletheore/history.py`

## 1. Understanding

What was asked, and what was decided across this conversation's questions:

- Aletheore's Flash Review already ranks and labels findings by severity (PR #793, live in production since the 2026-09-26-2 deploy). Severity shows as an emoji badge on each inline comment and a one-line count in the summary. The ordinal **rank** itself is computed, validated, and then discarded — nothing downstream ever reads it. That's the real, confirmed gap: the founder asked for "Rank + Severity," and severity alone shipped.
- The founder also wants the PR experience to look and feel the way Greptile and CodeRabbit's do: more human-legible, giving anyone opening the PR (or an agent reviewing it, not just building it) a fast, honest sense of what happened — not only a list of complaints.
- Explicit, hard constraint: **no change to what Flash Review finds, grounds, verifies, or posts.** This is presentation only.
- Studied for real (not from memory) before design: a live CodeRabbit PR (substrait-io/substrait#1222) and Greptile's own docs plus several real repos citing it. CodeRabbit posts two separate comments — one with findings, one a "Walkthrough" table (`Layer/File(s)` | `Summary`) plus a "Changes" table and a pre-merge-checks table. Greptile's summary comment carries a 0–5 confidence score and an issue table of changed files, separate from its inline comments.
- Aletheore already has one real, deterministic differentiator neither competitor has stated: the blast-radius section (who else depends on what changed, from the real import graph, no LLM involved). This design keeps and reuses it rather than replacing it.

Six decisions made through this conversation, each already recorded verbatim in the transcript:

1. The new "what changed" content stays **fully deterministic** — no new LLM call, matching "computed, not guessed."
2. A **top-issue callout** goes first in the Flash Review summary, above the existing finding count.
3. **Rank shows on every inline comment**, not just the top one (`🟠 High · #2 of 5`).
4. The "what changed" overview is a **separate, standalone comment** — not a section inside the Flash Review comment — because it must show something useful even when Flash Review found nothing, and tying its fate to the findings comment's marker/upsert would contradict that.
5. It lists **every changed file**, truncated past a cap, not only files with a finding.
6. Flash Review's summary carries a **hidden TOON-encoded block** (rank/severity/file/line/issue per finding) for an agent to read exact fields from, instead of parsing prose.

Assumption to confirm on review: both pieces below apply to whatever Flash Review already applies rank/severity to today (Flash and AIR, never free) for the rank/callout/TOON piece; the standalone "what changed" comment has no tier gate at all, since it rides on `run_pr_scan_job`, which already runs for every tier.

## 2. Current state (verified, not assumed)

- `_rank_findings_with_severity` (`github-app/scan_worker/flash_review.py:2483`) runs once per PR after all per-file findings are generated and grounded, on the same model as generation (GLM-5.3-Flash via IndieRouter). Cost measured at merge: ~$0.0002/PR. It returns `{**finding, "rank": int, "severity": str}` and fails open (leaves findings unranked/unlabeled) on any error.
- `jobs.py` wires it as `rank_findings=not is_free_tier` (`run_flash_review_job`, ~line 2212) — both paid tiers, never free.
- `_flash_review_comment_body` (`jobs.py:2311`) renders the severity emoji + label as a header on each inline comment. `_flash_review_severity_breakdown` (`jobs.py:2339`) renders the one-line count in the summary (e.g. "2 High, 1 Medium"). **Neither reads `finding["rank"]` at all** — confirmed by grep across `github-app/`, zero other references outside `flash_review.py`'s own construction and the test suite.
- `_post_flash_review_finding_comments` (`jobs.py:2367`) posts each finding as its own inline `create_pr_review_comment` call and only returns a `failed_new_posts` count to its caller — the created comment's `id` (needed to build a jump-link) is captured locally for `insert_flash_review_finding_comment` but never returned. This function's return value needs to broaden from `int` to a small structure that also carries posted findings' comment ids, to support the top-issue jump-link.
- `run_pr_scan_job` (`jobs.py:1279`) is a **separate job** from `run_flash_review_job`, fired by the `pull_request` webhook on `opened`/`synchronize` for every installation regardless of plan (only a monthly *distinct-repo* scan cap applies, and only to non-free plans — no PR-level or tier-level gate). It clones and scans **both** `base_sha` and `head_sha` (`jobs.py:1356-1360`), then calls `compute_diff(old, new, full=False)` from `src/aletheore/history.py` and posts its own comment (the "Aletheore evidence diff" comment already seen live on this repo's own PRs).
- `_compute_curated_diff` (`src/aletheore/history.py:114`) — read in full — produces new/resolved secrets, vulnerabilities, static-analysis findings, layer violations, endpoints, and three aggregate deltas (module count, edge count, commit count). **It has no per-file breakdown of symbol changes.** Building the "what changed per file" panel is new logic, but it costs nothing new to run: both evidence snapshots it would compare already exist in memory in this same job, for every PR, on every tier, today.
- `blast_radius_summary` (`github-app/scan_worker/blast_radius_summary.py`) is deterministic, already computes a per-target (per-file) direct/transitive dependents map internally, but only returns one rendered markdown string for the Flash Review summary — its internal per-file structure needs a small return-shape change to be reusable by the new comment without recomputing `find_blast_radius` a second time.
- `aletheore.toon_encoding.to_toon` already exists and is used today by the MCP server (`mcp_server.py`'s `_toon_result`) — reusable as-is for the hidden agent block.
- Dogfooded live, this session: PR #841's Flash Review summary read *"2 finding(s) posted... (1 High, 1 Low.)"* with 🟠/🔵 inline badges — confirming the shipped severity feature actually works in production, not just in tests.

## 3. Scope: two independent pieces, two PRs

The founder confirmed these ship separately. Each is scoped, tested, and deployed on its own; neither depends on the other existing first.

### Piece A — Rank surfaced on Flash Review (small)

Touches only `github-app/scan_worker/jobs.py` (and its tests). No new job, no new comment, no new data source.

1. **Inline comment badge gains rank.** `_flash_review_comment_body`: `🟠 High` → `🟠 High · #2 of 5` whenever both `rank` and `severity` are present and valid (same fail-open contract as today: absent or malformed either one, and the badge renders exactly as it does now with no rank suffix — never a partial/garbled one).
2. **Top-issue callout, first line of the summary.** Selects the lowest-numbered rank among findings that actually posted (not among all proposed — a finding that failed to post, per the existing `failed_new_posts` path, cannot be "the top issue" since a reader can't see it). Falls through to the next-lowest rank if the top one failed to post; omitted entirely if nothing posted. Renders as:
   ```
   🔴 Top issue: <finding's own issue text, first line only> → #discussion_r<comment_id>
   ```
   Requires broadening `_post_flash_review_finding_comments`'s return value from a bare `int` (failed-post count) to a small dataclass/dict carrying both that count and `{finding_identity: comment_id}` for whatever posted — its one caller in `jobs.py` is updated in the same commit.
3. **Hidden TOON block**, appended at the very end of the summary body:
   ```html
   <!-- aletheore-flash-review-data
   <TOON-encoded array of {rank, severity, file, line, issue} for every posted finding>
   -->
   ```
   Never rendered by GitHub; a marker distinct from `FLASH_REVIEW_MARKER` (used for the whole-comment upsert) so an agent can `grep` for it reliably. Emitted only when at least one finding carries rank+severity (paid tiers only, matching today); absent entirely otherwise, same as the severity breakdown line already behaves.

Nothing about which findings get generated, grounded, verified, dismissed, or posted changes. Every existing test for those paths is untouched.

### Piece B — Standalone "what changed" comment (larger)

New code in `run_pr_scan_job`'s path (`jobs.py`), a new comment with its own marker (e.g. `PR_OVERVIEW_MARKER`), upserted every run **regardless of tier, regardless of whether Flash Review ran, ran cleanly, or ran at all.**

**Data sources, all already computed by this job today, zero new scans or LLM calls:**
- `fetch_pr_changed_files(client, token, repo_full_name, base_sha, head_sha)` — already called at `jobs.py:1420` — gives per-file status (added/modified/removed/renamed) and line counts straight from the GitHub API, for literally every changed file, no scan needed.
- `old`/`new` evidence (both already in memory from the existing base/head scan) — new, small comparison function: for each changed file present in both evidence's `repository.modules`, diff the `symbols.functions`/`symbols.classes` name lists (added/removed), the same identity-set-diff idiom `_new_and_resolved` already uses in `history.py`, just keyed by file instead of by finding. A file only in `new` (added) or only in `old` (deleted) is reported as such, not diffed.
- Blast-radius fan-out per file — reuses `blast_radius_summary`'s internal per-target computation (return-shape change noted above), not a second `find_blast_radius` pass.

**Rendering:**
- One row per changed file: path, status, +/- lines (from GitHub), a short deterministic phrase for symbol changes ("+2 functions, -1 function" / "new file, 3 functions" / "removed"), and a dependents count if blast radius found any.
- Truncated past a cap (proposed: 20 files shown, "+N more" beyond that) — the same honest-truncation idiom `blast_radius_summary.MAX_TARGETS_SHOWN` already uses, not a silent cut.
- A one-line header stating this is deterministic and always posted, so nobody mistakes its absence-of-opinion for "nothing happened."

**Decided:** this is a new leading section on `run_pr_scan_job`'s *existing* "Aletheore evidence diff" comment, not a third comment type. That comment already exists, already has a marker, already upserts every run, and already fires under the exact conditions this needs (every tier, always) — one fewer comment type on every PR, and the per-file overview becomes what a reader sees first, with the existing new/resolved-findings content unchanged below it.

## 4. Testing

**Piece A:** rank-badge string (present/absent/malformed-rank fallback), top-issue selection (including the failed-post fallback and the "nothing posted" omission case), the broadened return value of `_post_flash_review_finding_comments` and its one call site, the TOON block's presence/absence and that it round-trips through `to_toon`/decode, and that a ranking-failed-open run produces none of the new UI (exact parity with today).

**Piece B:** the per-file symbol-diff function against real before/after evidence fixtures (added file, deleted file, function added, function removed, no change, rename — GitHub reports a rename as a single file with a `previous_filename`, needs an explicit fixture), truncation past the cap, and that this comment posts even when `findings_to_post` is empty in the sibling Flash Review job (the two jobs don't share state, so this is really "post it under every real `run_pr_scan_job` code path," not a cross-job assertion).

**Dogfood, both pieces:** after the next deploy, read the actual comment on a real PR in this repo, the same live check already done for Piece A's predecessor (rank+severity) on PR #841 in this session.

## 5. Rollout

Two PRs, in the order the founder chose: Piece A first (smaller, lower-risk, no new job touched), Piece B second. Both are `github-app` changes — deployed by hand over SSH per the existing deploy runbook, not a PyPI release. Both go out to production only after a real deploy, same as every other backend change this session.

## 6. Risks and open points

- GitHub's rename handling for the per-file diff needs an explicit fixture, not just added/removed/modified.
- The jump-link format (`#discussion_r<id>`) needs a live check against a real posted comment before trusting it — GitHub's URL scheme for a review comment anchor should be verified once, not assumed from memory.
- Piece B's per-file symbol diff is new code with no precedent in this codebase to copy verbatim (unlike `_new_and_resolved`, which is finding-shaped, not module-shaped) — real, not large, effort.
