# Brief S6: make the prompt hook fast and let hint mode fire on natural prompts
Read `briefs/CONTRACT.md` and `common.py`, then `briefs/s5_hook.md` (what exists). You may edit: `../src/aletheore/skill_hub.py`, `../src/aletheore/cli.py`,
create `../src/aletheore/hallbook_hook.py`, edit `../src/tests/test_skill_hub.py` and `../src/tests/test_cli.py`, and create `calibrate_hint.py`, `measure_hook.py` (update). Nothing else.

## Problems measured
1. `aletheore hallbook suggest` takes ~570 ms per call (Python start plus importing the whole CLI). Target: under 200 ms.
2. On 30 realistic chatty prompts only 1 produced a suggestion (3%), on benchmark queries 44%. `find_skill`'s "high" bar (precision 85-89%) is tuned for loading a whole
   skill. A hint costs ~180 characters, so hint mode can use a looser bar.

## Build
1. Speed: create `aletheore/hallbook_hook.py` with `main(argv=None)` that imports ONLY the standard library and `aletheore.skill_hub` (check with
   `python -X importtime -c "import aletheore.skill_hub"` that skill_hub does not pull in heavy modules such as typer, rich, httpx, tree_sitter, networkx; if it does, make those imports
   lazy inside the functions that need them, without changing behaviour). It reads stdin, calls `hook_suggestion`, prints, exits 0, swallows every exception. `python -m aletheore.hallbook_hook --mode hint|inject`
   must work. Make `aletheore hallbook suggest` delegate to the same function. Change `aletheore hallbook hook` so the printed settings snippet uses the command
   `"<sys.executable> -m aletheore.hallbook_hook --mode <mode>"` (quote the path if it has spaces). Measure the wall time of 20 subprocess calls (median and p95) and report it.
2. Looser hint tier: add `hook_suggestion` behaviour for mode "hint": confidence "high" -> as now (1 skill). Otherwise compute a "medium" tier from `search_skills_scored`: top score >= HINT_MIN_SCORE and
   top score >= HINT_MIN_RATIO * second score (constants, with a comment). For medium, hint names the top TWO skills: `Hallbook may have a skill for this: <id1> (<title1>) or <id2> (<title2>). If one applies, load it with aletheore_get_skill; otherwise ignore this.`
   Calibrate HINT_MIN_SCORE and HINT_MIN_RATIO in `calibrate_hint.py` on the DEV half of `data/queries_stage4.jsonl` (dev = `int(md5(skill_id),16)%2==0`), cached scores like `calibrate_confidence.py` does:
   choose the loosest values for which hit@2 (target among the top two) is at least 60% on DEV, then report TEST, `data/queries_heldout.jsonl`, and the 30 chatty prompts (write them again in `data/chatty_prompts.json`,
   realistic: "can you look at why my login test is flaky", "I need to add stripe webhooks", "this query is slow", plus 10 non-task prompts like "thanks", "rename this variable", "what does this function do"
   that should NOT fire; report both the share that fire on the task prompts and the share that wrongly fire on the 10 non-task ones; aim for under 20% wrongly firing).
   Inject mode keeps using the strict "high" bar.
3. Tests for the new behaviour (medium tier produces two ids, nothing fires for non-task prompts, module runs via `python -m`, hook snippet contains the interpreter path); all test files must pass.
Final report: before/after timing, calibration table (dev/test/held-out hit@2 and fire-rate), chatty-prompt results.
