PR #983 (as first submitted) removed `model_for_plan` from the import list in `github-app/scan_worker/jobs.py` as a dead import. Nothing in `jobs.py` calls it, but two tests in `github-app/tests/test_jobs.py` do `monkeypatch.setattr("scan_worker.jobs.model_for_plan", ...)`, which only resolves because the name is imported there. After the change both raise `AttributeError`, and CI failed on `pytest`.

Flash Review said "No issues found". The tests that break are in a file the PR does not touch and reference the name only inside a string, so the import graph does not link them: catching this needs a repo-wide search for the removed name, not the diff alone.

Real outcome: fixed by deleting the two stale patch lines (commit 721eda31).
