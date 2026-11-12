"""Python discover mode: follow request data to sinks, across functions and modules.

Same shape as the Java discovery: each function and method of a candidate file is an entry point
(web-route parameters are request data), the module body is run once for scripts, and each weakness
class is run separately because sanitizers are class specific.
"""

from __future__ import annotations

from aletheore.flow.budget import Budget

from pathlib import Path

from aletheore.flow import coverage
from aletheore.flow import extra_sinks as _X
from aletheore.flow.prefilter import classes_present
from aletheore.flow.py_index import PyFile, PyIndex
from aletheore.flow.py_interp import BudgetExceeded, PFrame, PyInterp
from aletheore.flow.py_sinks import CWE_NAMES_PY, DISCOVER_CWES_PY, ROUTE_MARKERS, SEVERITY_PY, sink_args
from aletheore.flow import library as _lib
from aletheore.flow.values import host_is_fixed, path_prefix_safe, Choice, Const, Obj, Tainted, classify
from aletheore.scanner.detect import IGNORED_DIRS

_SINK_WORDS = {
    89: (b"execute", b"raw(", b"mogrify", b".extra(", b"exec_driver_sql", b".sql(", b".query(", b"run_sql", b"callproc", b".exec("),
    78: (b"system(", b"popen", b"subprocess", b"spawn", b"Popen", b"exec", b"getoutput", b"check_output"),
    22: (b"open(", b"os.", b"shutil", b"send_file", b"send_from_directory", b"Path(", b"FileResponse"),
    79: (b"Markup", b"mark_safe", b"HttpResponse", b"make_response", b"render_template_string", b"HTMLResponse", b"route(", b".get(", b".post("),
    90: (b"search",),
    643: (b"xpath", b"XPath"),
    501: (b"session",),
    94: (b"eval(", b"exec(", b"compile(", b"import_module", b"__import__"),
    502: (b"pickle", b"yaml", b"marshal", b"dill", b"shelve", b"jsonpickle"),
    918: (b"requests", b"urlopen", b"httpx", b"urllib", b"aiohttp", b"session"),
    601: (b"redirect", b"HttpResponseRedirect", b"RedirectResponse"),
}
for _c in _X.rule_classes("python"):  # the shared catalogue's method names
    _SINK_WORDS[_c] = tuple(_SINK_WORDS.get(_c, ())) + _X.prefilter_words("python", _c)
_MASS_METHODS = {"create", "update", "get_or_create", "update_or_create", "bulk_create", "insert", "upsert", "filter_by_x"}
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
        return isinstance(t, Tainted) and (t.why in ("derived", "route-param", "sys.argv", "input()", "library parameter") or t.why.startswith("request"))
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts))


_SOURCE_MARKERS = (b"request", b"req", b"argv", b"input", b"environ", b"namelist", b"getnames", b"infolist", b"getmember", b"getinfo", b"next(",
                   b"get_argument", b"get_param", b"text_area", b"chat_input", b"file_uploader", b"data_editor", b"stdin", b"flask")


def _can_source(fn) -> bool:
    """Could running this function by itself bring request data in? For a method the whole class is read: a constructor or another method may have stored a request value in a field. Only a reference to a request, an archive entry or the like can; a plain
    helper is analysed in the context of whatever route calls it."""
    t = fn.text.lower()
    return any(m in t for m in _SOURCE_MARKERS)


_READ_WORDS = (b".objects", b"fetch", b"cursor", b"scalar", b".first(", b".one(", b".find")


def _rel_parts(path: Path, repo: Path) -> tuple:
    try:
        return path.relative_to(repo).parts
    except ValueError:
        return path.resolve().relative_to(repo.resolve()).parts


_FALCON_VERBS = frozenset(f"on_{v}" for v in ("get", "post", "put", "patch", "delete", "head", "options"))


def _public_py(cls, fn) -> bool:
    name = fn.child_by_field_name("name").text.decode("utf-8", "replace")
    if cls is not None and (cls.name.startswith("_") or cls.name.startswith("Test")):
        return False
    return not name.startswith("_") or name == "__init__"


