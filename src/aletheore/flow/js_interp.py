"""An abstract interpreter for JavaScript and TypeScript function bodies, built on tree-sitter.

Same stance as the Java and Python interpreters: it follows request data to sinks, and proves values
safe, but anything it cannot model is UNKNOWN. Callbacks passed to calls are run once with the values
they capture, because Node code puts most of its logic in them.
"""

from __future__ import annotations

import re

from functools import lru_cache

from aletheore.flow.budget import STEPS as _STEPS

from dataclasses import dataclass, field

from tree_sitter import Node

from aletheore.flow import extra_sinks as _X
from aletheore.flow import guards as G
from aletheore.flow.js_index import FN_TYPES, JsClass, JsFile, JsIndex
from aletheore.flow.js_sinks import KOA_ROOT, REQ_NAMES, REQUEST_METHODS, REQUEST_PROPS, SAFE_RESULT, SANITIZERS
from aletheore.flow.values import CallableV, add_prefix, SAFE, UNKNOWN, Choice, Const, ListV, MapV, Obj, Tainted, Val, classify, join, join_all

BENIGN = {"includes", "startsWith", "endsWith", "indexOf", "lastIndexOf", "test", "match", "has", "hasOwnProperty",
          "isArray", "isInteger", "isNaN", "isFinite", "localeCompare", "charCodeAt", "codePointAt"}
STRING_PURE = {"toLowerCase", "toUpperCase", "trim", "trimStart", "trimEnd", "toLocaleLowerCase", "toLocaleUpperCase",
               "replace", "replaceAll", "slice", "substring", "substr", "padStart", "padEnd", "repeat", "concat", "normalize", "at", "charAt"}
PURE_GLOBALS = {"Math", "Date", "JSON", "Object", "Array", "Number", "String", "Boolean", "Symbol", "Intl", "console", "process",
                "Buffer", "crypto", "util", "path", "os"}
PURE_PATH = {"join", "resolve", "basename", "dirname", "extname", "normalize", "relative", "parse", "format", "isAbsolute"}


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class FnV(CallableV):
    """A function value with the scope it was created in."""

    node: Node
    file: JsFile
    env: dict
    cls: object = None  # a method taken as a value keeps the class and the object it was read from
    this_obj: object = None
    bound: tuple = ()  # arguments fixed in advance (fn.bind(this, a, b))
    inline: bool = False  # written at the call site: it already ran as a callback when the arguments were evaluated
    lexical: bool = False  # True: the body sees only the scope it was created in, not the caller's variables


_ELEMENT_CALLBACKS = frozenset(("forEach", "map", "filter", "some", "every", "find", "findIndex", "flatMap", "then"))


def request_like(fn: Node) -> bool:
    """A function whose parameters look like an HTTP handler's (req, request or ctx)."""
    params = fn.child_by_field_name("parameters")
    if params is None:
        p = fn.child_by_field_name("parameter")
        return p is not None and _t(p) in ("req", "request", "ctx")
    for p in params.named_children:
        target = p
        if p.type in ("required_parameter", "optional_parameter"):  # TypeScript: `req: Request`
            target = p.child_by_field_name("pattern") or (p.named_children[0] if p.named_children else None)
        elif p.type == "assignment_pattern":
            target = p.child_by_field_name("left")
        if target is not None and target.type == "identifier" and _t(target) in ("req", "request", "ctx"):
            return True
    return False


@dataclass(eq=False)
class JFrame:
    file: JsFile
    cls: JsClass | None
    this_obj: Obj | None
    depth: int
    env: dict = field(default_factory=dict)
    rets: list = field(default_factory=list)
    branch: int = 0
    funcs: dict = field(default_factory=dict)
    route: bool = False


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


@lru_cache(maxsize=65536)
def _unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "'": "'", "\\": "\\", "`": "`"}.get(raw[i + 1], raw[i + 1]))
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


def _s(v: object) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _falsy(v: object) -> bool:
    return v is None or v is False or v == 0 or v == ""


def _archive_entry() -> Obj:
    """An archive entry (adm-zip, yauzl, unzipper, tar-stream): its name is chosen by whoever made the archive."""
    e = Tainted("archive.entry")
    return Obj(type_name="ArchiveEntry", fields={"entryName": e, "fileName": e, "path": e, "name": e, "linkname": e})


