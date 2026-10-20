# Brief S1: generate search "intents" for every skill (document expansion)
Read `briefs/CONTRACT.md` and `common.py` first. Create `gen_intents.py` (you may also create `data/intents.jsonl`
by running it). Goal: for every bundled skill, a list of 8 short, varied things a person might type to an AI coding
agent when they need that skill, so that keyword search can match everyday wording that the skill's own text lacks.
Skills: `skill_hub.list_bundled_skills()` (1,124) and `skill_hub.get_bundled_skill(id)` (title, description, content).
Model: `common.chat(prompt, model="deepseek-v4-flash", max_tokens=400, temperature=0.9, stage="s1")`. NOT glm (it wrote
the evaluation queries; we want an independent source).
Prompt rules (put them in the prompt): input is the skill's title, description and the first 1,200 characters of its body.
Write 8 different requests, 6 to 22 words each, in plain everyday wording as a working developer, designer, marketer or
founder would type them. Vary them: 3 describe the situation or problem (no tool jargon), 2 are short goal statements,
2 use different synonyms and technologies a user might name, 1 is phrased as a question. Do not mention "skill". Do not
copy sentences from the input. One request per line, no numbering, no quotes, nothing else.
Parse: strip bullets/numbers/quotes, drop lines under 4 or over 30 words or containing "skill", dedupe, keep up to 8.
If fewer than 5 are valid, retry the call once. Row: `{"key": skill_id, "skill_id", "intents": [...]}`.
8 threads (ThreadPoolExecutor), resumable, `--pilot` = first 6 skills (print them). The full run is ~1,124 calls;
it costs a few rupees; after the pilot looks good run it fully and report count of skills with >= 5 intents.
Also write `data/intents.json` = `{skill_id: [intents]}` (sorted keys, indent 0) from the jsonl at the end of the run.
