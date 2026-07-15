# Veridion v1 Design — Parts I–III

**Status:** Draft, pending review
**Date:** 2026-07-14

## Problem

Existing code-audit tooling is fragmented: SAST/linting tools (Snyk, SonarQube, Codacy) do
structural analysis but can't reason about architecture or business risk; LLM-based "review this
code" prompts reason well but hallucinate freely because they have no grounded facts about the
repo to check themselves against. There is no tool that hands an LLM verifiable, structured
evidence about a codebase and constrains it to only make claims that evidence supports.

Veridion is a code-audit tool built as an "operating manual" for whichever coding agent the
developer already uses, rather than a standalone product with its own hosted inference. It
supplies two things: (1) deterministic, machine-generated evidence about a repository, and (2)
instructions that force an agent to ground every claim in that evidence.

The full vision spans ten parts (operating instructions, repository intelligence, git
intelligence, architecture review, security, AI-stack review, startup due diligence, business
metrics, roadmaps, scorecards). This spec covers **v1 only: Parts I–III.** Parts IV and V are the
planned next milestones, evaluated only after v1 proves itself. Parts VI–X are not scaffolded,
stubbed, or referenced anywhere in this build — see Non-Goals.

## Goals (v1)

- Ship a single CLI command, `veridion audit [path]`, that produces a grounded audit report of a
  local repository by combining deterministic static analysis with an existing coding agent's
  reasoning.
- Every factual claim in the output report must trace to a specific field in a structured
  evidence file — no claim about a file, function, branch, or commit that isn't present in that
  evidence.
- Validate the tool by running it against a real, non-trivial repository the author already knows
  well (Procta / proctored-browser) and confirming the output holds up to manual fact-checking.

## Non-Goals (v1)

- **Part IV (architecture pattern review — DDD/hexagonal/clean/etc detection), Part V (security
  review).** Not built in this milestone. Part V is the explicit next step after v1 is validated;
  Part IV's place in the sequence is undecided until then.
- **Parts VI–X** (AI-stack review, startup due diligence, business metrics, roadmaps, 40
  scorecards). Not built, not stubbed, not referenced in code or manual files. Revisit only if
  Parts I–III+V demonstrate real value.
- **Multi-agent support beyond Claude Code.** The adapter interface is designed to support
  multiple agent CLIs, but v1 ships exactly one adapter (Claude Code). Cursor/OpenCode/Aider
  adapters are future work, not v1 deliverables.
- **Hosted/SaaS version, billing, monetization, executive PDF reports.** Out of scope entirely for
  this build.
- **Scored/generated outputs from the original Part III wishlist** — Merge Order Matrix, Conflict
  Prediction, Cherry-Pick Suggestions, Feature Consolidation Plan, and any numeric scoring of
  "engineering discipline" or "branching strategy quality." These require judgment on top of raw
  git facts; v1's deterministic scanner supplies the raw facts only. The agent, using the Part III
  manual, may attempt this kind of synthesis in its output, but v1 does not test for it or
  guarantee it.

## Architecture

`veridion audit [path]` runs two phases in one command:

```
                    ┌─────────────────────────────┐
                    │        Scan phase            │
                    │      (pure Python, no LLM)   │
                    │                               │
  repo path ───────►│  repo scanner  → repository{} │
                    │  git analyzer  → git{}        │──► .veridion/evidence.json
                    │                               │
                    └─────────────────────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │      Reasoning phase          │
                    │      (agent shell-out)        │
                    │                               │
                    │  adapter detection/selection  │
                    │  invoke agent CLI, pointed at │
                    │  manual/*.md + evidence.json  │──► .veridion/audit-report.md
                    │                               │
                    └─────────────────────────────┘
```

The scan phase never calls an LLM and is fully unit-testable. The reasoning phase never parses
code itself — it hands the agent CLI a short instruction pointing at the manual files and the
evidence file already sitting on disk, and lets the agent's own filesystem tools read them. This
avoids inlining large payloads into CLI args/stdin and avoids Veridion owning any LLM billing or
API keys — it rides on the coding agent subscription the user already has.

## Components

```
Veridion/
  manual/
    part-1-operating-instructions.md   # verification rules, output contract, review stance
    part-2-repository-intelligence.md  # how to interpret evidence.repository
    part-3-git-intelligence.md         # how to interpret evidence.git
  veridion/
    cli.py                 # entrypoint: `veridion audit [path]`
    scanner/
      detect.py            # language/framework/build-tool detection from manifests
      graph.py             # tree-sitter module & dependency graph
    git_intel/
      analyzer.py          # branch staleness, commit cadence, ownership
    evidence.py            # assembles/writes .veridion/evidence.json, owns the schema
    adapters/
      base.py              # AgentAdapter interface
      claude_code.py        # ClaudeCodeAdapter (v1's only implementation)
    report.py              # invokes adapter, writes .veridion/audit-report.md
  tests/
    fixtures/              # small synthetic repos (Python+JS, and a scripted git history)
    test_scanner.py
    test_git_intel.py
    test_evidence_schema.py
    test_adapters.py       # subprocess mocked, no live LLM calls
```

