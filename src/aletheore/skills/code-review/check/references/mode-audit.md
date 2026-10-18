# Project Audit Mode (project-wide scorecard)

Loaded from `check` Mode Picker when the request is a project-wide quality scorecard. Distinct from default review (diff-scoped) and triage (issue batching).

Single-pass project-wide quality assessment.

**Flow**

1. Run `python3 <skill-base-dir>/scripts/audit_signals.py --root <project>` from the target repo, with `<skill-base-dir>` replaced by this skill's base directory. The script emits labelled blocks (`=== FILE SIZE HOTSPOTS ===` ... `=== DENYLIST IN BUILD ===`) each ending with `status: PASS|WARN|FAIL|N/A`.
2. Skim the largest source files surfaced by `FILE SIZE HOTSPOTS` (typically 3-5; stop sooner if the architecture is already clear).
3. Read your agent's instructions file / `AGENTS.md` / `README.md` to learn the project's own stated conventions before judging it against generic ones. The repo's agent guidance itself is part of the audited surface: verify its commands and paths still exist, and report stale, conflicting, or deletable rules as findings.
4. Apply the four-axis rubric below. Each axis is independently scored 0-10. Overall = arithmetic mean.
5. Report every finding that moves an axis score, each with file:line citation when possible, severity (CRIT/STRUCT/INCR), and a one-line fix. Zero findings on an axis is a valid result; do not pad to a quota.
6. Output to **terminal only**. Do not create files in the target repo. If the user follows up with "save it", offer `./docs/<project>-audit.md` then; default is ephemeral.

**Rubric**

| Axis | What it covers |
|---|---|
| Architecture | Module boundaries, coupling, abstraction layers vs flat duplication, single source of truth |
| Code Quality | File size discipline, dedup, readability, comments on non-obvious behavior |
| Engineering | Tests, CI gates, version coordination, install URL pinning, packaging posture |
| Perf and Risk | Hazards, scope creep, distribution risk, privacy posture, third-party blast radius |

**Scoring anchors**

- 9-10: exceptional discipline, polish-only items
- 7-8.5: solid with clear targeted improvements
- 5-7: working but with structural debt
- below 5: significant rework recommended

A WARN that the project has explicitly justified (in its own docs or a comment) is not a finding; cite the justification and skip. Do not mechanically convert WARN to CRIT. A block with `status: N/A` means the surface does not exist (e.g. no packaging script); treat as silence, not as a positive signal.

**Output template (terminal)**

```
Project: <name>
Overall: X.X / 10

Architecture: X / 10 -- one-line summary
Code Quality: X / 10 -- one-line summary
Engineering:  X / 10 -- one-line summary
Perf & Risk:  X / 10 -- one-line summary

Findings
[CRIT] <file:line> -- <issue>
       why: <reason grounded in signal or read>
       fix: <concrete action>
[STRUCT] ...
[INCR] ...

Top 3 highest-leverage moves
1. ...
2. ...
3. ...
```

Stop after the report unless the user asks for follow-up implementation. Audit mode does not modify files in the target repo.
