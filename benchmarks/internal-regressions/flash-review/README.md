# Flash Review internal regression set

Real pull requests that Flash Review reviewed and called clean while a verifiable
defect was in them. This is **internal**: it is not part of the published PR-review
benchmark and is never compared with any other tool. (That corpus is fixed; changing
it would mean re-running every competitor.) It only answers one question: does Flash
Review, run as production runs it for a paid installation, catch these defects?

| Case | What Flash Review missed |
| --- | --- |
| `983-dead-import-breaks-test-patches` | Removing an "unused" import broke two tests in a file the PR did not touch that patch the name by string. |
| `998-degrade-to-empty-poisons-cache` | A dead worker's files were filled with an empty "no findings" result, which a per-file cache then stored as clean. |

Each case directory holds the as-submitted `pr.diff`, a `case.yaml` (base and head
commit, PR title, and what a catching finding must mention) and a `ground_truth.md`.

## Run it

```bash
cd github-app
# No model call, no cost: builds the real inputs and prompts against a stub adapter.
python3 ../benchmarks/internal-regressions/flash-review/run.py --dry-run

# Real run (needs INDIEROUTER_API_KEY, which is what production uses):
INDIEROUTER_API_KEY=... python3 ../benchmarks/internal-regressions/flash-review/run.py --runs 3
```

To try another reviewer on the same inputs, `--provider haiku` runs Claude Haiku (default
`claude-haiku-4-5-20251001`) at production's temperature of 0.2 with a hard spend guard
(`--max-usd`, default $1, checked before each run). It reads the key from `ANTHROPIC_API_KEY` or
the CLI's saved credentials. At list price one run of both cases is a few cents.

Running the real production configuration needs the IndieRouter key, which lives only on the
production server. Run it there from a throwaway container built from the scan-worker image,
passing only that one variable and mounting the repo read-only, so no other secret is exposed.
`git clone --shared` is used because a read-only mount cannot be hardlinked, and git needs a
global `safe.directory` exception (a `-c` flag is not enough for a local clone of a repo owned
by another user).

`run.py` mirrors the current paid-tier call in `scan_worker/jobs.py`: GLM-5.3-Flash
through `flash_review_generation_adapter`, `per_file_completeness` and `rank_findings`
on, the cross-file check off (its production default), shared PR context on,
sibling-file context off, no similarity cache. Inputs come from a checkout of the PR's
head commit. If production's call changes, update `run.py` to match, or this stops
measuring what production does.

A case counts as caught when a finding mentions every concept group in `case.yaml` (and
sits in an expected file, where the case names any). That is a keyword check, so read the
findings in `results/<timestamp>/results.json` too. The model is not deterministic, so use
several runs. Results are git-ignored.

## Cost

Flash Review averaged about $0.0045 per review in production (485 reviews over 30
days, maximum $0.088). The dry run measures one run of both cases at roughly 38,500 prompt
tokens over 8 model calls.
