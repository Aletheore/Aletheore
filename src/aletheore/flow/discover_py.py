"""Python discover mode: follow request data to sinks, across functions and modules.

Same shape as the Java discovery: each function and method of a candidate file is an entry point
(web-route parameters are request data), the module body is run once for scripts, and each weakness
class is run separately because sanitizers are class specific.
"""

from __future__ import annotations

import time
from pathlib import Path

from aletheore.flow.py_index import PyFile, PyIndex
from aletheore.flow.py_interp import BudgetExceeded, PFrame, PyInterp
from aletheore.flow.py_sinks import CWE_NAMES_PY, DISCOVER_CWES_PY, ROUTE_MARKERS, SEVERITY_PY, sink_args
from aletheore.flow.values import Choice, Obj, Tainted, classify
from aletheore.scanner.detect import IGNORED_DIRS

_SINK_WORDS = {
    89: (b"execute", b"raw(", b"mogrify"),
    78: (b"system(", b"popen", b"subprocess", b"spawn", b"Popen", b"exec", b"getoutput", b"check_output"),
    22: (b"open(", b"os.", b"shutil", b"send_file", b"send_from_directory", b"Path(", b"FileResponse"),
    79: (b"Markup", b"mark_safe", b"HttpResponse", b"make_response", b"render_template_string", b"HTMLResponse", b"route(", b".get(", b".post("),
    90: (b"search",),
    643: (b"xpath", b"XPath"),
    501: (b"session",),
    94: (b"eval(", b"exec(", b"compile("),
    502: (b"pickle", b"yaml", b"marshal", b"dill", b"shelve", b"jsonpickle"),
    918: (b"requests", b"urlopen", b"httpx", b"urllib", b"aiohttp", b"session"),
    601: (b"redirect", b"HttpResponseRedirect", b"RedirectResponse"),
}
_ENTRY_WORDS = (b"request", b"route(", b"sys.argv", b"input(", b"argparse", b"@app", b"@router", b"api_view", b"Flask(", b"Blueprint", b"FastAPI")
_EXTRA_SKIP = {".worktrees", ".repowise", "site-packages", "migrations"}
MAX_FILES = 6000


def _py_files(repo: Path) -> list[Path]:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    out: list[Path] = []
    for p in repo.rglob("*.py"):
        if skip.intersection(p.relative_to(repo).parts):
            continue
        out.append(p)
        if len(out) >= MAX_FILES:
            break
    return out


def has_python(repo: Path) -> bool:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    return any(not skip.intersection(p.relative_to(repo).parts) for p in repo.rglob("*.py"))


def _direct(v) -> bool:
    return isinstance(v, Tainted) or (isinstance(v, Choice) and any(isinstance(a, Tainted) for a in v.alts))


def _tainted_string(v) -> bool:
    """A tainted value that is the string itself: taken from the request, or built from it by
    concatenation or formatting. A value that merely passed through an unmodeled call (a framework
    response builder such as jsonify or render_template) is not."""
    def one(t) -> bool:
        return isinstance(t, Tainted) and (t.why in ("derived", "route-param", "sys.argv", "input()") or t.why.startswith("request"))
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts))


def _entries(pf: PyFile):
    """(class or None, function node, is_route) for every function and method."""
    def is_route(fn_node) -> bool:
        parent = fn_node.parent
        if parent is not None and parent.type == "decorated_definition":
            text = "".join(c.text.decode("utf-8", "replace") for c in parent.children if c.type == "decorator")
            return any(m in text for m in ROUTE_MARKERS)
        return False

    for fn in pf.functions.values():
        yield None, fn, is_route(fn)
    for cls in pf.classes.values():
        for fn in cls.methods.values():
            yield cls, fn, is_route(fn)


def discover_python(repo: Path, index: PyIndex | None = None, budget_seconds: float = 150.0) -> list[dict]:
    index = index or PyIndex(repo)
    repo_r = repo.resolve()
    started = time.monotonic()
    found: dict[tuple, dict] = {}
    for path in _py_files(repo):
        if time.monotonic() - started > budget_seconds:
            break
        try:
            data = path.read_bytes()
        except OSError:
            continue
        entry_file = any(w in data for w in _ENTRY_WORDS)
        cwes = list(DISCOVER_CWES_PY) if entry_file else [c for c in DISCOVER_CWES_PY if any(w in data for w in _SINK_WORDS[c])]
        if not cwes:
            continue
        pf = index.file(path)
        if pf is None:
            continue
        for cwe in cwes:
            slug, label = CWE_NAMES_PY[cwe]

            def record(node, fr, what, _cwe=cwe, _slug=slug, _label=label):
                rel = str(fr.file.path.relative_to(repo_r)) if fr.file.path.is_relative_to(repo_r) else str(fr.file.path)
                line = node.start_point[0] + 1
                found.setdefault((rel, line, _cwe), {
                    "tool": "aletheore-flow", "rule_id": f"flow.python.{_slug}", "severity": SEVERITY_PY[_cwe],
                    "type": "vulnerability", "path": rel, "line": line, "cwe": [f"CWE-{_cwe}"],
                    "message": f"Request data reaches a {_label} sink without being validated or encoded for it ({what}). "
                               "Found by following the value through the code, including calls into other functions and modules.",
                })

            def sink(node, name, recv_text, args, kw, recv, fr, _cwe=cwe):
                recv_tainted = isinstance(recv, Obj) and recv.type_name == "Path" and classify(recv) == "tainted"
                positions = sink_args(_cwe, name, recv_text, len(args), kw, recv_tainted)
                if positions is None:
                    return
                if not positions and recv_tainted:
                    positions = [-1]
                for i in positions:
                    value = recv if i == -1 else args[i] if i < len(args) else None
                    if value is None:
                        continue
                    if _cwe == 78 and hasattr(value, "items") and not (isinstance(kw.get("shell"), type(args[0])) and getattr(kw.get("shell"), "value", False) is True):
                        items = value.items
                        hit = bool(items) and classify(items[0]) == "tainted"
                    elif _cwe in (501, 79):
                        hit = _tainted_string(value)
                    else:
                        hit = classify(value) == "tainted"
                    if hit:
                        record(node, fr, f"{'receiver' if i == -1 else 'argument ' + str(i + 1)} of {name}")
                        return

            def store(node, base_text, key, value, fr, _cwe=cwe):
                if _cwe == 501 and base_text.split(".")[-1] == "session" and (_tainted_string(key) or _tainted_string(value)):
                    record(node, fr, "write to the session")

            def returned(node, value, fr, _cwe=cwe):
                if _cwe == 79 and fr.route and fr.depth == 0 and _tainted_string(value):
                    record(node, fr, "value returned as the response body")

            interp_args = dict(sink_hook=sink, store_hook=store, return_hook=returned)
            runs = [(None, None, False)] + [(c, f, r) for c, f, r in _entries(pf)]
            for cls, fnode, route in runs:
                if time.monotonic() - started > budget_seconds:
                    break
                interp = PyInterp(index, {cwe}, **interp_args)
                self_obj = Obj(cls=cls, type_name=cls.name) if cls is not None else None
                fr = PFrame(pf, cls, self_obj, 0, route=route)
                try:
                    if fnode is None:
                        interp.exec(pf.root, fr)  # module-level code, once
                    else:
                        body = fnode.child_by_field_name("body")
                        interp.bind_entry(fnode, fr, route)
                        interp.exec(body, fr)
                except (BudgetExceeded, RecursionError):
                    continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))
