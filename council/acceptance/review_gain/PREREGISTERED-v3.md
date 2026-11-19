# Review-gain experiment v3: criteria written before any results (2026-11-19)

Why v3: the networkx runs (v1, v2) are invalid (see INVALIDITY.md): a model can reproduce networkx files from memory and diff them, and the answer key was reachable.
v3 uses four lesser-known libraries, funcy 2.1, parse 1.22.4, pyrsistent 0.20.0 and schema 0.7.8 (about 8,200 lines of source in 23 files), none of them installed on this machine.
Each of 41 injected bugs is a single-token change that the library's own tests catch. Every pristine copy was deleted after the corpus was built; the corpus clones have no
remote and no history; the answer key is encrypted (its key is in the keychain, not on disk beside it); no agent has network access (Claude has no shell, Codex runs in a
read-only sandbox). Every command and file read of every agent is logged and audited.

Conditions (3 replicates each, real Claude Code and Codex CLIs, same task, same total cap of 3 findings per file = 69):
  claude-alone, codex-alone   one agent, one read-only turn, all 23 files
  split                       four agents in parallel (Codex, Claude, Codex, Claude), about 6 files each, no planner, reports joined
  council                     a planner (Claude) splits the files across the same four executors by mentioning them; they work in parallel and report back; the planner writes the
                              final review; one person message and nothing after it
Score: bugs found of 41 (location match, then a judge that must find the same defect). Precision is not measured.

Validity rule (new): a condition whose audit flags any oracle-seeking command (searching the disk for other copies, git remote, reading outside the corpus, the network, the
agent's memory files, the answer key) is not reported as a result; it is reported as contaminated and rerun.

Gain is claimed only as follows (a screen with 3 replicates, not a significance test):
  A. Accuracy gain: the council mean beats the better single agent's mean by at least 4 bugs (about 10 percent) AND the worst council replicate beats the best replicate of either
     single agent.
  B. Speed gain at equal accuracy: the council mean is within 2 bugs of the better single agent's mean AND its mean wall-clock is at most 70 percent of that agent's.
  Neither: reported as no evidence of a gain. Attribution: if split is within 2 bugs of the council on mean, any gain is from splitting work in parallel, not from the planner.
A run that fails is retried up to three times; no replicate is dropped for a poor score.
