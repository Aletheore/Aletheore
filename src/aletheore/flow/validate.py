"""Decide whether a scanner finding's sink value is provably safe.

`validate_finding` takes the location Semgrep reported (the sink or the sink argument), finds the
enclosing method, runs the abstract interpreter over it, and classifies the value(s) that reach the
sink. The result is conservative: only "safe" and "unreachable" justify dropping a finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node

from aletheore.flow.java_index import ClassInfo, JavaIndex
from aletheore.flow.java_interp import BudgetExceeded, Frame, Interp
from aletheore.flow.java_sinks import probe_positions
from aletheore.flow.values import Obj, classify

# CWE classes where "no attacker data reaches the sink" really does mean "safe": path traversal,
# OS command, XSS, SQL, LDAP and XPath injection, trust-boundary violation. Deliberately narrow.
# Weaknesses that are about a constant configuration (XXE feature flags, weak crypto parameters,
# insecure cookie flags) must never be judged by data flow: for those the constant IS the flaw, and
# the WebGoat audit found a real XXE misconfiguration wrongly judged safe before 611 was removed.
TAINT_CWES = {22, 23, 36, 73, 78, 79, 89, 90, 501, 643}


@dataclass(frozen=True)
class Verdict:
    kind: str  # "safe" | "unreachable" | "tainted" | "unknown" | "unsupported"
    detail: str = ""

    @property
    def droppable(self) -> bool:
        return self.kind in ("safe", "unreachable")


def _offset(src: bytes, line: int, col: int) -> int:
    pos = 0
    for _ in range(line - 1):
        nl = src.find(b"\n", pos)
        if nl < 0:
            return len(src)
        pos = nl + 1
    return min(pos + max(col - 1, 0), len(src))


def _enclosing(node: Node, types: tuple[str, ...]) -> Node | None:
    cur = node.parent
    while cur is not None:
        if cur.type in types:
            return cur
        cur = cur.parent
    return None


def _class_of(method: Node, jf) -> ClassInfo | None:
    best = None
    for c in jf.classes:
        if c.node.start_byte <= method.start_byte and method.end_byte <= c.node.end_byte:
            if best is None or c.node.start_byte >= best.node.start_byte:
                best = c
    return best


_CALLS = ("method_invocation", "object_creation_expression")


def _outer_calls(node: Node) -> list[Node]:
    """The outermost call expressions inside `node` (a statement or declaration)."""
    out: list[Node] = []

    def walk(n: Node) -> None:
        if n.type in _CALLS:
            out.append(n)
            return
        for c in n.named_children:
            walk(c)

    walk(node)
    return out


def _call_probes(call: Node, cwes: set[int] | None = None) -> list[Node]:
    args = call.child_by_field_name("arguments")
    named = list(args.named_children) if args is not None else []
    if named and cwes and call.type == "method_invocation":
        name = call.child_by_field_name("name")
        positions = probe_positions(cwes, name.text.decode("utf-8", "replace") if name is not None else "", len(named))
        if positions:
            return [named[i] for i in positions]
    return named if named else [call]


def _probe_nodes(root: Node, s: int, e: int, cwes: set[int] | None = None) -> list[Node]:
    node = root.descendant_for_byte_range(s, e)
    if node is None:
        return []
    if node.type == "assignment_expression":
        right = node.child_by_field_name("right")
        node = right if right is not None else node
    # A whole statement, declaration, block or resource (some registry rules report these).
    if (node.type in ("local_variable_declaration", "variable_declarator", "resource", "block")
            or node.type.endswith("_statement")):
        probes: list[Node] = []
        for call in _outer_calls(node):
            probes.extend(_call_probes(call, cwes))
        return probes or [node]
    # Climb to the whole call when the range covers a call's callee or a sub-part of it.
    call = node
    while call is not None and call.type not in _CALLS:
        if call.parent is not None and call.parent.type == "argument_list":
            return [call]  # the range is exactly an argument expression
        call = call.parent
    if call is None:
        return [node]
    if (call.start_byte, call.end_byte) != (s, e) and node is not call:
        arg = node
        while arg is not None and arg.parent is not None and arg.parent.type != "argument_list":
            arg = arg.parent
        if arg is not None and arg.parent is not None and arg.parent.type == "argument_list":
            return [arg]
    return _call_probes(call, cwes)


def validate_finding(
    repo: Path,
    rel_path: str,
    start: tuple[int, int],
    end: tuple[int, int],
    cwes: set[int],
    index: JavaIndex | None = None,
) -> Verdict:
    if not rel_path.endswith(".java"):
        return Verdict("unsupported", "not a Java file")
    if not (cwes & TAINT_CWES):
        return Verdict("unsupported", "finding class does not depend on data flow")
    index = index or JavaIndex(repo)
    jf = index.file(repo / rel_path)
    if jf is None:
        return Verdict("unknown", "file unreadable")
    s, e = _offset(jf.src, *start), _offset(jf.src, *end)
    probes = _probe_nodes(jf.root, s, e, cwes)
    if not probes:
        return Verdict("unknown", "no sink expression found")
    method = _enclosing(probes[0], ("method_declaration", "constructor_declaration"))
    if method is None:
        return Verdict("unknown", "not inside a method")
    cls = _class_of(method, jf)
    if cls is None:
        return Verdict("unknown", "enclosing class not found")
    body = method.child_by_field_name("body")
    if body is None:
        return Verdict("unknown", "no method body")
    interp = Interp(index, cwes)
    fr = Frame(file=jf, cls=cls, this_obj=Obj(cls=cls, type_name=cls.name), depth=0)
    interp.bind_entry_params(method, fr)
    for p in probes:
        interp.probe_keys.add((id(jf), p.start_byte, p.end_byte))
    try:
        interp.exec(body, fr)
    except BudgetExceeded:
        return Verdict("unknown", "analysis budget exceeded")
    except RecursionError:
        return Verdict("unknown", "recursion limit")
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
