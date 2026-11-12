"""Ruby discover and validate modes (see discover_php.py for the shared design)."""

from __future__ import annotations

from aletheore.flow.budget import Budget

from pathlib import Path

from tree_sitter import Node

from aletheore.flow import coverage
from aletheore.flow import library as _lib
from aletheore.flow import extra_sinks as _X
from aletheore.flow.prefilter import classes_present
from aletheore.flow.rb_index import RbClass, RbFile, RbIndex
from aletheore.flow.rb_interp import BudgetExceeded, RbInterp, RFrame
from aletheore.flow.rb_sinks import CWE_NAMES_RB, DISCOVER_CWES_RB, SEVERITY_RB, SINATRA_VERBS, sink_args
from aletheore.flow.values import host_is_fixed, path_prefix_safe, Choice, Const, MapV, Obj, Tainted, classify
from aletheore.scanner.detect import IGNORED_DIRS

_SINK_WORDS = {
    89: (b".where", b"find_by_sql", b"execute", b"exec_query", b".order", b".group", b".having", b".joins", b".from", b"pluck", b"exists?", b"find_by", b"select_all", b"select_value", b"update_all", b"delete_all", b"select_one", b"select_rows", b".calculate"),
    78: (b"system", b"exec", b"spawn", b"popen", b"Open3", b"`", b"%x", b"open("),
    22: (b"File.", b"IO.", b"Dir.", b"Pathname", b"FileUtils", b"send_file", b"render file", b"render template", b"render partial", b"open(", b"Tempfile"),
    79: (b"html_safe", b"raw"),
    601: (b"redirect_to", b"redirect "),
    918: (b"Net::HTTP", b"HTTParty", b"RestClient", b"Faraday", b"URI.open", b"Typhoeus", b"Excon", b"Curl", b"open-uri", b"Mechanize"),
    502: (b"Marshal", b"YAML", b"Psych", b"Oj.", b"Ox."),
    94: (b"eval", b"send", b"constantize", b"const_get", b"ERB.new", b"render inline", b"instance_variable", b"define_method", b".method("),
    643: (b"xpath", b"XPath"),
    90: (b"Filter.construct",),
    501: (b"session[",),
}
for _c in _X.rule_classes("ruby"):  # the shared catalogue's method names
    _SINK_WORDS[_c] = tuple(_SINK_WORDS.get(_c, ())) + _X.prefilter_words("ruby", _c)
_ENTRY_WORDS = (b"params", b"cookies", b"request", b"Controller", b"Sinatra", b"Rack", b"Grape", b"Hanami")
_EXTRA_SKIP = {"spec", "test", "tests", "vendor", "db", "node_modules", ".worktrees", ".repowise", "tmp", "features", "bin"}
MAX_FILES = 6000


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _rb_files(repo: Path) -> list[Path]:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    out: list[Path] = []
    for p in repo.rglob("*.rb"):
        if skip.intersection(p.relative_to(repo).parts):
            continue
        out.append(p)
        if len(out) >= MAX_FILES:
            break
    return out


def has_rb(repo: Path) -> bool:
    return bool(_rb_files(repo))


def _tainted_string(v) -> bool:
    def one(t) -> bool:
        return isinstance(t, Tainted) and (t.why in ("derived", "route-param", "library parameter") or t.why.startswith(("params", "request", "cookies")))
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts))


def _is_controller(cls: RbClass) -> bool:
    return b"Hanami::Action" in cls.node.text or any(b.endswith("Controller") or b in ("ActionController::Base", "Sinatra::Base", "Grape::API") for b in cls.bases) or cls.name.endswith("Controller")


def _sinatra_blocks(cls: RbClass):
    body = cls.node.child_by_field_name("body") if cls.name != "<main>" else cls.node
    for c in (body.named_children if body is not None else []):
        if c.type == "call" and c.child_by_field_name("receiver") is None and _t(c.child_by_field_name("method")) in SINATRA_VERBS:
            blk = c.child_by_field_name("block")
            if blk is not None:
                yield blk


def _exec_block(interp: RbInterp, blk: Node, fr: RFrame) -> None:
    bp = blk.child_by_field_name("parameters") or next((c for c in blk.named_children if c.type == "block_parameters"), None)
    for c in (bp.named_children if bp is not None else []):
        if c.type == "identifier":
            fr.env[_t(c)] = Tainted("route-param")
    body = blk.child_by_field_name("body") or next((c for c in blk.named_children if c.type in ("block_body", "body_statement")), None)
    interp.exec(body, fr)


def _rel_parts(path: Path, repo: Path) -> tuple:
    try:
        return path.relative_to(repo).parts
    except ValueError:
        return path.resolve().relative_to(repo.resolve()).parts