def _entries(pf: PyFile, index: PyIndex | None = None):
    """(class or None, function node, is_route) for every function and method."""
    def is_route(fn_node) -> bool:
        parent = fn_node.parent
        if parent is not None and parent.type == "decorated_definition":
            text = "".join(c.text.decode("utf-8", "replace") for c in parent.children if c.type == "decorator")
            return any(m in text for m in ROUTE_MARKERS)
        return False

    for fn in pf.functions.values():
        yield None, fn, is_route(fn)
    verbs = {"get", "post", "put", "patch", "delete", "head", "options", "prepare", "open", "on_message", "list", "create", "retrieve", "update", "destroy", "get_redirect_url", "get_success_url", "form_valid", "dispatch"}
    for cls in pf.classes.values():
        handler = PyInterp.handler_class(cls) or any(b.endswith(("RedirectView", "FormView", "View")) for b in cls.bases)
        for fn in cls.methods.values():
            nm = fn.child_by_field_name("name").text.decode("utf-8", "replace")
            yield cls, fn, is_route(fn) or (handler and (nm in verbs or nm.startswith("on_"))) or nm in _FALCON_VERBS
        # A method a subclass inherits runs on the subclass: that is where its constructor set the fields the method reads.
        seen, own = {id(cls)}, set(cls.methods)
        todo = list(cls.bases)
        while todo:
            hit = index.lookup(todo.pop(), cls.file) if index is not None else None
            if hit is None or hit[0] != "class" or id(hit[1]) in seen:
                continue
            seen.add(id(hit[1]))
            for name, fn in hit[1].methods.items():
                if name not in own:
                    own.add(name)
                    yield cls, fn, is_route(fn)
            todo.extend(hit[1].bases)


