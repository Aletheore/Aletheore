# Review-gain experiment v2 (large): criteria written before any results (2026-11-17)

Why a second experiment: in v1 (12 files, 24 bugs) one Claude agent found every bug in about a minute, so there was nothing for a pipeline to add (see RESULTS.md).
v2 makes the task bigger than one agent can read in one turn: 60 networkx algorithm modules (about 46,000 lines, one injected and test-confirmed bug in each, 60 bugs).

Conditions (3 replicates each, the real Claude Code and Codex CLIs, same task text, same total cap of 1.5 findings per file = 90 findings):
  claude-alone, codex-alone   one agent, one read-only turn, all 60 files
  split                       six agents in parallel (Codex, Claude, Codex, Claude, Codex, Claude), about 10 files each, no planner, reports joined
  council                     a planner (Claude) splits the files across the same six executors by mentioning them, they work in parallel and report back, the planner
                              writes the final review; one person message, then nothing
Score: bugs found of 60 (location match, then a judge that must find the same defect); precision is not measured, the cap keeps guessing from paying.

Gain is claimed only as follows (this is a screen with 3 replicates, not a significance test):
  A. Accuracy gain: the council mean beats the better single agent's mean by at least 6 bugs (10 percent of the bugs), AND the worst council replicate beats the best
     replicate of either single agent.
  B. Speed gain at equal accuracy: the council mean is within 3 bugs of the better single agent's mean AND its mean wall-clock is at most 70 percent of that agent's.
  Neither: reported as no evidence of a gain.
  Attribution: if the split condition is within 3 bugs of the council on mean, any gain is from splitting the work in parallel, not from the planner's coordination.
A single agent that times out or fails is scored on whatever text it returned (nothing means 0) and flagged; no replicate is dropped for a poor score.
Changes made before this run, from the pilots: the wake prompt is built to a budget (job and message always fit); a worker keeps 40 earlier messages; the planner's wake
budget is raised to cover six executors; the judge works in batches and a judge failure is recorded, never scored as zero.
