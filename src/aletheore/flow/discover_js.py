"""JavaScript and TypeScript discover mode: follow request data to sinks, across functions and modules.

Entry points: every route handler registered with `app.get("/x", handler)`-style calls (inline or by
name), every function whose parameters look like a request, and the module body once (scripts and
setup code). Each weakness class is run separately because sanitizers are class specific.
"""

from __future__ import annotations

import time
from pathlib import Path

from aletheore.flow.js_index import FN_TYPES, JsFile, JsIndex
from aletheore.flow.prefilter import classes_present
from aletheore.flow.js_interp import BudgetExceeded, JFrame, JsInterp, request_like
from aletheore.flow.js_sinks import CWE_NAMES_JS, DISCOVER_CWES_JS, ROUTE_METHODS, SEVERITY_JS, sink_args
from aletheore.flow.values import Choice, Const, MapV, Obj, Tainted, classify
from aletheore.scanner.detect import IGNORED_DIRS

_SINK_WORDS = {
    89: (b"query(", b"execute(", b"raw(", b".run(", b".all(", b".get(", b"prepare("),
    78: (b"exec", b"spawn", b"fork(", b"child_process"),
    22: (b"fs.", b"fs\n", b"readFile", b"createReadStream", b"sendFile", b"download(", b"promises"),
    79: (b"res.send", b"res.write", b"res.end", b"response.send", b"reply.send", b"ctx.body"),
    94: (b"eval(", b"Function(", b"runInNewContext", b"setTimeout(", b"setInterval("),
    601: (b"redirect(",),
    918: (b"axios", b"fetch(", b"request(", b"needle", b"http.get", b"https.get", b"got(", b"got."),
    502: (b"unserialize", b"deserialize", b"yaml"),
    943: (b"$where", b"find(", b"findOne(", b"update(", b"remove(", b"deleteOne", b"countDocuments"),
    501: (b"session",),
}
_ENTRY_WORDS = (b"req", b"request", b"ctx.", b"process.argv", b"express", b"koa", b"fastify", b"hapi")
_EXTS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
_SKIP_PARTS = {"node_modules", "dist", "build", "coverage", "__tests__", "test", "tests", "spec", "e2e", "frontend", "client",
               "public", "static", "vendor", "bower_components", ".next", ".nuxt", "cypress", "storybook"}
_EXTRA_SKIP = {".worktrees", ".repowise"}
MAX_FILES = 6000


def _js_files(repo: Path) -> list[Path]:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP | _SKIP_PARTS
    out: list[Path] = []
    for p in repo.rglob("*"):
        if p.suffix not in _EXTS or p.name.endswith((".min.js", ".d.ts")) or ".test." in p.name or ".spec." in p.name:
            continue
        if skip.intersection(p.relative_to(repo).parts):
            continue
        out.append(p)
        if len(out) >= MAX_FILES:
            break
    return out


def has_javascript(repo: Path) -> bool:
    return bool(_js_files(repo))


def _direct(v) -> bool:
    return isinstance(v, Tainted) or (isinstance(v, Choice) and any(isinstance(a, Tainted) for a in v.alts))


def _tainted_string(v) -> bool:
    def one(t) -> bool:
        return isinstance(t, Tainted) and (t.why in ("derived", "process.argv", "element") or t.why.startswith("request"))
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts))


def _route_handlers(jf: JsFile):
    """Function nodes registered as route handlers: app.get('/x', (req, res) => ..., or by name)."""
    stack = [jf.root]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            f = n.child_by_field_name("function")
            if f is not None and f.type == "member_expression" and f.child_by_field_name("property") is not None \
                    and f.child_by_field_name("property").text.decode("utf-8", "replace") in ROUTE_METHODS:
                args = n.child_by_field_name("arguments")
                for a in (args.named_children if args is not None else []):
                    if a.type in FN_TYPES:
                        yield a
                    elif a.type == "identifier" and a.text.decode("utf-8", "replace") in jf.functions:
                        yield jf.functions[a.text.decode("utf-8", "replace")]
        stack.extend(n.children)


def _request_like(fn) -> bool:
    return request_like(fn)


