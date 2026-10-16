"""Find the files and functions a short text query most plausibly refers to.

The query is split into identifiers; the repository's source files that contain them are found (git grep when the
checkout is a git repository), and files are ranked by how many specific (rare) identifiers they hold. The result
names the functions whose bodies contain the matches, with their source, so one call can replace a grep followed by
one or more file reads. Deterministic, local and read-only; when nothing is confident it says so.
"""

from __future__ import annotations

import math
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

TOP_K = 3
MAX_IDENTIFIERS = 25
MIN_SCORE = 4.0  # below this the best file is not worth mentioning
SINGLE_STRONG_SCORE = 12.0  # a lone matched identifier must be this specific (rare) to be trusted
PATH_MENTION_BONUS = 10.0
PATH_NAME_BONUS = 2.0
DEFAULT_DEADLINE_S = 3.0
MAX_FILE_BYTES = 1_000_000

_STOP = set(
    """python import print none true false return self class def function error errors test tests issue expected actual
    behavior behaviour steps reproduce example output version environment description bug fix repository library
    following source files change hidden should would could value values name names string strings object objects
    default option options command commands usage using used when with from that this have been http https github com www""".split()
)
_TEST_PATH = re.compile(r"(^|/)(tests?|testing|__tests__|spec)/|_test\.|\.test\.|\.spec\.|(^|/)test_[^/]*$|/testdata/|conftest")
_DUNDER = re.compile(r"^__\w+__$")
_PATH_TOKEN = re.compile(r"[\w./-]+\.(?:py|go|js|mjs|cjs|ts|tsx|jsx|java|rb|rs|php|cs|kt|swift|c|cc|cpp|h)\b")



@dataclass
class Symbol:
    kind: str  # "class" | "function"
    name: str
    start: int
    end: int


@dataclass
class Candidate:
    path: str
    score: float = 0.0
    matched: list[str] = field(default_factory=list)
    path_hit: bool = False
    symbols: list[Symbol] = field(default_factory=list)



# --- extracting identifiers -----------------------------------------------------

def extract_identifiers(query: str, *, all_words: bool = False) -> tuple[dict[str, int], list[str]]:
    """Identifiers in the query with a weight each (backticked: 2 per use, otherwise 1), and any file paths it names.
    Plain English words are ignored unless `all_words` is set, for short queries an agent wrote on purpose."""
    ids: dict[str, int] = {}
    for span in re.findall(r"`([^`\n]{2,120})`", query):
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", span):
            if tok.lower() not in _STOP:
                ids[tok] = ids.get(tok, 0) + 2
    shortest = 2 if all_words else 3  # an agent's own query may name a three-letter function
    for tok in re.findall(rf"\b[A-Za-z_][A-Za-z0-9_]{{{shortest},}}\b", query):
        codey = all_words or "_" in tok or re.search(r"[a-z][A-Z]", tok) or tok.endswith("Error")
        if codey and tok.lower() not in _STOP:
            ids[tok] = ids.get(tok, 0) + 1
    return ids, _PATH_TOKEN.findall(query)


# --- locating -------------------------------------------------------------------

def _symbols_of(module: dict) -> list[Symbol]:
    syms = module.get("symbols") or {}
    out = [Symbol("class", c["name"], c["start_line"], c["end_line"]) for c in syms.get("classes") or []]
    out += [Symbol("function", f["name"], f["start_line"], f["end_line"]) for f in syms.get("functions") or []]
    return sorted(out, key=lambda s: (s.start, -s.end))


def _git_files_containing(repo: Path, ident: str, candidates: set[str]) -> list[str] | None:
    r = subprocess.run(
        ["git", "grep", "-l", "-w", "-F", "-e", ident], cwd=repo, capture_output=True, text=True, timeout=10
    )
    if r.returncode not in (0, 1):
        return None
    return [f for f in r.stdout.splitlines() if f in candidates]


