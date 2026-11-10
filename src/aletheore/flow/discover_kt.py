"""Kotlin discover and validate modes (see discover_php.py for the shared design)."""

from __future__ import annotations

import time
from pathlib import Path

from tree_sitter import Node

from aletheore.flow.kt_index import KtClass, KtIndex, annotations
from aletheore.flow.kt_interp import BudgetExceeded, KFrame, KtInterp
from aletheore.flow.kt_sinks import FILE_USE, CONTROLLER_ANNOTATIONS, CWE_NAMES_KT, DISCOVER_CWES_KT, MAPPING_ANNOTATIONS, SEVERITY_KT, sink_args
from aletheore.flow import coverage
from aletheore.flow import extra_sinks as _X
from aletheore.flow.prefilter import classes_present
from aletheore.flow.values import host_is_fixed, path_prefix_safe, Choice, Const, Obj, Tainted, classify
from aletheore.scanner.detect import IGNORED_DIRS

_SINK_WORDS = {
    89: (b"executeQuery", b"executeUpdate", b"execute", b"prepareStatement", b"createQuery", b"createNativeQuery", b"queryFor", b".query(", b".update(", b"rawQuery", b"execSQL", b"createSQLQuery", b".exec("),
    78: (b"ProcessBuilder", b"Runtime"),
    22: (b"File(", b"FileInputStream", b"FileOutputStream", b"FileReader", b"FileWriter", b"Paths.", b"Path.of", b"Files.", b".resolve(", b"RandomAccessFile"),
    79: (b"getWriter", b"writer", b"respondText", b"respondHtml", b".print"),
    601: (b"sendRedirect", b"respondRedirect", b"RedirectView", b"redirect", b"setLocation"),
    918: (b"URL(", b"URI(", b"URI.create", b"RestTemplate", b"restTemplate", b"HttpGet", b"WebClient", b".url(", b".uri("),
    502: (b"ObjectInputStream", b"fromXML", b"Yaml"),
    94: (b"eval", b"evaluate", b"Class.forName", b"parseExpression", b"loadClass"),
    643: (b"XPath", b"xpath"),
    90: (b"search(",),
    501: (b"setAttribute", b"session"),
}
for _c in _X.rule_classes("kotlin"):  # the shared catalogue's method names
    _SINK_WORDS[_c] = tuple(_SINK_WORDS.get(_c, ())) + _X.prefilter_words("kotlin", _c)
_ENTRY_WORDS = (b"@RequestParam", b"@PathVariable", b"@RequestBody", b"@GetMapping", b"@PostMapping", b"@RequestMapping", b"@RestController", b"@Controller", b"HttpServletRequest",
                b"ApplicationCall", b"call.", b"@GET", b"@POST", b"ServerRequest", b"@QueryParam", b"@Path")
_EXTRA_SKIP = {"build", "out", "target", ".gradle", ".worktrees", ".repowise", "node_modules", "test", "tests", "androidTest", "generated"}
MAX_FILES = 6000


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _kt_files(repo: Path) -> list[Path]:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    out: list[Path] = []
    for p in repo.rglob("*.kt"):
        if skip.intersection(p.relative_to(repo).parts):
            continue
        out.append(p)
        if len(out) >= MAX_FILES:
            break
    return out


def has_kt(repo: Path) -> bool:
    return bool(_kt_files(repo))


def _tainted_string(v) -> bool:
    def one(t) -> bool:
        return isinstance(t, Tainted) and (t.why in ("derived", "param") or t.why.startswith("request"))
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts))


def _is_action(cls: KtClass, fn: Node) -> bool:
    return bool(annotations(fn) & MAPPING_ANNOTATIONS) or bool(cls.annots & CONTROLLER_ANNOTATIONS)


