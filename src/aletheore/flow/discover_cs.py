"""C# discover and validate modes (see discover_php.py for the shared design)."""

from __future__ import annotations

from aletheore.flow.budget import Budget

from pathlib import Path

from tree_sitter import Node

from aletheore.flow.cs_index import CsClass, CsFile, CsIndex
from aletheore.flow.cs_interp import BudgetExceeded, CFrame, CsInterp
from aletheore.flow.cs_sinks import CONTROLLER_BASES, CWE_NAMES_CS, DISCOVER_CWES_CS, SEVERITY_CS, sink_args
from aletheore.flow import coverage
from aletheore.flow import library as _lib
from aletheore.flow import extra_sinks as _X
from aletheore.flow.prefilter import classes_present
from aletheore.flow.values import host_is_fixed, path_prefix_safe, Choice, Const, Obj, Tainted, classify
from aletheore.scanner.detect import IGNORED_DIRS

_SINK_WORDS = {
    89: (b"Command", b"DataAdapter", b"FromSql", b"ExecuteSql", b"SqlQuery", b"Query(", b"Execute(", b"QueryFirst", b"CommandText"),
    78: (b"Process.Start", b"ProcessStartInfo"),
    22: (b"File.", b"Directory.", b"FileStream", b"StreamReader", b"StreamWriter", b"FileInfo", b"WriteFile", b"TransmitFile", b"PhysicalFile"),
    79: (b"Response.Write", b"Html.Raw", b"HtmlString", b"Content("),
    601: (b"Redirect",),
    918: (b"WebClient", b"HttpClient", b"WebRequest", b"DownloadString", b"GetAsync", b"PostAsync"),
    502: (b"Formatter", b"Deserialize"),
    90: (b"DirectorySearcher",),
    643: (b"SelectNodes", b"SelectSingleNode", b"XPath"),
    94: (b"CompileAssembly", b"Assembly.Load", b"Type.GetType"),
    501: (b"Session[",),
}
for _c in _X.rule_classes("csharp"):  # the shared catalogue's method names
    _SINK_WORDS[_c] = tuple(_SINK_WORDS.get(_c, ())) + _X.prefilter_words("csharp", _c)
_ENTRY_WORDS = (b"Request", b"Controller", b"[Http", b"[FromQuery", b"[FromBody", b"[FromRoute", b"[FromForm", b"Page_Load", b"PageModel")
_EXTRA_SKIP = {"obj", "packages", "bin", ".worktrees", ".repowise", "node_modules", "migrations", "Migrations", "Test", "Tests"}
MAX_FILES = 8000


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _cs_files(repo: Path) -> list[Path]:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    out: list[Path] = []
    for p in repo.rglob("*.cs"):
        if p.name.endswith((".Designer.cs", ".g.cs")) or skip.intersection(p.relative_to(repo).parts):
            continue
        out.append(p)
        if len(out) >= MAX_FILES:
            break
    return out


def has_cs(repo: Path) -> bool:
    return bool(_cs_files(repo))


def _tainted_string(v) -> bool:
    def one(t) -> bool:
        return isinstance(t, Tainted) and (t.why in ("derived", "route-param", "library parameter") or t.why.startswith("request"))
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts))


def _is_controller(cls: CsClass) -> bool:
    return any(b in CONTROLLER_BASES or b.endswith("Controller") for b in cls.bases)


def _entry_methods(cls: CsClass):
    for ms in cls.methods.values():
        for m in ms:
            yield m, False
    for c in cls.ctors:
        yield c, False


def _is_public(m: Node) -> bool:
    return any(c.type == "modifier" and _t(c) == "public" for c in m.children)




def _rel_parts(path: Path, repo: Path) -> tuple:
    try:
        return path.relative_to(repo).parts
    except ValueError:
        return path.resolve().relative_to(repo.resolve()).parts


