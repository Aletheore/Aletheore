"""Rust discover and validate modes (see discover_php.py for the shared design)."""

from __future__ import annotations

from aletheore.flow.budget import Budget

from pathlib import Path

from tree_sitter import Node

from aletheore.flow import coverage
from aletheore.flow import library as _lib
from aletheore.flow import extra_sinks as _X
from aletheore.flow.prefilter import classes_present
from aletheore.flow.rs_index import RsIndex, RsType, attrs_before, type_name
from aletheore.flow.rs_interp import BudgetExceeded, RsInterp, SFrame
from aletheore.flow.rs_sinks import CWE_NAMES_RS, DISCOVER_CWES_RS, EXTRACTORS, REQUEST_TYPES, ROUTE_ATTRS, SEVERITY_RS, sink_args
from aletheore.flow.values import host_is_fixed, path_prefix_safe, Choice, Obj, Tainted, classify
from aletheore.scanner.detect import IGNORED_DIRS

_SINK_WORDS = {
    89: (b"execute", b"query", b"prepare", b"fetch", b"sql_query", b"raw_sql", b"simple_query", b"batch_execute", b"exec"),
    78: (b"Command",),
    22: (b"File::", b"fs::", b"NamedFile", b"OpenOptions", b"ServeFile"),
    79: (b"Html", b"text/html"),
    601: (b"Redirect",),
    918: (b"reqwest", b"ureq", b"surf", b"isahc", b"attohttpc", b"minreq", b"client", b"Client"),
    94: (b"Library", b"eval", b"engine", b"lua", b"rhai"),
}
for _c in _X.rule_classes("rust"):  # the shared catalogue's method names
    _SINK_WORDS[_c] = tuple(_SINK_WORDS.get(_c, ())) + _X.prefilter_words("rust", _c)
_ENTRY_WORDS = (b"HttpRequest", b"web::Query", b"web::Path", b"web::Json", b"web::Form", b"Query<", b"Path<", b"Json<", b"Form<", b"#[get", b"#[post", b"#[put", b"#[delete", b"axum", b"rocket", b"warp", b"actix")
_EXTRA_SKIP = {"target", "tests", "benches", "examples", ".worktrees", ".repowise", "node_modules", "vendor"}
MAX_FILES = 6000


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _rs_files(repo: Path) -> list[Path]:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    out: list[Path] = []
    for p in repo.rglob("*.rs"):
        if skip.intersection(p.relative_to(repo).parts):
            continue
        out.append(p)
        if len(out) >= MAX_FILES:
            break
    return out


def has_rs(repo: Path) -> bool:
    return bool(_rs_files(repo))


def _tainted_string(v) -> bool:
    def one(t) -> bool:
        return isinstance(t, Tainted) and (t.why in ("derived", "param", "library parameter") or t.why.startswith("request"))
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts))


def _is_action(fn: Node) -> bool:
    if any(a.startswith(ROUTE_ATTRS) for a in attrs_before(fn)):
        return True
    pn = fn.child_by_field_name("parameters")
    for p in (pn.named_children if pn is not None else []):
        if p.type == "parameter":
            tn = type_name(p.child_by_field_name("type"))
            if tn in EXTRACTORS or tn in REQUEST_TYPES:
                return True
    return False


def _public_rs(fn) -> bool:
    return any(c.type == "visibility_modifier" for c in fn.children)

def _rel_parts(path: Path, repo: Path) -> tuple:
    try:
        return path.relative_to(repo).parts
    except ValueError:
        return path.resolve().relative_to(repo.resolve()).parts


