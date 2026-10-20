# Brief S3: one-call skill lookup that saves agent tokens
Read `briefs/CONTRACT.md` and `common.py` first. Precondition: `skill_hub.search_skills_scored` exists (built by the S2 builder). You may edit:
`../src/aletheore/mcp_server.py`, `../src/aletheore/skill_hub.py` (only the snippet, `AGENT_INSTRUCTION_SNIPPET`, and a new function
`find_skill`), `../src/aletheore/cli.py` (only if the snippet command needs it), `../src/tests/test_skill_hub.py`,
`../src/tests/test_mcp_server.py`, `calibrate_confidence.py` (create), `data/*` you create. Nothing else.

## Why
Agents currently do search -> (search again) -> get_skill: every extra step re-reads the whole conversation, and a loaded skill
that does not fit hurts (a 2026 benchmark of agent skills found a few skills lowered pass rates by up to 10 points, and another
study found agents fail at deciding which skill to load; the safe design returns a skill only when confident, and says "no
relevant skill" otherwise). We want ONE call that either hands over the right skill or tells the agent to move on.

## Build
1. `skill_hub.find_skill(task, category=None) -> dict` using `search_skills_scored(task, limit=5)`:
   - `confidence`: "high" | "low" | "none". "none": no result. "high": top score >= T1 AND (top score - second score) >= M or top/second >= R.
     Otherwise "low". Calibrate T1, M, R in `calibrate_confidence.py` on the benchmark queries (`data/queries_stage4.jsonl`, dev
     half = `int(md5(skill_id),16)%2==0`): among queries answered "high", the top result must be the target (or an acceptable
     duplicate: same stemmed name tokens) at least 85% of the time, while still answering "high" for as many queries as possible.
     Report on the test half and on `data/queries_heldout.jsonl` if it exists (created by the S2 builder; do not tune on it).
     Also make sure vague junk ("help me", "do the thing") gives "none" or "low". Store the chosen constants as named module constants with a comment.
   - result dict: `confidence`, `top` (skill_id, title, description), `alternatives` (up to 3: skill_id, title, one-line description <= 140 chars).
     For "high" also `skill`: the full `get_skill` result as a dict (content capped at 14,000 chars; if capped add `truncated: true` and say to
     fetch the rest with aletheore_get_skill), and a `note`: "Apply this guidance to the current project. Examples use sample values; do not copy them blindly. Skip steps that do not apply."
     For "low": `candidates` (up to 5 compact ones) and `note`: "No clear match. If none of these fits, continue without a skill and do not search again for the same need."
     For "none": `note`: "No relevant skill. Continue without one."
2. MCP tool `aletheore_find_skill(task: str, category: str | None = None) -> str` returning the dict via the existing `_toon_result`. Its docstring
   must say: call ONCE per task, early, with one sentence describing what you are about to do (the technology and the kind of work);
   skip it for trivial edits and simple questions; if the answer is low/none just carry on. Keep the other tools; shorten
   `aletheore_search_skills` output: description <= 160 chars, drop the license field from each list row (still present in get_skill),
   and say in its docstring that `aletheore_find_skill` is the usual first call.
3. New `AGENT_INSTRUCTION_SNIPPET` (<= 6 lines): for a non-trivial task that a specialist skill might cover (debugging, review, testing, planning,
   security, an API or platform integration, SEO, design), call `aletheore_find_skill` once with a one-sentence task description; if it returns a skill,
   follow it and adapt it to this project; if the match is low or none, carry on without it and do not keep searching; skip it for trivial edits.
   Mention `aletheore_get_skill_file` for files the skill points at. Update any test or doc string that quotes the old snippet text.
4. Tests: confidence high for 5 clear queries taken from the benchmark data, none for gibberish, low for a vague one, the size cap works (use a
   monkeypatched long skill), tool is registered, snippet mentions the tool name, search rows no longer contain license.
5. Measure: write `measure_tokens.py` that, for 40 random benchmark queries, prints the character size of (a) old flow: search result (limit 8, old format taken from git HEAD
   via `git show HEAD:src/aletheore/mcp_server.py` is NOT needed; just compute the new search row sizes) and (b) find_skill responses by confidence class, and the share of
   queries ending in "high". Report the calibration table (constants, precision/coverage on dev, test, held-out) and the measurements.
