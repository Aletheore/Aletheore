"""`aletheore usages <symbol>`: every call site and reference to a function or class, across the whole repo, as one
complete, deduplicated answer that includes the matching line of source for each hit and a footer that says how far
it can be trusted.

An agent that is handed only file:line pointers still has to open each file to see what the hit actually says. This
answers the whole question - where the symbol is defined, where else it is used, and what each usage line reads -
in one call, so it does not need to be re-derived with grep plus a read per hit.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from datetime import datetime
from pathlib import Path

USAGES_MAX_HITS = 200
_GIT_GREP_TIMEOUT_S = 15.0
_MAX_FILE_BYTES = 1_000_000


class UsagesError(Exception):
    """The request cannot be answered (unknown symbol, or the repo cannot be searched); the message is what the
    user should see."""


def _matches(path: str, pattern: str) -> bool:
    """`pattern` is a whole directory name or file name ("tests", "locale"), not a substring: "locale" must not drop
    a module called django/middleware/locale.py."""
    return pattern in path.split("/")


def _symbol_definitions(evidence: dict, symbol: str) -> list[tuple[str, str, int, int]]:
    """(path, kind, start_line, end_line) for every class/function in evidence named exactly `symbol`."""
    out: list[tuple[str, str, int, int]] = []
    for module in evidence["repository"]["modules"]:
        path = module.get("path")
        if not path:
            continue
        syms = module.get("symbols") or {}
        for kind, key in (("class", "classes"), ("function", "functions")):
            for s in syms.get(key) or []:
                if s.get("name") == symbol:
                    out.append((path, kind, s["start_line"], s["end_line"]))
    return sorted(out, key=lambda t: (t[0], t[2]))


def _known_symbol_names(evidence: dict) -> set[str]:
    names: set[str] = set()
    for module in evidence["repository"]["modules"]:
        syms = module.get("symbols") or {}
        for key in ("classes", "functions"):
            for s in syms.get(key) or []:
                if s.get("name"):
                    names.add(s["name"])
    return names


_GIT_GREP_Z_LINE = re.compile(r"(\d+)[:\0](.*)", re.DOTALL)


def _git_grep_usages(repo_path: Path, symbol: str) -> list[tuple[str, int, str]] | None:
    """(path, line, text) for every whole-word occurrence git tracks; None if git itself could not be used.

    `-z` NUL-delimits the pathname field, so a colon inside the path itself (legal on POSIX filesystems) can no
    longer be mistaken for the field separator. git versions differ on whether the line-number/text separator
    that follows is also NUL'd or stays a colon, so both are accepted here rather than assuming one."""
    try:
        result = subprocess.run(
            ["git", "grep", "-n", "-w", "-I", "-F", "-z", "-e", symbol],
            cwd=repo_path,
            capture_output=True,
            timeout=_GIT_GREP_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in (0, 1):
        return None
    out = []
    for raw in result.stdout.decode(errors="replace").split("\n"):
        if not raw:
            continue
        path, sep, rest = raw.partition("\0")
        if not sep:
            continue
        match = _GIT_GREP_Z_LINE.match(rest)
        if match:
            out.append((path, int(match.group(1)), match.group(2)))
    return out


def _plain_scan_usages(repo_path: Path, evidence: dict, symbol: str) -> list[tuple[str, int, str]]:
    """Fallback for a repo that is not (or no longer) a git checkout: scan every file evidence knows about
    directly. Slower and only covers files the last scan parsed, unlike git grep which sees the whole tree."""
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    out: list[tuple[str, int, str]] = []
    for module in evidence["repository"]["modules"]:
        path = module.get("path")
        if not path:
            continue
        try:
            full = Path(repo_path) / path
            if full.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = full.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.split("\n"), start=1):
            if pattern.search(line):
                out.append((path, lineno, line))
    return out


def _files_changed_since_scan(repo_path: Path, paths: list[str], scanned_at: object) -> list[str] | None:
    """Files whose mtime is later than the scan, so a stale answer can say so. None when the scan time is missing
    or unreadable: "nothing changed" and "could not check" must never look the same."""
    try:
        scan_time = datetime.fromisoformat(str(scanned_at)).timestamp()
    except ValueError:
        return None
    changed = []
    for rel in paths:
        try:
            mtime = (Path(repo_path) / rel).stat().st_mtime
        except OSError:
            # Could not check - most commonly because the file was deleted after the scan. "Could not check"
            # must never look like "nothing changed", so this counts as changed too (same fix applied to the
            # identical helper in tests_for.py per a Flash Review finding there).
            changed.append(rel)
            continue
        if mtime > scan_time:
            changed.append(rel)
    return changed


