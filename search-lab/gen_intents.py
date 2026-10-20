#!/usr/bin/env python3
"""Generate everyday search intents for every bundled skill (brief S1).

For each skill the cheap model writes 8 short requests a person might type to an AI
coding agent when they need that skill. Rows are appended to data/intents.jsonl keyed
by skill id, so an interrupted run resumes where it stopped. At the end the rows are
folded into data/intents.json = {skill_id: [intents]}.

Run with --pilot first (first 6 skills), check the output, then run the full set:

    python3 gen_intents.py --pilot
    python3 gen_intents.py
"""
from __future__ import annotations

import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import common

common.use_clean_source()

from aletheore import skill_hub  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT_JSONL = DATA / "intents.jsonl"
OUT_JSON = DATA / "intents.json"
STAGE = "s1"
MODEL = "deepseek-v4-flash"
# The model spends completion tokens on internal reasoning; 400 was consumed before
# any text was returned (empty rows), so allow enough room for both.
MAX_TOKENS = 3000
WORKERS = 8
PILOT_N = 6

_write_lock = threading.Lock()
_print_lock = threading.Lock()

PROMPT = """Below is a reusable instruction package described by its title, description and an excerpt of its body.

Title: {title}
Description: {description}
Body excerpt:
{body}

Write 8 different requests that a working developer, designer, marketer or founder might type to an AI coding agent when they need this package. Rules:
- 6 to 22 words each, in plain everyday wording.
- 3 describe the situation or problem, without tool jargon.
- 2 are short goal statements.
- 2 use different synonyms and technologies a user might name.
- 1 is phrased as a question.
- Never use the word "skill" in a request.
- Do not copy sentences from the input.
- One request per line, no numbering, no quotes, nothing else."""

_LEAD = re.compile(r"^\s*(?:[-*\u2022]+|\d+[.)])\s*")
_WORD = re.compile(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*")


def _parse(text: str) -> list[str]:
    intents: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        line = _LEAD.sub("", line)
        line = line.strip().strip("\"'`\u201c\u201d\u2018\u2019").strip()
        if not line:
            continue
        if "skill" in line.lower():
            continue
        words = _WORD.findall(line)
        if len(words) < 4 or len(words) > 30:
            continue
        folded = line.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        intents.append(line)
        if len(intents) >= 8:
            break
    return intents


def _call(skill_id: str) -> list[str]:
    skill = skill_hub.get_bundled_skill(skill_id)
    if skill is None:
        return []
    prompt = PROMPT.format(
        title=skill.title,
        description=skill.description,
        body=skill.content[:1200],
    )
    raw = common.chat(prompt, model=MODEL, max_tokens=MAX_TOKENS, temperature=0.9, stage=STAGE)
    intents = _parse(raw)
    if len(intents) < 5:
        retry = common.chat(prompt, model=MODEL, max_tokens=MAX_TOKENS, temperature=0.9, stage=STAGE)
        for intent in _parse(retry):
            if intent.casefold() not in {item.casefold() for item in intents}:
                intents.append(intent)
        intents = intents[:8]
    return intents


def _done_keys() -> set[str]:
    if not OUT_JSONL.exists():
        return set()
    keys: set[str] = set()
    for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("key"):
            keys.add(row["key"])
    return keys


def _append(row: dict) -> None:
    with _write_lock, open(OUT_JSONL, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json() -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        skill_id = row.get("skill_id")
        if skill_id:
            mapping[skill_id] = row.get("intents", [])
    OUT_JSON.write_text(json.dumps(mapping, indent=0, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return mapping


def main() -> None:
    DATA.mkdir(exist_ok=True)
    skills = skill_hub.list_bundled_skills()
    pilot = common.pilot_requested()
    done = _done_keys()
    todo = [meta.skill_id for meta in skills if meta.skill_id not in done]
    if pilot:
        todo = todo[:PILOT_N]
    common.log(STAGE, f"{'pilot' if pilot else 'full'} run: {len(todo)} skills to do "
                      f"({len(done)} already done, {len(skills)} total)")

    if pilot:
        with _print_lock:
            print("pilot skills:", ", ".join(todo), flush=True)

    failures = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_call, skill_id): skill_id for skill_id in todo}
        for future in as_completed(futures):
            skill_id = futures[future]
            try:
                intents = future.result()
            except Exception as exc:  # keep going; the key is retried next run
                failures += 1
                common.log(STAGE, f"{skill_id}: {type(exc).__name__}: {exc}")
                continue
            _append({"key": skill_id, "skill_id": skill_id, "intents": intents})
            completed += 1
            if pilot:
                with _print_lock:
                    print(f"\n{skill_id} ({len(intents)})", flush=True)
                    for intent in intents:
                        print(f"  - {intent}", flush=True)
            elif completed % 50 == 0:
                common.log(STAGE, f"{completed}/{len(todo)} done")

    mapping = _write_json()
    good = sum(1 for intents in mapping.values() if len(intents) >= 5)
    common.log(STAGE, f"wrote {OUT_JSONL.name} and {OUT_JSON.name}: "
                     f"{len(mapping)} skills, {good} with >= 5 intents, {failures} failures this run")
    print(f"skills: {len(mapping)}; with >= 5 intents: {good}; failures this run: {failures}")


if __name__ == "__main__":
    sys.exit(main())