def discover_python(repo: Path, index: PyIndex | None = None, budget_seconds: float = 150.0) -> list[dict]:
    index = index or PyIndex(repo)
    repo_r = repo.resolve()
    budget = Budget()
    found: dict[tuple, dict] = {}
    files = _py_files(repo)
    library = _lib.is_library(repo_r, "python")
    present = classes_present(files, _SINK_WORDS)
    core_budget = budget_seconds
    # Core classes first, with the whole budget; the added classes (template, regex, reflection, ...) after, in a
    # bounded extra slice, so a large repository never loses a core finding to the time the added classes take.
    stash: set = set()  # what handlers stored in the database; a second pass reads it back
    for stored_pass, phase in ((False, 0), (False, 1), (True, 0), (True, 1)):
        if stored_pass and not stash:
            continue
        budget_seconds = core_budget if phase == 0 else core_budget * 1.4
        for path in files:
            if budget.over(budget_seconds):
                break
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if stored_pass and not any(w in data for w in _READ_WORDS):
                continue
            entry_file = any(w in data for w in _ENTRY_WORDS)
            cwes = [c for c in DISCOVER_CWES_PY if c in present and _X.local_ok(c, data, _SINK_WORDS, "python")] if entry_file else [c for c in DISCOVER_CWES_PY if any(w in data for w in _SINK_WORDS[c])]
            cwes = [c for c in cwes if _X.added("python", c) == (phase == 1)]
            if not cwes:
                continue
            pf = index.file(path)
            if pf is None:
                continue
            for cwe in cwes:
                slug, label = CWE_NAMES_PY[cwe]

                route_now = [None]  # the kind of entry point being run
                url_stores: dict = {}  # (view, attribute) -> where the request value was assigned
                cur_interp = [None]

                def record(node, fr, what, _cwe=cwe, _slug=slug, _label=label):
                    rel = str(fr.file.path.relative_to(repo_r)) if fr.file.path.is_relative_to(repo_r) else str(fr.file.path)
                    line = node.start_point[0] + 1
                    lib = route_now[0] == "lib"
                    prefix = "Data that a request stored earlier (read back from the database) " if (stored_pass or (cur_interp[0] is not None and cur_interp[0].stored_reads)) else ""
                    found.setdefault((rel, line, _cwe), {
                        "tool": "aletheore-flow", "rule_id": f"flow.python.{_slug}", "severity": "minor" if lib else SEVERITY_PY[_cwe],
                        "type": "vulnerability", "path": rel, "line": line, "cwe": [f"CWE-{_cwe}"],
                        "message": (f"A parameter of a public function reaches a {_label} sink without being validated or encoded for it ({what}). "
                                    "Whoever calls the function controls it. Found by following the value through the code." if lib else
                                    (f"{prefix}reaches a {_label} sink without being validated or encoded for it ({what}). " if prefix else
                                     f"Request data reaches a {_label} sink without being validated or encoded for it ({what}). ")
                                    + "Found by following the value through the code, including calls into other functions and modules."),
                    })

                def sink(node, name, recv_text, args, kw, recv, fr, _cwe=cwe):
                    if _cwe == 470 and name == "getattr" and not recv_text and len(args) >= 2 and _tainted_string(args[1]) \
                            and node.parent is not None and node.parent.type == "call" and node.parent.child_by_field_name("function") == node:
                        record(node, fr, "getattr with a request-chosen name, then called")
                        return
                    if _cwe == 89 and name == "extra" and ".objects" in recv_text or (_cwe == 89 and name == "extra" and any(k in kw for k in ("where", "select", "tables", "order_by"))):
                        for k in ("where", "select", "tables", "order_by"):
                            v = kw.get(k)
                            items = v.items if hasattr(v, "items") and isinstance(getattr(v, "items"), list) else (list(v.entries.values()) if hasattr(v, "entries") else [v])
                            if v is not None and any(classify(x) == "tainted" for x in items):
                                record(node, fr, f"{k}= of QuerySet.extra() built from the request")
                                return
                    if _cwe == 89 and name in ("execute_statement", "execute", "query", "sql", "execute_sql", "run_query", "fetch", "run") and any(k in kw for k in ("statement", "sql", "query", "stmt", "operation")):
                        for k in ("statement", "sql", "query", "stmt", "operation"):
                            if k in kw and classify(kw[k]) == "tainted":
                                record(node, fr, f"{k}= of {name}")
                                return
                    if _cwe == 915:
                        if "**" in kw and _X.whole_body(kw["**"]) and (name[:1].isupper() or name in _MASS_METHODS):
                            record(node, fr, f"every request field passed as keyword arguments to {name}")
                        return
                    recv_tainted = isinstance(recv, Obj) and recv.type_name == "Path" and classify(recv) == "tainted"
                    positions = sink_args(_cwe, name, recv_text, len(args), kw, recv_tainted)
                    ext = False
                    if positions is None:  # the shared catalogue, for a string built from request data
                        positions = _X.sink_args("python", _cwe, name, recv_text, len(args), args)
                        ext = positions is not None
                        if positions is None:
                            return
                    if not positions and recv_tainted:
                        positions = [-1]
                    for i in positions:
                        if _cwe in (918, 601) and 0 <= i < len(args) and (host_is_fixed(args[i]) or path_prefix_safe(args[i])):
                            continue  # a fixed scheme://host/ prefix: only the path is attacker data
                        value = recv if i == -1 else args[i] if i < len(args) else None
                        if value is None:
                            continue
                        if _cwe == 78 and hasattr(value, "items") and not (isinstance(kw.get("shell"), type(args[0])) and getattr(kw.get("shell"), "value", False) is True):
                            items = value.items
                            hit = bool(items) and classify(items[0]) == "tainted"
                        elif ext:
                            hit = _X.ext_hit("python", _cwe, name, i, args, _tainted_string, node)
                        elif _cwe in (501, 79):
                            hit = _tainted_string(value)
                        else:
                            hit = classify(value) == "tainted"
                        if hit:
                            record(node, fr, f"{'receiver' if i == -1 else 'argument ' + str(i + 1)} of {name}")
                            return

                def store(node, base_text, key, value, fr, _cwe=cwe):
                    if isinstance(key, Const) and isinstance(key.value, str) and key.value.startswith("."):
                        if _tainted_string(value) and fr.self_obj is not None:
                            url_stores[(id(fr.self_obj), key.value[1:])] = node  # judged where the view returns, reported where it was set
                        return
                    if _cwe == 942:
                        if _X.acao_store(key, value, _tainted_string, node):
                            record(node, fr, "Access-Control-Allow-Origin set from the request")
                        return
                    if _cwe == 501 and base_text.split(".")[-1] == "session" and (_tainted_string(key) or _tainted_string(value)):
                        record(node, fr, "write to the session")

                fastapi = b"fastapi" in data.lower()

                def returned(node, value, fr, _cwe=cwe, _fastapi=fastapi):
                    if _fastapi and b"HTMLResponse" not in data and b"response_class" not in data:
                        return  # FastAPI serialises a returned value as JSON, not as an HTML page
                    if _cwe == 601 and fr.route and fr.depth == 0 and fr.cls is not None and fr.self_obj is not None \
                            and any(b.endswith(("RedirectView", "FormView", "CreateView", "UpdateView", "DeleteView")) for b in fr.cls.bases) \
                            and b"allowed_host" not in fr.cls.node.text and b"is_safe_url" not in fr.cls.node.text and b"get_success_url" not in fr.cls.node.text.replace(b"def get_success_url", b""):
                        for attr in ("url", "success_url", "redirect_url", "next_page"):
                            if _tainted_string(fr.self_obj.fields.get(attr)):
                                record(url_stores.get((id(fr.self_obj), attr), node), fr, f"{attr} set from the request; the view redirects to it")
                                break
                    if _cwe == 79 and fr.route and fr.depth == 0 and _tainted_string(value):
                        record(node, fr, "value returned as the response body")

                interp_args = dict(sink_hook=sink, store_hook=store, return_hook=returned)
                runs = [(None, None, False)] + [(c, f, r) for c, f, r in _entries(pf, index)]
                if library and cwe not in _lib.SKIP_CWES and not _lib.skipped_path(_rel_parts(path, repo)):
                    runs = [(c, f, "lib" if (r is False and f is not None and _public_py(c, f)) else r) for c, f, r in runs]
                for cls, fnode, route in runs:
                    if budget.over(budget_seconds):
                        break
                    if fnode is not None and route is False and not _can_source(cls.node if cls is not None else fnode):
                        continue  # a plain function whose own text has no source of request data: it only matters when a route calls it
                    route_now[0] = route if route == "lib" else None
                    interp = PyInterp(index, {cwe}, stash=stash, **interp_args)
                    cur_interp[0] = interp
                    interp.MAX_STEPS = max(interp.MAX_STEPS, len(data) // 4)  # big files get a proportionally bigger budget
                    self_obj = coverage.safe_this(interp, cls, pf) if cls is not None else None
                    fr = PFrame(pf, cls, self_obj, 0, route=route is True)
                    try:
                        if fnode is None:
                            interp.exec(pf.root, fr)  # module-level code, once
                        else:
                            body = fnode.child_by_field_name("body")
                            interp.bind_entry(fnode, fr, route)
                            interp.exec(body, fr)
                    except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                        coverage.note(path)
                        continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))