class JsInterp:
    MAX_STEPS = 60000
    MAX_DEPTH = 6

    def __init__(self, index: JsIndex, cwes: set[int] | None = None, sink_hook=None, store_hook=None, stash: set | None = None):
        self.index = index
        self.stash = stash if stash is not None else set()  # request properties written by one middleware and read by another
        self.cwes = cwes or set()
        self.sink_hook = sink_hook  # hook(node, name, recv_text, args, recv, frame)
        self.store_hook = store_hook  # hook(node, base_text, key, value, frame)
        self.steps = 0
        self.stored_reads = 0
        self.probes: dict[tuple, list] = {}
        self.probe_keys: set[tuple] = set()
        self.dead: list[tuple] = []
        self._active: set[int] = set()

    # ------------------------------------------------------------------ helpers
    def _tick(self) -> None:
        self.steps += 1
        _STEPS[0] += 1
        if self.steps > self.MAX_STEPS:
            raise BudgetExceeded

    def _key(self, fr: JFrame, n: Node) -> tuple:
        return (id(fr.file), n.start_byte, n.end_byte)

    def _dead(self, fr: JFrame, n: Node | None) -> None:
        if n is not None:
            self.dead.append(self._key(fr, n))

    def _sanitizer(self, name: str, recv: str = "") -> bool:
        return any(name in SANITIZERS.get(c, ()) for c in self.cwes) or _X.sanitizes("javascript", self.cwes, name, recv)

    @staticmethod
    def _combine(*vals: Val) -> Val:
        rank = {"safe": 0, "unknown": 1, "tainted": 2}
        worst = "safe"
        for v in vals:
            c = classify(v)
            if rank[c] > rank[worst]:
                worst = c
        return add_prefix({"safe": SAFE, "unknown": UNKNOWN, "tainted": Tainted("derived")}[worst], vals)

    def _mutating(self, fr: JFrame, c) -> None:
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
    def eval(self, node: Node | None, fr: JFrame) -> Val:
        if node is None:
            return UNKNOWN
        self._tick()
        val = self._eval(node, fr)
        key = self._key(fr, node) if self.probe_keys else None  # (nothing to record in a normal scan)
        if key is not None and key in self.probe_keys:
            self.probes.setdefault(key, []).append((val, classify(val)))
        return val

    def _eval(self, n: Node, fr: JFrame) -> Val:
        t = n.type
        if t in ("arrow_function", "function_expression", "function"):
            return FnV(n, fr.file, dict(fr.env))  # a closure: it keeps the variables it was created with
        if t == "string":
            return Const("".join(_unescape(_t(c)) for c in n.children if c.type in ("string_fragment", "escape_sequence")))
        if t == "template_string":
            return self._template(n, fr)
        if t == "number":
            try:
                text = _t(n).replace("_", "")
                return Const(int(text, 0) if text.lower().startswith(("0x", "0o", "0b")) else float(text) if any(c in text for c in ".eE") else int(text))
            except ValueError:
                return UNKNOWN
        if t == "true":
            return Const(True)
        if t == "false":
            return Const(False)
        if t in ("null", "undefined"):
            return Const(None)
        if t == "identifier":
            return self._name(_t(n), fr)
        if t == "this":
            return fr.this_obj or UNKNOWN
        if t == "member_expression":
            return self._member(n, fr)
        if t == "subscript_expression":
            return self._subscript(n, fr)
        if t == "call_expression":
            return self._call(n, fr)
        if t == "new_expression":
            return self._new(n, fr)
        if t in ("parenthesized_expression", "await_expression", "non_null_expression", "as_expression", "satisfies_expression", "type_assertion"):
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t == "binary_expression":
            return self._binary(n, fr)
        if t == "unary_expression":
            return self._unary(n, fr)
        if t == "ternary_expression":
            return self._ternary(n, fr)
        if t == "assignment_expression":
            return self._assign(n, fr)
        if t == "augmented_assignment_expression":
            return self._augassign(n, fr)
        if t == "update_expression":
            return SAFE
        if t == "array":
            return ListV([self.eval(c, fr) for c in n.named_children], birth_depth=fr.branch)
        if t == "object":
            return self._object(n, fr)
        if t == "spread_element":
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t in FN_TYPES or t == "class":
            return UNKNOWN
        return UNKNOWN

    def _template(self, n: Node, fr: JFrame) -> Val:
        parts: list[Val] = []
        for c in n.children:
            if c.type in ("string_fragment", "escape_sequence"):
                parts.append(Const(_unescape(_t(c))))
            elif c.type == "template_substitution":
                parts.append(self.eval(c.named_children[0], fr) if c.named_children else UNKNOWN)
        if all(isinstance(p, Const) for p in parts):
            return Const("".join(_s(p.value) for p in parts))
        return self._combine(*parts)

    def _name(self, name: str, fr: JFrame) -> Val:
        if name in fr.env and not (fr.env[name] is UNKNOWN and name in fr.file.requires):
            return fr.env[name]
        if name == "undefined":
            return Const(None)
        c = fr.file.constants.get(name)
        if c is not None:
            return self.eval(c, JFrame(fr.file, None, None, fr.depth + 1))
        req = fr.file.requires.get(name)
        if req and isinstance(req[0], str) and req[0].removeprefix("node:").startswith("path") and req[1] in ("sep", "delimiter"):
            return SAFE  # `const { sep } = require("path")`
        if name in fr.env:
            return fr.env[name]
        if name in fr.funcs:
            return FnV(fr.funcs[name], fr.file, dict(fr.env), lexical=True)
        hit = self.index.lookup(name, fr.file) if name not in fr.file.requires else None
        if hit is not None and hit[0] == "function":
            return FnV(hit[1], hit[2], {}, lexical=True)
        return UNKNOWN

    def _member(self, n: Node, fr: JFrame) -> Val:
        obj_n, prop = n.child_by_field_name("object"), _t(n.child_by_field_name("property"))
        if obj_n is not None and _t(obj_n) == "process.env" and "process" not in fr.env:
            return SAFE  # deployment configuration, not request data
        if obj_n is not None and _t(obj_n) == "path" and prop in ("sep", "delimiter") and "path" not in fr.env:
            return SAFE
        base = self.eval(obj_n, fr)
        if isinstance(base, Obj):
            if base.kind == "request":
                if prop in REQUEST_PROPS or base.type_name == "KoaContext":
                    return Tainted("request." + prop)
                if prop in self.stash:
                    return Tainted("request." + prop)
                if prop == "session":
                    return Obj(type_name="Session")
                return UNKNOWN
            if prop in base.fields:
                return base.fields[prop]
            if base.cls is not None:
                m = self._find_method(base.cls, prop)
                if m is not None:
                    return FnV(m[1], m[0].file, {}, cls=m[0], this_obj=base, lexical=True)
            return UNKNOWN
        if isinstance(base, MapV):
            if not base.dirty and not base.opaque:
                return base.entries.get(prop, Const(None))
            return join_all(list(base.entries.values()) + [UNKNOWN])
        if isinstance(base, Tainted) and base.why == "request.stored":
            return base  # a field of a record that a request stored
        if classify(base) == "tainted":
            return Tainted("derived")
        if prop == "length" and isinstance(base, (ListV, Const)):
            return SAFE
        return UNKNOWN

    def _subscript(self, n: Node, fr: JFrame) -> Val:
        base = self.eval(n.child_by_field_name("object"), fr)
        idx = self.eval(n.child_by_field_name("index"), fr)
        if isinstance(base, Obj) and base.kind == "request":
            return Tainted("request[...]")
        if isinstance(base, ListV):
            if isinstance(idx, Const) and isinstance(idx.value, (int, float)) and not isinstance(idx.value, bool) and not base.dirty and not base.opaque:
                i = int(idx.value)
                if 0 <= i < len(base.items):
                    return base.items[i]
            return join_all(base.items + [UNKNOWN]) if base.items else UNKNOWN
        if isinstance(base, MapV):
            if isinstance(idx, Const) and not base.dirty and not base.opaque and _s(idx.value) in base.entries:
                return base.entries[_s(idx.value)]
            return join_all(list(base.entries.values()) + [UNKNOWN])
        if classify(base) == "tainted":
            return Tainted("derived")
        return UNKNOWN

    def _binary(self, n: Node, fr: JFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        left_n, right_n = n.child_by_field_name("left"), n.child_by_field_name("right")
        a = self.eval(left_n, fr)
        if op in ("&&", "||", "??") and isinstance(a, Const):
            fa = _falsy(a.value) if op != "??" else a.value is not None
            if (op == "&&" and _falsy(a.value)) or (op == "||" and not _falsy(a.value)) or (op == "??" and a.value is not None):
                self._dead(fr, right_n)
                return a
            return self.eval(right_n, fr)
        b = self.eval(right_n, fr)
        if op in ("&&", "||", "??"):
            return join(a, b)
        if isinstance(a, Const) and isinstance(b, Const):
            x, y = a.value, b.value
            try:
                if op == "+":
                    if isinstance(x, str) or isinstance(y, str):
                        return Const(_s(x) + _s(y))
                    if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool) and not isinstance(y, bool):
                        return Const(x + y)
                if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool) and not isinstance(y, bool):
                    table = {"-": lambda: x - y, "*": lambda: x * y, "/": lambda: x / y, "%": lambda: x % y, "<": lambda: x < y, ">": lambda: x > y,
                             "<=": lambda: x <= y, ">=": lambda: x >= y, "==": lambda: x == y, "===": lambda: x == y, "!=": lambda: x != y, "!==": lambda: x != y}
                    if op in table and not (op in ("/", "%") and y == 0):
                        return Const(table[op]())
                if type(x) is type(y) and op in ("===", "==", "!==", "!="):
                    return Const((x == y) if op in ("===", "==") else (x != y))
            except (TypeError, ValueError, OverflowError):
                pass
            return SAFE
        return self._combine(a, b)

    def _unary(self, n: Node, fr: JFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        v = self.eval(n.child_by_field_name("argument"), fr)
        if op in ("+", "-") or op == "~":
            return SAFE  # numeric coercion
        if op == "!" and isinstance(v, Const):
            return Const(_falsy(v.value))
        if op == "typeof":
            return SAFE
        return self._combine(v)

    def _ternary(self, n: Node, fr: JFrame) -> Val:
        cond = self.eval(n.child_by_field_name("condition"), fr)
        cons_n, alt_n = n.child_by_field_name("consequence"), n.child_by_field_name("alternative")
        if isinstance(cond, Const):
            take = not _falsy(cond.value)
            self._dead(fr, alt_n if take else cons_n)
            return self.eval(cons_n if take else alt_n, fr)
        fr.branch += 1
        try:
            return join(self.eval(cons_n, fr), self.eval(alt_n, fr))
        finally:
            fr.branch -= 1

    def _object(self, n: Node, fr: JFrame) -> Val:
        m = MapV({}, birth_depth=fr.branch)
        for c in n.named_children:
            if c.type == "pair":
                k = c.child_by_field_name("key")
                key = _t(k).strip("'\"`") if k is not None and k.type != "computed_property_name" else None
                value_n = c.child_by_field_name("value")
                if value_n is not None and value_n.type in FN_TYPES:
                    if request_like(value_n):
                        self._callback(value_n, fr)
                    val = FnV(value_n, fr.file, dict(fr.env), lexical=True)
                else:
                    val = self.eval(value_n, fr)
                if key is not None:
                    m.entries[key] = val
                else:
                    m.entries[("*", id(c))] = val
                    m.dirty = True
            elif c.type == "shorthand_property_identifier":
                m.entries[_t(c)] = self._name(_t(c), fr)
            elif c.type == "spread_element":
                m.entries[("*", id(c))] = self.eval(c, fr)
                m.dirty = True
            elif c.type == "method_definition":
                m.entries[_t(c.child_by_field_name("name"))] = FnV(c, fr.file, dict(fr.env), lexical=True)
                if request_like(c):
                    self._callback(c, fr)
        return m

    # ------------------------------------------------------------------ assignment
    def _assign(self, n: Node, fr: JFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        if right is not None and right.type in FN_TYPES:
            if request_like(right):
                self._callback(right, fr)
            val = FnV(right, fr.file, dict(fr.env))
            self._bind(left, val, fr)
            return val
        val = self.eval(right, fr)
        self._bind(left, val, fr)
        return val

    def _augassign(self, n: Node, fr: JFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        op = _t(n.child_by_field_name("operator"))[:-1]
        cur = self.eval(left, fr)
        rhs = self.eval(right, fr)
        if op == "+" and isinstance(cur, Const) and isinstance(rhs, Const) and (isinstance(cur.value, str) or isinstance(rhs.value, str)):
            val = Const(_s(cur.value) + _s(rhs.value))
        else:
            val = self._combine(cur, rhs)
        self._bind(left, val, fr)
        return val

    def _bind(self, target: Node | None, val: Val, fr: JFrame) -> None:
        if target is None:
            return
        t = target.type
        if t == "identifier":
            fr.env[_t(target)] = val
        elif t == "object_pattern":
            for p in target.named_children:
                if p.type == "shorthand_property_identifier_pattern":
                    key = _t(p)
                    fr.env[key] = self._prop_of(val, key)
                elif p.type == "pair_pattern":
                    key = _t(p.child_by_field_name("key")).strip("'\"")
                    self._bind(p.child_by_field_name("value"), self._prop_of(val, key), fr)
                elif p.type == "rest_pattern" and p.named_children:
                    self._bind(p.named_children[0], self._combine(val), fr)
                elif p.type == "object_assignment_pattern":
                    left = p.child_by_field_name("left")
                    self._bind(left, self._prop_of(val, _t(left)), fr)
        elif t == "array_pattern":
            items = target.named_children
            for i, c in enumerate(items):
                if isinstance(val, ListV) and i < len(val.items) and not val.dirty and not val.opaque:
                    self._bind(c, val.items[i], fr)
                else:
                    self._bind(c, Tainted("derived") if classify(val) == "tainted" else UNKNOWN, fr)
        elif t == "member_expression":
            base_n, prop = target.child_by_field_name("object"), _t(target.child_by_field_name("property"))
            base = self.eval(base_n, fr)
            if self.store_hook is not None:
                self.store_hook(target, _t(base_n), Const(prop), val, fr)
            if isinstance(base, Obj) and base.kind == "request" and prop not in REQUEST_PROPS and classify(val) == "tainted":
                self.stash.add(prop)
            if isinstance(base, Obj):
                old = base.fields.get(prop)
                base.fields[prop] = join(old, val) if (fr.branch > 0 and old is not None) else val
            elif isinstance(base, MapV):
                self._mutating(fr, base)
                base.entries[prop] = val
        elif t == "subscript_expression":
            base_n = target.child_by_field_name("object")
            base = self.eval(base_n, fr)
            idx = self.eval(target.child_by_field_name("index"), fr)
            if self.store_hook is not None:
                self.store_hook(target, _t(base_n), idx, val, fr)
                if base_n is not None and base_n.type == "subscript_expression" and classify(idx) == "tainted" \
                        and classify(self.eval(base_n.child_by_field_name("index"), fr)) == "tainted":
                    # obj[a][b] = v with both keys from the request: a = "__proto__" writes to Object.prototype
                    self.store_hook(target, "__proto_pollution__", idx, val, fr)
            if isinstance(base, MapV):
                self._mutating(fr, base)
                if isinstance(idx, Const):
                    base.entries[_s(idx.value)] = val
                else:
                    base.entries[("*", id(target))] = val
                    base.dirty = True
            elif isinstance(base, ListV):
                self._mutating(fr, base)
                if isinstance(idx, Const) and isinstance(idx.value, (int, float)) and not isinstance(idx.value, bool) and 0 <= int(idx.value) < len(base.items) and not base.dirty:
                    base.items[int(idx.value)] = val
                else:
                    base.items.append(val)
                    base.dirty = True

    @staticmethod
    def _prop_of(val: Val, key: str) -> Val:
        if isinstance(val, MapV):
            return val.entries.get(key, UNKNOWN) if not val.dirty else join_all(list(val.entries.values()) + [UNKNOWN])
        if isinstance(val, Obj):
            if val.kind == "request":
                return Tainted("request." + key)
            return val.fields.get(key, UNKNOWN)
        return Tainted("derived") if classify(val) == "tainted" else UNKNOWN

    # ------------------------------------------------------------------ calls
    def _args(self, args_n: Node | None, fr: JFrame, cb_params: list[Val] | None = None) -> list[Val]:
        out: list[Val] = []
        for c in (args_n.named_children if args_n is not None else []):
            if c.type in FN_TYPES:
                self._callback(c, fr, cb_params)
                out.append(FnV(c, fr.file, dict(fr.env), inline=True, lexical=True))
            else:
                out.append(self.eval(c, fr))
        return out

    def _callback(self, fn: Node, fr: JFrame, params: list[Val] | None = None) -> None:
        """Run a callback once with the values it captures; its own parameters are unknown unless the caller knows them."""
        if fr.depth + 1 > self.MAX_DEPTH or id(fn) in self._active:
            return
        inner = JFrame(fr.file, fr.cls, fr.this_obj, fr.depth + 1, env=dict(fr.env), branch=fr.branch + 1, funcs=dict(fr.funcs))
        if params:
            self._bind_params(fn, params, inner)
        elif request_like(fn):
            self.bind_entry(fn, inner, route=False)  # a handler defined in this scope: the request is tainted
        else:
            self._bind_params(fn, [], inner, unknown=True)
        self._active.add(id(fn))
        try:
            self._run_body(fn, inner)
        finally:
            self._active.discard(id(fn))

    def _run_body(self, fn: Node, inner: JFrame) -> Val:
        body = fn.child_by_field_name("body")
        if body is None:
            return UNKNOWN
        if body.type == "statement_block":
            self.exec(body, inner)
            return join_all(inner.rets) if inner.rets else Const(None)
        return self.eval(body, inner)  # arrow function with an expression body

    def _bind_params(self, fn: Node, args: list[Val], inner: JFrame, unknown: bool = False) -> None:
        params = fn.child_by_field_name("parameters")
        if params is None:
            p = fn.child_by_field_name("parameter")
            if p is not None and p.type == "identifier":
                inner.env[_t(p)] = args[0] if args else UNKNOWN
            return
        pos = list(args)
        for p in params.named_children:
            t = p.type
            target = p
            default = None
            if t in ("required_parameter", "optional_parameter"):
                target = p.child_by_field_name("pattern") or (p.named_children[0] if p.named_children else None)
                default = p.child_by_field_name("value")
            elif t == "assignment_pattern":
                target, default = p.child_by_field_name("left"), p.child_by_field_name("right")
            elif t == "rest_pattern":
                if p.named_children:
                    inner.env[_t(p.named_children[0])] = ListV(list(pos), dirty=True)
                pos = []
                continue
            if target is None:
                continue
            val = pos.pop(0) if pos else (self.eval(default, inner) if default is not None else UNKNOWN)
            self._bind(target, val, inner)

    def bind_entry(self, fn: Node, fr: JFrame, route: bool) -> None:
        """Entry point: request-like parameters are the request, the second is the response."""
        params = fn.child_by_field_name("parameters")
        if route == "nest" and params is not None:
            # NestJS: `@Query('d') d: string`, `@Param()`, `@Body() body`, `@Headers()`, `@Req() req`
            for p in params.named_children:
                target = p.child_by_field_name("pattern") if p.type in ("required_parameter", "optional_parameter") else None
                if target is None or target.type != "identifier":
                    continue
                decos = [_t(c).lstrip("@").split("(")[0] for c in p.children if c.type == "decorator"]
                if any(d in ("Req", "Request") for d in decos):
                    fr.env[_t(target)] = Obj(kind="request", type_name="Request")
                elif any(d in ("Query", "Param", "Body", "Headers", "Header", "Cookies", "Ip", "HostParam", "UploadedFile", "UploadedFiles", "Payload", "MessageBody") for d in decos):
                    fr.env[_t(target)] = Tainted("request.nest")
                else:
                    fr.env[_t(target)] = UNKNOWN
            return
        if route == "trpc" and params is not None and params.named_children:
            p = params.named_children[0]
            pat = p.child_by_field_name("pattern") if p.type in ("required_parameter", "optional_parameter") else p
            if pat is not None and pat.type == "object_pattern":
                for c in pat.named_children:
                    nm = c if c.type == "shorthand_property_identifier_pattern" else c.child_by_field_name("value") if c.type == "pair_pattern" else None
                    key = _t(c) if c.type == "shorthand_property_identifier_pattern" else _t(c.child_by_field_name("key")) if c.type == "pair_pattern" else ""
                    if nm is not None and nm.type in ("identifier", "shorthand_property_identifier_pattern"):
                        fr.env[_t(nm)] = Tainted("request.trpc") if key == "input" else UNKNOWN
            elif pat is not None and pat.type == "identifier":
                fr.env[_t(pat)] = Obj(fields={"input": Tainted("request.trpc")}, type_name="TrpcOptions")
            return
        names: list[str] = []
        if params is None:
            p = fn.child_by_field_name("parameter")
            if p is not None:
                names = [_t(p)]
        else:
            for p in params.named_children:
                target = p.child_by_field_name("pattern") if p.type in ("required_parameter", "optional_parameter") else \
                    p.child_by_field_name("left") if p.type == "assignment_pattern" else p
                names.append(_t(target) if target is not None and target.type == "identifier" else "")
        for i, name in enumerate(names):
            if not name:
                continue
            if route == "lib" and name not in REQ_NAMES and name not in KOA_ROOT and name not in ("res", "response", "reply"):
                fr.env[name] = Tainted("library parameter")  # an exported function of a library: its caller chooses the arguments
            elif route == "mcp":
                fr.env[name] = Tainted("request.mcp") if i == 0 else UNKNOWN  # the tool's arguments; the second is the call context
            elif route == "next" and i == 1:  # App Router context: `params` holds the dynamic path segments
                fr.env[name] = Obj(kind="request", type_name="NextContext")
            elif name in REQ_NAMES or (route and i == 0):
                fr.env[name] = Obj(kind="request", type_name="Request")
            elif name in KOA_ROOT:
                fr.env[name] = Obj(kind="request", type_name="KoaContext")
            elif route and i == 1 or name in ("res", "response", "reply"):
                fr.env[name] = Obj(type_name="Response")
            else:
                fr.env[name] = UNKNOWN

    def _hook(self, node, name, recv_text, args, recv, fr) -> None:
        if self.sink_hook is not None:
            self.sink_hook(node, name, recv_text, args, recv, fr)

    def _call(self, n: Node, fr: JFrame) -> Val:
        func = n.child_by_field_name("function")
        args_n = n.child_by_field_name("arguments")
        if func is None:
            return UNKNOWN
        if func.type == "identifier":
            name = _t(func)
            args = self._args(args_n, fr)
            self._hook(n, name, "", args, None, fr)
            return self._call_name(name, n, args, fr)
        if func.type == "member_expression":
            obj_n, name = func.child_by_field_name("object"), _t(func.child_by_field_name("property"))
            recv_text = _t(obj_n)
            recv_pre = None
            first = args_n.named_children[0] if args_n is not None and args_n.named_children else None
            if (name == "on" and first is not None and first.type == "string" and _t(first).strip("'\"`") == "entry") \
                    or (name in ("forEach", "map", "filter", "some", "every") and recv_text.endswith("getEntries()")):
                args = self._args(args_n, fr, cb_params=[_archive_entry()])  # yauzl / unzipper / tar-stream / adm-zip entries
            elif name in _ELEMENT_CALLBACKS and first is not None and obj_n is not None:
                # arr.forEach(item => ...), promise.then(value => ...): the callback receives what the receiver holds
                recv_pre = self.eval(obj_n, fr)
                args = self._args(args_n, fr, cb_params=self._element_params(recv_pre, name))
            else:
                args = self._args(args_n, fr)
            if self._sanitizer(name, recv_text):
                return SAFE
            held = fr.env.get(_t(obj_n)) if obj_n is not None and obj_n.type == "identifier" else None
            # A name bound to require("...") is a placeholder module object: it must not hide the index.
            if obj_n is not None and obj_n.type == "identifier" and (held is None or (isinstance(held, Obj) and held.type_name == "module")):
                hit = self.index.module_member(_t(obj_n), name, fr.file)
                if hit is not None:
                    self._hook(n, name, recv_text, args, None, fr)
                    return self._run_symbol(hit, args, fr)
            recv = recv_pre if recv_pre is not None else self.eval(obj_n, fr)
            self._hook(n, name, recv_text, args, recv, fr)
            return self._method(recv, name, args, fr, recv_text, n)
        # IIFE and other call-of-expression forms
        if func.type in FN_TYPES:
            args = self._args(args_n, fr)
            return self._run_function(fr.file, func, None, None, args, fr)
        target = self.eval(func, fr)
        args = self._args(args_n, fr)
        if self._callable_part(target):
            return self._call_value(target, args, fr)
        return self._opaque(None, args)

    @staticmethod
    def _element_params(recv: Val, name: str) -> list[Val] | None:
        """What a callback's first parameter holds when it is handed an element of `recv` (or, for then, the resolved value)."""
        if isinstance(recv, ListV) and name != "then":
            return [join_all(recv.items)] if recv.items else None
        if classify(recv) == "tainted":
            return [Tainted("element" if name != "then" else "derived")]
        return None

    @staticmethod
    def _callable_part(v) -> bool:
        if isinstance(v, FnV):
            return True
        return isinstance(v, Choice) and any(isinstance(a, FnV) for a in v.alts)

    def _call_fn(self, fv: FnV, args: list[Val], fr: JFrame) -> Val:
        if fv.bound:
            args = list(fv.bound) + list(args)
        closure = fv.env if fv.lexical else {**fv.env, **fr.env}
        return self._run_function(fv.file, fv.node, fv.cls, fv.this_obj, args, fr, closure=closure)

    def _call_value(self, target: Val, args: list[Val], fr: JFrame) -> Val:
        """Call whatever a value may be: each function it can be runs with these arguments; an alternative that is not known to
        be a function is an unanalysed call."""
        alts = target.alts if isinstance(target, Choice) else [target]
        return join_all([self._call_fn(a, args, fr) if isinstance(a, FnV) else self._opaque(None, args) for a in alts])

    def _call_name(self, name: str, n: Node, args: list[Val], fr: JFrame) -> Val:
        if self._sanitizer(name):
            return SAFE
        if self._callable_part(fr.env.get(name)):
            return self._call_value(fr.env[name], args, fr)
        if name in fr.funcs:
            return self._run_function(fr.file, fr.funcs[name], None, None, args, fr, closure=fr.env)
        hit = self.index.lookup(name, fr.file)
        if hit is not None:
            return self._run_symbol(hit, args, fr)
        if name in SAFE_RESULT:
            return SAFE
        req = fr.file.requires.get(name) if fr.file is not None else None
        if req and isinstance(req[0], str) and req[0].removeprefix("node:") in ("path", "path/posix", "path/win32") and req[1] in PURE_PATH:
            return self._opaque(None, args, pure=True)  # `const { resolve } = require("path")`
        if name == "require":
            return Obj(type_name="module")
        if name in ("String", "decodeURIComponent", "decodeURI", "unescape", "atob", "btoa"):
            return self._combine(*args) if args else UNKNOWN
        if name == "Array" or name == "Object":
            return ListV(list(args), dirty=True)
        return self._opaque(None, args, pure=name in ("setTimeout", "setInterval", "clearTimeout", "console", "isNaN"))

    def _run_symbol(self, hit, args: list[Val], fr: JFrame) -> Val:
        kind, node, file = hit
        if kind == "function":
            return self._run_function(file, node, None, None, args, fr)
        cls: JsClass = node
        obj = Obj(cls=cls, type_name=cls.name)
        ctor = self._find_method(cls, "constructor")
        if ctor is not None:
            self._run_function(ctor[0].file, ctor[1], ctor[0], obj, args, fr)
        return obj

    def _find_method(self, cls: JsClass, name: str):
        seen: set[int] = set()
        cur: JsClass | None = cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if name in cur.methods:
                return cur, cur.methods[name]
            hit = self.index.lookup(cur.superclass, cur.file) if cur.superclass else None
            cur = hit[1] if hit is not None and hit[0] == "class" else None
        return None

    def _run_function(self, file: JsFile, fn: Node, cls: JsClass | None, this_obj: Obj | None, args: list[Val], fr: JFrame,
                      closure: dict | None = None) -> Val:
        if fr.depth + 1 > self.MAX_DEPTH or id(fn) in self._active:
            return self._opaque(this_obj, args)
        inner = JFrame(file, cls, this_obj, fr.depth + 1, env=dict(closure) if closure else {})
        self._bind_params(fn, args, inner)
        self._active.add(id(fn))
        try:
            return self._run_body(fn, inner)
        finally:
            self._active.discard(id(fn))

    def _new(self, n: Node, fr: JFrame) -> Val:
        ctor = n.child_by_field_name("constructor")
        name = _t(ctor).split(".")[-1]
        args = self._args(n.child_by_field_name("arguments"), fr)
        self._hook(n, name, "", args, None, fr)
        hit = self.index.lookup(name, fr.file) if ctor is not None and ctor.type == "identifier" else None
        if hit is not None and hit[0] == "class":
            return self._run_symbol(hit, args, fr)
        if hit is not None and hit[0] == "function":
            obj = Obj(type_name=name)
            self._run_function(hit[2], hit[1], None, obj, args, fr)  # constructor function: `this` is the new object
            return obj
        if name in ("Array", "Set"):
            return ListV(list(args[:1]) if args and isinstance(args[0], ListV) is False else (list(args[0].items) if args else []), dirty=True)
        if name in ("Map", "Object"):
            return MapV({}, dirty=True)
        if name in ("Date", "RegExp", "Error", "TypeError", "Promise"):
            return self._opaque(None, args, pure=True)
        return self._opaque(None, args)

    # ------------------------------------------------------------------ methods on values
    _DB_WRITES = frozenset(("create", "insertOne", "insertMany", "updateOne", "updateMany", "replaceOne", "findOneAndUpdate", "findByIdAndUpdate", "findOneAndReplace",
                            "bulkCreate", "upsert", "insert", "update", "save", "set", "add", "doc"))
    _DB_READS = frozenset(("find", "findOne", "findById", "findAll", "findByPk", "findOrCreate", "findUnique", "findFirst", "findMany", "get", "first", "all", "toArray",
                           "select", "lean", "exec", "populate"))
    _SQL_WRITE = re.compile(r"^\s*(insert|update|replace|merge|upsert)\b", re.I)

    def _storage(self, recv: Val, name: str, args: list[Val], recv_text: str) -> Val | None:
        """Second-order flows: `User.create(req.body)` / `db.run('INSERT ...', [x])` store request data; `await User.findOne(...)` / `db.get(...)` read it
        back. The store is coarse (per model name, or any) and shared with the second pass over the entry points."""
        tainted_in = any(classify(a) == "tainted" for a in args)
        head = recv_text.split(".")[0].split("(")[0]
        if name in ("run", "query", "exec", "execute", "prepare") and args:
            first = args[0]
            if isinstance(first, Const) and isinstance(first.value, str) and self._SQL_WRITE.match(first.value) and any(classify(a) == "tainted" for a in args[1:]):
                self.stash.add("db:*")
            return None
        if head[:1].isupper() or recv_text.startswith(("prisma.", "db.", "knex")):
            model = head if head[:1].isupper() else (recv_text.split(".")[1].split("(")[0] if recv_text.startswith("prisma.") and "." in recv_text else "*")
            if name in self._DB_WRITES and tainted_in:
                self.stash.add("db:" + model)
                self.stash.add("db:*")
            elif name in self._DB_READS and ("db:" + model) in self.stash:
                self.stored_reads += 1
                return Tainted("request.stored")
            return None
        if name in ("get", "all", "each") and "db:*" in self.stash and recv_text.split(".")[0] in ("db", "conn", "connection", "database", "sqlite", "stmt"):
            self.stored_reads += 1
            return Tainted("request.stored")
        return None

    def _method(self, recv: Val, name: str, args: list[Val], fr: JFrame, recv_text: str, node: Node) -> Val:
        if isinstance(recv, Choice):
            return join_all(self._method(a, name, args, fr, recv_text, node) for a in recv.alts)
        stored = self._storage(recv, name, args, recv_text)
        if stored is not None:
            return stored
        if name in _ELEMENT_CALLBACKS and args and isinstance(args[0], FnV) and not args[0].inline:
            # a named function handed to forEach / then: it receives what the receiver holds
            params = self._element_params(recv, name)
            if params is not None:
                self._call_fn(args[0], params, fr)
        if name == "getEntries" and not args:
            return ListV([_archive_entry()], birth_depth=fr.branch)  # adm-zip
        if isinstance(recv, Obj):
            if recv.kind == "request":
                if name in REQUEST_METHODS:
                    return Tainted("request." + name)
                return UNKNOWN
            if recv.cls is not None:
                m = self._find_method(recv.cls, name)
                if m is not None:
                    return self._run_function(m[0].file, m[1], m[0], recv, args, fr)
            held = recv.fields.get(name)
            if isinstance(held, FnV):
                return self._run_function(held.file, held.node, None, recv, args, fr, closure={**held.env, **fr.env})
            if self._callable_part(held):
                return self._call_value(held, args, fr)
            if recv.type_name == "Response":
                self._note_content_type(recv, name, args)
                return UNKNOWN
            if recv.type_name == "module":
                return UNKNOWN
            return self._opaque(recv, args)
        if isinstance(recv, ListV):
            return self._list_method(recv, name, args, fr)
        if isinstance(recv, MapV):
            held = recv.entries.get(name)
            if held is not None and self._callable_part(held):
                return self._call_value(held, args, fr)
            return self._map_method(recv, name, args, fr)
        if isinstance(recv, FnV) and name in ("call", "apply", "bind"):
            if name == "bind":
                return FnV(recv.node, recv.file, recv.env, recv.cls, recv.this_obj if recv.this_obj is not None else None,
                           tuple(recv.bound) + tuple(args[1:]), False, recv.lexical)
            if name == "call":
                return self._call_fn(recv, args[1:], fr)
            spread = args[1].items if len(args) > 1 and isinstance(args[1], ListV) else [UNKNOWN]
            return self._call_fn(recv, list(spread), fr)
        if isinstance(recv, Const) and isinstance(recv.value, str):
            return self._str_method(recv, name, args)
        if name in BENIGN or name in SAFE_RESULT:
            return SAFE
        if classify(recv) == "tainted":
            return Tainted("derived")
        if recv_text == "Object" and name in ("keys", "values", "entries") and args:
            return ListV([Tainted("derived")] if classify(args[0]) == "tainted" else [UNKNOWN], dirty=True)
        if recv_text.split(".")[-1] in ("fs", "promises", "fsPromises", "fse") and (
                name.startswith("read") or name in ("stat", "statSync", "lstat", "lstatSync", "existsSync", "exists", "access",
                                                    "accessSync", "realpath", "realpathSync", "opendir")):
            return UNKNOWN  # what a file contains is not request data, even when its path was
        pure = recv_text in PURE_GLOBALS or (recv_text.split(".")[-1] == "path" and name in PURE_PATH)
        return self._opaque(recv if not pure else None, args, pure=pure)

    @staticmethod
    def _note_content_type(res: Obj, name: str, args: list[Val]) -> None:
        """Remember an explicit Content-Type set on a response, so a write to it is judged by it."""
        def const(i: int):
            return args[i].value if len(args) > i and isinstance(args[i], Const) and isinstance(args[i].value, str) else None
        ctype = None
        if name in ("setHeader", "set", "header", "append") and (const(0) or "").lower() == "content-type":
            ctype = const(1)
        elif name in ("type", "contentType") and const(0):
            ctype = const(0)
        elif name == "writeHead" and len(args) > 1 and isinstance(args[-1], MapV):
            for k, v in args[-1].entries.items():
                if isinstance(k, str) and k.lower() == "content-type" and isinstance(v, Const) and isinstance(v.value, str):
                    ctype = v.value
        if ctype is not None:
            res.fields["_ctype"] = Const(ctype.lower())

    def _str_method(self, recv: Const, name: str, args: list[Val]) -> Val:
        s = recv.value
        consts = all(isinstance(a, Const) for a in args)
        if name in BENIGN:
            return SAFE
        if consts:
            vals = [a.value for a in args]
            try:
                if name == "toLowerCase":
                    return Const(s.lower())
                if name == "toUpperCase":
                    return Const(s.upper())
                if name in ("trim", "trimStart", "trimEnd"):
                    return Const(s.strip())
                if name in ("replace", "replaceAll") and len(vals) == 2 and isinstance(vals[0], str) and isinstance(vals[1], str):
                    return Const(s.replace(vals[0], vals[1], 1 if name == "replace" else -1))
                if name == "concat":
                    return Const(s + "".join(_s(v) for v in vals))
                if name in ("slice", "substring", "substr") and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
                    ints = [int(v) for v in vals]
                    return Const(s[ints[0]:ints[1]] if len(ints) > 1 else s[ints[0]:])
                if name == "split" and vals and isinstance(vals[0], str):
                    return ListV([Const(x) for x in s.split(vals[0])], birth_depth=0)
                if name == "charAt" and vals and isinstance(vals[0], int):
                    return Const(s[vals[0]] if 0 <= vals[0] < len(s) else "")
                if name == "repeat" and vals and isinstance(vals[0], int):
                    return Const(s * vals[0])
                if name in ("toString", "valueOf"):
                    return recv
            except (TypeError, ValueError, IndexError):
                return SAFE
            return SAFE
        if name in STRING_PURE or name in ("toString", "valueOf", "split"):
            return self._combine(recv, *args)
        return self._combine(recv, *args)

    def _list_method(self, lst: ListV, name: str, args: list[Val], fr: JFrame) -> Val:
        if lst.opaque and name not in ("push", "unshift", "pop", "shift", "splice", "concat"):
            return join_all(lst.items + [UNKNOWN])
        if name in ("push", "unshift"):
            self._mutating(fr, lst)
            if name == "push":
                lst.items.extend(args)
            else:
                lst.items[0:0] = args
            return SAFE
        if name in ("pop", "shift"):
            self._mutating(fr, lst)
            if lst.items and not lst.dirty:
                return lst.items.pop(-1 if name == "pop" else 0)
            lst.dirty = True
            return join_all(lst.items + [UNKNOWN])
        if name == "concat":
            items = list(lst.items)
            for a in args:
                items.extend(a.items if isinstance(a, ListV) else [a])
            return ListV(items, birth_depth=fr.branch, dirty=lst.dirty)
        if name == "join":
            if all(isinstance(i, Const) for i in lst.items) and not lst.dirty and not lst.opaque and (not args or isinstance(args[0], Const)):
                sep = _s(args[0].value) if args else ","
                return Const(sep.join(_s(i.value) for i in lst.items))
            return self._combine(*lst.items, *args)
        if name in ("slice", "filter", "reverse", "sort", "flat", "map", "flatMap"):
            return ListV(list(lst.items), birth_depth=fr.branch, dirty=True)
        if name in BENIGN or name in ("includes", "indexOf", "some", "every", "forEach"):
            return SAFE
        if name in ("find", "at"):
            return join_all(lst.items + [UNKNOWN])
        if name == "splice":
            self._mutating(fr, lst)
            lst.dirty = True
            return ListV(list(lst.items), dirty=True)
        return self._opaque(lst, args)

    def _map_method(self, m: MapV, name: str, args: list[Val], fr: JFrame) -> Val:
        if name in ("get", "getOwnProperty") and args and isinstance(args[0], Const) and not m.dirty and not m.opaque:
            return m.entries.get(_s(args[0].value), Const(None))
        if name in BENIGN or name in ("has", "hasOwnProperty"):
            return SAFE
        if name == "set" and len(args) == 2:
            self._mutating(fr, m)
            if isinstance(args[0], Const):
                m.entries[_s(args[0].value)] = args[1]
            else:
                m.dirty = True
            return m
        return self._opaque(m, args)

    # ------------------------------------------------------------------ statements
    def exec(self, n: Node | None, fr: JFrame) -> bool:
        if n is None:
            return False
        self._tick()
        t = n.type
        if t == "statement_block" or t == "program":
            for c in n.named_children:  # hoist function declarations
                if c.type in ("function_declaration", "generator_function_declaration"):
                    fr.funcs[_t(c.child_by_field_name("name"))] = c
            for c in n.named_children:
                if self.exec(c, fr):
                    return True
            return False
        if t in ("lexical_declaration", "variable_declaration"):
            for d in n.named_children:
                if d.type == "variable_declarator":
                    value_n = d.child_by_field_name("value")
                    if value_n is not None and value_n.type in ("arrow_function", "function_expression", "function"):
                        name_n = d.child_by_field_name("name")
                        if name_n is not None and name_n.type == "identifier":
                            fr.funcs[_t(name_n)] = value_n
                            fr.env[_t(name_n)] = FnV(value_n, fr.file, dict(fr.env))
                        if request_like(value_n):
                            self._callback(value_n, fr)
                        continue
                    val = self.eval(value_n, fr) if value_n is not None else Const(None)
                    self._bind(d.child_by_field_name("name"), val, fr)
            return False
        if t == "expression_statement":
            for c in n.named_children:
                self.eval(c, fr)
            return False
        if t == "return_statement":
            fr.rets.append(self.eval(n.named_children[0], fr) if n.named_children else Const(None))
            return True
        if t == "throw_statement":
            for c in n.named_children:
                self.eval(c, fr)
            return True
        if t == "if_statement":
            return self._if(n, fr)
        if t in ("for_statement", "while_statement", "do_statement", "for_in_statement"):
            return self._loop(n, fr)
        if t == "switch_statement":
            return self._switch(n, fr)
        if t == "try_statement":
            return self._try(n, fr)
        if t == "labeled_statement":
            return self.exec(n.named_children[-1], fr) if n.named_children else False
        if t in ("function_declaration", "generator_function_declaration"):
            fr.funcs[_t(n.child_by_field_name("name"))] = n
            return False
        if t in ("class_declaration", "import_statement", "export_statement", "break_statement", "continue_statement",
                 "empty_statement", "comment", "interface_declaration", "type_alias_declaration", "enum_declaration"):
            if t == "export_statement":
                for c in n.named_children:
                    if c.type in ("function_declaration", "lexical_declaration", "class_declaration"):
                        self.exec(c, fr)
            return False
        for c in n.named_children:
            if c.type.endswith("statement") or c.type == "statement_block":
                self.exec(c, fr)
            else:
                self.eval(c, fr)
        return False

    def _join_envs(self, fr: JFrame, a: dict, b: dict) -> None:
        out = {}
        for k in set(a) | set(b):
            out[k] = join(a[k], b[k]) if (k in a and k in b) else (a[k] if k in a else b[k])
        fr.env = out

    # ------------------------------------------------------------------ guards
    def _engine(self, fr: JFrame) -> G.GuardEngine:
        def norm(n):
            t = n.type
            if t == "parenthesized_expression":
                return ("paren", n.named_children[0] if n.named_children else None)
            if t == "unary_expression" and _t(n.child_by_field_name("operator")) == "!":
                return ("not", n.child_by_field_name("argument"))
            if t == "binary_expression":
                op = _t(n.child_by_field_name("operator"))
                l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
                return {"&&": ("and", l, r), "||": ("or", l, r), "===": ("eq", l, r), "==": ("eq", l, r), "!==": ("ne", l, r), "!=": ("ne", l, r)}.get(op, ("other",))
            if t == "call_expression":
                fn = n.child_by_field_name("function")
                args_n = n.child_by_field_name("arguments")
                args = list(args_n.named_children) if args_n is not None else []
                if fn is not None and fn.type == "member_expression":
                    return ("call", _t(fn.child_by_field_name("property")), fn.child_by_field_name("object"), args, _t(fn.child_by_field_name("object")))
                return ("call", _t(fn), None, args, "")
            return ("other",)

        def local(n):
            return _t(n) if n is not None and n.type == "identifier" and _t(n) in fr.env else None

        def const(n):
            if n is None:
                return None
            if n.type == "regex":
                return _t(n)
            v = self.eval(n, fr)
            return v.value if isinstance(v, Const) and isinstance(v.value, str) else None

        def safe(n):
            return classify(self.eval(n, fr)) == "safe"

        def coll(n):
            v = self.eval(n, fr)
            return isinstance(v, ListV) and bool(v.items) and not v.dirty and not v.opaque and all(classify(i) == "safe" for i in v.items)

        def pattern_source(n):
            if n is not None and n.type == "regex":
                return _t(n)
            if n is not None and n.type == "identifier" and fr.file is not None:
                init = fr.file.constants.get(_t(n))
                if init is not None and init.type == "regex":
                    return _t(init)
            return None

        def attr(n):
            if n is None or n.type != "member_expression":
                return None
            obj, prop = n.child_by_field_name("object"), _t(n.child_by_field_name("property"))
            if local(obj):
                return (local(obj), prop)
            if obj is not None and obj.type == "new_expression":  # new URL(x).hostname
                a = obj.child_by_field_name("arguments")
                first = a.named_children[0] if a is not None and a.named_children else None
                if first is not None and local(first):
                    return (local(first), prop)
            return None

        return G.GuardEngine(norm, local, const, safe, coll, pattern_source, self.cwes, attr=attr,
                             canon=lambda var: G.canonical_assignment(fr.file.src, var, r"\bresolve\("),
                             untainted=lambda n: n is not None and classify(self.eval(n, fr)) != "tainted")

    def _narrow(self, names: set, fr: JFrame) -> None:
        G.apply(names, self.cwes, fr.env, SAFE)

    def _if(self, n: Node, fr: JFrame) -> bool:
        cond_n = n.child_by_field_name("condition")
        cons_n = n.child_by_field_name("consequence")
        alt = n.child_by_field_name("alternative")
        alt_n = alt.named_children[0] if alt is not None and alt.named_children else None
        cond = self.eval(cond_n, fr)
        if isinstance(cond, Const):
            take = not _falsy(cond.value)
            self._dead(fr, alt_n if take else cons_n)
            if take:
                return self.exec(cons_n, fr)
            return self.exec(alt_n, fr) if alt_n is not None else False
        base = dict(fr.env)
        facts = self._engine(fr).facts(cond_n)
        fr.branch += 1
        try:
            self._narrow(facts[0], fr)
            term_a = self.exec(cons_n, fr)
            env_a = fr.env
            fr.env = dict(base)
            self._narrow(facts[1], fr)
            term_b = self.exec(alt_n, fr) if alt_n is not None else False
            env_b = fr.env
        finally:
            fr.branch -= 1
        if term_a and term_b:
            fr.env = env_a
            return True
        if term_a:
            fr.env = env_b
        elif term_b:
            fr.env = env_a
        else:
            self._join_envs(fr, env_a, env_b)
        return False

    def _loop(self, n: Node, fr: JFrame) -> bool:
        base = dict(fr.env)
        if n.type == "for_in_statement":
            it = self.eval(n.child_by_field_name("right"), fr)
            is_of = any(c.type == "of" or _t(c) == "of" for c in n.children if not c.is_named)
            if isinstance(it, ListV) and not it.items and not it.dirty and not it.opaque:
                return False
            if is_of and isinstance(it, ListV):
                elem = join_all(it.items + ([UNKNOWN] if it.dirty or it.opaque else []))
            elif is_of and classify(it) == "tainted":
                elem = Tainted("element")
            elif not is_of and classify(it) == "tainted":
                elem = Tainted("derived")  # keys of a request object are attacker chosen
            elif not is_of and isinstance(it, MapV):
                elem = join_all([Const(k) for k in it.entries if not isinstance(k, tuple)] + [UNKNOWN])
            else:
                elem = UNKNOWN
            fr.branch += 1
            try:
                self._bind(n.child_by_field_name("left"), elem, fr)
                self.exec(n.child_by_field_name("body"), fr)
            finally:
                fr.branch -= 1
        else:
            if n.type == "for_statement":
                init = n.child_by_field_name("initializer")
                if init is not None:
                    self.exec(init, fr) if init.type.endswith(("declaration", "statement")) else self.eval(init, fr)
            cond_n = n.child_by_field_name("condition")
            cond = self.eval(cond_n, fr) if cond_n is not None and n.type != "do_statement" else UNKNOWN
            if isinstance(cond, Const) and _falsy(cond.value) and n.type != "do_statement":
                self._dead(fr, n.child_by_field_name("body"))
                return False
            fr.branch += 1
            try:
                self.exec(n.child_by_field_name("body"), fr)
            finally:
                fr.branch -= 1
        if n.type != "do_statement":
            self._join_envs(fr, base, fr.env)
        return False

    def _switch(self, n: Node, fr: JFrame) -> bool:
        sel = self.eval(n.child_by_field_name("value"), fr)
        body = n.child_by_field_name("body")
        groups = [c for c in (body.named_children if body is not None else []) if c.type in ("switch_case", "switch_default")]
        base = dict(fr.env)
        envs = []
        has_default = any(g.type == "switch_default" for g in groups)
        fr.branch += 1
        try:
            for i in range(len(groups)):
                fr.env = dict(base)
                for j in range(i, len(groups)):
                    ended = False
                    for s in [c for c in groups[j].named_children if c is not groups[j].child_by_field_name("value")]:
                        if s.type == "break_statement":
                            ended = True
                            break
                        if self.exec(s, fr):
                            ended = True
                            break
                    if ended:
                        break
                envs.append(fr.env)
        finally:
            fr.branch -= 1
        if not has_default:
            envs.append(base)
        if envs:
            fr.env = envs[0]
            for e in envs[1:]:
                self._join_envs(fr, fr.env, e)
        return False

    def _try(self, n: Node, fr: JFrame) -> bool:
        base = dict(fr.env)
        term = self.exec(n.child_by_field_name("body"), fr)
        after = fr.env
        envs = [] if term else [after]
        all_term = term
        handler = n.child_by_field_name("handler")
        if handler is not None:
            fr.env = {k: join(base.get(k, v), v) if k in base else v for k, v in after.items()}
            param = handler.child_by_field_name("parameter")
            if param is not None and param.type == "identifier":
                fr.env[_t(param)] = UNKNOWN
            fr.branch += 1
            try:
                cterm = self.exec(handler.child_by_field_name("body"), fr)
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
        fin = n.child_by_field_name("finalizer")
        if fin is not None:
            self.exec(fin.child_by_field_name("body"), fr)
        return all_term and not envs
