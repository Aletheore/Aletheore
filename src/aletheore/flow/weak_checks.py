"""Weak validation checks that taint tracking cannot see because no request value is involved at the check itself.

Path containment by string prefix: `extract_path.start_with?(dest_dir)` accepts `/srv/upload-evil/x` for a root of `/srv/upload`, because the
prefix ends without a path separator. The check is only meaningful on a canonical path, so a nearby canonicalising call is required; a prefix
that visibly ends in a separator, or is joined with one, is fine. Found by reading the code around the check, not by following a value."""
from __future__ import annotations

import re
from pathlib import Path

from aletheore.flow import library as _lib
from aletheore.scanner.detect import IGNORED_DIRS

_EXTS = {".rb": "ruby", ".py": "python", ".go": "go", ".java": "java", ".kt": "kotlin", ".js": "javascript", ".ts": "javascript", ".mjs": "javascript",
         ".cs": "csharp", ".php": "php"}
_PATTERNS = {
    "ruby": re.compile(r"(?P<a>[\w:@.\[\]()'\"]+?)\.start_with\?\(\s*(?P<b>[^)]+?)\s*\)"),
    "python": re.compile(r"(?P<a>[\w.\[\]()'\"]+?)\.startswith\(\s*(?P<b>[^),]+?)\s*\)"),
    "go": re.compile(r"strings\.HasPrefix\(\s*(?P<a>[^,]+?)\s*,\s*(?P<b>[^)]+?)\s*\)"),
    "java": re.compile(r"(?P<a>[\w.\[\]()\"]+?)\.startsWith\(\s*(?P<b>[^)]+?)\s*\)"),
    "kotlin": re.compile(r"(?P<a>[\w.\[\]()\"?]+?)\.startsWith\(\s*(?P<b>[^)]+?)\s*\)"),
    "javascript": re.compile(r"(?P<a>[\w.\[\]()'\"]+?)\.startsWith\(\s*(?P<b>[^)]+?)\s*\)"),
    "csharp": re.compile(r"(?P<a>[\w.\[\]()\"]+?)\.StartsWith\(\s*(?P<b>[^),]+?)\s*[,)]"),
    "php": re.compile(r"(?:str_starts_with|strpos)\(\s*(?P<a>[^,]+?)\s*,\s*(?P<b>[^,)]+?)\s*\)"),
}
_A_NAME = re.compile(r"(path|file|entry|dest|target|extract|resolved|canonical|full|abs|real|child|candidate|requested)", re.I)
_B_NAME = re.compile(r"(dir|root|base|dest|folder|target|home|upload|storage|directory|parent)", re.I)
_SEP = re.compile(r"""(\bsep\b|[Ss]eparator|SEPARATOR|["'][/\\]["']|\+\s*["'][/\\]|["'][/\\]\s*\)?\s*$|\bjoin\b|\bJoin\b|\bCombine\b|\+\s*File\.|\+\s*os\.|/\s*["']\s*$)""")
_CANON = re.compile(r"(realpath|abspath|expand_path|absolute_path|Clean\(|filepath\.Abs|getCanonical|toRealPath|normalize|GetFullPath|\.resolve\(|cleanpath|EvalSymlinks|path\.resolve|toAbsolutePath)")
_GUARD = re.compile(r"(\bunless\b|\bif\b|\bnot\b|!|raise|throw|return false|\bassert|\breject\b|\babort\b|\bdie\b)")
_REJECT = re.compile(r"(\braise\b|\bthrow\b|\breturn\b|\babort\b|\bdie\b|\bfail\b|\bpanic\b|\bexit\b|\bErrorf?\(|\berror\(|\bassert|\bnew \w*(Exception|Error)\b|\bhalt\b|\brender\b.*(40\d|forbidden)|\bhead\b.*40\d)")
_LITERAL = re.compile(r"""^\s*(["']).*\1\s*$""")
_SKIP = set(IGNORED_DIRS) | {".worktrees", ".repowise", "site-packages", "migrations"} | set(_lib.SKIP_DIRS) | {"tasks", "rakelib", "script", "bin", "build-logic", "buildSrc"}
MAX_FILES = 6000