def validate_python_finding(repo: Path, rel_path: str, start: tuple[int, int], end: tuple[int, int], cwes: set[int], index: PyIndex | None = None):
    """Refine mode: prove the sink value of a Semgrep finding safe, or keep it (see validate_php_finding)."""
    from aletheore.flow.validate import Verdict, _offset

    index = index or PyIndex(repo)
    pf = index.file(repo / rel_path)
    if pf is None:
        return Verdict("unknown", "file unreadable")
    s, e = _offset(pf.src, *start), _offset(pf.src, *end)
    node = pf.root.descendant_for_byte_range(s, e)
    top = node
    while top is not None and top.type != "call":
        top = top.parent
    via_descent = top is None and node is not None
    if top is None and node is not None:
        down = [node]
        while down and top is None:
            cur = down.pop(0)
            if cur.start_byte < s or cur.end_byte > e:
                if cur.end_byte > s and cur.start_byte < e:
                    down.extend(cur.children)
                continue
            if cur.type == "call":
                top = cur
            down.extend(cur.children)
    if top is None:
        return Verdict("unknown", "no call at the finding location")
    cands = []
    stack = [top]
    while stack:
        cur = stack.pop()
        if cur.type == "call":
            cands.append(cur)
        stack.extend(cur.children)

    def txt(n):
        return n.text.decode("utf-8", "replace") if n is not None else ""

    def describe(call):
        fn = call.child_by_field_name("function")
        an = call.child_by_field_name("arguments")
        ex = [c for c in (an.named_children if an is not None else []) if c.type not in ("keyword_argument", "comment", "dictionary_splat", "list_splat")]
        total = len(an.named_children) if an is not None else 0
        if fn is not None and fn.type == "attribute":
            name, recv, recv_n = txt(fn.child_by_field_name("attribute")), txt(fn.child_by_field_name("object")), fn.child_by_field_name("object")
        else:
            name, recv, recv_n = txt(fn), "", None
        pos: set[int] = set()
        for cwe in cwes:
            p = sink_args(cwe, name, recv, total, {}, False)
            if p:
                pos.update(p)
        if -1 in pos and recv_n is not None:
            ex = [recv_n] + ex
            pos = {0 if x == -1 else x + 1 for x in pos}
        return ex, pos

    call, exprs, positions = top, [], set()
    probes = []
    for cand in cands:
        e_, p_ = describe(cand)
        if p_:
            if not probes:
                call = cand
            probes += [e_[i] for i in sorted(p_) if i < len(e_)]
    if not probes and not via_descent:
        exprs, positions = describe(top)
        probes = [exprs[i] for i in sorted(positions) if i < len(exprs)] if positions else exprs
    if not probes and via_descent:
        return Verdict("unknown", "finding spans several statements and none is a known sink")
    if not probes:
        return Verdict("unknown", "sink has no arguments to judge")
    scope = call
    while scope is not None and scope.type != "function_definition":
        scope = scope.parent
    cls = None
    if scope is not None:
        for c in pf.classes.values():
            if c.node.start_byte <= scope.start_byte and scope.end_byte <= c.node.end_byte:
                cls = c
    interp = PyInterp(index, cwes)
    for p in probes:
        interp.probe_keys.add((id(pf), p.start_byte, p.end_byte))
    route = False
    if scope is not None:
        parent = scope.parent
        if parent is not None and parent.type == "decorated_definition":
            text = "".join(c.text.decode("utf-8", "replace") for c in parent.children if c.type == "decorator")
            route = any(m in text for m in ROUTE_MARKERS)
    fr = PFrame(pf, cls, Obj(cls=cls, type_name=cls.name) if cls is not None else None, 0, route=route)
    try:
        if scope is None:
            interp.exec(pf.root, fr)
        else:
            interp.bind_entry(scope, fr, route)
            interp.exec(scope.child_by_field_name("body"), fr)
    except (BudgetExceeded, RecursionError):
        return Verdict("unknown", "analysis budget exceeded")
    kinds = []
    for p in probes:
        key = (id(pf), p.start_byte, p.end_byte)
        vals = interp.probes.get(key)
        if vals:
            kinds.extend(c for _, c in vals)
        elif any(d[0] == key[0] and d[1] <= key[1] and key[2] <= d[2] for d in interp.dead):
            kinds.append("dead")
        else:
            kinds.append("unreached")
    if "tainted" in kinds:
        return Verdict("tainted")
    if "unknown" in kinds or "unreached" in kinds:
        return Verdict("unknown", ",".join(sorted(set(kinds))))
    if all(k == "dead" for k in kinds):
        return Verdict("unreachable", "sink is in a branch that cannot run")
    return Verdict("safe", "every value reaching the sink is constant or benign")
