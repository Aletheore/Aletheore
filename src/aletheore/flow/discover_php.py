"""PHP discover and validate modes.

Discover: every file's top-level code is run once (scripts read superglobals directly), as is every
function and method, one weakness class at a time. Validate: for a scanner finding, the same interpreter
decides whether the value reaching the sink is provably safe.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from tree_sitter import Node

from aletheore.flow.php_index import EXTS, PhpFile, PhpIndex
from aletheore.flow import coverage
from aletheore.flow import extra_sinks as _X
from aletheore.flow.prefilter import classes_present
from aletheore.flow.php_interp import BudgetExceeded, PFrame, PhpInterp
from aletheore.flow.php_sinks import (
    CWE_NAMES_PHP, DISCOVER_CWES_PHP, INCLUDE_KINDS, SEVERITY_PHP, sink_args,
)
from aletheore.flow.values import host_is_fixed, path_prefix_safe, Choice, MapV, Obj, Tainted, classify
from aletheore.scanner.detect import IGNORED_DIRS

_SINK_WORDS = {
    89: (b"query", b"mysql", b"pg_", b"sqlite", b"prepare", b"exec", b"->get_", b"odbc", b"oci_", b"Raw(", b"DB::"),
    78: (b"exec", b"system", b"passthru", b"popen", b"proc_open", b"shell_exec", b"`"),
    22: (b"fopen", b"file_get_contents", b"file_put_contents", b"readfile", b"unlink", b"move_uploaded_file", b"copy(", b"rename(", b"mkdir", b"scandir", b"opendir", b"glob(", b"file(", b"touch(", b"highlight_file"),
    79: (b"echo", b"print", b"die(", b"exit(", b"<?=", b"response", b"Response"),
    98: (b"include", b"require"),
    94: (b"eval", b"assert", b"create_function", b"call_user_func"),
    502: (b"unserialize", b"yaml_parse"),
    918: (b"curl_", b"file_get_contents", b"fsockopen", b"get_headers", b"fopen"),
    601: (b"header(",),
    90: (b"ldap_",),
    643: (b"xpath", b"XPath"),
    501: (b"_SESSION",),
}
for _c in _X.rule_classes("php"):  # the shared catalogue's method names
    _SINK_WORDS[_c] = tuple(_SINK_WORDS.get(_c, ())) + _X.prefilter_words("php", _c)
_ENTRY_WORDS = (b"$_GET", b"$_POST", b"$_REQUEST", b"$_COOKIE", b"$_FILES", b"$_SERVER", b"$argv", b"php://input", b"getallheaders")
_EXTRA_SKIP = {"vendor", ".worktrees", ".repowise", "node_modules", "external", "tests", "test", "Tests", "docs", "migrations"}
MAX_FILES = 6000


def _php_files(repo: Path) -> list[Path]:
    skip = set(IGNORED_DIRS) | _EXTRA_SKIP
    out: list[Path] = []
    for p in repo.rglob("*"):
        if p.suffix not in EXTS or skip.intersection(p.relative_to(repo).parts):
            continue
        out.append(p)
        if len(out) >= MAX_FILES:
            break
    return out


def has_php(repo: Path) -> bool:
    return bool(_php_files(repo))


def _direct(v) -> bool:
    return isinstance(v, Tainted) or (isinstance(v, Choice) and any(isinstance(a, Tainted) for a in v.alts))


def tainted_string(v) -> bool:
    def one(t) -> bool:
        return isinstance(t, Tainted) and (t.why in ("derived", "argv") or t.why.startswith("request"))
    return one(v) or (isinstance(v, Choice) and any(one(a) for a in v.alts))


def _functions(pf: PhpFile):
    for fn in pf.functions.values():
        yield None, fn
    for cls in pf.classes.values():
        for m in cls.methods.values():
            yield cls, m


def discover_php(repo: Path, index: PhpIndex | None = None, budget_seconds: float = 150.0) -> list[dict]:
    index = index or PhpIndex(repo)
    repo_r = repo.resolve()
    started = time.monotonic()
    found: dict[tuple, dict] = {}
    files = _php_files(repo)
    json_roots = _json_api_roots(files)
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
            cwes = [c for c in DISCOVER_CWES_PHP if c in present and _X.local_ok(c, data, _SINK_WORDS, "php")] if entry_file else [c for c in DISCOVER_CWES_PHP if any(w in data for w in _SINK_WORDS[c])]
            cwes = [c for c in cwes if _X.added("php", c) == (phase == 1)]
            if not cwes:
                continue
            pf = index.file(path)
            if pf is None:
                continue
            for cwe in cwes:
                slug, label = CWE_NAMES_PHP[cwe]

                def record(node, fr, what, _cwe=cwe, _slug=slug, _label=label):
                    rel = str(fr.file.path.relative_to(repo_r)) if fr.file.path.is_relative_to(repo_r) else str(fr.file.path)
                    line = node.start_point[0] + 1
                    found.setdefault((rel, line, _cwe), {
                        "tool": "aletheore-flow", "rule_id": f"flow.php.{_slug}", "severity": SEVERITY_PHP[_cwe], "type": "vulnerability",
                        "path": rel, "line": line, "cwe": [f"CWE-{_cwe}"],
                        "message": f"Request data reaches a {_label} sink without being validated or encoded for it ({what}). "
                                   "Found by following the value through the code, including calls into other functions and files.",
                    })

                def sink(node, name, recv_text, args, recv, fr, _cwe=cwe):
                    text = node.text.decode("utf-8", "replace")
                    if _cwe == 79:
                        if json_roots and _json_class_file(fr.file.path, fr.file.src, json_roots):
                            return  # a class of an application whose entry script declares `Content-Type: application/json`
                        if name == "echo" and any(tainted_string(a) for a in args):
                            record(node, fr, "output")
                        elif name != "echo":
                            positions = sink_args(79, name, recv_text, len(args))
                            if positions and any(i < len(args) and tainted_string(args[i]) for i in positions):
                                record(node, fr, f"argument 1 of {name}")
                        return
                    if _cwe == 98:
                        if name == "__include__" and args and classify(args[0]) == "tainted":
                            record(node, fr, "path of an include")
                        return
                    rtype = recv.type_name if isinstance(recv, Obj) else None
                    first_const = "CURLOPT_URL" if name == "curl_setopt" and "CURLOPT_URL" in text else None
                    positions = sink_args(_cwe, name, recv_text, len(args), rtype, first_const)
                    ext = False
                    if not positions:  # the shared catalogue, for a string built from request data
                        positions = _X.sink_args("php", _cwe, recv_text if name == "<new>" else name, "" if name == "<new>" else recv_text, len(args), args)
                        ext = bool(positions)
                        if not positions:
                            return
                    if _cwe == 89 and name.lower() == "execute" and args and isinstance(args[0], MapV):
                        return  # PDOStatement::execute([...]) binds parameters; the SQL text was given to prepare()
                    if _cwe == 601 and "location" not in text.lower():
                        return
                    if _cwe == 918 and _local_path_prefix(node):
                        return  # "./x/" . $id is a local file read (path traversal at worst), not a request to another host
                    for i in positions:
                        if _cwe in (918, 601) and 0 <= i < len(args) and (host_is_fixed(args[i]) or path_prefix_safe(args[i])):
                            continue  # a fixed scheme://host/ prefix: only the path is attacker data
                        if i >= len(args):
                            continue
                        hit = _X.ext_hit("php", _cwe, recv_text if name == "<new>" else name, i, args, tainted_string, node) if ext else (tainted_string(args[i]) if _cwe == 601 else classify(args[i]) == "tainted")
                        if hit:
                            record(node, fr, f"argument {i + 1} of {name}")
                            return

                def store(node, base_text, key, value, fr, _cwe=cwe):
                    if _cwe == 942:
                        if _X.acao_store(key, value, tainted_string, node):
                            record(node, fr, "Access-Control-Allow-Origin set from the request")
                        return
                    if _cwe == 501 and base_text.strip() == "$_SESSION" and (tainted_string(key) or tainted_string(value)):
                        record(node, fr, "write to the session")

                runs = [(None, None)] + list(_functions(pf))
                for cls, fn in runs:
                    if time.monotonic() - started > budget_seconds:
                        break
                    interp = PhpInterp(index, {cwe}, sink_hook=sink, store_hook=store)
                    interp.MAX_STEPS = max(interp.MAX_STEPS, len(data) // 4)  # big files get a proportionally bigger budget
                    this = interp.make_this(cls, pf) if cls is not None else None
                    fr = PFrame(pf, cls, this, 0)
                    try:
                        if fn is None:
                            interp.exec(pf.root, fr)
                        else:
                            interp.bind_entry(fn, fr)
                            interp.exec(fn.child_by_field_name("body"), fr)
                    except Exception:  # a construct the engine mishandles must cost one entry point, never the scan
                        coverage.note(path)
                        continue
    return sorted(found.values(), key=lambda f: (f["path"], f["line"], f["rule_id"]))


# ---------------------------------------------------------------------------- validate mode
_CALLS = ("function_call_expression", "member_call_expression", "nullsafe_member_call_expression", "scoped_call_expression")


def _leading_literal(n: Node | None) -> str | None:
    """Text the expression starts with when it begins with a literal ("./{$id}/x" -> "./"), else None."""
    while n is not None:
        if n.type == "encapsed_string":
            first = next((c for c in n.children if c.type in ("string_content", "string_value", "escape_sequence", "variable_name", "member_access_expression") or c.is_named), None)
            return first.text.decode("utf-8", "replace") if first is not None and first.type in ("string_content", "string_value") else None
        if n.type in ("string", "string_value", "string_content"):
            return n.text.decode("utf-8", "replace").strip("'\"")
        if n.type == "binary_expression" and n.child_by_field_name("operator") is not None and n.child_by_field_name("operator").text == b".":
            n = n.child_by_field_name("left")
            continue
        if n.type == "parenthesized_expression" and n.named_children:
            n = n.named_children[0]
            continue
        return None
    return None


def _local_path_prefix(call: Node) -> bool:
    args_n = call.child_by_field_name("arguments")
    first = args_n.named_children[0] if args_n is not None and args_n.named_children else None
    arg = first.named_children[-1] if first is not None and first.named_children else None
    lit = _leading_literal(arg)
    return bool(lit) and lit.startswith(("./", "../", "/")) and "://" not in lit and not lit.startswith("//")


_JSON_HEADER = re.compile(rb"header\s*\(\s*['\"]Content-Type:\s*application/json", re.I)


def _json_class_file(path: Path, src: bytes, roots: set[Path]) -> bool:
    return re.search(rb"\bclass\s+\w+", src) is not None and any(r in path.resolve().parents for r in roots)


def _json_api_roots(files: list[Path]) -> set[Path]:
    """Directories of applications that answer every request as JSON: an entry script (usually public/index.php) sets the header once."""
    roots: set[Path] = set()
    for p in files:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if _JSON_HEADER.search(data):
            d = p.resolve().parent
            roots.add(d.parent if d.name in ("public", "web", "www", "html", "htdocs") else d)
    return roots


def _call_name(call: Node) -> tuple[str, str]:
    t = call.type
    if t == "function_call_expression":
        return (call.child_by_field_name("function").text.decode("utf-8", "replace").split("\\")[-1] if call.child_by_field_name("function") is not None else ""), ""
    name = call.child_by_field_name("name")
    recv = call.child_by_field_name("object") or call.child_by_field_name("scope")
    return (name.text.decode("utf-8", "replace") if name is not None else ""), (recv.text.decode("utf-8", "replace") if recv is not None else "")


def validate_php_finding(repo: Path, rel_path: str, start: tuple[int, int], end: tuple[int, int], cwes: set[int], index: PhpIndex | None = None):
    from aletheore.flow.validate import Verdict, _offset

    index = index or PhpIndex(repo)
    pf = index.file(repo / rel_path)
    if pf is None:
        return Verdict("unknown", "file unreadable")
    s, e = _offset(pf.src, *start), _offset(pf.src, *end)
    node = pf.root.descendant_for_byte_range(s, e)
    if node is None:
        return Verdict("unknown", "no sink expression found")
    call = node
    while call is not None and call.type not in _CALLS and call.type not in ("echo_statement", "shell_command_expression") and call.type not in INCLUDE_KINDS:
        call = call.parent
    if call is None:
        return Verdict("unknown", "no call at the finding location")
    probes: list[Node] = []
    if call.type == "echo_statement":
        probes = list(call.named_children)
    elif call.type in _CALLS:
        args_n = call.child_by_field_name("arguments")
        exprs = [a.named_children[-1] for a in (args_n.named_children if args_n is not None else []) if a.named_children]
        name, recv = _call_name(call)
        positions: set[int] = set()
        for cwe in cwes:
            pos = sink_args(cwe, name, recv, len(exprs))
            if pos:
                positions.update(pos)
        probes = [exprs[i] for i in sorted(positions) if i < len(exprs)] if positions else exprs
    else:
        probes = [c for c in call.named_children][:1]
    if not probes:
        return Verdict("unknown", "sink has no arguments to judge")
    scope = call
    while scope is not None and scope.type not in ("function_definition", "method_declaration", "program"):
        scope = scope.parent
    if scope is None:
        return Verdict("unknown", "not inside a function or script")
    cls = None
    if scope.type == "method_declaration":
        for c in pf.classes.values():
            if c.node.start_byte <= scope.start_byte and scope.end_byte <= c.node.end_byte:
                cls = c
    interp = PhpInterp(index, cwes)
    fr = PFrame(pf, cls, Obj(cls=cls, type_name=cls.name) if cls else None, 0)
    for p in probes:
        interp.probe_keys.add((id(pf), p.start_byte, p.end_byte))
    try:
        if scope.type == "program":
            interp.exec(pf.root, fr)
        else:
            interp.bind_entry(scope, fr)
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
