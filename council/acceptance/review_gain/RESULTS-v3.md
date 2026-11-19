# Review-gain v3 (2026-11-19): no evidence that planner-led Council beats one agent; a plain parallel split matches one agent in a third of the time

Corpus: funcy 2.1, parse 1.22.4, pyrsistent 0.20.0, schema 0.7.8 (about 8,200 lines, 23 files), 41 injected single-token bugs each confirmed by the library's own tests. None of the
libraries is installed on the machine, every pristine copy was deleted, clones have no remote or history, the answer key is encrypted, every agent command is logged and audited.
Criteria were committed first (`PREREGISTERED-v3.md`, with one addendum written before any score was examined). 3 replicates per condition, real Claude Code and Codex CLIs.

| condition | bugs found of 41 (rep 1, 2, 3) | mean | wall-clock (mean) | clean runs |
|---|---|---|---|---|
| Claude alone | 38, 39, 39 | 38.7 | 182 s | 3 of 3 |
| Codex alone | 36, 41, 38 | 38.3 | 169 s | 3 of 3 |
| split (4 agents in parallel, fixed groups, no planner) | 39, 39, 39 | 39.0 | 64 s | 3 of 3 |
| council (planner + 4 executors) | 34 | 34.0 | 89 s | **1 of 3** |

Against the preregistered criteria
- A (accuracy gain: council mean at least 4 bugs above the better single agent AND its worst run above their best run): **not met, and could not have been met**: it needs a council mean
  of at least 42.7 on a corpus of 41 bugs, because the single agents already average 38.7.
- B (speed gain at equal accuracy: council within 2 bugs of the better single agent AND at most 70 percent of its time): **not met**: the one valid council run found 34 (needs 36.7 or more).
- Attribution: the **split** matched the single agents on accuracy (39.0 against 38.7) in 64 s against 182 s, 35 percent of the time. Splitting the reading across parallel agents is
  what saves time; the planner added coordination (89 s) and, in the valid run, lost accuracy.
- In the valid council run the executors together reported 26 of 41 bugs; the planner's final review had 34, because it read the code itself and added 9 (and dropped 1).

Validity: what to be careful about
- **Only one council run is valid.** In 6 of the 7 council attempts the agents ran shell commands that looked for the original code (`pip download parse==1.20.2 schema --no-deps`, `pip list | grep`,
  `import parse; print(parse.__file__)`). Under the preregistered rule those runs are contaminated and excluded; each of replicates 2 and 3 was rerun three times and the last attempt was still
  flagged. Their scores (37, 38 for replicate 2 and 37, 34 for replicate 3) are not counted. None of the 12 solo and split runs made such an attempt. The Claude agents in these runs have a shell
  and network access (the user's own Claude settings allow it); the audit records attempts, so no flagged run is used, but a council score cannot be produced without blocking that access.
- One valid council replicate is not a measure of its spread. The conclusion "no gain" rests mainly on the single agents already being near the ceiling and on split matching them.
- The corpus is small: one agent can read all of it in a few minutes (Codex's 41 of 41 came from reading every file with `nl -ba`, checked in its command log). A gain would need a task one agent
  cannot do in one turn.
- Eight bugs were missed at least once by some clean condition. The two hardest were in `schema` (M41 `if schema.name and not title`, M39 `break` to `continue` in `Schema.validate`): Claude alone and
  the split missed both in all three replicates, Codex alone found them 1 and 2 times. Precision (false alarms) is not measured; the cap of 3 findings per file limits guessing.
- The judge: a Claude call that must find the same defect; it sometimes corrects itself after its first answer, and the last JSON answer is used (a parsing bug on this had made earlier runs fail to score).

What would change the answer
- Block shell and network for the agents (Claude's tool list, for example `--disallowed-tools Bash WebFetch WebSearch`) in every condition, and rerun all four. That would give three valid council replicates.
- Use a task bigger than one agent handles in one turn (hundreds of files), or work other than review (several executors writing independent modules) where parallel speed-up is the point.