def discover_kt(repo: Path, index: KtIndex | None = None, budget_seconds: float = 150.0) -> list[dict]:
    index = index or KtIndex(repo)
    repo_r = repo.resolve()
    started = time.monotonic()
    found: dict[tuple, dict] = {}
    files = _kt_files(repo)
    present = classes_present(files, _SINK_WORDS)
    core_budget = budget_seconds
    # Core classes first, with the whole budget; the added classes (template, regex, reflection, ...) after, in a
    # bounded extra slice, so a large repository never loses a core finding to the time the added classes take.
    for phase in (0, 1):
        budget_seconds = core_budget if phase == 0 else core_budget * 1.4
        for path in files:
            if time.monotonic() - started > budget_seconds:
                break
            try:
                data = path.read_bytes()
            except OSError:
                continue
            entry_file = any(w in data for w in _ENTRY_WORDS)
            cwes = [c for c in DISCOVER_CWES_KT if c in present and _X.local_ok(c, data, _SINK_WORDS, "kotlin")] if entry_file else [c for c in DISCOVER_CWES_KT if any(w in data for w in _SINK_WORDS[c])]
            cwes = [c for c in cwes if _X.added("kotlin", c) == (phase == 1)]
            if not cwes:
                continue
            kf = index.file(path)
            if kf is None:
                continue
            for cwe in cwes:
                slug, label = CWE_NAMES_KT[cwe]

                def record(node, fr, what, _cwe=cwe, _slug=slug, _label=label):
                    rel = str(fr.file.path.relative_to(repo_r)) if fr.file.path.is_relative_to(repo_r) else str(fr.file.path)
                    line = node.start_point[0] + 1
                    found.setdefault((rel, line, _cwe), {
                        "tool": "aletheore-flow", "rule_id": f"flow.kt.{_slug}", "severity": SEVERITY_KT[_cwe], "type": "vulnerability",
                        "path": rel, "line": line, "cwe": [f"CWE-{_cwe}"],
                        "message": f"Request data reaches a {_label} sink without being validated or encoded for it ({what}). "
                                   "Found by following the value through the code, including calls into other functions and classes.",
                    })

                def sink(node, name, recv_text, args, recv, fr, _cwe=cwe):
                    if _cwe == 501 and name == "setAttribute" and "session" in recv_text.lower() and len(args) >= 2:
                        if _tainted_string(args[0]) or _tainted_string(args[1]):
                            record(node, fr, "write to the session")
                        return
                    if _cwe == 22 and name in FILE_USE and recv_text and not any(h in recv_text.lower() for h in ("url", "uri", "request", "http")) \
                            and classify(recv) == "tainted":
                        record(node, fr, f"{name} on a path built from request data")
                        return
                    positions = sink_args(_cwe, name, recv_text, len(args))
                    ext = False
                    if not positions:  # the shared catalogue, for a string built from request data
                        positions = _X.sink_args("kotlin", _cwe, name, recv_text, len(args), args)
                        ext = bool(positions)
                        if not positions:
                            return
                    for i in positions:
                        if _cwe in (918, 601) and 0 <= i < len(args) and (host_is_fixed(args[i]) or path_prefix_safe(args[i])):
                            continue  # a fixed scheme://host/ prefix: only the path is attacker data
                        if i >= len(args):
                            continue
                        hit = _X.ext_hit("kotlin", _cwe, name, i, args, _tainted_string, node) if ext else (_tainted_string(args[i]) if _cwe in (79, 601) else classify(args[i]) == "tainted")
                        if hit:
                            record(node, fr, f"argument {i + 1} of {recv_text + '.' if recv_text else ''}{name}")
                            return

                def store(node, base_text, key, value, fr, _cwe=cwe):
                    if _cwe == 942:
                        if _X.acao_store(key, value, _tainted_string, node):
                            record(node, fr, "Access-Control-Allow-Origin set from the request")
                        return
                    if _cwe == 501 and base_text.lower().endswith("session") and (_tainted_string(key) or _tainted_string(value)):
                        record(node, fr, "write to the session")

                if b"call." in data and cwe in DISCOVER_CWES_KT:
                    for lam in _ktor_handlers(kf.root):
                        interp = KtInterp(index, {cwe}, sink_hook=sink, store_hook=store)
                        interp.MAX_STEPS = max(interp.MAX_STEPS, len(data) // 4)  # big files get a proportionally bigger budget
                        fr = KFrame(kf, kf.main, None, 0)
                        try:
                            interp._run_lambda(lam, [], fr)
                        except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                            coverage.note(path)
                            continue
                for cls in kf.classes:
                    if time.monotonic() - started > budget_seconds:
                        break
                    for fns in cls.funcs.values():
                        for fn in fns:
                            interp = KtInterp(index, {cwe}, sink_hook=sink, store_hook=store)
                            interp.MAX_STEPS = max(interp.MAX_STEPS, len(data) // 4)  # big files get a proportionally bigger budget
                            fr = KFrame(kf, cls, coverage.safe_this(interp, cls, kf) if cls.name != "<main>" else None, 0)
                            try:
                                interp.bind_entry(fn, fr, _is_action(cls, fn))
                                body = next((c for c in fn.children if c.type == "function_body"), None)
                                inner = next((c for c in (body.named_children if body is not None else [])), None)
                                if inner is not None and inner.type != "block":
                                    interp.eval(inner, fr)
                                else:
                                    interp.exec(inner, fr)
                            except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                                coverage.note(path)
                                continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))


