"""Discover mode: find tainted flows into known sinks, following calls across files.

Every method in a candidate file is run as an entry point, once per weakness class (a value encoded
for HTML is still dangerous in a SQL query, so sanitizers are class specific). A finding is reported
when a value classified "tainted" reaches a catalogued sink argument. Calls into other methods of the
repository are followed, so a flow from a controller through a helper to a sink in another file is
found and reported at the sink.
"""

from __future__ import annotations

import time
from pathlib import Path

from aletheore.flow.java_index import JavaIndex, JavaFile
from aletheore.flow import coverage
from aletheore.flow.java_interp import BudgetExceeded, Frame, Interp
from aletheore.flow.java_sinks import CWE_NAMES, DISCOVER_CWES, sink_args
from aletheore.flow.values import host_is_fixed, path_prefix_safe, Choice, Const, ListV, Obj, Tainted, classify
from aletheore.flow.prefilter import classes_present
from aletheore.scanner.detect import IGNORED_DIRS

# A file is only run for a weakness class when it contains a word that class's sinks need, or when it
# handles web requests (its flows can reach sinks in other files).
_SINK_WORDS = {
    89: (b"execute", b"prepareStatement", b"prepareCall", b"createQuery", b"createNativeQuery", b"createSQLQuery", b"JdbcTemplate", b"addBatch"),
    78: (b"exec(", b"ProcessBuilder"),
    22: (b"File", b"Paths", b"getResourceAsStream", b"getResource(", b"getRequestDispatcher"),
    79: (b"getWriter", b"JspWriter", b"getOutputStream", b"PrintWriter"),
    90: (b"search(",),
    643: (b"XPath", b"xpath", b"selectNodes", b"selectSingleNode"),
    501: (b"setAttribute", b"putValue"),
    601: (b"sendRedirect", b"RedirectView", b"Location"),
    918: (b"URL(", b"URI(", b"URI.create", b"RestTemplate", b"HttpGet", b"HttpPost", b"WebClient"),
    94: (b"ScriptEngine", b"GroovyShell", b"forName", b"parseExpression", b"Interpreter"),
    502: (b"ObjectInputStream", b"fromXML", b"Yaml"),
}
_ENTRY_WORDS = (b"HttpServletRequest", b"ServletRequest", b"@RequestParam", b"@PathVariable", b"@RequestBody",
                b"@RequestHeader", b"@CookieValue", b"@QueryParam", b"@PathParam", b"@FormParam", b"@HeaderParam",
                b"@ModelAttribute", b"@CookieParam")
_SEVERITY = {89: "critical", 78: "critical", 22: "major", 79: "major", 90: "major", 643: "major", 501: "minor", 601: "minor", 918: "major", 94: "critical", 502: "critical"}
MAX_SECONDS = 150.0
MAX_FILES = 6000
_EXTRA_SKIP = {"target", ".worktrees", ".repowise"}


def _direct_taint(v) -> bool:
    return isinstance(v, Tainted) or (isinstance(v, Choice) and any(isinstance(a, Tainted) for a in v.alts))


def _string_taint(v) -> bool:
    """Output and redirect targets: tainted text, not whatever an unmodelled call (a file read, a DB lookup) returned."""
    def one(t) -> bool:
        return isinstance(t, Tainted) and t.why != "through-call"
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts)) or (classify(v) == "tainted" and not isinstance(v, (Tainted, Choice)))


def _java_files(repo: Path) -> list[Path]:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    out: list[Path] = []
    for path in repo.rglob("*.java"):
        if skip.intersection(path.relative_to(repo).parts):
            continue
        out.append(path)
        if len(out) >= MAX_FILES:
            break
    return out


def has_java(repo: Path) -> bool:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    return any(not skip.intersection(p.relative_to(repo).parts) for p in repo.rglob("*.java"))


def _methods(jf: JavaFile):
    for cls in jf.classes:
        for methods in cls.methods.values():
            for m in methods:
                yield cls, m
        for c in cls.ctors:
            yield cls, c