def usages_report(
    evidence: dict,
    repo_path: Path,
    symbol: str,
    exclude: list[str] | None = None,
    under: list[str] | None = None,
) -> str:
    known = _known_symbol_names(evidence)
    if symbol not in known:
        close = difflib.get_close_matches(symbol, sorted(known), n=3)
        hint = f"; closest: {', '.join(close)}" if close else ""
        raise UsagesError(f"{symbol!r} is not a known function or class name in the scan{hint}")

    defs = _symbol_definitions(evidence, symbol)
    def_ranges: dict[str, list[tuple[int, int]]] = {}
    for path, _kind, start, end in defs:
        def_ranges.setdefault(path, []).append((start, end))

    def _inside_own_definition(path: str, lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in def_ranges.get(path, []))

    repo_path = Path(repo_path)
    hits = _git_grep_usages(repo_path, symbol) if (repo_path / ".git").exists() else None
    used_git = hits is not None
    if hits is None:
        hits = _plain_scan_usages(repo_path, evidence, symbol)

    # A hit anywhere inside the definition's own start..end range is part of the definition, not a usage of it -
    # a recursive call, a class referencing its own name in a nested method, etc. must not count twice.
    non_def_hits = [h for h in hits if not _inside_own_definition(h[0], h[1])]

    exclude = exclude or []
    under = [u.removeprefix("./").strip("/") for u in (under or [])]
    under = [u + "/" for u in under if u not in ("", ".")]

    filtered = [
        h for h in non_def_hits if not any(_matches(h[0], e) for e in exclude) and (not under or h[0].startswith(tuple(under)))
    ]
    filtered.sort(key=lambda h: (h[0], h[1]))
    dropped_by_filter = len(non_def_hits) - len(filtered)

    shown = filtered[:USAGES_MAX_HITS]
    truncated = len(filtered) - len(shown)

    all_paths = sorted({p for p, _l, _t in filtered} | {p for p, _k, _s, _e in defs})
    scan_time = evidence.get("scanned_at")
    changed = _files_changed_since_scan(repo_path, all_paths, scan_time)
    if changed is None:
        fresh = "freshness unknown (the evidence has no readable scan time), so re-scan if the code changed"
    elif not changed:
        fresh = "no listed file changed since the scan (files added after it are not covered)"
    else:
        extra = f", {', '.join(changed[:3])}{', ...' if len(changed) > 3 else ''}"
        fresh = f"STALE: {len(changed)} file(s) changed after the scan{extra}; re-scan before relying on this"

    # `symbol` passed the `known` check above using the same classes/functions lists `_symbol_definitions` reads,
    # so at least one definition always exists here.
    lines: list[str] = [f"Defined ({len(defs)}):", *[f"{path}:{start} ({kind})" for path, kind, start, _end in defs], ""]

    excl = f" ({dropped_by_filter} more left out by --exclude/--under)" if dropped_by_filter else ""
    trunc = f" (showing first {len(shown)})" if truncated else ""
    lines.append(f"Used {len(filtered)} time(s) outside its own definition{excl}{trunc}:")
    lines += [f"{path}:{lineno}: {text.strip()}" for path, lineno, text in shown]
    if truncated:
        lines.append(f"... {truncated} more not shown; narrow with --exclude/--under")
    lines.append("")

    search_method = "git grep (whole tree)" if used_git else "evidence-scanned files only (not a git checkout)"
    lines.append(
        f"Coverage: literal whole-word text match via {search_method}, not semantic - this includes the same "
        "text inside comments, strings and docstrings, and any unrelated symbol that happens to share this exact "
        "name in another scope. Not tracked: dynamic dispatch (getattr, string-based plugin loading) and renamed "
        f"imports that alias the symbol under a different local name. {fresh}. This list does not need to be "
        "re-derived with grep."
    )
    return "\n".join(lines)
