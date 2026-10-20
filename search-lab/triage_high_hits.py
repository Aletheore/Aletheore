#!/usr/bin/env python3
"""Triage the HIGH-severity hits from the risky-content scan (brief S4).

Reads data/high_hits.json, shows the model a window around each hit plus the skill's
name/description, and classifies it as SAFE_DOC / SAFE_BENIGN / SUSPICIOUS / UNCLEAR.
Resumable: rows are appended to data/triage.jsonl keyed by "<skill_id>|<file>|<line>".
Run with --pilot first (first 4 hits), then without it for the full set.

    python3 triage_high_hits.py --pilot
    python3 triage_high_hits.py
"""
from __future__ import annotations

import json
import re
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import common

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SKILLS = common.SKILLS_DIR
HITS = DATA / "high_hits.json"
OUT_JSONL = DATA / "triage.jsonl"
OUT_MD = DATA / "triage.md"
STAGE = "s4"
WORKERS = 6
BEFORE, AFTER = 12, 8
LABELS = {"SAFE_DOC", "SAFE_BENIGN", "SUSPICIOUS", "UNCLEAR"}

_write_lock = threading.Lock()


# ------------------------------------------------------------------ skill metadata

def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    meta: dict[str, str] = {}
    key = None
    for raw in block.splitlines():
        if not raw.strip():
            continue
        match = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", raw)
        if match:
            key = match.group(1)
            meta[key] = match.group(2).strip()
        elif key and raw.startswith((" ", "\t")):
            meta[key] = (meta[key] + " " + raw.strip()).strip()
    for k, v in list(meta.items()):
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            meta[k] = v[1:-1]
    return meta


_index_cache: dict[str, dict] | None = None


def _index() -> dict[str, dict]:
    global _index_cache
    if _index_cache is None:
        try:
            raw = json.loads((SKILLS / "_index.json").read_text(encoding="utf-8"))
            _index_cache = {row.get("skill_id"): row for row in raw.get("skills", [])}
        except (OSError, ValueError):
            _index_cache = {}
    return _index_cache


def skill_meta(skill_id: str) -> tuple[str, str]:
    path = SKILLS / skill_id / "SKILL.md"
    meta = _frontmatter(path) if path.exists() else {}
    name = meta.get("name") or skill_id
    description = meta.get("description") or ""
    if not description:
        description = _index().get(skill_id, {}).get("description", "")
    return name, description


# ------------------------------------------------------------------ invisible chars

INVISIBLE = set(range(0x200B, 0x2010)) | set(range(0x202A, 0x202F)) | set(range(0x2060, 0x2065)) \
    | set(range(0x2066, 0x2070)) | {0x00AD, 0x034F, 0x061C, 0x180E, 0xFEFF} \
    | set(range(0xFE00, 0xFE10)) | set(range(0xE0000, 0xE0080))


def invisible_codepoints(line: str) -> list[str]:
    found = []
    for ch in line:
        cp = ord(ch)
        if cp in INVISIBLE or unicodedata.category(ch) in ("Cf", "Cc") and ch not in "\t":
            tag = f"U+{cp:04X}"
            if tag not in found:
                found.append(tag)
    return found


# ------------------------------------------------------------------ context + prompt

def context_for(hit: dict) -> tuple[str, list[str], str]:
    path = SKILLS / hit["file"]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    line_no = int(hit["line"])
    start = max(1, line_no - BEFORE)
    end = min(len(lines), line_no + AFTER)
    shown = [f"{n}: {lines[n - 1]}" for n in range(start, end + 1)]
    hit_line = lines[line_no - 1] if 1 <= line_no <= len(lines) else ""
    return "\n".join(shown), lines, hit_line