def discover_cs(repo: Path, index: CsIndex | None = None, budget_seconds: float = 150.0) -> list[dict]:
    index = index or CsIndex(repo)
    repo_r = repo.resolve()
    budget = Budget()
    found: dict[tuple, dict] = {}
    files = _cs_files(repo)
    library = _lib.is_library(repo_r, "csharp")
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
            cwes = [c for c in DISCOVER_CWES_CS if c in present and _X.local_ok(c, data, _SINK_WORDS, "csharp")] if entry_file else [c for c in DISCOVER_CWES_CS if any(w in data for w in _SINK_WORDS[c])]
            cwes = [c for c in cwes if _X.added("csharp", c) == (phase == 1)]
            if not cwes:
                continue
            cf = index.file(path)
            if cf is None:
                continue
            for cwe in cwes:
                slug, label = CWE_NAMES_CS[cwe]

                route_now = [False]  # running a public function of a library

                def record(node, fr, what, _cwe=cwe, _slug=slug, _label=label):
                    rel = str(fr.file.path.relative_to(repo_r)) if fr.file.path.is_relative_to(repo_r) else str(fr.file.path)
                    line = node.start_point[0] + 1
                    lib = route_now[0]
                    found.setdefault((rel, line, _cwe), {
                        "tool": "aletheore-flow", "rule_id": f"flow.cs.{_slug}", "severity": "minor" if lib else SEVERITY_CS[_cwe], "type": "vulnerability",
                        "path": rel, "line": line, "cwe": [f"CWE-{_cwe}"],
                        "message": (f"A parameter of a public function reaches a {_label} sink without being validated or encoded for it ({what}). "
                                    "Whoever calls the function controls it. Found by following the value through the code." if lib else
                                    f"Request data reaches a {_label} sink without being validated or encoded for it ({what}). "
                                    "Found by following the value through the code, including calls into other methods and classes."),
                    })

                def sink(node, name, recv_text, args, recv, fr, _cwe=cwe):
                    positions = sink_args(_cwe, name, recv_text, len(args), recv.type_name if isinstance(recv, Obj) else None)
                    ext = False
                    if not positions:  # the shared catalogue, for a string built from request data
                        positions = _X.sink_args("csharp", _cwe, recv_text if name == "<new>" else name, "" if name == "<new>" else recv_text, len(args), args)
                        ext = bool(positions)
                        if not positions:
                            return
                    for i in positions:
                        if _cwe in (918, 601) and 0 <= i < len(args) and (host_is_fixed(args[i]) or path_prefix_safe(args[i])):
                            continue  # a fixed scheme://host/ prefix: only the path is attacker data
                        if i >= len(args):
                            continue
                        hit = _X.ext_hit("csharp", _cwe, recv_text if name == "<new>" else name, i, args, _tainted_string, node) if ext else (_tainted_string(args[i]) if _cwe in (79, 601) else classify(args[i]) == "tainted")
                        if hit:
                            record(node, fr, f"argument {i + 1} of {recv_text + '.' if recv_text else ''}{name}")
                            return

                def store(node, base_text, key, value, fr, _cwe=cwe):
                    if _cwe == 942:
                        if _X.acao_store(key, value, _tainted_string, node):
                            record(node, fr, "Access-Control-Allow-Origin set from the request")
                        return
                    if _cwe == 89 and isinstance(key, Const) and key.value == "CommandText" and classify(value) == "tainted":
                        record(node, fr, "assigned to CommandText")
                    elif _cwe == 90 and isinstance(key, Const) and key.value == "Filter" and classify(value) == "tainted" and "Searcher" in base_text.title():
                        record(node, fr, "assigned to the search filter")
                    elif _cwe == 501 and base_text.endswith("Session") and (_tainted_string(key) or _tainted_string(value)):
                        record(node, fr, "write to the session")

                for cls in cf.classes:
                    if budget.over(budget_seconds):
                        break
                    action_cls = _is_controller(cls)
                    for m, _ in _entry_methods(cls):
                        interp = CsInterp(index, {cwe}, sink_hook=sink, store_hook=store)
                        interp.MAX_STEPS = max(interp.MAX_STEPS, len(data) // 4)  # big files get a proportionally bigger budget
                        fr = CFrame(cf, cls, coverage.safe_this(interp, cls, cf), 0)
                        try:
                            route_now[0] = (not (action_cls and _is_public(m))) and library and cwe not in _lib.SKIP_CWES and not _lib.skipped_path(_rel_parts(path, repo)) and _is_public(m) and _is_public(cls.node)
                            interp.bind_entry(m, fr, "lib" if route_now[0] else (action_cls and _is_public(m)))
                            body = m.child_by_field_name("body") or next((c for c in m.children if c.type in ("block", "arrow_expression_clause")), None)
                            if body is not None and body.type == "arrow_expression_clause":
                                interp.eval(body.named_children[-1] if body.named_children else None, fr)
                            else:
                                interp.exec(body, fr)
                        except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                            coverage.note(path)
                            continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))


