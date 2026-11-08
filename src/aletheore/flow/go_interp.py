"""An abstract interpreter for Go, built on tree-sitter.

Same stance as the other interpreters: follow request data to sinks and prove values safe; anything it
cannot model is UNKNOWN. Go specifics: multi-value assignment and returns, `if err != nil { return }` guards,
closures registered as handlers, struct methods, and package functions resolved by directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from aletheore.flow import guards as G
from aletheore.flow.go_index import GoFile, GoIndex
from aletheore.flow.go_sinks import (
    PURE_PKGS, REQUEST_FIELDS, REQUEST_METHODS, REQUEST_PARAM_TYPES, RESPONSE_PARAM_TYPES, SAFE_FUNCS, SANITIZERS,
)
from aletheore.flow.values import add_prefix, SAFE, UNKNOWN, BuilderV, Choice, Const, ListV, MapV, Obj, Tainted, Val, classify, join, join_all


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class FnV(Val):
    node: Node
    file: GoFile
    env: dict


@dataclass(eq=False)
class GFrame:
    file: GoFile
    this_obj: Obj | None
    depth: int
    env: dict = field(default_factory=dict)
    rets: list = field(default_factory=list)
    branch: int = 0


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _unquote(raw: str) -> str:
    if raw.startswith("`"):
        return raw.strip("`")
    body = raw[1:-1] if raw.startswith('"') else raw
    out, i = [], 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'"}.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def _s(v: object) -> str:
    return "<nil>" if v is None else ("true" if v is True else "false" if v is False else str(v))


def _go_format(fmt: str, vals: list) -> str | None:
    """A small subset of fmt verbs over constants; None if anything is outside it."""
    out, i, k = [], 0, 0
    while i < len(fmt):
        ch = fmt[i]
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        i += 1
        while i < len(fmt) and fmt[i] in "+-# 0123456789.":
            i += 1
        if i >= len(fmt):
            return None
        verb = fmt[i]
        i += 1
        if verb == "%":
            out.append("%")
            continue
        if k >= len(vals) or verb not in "sdvqtx":
            return None
        v = vals[k]
        k += 1
        out.append(('"' + _s(v) + '"') if verb == "q" else _s(v))
    return "".join(out) if k == len(vals) else None


class GoInterp:
    MAX_STEPS = 60000
    MAX_DEPTH = 6

    def __init__(self, index: GoIndex, cwes: set[int] | None = None, sink_hook=None, store_hook=None):
        self.index = index
        self.cwes = cwes or set()
        self.sink_hook = sink_hook    # hook(node, name, recv_text, args, recv, frame)
        self.store_hook = store_hook  # hook(node, base_text, key, value, frame)
        self.steps = 0
        self.probes: dict[tuple, list] = {}
        self.probe_keys: set[tuple] = set()
        self.dead: list[tuple] = []
        self._active: set[int] = set()

    # ------------------------------------------------------------------ helpers
    def _tick(self) -> None:
        self.steps += 1
        if self.steps > self.MAX_STEPS:
            raise BudgetExceeded

    def _key(self, fr: GFrame, n: Node) -> tuple:
        return (id(fr.file), n.start_byte, n.end_byte)

    def _dead(self, fr: GFrame, n: Node | None) -> None:
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

    def _mutating(self, fr: GFrame, c) -> None:
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
    def eval(self, node: Node | None, fr: GFrame) -> Val:
        if node is None:
            return UNKNOWN
        self._tick()
        val = self._eval(node, fr)
        key = self._key(fr, node)
        if key in self.probe_keys:
            self.probes.setdefault(key, []).append((val, classify(val)))
        return val

    def _eval(self, n: Node, fr: GFrame) -> Val:
        t = n.type
        if t in ("interpreted_string_literal", "raw_string_literal"):
            return Const(_unquote(_t(n)))
        if t == "int_literal":
            try:
                return Const(int(_t(n).replace("_", ""), 0))
            except ValueError:
                return UNKNOWN
        if t == "float_literal":
            try:
                return Const(float(_t(n).replace("_", "")))
            except ValueError:
                return UNKNOWN
        if t == "rune_literal":
            return Const(_unquote(_t(n)[:1] + _t(n)[1:-1] + _t(n)[:1]) if len(_t(n)) > 2 else "")
        if t == "true":
            return Const(True)
        if t == "false":
            return Const(False)
        if t == "nil":
            return Const(None)
        if t == "identifier":
            name = _t(n)
            if name in fr.env:
                return fr.env[name]
            return self._package_value(name, fr)
        if t == "parenthesized_expression":
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t == "selector_expression":
            return self._selector(n, fr)
        if t == "index_expression":
            return self._index(n, fr)
        if t == "slice_expression":
            base = self.eval(n.child_by_field_name("operand"), fr)
            return self._combine(base)
        if t == "call_expression":
            return self._call(n, fr)
        if t == "binary_expression":
            return self._binary(n, fr)
        if t == "unary_expression":
            op = _t(n.child_by_field_name("operator"))
            v = self.eval(n.child_by_field_name("operand"), fr)
            if op == "!" and isinstance(v, Const) and isinstance(v.value, bool):
                return Const(not v.value)
            if op in ("-", "+", "^"):
                return SAFE
            return v  # &x and <-ch keep the value
        if t == "composite_literal":
            return self._composite(n, fr)
        if t == "func_literal":
            return FnV(n, fr.file, dict(fr.env))
        if t == "type_assertion_expression":
            return self.eval(n.child_by_field_name("operand"), fr)
        if t == "type_conversion_expression":
            return self.eval(n.child_by_field_name("operand"), fr) if n.child_by_field_name("operand") else UNKNOWN
        return UNKNOWN

    def _package_value(self, name: str, fr: GFrame) -> Val:
        """A package-level constant (`const T = "users"`) or a package variable nothing ever assigns to: its value when it
        evaluates to something safe."""
        hit = self.index.package_value(name, fr.file)
        if hit is None or id(hit[1]) in self._active or fr.depth > self.MAX_DEPTH:
            return UNKNOWN
        gf, node = hit
        decl = node.parent
        while decl is not None and decl.type not in ("const_spec", "var_spec"):
            decl = decl.parent
        if decl is None or (decl.type != "const_spec" and self.index.package_assigned(name, fr.file)):
            return UNKNOWN  # a package variable some function assigns to is not a constant
        self._active.add(id(node))
        try:
            v = self.eval(node, GFrame(gf, None, 0))
        finally:
            self._active.discard(id(node))
        return v if classify(v) == "safe" else UNKNOWN

    def _selector(self, n: Node, fr: GFrame) -> Val:
        op_n, field_n = n.child_by_field_name("operand"), n.child_by_field_name("field")
        field_name = _t(field_n)
        base = self.eval(op_n, fr)
        if isinstance(base, Obj):
            if base.kind == "request":
                return Tainted("request." + field_name) if field_name in REQUEST_FIELDS else UNKNOWN
            if field_name in base.fields:
                return base.fields[field_name]
            return UNKNOWN
        if isinstance(base, MapV) and field_name in base.entries:
            return base.entries[field_name]
        if classify(base) == "tainted":
            return Tainted("derived")
        return UNKNOWN

    def _index(self, n: Node, fr: GFrame) -> Val:
        base = self.eval(n.child_by_field_name("operand"), fr)
        idx = self.eval(n.child_by_field_name("index"), fr)
        if isinstance(base, MapV):
            if isinstance(idx, Const) and not base.dirty and not base.opaque and idx.value in base.entries:
                return base.entries[idx.value]
            return join_all(list(base.entries.values()) + [UNKNOWN])
        if isinstance(base, ListV):
            if isinstance(idx, Const) and isinstance(idx.value, int) and not isinstance(idx.value, bool) and not base.dirty and not base.opaque \
                    and 0 <= idx.value < len(base.items):
                return base.items[idx.value]
            return join_all(base.items + [UNKNOWN]) if base.items else UNKNOWN
        if classify(base) == "tainted":
            return Tainted("derived")
        return UNKNOWN

    def _binary(self, n: Node, fr: GFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        left_n, right_n = n.child_by_field_name("left"), n.child_by_field_name("right")
        a = self.eval(left_n, fr)
        if op in ("&&", "||") and isinstance(a, Const) and isinstance(a.value, bool):
            if (op == "&&" and not a.value) or (op == "||" and a.value):
                self._dead(fr, right_n)
                return a
            return self.eval(right_n, fr)
        b = self.eval(right_n, fr)
        if op in ("&&", "||"):
            return SAFE
        if isinstance(a, Const) and isinstance(b, Const):
            x, y = a.value, b.value
            try:
                if op == "+" and isinstance(x, str) and isinstance(y, str):
                    return Const(x + y)
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
        if op in ("==", "!=", "<", ">", "<=", ">="):
            return SAFE
        return self._combine(a, b)

    def _composite(self, n: Node, fr: GFrame) -> Val:
        type_n, body = n.child_by_field_name("type"), n.child_by_field_name("body")
        ttext = _t(type_n)
        elems = body.named_children if body is not None else []
        if ttext in ("strings.Builder", "bytes.Buffer"):
            return BuilderV([], birth_depth=fr.branch)
        if ttext.startswith("map["):
            m = MapV({}, birth_depth=fr.branch)
            for e in elems:
                if e.type == "keyed_element" and len(e.named_children) >= 2:
                    k = self.eval(self._elem_expr(e.named_children[0]), fr)
                    v = self.eval(self._elem_expr(e.named_children[-1]), fr)
                    if isinstance(k, Const):
                        m.entries[k.value] = v
                    else:
                        m.entries[("*", id(e))] = v
                        m.dirty = True
            return m
        if ttext.startswith("[") or ttext == "":
            return ListV([self.eval(self._elem_expr(e), fr) for e in elems], birth_depth=fr.branch)
        # a struct literal: fields by name
        obj = Obj(type_name=ttext.lstrip("*&").split(".")[-1])
        for e in elems:
            if e.type == "keyed_element" and len(e.named_children) >= 2:
                obj.fields[_t(e.named_children[0]).strip()] = self.eval(self._elem_expr(e.named_children[-1]), fr)
            else:
                obj.fields[f"_{len(obj.fields)}"] = self.eval(self._elem_expr(e), fr)
        return obj

    @staticmethod
    def _elem_expr(e: Node) -> Node:
        if e.type in ("literal_element", "keyed_element") and e.named_children:
            return e.named_children[-1] if e.type == "literal_element" else e
        return e

    # ------------------------------------------------------------------ calls
    def _args(self, args_n: Node | None, fr: GFrame) -> list[Val]:
        out: list[Val] = []
        for c in (args_n.named_children if args_n is not None else []):
            if c.type == "func_literal":
                self._callback(c, fr)
                out.append(UNKNOWN)
            else:
                out.append(self.eval(c.named_children[0] if c.type == "variadic_argument" and c.named_children else c, fr))
        return out

    def _bind_params(self, fn: Node, args: list[Val], inner: GFrame, entry: bool = False) -> None:
        params = fn.child_by_field_name("parameters")
        pos = list(args)
        for p in (params.named_children if params is not None else []):
            if p.type not in ("parameter_declaration", "variadic_parameter_declaration"):
                continue
            names = [c for c in p.named_children if c.type == "identifier"]
            type_n = p.child_by_field_name("type")
            ttext = _t(type_n).replace(" ", "")
            for nm in names:
                if entry or not pos:
                    inner.env[_t(nm)] = self._typed_param(ttext)
                else:
                    inner.env[_t(nm)] = pos.pop(0)

    @staticmethod
    def _typed_param(ttext: str) -> Val:
        if ttext in REQUEST_PARAM_TYPES or ttext.endswith(".Request") or ttext.endswith(".Context") and ttext != "context.Context":
            return Obj(kind="request", type_name="Request")
        if ttext in RESPONSE_PARAM_TYPES:
            return Obj(type_name="ResponseWriter")
        return UNKNOWN

    def _callback(self, fn: Node, fr: GFrame) -> None:
        if fr.depth + 1 > self.MAX_DEPTH or id(fn) in self._active:
            return
        inner = GFrame(fr.file, fr.this_obj, fr.depth + 1, env=dict(fr.env), branch=fr.branch + 1)
        self._bind_params(fn, [], inner, entry=True)
        self._active.add(id(fn))
        try:
            self.exec(fn.child_by_field_name("body"), inner)
        finally:
            self._active.discard(id(fn))

    def _hook(self, node, name, recv_text, args, recv, fr) -> None:
        if self.sink_hook is not None:
            self.sink_hook(node, name, recv_text, args, recv, fr)

    def _call(self, n: Node, fr: GFrame) -> Val:
        func = n.child_by_field_name("function")
        args_n = n.child_by_field_name("arguments")
        if func is None:
            return UNKNOWN
        t = func.type
        if t == "identifier":
            name = _t(func)
            args = self._args(args_n, fr)
            self._hook(n, name, "", args, None, fr)
            return self._call_name(name, n, args, fr)
        if t == "selector_expression":
            op_n, name_n = func.child_by_field_name("operand"), func.child_by_field_name("field")
            name, recv_text = _t(name_n), _t(op_n)
            args = self._args(args_n, fr)
            if self._sanitizer(name):
                return SAFE
            if op_n.type == "identifier" and _t(op_n) not in fr.env:
                hit = self.index.package_function(_t(op_n), name, fr.file)
                if hit is not None:
                    self._hook(n, name, recv_text, args, None, fr)
                    return self._run_function(hit[0], hit[1], None, args, fr)
                self._hook(n, name, recv_text, args, None, fr)
                return self._pkg_call(_t(op_n), name, n, args, fr)
            recv = self.eval(op_n, fr)
            self._hook(n, name, recv_text, args, recv, fr)
            return self._method(recv, name, args, fr, recv_text, n)
        if t == "func_literal":
            return self._run_function(fr.file, func, None, self._args(args_n, fr), fr, closure=fr.env)
        # a type conversion such as []byte(x) or string(x) written with a composite type
        args = self._args(args_n, fr)
        return args[0] if len(args) == 1 else self._opaque(None, args)

    def _call_name(self, name: str, n: Node, args: list[Val], fr: GFrame) -> Val:
        v = fr.env.get(name)
        if isinstance(v, FnV):
            return self._run_function(v.file, v.node, None, args, fr, closure={**v.env, **fr.env})
        hit = self.index.function(name, fr.file)
        if hit is not None:
            return self._run_function(hit[0], hit[1], None, args, fr)
        if name in ("string", "[]byte", "[]rune", "rune", "byte"):
            return args[0] if len(args) == 1 else self._combine(*args)
        if name in ("int", "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32", "uint64", "float32", "float64", "bool", "len", "cap", "complex", "real", "imag"):
            return SAFE
        if name == "append" and args:
            base = args[0]
            items = list(base.items) if isinstance(base, ListV) else [base]
            return ListV(items + list(args[1:]), birth_depth=fr.branch, dirty=isinstance(base, ListV) and base.dirty)
        if name == "make":
            return MapV({}, birth_depth=fr.branch) if "map[" in _t(n) else ListV([], birth_depth=fr.branch, dirty=True)
        if name == "new":
            return Obj()
        if name in ("copy", "delete", "close", "print", "println", "panic", "recover"):
            return UNKNOWN
        return self._opaque(None, args)

    def _pkg_call(self, pkg: str, name: str, n: Node, args: list[Val], fr: GFrame) -> Val:
        if pkg == "fmt" and name in ("Sprintf", "Sprint", "Sprintln", "Errorf"):
            if name == "Sprintf" and args and isinstance(args[0], Const) and isinstance(args[0].value, str) and all(isinstance(a, Const) for a in args[1:]):
                out = _go_format(args[0].value, [a.value for a in args[1:]])
                if out is not None:
                    return Const(out)
            return self._combine(*args)
        if pkg == "strings":
            if name == "Join" and args and isinstance(args[0], ListV):
                items = args[0].items
                if all(isinstance(i, Const) for i in items) and not args[0].dirty and not args[0].opaque and len(args) > 1 and isinstance(args[1], Const):
                    return Const(_s(args[1].value).join(_s(i.value) for i in items))
                return self._combine(*items, *args[1:])
            if name in ("Split", "SplitN", "Fields"):
                if args and isinstance(args[0], Const) and name == "Split" and len(args) > 1 and isinstance(args[1], Const) \
                        and isinstance(args[0].value, str) and isinstance(args[1].value, str):
                    return ListV([Const(p) for p in args[0].value.split(args[1].value)], birth_depth=fr.branch)
                return ListV([self._combine(*args)], birth_depth=fr.branch, dirty=True)
            if name in ("ToLower", "ToUpper", "TrimSpace") and args and isinstance(args[0], Const) and isinstance(args[0].value, str):
                return Const({"ToLower": args[0].value.lower, "ToUpper": args[0].value.upper, "TrimSpace": args[0].value.strip}[name]())
            if name in ("HasPrefix", "HasSuffix", "Contains", "Index", "Count", "EqualFold", "ContainsAny"):
                return SAFE
        if name in SAFE_FUNCS:
            return SAFE
        if pkg == "exec" and name in ("Command", "CommandContext"):
            return Obj(type_name="Cmd", fields={"v": self._combine(*args)})
        if pkg == "http" and name in ("NewRequest", "NewRequestWithContext"):
            return Obj(type_name="OutRequest", fields={"v": self._combine(*args)})
        if pkg in PURE_PKGS:
            return self._combine(*args)
        return self._opaque(None, args)

    def _method(self, recv: Val, name: str, args: list[Val], fr: GFrame, recv_text: str, node: Node) -> Val:
        if isinstance(recv, Choice):
            return join_all(self._method(a, name, args, fr, recv_text, node) for a in recv.alts)
        if isinstance(recv, Obj):
            if recv.kind == "request":
                return Tainted("request." + name) if name in REQUEST_METHODS else UNKNOWN
            if recv.type_name == "ResponseWriter":
                if name == "Header":
                    return Obj(type_name="HeaderMap", fields={"_w": recv})  # w.Header().Set("Content-Type", ...) lands on w
                return UNKNOWN
            if recv.type_name == "HeaderMap":
                w = recv.fields.get("_w")
                if isinstance(w, Obj):
                    self._note_content_type(w, name, args, node)
                return UNKNOWN
            hit = self.index.method(recv.type_name or "", name, fr.file)
            if hit is not None:
                return self._run_function(hit[0], hit[1], recv, args, fr)
            held = recv.fields.get(name)
            if isinstance(held, FnV):
                return self._run_function(held.file, held.node, recv, args, fr, closure={**held.env, **fr.env})
            return self._opaque(recv, args)
        if isinstance(recv, FnV):
            return self._run_function(recv.file, recv.node, None, args, fr, closure={**recv.env, **fr.env})
        if isinstance(recv, BuilderV):
            if name in ("WriteString", "Write", "WriteByte", "WriteRune") and args:
                if fr.branch > recv.birth_depth:
                    recv.dirty = True
                recv.parts.append(args[0])
                return UNKNOWN
            if name == "String":
                return Const("".join(str(p.value) for p in recv.parts)) if not recv.dirty and not recv.opaque and all(isinstance(p, Const) for p in recv.parts) \
                    else self._combine(*recv.parts, *([UNKNOWN] if recv.opaque else []))
            if name in ("Reset", "Truncate", "Grow"):
                recv.dirty = True
                return UNKNOWN
            return self._opaque(recv, args)
        if isinstance(recv, Tainted) and recv.why == "through-call":
            return UNKNOWN  # an object returned by an unmodeled call (a database record): its methods are not known to return the input
        if classify(recv) == "tainted":
            return Tainted("derived")
        if isinstance(recv, MapV) and name == "Get" and args and isinstance(args[0], Const) and not recv.dirty and not recv.opaque:
            return recv.entries.get(args[0].value, Const(""))
        return self._opaque(recv, args)

    @staticmethod
    def _note_content_type(res: Obj, name: str, args: list[Val], node: Node) -> None:
        text = _t(node).lower()
        if "content-type" in text and name in ("Set", "Add") and len(args) >= 2 and isinstance(args[1], Const) and isinstance(args[1].value, str):
            res.fields["_ctype"] = Const(args[1].value.lower())

    def _run_function(self, file: GoFile, fn: Node, this_obj: Obj | None, args: list[Val], fr: GFrame, closure: dict | None = None) -> Val:
        if fr.depth + 1 > self.MAX_DEPTH or id(fn) in self._active:
            return self._opaque(this_obj, args)
        body = fn.child_by_field_name("body")
        if body is None:
            return self._opaque(this_obj, args)
        inner = GFrame(file, this_obj, fr.depth + 1, env=dict(closure) if closure else {})
        self._bind_receiver(fn, this_obj, inner)
        self._bind_params(fn, args, inner)
        self._active.add(id(fn))
        try:
            self.exec(body, inner)
        finally:
            self._active.discard(id(fn))
        if not inner.rets:
            return Const(None)
        multi = [r for r in inner.rets]
        return join_all(multi)

    @staticmethod
    def _bind_receiver(fn: Node, this_obj: Obj | None, inner: GFrame) -> None:
        recv = fn.child_by_field_name("receiver")
        if recv is not None and this_obj is not None:
            for p in recv.named_children:
                nm = next((c for c in p.named_children if c.type == "identifier"), None)
                if nm is not None:
                    inner.env[_t(nm)] = this_obj

    def bind_entry(self, fn: Node, fr: GFrame) -> None:
        self._bind_receiver(fn, fr.this_obj, fr)
        self._bind_params(fn, [], fr, entry=True)

    # ------------------------------------------------------------------ statements
    def _bind(self, target: Node | None, val: Val, fr: GFrame) -> None:
        if target is None:
            return
        t = target.type
        if t == "identifier":
            if _t(target) != "_":
                fr.env[_t(target)] = val
        elif t == "selector_expression":
            base = self.eval(target.child_by_field_name("operand"), fr)
            fname = _t(target.child_by_field_name("field"))
            if isinstance(base, Obj):
                old = base.fields.get(fname)
                base.fields[fname] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "index_expression":
            base_n = target.child_by_field_name("operand")
            base = self.eval(base_n, fr)
            idx = self.eval(target.child_by_field_name("index"), fr)
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
                if isinstance(idx, Const) and isinstance(idx.value, int) and not isinstance(idx.value, bool) and 0 <= idx.value < len(base.items) and not base.dirty:
                    base.items[idx.value] = val
                else:
                    base.items.append(val)
                    base.dirty = True
        elif t == "parenthesized_expression" and target.named_children:
            self._bind(target.named_children[0], val, fr)
        elif t == "unary_expression" and target.named_children:  # *p = v
            self._bind(target.named_children[-1], val, fr)

    def _bind_many(self, targets: list[Node], values: list[Val], fr: GFrame, single_call: bool) -> None:
        if len(targets) == len(values):
            for t, v in zip(targets, values):
                self._bind(t, v, fr)
            return
        v = values[0] if values else UNKNOWN
        if len(targets) > 1 and len(values) == 1:
            if isinstance(v, ListV) and len(v.items) == len(targets) and not v.dirty and not v.opaque:
                for t, x in zip(targets, v.items):
                    self._bind(t, x, fr)
                return
            # value, err := f()   /   value, ok := m[k]
            self._bind(targets[0], v, fr)
            for t in targets[1:]:
                self._bind(t, UNKNOWN if single_call else SAFE, fr)
            return
        for t in targets:
            self._bind(t, UNKNOWN, fr)

    def exec(self, n: Node | None, fr: GFrame) -> bool:
        if n is None:
            return False
        self._tick()
        t = n.type
        if t in ("block", "statement_list", "source_file"):
            for c in n.named_children:
                if self.exec(c, fr):
                    return True
            return False
        if t in ("short_var_declaration", "assignment_statement"):
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            targets = list(left.named_children) if left is not None else []
            rights = list(right.named_children) if right is not None else []
            op = _t(n.child_by_field_name("operator")) if t == "assignment_statement" else ":="
            vals = []
            for r in rights:
                if r.type == "func_literal":
                    vals.append(FnV(r, fr.file, dict(fr.env)))
                else:
                    vals.append(self.eval(r, fr))
            if op not in (":=", "=") and targets and vals:
                cur = self.eval(targets[0], fr)
                if op == "+=" and isinstance(cur, Const) and isinstance(vals[0], Const) and isinstance(cur.value, str) and isinstance(vals[0].value, str):
                    vals = [Const(cur.value + vals[0].value)]
                else:
                    vals = [self._combine(cur, vals[0])]
            self._bind_many(targets, vals, fr, single_call=bool(rights and rights[0].type == "call_expression"))
            return False
        if t in ("var_declaration", "const_declaration"):
            for spec in n.named_children:
                specs = [spec] if spec.type in ("var_spec", "const_spec") else [c for c in spec.named_children if c.type in ("var_spec", "const_spec")]
                for sp in specs:
                    names = [c for c in sp.named_children if c.type == "identifier"]
                    val_n = sp.child_by_field_name("value")
                    rights = list(val_n.named_children) if val_n is not None else []
                    vals = [self.eval(r, fr) for r in rights]
                    if vals:
                        self._bind_many(names, vals, fr, single_call=bool(rights and rights[0].type == "call_expression"))
                    else:
                        tn = _t(sp.child_by_field_name("type")).replace(" ", "")
                        for nm in names:
                            fr.env[_t(nm)] = BuilderV([], birth_depth=fr.branch) if tn in ("strings.Builder", "bytes.Buffer") else UNKNOWN
            return False
        if t == "inc_dec_statement":
            return False
        if t == "expression_statement":
            for c in n.named_children:
                self.eval(c, fr)
                if c.type == "call_expression" and _t(c.child_by_field_name("function")) == "panic":
                    return True
            return False
        if t == "return_statement":
            vals = [self.eval(c, fr) for c in (n.named_children[0].named_children if n.named_children and n.named_children[0].type == "expression_list" else n.named_children)]
            fr.rets.append(vals[0] if len(vals) == 1 else ListV(vals, birth_depth=0) if vals else Const(None))
            return True
        if t == "if_statement":
            return self._if(n, fr)
        if t in ("for_statement",):
            return self._for(n, fr)
        if t in ("expression_switch_statement", "type_switch_statement"):
            return self._switch(n, fr)
        if t == "select_statement":
            for c in n.named_children:
                self.exec(c, fr)
            return False
        if t in ("defer_statement", "go_statement"):
            for c in n.named_children:
                self.eval(c, fr)
            return False
        if t == "labeled_statement":
            return self.exec(n.named_children[-1], fr) if n.named_children else False
        if t in ("function_declaration", "method_declaration", "type_declaration", "import_declaration", "package_clause", "break_statement",
                 "continue_statement", "goto_statement", "empty_statement", "comment", "fallthrough_statement"):
            return False
        for c in n.named_children:
            if c.type.endswith("statement") or c.type in ("block", "statement_list"):
                self.exec(c, fr)
            else:
                self.eval(c, fr)
        return False

    def _join_envs(self, fr: GFrame, a: dict, b: dict) -> None:
        out = {}
        for k in set(a) | set(b):
            out[k] = join(a[k], b[k]) if (k in a and k in b) else (a[k] if k in a else b[k])
        fr.env = out

    # ------------------------------------------------------------------ guards
    def _engine(self, fr: GFrame) -> G.GuardEngine:
        def norm(n):
            t = n.type
            if t == "parenthesized_expression":
                return ("paren", n.named_children[0] if n.named_children else None)
            if t == "unary_expression" and _t(n.child_by_field_name("operator")) == "!":
                return ("not", n.child_by_field_name("operand"))
            if t == "binary_expression":
                op = _t(n.child_by_field_name("operator"))
                l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
                return {"&&": ("and", l, r), "||": ("or", l, r), "==": ("eq", l, r), "!=": ("ne", l, r)}.get(op, ("other",))
            if t == "call_expression":
                fn = n.child_by_field_name("function")
                args_n = n.child_by_field_name("arguments")
                args = list(args_n.named_children) if args_n is not None else []
                if fn is not None and fn.type == "selector_expression":
                    return ("call", _t(fn.child_by_field_name("field")), fn.child_by_field_name("operand"), args, _t(fn.child_by_field_name("operand")))
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
            if n.type == "identifier":
                hit = self.index.package_value(_t(n), fr.file)
                n = hit[1] if hit is not None else None
            if n is not None and n.type == "call_expression" and _t(n.child_by_field_name("function")).endswith(("MustCompile", "Compile")):
                args_n = n.child_by_field_name("arguments")
                first = args_n.named_children[0] if args_n is not None and args_n.named_children else None
                return const(first)
            return None

        return G.GuardEngine(norm, local, const, safe, coll, pattern_source, self.cwes)

    def _narrow(self, names: set, fr: GFrame) -> None:
        G.apply(names, self.cwes, fr.env, SAFE)

    def _if(self, n: Node, fr: GFrame) -> bool:
        init = n.child_by_field_name("initializer")
        if init is not None:
            self.exec(init, fr)
        cond = self.eval(n.child_by_field_name("condition"), fr)
        cons, alt = n.child_by_field_name("consequence"), n.child_by_field_name("alternative")
        if isinstance(cond, Const) and isinstance(cond.value, bool):
            self._dead(fr, alt if cond.value else cons)
            return self.exec(cons, fr) if cond.value else (self.exec(alt, fr) if alt is not None else False)
        base = dict(fr.env)
        facts = self._engine(fr).facts(n.child_by_field_name("condition"))
        fr.branch += 1
        try:
            self._narrow(facts[0], fr)
            term_a = self.exec(cons, fr)
            env_a = fr.env
            fr.env = dict(base)
            self._narrow(facts[1], fr)
            term_b = self.exec(alt, fr) if alt is not None else False
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

    def _for(self, n: Node, fr: GFrame) -> bool:
        base = dict(fr.env)
        fr.branch += 1
        try:
            clause = next((c for c in n.named_children if c.type in ("range_clause", "for_clause")), None)
            if clause is not None and clause.type == "range_clause":
                it = self.eval(clause.child_by_field_name("right"), fr)
                left = clause.child_by_field_name("left")
                targets = list(left.named_children) if left is not None else []
                if isinstance(it, ListV):
                    elem = join_all(it.items + ([UNKNOWN] if it.dirty or it.opaque else []))
                    keys = SAFE
                elif isinstance(it, MapV):
                    elem = join_all(list(it.entries.values()) + [UNKNOWN])
                    keys = join_all([Const(k) if not isinstance(k, tuple) else UNKNOWN for k in it.entries] + [UNKNOWN])
                elif classify(it) == "tainted":
                    elem = keys = Tainted("derived")
                else:
                    elem = keys = UNKNOWN
                if len(targets) == 1:
                    self._bind(targets[0], keys if isinstance(it, MapV) else SAFE, fr)
                elif len(targets) >= 2:
                    self._bind(targets[0], keys, fr)
                    self._bind(targets[1], elem, fr)
            elif clause is not None:
                init = clause.child_by_field_name("initializer")
                if init is not None:
                    self.exec(init, fr)
            self.exec(n.child_by_field_name("body") or next((c for c in n.named_children if c.type == "block"), None), fr)
        finally:
            fr.branch -= 1
        self._join_envs(fr, base, fr.env)
        return False

    def _switch(self, n: Node, fr: GFrame) -> bool:
        init = n.child_by_field_name("initializer")
        if init is not None:
            self.exec(init, fr)
        value = n.child_by_field_name("value")
        sel = self.eval(value, fr) if value is not None else Const(True)
        cases = [c for c in n.named_children if c.type in ("expression_case", "default_case", "type_case")]
        base = dict(fr.env)
        envs = []
        has_default = any(c.type == "default_case" for c in cases)
        taken = None
        if isinstance(sel, Const):
            for i, c in enumerate(cases):
                if c.type == "expression_case":
                    exprs = c.child_by_field_name("value")
                    vals = [self.eval(e, fr) for e in (exprs.named_children if exprs is not None else [])]
                    if all(isinstance(v, Const) for v in vals) and any(v.value == sel.value for v in vals):
                        taken = i
                        break
            if taken is None and has_default and all(
                    c.type == "default_case" or all(isinstance(self.eval(e, fr), Const) for e in (c.child_by_field_name("value").named_children if c.child_by_field_name("value") is not None else []))
                    for c in cases):
                taken = next(i for i, c in enumerate(cases) if c.type == "default_case")
            if taken is not None:
                for i, c in enumerate(cases):
                    if i != taken:
                        self._dead(fr, c)
                return self._run_case(cases[taken], fr)
        fr.branch += 1
        try:
            for c in cases:
                fr.env = dict(base)
                self._run_case(c, fr)
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

    def _run_case(self, case: Node, fr: GFrame) -> bool:
        for c in case.named_children:
            if c.type == "statement_list":
                return self.exec(c, fr)
        return False
