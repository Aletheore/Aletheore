"""Go discover and validate modes (see discover_php.py for the shared design)."""

from __future__ import annotations

from aletheore.flow.budget import Budget

from pathlib import Path

from tree_sitter import Node

from aletheore.flow.go_index import GoFile, GoIndex
from aletheore.flow import coverage
from aletheore.flow import extra_sinks as _X
from aletheore.flow.prefilter import classes_present
from aletheore.flow.go_interp import BudgetExceeded, GFrame, GoInterp
from aletheore.flow.go_sinks import CWE_NAMES_GO, DISCOVER_CWES_GO, SEVERITY_GO, sink_args
from aletheore.flow import library as _lib
from aletheore.flow.values import host_is_fixed, path_prefix_safe, Choice, Const, Obj, Tainted, classify
from aletheore.scanner.detect import IGNORED_DIRS

_SINK_WORDS = {
    89: (b".Query", b".Exec", b".Prepare", b".Raw(", b".Select(", b".Get(", b".Where(", b"QueryRow"),
    78: (b"exec.Command", b"syscall.Exec"),
    22: (b"os.Open", b"os.ReadFile", b"ioutil.", b"os.Create", b"os.Remove", b"ServeFile", b"os.Mkdir", b"os.Stat", b"os.WriteFile", b"filepath.Walk", b"os.Rename"),
    79: (b"Fprintf", b"Fprint", b".Write(", b"WriteString", b"template.HTML", b"c.String", b"c.HTML", b"c.Data"),
    601: (b"Redirect",),
    918: (b"http.Get", b"http.Post", b"http.Head", b"http.NewRequest", b"net.Dial", b".Get(", b".Post("),
    90: (b"ldap.",),
    643: (b"xpath", b"xmlquery", b"htmlquery"),
    94: (b"plugin.Open",),
    502: (b"yaml.", b"gob."),
    501: (b".Values["),
}
for _c in _X.rule_classes("go"):  # the shared catalogue's method names
    _SINK_WORDS[_c] = tuple(_SINK_WORDS.get(_c, ())) + _X.prefilter_words("go", _c)
_ENTRY_WORDS = (b"http.Request", b"gin.Context", b"echo.Context", b"fiber.Ctx", b"os.Args", b"fasthttp", b"iris.Context", b"mux.Vars", b"chi.URLParam")
_EXTRA_SKIP = {"vendor", "testdata", ".worktrees", ".repowise", "node_modules", "third_party"}
MAX_FILES = 6000


def _go_files(repo: Path) -> list[Path]:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    out: list[Path] = []
    for p in repo.rglob("*.go"):
        if p.name.endswith("_test.go") or skip.intersection(p.relative_to(repo).parts):
            continue
        out.append(p)
        if len(out) >= MAX_FILES:
            break
    return out


def has_go(repo: Path) -> bool:
    return bool(_go_files(repo))


def _tainted_string(v) -> bool:
    def one(t) -> bool:
        return isinstance(t, Tainted) and (t.why in ("derived", "library parameter") or t.why.startswith("request"))
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts))


def _rel_parts(path: Path, repo: Path) -> tuple:
    try:
        return path.relative_to(repo).parts
    except ValueError:
        return path.resolve().relative_to(repo.resolve()).parts


def _functions(gf: GoFile, library: bool = False):
    """(receiver type or None, function, is a public library entry). In a library, an exported function (or an exported
    method of an exported type) is called with whatever the importing program passes."""
    for name, fn in gf.functions.items():
        yield None, fn, library and name[:1].isupper()
    for (rtype, name), m in gf.methods.items():
        yield rtype, m, library and name[:1].isupper() and (rtype or "").lstrip("*")[:1].isupper()


