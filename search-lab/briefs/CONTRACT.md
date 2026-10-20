# Contract for search-lab builders
You are a builder agent working in this folder (`search-lab/`) inside a git worktree of the Aletheore repo.
- Edit ONLY the files your brief names. Never edit `common.py` or `delegate.py`. Never run state-changing git commands (no add/commit/checkout/stash/reset).
- Network only through `common.chat(...)` (cheap model calls) and `common.run_agent(...)`. Never print or write the API key.
- Python 3, standard library only for scripts you write. Import the Hallbook code through `common.use_clean_source()` BEFORE `import aletheore...` (it puts the worktree's `src/` on the path). The package source is in `../src/aletheore/` (relative to this folder), skills in `../src/aletheore/skills/`.
- Long jobs must be resumable (rows in a jsonl keyed by a unique "key") and print progress. Never run a job without `--pilot` first (a tiny subset), check the output, then the full run.
- Run tests with `cd ../src && python3 -m pytest tests/test_skill_hub.py tests/test_mcp_server.py -q` (all must still pass).
- Be lean: do not print large files; use head, grep and line ranges.
- At the end reply with a short report: what you built, exact commands to rerun, numbers, anything you could not do.
