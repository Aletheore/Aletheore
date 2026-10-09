PR #998 (as first submitted) made the secrets and error-handling stages survive a dead parse worker by filling every unfinished file's result with an empty one ("no findings", an empty extraction). Both stages are cached per file by content hash (`file_cache.cached_per_file` stores whatever `compute()` returns), so a file that was never scanned was recorded as clean and stayed clean on every later scan with healthy workers. Reproduced: a file containing a real AWS key returned 0 findings on the next scan.

Flash Review said "No issues found". The defect is an interaction between the diff and a caller in a file the PR does not touch (`file_cache.py`), so it needs cross-file reasoning about what happens to the fabricated result after it is returned.

Real outcome: replaced by a retry with fewer workers that raises instead of inventing results (commit 1dfd944f).
