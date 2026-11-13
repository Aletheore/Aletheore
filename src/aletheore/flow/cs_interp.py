"""An abstract interpreter for C#, built on tree-sitter. Same stance as the other interpreters: follow request
data to sinks and prove values safe; anything it cannot model is UNKNOWN. C# specifics: ASP.NET request objects,
class constants and `static readonly` fields, property-assignment sinks (`cmd.CommandText = ...`), `using`
blocks, string interpolation and `string.Format`."""

from __future__ import annotations

from functools import lru_cache

from aletheore.flow.budget import STEPS as _STEPS

from dataclasses import dataclass, field

from tree_sitter import Node

from aletheore.flow import extra_sinks as _X
from aletheore.flow import guards as G
from aletheore.flow.cs_index import CsClass, CsFile, CsIndex
from aletheore.flow.cs_sinks import PURE_TYPES, REQUEST_PROPS, SAFE_METHODS, SANITIZERS
from aletheore.flow.values import (
    ClosureV, FuncV, add_prefix, SAFE, UNKNOWN, BuilderV, Choice, Const, ListV, MapV, Obj, Tainted, Val, classify, join, join_all,
)

STRING_PURE = {"ToLower", "ToUpper", "ToLowerInvariant", "ToUpperInvariant", "Trim", "TrimStart", "TrimEnd", "Replace", "Substring", "PadLeft", "PadRight",
               "Remove", "Insert", "Normalize", "ToString", "Concat", "Format", "Join", "Split", "Clone"}
BENIGN = {"Contains", "StartsWith", "EndsWith", "Equals", "IndexOf", "LastIndexOf", "CompareTo", "Compare", "Any", "All", "GetHashCode", "IsMatch", "Length", "Count"}


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class CFrame:
    file: CsFile
    cls: CsClass | None
    this_obj: Obj | None
    depth: int
    env: dict = field(default_factory=dict)
    rets: list = field(default_factory=list)
    branch: int = 0


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _s(v: object) -> str:
    return "" if v is None else ("True" if v is True else "False" if v is False else str(v))


@lru_cache(maxsize=65536)
def _unquote(raw: str) -> str:
    if raw.startswith('@"') or raw.startswith('$@"') or raw.startswith('@$"'):
        return raw[raw.index('"') + 1:-1].replace('""', '"')
    if raw.startswith('"""'):
        return raw.strip('"')
    body = raw[1:-1] if raw.startswith('"') else raw
    out, i = [], 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'", "0": "\0"}.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def _declarator_value(d: Node) -> tuple[str, Node | None]:
    kids = d.named_children
    name = _t(kids[0]) if kids else ""
    val = kids[-1] if len(kids) > 1 else None
    if val is not None and val.type == "equals_value_clause" and val.named_children:
        val = val.named_children[-1]
    return name, val


def _zip_archive(fr) -> Obj:
    """A zip archive: each entry's FullName/Name is chosen by whoever made the archive (zip slip)."""
    e = Tainted("archive.entry")
    entry = Obj(type_name="ZipArchiveEntry", fields={"FullName": e, "Name": e})
    return Obj(type_name="ZipArchive", fields={"Entries": ListV([entry], birth_depth=fr.branch)})


