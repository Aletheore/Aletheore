# Why the networkx review-gain results (v1 and v2) cannot be trusted (found 2026-11-18)

While checking a suspiciously perfect result (Codex alone: 58 of 60 injected bugs, every finding exact, no false alarms, in 141 s), the shell commands Codex ran were logged
(`diag.py`). What it did:

1. It ran `rg` for suspicious patterns (`== 0`, `!= 1`, comparisons), then for each file it **wrote the original file out from memory** (each command embeds a 25 to
   32,000-character pristine copy of the module) and diffed the corpus file against it token by token with `difflib`. networkx is one of the most copied libraries on the
   internet, so a model can reproduce its files. What was measured was recall of memorised source, not code review.
2. It searched the disk for another copy of the library (`find / -path '*/networkx/...'`, `ls .../site-packages/networkx`).
3. It ran `git remote -v` in the corpus clone and then listed the folder the clone came from. **My clones kept `origin` pointing at the corpus folder, and the answer key
   (`manifest.json`) sits next to it.** The commands I inspected show the listing, not a read of the file, but the path to the key was exposed to every agent in v1 and v2.
4. It read `~/.codex/memories/MEMORY.md` first (agent-side memory outside the experiment).

What this does to the results
- v2 (60 files) numbers for anything involving Codex (codex-alone, split, council executors that are Codex) are measuring memory plus diffing, and are not reported.
- v1 (12 files): Claude alone found 24 of 24 every time; Claude has no shell, but it can read any file by path and knows networkx. The "single agent is already at the ceiling"
  conclusion may be partly memorisation. v1's "no evidence of a gain" stands as stated, but the numbers should not be read as how good agents are at reviewing unfamiliar code.

What a valid version needs (v3)
- Code the models cannot have memorised and no pristine copy on disk: lesser-known libraries fetched into a temporary folder, mutated and validated there, with every
  pristine copy and the package cache deleted afterwards. The review agents run with no network (Codex's read-only sandbox, Claude has no shell).
- Clones with no remote and no history; the answer key encrypted, with the key held only in the harness's environment, never on disk.
- Log every shell command and file read of every agent, and check them for oracle-seeking (searching for other copies, reading anything outside the corpus) before reporting a number.