def _request_like_functions(jf: JsFile):
    """Every function anywhere in the file whose parameters look like a request: handlers assigned to
    `this.x`, object properties, class methods and callbacks, not just declared functions."""
    stack = [jf.root]
    while stack:
        n = stack.pop()
        if (n.type in FN_TYPES or n.type == "method_definition") and _request_like(n):
            yield n
        stack.extend(n.children)


def discover_javascript(repo: Path, index: JsIndex | None = None, budget_seconds: float = 150.0) -> list[dict]:
    index = index or JsIndex(repo)
    repo_r = repo.resolve()
    started = time.monotonic()
    found: dict[tuple, dict] = {}
    files = _js_files(repo)
    present = classes_present(files, _SINK_WORDS)
    for path in files:
        if time.monotonic() - started > budget_seconds:
            break
        try:
            data = path.read_bytes()
        except OSError:
            continue
        entry_file = any(w in data for w in _ENTRY_WORDS)
        cwes = [c for c in DISCOVER_CWES_JS if c in present] if entry_file else [c for c in DISCOVER_CWES_JS if any(w in data for w in _SINK_WORDS[c])]
        if not cwes:
            continue
        jf = index.file(path)
        if jf is None:
            continue
        handlers = list(_route_handlers(jf))
        handler_ids = {id(h) for h in handlers}
        entries = [(None, False)] + [(h, True) for h in handlers]
        seen_entries = set(handler_ids)
        # Enclosing functions (a handler factory or constructor) build the scope their handlers close over.
        for fn in list(jf.functions.values()):
            if id(fn) not in seen_entries:
                seen_entries.add(id(fn))
                entries.append((fn, False))
        for fn in _request_like_functions(jf):
            if id(fn) not in seen_entries:
                seen_entries.add(id(fn))
                entries.append((fn, False))
        for cls in jf.classes.values():
            for m in cls.methods.values():
                if id(m) not in seen_entries:
                    seen_entries.add(id(m))
                    entries.append((m, False))
        for cwe in cwes:
            slug, label = CWE_NAMES_JS[cwe]

            def record(node, fr, what, _cwe=cwe, _slug=slug, _label=label):
                rel = str(fr.file.path.relative_to(repo_r)) if fr.file.path.is_relative_to(repo_r) else str(fr.file.path)
                line = node.start_point[0] + 1
                found.setdefault((rel, line, _cwe), {
                    "tool": "aletheore-flow", "rule_id": f"flow.javascript.{_slug}", "severity": SEVERITY_JS[_cwe],
                    "type": "vulnerability", "path": rel, "line": line, "cwe": [f"CWE-{_cwe}"],
                    "message": f"Request data reaches a {_label} sink without being validated or encoded for it ({what}). "
                               "Found by following the value through the code, including calls into other functions and modules.",
                })

            def sink(node, name, recv_text, args, recv, fr, _cwe=cwe):
                if _cwe == 943:
                    if name in ("find", "findOne", "update", "updateOne", "updateMany", "deleteOne", "deleteMany", "remove", "count",
                                "countDocuments", "aggregate", "findOneAndUpdate", "findOneAndDelete", "replaceOne"):
                        for a in args[:2]:
                            alts = a.alts if isinstance(a, Choice) else (a,)
                            if any(isinstance(m, MapV) and "$where" in m.entries and classify(m.entries["$where"]) == "tainted" for m in alts):
                                record(node, fr, f"$where clause passed to {name}")
                                return
                            if isinstance(a, Tainted) and a.why in ("request.body", "request.query", "request.params"):
                                record(node, fr, f"a whole request object passed as the query to {name}")
                                return
                    return
                positions = sink_args(_cwe, name, recv_text, len(args))
                if not positions:
                    return
                if _cwe == 79 and isinstance(recv, Obj) and isinstance(recv.fields.get("_ctype"), Const):
                    ctype = recv.fields["_ctype"].value
                    if "html" not in ctype:
                        return  # an explicit non-HTML content type (JSON, event stream, plain text) is not XSS
                for i in positions:
                    if i >= len(args):
                        continue
                    value = args[i]
                    hit = _tainted_string(value) if _cwe in (79, 601) else classify(value) == "tainted"
                    if hit:
                        record(node, fr, f"argument {i + 1} of {name}")
                        return

            def store(node, base_text, key, value, fr, _cwe=cwe):
                if _cwe == 501 and base_text.split(".")[-1] == "session" and (_tainted_string(key) or _tainted_string(value)):
                    record(node, fr, "write to the session")

            for fn, route in entries:
                if time.monotonic() - started > budget_seconds:
                    break
                interp = JsInterp(index, {cwe}, sink_hook=sink, store_hook=store)
                fr = JFrame(jf, None, Obj(type_name="module"), 0, route=route)
                try:
                    if fn is None:
                        interp.exec(jf.root, fr)
                    else:
                        interp.bind_entry(fn, fr, route)
                        interp._run_body(fn, fr)
                except (BudgetExceeded, RecursionError):
                    continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))