def discover_rb(repo: Path, index: RbIndex | None = None, budget_seconds: float = 150.0) -> list[dict]:
    index = index or RbIndex(repo)
    repo_r = repo.resolve()
    budget = Budget()
    found: dict[tuple, dict] = {}
    files = _rb_files(repo)
    library = _lib.is_library(repo_r, "ruby")
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
            cwes = [c for c in DISCOVER_CWES_RB if c in present and _X.local_ok(c, data, _SINK_WORDS, "ruby")] if entry_file else [c for c in DISCOVER_CWES_RB if any(w in data for w in _SINK_WORDS[c])]
            cwes = [c for c in cwes if _X.added("ruby", c) == (phase == 1)]
            if not cwes:
                continue
            rf = index.file(path)
            if rf is None:
                continue
            for cwe in cwes:
                slug, label = CWE_NAMES_RB[cwe]

                route_now = [False]  # running a public function of a library

                def record(node, fr, what, _cwe=cwe, _slug=slug, _label=label):
                    rel = str(fr.file.path.relative_to(repo_r)) if fr.file.path.is_relative_to(repo_r) else str(fr.file.path)
                    line = node.start_point[0] + 1
                    lib = route_now[0]
                    found.setdefault((rel, line, _cwe), {
                        "tool": "aletheore-flow", "rule_id": f"flow.rb.{_slug}", "severity": "minor" if lib else SEVERITY_RB[_cwe], "type": "vulnerability",
                        "path": rel, "line": line, "cwe": [f"CWE-{_cwe}"],
                        "message": (f"A parameter of a public function reaches a {_label} sink without being validated or encoded for it ({what}). "
                                    "Whoever calls the function controls it. Found by following the value through the code." if lib else
                                    f"Request data reaches a {_label} sink without being validated or encoded for it ({what}). "
                                    "Found by following the value through the code, including calls into other methods and classes."),
                    })

                def sink(node, name, recv_text, args, recv, fr, _cwe=cwe):
                    if _cwe == 915:
                        if name == "permit!" and (recv_text.split(".")[0] in ("params", "request") or (isinstance(recv, Obj) and recv.kind == "request")):
                            record(node, fr, "params.permit! allows every request field into the model")
                        return
                    if name == "render" and not recv_text and args and isinstance(args[-1], MapV):
                        opts = args[-1].entries
                        if _cwe == 22 and any(_cond_taint(opts.get(k)) for k in ("file", "template", "partial")):
                            record(node, fr, "render file/template/partial name")
                        elif _cwe == 94 and _cond_taint(opts.get("inline")):
                            record(node, fr, "render inline template")
                        return
                    first_const = bool(args) and isinstance(args[0], Const)
                    positions = sink_args(_cwe, name, recv_text, len(args), first_const)
                    ext = False
                    if not positions:  # the shared catalogue, for a string built from request data
                        positions = _X.sink_args("ruby", _cwe, name, recv_text, len(args), args)
                        ext = bool(positions)
                        if not positions:
                            return
                    if _cwe == 601 and len(args) >= 2 and isinstance(args[-1], MapV) and isinstance(args[-1].entries.get("only_path"), Const) and args[-1].entries["only_path"].value is True:
                        return
                    if _cwe == 601 and args and isinstance(args[-1], MapV) and isinstance(args[-1].entries.get("allow_other_host"), Const) and args[-1].entries["allow_other_host"].value is False:
                        return
                    for i in positions:
                        if _cwe in (918, 601) and 0 <= i < len(args) and (host_is_fixed(args[i]) or path_prefix_safe(args[i])):
                            continue  # a fixed scheme://host/ prefix: only the path is attacker data
                        val = recv if i == -1 else (args[i] if i < len(args) else None)
                        if val is None or isinstance(val, MapV):
                            continue
                        if hasattr(val, "items") and not isinstance(val, MapV) and hasattr(val, "dirty") and _cwe == 89:
                            items = getattr(val, "items", [])
                            val = items[0] if items else val
                        hit = _X.ext_hit("ruby", _cwe, name, i, args, _tainted_string, node) if ext else (_tainted_string(val) if _cwe in (79, 601, 501) else classify(val) == "tainted")
                        if hit:
                            record(node, fr, f"argument {i + 1} of {recv_text + '.' if recv_text else ''}{name}" if i >= 0 else f"receiver of {name}")
                            return

                def store(node, base_text, key, value, fr, _cwe=cwe):
                    if _cwe == 942:
                        if _X.acao_store(key, value, _tainted_string, node):
                            record(node, fr, "Access-Control-Allow-Origin set from the request")
                        return
                    if _cwe == 501 and base_text.endswith("session") and (_tainted_string(key) or _tainted_string(value)):
                        record(node, fr, "write to the session")

                for cls in rf.classes:
                    if budget.over(budget_seconds):
                        break
                    action_cls = _is_controller(cls)
                    entries: list[tuple[Node, bool, bool]] = []
                    for ms in cls.methods.values():
                        for m in ms:
                            entries.append((m, action_cls and m.start_byte not in cls.private, False))
                    for ms in cls.smethods.values():
                        for m in ms:
                            entries.append((m, False, True))
                    skip_lib = not library or cwe in _lib.SKIP_CWES or _lib.skipped_path(_rel_parts(path, repo))
                    for m, is_action, singleton in entries:
                        route_now[0] = (not is_action) and not skip_lib and (singleton or m.start_byte not in cls.private)
                        interp = RbInterp(index, {cwe}, sink_hook=sink, store_hook=store)
                        interp.MAX_STEPS = max(interp.MAX_STEPS, len(data) // 4)  # big files get a proportionally bigger budget
                        fr = RFrame(rf, cls, coverage.safe_this(interp, cls, rf) if not singleton else Obj(cls=cls, type_name=cls.name), 0, singleton=singleton)
                        try:
                            interp.bind_entry(m, fr, "lib" if route_now[0] else is_action)
                            body = m.child_by_field_name("body")
                            if body is not None:
                                if body.type in ("body_statement", "begin"):
                                    interp.exec(body, fr)
                                else:
                                    interp.eval(body, fr)
                        except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                            coverage.note(path)
                            continue
                    for blk in _sinatra_blocks(cls):
                        interp = RbInterp(index, {cwe}, sink_hook=sink, store_hook=store)
                        interp.MAX_STEPS = max(interp.MAX_STEPS, len(data) // 4)  # big files get a proportionally bigger budget
                        fr = RFrame(rf, cls, Obj(cls=cls, type_name=cls.name), 0)
                        try:
                            _exec_block(interp, blk, fr)
                        except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                            coverage.note(path)
                            continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))