def _balanced(line: str, open_at: int) -> str:
    """The text between the parenthesis at open_at and its match (the whole argument list of the call)."""
    depth = 0
    for j in range(open_at, len(line)):
        if line[j] == "(":
            depth += 1
        elif line[j] == ")":
            depth -= 1
            if depth == 0:
                return line[open_at + 1: j]
    return line[open_at + 1:]


def _last_ident(text: str) -> str:
    ids = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
    return ids[-1] if ids else ""


def scan(repo: Path) -> list[dict]:
    out: list[dict] = []
    repo_r = repo.resolve()
    n = 0
    for p in repo.rglob("*"):
        lang = _EXTS.get(p.suffix)
        if lang is None or not p.is_file():
            continue
        try:
            rel_parts = p.relative_to(repo).parts
        except ValueError:
            continue
        if _SKIP.intersection(rel_parts[:-1]) or p.name.endswith((".min.js", ".d.ts")) or ".test." in p.name or ".spec." in p.name or p.name.endswith(("_test.go", "Test.java")):
            continue
        n += 1
        if n > MAX_FILES:
            break
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if len(raw) > 600_000 or (b"start_with?" not in raw and b"startswith" not in raw and b"HasPrefix" not in raw and b"startsWith" not in raw
                                  and b"StartsWith" not in raw and b"strpos" not in raw and b"str_starts_with" not in raw):
            continue
        lines = raw.decode("utf-8", "replace").splitlines()
        pat = _PATTERNS[lang]
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*", "/*")):
                continue
            for m in pat.finditer(line):
                a, b = m.group("a"), m.group("b")
                call_open = line.find("(", m.start("b") - 1 - (len(m.group(0)) - len(m.group(0).lstrip())))
                lp = line.rfind("(", 0, m.start("b") + 1)
                if lp != -1 and lang in ("ruby", "python", "java", "kotlin", "javascript", "csharp"):
                    full = _balanced(line, lp)
                    b = full.split(",")[0].strip() if lang in ("python", "csharp") and "(" not in full.split(",")[0] else full.strip()
                if not _A_NAME.search(_last_ident(a) or a) or _LITERAL.match(b) or not _B_NAME.search(b):
                    continue
                if lang == "php" and "strpos" in m.group(0) and not re.search(r"strpos\([^;]*\)\s*(===|==|!==|!=)\s*0", line):
                    continue
                if _SEP.search(b) or not _GUARD.search(line):
                    continue
                if lang in ("java", "kotlin"):
                    text_all = raw.decode("utf-8", "replace")
                    ids = {_last_ident(a), _last_ident(b)} - {""}
                    if any(re.search(rf"\bPath\s+{re.escape(x)}\b|\b{re.escape(x)}\s*:\s*Path\b|\b{re.escape(x)}\s*=\s*(Paths?\.(get|of)|Path\.of)", text_all) for x in ids):
                        continue  # a java.nio.file.Path: startsWith compares whole components
                if lang in ("java", "kotlin", "csharp") and re.search(r"\bPath\b|Paths?\.|\brelativize\b|\.toPath\(", a + " " + b) \
                        and not re.search(r"getCanonicalPath|getAbsolutePath|GetFullPath|toString\(\)", a):
                    continue  # java.nio Path.startsWith compares whole components
                if not _REJECT.search("\n".join(lines[i: i + 4])):
                    continue  # the check does not reject anything: it is not a containment guard
                window = "\n".join(lines[max(0, i - 25): i + 1])
                if not _CANON.search(window):
                    continue
                bid = _last_ident(b)
                if bid and re.search(rf"\b{re.escape(bid)}\b\s*(=|:=|<<)[^\n]*(sep|SEPARATOR|Separator|[\"'][/\\][\"']|\bjoin\b|\bJoin\b|\bCombine\b)", window):
                    continue  # the prefix variable was built with a trailing separator
                out.append({
                    "tool": "aletheore-flow", "rule_id": f"flow.{lang}.path-containment", "severity": "minor", "type": "vulnerability",
                    "path": str(p.relative_to(repo_r)) if p.is_relative_to(repo_r) else str(p), "line": i + 1, "cwe": ["CWE-22"],
                    "message": "A path is checked to be inside a directory by string prefix, and the prefix does not end in a path separator, so a sibling such "
                               "as /srv/upload-evil passes a check against /srv/upload. Compare whole path components or add the separator.",
                })
                break
    return out