**Language coverage (v1):** Python and JavaScript/TypeScript tree-sitter grammars only, matching
the dogfood target (Procta's FastAPI backend + Electron frontend). `detect.py`'s language registry
is structured so adding a grammar later is a registration, not a rewrite. Files in unsupported
languages are recorded in `unparseable_files`, not silently dropped.

**Agent adapter interface:**

```python
class AgentAdapter(ABC):
    name: str
    def is_available(self) -> bool: ...       # e.g. `which claude` on PATH
    def invoke(self, instruction: str, cwd: str) -> str: ...  # runs headless, returns output
```

`ClaudeCodeAdapter.invoke` runs the CLI in non-interactive/print mode with a short instruction
("read manual/*.md and .veridion/evidence.json in this directory, then produce an audit report
following the output contract in Part I") rather than passing file contents directly.

## Evidence schema (`.veridion/evidence.json`)

```json
{
  "veridion_version": "0.1.0",
  "scanned_at": "2026-07-14T12:00:00Z",
  "repo_path": "/abs/path",
  "repository": {
    "languages": [{"name": "python", "file_count": 42, "loc": 5000}],
    "frameworks": [{"name": "fastapi", "evidence": "requirements.txt:fastapi==0.110.0"}],
    "build_tools": [{"name": "..." , "evidence": "..."}],
    "monorepo": {"detected": false, "workspaces": []},
    "modules": [
      {
        "path": "app/auth.py",
        "language": "python",
        "imports": ["app.db", "app.config"],
        "imported_by": ["app.routes.login", "app.routes.signup"],
        "symbols": {"functions": ["..."], "classes": ["..."]}
      }
    ],
    "dependency_graph": {"nodes": ["..."], "edges": [["a", "b"]]},
    "unparseable_files": [{"path": "app/native/helper.swift", "reason": "no grammar registered"}]
  },
  "git": {
    "available": true,
    "branches": [
      {"name": "main", "type": "local", "last_commit_at": "2026-07-10T09:00:00Z",
       "stale_days": 4, "ahead_of_main": 0, "behind_main": 0}
    ],
    "commit_cadence": {"weekly_counts": [12, 8, 15], "trend": "flat"},
    "ownership": [{"author": "...", "commit_count": 120, "percent": 0.62}],
    "repo_age_days": 340,
    "total_commits": 512
  }
}
```

If the target repo has no commits (e.g. Veridion's own repo, right now), `git.available` is
`false` and no other `git` fields are populated — see Error Handling.

## Manual content (v1)

- **Part I — Operating Instructions.** Two tiers, explicitly ranked. *Primary, mandatory:*
  every claim must cite a specific `evidence.json` path/field; if evidence doesn't support a
  claim, the agent must write "not determinable from available evidence" instead of guessing;
  every major finding gets a stated confidence level (high/medium/low); the agent must never
  reference a file, function, or branch absent from `evidence.json`. A fixed output-section
  contract follows, so reports are structurally consistent. *Secondary, stylistic:* review-stance
  framing ("bias toward maintainability over cleverness," "review as a principal engineer would")
  — kept lightweight and explicitly subordinate to the verification rules, since tone framing
  changes vocabulary, not factual grounding.
- **Part II — Repository Intelligence.** Instructions for reading `evidence.repository`: what
  counts as noteworthy (high fan-in modules with no test coverage signal, circular import chains,
  single-file god-modules, coverage gaps from `unparseable_files`), and an explicit
  "do not speculate about languages/frameworks absent from the `languages`/`frameworks` arrays"
  rule.
- **Part III — Git Intelligence.** Instructions for reading `evidence.git`: what counts as
  noteworthy (long-stale branches, ownership concentration, cadence drop-offs), and an explicit
  rule that if `git.available` is `false`, the agent must state git intelligence is unavailable
  rather than inventing branch or commit history.

## Error Handling

- **No agent CLI found on PATH:** scan phase still completes and `evidence.json` is retained;
  the command exits non-zero with install instructions for a supported adapter.
- **Multiple agent CLIs found:** interactive prompt asking which to use (matching the pattern the
  user liked from repowise). In a non-interactive context (e.g. CI), an explicit `--agent` flag is
  required; omitting it is an error, not a silent default.
- **Target repo has no git history** (uninitialized or zero commits): `git.available: false`,
  Part II scanning still runs independently, nothing crashes.
- **Unparseable files** (no tree-sitter grammar registered): recorded in `unparseable_files`,
  scan continues; evidence coverage gaps are visible in the schema rather than silently dropped.
- **Agent CLI invocation fails or times out:** `evidence.json` remains on disk regardless (scan
  phase already completed and persisted before the reasoning phase starts), so nothing produced
  so far is lost; the error is surfaced with the evidence file's path.

## Testing Strategy

- **Scanner and git analyzer:** pure unit tests against small fixture repos checked into
  `tests/fixtures/` (a synthetic Python+JS repo with known imports; a scripted git history with
  known branches/cadence/ownership). No LLM involved — these are deterministic and CI-gated.
- **Adapter layer:** unit tests mock `subprocess` calls to verify correct invocation arguments and
  correct handling of "binary not found" / non-zero exit. No live LLM calls in CI.
- **End-to-end acceptance (not CI-automated):** a manual `veridion audit` run against Procta
  (proctored-browser), the author's own well-understood codebase, used as the go/no-go gate for
  calling v1 done — see Success Criteria.

## Success Criteria (v1 done)

1. `veridion audit .` completes end-to-end against Procta without crashing.
2. `evidence.json` contains a real, non-empty import graph covering both the FastAPI backend and
   the Electron frontend.
3. Every branch currently in the Procta repo appears in `evidence.json` with staleness/cadence
   numbers that match a manual `git log`/`git branch` spot-check.
4. `audit-report.md` contains zero claims about files, functions, or branches that don't appear in
   `evidence.json` (spot-checked).
5. Re-running against a repo with no git history (Veridion's own repo, today) does not crash —
   Part III degrades to "unavailable" instead of fabricating.

## Open Questions for Post-v1

- Where Part IV (architecture pattern review) and Part V (security) rank relative to each other
  once v1 is validated — not decided here.
- Whether the reasoning-phase agent should be allowed to attempt the deferred Part III
  outputs (Merge Order Matrix, Conflict Prediction) opportunistically, or whether that stays
  explicitly out of the manual until a later part formalizes it.