def validate_javascript_finding(repo: Path, rel_path: str, start: tuple[int, int], end: tuple[int, int], cwes: set[int], index: JsIndex | None = None):
    """Refine mode: prove the sink value of a Semgrep finding safe, or keep it (see validate_php_finding)."""
    from aletheore.flow.validate import Verdict, _offset

    index = index or JsIndex(repo)
    jf = index.file(repo / rel_path)
    if jf is None:
        return Verdict("unknown", "file unreadable")
    s, e = _offset(jf.src, *start), _offset(jf.src, *end)
    node = jf.root.descendant_for_byte_range(s, e)
    top = node
    while top is not None and top.type != "call_expression":
        top = top.parent
    via_descent = top is None and node is not None
    if via_descent:
        down = [node]
        while down and top is None:
            cur = down.pop(0)
            if cur.start_byte < s or cur.end_byte > e:
                if cur.end_byte > s and cur.start_byte < e:
                    down.extend(cur.children)
                continue
            if cur.type == "call_expression":
                top = cur
            down.extend(cur.children)
    if top is None:
        return Verdict("unknown", "no call at the finding location")
    cands = []
    stack = [top]
    while stack:
        cur = stack.pop()
        if cur.type == "call_expression":
            cands.append(cur)
        stack.extend(cur.children)

    def txt(n):
        return n.text.decode("utf-8", "replace") if n is not None else ""

    def describe(call):
        fn = call.child_by_field_name("function")
        an = call.child_by_field_name("arguments")
        ex = [c for c in (an.named_children if an is not None else []) if c.type not in ("comment", "spread_element")]
        if fn is not None and fn.type == "member_expression":
            name, recv = txt(fn.child_by_field_name("property")), txt(fn.child_by_field_name("object"))
        else:
            name, recv = txt(fn), ""
        pos: set[int] = set()
        for cwe in cwes:
            p = sink_args(cwe, name, recv, len(ex))
            if p:
                pos.update(p)
        return ex, pos

    call, probes = top, []
    for cand in cands:
        e_, p_ = describe(cand)
        if p_:
            if not probes:
                call = cand
            probes += [e_[i] for i in sorted(p_) if i < len(e_)]
    if not probes and not via_descent:
        ex, _ = describe(top)
        probes = ex
    if not probes and via_descent:
        return Verdict("unknown", "finding spans several statements and none is a known sink")
    if not probes:
        return Verdict("unknown", "sink has no arguments to judge")
    handler_starts = {h.start_byte for h in _route_handlers(jf)}
    chain = []
    cur = call.parent
    while cur is not None:
        if cur.type in FN_TYPES or cur.type == "method_definition":
            chain.append(cur)
        cur = cur.parent
    scope = None
    for fn in reversed(chain):  # outermost first
        if fn.start_byte in handler_starts or _request_like(fn):
            scope = fn
            break
    if scope is None and chain:
        scope = chain[0]
    route = scope is not None and scope.start_byte in handler_starts
    interp = JsInterp(index, cwes)
    for p in probes:
        interp.probe_keys.add((id(jf), p.start_byte, p.end_byte))
    fr = JFrame(jf, None, Obj(type_name="module"), 0, route=route)
    try:
        if scope is None:
            interp.exec(jf.root, fr)
        else:
            interp.bind_entry(scope, fr, route)
            interp._run_body(scope, fr)
    except (BudgetExceeded, RecursionError):
        return Verdict("unknown", "analysis budget exceeded")
    kinds = []
    for p in probes:
        key = (id(jf), p.start_byte, p.end_byte)
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