def discover_java(repo: Path, index: JavaIndex | None = None, budget_seconds: float = MAX_SECONDS) -> list[dict]:
    index = index or JavaIndex(repo)
    started = time.monotonic()
    found: dict[tuple, dict] = {}
    files = _java_files(repo)
    present = classes_present(files, _SINK_WORDS)
    for path in files:
        if time.monotonic() - started > budget_seconds:
            break
        try:
            data = path.read_bytes()
        except OSError:
            continue
        is_entry_file = any(w in data for w in _ENTRY_WORDS)
        cwes = [c for c in DISCOVER_CWES if any(w in data for w in _SINK_WORDS[c])]
        # A request-handling file may call a helper that holds the sink, so every class is tried.
        if is_entry_file:
            cwes = [c for c in DISCOVER_CWES if c in present]
        if not cwes:
            continue
        jf = index.file(path)
        if jf is None:
            continue
        for cwe in cwes:
            for cls, method in _methods(jf):
                if time.monotonic() - started > budget_seconds:
                    break
                body = method.child_by_field_name("body")
                if body is None:
                    continue

                def hook(node, name, cname, recv_type, recv_text, args, fr, recv=None, _cwe=cwe):
                    recv_tainted = recv is not None and _direct_taint(recv)
                    positions = sink_args(_cwe, name, cname, recv_type, recv_text, len(args), recv_tainted)
                    if positions is None:
                        return
                    if not positions and recv_tainted:
                        positions = [-1]  # the receiver is the tainted value (a tainted File being used)
                    if _cwe == 601 and name in ("setHeader", "addHeader") and not (args and isinstance(args[0], Const) and str(args[0].value).lower() == "location"):
                        return
                    if _cwe == 78 and args and name in ("<init>", "exec"):
                        first = args[0]
                        if len(args) == 1 and isinstance(first, ListV) and len(first.items) >= 2 and not first.dirty and not first.opaque:
                            first = first.items[0]
                            multi = True
                        else:
                            multi = len(args) >= 2
                        if multi and isinstance(first, Const) and str(first.value).lower().rsplit("/", 1)[-1] not in ("sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh", "dash", "ksh", "csh"):
                            return  # constant program with separate arguments: no shell, no injection of a second command
                    for i in positions:
                        if _cwe in (918, 601) and 0 <= i < len(args) and (host_is_fixed(args[i]) or path_prefix_safe(args[i])):
                            continue  # a fixed scheme://host/ prefix: only the path is attacker data
                        value = recv if i == -1 else args[i] if i < len(args) else None
                        if value is None:
                            continue
                        # A trust-boundary finding needs a tainted STRING; an object bound from a form and
                        # stored in the session (the usual Spring pattern) is not reported.
                        hit = _direct_taint(value) if _cwe == 501 else _string_taint(value) if _cwe in (79, 601) else classify(value) == "tainted"
                        if hit:
                            rel = str(fr.file.path.relative_to(repo.resolve())) if fr.file.path.is_relative_to(repo.resolve()) else str(fr.file.path)
                            line = node.start_point[0] + 1
                            slug, label = CWE_NAMES[_cwe]
                            found.setdefault(
                                (rel, line, _cwe),
                                {
                                    "tool": "aletheore-flow",
                                    "rule_id": f"flow.java.{slug}",
                                    "severity": _SEVERITY[_cwe],
                                    "type": "vulnerability",
                                    "path": rel,
                                    "line": line,
                                    "message": f"Request data reaches a {label} sink without being validated or encoded for it "
                                               f"({'the receiver' if i == -1 else f'argument {i + 1}'} of {name}). Found by following the value through the code, "
                                               "including calls into other methods and files.",
                                    "cwe": [f"CWE-{_cwe}"],
                                },
                            )
                            return

                interp = Interp(index, {cwe}, sink_hook=hook, annotated_sources=True)
                fr = Frame(file=jf, cls=cls, this_obj=interp.make_this(cls, jf), depth=0)
                interp.bind_entry_params(method, fr)
                try:
                    interp.exec(body, fr)
                except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                    coverage.note(jf.path)
                    continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))


def discover_repo(repo: Path, index: JavaIndex | None = None, budget_seconds: float = MAX_SECONDS) -> list[dict]:
    """Discover tainted flows in every supported language the repository contains."""
    coverage.reset()
    from aletheore.flow.discover_cs import discover_cs, has_cs
    from aletheore.flow.discover_kt import discover_kt, has_kt
    from aletheore.flow.discover_rs import discover_rs, has_rs
    from aletheore.flow.discover_rb import discover_rb, has_rb
    from aletheore.flow.discover_go import discover_go, has_go
    from aletheore.flow.discover_js import discover_javascript, has_javascript
    from aletheore.flow.discover_php import discover_php, has_php
    from aletheore.flow.discover_py import discover_python, has_python

    out = discover_java(repo, index, budget_seconds) if has_java(repo) else []
    if has_python(repo):
        out += discover_python(repo, None, budget_seconds)
    if has_javascript(repo):
        out += discover_javascript(repo, None, budget_seconds)
    if has_php(repo):
        out += discover_php(repo, None, budget_seconds)
    if has_go(repo):
        out += discover_go(repo, None, budget_seconds)
    if has_cs(repo):
        out += discover_cs(repo, None, budget_seconds)
    if has_rb(repo):
        out += discover_rb(repo, None, budget_seconds)
    if has_kt(repo):
        out += discover_kt(repo, None, budget_seconds)
    if has_rs(repo):
        out += discover_rs(repo, None, budget_seconds)
    return sorted(out, key=lambda f: (f["path"], f["line"], f["rule_id"]))


def has_source(repo: Path) -> bool:
    from aletheore.flow.discover_cs import has_cs
    from aletheore.flow.discover_kt import has_kt
    from aletheore.flow.discover_rs import has_rs
    from aletheore.flow.discover_rb import has_rb
    from aletheore.flow.discover_go import has_go
    from aletheore.flow.discover_js import has_javascript
    from aletheore.flow.discover_php import has_php
    from aletheore.flow.discover_py import has_python

    return has_java(repo) or has_python(repo) or has_javascript(repo) or has_php(repo) or has_go(repo) or has_cs(repo) or has_rb(repo) or has_kt(repo) or has_rs(repo)