def _cond_taint(v) -> bool:
    return v is not None and classify(v) == "tainted"


def validate_rb_finding(repo: Path, rel_path: str, start: tuple[int, int], end: tuple[int, int], cwes: set[int], index: RbIndex | None = None):
    from aletheore.flow.validate import Verdict, _offset

    index = index or RbIndex(repo)
    rf = index.file(repo / rel_path)
    if rf is None:
        return Verdict("unknown", "file unreadable")
    s, e = _offset(rf.src, *start), _offset(rf.src, *end)
    node = rf.root.descendant_for_byte_range(s, e)
    types = ("call", "subshell")
    top = node
    while top is not None and top.type not in types:
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
            if cur.type in types:
                top = cur
            down.extend(cur.children)
    if top is None:
        return Verdict("unknown", "no call at the finding location")
    cands: list[Node] = []
    stack = [top]
    while stack:
        cur = stack.pop()
        if cur.type in types:
            cands.append(cur)
        stack.extend(cur.children)

    def positional(args_n: Node | None) -> list[Node]:
        return [a for a in (args_n.named_children if args_n is not None else []) if a.type not in ("pair", "comment", "splat_argument", "block_argument", "hash_splat_argument")]

    def describe(call: Node):
        if call.type == "subshell":
            return [call], {0}, 78 in cwes
        recv_n, args_n = call.child_by_field_name("receiver"), call.child_by_field_name("arguments")
        name = _t(call.child_by_field_name("method"))
        recv = _t(recv_n)
        ex = positional(args_n)
        total = len(args_n.named_children) if args_n is not None else 0
        first_const = bool(ex) and ex[0].type == "string" and not any(c.type == "interpolation" for c in ex[0].children)
        pos: set[int] = set()
        for cwe in cwes:
            p = sink_args(cwe, name, recv, total, first_const)
            if p:
                pos.update(p)
        if -1 in pos and recv_n is not None:
            ex = [recv_n] + ex
            pos = {0 if x == -1 else x + 1 for x in pos}
        return ex, pos, True

    call, exprs, positions = top, [], set()
    probes = []
    for cand in cands:
        e_, p_, ok = describe(cand)
        if p_ and ok:
            if not probes:
                call = cand
            probes += [e_[i] for i in sorted(p_) if i < len(e_)]
    if not probes and not via_descent:
        exprs, positions, _ = describe(top)
        probes = [exprs[i] for i in sorted(positions) if i < len(exprs)] if positions else exprs
    if not probes and via_descent:
        return Verdict("unknown", "finding spans several statements and none is a known sink")
    if not probes:
        return Verdict("unknown", "sink has no arguments to judge")
    scope = call
    while scope is not None and scope.type not in ("method", "singleton_method"):
        if scope.type in ("do_block", "block") and scope.parent is not None and scope.parent.type == "call" and _t(scope.parent.child_by_field_name("method")) in SINATRA_VERBS:
            break
        scope = scope.parent
    if scope is None:
        return Verdict("unknown", "not inside a method")
    cls = None
    for c in rf.classes:
        if c.name != "<main>" and c.node.start_byte <= scope.start_byte and scope.end_byte <= c.node.end_byte:
            if cls is None or c.node.start_byte >= cls.node.start_byte:
                cls = c
    cls = cls or rf.main
    if cls is None:
        return Verdict("unknown", "no enclosing class")
    interp = RbInterp(index, cwes)
    fr = RFrame(rf, cls, Obj(cls=cls, type_name=cls.name), 0, singleton=scope.type == "singleton_method")
    for p in probes:
        interp.probe_keys.add((id(rf), p.start_byte, p.end_byte))
    try:
        if scope.type in ("do_block", "block"):
            _exec_block(interp, scope, fr)
        else:
            interp.bind_entry(scope, fr, _is_controller(cls) and scope.start_byte not in cls.private)
            body = scope.child_by_field_name("body")
            if body is not None:
                if body.type in ("body_statement", "begin"):
                    interp.exec(body, fr)
                else:
                    interp.eval(body, fr)
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
