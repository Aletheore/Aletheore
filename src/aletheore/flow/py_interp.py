"""An abstract interpreter for Python function bodies, built on tree-sitter.

Same stance as the Java interpreter: it exists to prove a value safe or to follow request data to a
sink. What it cannot model becomes UNKNOWN (which keeps a finding), a container touched under a
condition that may not hold stops being trusted, and calls into the repository are followed across
files through the import index.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field

from tree_sitter import Node

from aletheore.flow.py_index import PyClass, PyFile, PyIndex
from aletheore.flow.py_sinks import REQUEST_ATTRS_SAFE, SAFE_RESULT_CALLS, SANITIZERS
from aletheore.flow import guards as G
from aletheore.flow.values import (
    ClosureV, add_prefix, SAFE, UNKNOWN, Choice, Const, ListV, MapV, Obj, Tainted, Val, classify, join, join_all,
)

BENIGN_METHODS = {"startswith", "endswith", "isdigit", "isalpha", "isalnum", "isnumeric", "isdecimal", "isspace", "islower",
                  "isupper", "isidentifier", "count", "find", "rfind", "index", "rindex", "__len__"}
PROPAGATING_BUILTINS = {"str", "repr", "bytes", "bytearray", "format", "unicode", "ascii", "sorted", "reversed", "iter",
                        "next", "min", "max", "sum", "tuple", "frozenset", "memoryview", "copy", "deepcopy", "dumps"}
PURE_MODULES = {"json", "math", "re", "string", "base64", "hashlib", "uuid", "datetime", "time", "random", "secrets",
                "textwrap", "itertools", "functools", "operator", "binascii", "urllib.parse"}
PURE_FUNCS = {("os.path", "join"), ("os.path", "basename"), ("os.path", "dirname"), ("os.path", "abspath"),
              ("os.path", "normpath"), ("os.path", "splitext"), ("path", "join"), ("path", "basename"),
              ("path", "dirname"), ("path", "abspath"), ("path", "normpath"), ("path", "splitext"),
              ("os", "getcwd"), ("os", "urandom"), ("os", "cpu_count")}


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class PFrame:
    file: PyFile
    cls: PyClass | None
    self_obj: Obj | None
    depth: int
    env: dict = field(default_factory=dict)
    rets: list = field(default_factory=list)
    branch: int = 0
    local_funcs: dict = field(default_factory=dict)
    route: bool = False


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "'": "'", "\\": "\\"}.get(raw[i + 1], raw[i + 1]))
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


def _s(v: object) -> str:
    return "None" if v is None else ("True" if v is True else "False" if v is False else str(v))


class PyInterp:
    MAX_STEPS = 60000
    MAX_DEPTH = 6

    def __init__(self, index: PyIndex, cwes: set[int] | None = None, sink_hook=None, store_hook=None, return_hook=None):
        self.index = index
        self.cwes = cwes or set()
        self.sink_hook = sink_hook      # hook(node, name, recv_text, args, kw, recv, frame)
        self.store_hook = store_hook    # hook(node, base_text, key, value, frame)
        self.return_hook = return_hook  # hook(node, value, frame)
        self.steps = 0
        self._cur_env: dict = {}
        self.probes: dict[tuple, list] = {}
        self.probe_keys: set[tuple] = set()
        self.dead: list[tuple] = []
        self._active: set[int] = set()

    # ------------------------------------------------------------------ helpers
    def _tick(self) -> None:
        self.steps += 1
        if self.steps > self.MAX_STEPS:
            raise BudgetExceeded

    def _key(self, fr: PFrame, n: Node) -> tuple:
        return (id(fr.file), n.start_byte, n.end_byte)

    def _dead(self, fr: PFrame, n: Node | None) -> None:
        if n is not None:
            self.dead.append(self._key(fr, n))

    def _sanitizer(self, name: str) -> bool:
        return any(name in SANITIZERS.get(c, ()) for c in self.cwes)

    @staticmethod
    def _combine(*vals: Val) -> Val:
        rank = {"safe": 0, "unknown": 1, "tainted": 2}
        worst = "safe"
        for v in vals:
            c = classify(v)
            if rank[c] > rank[worst]:
                worst = c
        return add_prefix({"safe": SAFE, "unknown": UNKNOWN, "tainted": Tainted("derived")}[worst], vals)

    def _mutating(self, fr: PFrame, c) -> None:
        if fr.branch > c.birth_depth:
            c.dirty = True

    @staticmethod
    def _opaque(recv: Val | None, args: list[Val], pure: bool = False) -> Val:
        vals = ([recv] if recv is not None else []) + list(args)
        if not pure:
            for v in vals:
                if isinstance(v, (ListV, MapV)):
                    v.opaque = True
        classes = [classify(v) for v in vals]
        if "tainted" in classes:
            return Tainted("through-call")
        if all(c == "safe" for c in classes) and (pure or recv is not None):
            return SAFE
        return UNKNOWN

    # ------------------------------------------------------------------ expressions
    def eval(self, node: Node | None, fr: PFrame) -> Val:
        if node is None:
            return UNKNOWN
        self._tick()
        val = self._eval(node, fr)
        key = self._key(fr, node)
        if key in self.probe_keys:
            self.probes.setdefault(key, []).append((val, classify(val)))
        return val

    def _eval(self, n: Node, fr: PFrame) -> Val:
        t = n.type
        if t == "string":
            return self._string(n, fr)
        if t == "concatenated_string":
            return self._join_const([self.eval(c, fr) for c in n.named_children])
        if t == "integer":
            try:
                return Const(int(_t(n).replace("_", ""), 0))
            except ValueError:
                return UNKNOWN
        if t == "float":
            try:
                return Const(float(_t(n).replace("_", "")))
            except ValueError:
                return UNKNOWN
        if t == "true":
            return Const(True)
        if t == "false":
            return Const(False)
        if t == "none":
            return Const(None)
        if t == "identifier":
            return self._name(_t(n), fr)
        if t == "attribute":
            return self._attr(n, fr)
        if t == "call":
            return self._call(n, fr)
        if t == "subscript":
            return self._subscript(n, fr)
        if t == "parenthesized_expression":
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t == "binary_operator":
            return self._binary(n, fr)
        if t == "unary_operator":
            v = self.eval(n.child_by_field_name("argument"), fr)
            op = _t(n.child_by_field_name("operator"))
            if isinstance(v, Const) and isinstance(v.value, (int, float)) and not isinstance(v.value, bool):
                return Const(-v.value if op == "-" else v.value)
            return self._combine(v)
        if t == "not_operator":
            v = self.eval(n.child_by_field_name("argument"), fr)
            return Const(not v.value) if isinstance(v, Const) and isinstance(v.value, (bool, int, str, type(None))) else self._combine(v)
        if t == "boolean_operator":
            return self._boolop(n, fr)
        if t == "comparison_operator":
            return self._compare(n, fr)
        if t == "conditional_expression":
            return self._ifexp(n, fr)
        if t in ("list", "tuple", "set", "expression_list"):
            return ListV([self.eval(c, fr) for c in n.named_children], birth_depth=fr.branch)
        if t == "dictionary":
            return self._dict(n, fr)
        if t in ("list_comprehension", "set_comprehension", "generator_expression", "dictionary_comprehension"):
            return self._comprehension(n, fr)
        if t in ("assignment", "augmented_assignment"):
            return self._assign(n, fr)
        if t == "named_expression":
            val = self.eval(n.child_by_field_name("value"), fr)
            self._bind(n.child_by_field_name("name"), val, fr)
            return val
        if t in ("await", "yield"):
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t == "lambda":
            return ClosureV(n, fr.file, dict(fr.env))
        if t in ("ellipsis",):
            return UNKNOWN
        return UNKNOWN

    def _join_const(self, parts: list[Val]) -> Val:
        if all(isinstance(p, Const) for p in parts):
            return Const("".join(_s(p.value) for p in parts))
        return self._combine(*parts)

    def _string(self, n: Node, fr: PFrame) -> Val:
        prefix = _t(next((c for c in n.children if c.type == "string_start"), None)).lower()
        raw = "r" in prefix
        parts: list[Val] = []
        for c in n.children:
            if c.type == "string_content":
                parts.append(Const(_t(c) if raw else _unescape(_t(c))))
            elif c.type == "escape_sequence":
                parts.append(Const(_t(c) if raw else _unescape(_t(c))))
            elif c.type == "interpolation":
                expr = c.child_by_field_name("expression") or (c.named_children[0] if c.named_children else None)
                parts.append(self.eval(expr, fr))
        return self._join_const(parts) if parts else Const("")

    def _name(self, name: str, fr: PFrame) -> Val:
        if name in fr.env:
            return fr.env[name]
        if fr.self_obj is not None and name == "self":
            return fr.self_obj
        if name == "request" and "request" not in fr.file.constants and "request" not in fr.file.functions:
            return Obj(kind="request", type_name="Request")
        const = fr.file.constants.get(name)
        if const is not None and id(const) not in self._active and fr.depth <= self.MAX_DEPTH:
            self._active.add(id(const))
            try:
                v = self.eval(const, PFrame(fr.file, None, None, fr.depth + 1))
            finally:
                self._active.discard(id(const))
            return v if classify(v) == "safe" else UNKNOWN
        imp = fr.file.imports.get(name)
        if imp and imp[1]:
            mod = self.index.resolve_module(imp[0], imp[2], fr.file)
            if mod is not None and imp[1] in mod.constants and id(mod.constants[imp[1]]) not in self._active:
                node = mod.constants[imp[1]]
                self._active.add(id(node))
                try:
                    v = self.eval(node, PFrame(mod, None, None, fr.depth + 1))
                finally:
                    self._active.discard(id(node))
                return v if classify(v) == "safe" else UNKNOWN
        return UNKNOWN

    def _attr(self, n: Node, fr: PFrame) -> Val:
        obj_n, attr = n.child_by_field_name("object"), _t(n.child_by_field_name("attribute"))
        base = self.eval(obj_n, fr)
        if isinstance(base, Obj):
            if base.kind == "request":
                return SAFE if attr in REQUEST_ATTRS_SAFE else Tainted("request." + attr)
            if attr in base.fields:
                return base.fields[attr]
            return UNKNOWN
        if classify(base) == "tainted":
            return Tainted("through-attr")
        return UNKNOWN

    def _subscript(self, n: Node, fr: PFrame) -> Val:
        base = self.eval(n.child_by_field_name("value"), fr)
        idx_nodes = n.children_by_field_name("subscript")
        idx = self.eval(idx_nodes[0], fr) if idx_nodes else UNKNOWN
        if isinstance(base, Obj) and base.kind == "request":
            return Tainted("request[...]")
        if isinstance(base, ListV):
            if isinstance(idx, Const) and isinstance(idx.value, int) and not isinstance(idx.value, bool) and not base.dirty and not base.opaque:
                i = idx.value
                if -len(base.items) <= i < len(base.items):
                    return base.items[i]
            return join_all(base.items + [UNKNOWN]) if base.items else UNKNOWN
        if isinstance(base, MapV):
            if isinstance(idx, Const) and not base.dirty and not base.opaque and idx.value in base.entries:
                return base.entries[idx.value]
            return join_all(list(base.entries.values()) + [UNKNOWN])
        if classify(base) == "tainted":
            return Tainted("derived")
        if isinstance(base, Const) and isinstance(base.value, str):
            return SAFE
        return UNKNOWN

    def _binary(self, n: Node, fr: PFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        a = self.eval(n.child_by_field_name("left"), fr)
        b = self.eval(n.child_by_field_name("right"), fr)
        if op == "/" and ((isinstance(a, Obj) and a.type_name == "Path") or (isinstance(b, Obj) and b.type_name == "Path")):
            return Obj(type_name="Path", fields={"v": self._combine(a, b)})
        if isinstance(a, ListV) and isinstance(b, ListV) and op == "+":
            return ListV(a.items + b.items, birth_depth=fr.branch)
        if isinstance(a, Const) and isinstance(b, Const) and op != "%":
            x, y = a.value, b.value
            try:
                if op == "+" and isinstance(x, str) and isinstance(y, str):
                    return Const(x + y)
                if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool) and not isinstance(y, bool):
                    if op == "+": return Const(x + y)
                    if op == "-": return Const(x - y)
                    if op == "*": return Const(x * y)
                    if op == "//" and y != 0: return Const(x // y)
                    if op == "/" and y != 0: return Const(x / y)
                    if op == "%" and y != 0: return Const(x % y)
                if op == "*" and isinstance(x, str) and isinstance(y, int) and not isinstance(y, bool):
                    return Const(x * y)
            except (TypeError, ValueError, OverflowError):
                pass
            return SAFE
        if op == "%" and isinstance(a, Const) and isinstance(a.value, str):
            fmt_args = b.items if isinstance(b, ListV) else [b]
            if all(isinstance(x, Const) for x in fmt_args):
                try:
                    vals = tuple(x.value for x in fmt_args)
                    return Const(a.value % (vals if len(vals) != 1 else vals[0]))
                except (TypeError, ValueError):
                    return SAFE
            return self._combine(a, *fmt_args)
        return self._combine(a, b)

    def _truthy(self, v: Val):
        if isinstance(v, Const):
            return bool(v.value)
        return None

    def _boolop(self, n: Node, fr: PFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        left_n, right_n = n.child_by_field_name("left"), n.child_by_field_name("right")
        a = self.eval(left_n, fr)
        tv = self._truthy(a)
        if tv is not None:
            if op == "and":
                if tv is False:
                    self._dead(fr, right_n)
                    return a
                return self.eval(right_n, fr)
            if tv is True:
                self._dead(fr, right_n)
                return a
            return self.eval(right_n, fr)
        fr.branch += 1
        try:
            return join(a, self.eval(right_n, fr))
        finally:
            fr.branch -= 1

    def _compare(self, n: Node, fr: PFrame) -> Val:
        operands = [self.eval(c, fr) for c in n.named_children]
        ops = [_t(c) for c in n.children if not c.is_named]
        if len(operands) == 2 and len(ops) == 1 and all(isinstance(o, Const) for o in operands):
            x, y, op = operands[0].value, operands[1].value, ops[0]
            try:
                return Const({"==": x == y, "!=": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}.get(op, UNKNOWN)) \
                    if op in ("==", "!=", "<", ">", "<=", ">=") else SAFE
            except TypeError:
                return SAFE
        return SAFE

    def _ifexp(self, n: Node, fr: PFrame) -> Val:
        parts = n.named_children
        if len(parts) != 3:
            return UNKNOWN
        cons_n, cond_n, alt_n = parts
        cond = self.eval(cond_n, fr)
        tv = self._truthy(cond)
        if tv is not None:
            self._dead(fr, alt_n if tv else cons_n)
            return self.eval(cons_n if tv else alt_n, fr)
        fr.branch += 1
        try:
            return join(self.eval(cons_n, fr), self.eval(alt_n, fr))
        finally:
            fr.branch -= 1

    def _dict(self, n: Node, fr: PFrame) -> Val:
        m = MapV({}, birth_depth=fr.branch)
        for c in n.named_children:
            if c.type == "pair":
                k, v = self.eval(c.child_by_field_name("key"), fr), self.eval(c.child_by_field_name("value"), fr)
                if isinstance(k, Const):
                    m.entries[k.value] = v
                else:
                    m.entries[("*", id(c))] = v
                    m.dirty = True
            else:  # **spread
                m.entries[("*", id(c))] = self.eval(c, fr)
                m.dirty = True
        return m

    _SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- ")
    _CLASS_CALLS = {"isalnum", "isalpha", "isdigit", "isnumeric", "isdecimal"}

    def _is_whitelist(self, cond: Node | None, var: str) -> bool:
        """True when `cond` only lets through characters that are safe in a path, command or query:
        letters, digits, underscore, hyphen and space. A conjunction needs one such term; a disjunction
        needs all of them."""
        if cond is None:
            return False
        t = cond.type
        if t == "parenthesized_expression":
            return self._is_whitelist(cond.named_children[0] if cond.named_children else None, var)
        if t == "boolean_operator":
            parts = [cond.child_by_field_name("left"), cond.child_by_field_name("right")]
            op = _t(cond.child_by_field_name("operator"))
            results = [self._is_whitelist(p, var) for p in parts]
            return all(results) if op == "or" else any(results)
        if t == "call":
            f = cond.child_by_field_name("function")
            return (f is not None and f.type == "attribute" and _t(f.child_by_field_name("object")) == var
                    and _t(f.child_by_field_name("attribute")) in self._CLASS_CALLS)
        if t == "comparison_operator":
            ops = [_t(c) for c in cond.children if not c.is_named]
            operands = cond.named_children
            if len(operands) == 2 and ops == ["in"] and _t(operands[0]) == var and operands[1].type == "string":
                lit = self._string(operands[1], PFrame(None, None, None, 0))  # type: ignore[arg-type]
                return isinstance(lit, Const) and isinstance(lit.value, str) and set(lit.value) <= self._SAFE_CHARS
        return False

    def _comprehension(self, n: Node, fr: PFrame) -> Val:
        body = n.child_by_field_name("body")
        fors = [c for c in n.named_children if c.type == "for_in_clause"]
        ifs = [c for c in n.named_children if c.type == "if_clause"]
        if len(fors) == 1 and body is not None and body.type == "identifier":
            var = _t(body)
            left = fors[0].child_by_field_name("left")
            if left is not None and left.type == "identifier" and _t(left) == var \
                    and any(self._is_whitelist(c.named_children[0] if c.named_children else None, var) for c in ifs):
                self.eval(fors[0].child_by_field_name("right"), fr)
                return ListV([SAFE], birth_depth=fr.branch, dirty=True)
        vals = []
        for c in n.named_children:
            if c.type in ("for_in_clause", "if_clause"):
                for g in c.named_children:
                    vals.append(self.eval(g, fr))
        return ListV([self._combine(*vals)] if vals else [], birth_depth=fr.branch, dirty=True)

    # ------------------------------------------------------------------ assignment
    def _assign(self, n: Node, fr: PFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        if n.type == "augmented_assignment":
            op = _t(n.child_by_field_name("operator"))[:-1]
            cur = self.eval(left, fr)
            rhs = self.eval(right, fr)
            if isinstance(cur, ListV) and op == "+" and isinstance(rhs, ListV):
                self._mutating(fr, cur)
                cur.items.extend(rhs.items)
                return cur
            if isinstance(cur, Const) and isinstance(rhs, Const) and op == "+" and isinstance(cur.value, str) and isinstance(rhs.value, str):
                val = Const(cur.value + rhs.value)
            elif isinstance(cur, Const) and isinstance(rhs, Const) and op in "+-*" and all(isinstance(x.value, (int, float)) and not isinstance(x.value, bool) for x in (cur, rhs)):
                val = Const(cur.value + rhs.value if op == "+" else cur.value - rhs.value if op == "-" else cur.value * rhs.value)
            else:
                val = self._combine(cur, rhs)
            self._bind(left, val, fr)
            return val
        if right is None:
            return UNKNOWN
        val = self.eval(right, fr)
        self._bind(left, val, fr)
        return val

    def _bind(self, target: Node | None, val: Val, fr: PFrame) -> None:
        if target is None:
            return
        t = target.type
        if t == "identifier":
            fr.env[_t(target)] = val
        elif t in ("pattern_list", "tuple_pattern", "list_pattern", "tuple", "list"):
            targets = [c for c in target.named_children]
            if isinstance(val, ListV) and len(val.items) == len(targets) and not val.dirty and not val.opaque \
                    and not any(c.type == "list_splat_pattern" for c in targets):
                for c, v in zip(targets, val.items):
                    self._bind(c, v, fr)
            else:
                elem = join_all(val.items + [UNKNOWN]) if isinstance(val, ListV) else (Tainted("element") if classify(val) == "tainted" else UNKNOWN)
                for c in targets:
                    self._bind(c, elem, fr)
        elif t == "list_splat_pattern":
            self._bind(target.named_children[0] if target.named_children else None, val, fr)
        elif t == "attribute":
            base = self.eval(target.child_by_field_name("object"), fr)
            attr = _t(target.child_by_field_name("attribute"))
            if isinstance(base, Obj):
                old = base.fields.get(attr)
                base.fields[attr] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "subscript":
            base_n = target.child_by_field_name("value")
            base = self.eval(base_n, fr)
            idx_nodes = target.children_by_field_name("subscript")
            idx = self.eval(idx_nodes[0], fr) if idx_nodes else UNKNOWN
            if self.store_hook is not None:
                self.store_hook(target, _t(base_n), idx, val, fr)
            if isinstance(base, MapV):
                self._mutating(fr, base)
                if isinstance(idx, Const):
                    base.entries[idx.value] = val
                else:
                    base.entries[("*", id(target))] = val
                    base.dirty = True
            elif isinstance(base, ListV):
                self._mutating(fr, base)
                if isinstance(idx, Const) and isinstance(idx.value, int) and not isinstance(idx.value, bool) and -len(base.items) <= idx.value < len(base.items) and not base.dirty:
                    base.items[idx.value] = val
                else:
                    base.items.append(val)
                    base.dirty = True

    # ------------------------------------------------------------------ calls
    def _collect_args(self, args_n: Node | None, fr: PFrame) -> tuple[list[Val], dict[str, Val]]:
        pos: list[Val] = []
        kw: dict[str, Val] = {}
        if args_n is None or args_n.type != "argument_list":
            if args_n is not None:  # a bare generator expression argument
                pos.append(self.eval(args_n, fr))
            return pos, kw
        for c in args_n.named_children:
            if c.type == "keyword_argument":
                kw[_t(c.child_by_field_name("name"))] = self.eval(c.child_by_field_name("value"), fr)
            elif c.type in ("list_splat", "dictionary_splat"):
                pos.append(self.eval(c.named_children[0], fr) if c.named_children else UNKNOWN)
            else:
                pos.append(self.eval(c, fr))
        return pos, kw

    def _hook(self, node, name, recv_text, args, kw, recv, fr) -> None:
        if self.sink_hook is not None:
            self.sink_hook(node, name, recv_text, args, kw, recv, fr)

    def _call(self, n: Node, fr: PFrame) -> Val:
        func = n.child_by_field_name("function")
        args, kw = self._collect_args(n.child_by_field_name("arguments"), fr)
        if func is None:
            return UNKNOWN
        if func.type == "identifier":
            name = _t(func)
            self._hook(n, name, "", args, kw, None, fr)
            imp = fr.file.imports.get(name)
            if imp is not None and imp[1] and imp[0] and name not in fr.file.functions and name not in fr.file.classes:
                # `from subprocess import run as run_proc`: the call is subprocess.run
                self._hook(n, imp[1], imp[0], args, kw, None, fr)
            return self._call_name(name, n, args, kw, fr)
        if func.type == "attribute":
            obj_n, name = func.child_by_field_name("object"), _t(func.child_by_field_name("attribute"))
            recv_text = _t(obj_n)
            if self._sanitizer(name):
                self.eval(obj_n, fr)
                return SAFE
            # module.function resolved through the import index
            if obj_n is not None and obj_n.type == "identifier" and _t(obj_n) not in fr.env:
                hit = self.index.module_attr(_t(obj_n), name, fr.file)
                if hit is not None:
                    self._hook(n, name, recv_text, args, kw, None, fr)
                    return self._run_symbol(hit, args, kw, fr)
            recv = self.eval(obj_n, fr)
            self._hook(n, name, recv_text, args, kw, recv, fr)
            return self._method(recv, name, args, kw, fr, recv_text)
        # calling a call result, subscript, etc.
        self.eval(func, fr)
        return self._opaque(None, args)

    def _call_closure(self, c: ClosureV, args: list[Val], kw: dict, fr: PFrame) -> Val:
        if fr.depth + 1 > self.MAX_DEPTH or id(c.node) in self._active:
            return self._opaque(None, args + list(kw.values()))
        inner = PFrame(c.file, fr.cls, fr.self_obj, fr.depth + 1, route=fr.route)
        inner.env.update(c.env)
        inner.local_funcs.update(fr.local_funcs)
        params = c.node.child_by_field_name("parameters")
        pos = list(args)
        for p in (params.named_children if params is not None else []):
            nm = p if p.type == "identifier" else p.child_by_field_name("name") if p.type in ("default_parameter",) else None
            if nm is not None:
                inner.env[_t(nm)] = pos.pop(0) if pos else kw.get(_t(nm), UNKNOWN)
        self._active.add(id(c.node))
        try:
            return self.eval(c.node.child_by_field_name("body"), inner)
        finally:
            self._active.discard(id(c.node))

    def _call_name(self, name: str, n: Node, args: list[Val], kw: dict, fr: PFrame) -> Val:
        held = fr.env.get(name)
        if isinstance(held, ClosureV):
            return self._call_closure(held, args, kw, fr)
        if self._sanitizer(name):
            return SAFE
        if name in fr.local_funcs:
            return self._run_function(fr.file, fr.local_funcs[name], None, None, args, kw, fr, closure=fr)
        hit = self.index.lookup(name, fr.file)
        if hit is not None:
            return self._run_symbol(hit, args, kw, fr)
        if name in SAFE_RESULT_CALLS:
            return SAFE
        if name in ("list", "set", "frozenset") and args and isinstance(args[0], ListV):
            return ListV(list(args[0].items), birth_depth=fr.branch)
        if name == "dict" and args and isinstance(args[0], MapV):
            return MapV(dict(args[0].entries), birth_depth=fr.branch)
        if name in ("list", "dict", "set"):
            return ListV([], birth_depth=fr.branch) if name != "dict" else MapV(dict(kw), birth_depth=fr.branch)
        if name == "range":
            return SAFE
        if name == "enumerate" and args:
            elem = join_all(args[0].items) if isinstance(args[0], ListV) and args[0].items else (Tainted("element") if classify(args[0]) == "tainted" else UNKNOWN)
            return ListV([ListV([SAFE, elem], birth_depth=fr.branch)], birth_depth=fr.branch, dirty=True)
        if name in PROPAGATING_BUILTINS:
            return self._combine(*args, *kw.values()) if args else UNKNOWN
        if name in ("Path", "PurePath", "PosixPath", "WindowsPath", "PurePosixPath"):
            return Obj(type_name="Path", fields={"v": self._combine(*args)})
        if name == "open":
            return Obj(type_name="file")
        return self._opaque(None, args + list(kw.values()), pure=name in ("print", "zip", "map", "filter", "any", "all", "type"))

    def _run_symbol(self, hit, args: list[Val], kw: dict, fr: PFrame) -> Val:
        kind, node, file = hit
        if kind == "function":
            return self._run_function(file, node, None, None, args, kw, fr)
        cls: PyClass = node
        obj = Obj(cls=cls, type_name=cls.name)
        init = self._find_method(cls, "__init__")
        if init is not None:
            self._run_function(init[0].file, init[1], init[0], obj, args, kw, fr)
        return obj

    def make_this(self, cls: PyClass, pf: PyFile) -> Obj:
        try:
            obj = self._run_symbol(("class", cls, cls.file), [], {}, PFrame(pf, cls, None, 0))
        except (BudgetExceeded, RecursionError):
            return Obj(cls=cls, type_name=cls.name)
        return obj if isinstance(obj, Obj) else Obj(cls=cls, type_name=cls.name)

    def _find_method(self, cls: PyClass, name: str):
        seen: set[int] = set()
        cur: PyClass | None = cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if name in cur.methods:
                return cur, cur.methods[name]
            nxt = None
            for b in cur.bases:
                hit = self.index.lookup(b, cur.file)
                if hit is not None and hit[0] == "class":
                    nxt = hit[1]
                    break
            cur = nxt
        return None

    def _run_function(self, file: PyFile, fnode: Node, cls: PyClass | None, self_obj: Obj | None, args: list[Val], kw: dict, fr: PFrame, closure: PFrame | None = None) -> Val:
        if fr.depth + 1 > self.MAX_DEPTH or id(fnode) in self._active:
            return self._opaque(self_obj, args + list(kw.values()))
        body = fnode.child_by_field_name("body")
        if body is None:
            return self._opaque(self_obj, args)
        inner = PFrame(file, cls, self_obj, fr.depth + 1)
        if closure is not None:
            # A nested function sees the variables of the function that defines it, as they are when it is called.
            inner.env.update(closure.env)
            inner.local_funcs.update(closure.local_funcs)
            inner.route = closure.route
        self._bind_params(fnode, args, kw, inner, self_obj)
        self._active.add(id(fnode))
        try:
            self.exec(body, inner)
        finally:
            self._active.discard(id(fnode))
        if closure is not None:
            # `nonlocal x` writes reach the defining function.
            stack = [body]
            while stack:
                cur = stack.pop()
                if cur.type == "nonlocal_statement":
                    for c in cur.named_children:
                        nm = _t(c)
                        if nm in inner.env:
                            closure.env[nm] = join(closure.env[nm], inner.env[nm]) if nm in closure.env else inner.env[nm]
                elif cur.type not in ("function_definition", "class_definition"):
                    stack.extend(cur.children)
        return join_all(inner.rets) if inner.rets else Const(None)

    def _bind_params(self, fnode: Node, args: list[Val], kw: dict, inner: PFrame, self_obj: Obj | None) -> None:
        params = fnode.child_by_field_name("parameters")
        if params is None:
            return
        pos = list(args)
        first = True
        for p in params.named_children:
            t = p.type
            name_n = p if t == "identifier" else p.child_by_field_name("name") if t in ("default_parameter", "typed_default_parameter") \
                else (p.named_children[0] if t == "typed_parameter" and p.named_children else None)
            if t == "list_splat_pattern":
                inner.env[_t(p.named_children[0]) if p.named_children else "*args"] = ListV(list(pos), birth_depth=0, dirty=True)
                pos = []
                continue
            if t == "dictionary_splat_pattern":
                inner.env[_t(p.named_children[0]) if p.named_children else "**kw"] = MapV(dict(kw), birth_depth=0, dirty=True)
                continue
            if name_n is None:
                continue
            name = _t(name_n)
            if first and self_obj is not None and name in ("self", "cls"):
                inner.env[name] = self_obj
                first = False
                continue
            first = False
            if pos:
                inner.env[name] = pos.pop(0)
            elif name in kw:
                inner.env[name] = kw[name]
            elif t in ("default_parameter", "typed_default_parameter"):
                inner.env[name] = self.eval(p.child_by_field_name("value"), inner)
            else:
                inner.env[name] = UNKNOWN

    def bind_entry(self, fnode: Node, fr: PFrame, route: bool) -> None:
        """Parameters of a function run as an entry point. In a web route, parameters are request data."""
        params = fnode.child_by_field_name("parameters")
        if params is None:
            return
        for p in params.named_children:
            t = p.type
            name_n = p if t == "identifier" else p.child_by_field_name("name") if t in ("default_parameter", "typed_default_parameter") \
                else (p.named_children[0] if t == "typed_parameter" and p.named_children else None)
            if name_n is None:
                continue
            name = _t(name_n)
            if name in ("self", "cls"):
                fr.env[name] = fr.self_obj or UNKNOWN
            elif name == "request":
                fr.env[name] = Obj(kind="request", type_name="Request")
            elif route and t in ("default_parameter", "typed_default_parameter") and re.match(r"\s*(?:fastapi\.)?(Depends|Security)\(", _t(p.child_by_field_name("value"))):
                fr.env[name] = UNKNOWN  # a FastAPI dependency (database session, current user), not request data
            elif route and t != "list_splat_pattern":
                fr.env[name] = Tainted("route-param")
            else:
                fr.env[name] = UNKNOWN

    # ------------------------------------------------------------------ methods on values
    def _method(self, recv: Val, name: str, args: list[Val], kw: dict, fr: PFrame, recv_text: str) -> Val:
        if isinstance(recv, Choice):
            return join_all(self._method(a, name, args, kw, fr, recv_text) for a in recv.alts)
        if isinstance(recv, Obj):
            if recv.kind == "request":
                return Tainted("request." + name) if name not in REQUEST_ATTRS_SAFE else SAFE
            if recv.cls is not None:
                m = self._find_method(recv.cls, name)
                if m is not None:
                    return self._run_function(m[0].file, m[1], m[0], recv, args, kw, fr)
            if recv.type_name == "file" and name in ("read", "readline", "readlines", "write", "close", "seek", "__enter__", "__exit__"):
                return UNKNOWN
            return self._opaque(recv, args + list(kw.values()))
        if isinstance(recv, ListV):
            return self._list_method(recv, name, args, fr)
        if isinstance(recv, MapV):
            return self._dict_method(recv, name, args, fr)
        if isinstance(recv, Const) and isinstance(recv.value, str):
            return self._str_method(recv, name, args, kw)
        if name in BENIGN_METHODS or name in SAFE_RESULT_CALLS:
            return SAFE
        if classify(recv) == "tainted":
            return Tainted("derived")
        if recv_text == "re" and name in ("sub", "subn") and len(args) >= 3 and isinstance(args[0], Const) \
                and isinstance(args[1], Const) and args[1].value == "" and self._negated_safe_class(args[0].value):
            return SAFE
        base = recv_text.split(".")[-1]
        pure = (recv_text, name) in PURE_FUNCS or base in PURE_MODULES or recv_text in PURE_MODULES
        return self._opaque(None, args + list(kw.values()), pure=pure) if pure else self._opaque(recv, args + list(kw.values()))

    @staticmethod
    def _negated_safe_class(pattern: object) -> bool:
        """`[^a-zA-Z0-9_-]`-style patterns: removing everything outside a safe set."""
        import re as _re

        if not isinstance(pattern, str):
            return False
        m = _re.fullmatch(r"\[\^([^\]]+)\][+*]?", pattern)
        if not m:
            return False
        rest = m.group(1)
        for token in ("a-z", "A-Z", "0-9", "\\w", "\\d", "_", "-", " "):
            rest = rest.replace(token, "")
        return rest == ""

    def _str_method(self, recv: Const, name: str, args: list[Val], kw: dict) -> Val:
        s = recv.value
        consts = all(isinstance(a, Const) for a in args) and all(isinstance(v, Const) for v in kw.values())
        if name == "format":
            if consts:
                try:
                    return Const(s.format(*[a.value for a in args], **{k: v.value for k, v in kw.items()}))
                except (IndexError, KeyError, ValueError, AttributeError):
                    return SAFE
            return self._combine(recv, *args, *kw.values())
        if name == "join" and args:
            if isinstance(args[0], ListV):
                items = args[0].items
                if all(isinstance(i, Const) for i in items) and not args[0].dirty and not args[0].opaque:
                    return Const(s.join(_s(i.value) for i in items))
                return self._combine(recv, *items)
            return self._combine(recv, args[0])
        if consts:
            try:
                if name in ("upper", "lower", "strip", "lstrip", "rstrip", "title", "capitalize", "casefold"):
                    return Const(getattr(s, name)(*[a.value for a in args]))
                if name in ("replace", "removeprefix", "removesuffix", "ljust", "rjust", "center", "zfill"):
                    return Const(getattr(s, name)(*[a.value for a in args]))
                if name in ("startswith", "endswith", "isdigit", "isalpha", "isalnum", "find", "count", "index", "isspace"):
                    return Const(getattr(s, name)(*[a.value for a in args]))
                if name in ("split", "rsplit", "splitlines", "partition", "rpartition"):
                    return ListV([Const(x) for x in getattr(s, name)(*[a.value for a in args])], birth_depth=0)
                if name == "encode" or name == "decode":
                    return recv
            except (TypeError, ValueError, AttributeError):
                return SAFE
            return SAFE
        if name in BENIGN_METHODS:
            return SAFE
        return self._combine(recv, *args, *kw.values())

    def _list_method(self, lst: ListV, name: str, args: list[Val], fr: PFrame) -> Val:
        idx = args[0].value if args and isinstance(args[0], Const) and isinstance(args[0].value, int) and not isinstance(args[0].value, bool) else None
        if lst.opaque and name not in ("append", "extend", "insert", "pop", "remove", "clear"):
            return join_all(lst.items + [UNKNOWN])
        if name == "append" and args:
            self._mutating(fr, lst)
            lst.items.append(args[0])
            return Const(None)
        if name == "extend" and args:
            self._mutating(fr, lst)
            if isinstance(args[0], ListV):
                lst.items.extend(args[0].items)
            else:
                lst.items.append(args[0])
                lst.dirty = True
            return Const(None)
        if name == "insert" and len(args) == 2:
            self._mutating(fr, lst)
            if idx is not None and 0 <= idx <= len(lst.items):
                lst.items.insert(idx, args[1])
            else:
                lst.items.append(args[1])
                lst.dirty = True
            return Const(None)
        if name == "pop":
            self._mutating(fr, lst)
            if not lst.dirty and lst.items:
                i = idx if idx is not None else -1
                if -len(lst.items) <= i < len(lst.items):
                    return lst.items.pop(i)
            lst.dirty = True
            return join_all(lst.items + [UNKNOWN])
        if name == "remove" and args:
            self._mutating(fr, lst)
            if isinstance(args[0], Const) and not lst.dirty:
                for i, it in enumerate(lst.items):
                    if it == args[0]:
                        lst.items.pop(i)
                        break
            else:
                lst.dirty = True
            return Const(None)
        if name == "clear":
            self._mutating(fr, lst)
            lst.items.clear()
            return Const(None)
        if name == "copy":
            return ListV(list(lst.items), birth_depth=fr.branch, dirty=lst.dirty, opaque=lst.opaque)
        if name in ("index", "count") or name in BENIGN_METHODS:
            return SAFE
        if name in ("sort", "reverse"):
            lst.dirty = True
            return Const(None)
        return self._opaque(lst, args)

    def _dict_method(self, m: MapV, name: str, args: list[Val], fr: PFrame) -> Val:
        key = args[0].value if args and isinstance(args[0], Const) and isinstance(args[0].value, (str, int)) else None
        if m.opaque and name not in ("update", "pop", "clear", "setdefault"):
            return join_all(list(m.entries.values()) + [UNKNOWN])
        if name == "get" and args:
            if key is not None and not m.dirty:
                if key in m.entries:
                    return m.entries[key]
                return args[1] if len(args) > 1 else Const(None)
            return join_all(list(m.entries.values()) + [UNKNOWN])
        if name == "setdefault" and args:
            self._mutating(fr, m)
            if key is not None and not m.dirty:
                if key not in m.entries:
                    m.entries[key] = args[1] if len(args) > 1 else Const(None)
                return m.entries[key]
            m.dirty = True
            return UNKNOWN
        if name == "pop" and args:
            self._mutating(fr, m)
            if key is not None and not m.dirty and key in m.entries:
                return m.entries.pop(key)
            m.dirty = True
            return join_all(list(m.entries.values()) + [UNKNOWN])
        if name == "update":
            self._mutating(fr, m)
            if args and isinstance(args[0], MapV) and not args[0].dirty:
                m.entries.update(args[0].entries)
            else:
                m.dirty = True
                m.entries[("*", id(args[0]) if args else 0)] = self._combine(*args) if args else UNKNOWN
            return Const(None)
        if name == "keys":
            return ListV([Const(k) for k in m.entries if not isinstance(k, tuple)], birth_depth=fr.branch, dirty=m.dirty)
        if name == "values":
            return ListV(list(m.entries.values()), birth_depth=fr.branch, dirty=m.dirty)
        if name == "items":
            return ListV([ListV([Const(k) if not isinstance(k, tuple) else UNKNOWN, v], birth_depth=fr.branch) for k, v in m.entries.items()],
                         birth_depth=fr.branch, dirty=m.dirty)
        if name == "copy":
            return MapV(dict(m.entries), birth_depth=fr.branch, dirty=m.dirty, opaque=m.opaque)
        if name == "clear":
            self._mutating(fr, m)
            m.entries.clear()
            return Const(None)
        if name in BENIGN_METHODS or name in ("__contains__",):
            return SAFE
        return self._opaque(m, args)

    # ------------------------------------------------------------------ statements
    def exec(self, n: Node | None, fr: PFrame) -> bool:
        """Run a statement; True when control cannot continue past it (return or raise)."""
        if n is None:
            return False
        self._tick()
        t = n.type
        if t == "block" or t == "module":
            for c in n.named_children:
                if self.exec(c, fr):
                    return True
            return False
        if t == "expression_statement":
            for c in n.named_children:
                self.eval(c, fr)
            return False
        if t == "return_statement":
            val = self.eval(n.named_children[0], fr) if n.named_children else Const(None)
            fr.rets.append(val)
            if self.return_hook is not None:
                self.return_hook(n, val, fr)
            return True
        if t == "raise_statement":
            for c in n.named_children:
                self.eval(c, fr)
            return True
        if t == "if_statement":
            return self._if(n, fr)
        if t in ("for_statement", "while_statement"):
            return self._loop(n, fr)
        if t == "try_statement":
            return self._try(n, fr)
        if t == "with_statement":
            return self._with(n, fr)
        if t == "match_statement":
            return self._match(n, fr)
        if t == "decorated_definition":
            d = n.child_by_field_name("definition")
            return self.exec(d, fr) if d is not None else False
        if t == "function_definition":
            fr.local_funcs[_t(n.child_by_field_name("name"))] = n
            return False
        if t in ("class_definition", "import_statement", "import_from_statement", "future_import_statement", "pass_statement",
                 "break_statement", "continue_statement", "global_statement", "nonlocal_statement", "type_alias_statement"):
            return False
        for c in n.named_children:  # assert, delete, print and the like: evaluate for probes and hooks
            if c.type.endswith("statement") or c.type == "block":
                self.exec(c, fr)
            else:
                self.eval(c, fr)
        return False

    def _join_envs(self, fr: PFrame, a: dict, b: dict) -> None:
        out = {}
        for k in set(a) | set(b):
            out[k] = join(a[k], b[k]) if (k in a and k in b) else (a[k] if k in a else b[k])
        fr.env = out

    def _mark_safe(self, g: str, fr: PFrame) -> None:
        if g.endswith("@918"):
            if 918 in self.cwes:  # an allowed host proves the URL cannot point elsewhere; it proves nothing about other classes
                fr.env[g[:-4]] = SAFE
        else:
            fr.env[g] = SAFE

    def _host_var(self, n: Node | None) -> str | None:
        """`u.hostname`, `urlparse(u).netloc`: the local variable whose URL host this expression reads."""
        if n is None or n.type != "attribute" or _t(n.child_by_field_name("attribute")) not in G.HOST_ATTRS:
            return None
        obj = n.child_by_field_name("object")
        if obj is not None and obj.type == "identifier" and _t(obj) in self._cur_env:
            return _t(obj)
        if obj is not None and obj.type == "call":
            a = obj.child_by_field_name("arguments")
            first = a.named_children[0] if a is not None and a.named_children else None
            if first is not None and first.type == "identifier" and _t(first) in self._cur_env:
                return _t(first)
        return None

    def _guarded_names(self, cond: Node | None, fr: PFrame, negated: bool = False) -> set[str]:
        """Names proven safe when `cond` holds (or, with negated=True, when it does NOT hold).

        Recognized: `name in <safe collection>`, `name == <safe value>`, and name.isalnum()-style tests;
        a conjunction guards everything either side guards, a disjunction only what both guard."""
        if cond is None:
            return set()
        self._cur_env = fr.env
        t = cond.type
        if t == "parenthesized_expression":
            return self._guarded_names(cond.named_children[0] if cond.named_children else None, fr, negated)
        if t == "not_operator":
            return self._guarded_names(cond.child_by_field_name("argument"), fr, not negated)
        if t == "boolean_operator":
            op = _t(cond.child_by_field_name("operator"))
            left = self._guarded_names(cond.child_by_field_name("left"), fr, negated)
            right = self._guarded_names(cond.child_by_field_name("right"), fr, negated)
            holds_for_both = (op == "and") != negated  # `and` when true, `or` when false
            return left | right if holds_for_both else left & right
        if t == "call":
            f = cond.child_by_field_name("function")
            if not negated and f is not None and f.type == "attribute" and _t(f.child_by_field_name("object")).isidentifier() \
                    and _t(f.child_by_field_name("attribute")) in self._CLASS_CALLS:
                return {_t(f.child_by_field_name("object"))}
            return set()
        if t == "comparison_operator":
            operands = cond.named_children
            ops = " ".join(_t(c) for c in cond.children if not c.is_named)
            if len(operands) != 2:
                return set()
            positive = ops in ("in", "==")
            negative = ops in ("not in", "!=")
            if (positive and negated) or (negative and not negated) or not (positive or negative):
                return set()
            host = self._host_var(operands[0]) if ops in ("in", "not in", "==", "!=") else None
            if host is not None:
                name, other = host + "@918", operands[1]
            elif operands[0].type == "identifier":
                name, other = _t(operands[0]), operands[1]
            elif operands[1].type == "identifier" and ops in ("==", "!="):
                name, other = _t(operands[1]), operands[0]
            elif ops in ("==", "!=") and self._host_var(operands[1]) is not None:
                name, other = self._host_var(operands[1]) + "@918", operands[0]
            else:
                return set()
            val = self.eval(other, fr)
            if ops in ("in", "not in"):
                if isinstance(val, ListV) and not val.dirty and not val.opaque and all(classify(i) == "safe" for i in val.items):
                    return {name}
                if isinstance(val, MapV) and not val.dirty and not val.opaque and all(not isinstance(k, tuple) for k in val.entries):
                    return {name}
                return set()
            return {name} if classify(val) == "safe" else set()
        return set()

    def _if(self, n: Node, fr: PFrame) -> bool:
        clauses: list[tuple[Node | None, Node | None]] = [(n.child_by_field_name("condition"), n.child_by_field_name("consequence"))]
        has_else = False
        for c in n.named_children:
            if c.type == "elif_clause":
                clauses.append((c.child_by_field_name("condition"), c.child_by_field_name("consequence")))
            elif c.type == "else_clause":
                clauses.append((None, c.child_by_field_name("body") or (c.named_children[-1] if c.named_children else None)))
                has_else = True
        early_exit_guard: set[str] = set()
        results: list[dict] = []
        all_terminated = True
        i = 0
        while i < len(clauses):
            cond_n, block = clauses[i]
            cond = self.eval(cond_n, fr) if cond_n is not None else Const(True)
            tv = self._truthy(cond)
            if tv is True:
                for _, later in clauses[i + 1:]:
                    self._dead(fr, later)
                term = self.exec(block, fr)
                if results:
                    if not term:
                        results.append(fr.env)
                        all_terminated = False
                    first = results[0]
                    fr.env = first
                    for e in results[1:]:
                        self._join_envs(fr, fr.env, e)
                    return all_terminated and term and True
                return term
            if tv is False:
                self._dead(fr, block)
                i += 1
                continue
            base = dict(fr.env)
            for g in self._guarded_names(cond_n, fr):
                self._mark_safe(g, fr)  # inside this branch the tested name has passed the check
            fr.branch += 1
            try:
                term = self.exec(block, fr)
            finally:
                fr.branch -= 1
            if not term:
                results.append(fr.env)
                all_terminated = False
            elif i == 0 and not has_else and len(clauses) == 1:
                early_exit_guard = self._guarded_names(cond_n, fr, negated=True)
            fr.env = base
            i += 1
        if not has_else:
            results.append(fr.env)
            all_terminated = False
        if not results:
            return True
        fr.env = results[0]
        for e in results[1:]:
            self._join_envs(fr, fr.env, e)
        for g in early_exit_guard:
            self._mark_safe(g, fr)  # `if not check(name): return` leaves only the passing path
        return all_terminated and has_else

    def _loop(self, n: Node, fr: PFrame) -> bool:
        base = dict(fr.env)
        if n.type == "for_statement":
            it = self.eval(n.child_by_field_name("right"), fr)
            if isinstance(it, ListV) and not it.items and not it.dirty and not it.opaque:
                return False
            if isinstance(it, ListV):
                elem = join_all(it.items + ([UNKNOWN] if it.dirty or it.opaque else []))
            elif isinstance(it, MapV):
                elem = join_all([Const(k) if not isinstance(k, tuple) else UNKNOWN for k in it.entries] + [UNKNOWN])
            elif classify(it) == "tainted":
                elem = Tainted("element")
            elif isinstance(it, Const) and isinstance(it.value, str):
                elem = SAFE
            else:
                elem = UNKNOWN
            fr.branch += 1
            try:
                self._bind(n.child_by_field_name("left"), elem, fr)
                self.exec(n.child_by_field_name("body"), fr)
            finally:
                fr.branch -= 1
        else:
            cond = self.eval(n.child_by_field_name("condition"), fr)
            if self._truthy(cond) is False:
                self._dead(fr, n.child_by_field_name("body"))
                return False
            fr.branch += 1
            try:
                self.exec(n.child_by_field_name("body"), fr)
            finally:
                fr.branch -= 1
        self._join_envs(fr, base, fr.env)
        return False

    def _try(self, n: Node, fr: PFrame) -> bool:
        base = dict(fr.env)
        body = n.child_by_field_name("body")
        term = self.exec(body, fr)
        after = fr.env
        envs = [] if term else [after]
        all_term = term
        for c in n.named_children:
            if c.type == "else_clause" and not term:
                self.exec(c.named_children[-1] if c.named_children else None, fr)
                envs = [fr.env]
        for c in n.named_children:
            if c.type == "except_clause" or c.type == "except_group_clause":
                fr.env = {k: join(base.get(k, v), v) if k in base else v for k, v in after.items()}
                for a in c.named_children:
                    if a.type == "as_pattern":
                        tgt = next((x for x in a.named_children if x.type == "as_pattern_target"), None)
                        if tgt is not None and tgt.named_children:
                            fr.env[_t(tgt.named_children[0])] = UNKNOWN
                blk = next((x for x in c.named_children if x.type == "block"), None)
                fr.branch += 1
                try:
                    cterm = self.exec(blk, fr)
                finally:
                    fr.branch -= 1
                all_term = all_term and cterm
                if not cterm:
                    envs.append(fr.env)
        if envs:
            fr.env = envs[0]
            for e in envs[1:]:
                self._join_envs(fr, fr.env, e)
        else:
            fr.env = after
        for c in n.named_children:
            if c.type == "finally_clause":
                self.exec(next((x for x in c.named_children if x.type == "block"), None), fr)
        return all_term and not envs

    def _with(self, n: Node, fr: PFrame) -> bool:
        for c in n.named_children:
            if c.type == "with_clause":
                for item in c.named_children:
                    if item.type != "with_item":
                        continue
                    val_n = item.child_by_field_name("value") or (item.named_children[0] if item.named_children else None)
                    if val_n is not None and val_n.type == "as_pattern":
                        ctx = self.eval(val_n.named_children[0], fr)
                        tgt = next((x for x in val_n.named_children if x.type == "as_pattern_target"), None)
                        if tgt is not None and tgt.named_children:
                            self._bind(tgt.named_children[0], ctx, fr)
                    elif val_n is not None:
                        self.eval(val_n, fr)
        return self.exec(n.child_by_field_name("body"), fr)

    def _match(self, n: Node, fr: PFrame) -> bool:
        self.eval(n.child_by_field_name("subject"), fr)
        base = dict(fr.env)
        envs = [base]
        fr.branch += 1
        try:
            for c in n.named_children:
                if c.type == "block":
                    for case in c.named_children:
                        fr.env = dict(base)
                        for b in case.named_children:
                            if b.type == "block":
                                self.exec(b, fr)
                        envs.append(fr.env)
        finally:
            fr.branch -= 1
        fr.env = envs[0]
        for e in envs[1:]:
            self._join_envs(fr, fr.env, e)
        return False
