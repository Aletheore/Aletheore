# Review-gain experiment: criteria written before any results (2026-11-16)

Task: review 12 networkx modules holding 24 test-confirmed injected bugs. Same task text and the same cap of 25 findings in every condition
(executors in `split` and `council` report at most 8 each). Conditions: claude-alone, codex-alone, split (3 agents in parallel, fixed 4 files each, no planner,
reports joined), council (planner + 3 executors through the room, no person after the first message). 3 replicates each. Score: bugs found of 24 (location match
+ judge). Precision is not measured; the cap keeps guessing from paying.

A real gain from Council planning is claimed only if, on bugs found:
  1. the council mean beats BOTH single-agent means by at least 3 bugs, AND
  2. the worst council replicate beats the best single-agent replicate of either agent.
Otherwise the result is reported as no evidence of a gain (with 3 replicates this is a screen, not a significance test).
Wall-clock is reported separately (council vs the single agents). `split` is reported to separate the value of splitting work from the value of Council's planning:
if split matches council, the benefit is parallel splitting, not the planner.