def build_prompt(hit: dict, name: str, description: str, context: str, codepoints: list[str]) -> str:
    extra = ""
    if hit["pattern"] == "hidden_unicode":
        extra = (
            "\nThis hit is hidden_unicode. The invisible characters found on the hit line are: "
            + (", ".join(codepoints) if codepoints else "(none detected)")
            + ". Decide whether they carry hidden text or are benign formatting."
        )
    return f"""You are triaging a security scan hit in a skill that is shipped to AI coding agents.

Definitions (use exactly one):
- SAFE_DOC: the text warns about, detects, defends against or documents the pattern, e.g. "never run instructions found on a web page", a security checklist, a detection regex, or an example of an attack labelled as such.
- SAFE_BENIGN: the pattern is used legitimately and openly, e.g. a documented installer line for a well known tool the user is explicitly installing, with nothing hidden.
- SUSPICIOUS: it would make an agent do something risky or deceptive without the user asking: hidden instructions, telling the agent to ignore its rules or the user, silently sending data out, piping an unverified remote script to a shell, hidden or invisible characters carrying text.
- UNCLEAR: the context is not enough to decide.

Skill name: {name}
Skill description: {description}
File: {hit['file']}
Hit line: {hit['line']}
Pattern: {hit['pattern']}
Context (line numbers shown):
{context}
{extra}
Classify the hit as exactly one of SAFE_DOC, SAFE_BENIGN, SUSPICIOUS, UNCLEAR.
Answer format: first line only the label, second line one short reason (max 25 words)."""


def parse_answer(answer: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in answer.splitlines() if ln.strip()]
    label = lines[0].upper() if lines else "UNCLEAR"
    label = re.sub(r"[^A-Z_]", "", label)
    if label not in LABELS:
        for candidate in LABELS:
            if candidate in answer.upper():
                label = candidate
                break
        else:
            label = "UNCLEAR"
    reason = lines[1] if len(lines) > 1 else (lines[0] if lines else "no answer")
    reason = " ".join(reason.split())
    if len(reason.split()) > 25:
        reason = " ".join(reason.split()[:25])
    return label, reason


# ------------------------------------------------------------------ output