def discover_rs(repo: Path, index: RsIndex | None = None, budget_seconds: float = 150.0) -> list[dict]:
    index = index or RsIndex(repo)
    repo_r = repo.resolve()
    budget = Budget()
    found: dict[tuple, dict] = {}
    files = _rs_files(repo)
    library = _lib.is_library(repo_r, "rust")
    present = classes_present(files, _SINK_WORDS)
    core_budget = budget_seconds
    # Core classes first, with the whole budget; the added classes (template, regex, reflection, ...) after, in a
    # bounded extra slice, so a large repository never loses a core finding to the time the added classes take.
    for phase in (0, 1):
        budget_seconds = core_budget if phase == 0 else core_budget * 1.4
        for path in files:
            if budget.over(budget_seconds):
                break
            try:
                data = path.read_bytes()
            except OSError:
                continue
            entry_file = any(w in data for w in _ENTRY_WORDS)
            cwes = [c for c in DISCOVER_CWES_RS if c in present and _X.local_ok(c, data, _SINK_WORDS, "rust")] if entry_file else [c for c in DISCOVER_CWES_RS if any(w in data for w in _SINK_WORDS[c])]
            cwes = [c for c in cwes if _X.added("rust", c) == (phase == 1)]
            if not cwes:
                continue
            rf = index.file(path)
            if rf is None:
                continue
            for cwe in cwes:
                slug, label = CWE_NAMES_RS[cwe]

                route_now = [False]  # running a public function of a library

                def record(node, fr, what, _cwe=cwe, _slug=slug, _label=label):
                    rel = str(fr.file.path.relative_to(repo_r)) if fr.file.path.is_relative_to(repo_r) else str(fr.file.path)
                    line = node.start_point[0] + 1
                    lib = route_now[0]
                    found.setdefault((rel, line, _cwe), {
                        "tool": "aletheore-flow", "rule_id": f"flow.rs.{_slug}", "severity": "minor" if lib else SEVERITY_RS[_cwe], "type": "vulnerability",
                        "path": rel, "line": line, "cwe": [f"CWE-{_cwe}"],
                        "message": (f"A parameter of a public function reaches a {_label} sink without being validated or encoded for it ({what}). "
                                    "Whoever calls the function controls it. Found by following the value through the code." if lib else
                                    f"Request data reaches a {_label} sink without being validated or encoded for it ({what}). "
                                    "Found by following the value through the code, including calls into other functions and types."),
                    })

                def sink(node, name, recv_text, args, recv, fr, _cwe=cwe):
                    positions = sink_args(_cwe, name, recv_text, len(args))
                    ext = False
                    if not positions:  # the shared catalogue, for a string built from request data
                        positions = _X.sink_args("rust", _cwe, name, recv_text, len(args), args)
                        ext = bool(positions)
                        if not positions:
                            return
                    for i in positions:
                        if _cwe in (918, 601) and 0 <= i < len(args) and (host_is_fixed(args[i]) or path_prefix_safe(args[i])):
                            continue  # a fixed scheme://host/ prefix: only the path is attacker data
                        if i >= len(args):
                            continue
                        hit = _X.ext_hit("rust", _cwe, name, i, args, _tainted_string, node) if ext else (_tainted_string(args[i]) if _cwe in (79, 601) else classify(args[i]) == "tainted")
                        if hit:
                            record(node, fr, f"argument {i + 1} of {recv_text + '.' if recv_text else ''}{name}")
                            return

                for ty in rf.types:
                    if budget.over(budget_seconds):
                        break
                    for fns in ty.funcs.values():
                        for fn in fns:
                            interp = RsInterp(index, {cwe}, sink_hook=sink)
                            interp.MAX_STEPS = max(interp.MAX_STEPS, len(data) // 4)  # big files get a proportionally bigger budget
                            fr = SFrame(rf, ty, Obj(cls=ty, type_name=ty.name) if ty.name != "<main>" else None, 0)
                            try:
                                route_now[0] = (not _is_action(fn)) and library and cwe not in _lib.SKIP_CWES and not _lib.skipped_path(_rel_parts(path, repo)) and _public_rs(fn)
                                interp.bind_entry(fn, fr, "lib" if route_now[0] else _is_action(fn))
                                interp.exec(fn.child_by_field_name("body"), fr)
                            except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                                coverage.note(path)
                                continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))


def validate_rs_finding(repo: Path, rel_path: str, start: tuple[int, int], end: tuple[int, int], cwes: set[int], index: RsIndex | None = None):
    from aletheore.flow.validate import Verdict, _offset

    index = index or RsIndex(repo)
    rf = index.file(repo / rel_path)
    if rf is None:
        return Verdict("unknown", "file unreadable")
    s, e = _offset(rf.src, *start), _offset(rf.src, *end)
    node = rf.root.descendant_for_byte_range(s, e)
    top = node
    while top is not None and top.type != "call_expression":
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
            if cur.type == "call_expression":
                top = cur
            down.extend(cur.children)
    if top is None:
        return Verdict("unknown", "no call at the finding location")
    cands: list[Node] = []
    stack = [top]
    while stack:
        cur = stack.pop()
        if cur.type == "call_expression":
            cands.append(cur)
        stack.extend(cur.children)

    def describe(call: Node):
        fn = call.child_by_field_name("function")
        if fn is not None and fn.type == "generic_function":
            fn = fn.child_by_field_name("function")
        a = call.child_by_field_name("arguments")
        ex = [c for c in (a.named_children if a is not None else []) if c.type not in ("line_comment", "block_comment")]
        if fn is not None and fn.type == "field_expression":
            name, recv = _t(fn.child_by_field_name("field")), _t(fn.child_by_field_name("value"))
        elif fn is not None and fn.type == "scoped_identifier":
            name, recv = _t(fn.child_by_field_name("name")), _t(fn.child_by_field_name("path"))
        else:
            name, recv = _t(fn), ""
        pos: set[int] = set()
        for cwe in cwes:
            p = sink_args(cwe, name, recv, len(ex))
            if p:
                pos.update(p)
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
    while scope is not None and scope.type != "function_item":
        scope = scope.parent
    if scope is None:
        return Verdict("unknown", "not inside a function")
    ty: RsType | None = None
    for t_ in rf.types:
        if t_.name != "<main>" and t_.node.start_byte <= scope.start_byte and scope.end_byte <= t_.node.end_byte:
            ty = t_
    # impl blocks are merged by type name; find the one that owns this function
    for t_ in rf.types:
        if any(f is scope or (f.start_byte == scope.start_byte) for fs in t_.funcs.values() for f in fs):
            ty = t_
            break
    ty = ty or rf.main
    if ty is None:
        return Verdict("unknown", "no enclosing type")
    interp = RsInterp(index, cwes)
    fr = SFrame(rf, ty, Obj(cls=ty, type_name=ty.name) if ty.name != "<main>" else None, 0)
    for p in probes:
        interp.probe_keys.add((id(rf), p.start_byte, p.end_byte))
    try:
        interp.bind_entry(scope, fr, _is_action(scope))
        interp.exec(scope.child_by_field_name("body"), fr)
    except (BudgetExceeded, RecursionError):
        return Verdict("unknown", "analysis budget exceeded")
    kinds = []
    for p in probes:
        key = (id(rf), p.start_byte, p.end_byte)
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