def validate_cs_finding(repo: Path, rel_path: str, start: tuple[int, int], end: tuple[int, int], cwes: set[int], index: CsIndex | None = None):
    from aletheore.flow.validate import Verdict, _offset

    index = index or CsIndex(repo)
    cf = index.file(repo / rel_path)
    if cf is None:
        return Verdict("unknown", "file unreadable")
    s, e = _offset(cf.src, *start), _offset(cf.src, *end)
    node = cf.root.descendant_for_byte_range(s, e)
    top = node
    while top is not None and top.type not in ("invocation_expression", "object_creation_expression", "assignment_expression"):
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
            if cur.type in ("invocation_expression", "object_creation_expression", "assignment_expression"):
                top = cur
            down.extend(cur.children)
    if top is None:
        return Verdict("unknown", "no call at the finding location")
    cands: list[Node] = []
    stack = [top]
    while stack:
        cur = stack.pop()
        if cur.type in ("invocation_expression", "object_creation_expression", "assignment_expression"):
            cands.append(cur)
        stack.extend(cur.children)

    def exprs_of(args_n: Node | None) -> list[Node]:
        return [(a.named_children[-1] if a.type == "argument" and a.named_children else a) for a in (args_n.named_children if args_n is not None else [])]

    def describe(call: Node):
        if call.type == "assignment_expression":
            left = call.child_by_field_name("left")
            if left is not None and left.type == "member_access_expression" and _t(left.child_by_field_name("name")) == "CommandText" and 89 in cwes:
                return [call.child_by_field_name("right")], {0}
            return [], set()
        if call.type == "object_creation_expression":
            name, recv = "<new>", _t(call.child_by_field_name("type")).split("<")[0].split(".")[-1]
            ex = exprs_of(call.child_by_field_name("arguments"))
        else:
            func = call.child_by_field_name("function")
            ex = exprs_of(call.child_by_field_name("arguments"))
            if func is not None and func.type == "member_access_expression":
                name, recv = _t(func.child_by_field_name("name")), _t(func.child_by_field_name("expression"))
            else:
                name, recv = _t(func), ""
        pos: set[int] = set()
        for cwe in cwes:
            p = sink_args(cwe, name, recv, len(ex), None)
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
    while scope is not None and scope.type not in ("method_declaration", "constructor_declaration", "accessor_declaration"):
        scope = scope.parent
    if scope is None:
        return Verdict("unknown", "not inside a method")
    cls = next((c for c in cf.classes if c.node.start_byte <= scope.start_byte and scope.end_byte <= c.node.end_byte
                and not any(o is not c and c.node.start_byte < o.node.start_byte and o.node.end_byte <= c.node.end_byte
                            and o.node.start_byte <= scope.start_byte and scope.end_byte <= o.node.end_byte for o in cf.classes)), None)
    if cls is None:
        return Verdict("unknown", "no enclosing class")
    interp = CsInterp(index, cwes)
    fr = CFrame(cf, cls, Obj(cls=cls, type_name=cls.name), 0)
    for p in probes:
        interp.probe_keys.add((id(cf), p.start_byte, p.end_byte))
    try:
        interp.bind_entry(scope, fr, _is_controller(cls) and _is_public(scope))
        body = scope.child_by_field_name("body") or next((c for c in scope.children if c.type in ("block", "arrow_expression_clause")), None)
        if body is not None and body.type == "arrow_expression_clause":
            interp.eval(body.named_children[-1] if body.named_children else None, fr)
        else:
            interp.exec(body, fr)
    except (BudgetExceeded, RecursionError):
        return Verdict("unknown", "analysis budget exceeded")
    kinds = []
    for p in probes:
        key = (id(cf), p.start_byte, p.end_byte)
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
