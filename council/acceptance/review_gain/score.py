"""Score a review against the manifest: which injected bugs did it report?

A bug is a CANDIDATE when the review names its function (the last part of the qualified name) or cites its file with a line number within 5 lines.
Candidates go to a judge (one Claude call, no tools) that decides whether the review reports THIS defect: the same spot and the same wrong behaviour.
Mentioning a function, or a different problem in it, does not count. Findings about code that is not in the manifest are not scored (precision is not
measured here, which is why every condition is capped at the same number of findings)."""
import json
import re
import tempfile
import time
from pathlib import Path


def candidates(text, bugs):
    low = text.lower()
    out = []
    for b in bugs:
        leaf = b["function"].split(".")[-1].lower()
        base = Path(b["file"]).name.lower()
        hit = bool(re.search(rf"(?<![\w]){re.escape(leaf)}(?![\w])", low))
        if not hit:
            for m in re.finditer(rf"{re.escape(base)}[^\n]{{0,14}}?(\d+)", low):
                if abs(int(m.group(1)) - b["line"]) <= 5:
                    hit = True
                    break
        if hit:
            out.append(b)
    return out


JUDGE_PROMPT = """You are grading a code review against a known list of injected bugs. Each bug is a one-token change made to real library code.

For EACH candidate below, decide whether the review reports THIS defect: it must point at the same place (the function, or the file and a nearby line) AND say or
clearly imply the same wrong behaviour (the changed expression is wrong, or what goes wrong because of it). Do NOT count: a review that only mentions the function,
describes what it does, or reports a different problem in it. A different fix suggestion is fine if the problem named is the same.

Answer with ONLY a JSON object mapping each candidate id to true or false, for example {{"M01": true, "M02": false}}.

CANDIDATES
{cands}

THE REVIEW
{review}
"""


class JudgeFailed(Exception):
    """The judge gave no readable answer twice in a row. Never treated as 'found nothing': the caller keeps the review text and scores it again later."""


BATCH = 15


def _ask(prompt, runner, sleep=time.sleep):
    """One judge call. A CLI that fails outright (missing for a moment because Claude Code updates itself in place, a rate limit) is waited out with growing pauses,
    up to 6 attempts; an answer that is there but unreadable gets one more try."""
    d = Path(tempfile.mkdtemp(prefix="judge-"))
    cfg = d / "mcp.json"
    cfg.write_text('{"mcpServers": {}}')
    unreadable = 0
    for attempt in range(6):
        r = runner.run(prompt, d, cfg, {}, 600, read_only=True)
        if not r.ok:
            if attempt < 5:
                sleep(min(30 * (attempt + 1), 180))
            continue
        m = re.search(r"\{.*\}", r.final_text or "", re.S)
        if m:
            try:
                return {k: bool(v) for k, v in json.loads(m.group(0)).items()}
            except (ValueError, AttributeError):
                pass
        unreadable += 1
        if unreadable >= 2:
            break
    raise JudgeFailed("the judge's answer could not be read")


def judge(text, bugs, runner, sleep=time.sleep):
    """{bug id: found?} for the candidate bugs of this review, asked in batches so a long list never swamps the judge."""
    cands = candidates(text, bugs)
    out = {}
    for i in range(0, len(cands), BATCH):
        batch = cands[i:i + BATCH]
        listing = "\n".join(f"{b['id']}: {b['file']} line {b['line']}, function {b['function']}: the code was changed from `{b['original']}` to `{b['mutated']}`" for b in batch)
        verdict = _ask(JUDGE_PROMPT.format(cands=listing, review=text[:80000]), runner, sleep)
        out.update({b["id"]: verdict.get(b["id"], False) for b in batch})
    return out


def found_ids(text, bugs, runner, sleep=time.sleep):
    return sorted(k for k, v in judge(text, bugs, runner, sleep).items() if v)