def discover_go(repo: Path, index: GoIndex | None = None, budget_seconds: float = 150.0) -> list[dict]:
    index = index or GoIndex(repo)
    repo_r = repo.resolve()
    budget = Budget()
    found: dict[tuple, dict] = {}
    files = _go_files(repo)
    library = _lib.is_library(repo_r, "go")
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
            cwes = [c for c in DISCOVER_CWES_GO if c in present and _X.local_ok(c, data, _SINK_WORDS, "go")] if entry_file else [c for c in DISCOVER_CWES_GO if any(w in data for w in _SINK_WORDS[c])]
            cwes = [c for c in cwes if _X.added("go", c) == (phase == 1)]
            if not cwes:
                continue
            gf = index.file(path)
            if gf is None:
                continue
            for cwe in cwes:
                slug, label = CWE_NAMES_GO[cwe]

                route_now = [False]  # running an exported function of a library

                def record(node, fr, what, _cwe=cwe, _slug=slug, _label=label):
                    rel = str(fr.file.path.relative_to(repo_r)) if fr.file.path.is_relative_to(repo_r) else str(fr.file.path)
                    line = node.start_point[0] + 1
                    lib = route_now[0]
                    found.setdefault((rel, line, _cwe), {
                        "tool": "aletheore-flow", "rule_id": f"flow.go.{_slug}", "severity": "minor" if lib else SEVERITY_GO[_cwe], "type": "vulnerability",
                        "path": rel, "line": line, "cwe": [f"CWE-{_cwe}"],
                        "message": (f"A parameter of an exported function reaches a {_label} sink without being validated or encoded for it ({what}). "
                                    "Whoever calls the function controls it. Found by following the value through the code." if lib else
                                    f"Request data reaches a {_label} sink without being validated or encoded for it ({what}). "
                                    "Found by following the value through the code, including calls into other functions and packages."),
                    })

                def sink(node, name, recv_text, args, recv, fr, _cwe=cwe):
                    first_const = args[0].value if args and isinstance(args[0], Const) and isinstance(args[0].value, str) else None
                    positions = sink_args(_cwe, name, recv_text, len(args), first_const)
                    ext = False
                    if not positions:  # the shared catalogue, for a string built from request data
                        positions = _X.sink_args("go", _cwe, name, recv_text, len(args), args)
                        ext = bool(positions)
                        if not positions:
                            return
                    if _cwe == 79:
                        writer = args[0] if recv_text.split(".")[-1] in ("fmt", "io") and args else recv
                        if not (isinstance(writer, Obj) and (writer.type_name == "ResponseWriter" or writer.kind == "request")):
                            return
                        ct = writer.fields.get("_ctype") if isinstance(writer, Obj) else None
                        if isinstance(ct, Const) and "html" not in str(ct.value):
                            return
                    for i in positions:
                        if _cwe in (918, 601) and 0 <= i < len(args) and (host_is_fixed(args[i]) or path_prefix_safe(args[i])):
                            continue  # a fixed scheme://host/ prefix: only the path is attacker data
                        if i >= len(args):
                            continue
                        hit = _X.ext_hit("go", _cwe, name, i, args, _tainted_string, node) if ext else (_tainted_string(args[i]) if _cwe in (79, 601) else classify(args[i]) == "tainted")
                        if hit:
                            record(node, fr, f"argument {i + 1} of {recv_text + '.' if recv_text else ''}{name}")
                            return

                def store(node, base_text, key, value, fr, _cwe=cwe):
                    if _cwe == 942:
                        if _X.acao_store(key, value, _tainted_string, node):
                            record(node, fr, "Access-Control-Allow-Origin set from the request")
                        return
                    if _cwe == 501 and base_text.endswith(".Values") and (_tainted_string(key) or _tainted_string(value)):
                        record(node, fr, "write to the session")

                stash: set = set()
                skip_lib = cwe in _lib.SKIP_CWES or _lib.skipped_path(_rel_parts(path, repo))
                fns = list(_functions(gf, library and not skip_lib))
                for step, (rtype, fn, is_lib) in enumerate(fns + fns):  # a second pass sees state stored by a handler run later
                    if budget.over(budget_seconds) or (step >= len(fns) and not stash):
                        break
                    interp = GoInterp(index, {cwe}, sink_hook=sink, store_hook=store, stash=stash)
                    interp.MAX_STEPS = max(interp.MAX_STEPS, len(data) // 4)  # big files get a proportionally bigger budget
                    this = Obj(type_name=rtype) if rtype else None
                    fr = GFrame(gf, this, 0)
                    route_now[0] = is_lib
                    try:
                        interp.bind_entry(fn, fr, is_lib)
                        interp.exec(fn.child_by_field_name("body"), fr)
                    except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                        coverage.note(path)
                        continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))


def validate_go_finding(repo: Path, rel_path: str, start: tuple[int, int], end: tuple[int, int], cwes: set[int], index: GoIndex | None = None):
    from aletheore.flow.validate import Verdict, _offset

    index = index or GoIndex(repo)
    gf = index.file(repo / rel_path)
    if gf is None:
        return Verdict("unknown", "file unreadable")
    s, e = _offset(gf.src, *start), _offset(gf.src, *end)
    node = gf.root.descendant_for_byte_range(s, e)
    # The range may cover a chain such as exec.Command(...).Run(): pick the call in it that IS a sink.
    candidates: list[Node] = []
    top = node
    while top is not None and top.type != "call_expression":
        top = top.parent
    if top is None:
        return Verdict("unknown", "no call at the finding location")
    stack = [top]
    while stack:
        cur = stack.pop()
        if cur.type == "call_expression":
            candidates.append(cur)
        stack.extend(cur.children)

    def describe(call: Node):
        func = call.child_by_field_name("function")
        args_n = call.child_by_field_name("arguments")
        exprs = list(args_n.named_children) if args_n is not None else []
        first = exprs[0].text.decode("utf-8", "replace").strip('"`') if exprs and exprs[0].type in ("interpreted_string_literal", "raw_string_literal") else None
        positions: set[int] = set()
        for cwe in cwes:
            pos = sink_args(cwe, _t_name(func), _t_recv(func), len(exprs), first)
            if pos:
                positions.update(pos)
        return exprs, positions

    via_descent = False
    call, exprs, positions = top, [], set()
    probes = []
    for cand in candidates:
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
    while scope is not None and scope.type not in ("function_declaration", "method_declaration"):
        scope = scope.parent
    if scope is None:
        return Verdict("unknown", "not inside a function")
    rtype = None
    if scope.type == "method_declaration":
        from aletheore.flow.go_index import _recv_type
        rtype = _recv_type(scope)
    interp = GoInterp(index, cwes)
    fr = GFrame(gf, Obj(type_name=rtype) if rtype else None, 0)
    for p in probes:
        interp.probe_keys.add((id(gf), p.start_byte, p.end_byte))
    try:
        interp.bind_entry(scope, fr)
        interp.exec(scope.child_by_field_name("body"), fr)
    except (BudgetExceeded, RecursionError):
        return Verdict("unknown", "analysis budget exceeded")
    kinds = []
    for p in probes:
        key = (id(gf), p.start_byte, p.end_byte)
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


def _t_name(func: Node | None) -> str:
    if func is None:
        return ""
    if func.type == "selector_expression":
        f = func.child_by_field_name("field")
        return f.text.decode("utf-8", "replace") if f is not None else ""
    return func.text.decode("utf-8", "replace")


def _t_recv(func: Node | None) -> str:
    if func is not None and func.type == "selector_expression":
        o = func.child_by_field_name("operand")
        return o.text.decode("utf-8", "replace") if o is not None else ""
    return ""