class CsInterp:
    MAX_STEPS = 60000
    MAX_DEPTH = 6

    def __init__(self, index: CsIndex, cwes: set[int] | None = None, sink_hook=None, store_hook=None):
        self.index = index
        self.cwes = cwes or set()
        self.sink_hook = sink_hook    # hook(node, name, recv_text, args, recv, frame)
        self.store_hook = store_hook  # hook(node, base_text, key, value, frame)
        self.steps = 0
        self._outs: list | None = None
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

    def _key(self, fr: CFrame, n: Node) -> tuple:
        return (id(fr.file), n.start_byte, n.end_byte)

    def _dead(self, fr: CFrame, n: Node | None) -> None:
        if n is not None:
            self.dead.append(self._key(fr, n))

    def _sanitizer(self, name: str, recv: str = "") -> bool:
        return any(name in SANITIZERS.get(c, ()) for c in self.cwes) or _X.sanitizes("csharp", self.cwes, name, recv)

    @staticmethod
    def _combine(*vals: Val) -> Val:
        rank = {"safe": 0, "unknown": 1, "tainted": 2}
        worst = "safe"
        for v in vals:
            c = classify(v)
            if rank[c] > rank[worst]:
                worst = c
        return add_prefix({"safe": SAFE, "unknown": UNKNOWN, "tainted": Tainted("derived")}[worst], vals)

    def _mutating(self, fr: CFrame, c) -> None:
        if fr.branch > c.birth_depth:
            c.dirty = True

    @staticmethod
    def _opaque(recv: Val | None, args: list[Val], pure: bool = False) -> Val:
        vals = ([recv] if recv is not None else []) + list(args)
        if not pure:
            for v in vals:
                if isinstance(v, (ListV, MapV, BuilderV)):
                    v.opaque = True
        classes = [classify(v) for v in vals]
        if "tainted" in classes:
            return Tainted("through-call")
        if all(c == "safe" for c in classes) and (pure or recv is not None):
            return SAFE
        return UNKNOWN

    # ------------------------------------------------------------------ expressions
    def eval(self, node: Node | None, fr: CFrame) -> Val:
        if node is None:
            return UNKNOWN
        self._tick()
        val = self._eval(node, fr)
        key = self._key(fr, node) if self.probe_keys else None  # (nothing to record in a normal scan)
        if key is not None and key in self.probe_keys:
            self.probes.setdefault(key, []).append((val, classify(val)))
        return val

    def _eval(self, n: Node, fr: CFrame) -> Val:
        t = n.type
        if t == "tuple_expression":
            return ListV([self.eval(c.named_children[-1] if c.type == "argument" and c.named_children else c, fr) for c in n.named_children], birth_depth=fr.branch)
        if t in ("string_literal", "verbatim_string_literal", "raw_string_literal"):
            return Const(_unquote(_t(n)))
        if t == "character_literal":
            return Const(_unquote('"' + _t(n)[1:-1] + '"'))
        if t == "integer_literal":
            try:
                return Const(int(_t(n).replace("_", "").rstrip("uUlL"), 0))
            except ValueError:
                return UNKNOWN
        if t == "real_literal":
            try:
                return Const(float(_t(n).rstrip("fFdDmM")))
            except ValueError:
                return UNKNOWN
        if t == "boolean_literal":
            return Const(_t(n) == "true")
        if t == "null_literal":
            return Const(None)
        if t == "interpolated_string_expression":
            return self._interpolated(n, fr)
        if t == "identifier":
            return self._lookup(_t(n), fr)
        if t in ("this_expression", "this", "base", "base_expression"):
            return fr.this_obj or UNKNOWN
        if t == "parenthesized_expression":
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t == "member_access_expression":
            return self._member(n, fr)
        if t == "element_access_expression":
            return self._element(n, fr)
        if t == "invocation_expression":
            return self._invoke(n, fr)
        if t == "object_creation_expression":
            return self._new(n, fr)
        if t == "binary_expression":
            return self._binary(n, fr)
        if t in ("prefix_unary_expression", "postfix_unary_expression"):
            v = self.eval(n.named_children[-1], fr) if n.named_children else UNKNOWN
            op = _t(n.children[0]) if t == "prefix_unary_expression" and n.children else ""
            if op == "!" and isinstance(v, Const) and isinstance(v.value, bool):
                return Const(not v.value)
            return SAFE if op in ("-", "+", "~", "++", "--") or t == "postfix_unary_expression" else self._combine(v)
        if t == "conditional_expression":
            return self._ternary(n, fr)
        if t == "assignment_expression":
            return self._assign(n, fr)
        if t == "cast_expression":
            return self.eval(n.child_by_field_name("value") or (n.named_children[-1] if n.named_children else None), fr)
        if t in ("as_expression", "await_expression", "checked_expression", "suppress_nullable_warning_expression"):
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t in ("array_creation_expression", "implicit_array_creation_expression", "initializer_expression", "collection_expression"):
            return self._array(n, fr)
        if t in ("lambda_expression", "anonymous_method_expression"):
            return ClosureV(n, fr.file, dict(fr.env))
        if t in ("is_expression", "is_pattern_expression", "typeof_expression", "sizeof_expression", "default_expression", "nameof_expression"):
            return UNKNOWN if t in ("lambda_expression", "anonymous_method_expression") else SAFE
        return UNKNOWN

    def _interpolated(self, n: Node, fr: CFrame) -> Val:
        parts: list[Val] = []
        for c in n.children:
            if c.type == "string_content":
                parts.append(Const(_t(c)))
            elif c.type == "interpolation":
                expr = next((x for x in c.named_children if x.type not in ("interpolation_brace", "interpolation_alignment_clause", "interpolation_format_clause")), None)
                parts.append(self.eval(expr, fr))
        if all(isinstance(p, Const) for p in parts):
            return Const("".join(_s(p.value) for p in parts))
        return self._combine(*parts)

    def _array(self, n: Node, fr: CFrame) -> Val:
        items = []
        for c in n.named_children:
            if c.type in ("initializer_expression", "array_rank_specifier", "array_type"):
                if c.type == "initializer_expression":
                    items.extend(self.eval(x, fr) for x in c.named_children)
            elif c.type not in ("predefined_type", "identifier_name", "generic_name"):
                items.append(self.eval(c, fr))
        return ListV(items, birth_depth=fr.branch)

    def _lookup(self, name: str, fr: CFrame) -> Val:
        if name in fr.env:
            return fr.env[name]
        if fr.this_obj is not None and name in fr.this_obj.fields:
            return fr.this_obj.fields[name]
        c = fr.cls
        while c is not None:
            if name in c.consts and c.consts[name] is not None:
                return self._const_value(c, name, fr)
            c = self.index.cls(c.bases[0], c.file) if c.bases else None
        if name in ("RouteData", "RouteValues"):
            return Obj(kind="request", type_name="Request")
        if name == "Request" or name == "request":
            return Obj(kind="request", type_name="Request")
        if name in ("HttpContext", "Context"):
            return Obj(type_name="HttpContext")
        if fr.cls is not None:  # a method group: Action<string> f = Sink;
            hit = self._find_method_any(fr.cls, name)
            if hit is not None:
                return FuncV(hit[1], hit[0].file, hit[0], fr.this_obj, name=name)
        return UNKNOWN

    def _find_method_any(self, cls: CsClass, name: str):
        seen: set[int] = set()
        cur: CsClass | None = cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if cur.methods.get(name):
                return cur, cur.methods[name][0]
            cur = self.index.cls(cur.bases[0], cur.file) if cur.bases else None
        return None

    @staticmethod
    def _callable_part(v) -> bool:
        return isinstance(v, FuncV) or (isinstance(v, Choice) and any(isinstance(a, (FuncV, ClosureV)) for a in v.alts))

    def _call_value(self, target: Val, args: list[Val], fr: CFrame) -> Val:
        alts = target.alts if isinstance(target, Choice) else [target]
        outs = []
        for a in alts:
            if isinstance(a, FuncV):
                m = next((x for x in a.cls.methods.get(a.name, []) if self._arity(x) == len(args)), a.node)
                outs.append(self._run_method(a.cls, m, a.self_obj, list(args), fr))
            elif isinstance(a, ClosureV):
                outs.append(self._call_closure(a, args, fr))
            else:
                outs.append(self._opaque(None, args))
        return join_all(outs)

    def _const_value(self, cls: CsClass, name: str, fr: CFrame) -> Val:
        init = cls.consts.get(name)
        if init is None or id(init) in self._active or fr.depth > self.MAX_DEPTH:
            return UNKNOWN
        self._active.add(id(init))
        try:
            v = self.eval(init, CFrame(cls.file, cls, None, fr.depth + 1))
        finally:
            self._active.discard(id(init))
        return v if classify(v) == "safe" else UNKNOWN

    def _member(self, n: Node, fr: CFrame) -> Val:
        expr, name_n = n.child_by_field_name("expression"), n.child_by_field_name("name")
        name = _t(name_n)
        if expr is not None and expr.type == "identifier":
            cname = _t(expr)
            if cname not in fr.env and not (fr.this_obj and cname in fr.this_obj.fields):
                cls = self.index.cls(cname, fr.file)
                if cls is not None and name in cls.consts:
                    return self._const_value(cls, name, fr)
                if cname in ("string", "String") and name == "Empty":
                    return Const("")
                if cname in ("Environment", "DateTime", "TimeSpan", "Guid", "Math", "Path", "int", "Int32", "long", "double") or name in ("MaxValue", "MinValue", "Empty", "Now", "UtcNow"):
                    return SAFE
        base = self.eval(expr, fr)
        if isinstance(base, Obj):
            if base.kind == "request":
                return Tainted("request." + name) if name in REQUEST_PROPS or base.type_name == "Request" else UNKNOWN
            if base.type_name == "HttpContext" and name == "Request":
                return Obj(kind="request", type_name="Request")
            if name in base.fields:
                return base.fields[name]
            if base.cls is not None:
                hit = self._find_method_any(base.cls, name)
                if hit is not None:
                    return FuncV(hit[1], hit[0].file, hit[0], base, name=name)
            return UNKNOWN
        if classify(base) == "tainted":
            return Tainted("derived")
        if name in ("Length", "Count") and isinstance(base, (ListV, Const, MapV)):
            return SAFE
        return UNKNOWN

    def _element(self, n: Node, fr: CFrame) -> Val:
        base = self.eval(n.child_by_field_name("expression"), fr)
        sub = n.child_by_field_name("subscript")
        arg = sub.named_children[0].named_children[-1] if sub is not None and sub.named_children and sub.named_children[0].named_children else None
        idx = self.eval(arg, fr) if arg is not None else UNKNOWN
        if isinstance(base, Obj) and base.kind == "request":
            return Tainted("request[...]")
        if isinstance(base, ListV):
            if isinstance(idx, Const) and isinstance(idx.value, int) and not isinstance(idx.value, bool) and not base.dirty and not base.opaque and 0 <= idx.value < len(base.items):
                return base.items[idx.value]
            return join_all(base.items + [UNKNOWN]) if base.items else UNKNOWN
        if isinstance(base, MapV):
            if isinstance(idx, Const) and not base.dirty and not base.opaque and idx.value in base.entries:
                return base.entries[idx.value]
            return join_all(list(base.entries.values()) + [UNKNOWN])
        if classify(base) == "tainted":
            return Tainted("request[...]") if isinstance(base, Tainted) and base.why.startswith("request") else Tainted("derived")
        return UNKNOWN

    def _binary(self, n: Node, fr: CFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        left_n, right_n = n.child_by_field_name("left"), n.child_by_field_name("right")
        a = self.eval(left_n, fr)
        if op in ("&&", "||") and isinstance(a, Const) and isinstance(a.value, bool):
            if (op == "&&" and not a.value) or (op == "||" and a.value):
                self._dead(fr, right_n)
                return a
            return self.eval(right_n, fr)
        if op == "??" and isinstance(a, Const):
            if a.value is not None:
                self._dead(fr, right_n)
                return a
            return self.eval(right_n, fr)
        b = self.eval(right_n, fr)
        if op in ("&&", "||", "??"):
            return join(a, b)
        if isinstance(a, Const) and isinstance(b, Const):
            x, y = a.value, b.value
            try:
                if op == "+" and (isinstance(x, str) or isinstance(y, str)):
                    return Const(_s(x) + _s(y))
                num = (int, float)
                if isinstance(x, num) and isinstance(y, num) and not isinstance(x, bool) and not isinstance(y, bool):
                    table = {"+": lambda: x + y, "-": lambda: x - y, "*": lambda: x * y, "/": lambda: x // y if isinstance(x, int) and isinstance(y, int) else x / y,
                             "%": lambda: x % y, "<": lambda: x < y, ">": lambda: x > y, "<=": lambda: x <= y, ">=": lambda: x >= y, "==": lambda: x == y, "!=": lambda: x != y}
                    if op in table and not (op in ("/", "%") and y == 0):
                        return Const(table[op]())
                if op in ("==", "!=") and type(x) is type(y):
                    return Const((x == y) if op == "==" else (x != y))
            except (TypeError, ValueError, OverflowError):
                pass
            return SAFE
        if op in ("==", "!=", "<", ">", "<=", ">=", "is", "as"):
            return SAFE
        return self._combine(a, b)

    def _ternary(self, n: Node, fr: CFrame) -> Val:
        cond = self.eval(n.child_by_field_name("condition"), fr)
        cons_n, alt_n = n.child_by_field_name("consequence"), n.child_by_field_name("alternative")
        if isinstance(cond, Const) and isinstance(cond.value, bool):
            self._dead(fr, alt_n if cond.value else cons_n)
            return self.eval(cons_n if cond.value else alt_n, fr)
        fr.branch += 1
        try:
            return join(self.eval(cons_n, fr), self.eval(alt_n, fr))
        finally:
            fr.branch -= 1

    def _assign(self, n: Node, fr: CFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        op = _t(n.child_by_field_name("operator")) or "="
        val = self.eval(right, fr)
        if op != "=":
            cur = self.eval(left, fr)
            if op == "+=" and isinstance(cur, Const) and isinstance(val, Const) and (isinstance(cur.value, str) or isinstance(val.value, str)):
                val = Const(_s(cur.value) + _s(val.value))
            else:
                val = self._combine(cur, val)
        self._store(left, val, fr)
        return val

    def _store(self, target: Node | None, val: Val, fr: CFrame) -> None:
        if target is None:
            return
        t = target.type
        if t == "tuple_expression":
            self._deconstruct(target, val, fr)
        elif t == "identifier":
            name = _t(target)
            if name in fr.env or not (fr.this_obj is not None and name in fr.this_obj.fields):
                fr.env[name] = val
            else:
                old = fr.this_obj.fields.get(name)
                fr.this_obj.fields[name] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "member_access_expression":
            expr, name = target.child_by_field_name("expression"), _t(target.child_by_field_name("name"))
            base = self.eval(expr, fr)
            if self.store_hook is not None:
                self.store_hook(target, _t(expr), Const(name), val, fr)
            if isinstance(base, Obj):
                old = base.fields.get(name)
                base.fields[name] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "element_access_expression":
            expr = target.child_by_field_name("expression")
            base = self.eval(expr, fr)
            sub = target.child_by_field_name("subscript")
            arg = sub.named_children[0].named_children[-1] if sub is not None and sub.named_children and sub.named_children[0].named_children else None
            idx = self.eval(arg, fr) if arg is not None else UNKNOWN
            if self.store_hook is not None:
                self.store_hook(target, _t(expr), idx, val, fr)
            if isinstance(base, MapV):
                self._mutating(fr, base)
                if isinstance(idx, Const):
                    base.entries[idx.value] = val
                else:
                    base.entries[("*", id(target))] = val
                    base.dirty = True
            elif isinstance(base, ListV):
                self._mutating(fr, base)
                if isinstance(idx, Const) and isinstance(idx.value, int) and not isinstance(idx.value, bool) and 0 <= idx.value < len(base.items) and not base.dirty:
                    base.items[idx.value] = val
                else:
                    base.items.append(val)
                    base.dirty = True

    # ------------------------------------------------------------------ calls
    def _args(self, args_n: Node | None, fr: CFrame) -> list[Val]:
        out = []
        for a in (args_n.named_children if args_n is not None else []):
            expr = a.named_children[-1] if a.type == "argument" and a.named_children else a
            out.append(self.eval(expr, fr))
        return out

    def _hook(self, node, name, recv_text, args, recv, fr) -> None:
        if self.sink_hook is not None:
            self.sink_hook(node, name, recv_text, args, recv, fr)

    def _invoke(self, n: Node, fr: CFrame) -> Val:
        func = n.child_by_field_name("function")
        args_n = n.child_by_field_name("arguments")
        if func is None:
            return UNKNOWN
        outs = [(i, a.named_children[-1]) for i, a in enumerate(args_n.named_children if args_n is not None else [])
                if a.type == "argument" and a.named_children and any(c.type in ("out", "ref") for c in a.children)]
        if func.type == "identifier":
            name = _t(func)
            args = self._args(args_n, fr)
            self._hook(n, name, "", args, None, fr)
            held = fr.env.get(name)
            if isinstance(held, ClosureV):
                return self._call_closure(held, args, fr)
            if self._callable_part(held):
                return self._call_value(held, args, fr)
            self._outs = outs or None
            try:
                return self._call_unqualified(name, args, fr)
            finally:
                self._outs = None
        if func.type == "member_access_expression":
            expr, name = func.child_by_field_name("expression"), _t(func.child_by_field_name("name"))
            recv_text = _t(expr)
            args = self._args(args_n, fr)
            if name in ("MapGet", "MapPost", "MapPut", "MapDelete", "MapPatch", "MapMethods", "Map") and args and isinstance(args[-1], ClosureV):
                self._run_minimal_api_handler(args[-1], fr)  # app.MapGet("/", (string d) => ...): the parameters are request data
            if self._sanitizer(name, recv_text):
                self.eval(expr, fr)
                return SAFE
            cname = None
            if expr is not None and expr.type == "identifier" and _t(expr) not in fr.env and not (fr.this_obj and _t(expr) in fr.this_obj.fields):
                cname = _t(expr)
            if cname is not None:
                self._hook(n, name, recv_text, args, None, fr)
                self._outs = outs or None
                try:
                    return self._static_call(cname, name, args, fr)
                finally:
                    self._outs = None
            recv = self.eval(expr, fr)
            self._hook(n, name, recv_text, args, recv, fr)
            self._outs = outs or None
            try:
                return self._method(recv, name, args, fr, recv_text)
            finally:
                self._outs = None
        if func.type == "generic_name":
            name = _t(func.named_children[0]) if func.named_children else ""
            args = self._args(args_n, fr)
            self._hook(n, name, "", args, None, fr)
            return self._call_unqualified(name, args, fr)
        target = self.eval(func, fr)  # table["go"](x)
        args = self._args(args_n, fr)
        if self._callable_part(target):
            return self._call_value(target, args, fr)
        return self._opaque(None, args)

    def _run_minimal_api_handler(self, c: ClosureV, fr: CFrame) -> None:
        params = c.node.child_by_field_name("parameters") or next((x for x in c.node.children if x.type == "parameter_list"), None)
        vals: list[Val] = []
        for p in (params.named_children if params is not None else []):
            ids = [x for x in p.named_children if x.type == "identifier"]
            ptype = next((x for x in p.named_children if x.type in ("predefined_type", "identifier", "generic_name", "nullable_type", "qualified_name", "array_type") and x is not (ids[-1] if ids else None)), None)
            t = _t(ptype).rstrip("?") if ptype is not None else ""
            if t in ("HttpRequest", "HttpContext"):
                vals.append(Obj(kind="request", type_name="Request"))
            elif t in ("string", "String", "string[]", "List<string>", "IEnumerable<string>") or t.endswith("Dto") or t.endswith("Request") or t.endswith("Model"):
                vals.append(Tainted("route-param"))
            else:
                vals.append(UNKNOWN)
        self._call_closure(c, vals, fr)

    def _call_closure(self, c: ClosureV, args: list[Val], fr: CFrame) -> Val:
        if fr.depth + 1 > self.MAX_DEPTH or id(c.node) in self._active:
            return self._opaque(None, args)
        inner = CFrame(c.file, fr.cls, fr.this_obj, fr.depth + 1)
        inner.env.update(c.env)
        params = c.node.child_by_field_name("parameters") or next((x for x in c.node.children if x.type in ("parameter_list", "implicit_parameter", "identifier")), None)
        names = []
        if params is not None:
            if params.type in ("identifier", "implicit_parameter"):
                names = [_t(params)]
            else:
                names = [_t(([x for x in p.named_children if x.type == "identifier"] or [p])[-1]) for p in params.named_children]
        pos = list(args)
        for nm in names:
            inner.env[nm] = pos.pop(0) if pos else UNKNOWN
        body = c.node.child_by_field_name("body") or c.node.named_children[-1]
        self._active.add(id(c.node))
        try:
            if body.type == "block":
                self.exec(body, inner)
                return join_all(inner.rets) if inner.rets else Const(None)
            return self.eval(body, inner)
        finally:
            self._active.discard(id(c.node))

    def _find_method(self, cls: CsClass, name: str, argc: int):
        seen: set[int] = set()
        cur: CsClass | None = cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            ms = [m for m in cur.methods.get(name, []) if self._arity(m) == argc]
            if ms:
                return cur, ms[0]
            cur = self.index.cls(cur.bases[0], cur.file) if cur.bases else None
        return None

    @staticmethod
    def _arity(m: Node) -> int:
        p = m.child_by_field_name("parameters") or next((c for c in m.children if c.type == "parameter_list"), None)
        return len(p.named_children) if p is not None else 0

    def _call_unqualified(self, name: str, args: list[Val], fr: CFrame) -> Val:
        if fr.cls is not None:
            hit = self._find_method(fr.cls, name, len(args))
            if hit is not None:
                return self._run_method(hit[0], hit[1], fr.this_obj, args, fr)
        return self._opaque(None, args)

    def _static_call(self, cname: str, name: str, args: list[Val], fr: CFrame) -> Val:
        if cname == "ZipFile" and name in ("OpenRead", "Open"):
            return _zip_archive(fr)
        cls = self.index.cls(cname, fr.file)
        if cls is not None:
            hit = self._find_method(cls, name, len(args))
            if hit is not None:
                return self._run_method(hit[0], hit[1], None, args, fr)
        if cname in ("string", "String") and name in ("Format", "Concat", "Join"):
            if name == "Format" and args and isinstance(args[0], Const) and isinstance(args[0].value, str) and all(isinstance(a, Const) for a in args[1:]):
                try:
                    import re as _re
                    vals = [_s(a.value) for a in args[1:]]
                    return Const(_re.sub(r"\{(\d+)(?:[:,][^}]*)?\}", lambda m: vals[int(m.group(1))], args[0].value))
                except (IndexError, ValueError):
                    return SAFE
            if name == "Concat" and all(isinstance(a, Const) for a in args):
                return Const("".join(_s(a.value) for a in args))
            return self._combine(*args)
        if name in SAFE_METHODS and cname in ("int", "Int32", "Int64", "long", "double", "Double", "Convert", "Guid", "decimal", "Decimal", "bool", "Boolean", "Math", "DateTime", "string", "String", "Regex", "Char", "char"):
            return SAFE
        if cname == "Path":
            return self._combine(*args)
        return self._opaque(None, args, pure=cname in PURE_TYPES)

    def _method(self, recv: Val, name: str, args: list[Val], fr: CFrame, recv_text: str) -> Val:
        if isinstance(recv, Choice):
            return join_all(self._method(a, name, args, fr, recv_text) for a in recv.alts)
        if name == "Invoke" and isinstance(recv, (ClosureV, FuncV)):
            return self._call_value(recv, args, fr)
        if name in ("ForEach", "Select", "Where", "SelectMany", "Any", "All", "First", "FirstOrDefault", "Count") and args \
                and isinstance(args[-1], (ClosureV, FuncV)):
            held = join_all(recv.items) if isinstance(recv, ListV) and recv.items else (Tainted("element") if classify(recv) == "tainted" else None)
            if held is not None:
                self._call_value(args[-1], [held], fr)
        if isinstance(recv, Obj):
            if recv.kind == "request":
                return Tainted("request." + name)
            if recv.cls is not None:
                m = self._find_method(recv.cls, name, len(args))
                if m is not None:
                    return self._run_method(m[0], m[1], recv, args, fr)
            return self._opaque(recv, args)
        if isinstance(recv, ListV):
            return self._list_method(recv, name, args, fr)
        if isinstance(recv, MapV):
            return self._map_method(recv, name, args, fr)
        if isinstance(recv, BuilderV):
            if name == "Append" and args:
                self._mutating(fr, recv)
                recv.parts.append(args[0])
                return recv
            if name in ("AppendFormat", "Insert", "Replace", "Remove", "Clear"):
                self._mutating(fr, recv)
                recv.parts.extend(args)
                recv.dirty = True
                return recv
            if name == "ToString":
                return Const("".join(_s(p.value) for p in recv.parts)) if not recv.dirty and not recv.opaque and all(isinstance(p, Const) for p in recv.parts) \
                    else self._combine(*recv.parts, *([UNKNOWN] if recv.opaque else []))
            return self._opaque(recv, args)
        if isinstance(recv, Const) and isinstance(recv.value, str):
            return self._string_method(recv, name, args)
        if name in BENIGN or name in SAFE_METHODS:
            return SAFE
        if classify(recv) == "tainted":
            return Tainted("derived")
        return self._opaque(recv, args)

    def _string_method(self, recv: Const, name: str, args: list[Val]) -> Val:
        s = recv.value
        consts = all(isinstance(a, Const) for a in args)
        if name in BENIGN or name in SAFE_METHODS:
            return SAFE
        if consts:
            v = [a.value for a in args]
            try:
                if name in ("ToLower", "ToLowerInvariant"):
                    return Const(s.lower())
                if name in ("ToUpper", "ToUpperInvariant"):
                    return Const(s.upper())
                if name == "Trim":
                    return Const(s.strip())
                if name == "Replace" and len(v) == 2:
                    return Const(s.replace(_s(v[0]), _s(v[1])))
                if name == "Substring" and v and isinstance(v[0], int):
                    return Const(s[v[0]:v[0] + v[1]] if len(v) > 1 else s[v[0]:])
                if name == "ToString":
                    return recv
                if name == "Concat":
                    return Const(s + "".join(_s(x) for x in v))
            except (TypeError, ValueError, IndexError):
                return SAFE
            return SAFE
        return self._combine(recv, *args)

    def _list_method(self, lst: ListV, name: str, args: list[Val], fr: CFrame) -> Val:
        if lst.opaque and name not in ("Add", "AddRange", "Clear", "Remove", "RemoveAt", "Insert"):
            return join_all(lst.items + [UNKNOWN])
        if name == "Add" and args:
            self._mutating(fr, lst)
            lst.items.append(args[0])
            return SAFE
        if name == "AddRange" and args:
            self._mutating(fr, lst)
            if isinstance(args[0], ListV):
                lst.items.extend(args[0].items)
            else:
                lst.items.append(args[0])
                lst.dirty = True
            return SAFE
        if name in ("Remove", "RemoveAt", "Clear", "Insert"):
            self._mutating(fr, lst)
            lst.dirty = True
            if name == "Insert" and len(args) == 2:
                lst.items.append(args[1])
            return SAFE
        if name in ("Contains", "Count", "Any", "IndexOf"):
            return SAFE
        if name in ("ToArray", "ToList", "Reverse", "Distinct", "OrderBy", "Where", "Select", "Take", "Skip"):
            return ListV(list(lst.items), birth_depth=fr.branch, dirty=True)
        if name in ("First", "FirstOrDefault", "Last", "LastOrDefault", "ElementAt", "Single", "SingleOrDefault"):
            return join_all(lst.items + [UNKNOWN]) if lst.items else UNKNOWN
        return self._opaque(lst, args)

    def _map_method(self, m: MapV, name: str, args: list[Val], fr: CFrame) -> Val:
        key = args[0].value if args and isinstance(args[0], Const) and isinstance(args[0].value, (str, int)) else None
        if name in ("Add", "TryAdd") and len(args) == 2:
            self._mutating(fr, m)
            if key is not None:
                m.entries[key] = args[1]
            else:
                m.entries[("*", id(args[0]))] = args[1]
                m.dirty = True
            return SAFE
        if name in ("ContainsKey", "ContainsValue", "Count", "TryGetValue_x"):
            return SAFE
        if name in ("Remove", "Clear"):
            self._mutating(fr, m)
            m.dirty = True
            return SAFE
        if name in ("GetValueOrDefault",) and args:
            if key is not None and not m.dirty and not m.opaque:
                return m.entries.get(key, args[1] if len(args) > 1 else Const(None))
            return join_all(list(m.entries.values()) + [UNKNOWN])
        if name in ("Keys", "Values"):
            return ListV(list(m.entries.values()), birth_depth=fr.branch, dirty=True)
        return self._opaque(m, args)

    def _new(self, n: Node, fr: CFrame) -> Val:
        type_n = n.child_by_field_name("type")
        tname = _t(type_n).split("<")[0].split(".")[-1]
        args = self._args(n.child_by_field_name("arguments"), fr)
        self._hook(n, "<new>", tname, args, None, fr)
        if tname == "ZipArchive":
            return _zip_archive(fr)
        if tname in ("List", "ArrayList", "HashSet", "Queue", "Stack", "Collection", "LinkedList", "SortedSet"):
            lv = ListV(list(args[0].items) if args and isinstance(args[0], ListV) else [], birth_depth=fr.branch,
                       dirty=bool(args and not isinstance(args[0], (ListV, Const))))
            self._collection_initializer(lv, n, fr)
            return lv
        if tname in ("Dictionary", "Hashtable", "SortedDictionary", "NameValueCollection", "ConcurrentDictionary"):
            mv = MapV({}, birth_depth=fr.branch, dirty=bool(args and not isinstance(args[0], Const)))
            self._collection_initializer(mv, n, fr)
            return mv
        if tname == "StringBuilder":
            return BuilderV(list(args[:1]) if args and not (isinstance(args[0], Const) and isinstance(args[0].value, int)) else [], birth_depth=fr.branch)
        if tname == "String" and args:
            return args[0]
        cls = self.index.cls(tname, fr.file)
        init = n.child_by_field_name("initializer") or next((c for c in n.children if c.type == "initializer_expression"), None)
        if cls is None:
            obj = Obj(type_name=tname, fields={"_args": self._combine(*args)} if args else {})
            if tname in ("SqlCommand", "OleDbCommand", "MySqlCommand", "NpgsqlCommand", "SQLiteCommand", "SqliteCommand", "OdbcCommand") and args:
                obj.fields["CommandText"] = args[0]
            self._apply_initializer(obj, init, fr)
            return obj
        obj = Obj(cls=cls, type_name=cls.name)
        for fname, initn in cls.fields.items():
            if initn is not None:
                obj.fields[fname] = self.eval(initn, CFrame(cls.file, cls, obj, fr.depth + 1))
        ctors = [c for c in cls.ctors if self._arity(c) == len(args)]
        if ctors:
            self._run_method(cls, ctors[0], obj, args, fr, ctor=True)
        self._apply_initializer(obj, init, fr)
        return obj

    def make_this(self, cls: CsClass, cf: CsFile) -> Obj:
        """`this` for an entry point: field initializers run and the constructor has left its fields set."""
        obj = Obj(cls=cls, type_name=cls.name)
        fr = CFrame(cf, cls, obj, 0)
        try:
            for fname, initn in cls.fields.items():
                if initn is not None:
                    obj.fields[fname] = self.eval(initn, CFrame(cls.file, cls, obj, 1))
            if cls.ctors:
                self._run_method(cls, cls.ctors[0], obj, [], fr, ctor=True)
        except (BudgetExceeded, RecursionError):
            pass
        return obj

    def _collection_initializer(self, coll: Val, n: Node, fr: CFrame) -> None:
        """`new List<string> { a, b }` and `new Dictionary<K, V> { { k, v }, [k] = v }`."""
        init = n.child_by_field_name("initializer") or next((c for c in n.children if c.type == "initializer_expression"), None)
        if init is None:
            return
        for c in init.named_children:
            if isinstance(coll, ListV):
                coll.items.append(self.eval(c, fr))
                continue
            key = val = None
            if c.type == "initializer_expression" and len(c.named_children) == 2:
                key, val = self.eval(c.named_children[0], fr), self.eval(c.named_children[1], fr)
            elif c.type == "assignment_expression":
                left = c.child_by_field_name("left")
                idx = next((x for x in (left.named_children if left is not None else []) if x.type in ("bracketed_argument_list", "argument")), None)
                inner = idx.named_children[-1] if idx is not None and idx.named_children else None
                if inner is not None and inner.type == "argument":
                    inner = inner.named_children[-1] if inner.named_children else None
                if inner is not None:
                    key = self.eval(inner, fr)
                val = self.eval(c.child_by_field_name("right"), fr)
            if val is None:
                coll.dirty = True
            elif isinstance(key, Const):
                coll.entries[key.value] = val
            else:
                coll.entries[("*", id(c))] = val
                coll.dirty = True

    def _apply_initializer(self, obj: Obj, init: Node | None, fr: CFrame) -> None:
        for c in (init.named_children if init is not None else []):
            if c.type == "assignment_expression":
                left = c.child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    obj.fields[_t(left)] = self.eval(c.child_by_field_name("right"), fr)

    def _run_method(self, cls: CsClass, m: Node, recv: Obj | None, args: list[Val], fr: CFrame, ctor: bool = False) -> Val:
        outs, self._outs = self._outs, None
        if fr.depth + 1 > self.MAX_DEPTH or id(m) in self._active:
            return self._opaque(recv, args)
        body = m.child_by_field_name("body") or next((c for c in m.children if c.type in ("block", "arrow_expression_clause")), None)
        if body is None:
            return self._opaque(recv, args)
        inner = CFrame(cls.file, cls, recv, fr.depth + 1)
        self._bind_params(m, args, inner)
        self._active.add(id(m))
        try:
            if body.type == "arrow_expression_clause":
                inner.rets.append(self.eval(body.named_children[-1] if body.named_children else None, inner))
            else:
                self.exec(body, inner)
        finally:
            self._active.discard(id(m))
        if outs:
            params = m.child_by_field_name("parameters") or next((c for c in m.children if c.type == "parameter_list"), None)
            names = [_t(([c for c in p.named_children if c.type == "identifier"] or [None])[-1]) for p in (params.named_children if params is not None else [])]
            for i, target in outs:  # `out`/`ref` arguments: the callee's final value flows back into the caller's variable
                if i < len(names) and names[i] in inner.env:
                    self._store_out(target, inner.env[names[i]], fr)
        if ctor:
            return UNKNOWN
        return join_all(inner.rets) if inner.rets else Const(None)

    def _deconstruct(self, pat: Node, val: Val, fr: CFrame) -> None:
        """`var (a, b) = f();` and `(x, y) = f();`: element i of the tuple goes to the i-th name."""
        items = [c for c in pat.named_children if c.type != "type"]
        for i, c in enumerate(items):
            tgt = c.named_children[-1] if c.type in ("argument", "declaration_expression") and c.named_children else c
            if isinstance(val, ListV) and not val.dirty and i < len(val.items):
                el = val.items[i]
            else:
                el = Tainted("derived") if classify(val) == "tainted" else UNKNOWN
            if tgt.type in ("tuple_pattern", "tuple_expression"):
                self._deconstruct(tgt, el, fr)
            elif tgt.type == "identifier":
                fr.env[_t(tgt)] = el

    def _store_out(self, target: Node, val: Val, fr: CFrame) -> None:
        if target.type == "declaration_expression":
            ident = next((c for c in reversed(target.children) if c.type == "identifier"), None)
            if ident is not None:
                fr.env[_t(ident)] = val
        elif target.type == "identifier":
            self._store(target, val, fr)

    def _bind_params(self, m: Node, args: list[Val], inner: CFrame) -> None:
        params = m.child_by_field_name("parameters") or next((c for c in m.children if c.type == "parameter_list"), None)
        pos = list(args)
        for p in (params.named_children if params is not None else []):
            ids = [c for c in p.named_children if c.type == "identifier"]
            if ids:
                inner.env[_t(ids[-1])] = pos.pop(0) if pos else UNKNOWN

    def bind_entry(self, m: Node, fr: CFrame, action) -> None:
        """Entry point. Parameters of a controller action are model-bound request data."""
        lib, action = action == "lib", action is True
        params = m.child_by_field_name("parameters") or next((c for c in m.children if c.type == "parameter_list"), None)
        for p in (params.named_children if params is not None else []):
            ids = [c for c in p.named_children if c.type == "identifier"]
            if not ids:
                continue
            name = _t(ids[-1])
            ptype = next((c for c in p.named_children if c.type in ("predefined_type", "identifier", "generic_name", "nullable_type", "qualified_name", "array_type") and c is not ids[-1]), None)
            tname = _t(ptype).rstrip("?")
            if tname in ("HttpRequest", "HttpRequestBase", "HttpRequestMessage") or name in ("request", "Request"):
                fr.env[name] = Obj(kind="request", type_name="Request")
            elif lib and tname in ("string", "String", "string[]"):
                fr.env[name] = Tainted("library parameter")  # a public method of a library: its caller chooses the arguments
            elif action and tname in ("int", "long", "short", "byte", "bool", "double", "decimal", "float", "Guid", "DateTime", "CancellationToken", "uint", "ulong"):
                fr.env[name] = SAFE
            elif action and tname not in ("HttpContext", "ILogger", "IConfiguration") and not tname.startswith("I"):
                fr.env[name] = Tainted("route-param")
            else:
                fr.env[name] = UNKNOWN

    # ------------------------------------------------------------------ statements
    def exec(self, n: Node | None, fr: CFrame) -> bool:
        if n is None:
            return False
        self._tick()
        t = n.type
        if t in ("block", "compilation_unit", "switch_section_body_x"):
            for c in n.named_children:
                if self.exec(c, fr):
                    return True
            return False
        if t == "local_declaration_statement":
            for vd in n.named_children:
                if vd.type == "variable_declaration":
                    for d in vd.named_children:
                        if d.type == "variable_declarator":
                            pat = next((c for c in d.named_children if c.type == "tuple_pattern"), None)
                            if pat is not None:
                                val_n = d.named_children[-1]
                                if val_n.type == "equals_value_clause" and val_n.named_children:
                                    val_n = val_n.named_children[-1]
                                self._deconstruct(pat, self.eval(val_n, fr) if val_n is not pat else UNKNOWN, fr)
                                continue
                            name, val_n = _declarator_value(d)
                            fr.env[name] = self.eval(val_n, fr) if val_n is not None else UNKNOWN
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
        if t in ("for_statement", "while_statement", "do_statement", "for_each_statement", "foreach_statement"):
            return self._loop(n, fr)
        if t == "switch_statement":
            return self._switch(n, fr)
        if t == "try_statement":
            return self._try(n, fr)
        if t in ("using_statement", "lock_statement", "checked_statement", "unsafe_statement", "fixed_statement"):
            for c in n.named_children:
                if c.type == "variable_declaration":
                    for d in c.named_children:
                        if d.type == "variable_declarator":
                            name, val_n = _declarator_value(d)
                            fr.env[name] = self.eval(val_n, fr) if val_n is not None else UNKNOWN
                elif c.type in ("block",) or c.type.endswith("statement"):
                    if self.exec(c, fr):
                        return True
                else:
                    self.eval(c, fr)
            return False
        if t == "yield_statement":
            if n.named_children:
                fr.rets.append(self.eval(n.named_children[-1], fr))  # `yield return x`: handed to whoever iterates
            return False
        if t in ("break_statement", "continue_statement", "empty_statement", "local_function_statement", "labeled_statement", "goto_statement"):
            return False
        for c in n.named_children:
            if c.type.endswith("statement") or c.type == "block":
                self.exec(c, fr)
            else:
                self.eval(c, fr)
        return False

    # ------------------------------------------------------------------ guards
    def _engine(self, fr: CFrame) -> G.GuardEngine:
        def norm(n):
            t = n.type
            if t == "parenthesized_expression":
                return ("paren", n.named_children[0] if n.named_children else None)
            if t == "prefix_unary_expression" and n.children and _t(n.children[0]) == "!":
                return ("not", n.named_children[-1] if n.named_children else None)
            if t == "binary_expression":
                op = _t(n.child_by_field_name("operator"))
                l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
                return {"&&": ("and", l, r), "||": ("or", l, r), "==": ("eq", l, r), "!=": ("ne", l, r)}.get(op, ("other",))
            if t == "invocation_expression":
                fn = n.child_by_field_name("function")
                args_n = n.child_by_field_name("arguments")
                args = [(a.named_children[-1] if a.type == "argument" and a.named_children else a) for a in (args_n.named_children if args_n is not None else [])]
                if fn is not None and fn.type == "member_access_expression":
                    return ("call", _t(fn.child_by_field_name("name")), fn.child_by_field_name("expression"), args, _t(fn.child_by_field_name("expression")))
                return ("call", _t(fn), None, args, "")
            return ("other",)

        def local(n):
            return _t(n) if n is not None and n.type == "identifier" and _t(n) in fr.env else None

        def const(n):
            if n is None:
                return None
            v = self.eval(n, fr)
            return v.value if isinstance(v, Const) and isinstance(v.value, str) else None

        def safe(n):
            return classify(self.eval(n, fr)) == "safe"

        def coll(n):
            v = self.eval(n, fr)
            return isinstance(v, ListV) and bool(v.items) and not v.dirty and not v.opaque and all(classify(i) == "safe" for i in v.items)

        def pattern_source(n):
            if n is None:
                return None
            if n.type == "object_creation_expression" and _t(n.child_by_field_name("type")).split(".")[-1] == "Regex":
                args_n = n.child_by_field_name("arguments")
                first = args_n.named_children[0] if args_n is not None and args_n.named_children else None
                inner = first.named_children[-1] if first is not None and first.named_children else None
                return const(inner)
            if n.type == "identifier":
                c = fr.cls
                while c is not None:
                    init = c.fields.get(_t(n))
                    if init is not None:
                        return pattern_source(init)
                    c = self.index.cls(c.bases[0], c.file) if c.bases else None
            return None

        def attr(n):
            if n is None or n.type != "member_access_expression":
                return None
            expr, nm = n.child_by_field_name("expression"), _t(n.child_by_field_name("name"))
            if local(expr):
                return (local(expr), nm)
            if expr is not None and expr.type == "object_creation_expression":  # new Uri(x).Host
                a = expr.child_by_field_name("arguments")
                first = a.named_children[0] if a is not None and a.named_children else None
                inner = first.named_children[-1] if first is not None and first.named_children else None
                if inner is not None and local(inner):
                    return (local(inner), nm)
            return None

        return G.GuardEngine(norm, local, const, safe, coll, pattern_source, self.cwes, attr=attr, canon=lambda var: G.canonical_assignment(fr.file.src, var), untainted=lambda n: n is not None and classify(self.eval(n, fr)) != "tainted")

    def _guard_facts(self, cond: Node | None, fr: CFrame):
        return self._engine(fr).facts(cond)

    def _narrow(self, names: set, fr: CFrame) -> None:
        G.apply(names, self.cwes, fr.env, SAFE)

    def _join_envs(self, fr: CFrame, a: dict, b: dict) -> None:
        out = {}
        for k in set(a) | set(b):
            out[k] = join(a[k], b[k]) if (k in a and k in b) else (a[k] if k in a else b[k])
        fr.env = out

    def _if(self, n: Node, fr: CFrame) -> bool:
        cond = self.eval(n.child_by_field_name("condition"), fr)
        cons, alt = n.child_by_field_name("consequence"), n.child_by_field_name("alternative")
        if isinstance(cond, Const) and isinstance(cond.value, bool):
            self._dead(fr, alt if cond.value else cons)
            return self.exec(cons, fr) if cond.value else (self.exec(alt, fr) if alt is not None else False)
        base = dict(fr.env)
        facts = self._guard_facts(n.child_by_field_name("condition"), fr)
        fr.branch += 1
        try:
            self._narrow(facts[0], fr)
            ta = self.exec(cons, fr)
            ea = fr.env
            fr.env = dict(base)
            self._narrow(facts[1], fr)
            tb = self.exec(alt, fr) if alt is not None else False
            eb = fr.env
        finally:
            fr.branch -= 1
        if ta and tb:
            fr.env = ea
            return True
        if ta:
            fr.env = eb
        elif tb:
            fr.env = ea
        else:
            self._join_envs(fr, ea, eb)
        return False

    def _loop(self, n: Node, fr: CFrame) -> bool:
        base = dict(fr.env)
        fr.branch += 1
        try:
            if n.type in ("for_each_statement", "foreach_statement"):
                it = self.eval(n.child_by_field_name("right"), fr)
                left = n.child_by_field_name("left")
                if isinstance(it, ListV):
                    elem = join_all(it.items + ([UNKNOWN] if it.dirty or it.opaque else []))
                elif classify(it) == "tainted":
                    elem = Tainted("derived")
                else:
                    elem = UNKNOWN
                if left is not None:
                    fr.env[_t(left)] = elem
            elif n.type == "for_statement":
                for c in n.named_children:
                    if c.type == "variable_declaration":
                        for d in c.named_children:
                            if d.type == "variable_declarator":
                                name, val_n = _declarator_value(d)
                                fr.env[name] = self.eval(val_n, fr) if val_n is not None else UNKNOWN
            self.exec(n.child_by_field_name("body") or next((c for c in n.named_children if c.type in ("block",) or c.type.endswith("statement")), None), fr)
        finally:
            fr.branch -= 1
        self._join_envs(fr, base, fr.env)
        return False

    def _switch(self, n: Node, fr: CFrame) -> bool:
        sel = self.eval(n.child_by_field_name("value"), fr)
        body = n.child_by_field_name("body") or next((c for c in n.named_children if c.type == "switch_body"), None)
        sections = [c for c in (body.named_children if body is not None else []) if c.type == "switch_section"]
        base = dict(fr.env)
        envs = []
        has_default = any(any(x.type == "default_switch_label" or _t(x).strip().startswith("default") for x in s.named_children[:1]) for s in sections)
        fr.branch += 1
        try:
            for i in range(len(sections)):
                fr.env = dict(base)
                for s in sections[i].named_children:
                    if s.type in ("case_switch_label", "default_switch_label", "constant_pattern", "case_pattern_switch_label", "switch_label"):
                        continue
                    if s.type == "break_statement":
                        break
                    if self.exec(s, fr):
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

    def _try(self, n: Node, fr: CFrame) -> bool:
        base = dict(fr.env)
        body = n.child_by_field_name("body") or next((c for c in n.named_children if c.type == "block"), None)
        term = self.exec(body, fr)
        after = fr.env
        envs = [] if term else [after]
        all_term = term
        for c in n.named_children:
            if c.type == "catch_clause":
                fr.env = {k: join(base.get(k, v), v) if k in base else v for k, v in after.items()}
                fr.branch += 1
                try:
                    cterm = self.exec(c.child_by_field_name("body") or next((x for x in c.named_children if x.type == "block"), None), fr)
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
