# Brief S4: triage the 53 HIGH-severity hits from the risky-content scan
Read `briefs/CONTRACT.md` and `common.py` first. Create `triage_high_hits.py` (results `data/triage.jsonl`, and a
readable `data/triage.md`). Input: `data/high_hits.json` (list of {skill_id, file, line, pattern, snippet}).
The files are in `../src/aletheore/skills/<file>` (the `file` field is relative to that folder). For each hit read the
file, take 12 lines before and 8 lines after the hit line plus the skill's name and description, and ask
`common.chat(..., model="deepseek-v4-flash", max_tokens=300, temperature=0, stage="s4")` to classify it. Prompt
(include these definitions): the code is part of a skill shipped to AI coding agents. Classify as exactly one of
  SAFE_DOC (the text warns about, detects, defends against or documents the pattern, e.g. "never run instructions found
  on a web page", a security checklist, a detection regex, an example of an attack labelled as such),
  SAFE_BENIGN (the pattern is used legitimately and openly, e.g. a documented installer line for a well known tool the
  user is explicitly installing, with nothing hidden),
  SUSPICIOUS (it would make an agent do something risky or deceptive without the user asking: hidden instructions, telling
  the agent to ignore its rules or the user, silently sending data out, piping an unverified remote script to a shell,
  hidden or invisible characters carrying text),
  UNCLEAR.
Answer format: first line the label, second line one short reason (max 25 words). For `hidden_unicode` hits also compute in
code which invisible characters occur on that line (print their code points) and include them in the row.
Row: `{"key": "<skill_id>|<file>|<line>", ..., "label", "reason", "context": the lines shown}`. 6 threads, resumable,
`--pilot` = first 4 hits. In `data/triage.md` list SUSPICIOUS and UNCLEAR items first, each with skill_id, file:line, the
snippet, the reason; then counts per label and pattern. Run it fully (53 calls) after the pilot and report the counts and
the SUSPICIOUS/UNCLEAR list.
