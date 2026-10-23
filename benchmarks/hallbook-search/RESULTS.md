# Hallbook search quality results

This file records the held-out benchmark before and after the search-quality work. The benchmark has 994
queries, split by skill into 487 dev queries and 507 test queries. The baseline is commit `2839d03b`; the final
numbers are in `after_all.json`.

All runs used a temporary `HOME` so custom skills on the developer machine could not affect the held-out
catalog. The command was:

```sh
cd src
HOME="$(mktemp -d)" python ../benchmarks/hallbook-search/eval.py --split all
```

## Held-out ranking and confidence

| Split | Top1 | Top3 | Top5 | High precision | High results (wrong) | Correct high kept |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dev before | 0.4189 | 0.6099 | 0.6407 | 0.7857 | 84 (18) | 66 of 66 |
| Dev after | 0.4476 | 0.6345 | 0.6920 | 0.8714 | 70 (9) | 61 of 66, 92.4% |
| Test before | 0.4260 | 0.6351 | 0.6824 | 0.7755 | 98 (22) | 76 of 76 |
| Test after | 0.4872 | 0.6667 | 0.7041 | 0.8800 | 75 (9) | 66 of 76, 86.8% |
| All before | 0.4225 | 0.6227 | 0.6620 | 0.7802 | 182 (40) | 142 of 142 |
| All after | 0.4678 | 0.6509 | 0.6982 | 0.8759 | 145 (18) | 127 of 142, 89.4% |

Correct high results fell by 13.2% on test, inside the 15% budget. Precision rose on every split, and the
number of wrong high answers fell from 40 to 18 across all queries.

## Typo recovery

The typo metric was added with goal 1, so these rows compare the first measurable typo run with the final
run. Each value is `typo_top3`, with the clean-query result in parentheses.

| Split | First typo run | Final |
| --- | ---: | ---: |
| Dev | 0.5732 (0.6082) | 0.6124 (0.6351) |
| Test | 0.6079 (0.6376) | 0.6634 (0.6693) |
| All | 0.5899 (0.6232) | 0.6384 (0.6525) |

The final typo numbers are stable across processes. Candidate order in the one-edit vocabulary and prefix map
was made deterministic after an earlier run moved between 0.6020 and 0.6059 on test.

## Non-task prompts

The expanded prompt set has 63 non-task prompts and 60 task prompts.

| Split | Non-task `none` | Task `none` |
| --- | ---: | ---: |
| Dev | 63 of 63 | 0 of 60 |
| Test | 63 of 63 | 0 of 60 |
| All | 63 of 63 | 0 of 60 |

The baseline had 10 non-task prompts and 0 of 10 returned `none`.

## Prompt hook

The hook cases were added with goal 4. There was no baseline run for them.

| Split | Follow-ups silent | Clear tasks that fired |
| --- | ---: | ---: |
| Dev | 12 of 12 | 8 of 9 |
| Test | 12 of 12 | 8 of 9 |
| All | 12 of 12 | 8 of 9 |

The one clear task left silent is "make the landing page faster on mobile". Its top two skills are within the
existing 5% ambiguity guard, so the hook deliberately does not choose between them.

## What changed, goal by goal

1. `59d5d487` added seeded one-edit typo recovery. A query token with no exact match can use a vocabulary token
   one edit away, restricted by first letter and length and down-weighted to 0.55.
2. `7e5c2bac` made `find_skill` return `none` for small talk, trivial edits, chat questions and weak scattered
   matches. It added the task and non-task prompt sets and the `non_task_none` and `task_none` metrics.
3. `ca6340c9` required a high-confidence result to cover at least 80% of the task's content words. This raised
   precision and cut wrong high answers while keeping 86.8% of correct high answers on test.
4. `b14a8ee0` made the hook silent for follow-up questions and medium hints cover at least half the prompt.
   It added per-session skill dedupe in an atomic JSON file under `~/.config/aletheore/hallbook_sessions/`,
   pruned after seven days.
5. `12f2a396` indexed custom skills by title, frontmatter description, headings and body with the same BM25F
   fields. The cache is keyed by path, mtime and size, and custom results stay capped below the high bar and
   out of the runner-up check.
6. `765d7b9a` sorted the fuzzy and prefix candidate maps so typo results do not depend on Python set order.
7. `9c4f67d4` penalised each name token the query did not ask for. This stopped long, specific skill names
   from beating general skills. `postgres migration` now puts `data/database-migration` third, and every
   top-k and typo metric improved. It also added the 15-query top-three regression list.
8. `ee804150` added the hook throttle: one hint every three prompts in a session, tracked in the same state
   file without changing the evaluation metrics.

## Tried, rejected or refined

- Name-specificity penalties of 2.5 and 3.5 were tried on dev. 2.5 had a slightly higher top1 but left
  `postgres migration` at 4th and lowered top5. 3.5 lowered typo recovery and top5. 3.0 was kept.
- The first custom-skill score floor was too low for exact-name custom skills, which pushed them out of the
  alternatives in an existing regression test. The floor was raised within the same capped range.
- The hook word minimum was raised from 5 to 6 words so "add a stripe webhook handler" stays silent, as the
  brief asks. The medium coverage floor did not do that on its own after goal 6 changed the ranking.
- No goal was reverted. Test metrics improved on every tracked ranking metric.

## Known limits

- The held-out set measures bundled skills only. Custom content search is covered by unit tests, not by the
  994-query corpus.
- `postgres migration` still ranks the Oracle-to-Postgres skill first because its description contains that
  exact phrase. The general `data/database-migration` skill is third, which is the goal.
- Typo recovery handles one insertion, deletion, substitution or adjacent swap in words of five or more
  letters, and keeps the first letter. It is not a general spell checker.
- Custom skills use a coverage floor because a one-file corpus has tiny IDF values. They are kept below the
  high bar, so a very strong custom match is shown as a low-confidence candidate.
- Hook state is best effort. If the file cannot be read or written, the hook may repeat a suggestion or skip
  the three-prompt throttle; it never fails the prompt.
- Two tests in the full suite under `pytest -n 4` failed in this terminal because of xdist and terminal width,
  and pass when run directly. The 662-test relevant subset used for these commits passes.