_KTOR_VERBS = {"get", "post", "put", "delete", "patch", "head", "options", "webSocket"}


def _ktor_handlers(root: Node):
    stack = [root]
    while stack:
        cur = stack.pop()
        head = cur.children[0] if cur.type == "call_expression" and cur.children else None
        if head is not None and head.type == "call_expression" and head.children:
            head = head.children[0]
        if head is not None and head.type == "identifier" and _t(head) in _KTOR_VERBS:
            al = next((c for c in cur.children if c.type == "annotated_lambda"), None)
            lam = next((c for c in (al.named_children if al is not None else []) if c.type == "lambda_literal"), None)
            if lam is not None:
                yield lam
        stack.extend(cur.children)


def validate_kt_finding(repo: Path, rel_path: str, start: tuple[int, int], end: tuple[int, int], cwes: set[int], index: KtIndex | None = None):
    from aletheore.flow.validate import Verdict, _offset

    index = index or KtIndex(repo)
    kf = index.file(repo / rel_path)
    if kf is None:
        return Verdict("unknown", "file unreadable")
    s, e = _offset(kf.src, *start), _offset(kf.src, *end)
    node = kf.root.descendant_for_byte_range(s, e)
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
        callee = call.children[0] if call.children else None
        va = next((c for c in call.children if c.type == "value_arguments"), None)
        ex = []
        for a in (va.named_children if va is not None else []):
            kids = [c for c in a.named_children if c.type != "annotation"]
            if a.type == "value_argument" and kids:
                ex.append(kids[-1])
        if callee is not None and callee.type == "navigation_expression":
            name = _t(next((c for c in reversed(callee.children) if c.type == "identifier"), None))
            recv = _t(callee.children[0])
        else:
            name, recv = _t(callee), ""
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
    while scope is not None and scope.type != "function_declaration":
        scope = scope.parent
    if scope is None:
        return Verdict("unknown", "not inside a function")
    cls = None
    for c in kf.classes:
        if c.name != "<main>" and c.node.start_byte <= scope.start_byte and scope.end_byte <= c.node.end_byte:
            if cls is None or c.node.start_byte >= cls.node.start_byte:
                cls = c
    cls = cls or kf.main
    if cls is None:
        return Verdict("unknown", "no enclosing class")
    interp = KtInterp(index, cwes)
    fr = KFrame(kf, cls, Obj(cls=cls, type_name=cls.name) if cls.name != "<main>" else None, 0)
    for p in probes:
        interp.probe_keys.add((id(kf), p.start_byte, p.end_byte))
    try:
        interp.bind_entry(scope, fr, _is_action(cls, scope))
        body = next((c for c in scope.children if c.type == "function_body"), None)
        inner = next((c for c in (body.named_children if body is not None else [])), None)
        if inner is not None and inner.type != "block":
            interp.eval(inner, fr)
        else:
            interp.exec(inner, fr)
    except (BudgetExceeded, RecursionError):
        return Verdict("unknown", "analysis budget exceeded")
    kinds = []
    for p in probes:
        key = (id(kf), p.start_byte, p.end_byte)
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
