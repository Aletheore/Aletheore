# Review-gain experiment, first run (2026-11-16): no evidence of a gain, and the test had no room for one

Task: review 12 real networkx 3.7 modules holding 24 injected bugs (each confirmed by networkx's own tests). 3 replicates per condition with the real Claude Code and
Codex CLIs. Criteria were written before any results (`PREREGISTERED.md`).

| condition | bugs found (rep 1, 2, 3) | mean | wall-clock (mean) |
|---|---|---|---|
| Claude alone | 24, 24, 24 | 24.0 | 70 s |
| Codex alone | 23, 22, 21 | 22.0 | 81 s |
| split (3 agents in parallel, fixed 4 to 5 files each, no planner) | 22, 22, 22 | 22.0 | 44 s |
| council (planner + 3 executors, through the room) | 21, 24, 22 | 22.3 | 67 s |

Preregistered test (council mean beats both single agents by 3 or more, and its worst run beats their best run): **not met**. It could not have been met: Claude alone found
all 24 in every replicate, so there was nothing left to gain.

Things that matter when reading this
- **No headroom.** One Claude agent finds every bug in about a minute. A review of 12 files is within a single agent's ability, so delegation cannot improve accuracy.
- **My cap handicapped the multi-agent conditions.** Executors were limited to 8 findings, but the file groups held 6, 8 and 10 bugs, so a 10-bug group loses at least two by
  construction. Split scored exactly 22 in all three replicates for this reason (its parts found 6, 8 and 8 in replicate 3). The council could do better than 22 only because
  the planner can read the code and add findings (it added a bug in all three replicates). Split versus the others is therefore not a fair comparison in this run.
- **Speed.** Split was the fastest (44 s against 70 s), so parallel work does save time, but the council's four planner turns used the saving up (67 s, the same as Claude alone).
- **The planner rarely lost findings.** Executors' union against the planner's final review: 20 to 21, 23 to 24, 21 to 22 (it dropped one correct finding in one replicate and
  added one or two it found itself in each).
- **Scoring.** A bug counts when the review names its function or file and line and a judge (Claude, no tools, validated on hand-written reviews) says it describes the same defect.
  Precision is not measured. The judge call on the three joined split reports returned nothing in replicate 3, so split is scored as the union of per-part judgements for all
  replicates (the same judge on smaller texts). Replicate 3's split and council were rerun after a harness race (three threads shared one config file); the pilot (replicate 0) is
  excluded, and a product fix (earlier room messages cut at 600 characters, now 6000) was made between the pilot and these runs.
- **Which bugs were hard.** Everything was found in all replicates by Claude alone. Codex alone missed M07, M15, M19, M20, M23 in some replicates; the council missed M05, M08, M13, M20, M23 in some.

What would show a gain: a task a single agent cannot do in one turn. Next version: many more files (the whole networkx algorithms folder, about 150 files, with 60 injected bugs)
so that one agent's context runs out, executors capped by file count rather than a fixed 8, and the same preregistered comparison.