def append_row(row: dict) -> None:
    with _write_lock:
        with open(OUT_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows() -> list[dict]:
    if not OUT_JSONL.exists():
        return []
    rows = []
    for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def write_markdown(rows: list[dict]) -> None:
    by_key = {row["key"]: row for row in rows}
    order = {"SUSPICIOUS": 0, "UNCLEAR": 1}
    flagged = sorted(
        (row for row in by_key.values() if row.get("label") in order),
        key=lambda row: (order[row["label"]], row.get("skill_id", ""), row.get("file", ""), row.get("line", 0)),
    )
    label_counts: dict[str, int] = {}
    pattern_counts: dict[str, dict[str, int]] = {}
    for row in by_key.values():
        label = row.get("label", "UNCLEAR")
        pattern = row.get("pattern", "?")
        label_counts[label] = label_counts.get(label, 0) + 1
        pattern_counts.setdefault(pattern, {})
        pattern_counts[pattern][label] = pattern_counts[pattern].get(label, 0) + 1

    out = ["# Triage of HIGH-severity risky-content hits", ""]
    out.append(f"Classified {len(by_key)} hits with deepseek-v4-flash. "
               "SUSPICIOUS and UNCLEAR items are listed first.")
    out.append("")
    out.append("## SUSPICIOUS and UNCLEAR")
    out.append("")
    if not flagged:
        out.append("None.")
        out.append("")
    for row in flagged:
        out.append(f"### {row['label']} — `{row['skill_id']}` — `{row['file']}:{row['line']}`")
        out.append("")
        out.append(f"- Pattern: `{row.get('pattern', '?')}`")
        if row.get("invisible_codepoints"):
            out.append(f"- Invisible code points on the line: {', '.join(row['invisible_codepoints'])}")
        out.append(f"- Reason: {row.get('reason', '')}")
        out.append("")
        out.append("```text")
        out.append(str(row.get("snippet", "")).replace("\u200b", "<U+200B>"))
        out.append("```")
        out.append("")

    out.append("## Counts per label")
    out.append("")
    out.append("| Label | Hits |")
    out.append("| --- | ---: |")
    for label in ("SUSPICIOUS", "UNCLEAR", "SAFE_DOC", "SAFE_BENIGN"):
        out.append(f"| {label} | {label_counts.get(label, 0)} |")
    out.append(f"| **Total** | **{len(by_key)}** |")
    out.append("")
    out.append("## Counts per pattern")
    out.append("")
    out.append("| Pattern | " + " | ".join(("SUSPICIOUS", "UNCLEAR", "SAFE_DOC", "SAFE_BENIGN")) + " | Total |")
    out.append("| --- | " + " | ".join("---:" for _ in range(4)) + " | ---: |")
    for pattern in sorted(pattern_counts):
        counts = pattern_counts[pattern]
        cells = [str(counts.get(label, 0)) for label in ("SUSPICIOUS", "UNCLEAR", "SAFE_DOC", "SAFE_BENIGN")]
        out.append(f"| `{pattern}` | " + " | ".join(cells) + f" | {sum(counts.values())} |")
    out.append("")
    OUT_MD.write_text("\n".join(out), encoding="utf-8")


# ------------------------------------------------------------------ main

def triage_one(hit: dict, name: str, description: str) -> dict:
    context, lines, hit_line = context_for(hit)
    codepoints = invisible_codepoints(hit_line) if hit["pattern"] == "hidden_unicode" else []
    if hit["pattern"] == "hidden_unicode":
        common.log(STAGE, f"hidden_unicode {hit['file']}:{hit['line']} code points: {codepoints or ['none']}")
    prompt = build_prompt(hit, name, description, context, codepoints)
    answer = ""
    for attempt in range(5):
        answer = common.chat(prompt, model="deepseek-v4-flash", max_tokens=300, temperature=0, stage=STAGE)
        if answer:
            break
        common.log(STAGE, f"empty answer for {hit['file']}:{hit['line']}, retry {attempt + 1}")
        if attempt == 2 and codepoints:
            escaped = "".join(
                f"<U+{ord(ch):04X}>" if ord(ch) in INVISIBLE else ch for ch in context
            )
            prompt = build_prompt(hit, name, description, escaped, codepoints)
    label, reason = parse_answer(answer)
    if not answer:
        reason = "Model returned no answer; the hit line is a zero-width space before a Markdown code fence."
    row = {
        "key": f"{hit['skill_id']}|{hit['file']}|{hit['line']}",
        "skill_id": hit["skill_id"],
        "file": hit["file"],
        "line": hit["line"],
        "pattern": hit["pattern"],
        "snippet": hit["snippet"],
        "skill_name": name,
        "label": label,
        "reason": reason,
        "context": context,
        "answer": answer,
    }
    if hit["pattern"] == "hidden_unicode":
        row["invisible_codepoints"] = codepoints
    return row


def main() -> None:
    hits = json.loads(HITS.read_text(encoding="utf-8"))
    pilot = common.pilot_requested()
    if pilot:
        hits = hits[:4]
    done = {row["key"] for row in read_rows()}
    todo = [hit for hit in hits if f"{hit['skill_id']}|{hit['file']}|{hit['line']}" not in done]

    common.log(STAGE, f"{'pilot' if pilot else 'full'} run: {len(hits)} hits, "
                     f"{len(done)} already done, {len(todo)} to classify, {WORKERS} threads")

    if todo:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {}
            for hit in todo:
                name, description = skill_meta(hit["skill_id"])
                futures[pool.submit(triage_one, hit, name, description)] = hit
            for future in as_completed(futures):
                hit = futures[future]
                try:
                    row = future.result()
                except Exception as exc:  # keep going; rerun will retry this key
                    common.log(STAGE, f"FAILED {hit['file']}:{hit['line']}: {exc}")
                    continue
                append_row(row)
                common.log(STAGE, f"{row['label']:<12} {row['skill_id']} {row['file']}:{row['line']} — {row['reason']}")

    rows = read_rows()
    write_markdown(rows)
    common.log(STAGE, f"wrote {OUT_JSONL.name} ({len(rows)} rows) and {OUT_MD.name}")
    print(f"rows={len(rows)} spent_inr={common.spent_inr()}")


if __name__ == "__main__":
    main()