def locate(
    prompt: str,
    repo_path: Path,
    modules: list[dict],
    *,
    top_k: int = TOP_K,
    deadline_s: float = DEFAULT_DEADLINE_S,
    min_score: float = MIN_SCORE,
    strict: bool = True,
    all_words: bool = False,
    require_confidence: bool = True,
) -> list[Candidate]:
    """Rank the repository's non-test source files for `prompt`; [] when nothing is confident or time runs out.
    `strict` drops dunder names (`__name__`, `__main__`), which say nothing about where a bug lives.
    `require_confidence=False` returns the best matches even when they are weak (an agent chose the query itself)."""
    started = time.monotonic()
    repo = Path(repo_path)
    by_path = {m["path"]: m for m in modules if m.get("path") and not _TEST_PATH.search(m["path"])}
    if not by_path:
        return []
    ids, mentioned = extract_identifiers(prompt, all_words=all_words)
    if strict:
        ids = {k: v for k, v in ids.items() if not _DUNDER.match(k)}
    if not ids and not mentioned:
        return []
    ordered = sorted(ids.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_IDENTIFIERS]
    n = len(by_path)
    candidate_set = set(by_path)
    use_git = (repo / ".git").exists()
    texts: dict[str, str] = {}
    if not use_git:
        for rel in by_path:
            try:
                p = repo / rel
                if p.stat().st_size <= MAX_FILE_BYTES:
                    texts[rel] = p.read_text(errors="replace")
            except OSError:
                continue

    scores = {rel: Candidate(rel) for rel in by_path}
    for ident, weight in ordered:
        if time.monotonic() - started >= deadline_s:
            return []
        try:
            if use_git:
                hits = _git_files_containing(repo, ident, candidate_set)
                if hits is None:
                    continue
            else:
                pat = re.compile(rf"\b{re.escape(ident)}\b")
                hits = [rel for rel, text in texts.items() if pat.search(text)]
        except (OSError, subprocess.SubprocessError):
            return []
        if not hits or len(hits) > max(25, n // 6):  # absent, or too common to say anything
            continue
        idf = 1 + math.log(n / len(hits))
        for rel in hits:
            scores[rel].score += weight * idf
            scores[rel].matched.append(ident)
        low = ident.lower()
        for rel in by_path:
            if low in rel.lower():
                scores[rel].score += weight * PATH_NAME_BONUS
    for mention in mentioned:
        tail = mention.lstrip("./")
        for rel in by_path:
            if rel.endswith(tail):
                scores[rel].score += PATH_MENTION_BONUS
                scores[rel].path_hit = True

    ranked = sorted((c for c in scores.values() if c.score > 0), key=lambda c: (-c.score, c.path))
    if not ranked:
        return []
    top = ranked[0]
    confident = top.score >= min_score and (
        len(top.matched) >= 2 or top.path_hit or top.score >= SINGLE_STRONG_SCORE
    )
    if require_confidence and not confident:
        return []
    out = ranked[:top_k]
    for c in out:
        c.symbols = _symbols_of(by_path[c.path])
    return out



# --- rendering ------------------------------------------------------------------

def _innermost(line_no: int, syms: list[Symbol], kind: str) -> Symbol | None:
    inside = [s for s in syms if s.kind == kind and s.start <= line_no <= s.end]
    return min(inside, key=lambda s: s.end - s.start) if inside else None


def render_locate(
    cands: list[Candidate],
    repo_path: Path,
    *,
    max_files: int = 3,
    max_symbols: int = 3,
    max_lines: int = 40,
    max_chars: int = 4000,
    rel_cutoff: float = 0.4,
) -> str:
    """For each file, the functions whose code holds the matched words: a `path::Name Lstart-end` header and the raw
    source (cut at `max_lines`), best functions first. Files scoring well below the top file are left out."""
    out: list[str] = []
    used = 0
    top = cands[0].score if cands else 0.0
    for c in [c for c in cands if c.path_hit or c.score >= rel_cutoff * top][:max_files]:
        try:
            p = Path(repo_path) / c.path
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            text_lines = p.read_text(errors="replace").split("\n")
        except OSError:
            continue
        pats = {m: re.compile(rf"\b{re.escape(m)}\b") for m in c.matched}
        found: dict[tuple[int, int], tuple[Symbol, set[str]]] = {}
        for i, text in enumerate(text_lines, start=1):
            hit = {m for m, pat in pats.items() if pat.search(text)}
            if not hit:
                continue
            sym = _innermost(i, c.symbols, "function") or _innermost(i, c.symbols, "class")
            if sym:
                found.setdefault((sym.start, sym.end), (sym, set()))[1].update(hit)
        ranked = sorted(found.values(), key=lambda t: (-len(t[1]), t[0].end - t[0].start, t[0].start))[:max_symbols]
        for sym, hit in sorted(ranked, key=lambda t: t[0].start):
            owner = _innermost(sym.start, [s for s in c.symbols if s is not sym], "class") if sym.kind == "function" else None
            name = f"{owner.name}.{sym.name}" if owner else sym.name
            # a class that merely contains the matching method is shown as its signature line, not its whole body
            body = text_lines[sym.start - 1 : sym.start] if sym.kind == "class" and len(found) > 1 else text_lines[sym.start - 1 : sym.end]
            cut = len(body) > max_lines
            block = f"{c.path}::{name} L{sym.start}-{sym.end}\n" + "\n".join(body[:max_lines])
            if cut:
                block += f"\n... ({len(body) - max_lines} more lines; use aletheore_symbol_source for the whole function)"
            if used + len(block) > max_chars and out:
                out.append("... (more matches not shown; narrow the query)")
                return "\n\n".join(out)
            out.append(block)
            used += len(block)
    return "\n\n".join(out)

