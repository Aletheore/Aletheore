"""An abstract interpreter for Kotlin, built on tree-sitter. Same stance as the other interpreters: follow request
data (annotated Spring parameters, servlet and Ktor request objects) to sinks and prove values safe; anything it
cannot model is UNKNOWN. Kotlin specifics: `$name` and `${expr}` templates, expression-bodied functions, companion
objects, `if`/`when`/`try` as expressions, lambdas with implicit `it`, collections factories and destructuring."""

from __future__ import annotations

from aletheore.flow.budget import STEPS as _STEPS

import re
from dataclasses import dataclass, field

from tree_sitter import Node

from aletheore.flow import extra_sinks as _X
from aletheore.flow import guards as G
from aletheore.flow.kt_index import KtClass, KtFile, KtIndex, annotations
from aletheore.flow.kt_sinks import (
    PARAM_ANNOTATIONS, PURE_TYPES, REQUEST_TYPES, SAFE_METHODS, SAFE_PARAM_TYPES, SANITIZERS, SERVLET_BENIGN,
)
from aletheore.flow.values import (
    add_prefix, SAFE, UNKNOWN, BuilderV, Choice, ClosureV, FuncV, Const, ListV, MapV, Obj, Tainted, Val, classify, join, join_all,
)

def _zip_entry() -> Obj:
    e = Tainted("archive.entry")
    return Obj(type_name="ZipEntry", fields={"name": e, "getName": e})


LOOP_FUNS = {"forEach", "map", "filter", "flatMap", "mapNotNull", "forEachIndexed", "mapIndexed", "let", "also", "apply", "run", "with", "takeIf", "takeUnless", "use",
             "onEach", "filterNot", "any", "all", "none", "count", "first", "firstOrNull", "find", "sortedBy", "groupBy", "associate", "associateBy", "fold", "reduce",
             "repeat", "buildString", "buildList", "sumOf", "maxByOrNull", "minByOrNull", "partition", "zip_x"}
ID_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class KFrame:
    file: KtFile
    cls: KtClass | None
    this_obj: Obj | None
    depth: int
    env: dict = field(default_factory=dict)
    rets: list = field(default_factory=list)
    branch: int = 0
    last: Val = UNKNOWN


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _s(v: object) -> str:
    return "" if v is None else ("true" if v is True else "false" if v is False else str(v))


def _unesc(raw: str) -> str:
    table = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", '"': '"', "'": "'", "\\": "\\", "$": "$", "0": "\0"}
    return table.get(raw[1], raw[1:]) if raw.startswith("\\") and len(raw) >= 2 else raw


def _params(fn: Node) -> list[Node]:
    p = next((c for c in fn.children if c.type == "function_value_parameters"), None)
    return [c for c in (p.named_children if p is not None else []) if c.type == "parameter"]


def _pname(p: Node) -> str:
    return _t(next((c for c in p.children if c.type == "identifier"), None))


def _ptype(p: Node) -> str:
    ut = next((c for c in p.children if c.type in ("user_type", "nullable_type")), None)
    if ut is None:
        return ""
    if ut.type == "nullable_type":
        ut = next((c for c in ut.children if c.type == "user_type"), ut)
    ident = next((c for c in ut.children if c.type == "identifier"), None)
    return _t(ident) or _t(ut).rstrip("?")


class KtInterp:
    MAX_STEPS = 60000
    MAX_DEPTH = 6

    def __init__(self, index: KtIndex, cwes: set[int] | None = None, sink_hook=None, store_hook=None):
        self.index = index
        self.cwes = cwes or set()
        self.sink_hook = sink_hook    # hook(node, name, recv_text, args, recv, fr)
        self.store_hook = store_hook  # hook(node, base_text, key, value, fr)
        self.steps = 0
        self.probes: dict[tuple, list] = {}
        self.probe_keys: set[tuple] = set()
        self.dead: list[tuple] = []
        self._active: set[tuple] = set()

    # ------------------------------------------------------------------ helpers
    def _tick(self) -> None:
        self.steps += 1
        _STEPS[0] += 1
        if self.steps > self.MAX_STEPS:
            raise BudgetExceeded

    def _key(self, fr: KFrame, n: Node) -> tuple:
        return (id(fr.file), n.start_byte, n.end_byte)

    def _dead(self, fr: KFrame, n: Node | None) -> None:
        if n is not None:
            self.dead.append(self._key(fr, n))

    def _sanitizer(self, name: str, recv: str = "") -> bool:
        return any(name in SANITIZERS.get(c, ()) for c in self.cwes) or _X.sanitizes("kotlin", self.cwes, name, recv)

    @staticmethod
    def _combine(*vals: Val) -> Val:
        rank = {"safe": 0, "unknown": 1, "tainted": 2}
        worst = "safe"
        for v in vals:
            c = classify(v)
            if rank[c] > rank[worst]:
                worst = c
        return add_prefix({"safe": SAFE, "unknown": UNKNOWN, "tainted": Tainted("derived")}[worst], vals)

    def _mutating(self, fr: KFrame, c) -> None:
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
    def eval(self, node: Node | None, fr: KFrame) -> Val:
        if node is None:
            return UNKNOWN
        self._tick()
        val = self._eval(node, fr)
        key = self._key(fr, node) if self.probe_keys else None  # (nothing to record in a normal scan)
        if key is not None and key in self.probe_keys:
            self.probes.setdefault(key, []).append((val, classify(val)))
        return val

    def _eval(self, n: Node, fr: KFrame) -> Val:
        t = n.type
        if t == "string_literal":
            return self._string(n, fr)
        if t == "number_literal":
            txt = _t(n).replace("_", "").rstrip("lLfFuU")
            try:
                return Const(int(txt, 0)) if not any(c in txt for c in ".eE") or txt.startswith("0x") else Const(float(txt))
            except ValueError:
                return SAFE
        if t == "boolean_literal":
            return Const(_t(n) == "true")
        if t in ("null_literal",):
            return Const(None)
        if t == "character_literal":
            return Const(_t(n)[1:-1])
        if t == "identifier":
            return self._ident(_t(n), fr)
        if t == "this_expression":
            return fr.this_obj or UNKNOWN
        if t == "parenthesized_expression":
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t == "callable_reference":
            ids = [c for c in n.children if c.type == "identifier"]
            return self._fn_ref(None, _t(ids[-1]) if ids else "", fr)
        if t == "navigation_expression" and any(c.type == "::" for c in n.children):
            return self._fn_ref(n.children[0], _t(n.children[-1]), fr)  # this::name, obj::name, Type::name
        if t == "navigation_expression":
            return self._member(n, fr)
        if t == "index_expression":
            return self._index(n, fr)
        if t == "call_expression":
            return self._call(n, fr)
        if t == "binary_expression":
            return self._binary(n, fr)
        if t == "infix_expression":
            kids = n.named_children
            if len(kids) == 3 and _t(kids[1]) == "to":
                return ListV([self.eval(kids[0], fr), self.eval(kids[2], fr)], birth_depth=fr.branch)
            return self._combine(*(self.eval(k, fr) for k in kids if k.type != "identifier" or k is kids[0]))
        if t in ("unary_expression", "prefix_expression", "postfix_expression"):
            op = _t(n.child_by_field_name("operator")) or (_t(n.children[0]) if n.children else "")
            arg = n.child_by_field_name("argument") or (n.named_children[-1] if n.named_children else None)
            v = self.eval(arg, fr)
            if op == "!" and isinstance(v, Const) and isinstance(v.value, bool):
                return Const(not v.value)
            if op in ("!!",):
                return v
            return SAFE if op in ("-", "+", "!", "++", "--") else self._combine(v)
        if t == "assignment":
            return self._assign(n, fr)
        if t in ("as_expression", "check_expression", "spread_expression"):
            return self.eval(n.named_children[0], fr) if n.named_children and t != "check_expression" else SAFE
        if t in ("if_expression", "when_expression", "try_expression", "return_expression", "throw_expression", "jump_expression", "for_statement", "while_statement",
                 "do_while_statement", "block", "control_structure_body", "statements"):
            self.exec(n, fr)
            return fr.last
        if t == "lambda_literal":
            return ClosureV(n, fr.file, dict(fr.env))  # runs where it is invoked, with what it captured
        if t == "anonymous_function":
            return UNKNOWN
        if t in ("annotated_lambda",):
            return UNKNOWN
        if t == "collection_literal":
            return ListV([self.eval(c, fr) for c in n.named_children], birth_depth=fr.branch)
        if t in ("range_expression", "comparison_expression", "equality_expression", "conjunction_expression", "disjunction_expression"):
            return SAFE
        return UNKNOWN

    def _string(self, n: Node, fr: KFrame) -> Val:
        parts: list[Val] = []
        kids = list(n.children)
        i = 0
        while i < len(kids):
            c = kids[i]
            if c.type == "string_content":
                txt = _t(c)
                if txt == "$" and i + 1 < len(kids) and kids[i + 1].type == "string_content":
                    nxt = _t(kids[i + 1])
                    m = ID_RX.match(nxt)
                    if m:
                        parts.append(self._ident(m.group(0), fr))
                        rest = nxt[m.end():]
                        if rest:
                            parts.append(Const(rest))
                        i += 2
                        continue
                parts.append(Const(txt))
            elif c.type == "escape_sequence":
                parts.append(Const(_unesc(_t(c))))
            elif c.type == "interpolation":
                expr = next((x for x in c.named_children), None)
                parts.append(self.eval(expr, fr) if expr is not None else Const(""))
            i += 1
        if all(isinstance(p, Const) for p in parts):
            return Const("".join(_s(p.value) for p in parts))
        return self._combine(*parts)

    def _ident(self, name: str, fr: KFrame) -> Val:
        if name in fr.env:
            return fr.env[name]
        if name == "true":
            return Const(True)
        if name == "false":
            return Const(False)
        if name == "null":
            return Const(None)
        if fr.this_obj is not None and name in fr.this_obj.fields:
            return fr.this_obj.fields[name]
        c, seen = fr.cls, set()
        while c is not None and id(c) not in seen:
            seen.add(id(c))
            if name in c.consts:
                return self._const_value(c, name, fr)
            c = self._base(c)
        if fr.file.main is not None and name in fr.file.main.consts:
            return self._const_value(fr.file.main, name, fr)
        if name == "call":
            return Obj(kind="request", type_name="ApplicationCall")
        return UNKNOWN

    def _base(self, c: KtClass) -> KtClass | None:
        for b in c.bases:
            found = self.index.cls(b, c.file)
            if found is not None and found is not c:
                return found
        return None

    def _const_value(self, cls: KtClass, name: str, fr: KFrame) -> Val:
        init = cls.consts.get(name)
        key = (id(cls.file), init.start_byte if init is not None else -1)
        if init is None or key in self._active or fr.depth > self.MAX_DEPTH:
            return UNKNOWN
        self._active.add(key)
        try:
            v = self.eval(init, KFrame(cls.file, cls, None, fr.depth + 1))
        finally:
            self._active.discard(key)
        return v if classify(v) == "safe" else UNKNOWN

    @staticmethod
    def _split_nav(n: Node) -> tuple[Node | None, str, bool]:
        kids = n.children
        name_n = kids[-1] if kids and kids[-1].type == "identifier" else next((c for c in reversed(kids) if c.type == "identifier"), None)
        recv = kids[0] if kids else None
        return recv, _t(name_n), any(c.type == "?." for c in kids)

    def _member(self, n: Node, fr: KFrame) -> Val:
        recv_n, name, _ = self._split_nav(n)
        if recv_n is not None and recv_n.type == "identifier":
            cname = _t(recv_n)
            if cname not in fr.env and not (fr.this_obj and cname in fr.this_obj.fields):
                cls = self.index.cls(cname, fr.file)
                if cls is not None and name in cls.consts:
                    return self._const_value(cls, name, fr)
                if cname in ("Integer", "Long", "Double", "Math", "Int", "System", "TimeUnit", "Charsets", "StandardCharsets", "MediaType", "HttpStatus") or name in ("MAX_VALUE", "MIN_VALUE", "EMPTY"):
                    return SAFE
        base = self.eval(recv_n, fr)
        if name in ("nextEntry", "nextJarEntry", "nextTarEntry", "nextZipEntry"):
            return _zip_entry()  # zis.nextEntry: an archive entry, named by whoever made the archive
        if isinstance(base, Obj):
            if base.kind == "request":
                return SAFE if name in SERVLET_BENIGN else Tainted("request." + name)
            if name in base.fields:
                return base.fields[name]
            return UNKNOWN
        if isinstance(base, Choice):
            return join_all(self._member_of(a, name) for a in base.alts)
        return self._prop(base, name)

    def _member_of(self, a: Val, name: str) -> Val:
        if isinstance(a, Obj) and a.kind != "request":
            return a.fields.get(name, UNKNOWN)
        return self._prop(a, name)

    def _prop(self, base: Val, name: str) -> Val:
        if name in ("length", "size", "indices", "lastIndex", "isEmpty"):
            return SAFE
        if isinstance(base, Const) and isinstance(base.value, str) and name in ("trim", "lowercase"):
            return base
        if classify(base) == "tainted":
            return Tainted("derived")
        return UNKNOWN

    def _index(self, n: Node, fr: KFrame) -> Val:
        kids = n.named_children
        base = self.eval(kids[0], fr) if kids else UNKNOWN
        idx = self.eval(kids[1], fr) if len(kids) > 1 else UNKNOWN
        if isinstance(base, Obj):
            return Tainted("request[...]") if base.kind == "request" else UNKNOWN
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

    def _binary(self, n: Node, fr: KFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        left_n, right_n = n.child_by_field_name("left"), n.child_by_field_name("right")
        a = self.eval(left_n, fr)
        if op in ("&&", "||"):
            if isinstance(a, Const) and isinstance(a.value, bool):
                if (op == "&&" and not a.value) or (op == "||" and a.value):
                    self._dead(fr, right_n)
                    return a
                return self.eval(right_n, fr)
            fr.branch += 1
            try:
                b = self.eval(right_n, fr)
            finally:
                fr.branch -= 1
            return join(a, b)
        if op == "?:":
            if isinstance(a, Const):
                if a.value is not None:
                    self._dead(fr, right_n)
                    return a
                return self.eval(right_n, fr)
            fr.branch += 1
            try:
                b = self.eval(right_n, fr)
            finally:
                fr.branch -= 1
            return join(a, b)
        b = self.eval(right_n, fr)
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
        if op in ("==", "!=", "===", "!==", "<", ">", "<=", ">=", "in", "!in", "is", "!is", ".."):
            return SAFE
        return self._combine(a, b)

    def _assign(self, n: Node, fr: KFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        op = _t(n.child_by_field_name("operator")) or "="
        val = self.eval(right, fr)
        if op != "=":
            cur = self.eval(left, fr)
            if op == "+=" and isinstance(cur, Const) and isinstance(val, Const) and (isinstance(cur.value, str) or isinstance(val.value, str)):
                val = Const(_s(cur.value) + _s(val.value))
            elif op == "+=" and isinstance(cur, ListV):
                self._mutating(fr, cur)
                cur.items.append(val)
                return cur
            else:
                val = self._combine(cur, val)
        self._store(left, val, fr)
        return val

    def _store(self, target: Node | None, val: Val, fr: KFrame) -> None:
        if target is None:
            return
        if target.type == "directly_assignable_expression" and target.named_children:
            target = target.named_children[0]
        t = target.type
        if t == "identifier":
            name = _t(target)
            is_prop = fr.this_obj is not None and (name in fr.this_obj.fields or (fr.cls is not None and name in fr.cls.props))
            if name in fr.env or not is_prop:
                fr.env[name] = val
            else:
                old = fr.this_obj.fields.get(name)
                fr.this_obj.fields[name] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "navigation_expression":
            recv_n, name, _ = self._split_nav(target)
            base = self.eval(recv_n, fr)
            if self.store_hook is not None:
                self.store_hook(target, _t(recv_n), Const(name), val, fr)
            if isinstance(base, Obj):
                old = base.fields.get(name)
                base.fields[name] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "index_expression":
            kids = target.named_children
            base = self.eval(kids[0], fr) if kids else UNKNOWN
            idx = self.eval(kids[1], fr) if len(kids) > 1 else UNKNOWN
            if self.store_hook is not None:
                self.store_hook(target, _t(kids[0]) if kids else "", idx, val, fr)
            if isinstance(base, MapV):
                self._mutating(fr, base)
                if isinstance(idx, Const) and isinstance(idx.value, (str, int)):
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
    def _args(self, call: Node, fr: KFrame) -> list[Val]:
        va = next((c for c in call.children if c.type == "value_arguments"), None)
        out = []
        for a in (va.named_children if va is not None else []):
            if a.type != "value_argument":
                continue
            exprs = [c for c in a.named_children if c.type not in ("annotation",)]
            out.append(self.eval(exprs[-1], fr) if exprs else UNKNOWN)
        return out

    def _lambda_of(self, call: Node) -> Node | None:
        al = next((c for c in call.children if c.type == "annotated_lambda"), None)
        return next((c for c in (al.named_children if al is not None else []) if c.type == "lambda_literal"), None)

    def _hook(self, node, name, recv_text, args, recv, fr) -> None:
        if self.sink_hook is not None:
            self.sink_hook(node, name, recv_text, args, recv, fr)

    def _call(self, n: Node, fr: KFrame) -> Val:
        if G.strip_call_is_safe(_t(n), self.cwes):
            for c in n.named_children:  # (evaluated for what they do; the result is harmless text either way)
                self.eval(c, fr)
            return SAFE
        callee = n.children[0] if n.children else None
        if callee is None:
            return UNKNOWN
        lam = self._lambda_of(n)
        if callee.type == "identifier":
            name = _t(callee)
            args = self._args(n, fr)
            self._hook(n, name, "", args, None, fr)
            return self._unqualified(name, args, fr, lam, n)
        if callee.type == "navigation_expression":
            recv_n, name, safe_nav = self._split_nav(callee)
            recv_text = _t(recv_n)
            args = self._args(n, fr)
            if self._sanitizer(name, recv_text):
                self.eval(recv_n, fr)
                if lam is not None:
                    self._run_lambda(lam, [UNKNOWN], fr)
                return SAFE
            if recv_n is not None and recv_n.type == "identifier" and _t(recv_n) not in fr.env and not (fr.this_obj and _t(recv_n) in fr.this_obj.fields):
                cname = _t(recv_n)
                if cname[:1].isupper():
                    self._hook(n, name, recv_text, args, None, fr)
                    return self._static(cname, name, args, fr, lam)
            recv = self.eval(recv_n, fr)
            self._hook(n, name, recv_text, args, recv, fr)
            return self._method(recv, name, args, fr, recv_n, lam)
        target = self.eval(callee, fr)  # table[key]!!(x), makeHandler()(x)
        if self._callable_part(target):
            return self._call_value(target, self._args(n, fr), fr)
        return self._opaque(None, self._args(n, fr))

    def _fn_ref(self, recv_n: Node | None, name: str, fr: KFrame) -> Val:
        """::name, this::name, obj::name as a value that runs the function when called."""
        cls, obj = fr.cls, fr.this_obj
        if recv_n is not None and recv_n.type != "this_expression":
            v = self.eval(recv_n, fr)
            if isinstance(v, Obj) and v.cls is not None:
                cls, obj = v.cls, v
            else:
                cls = self.index.cls(_t(recv_n), fr.file) if _t(recv_n)[:1].isupper() else None
                obj = None
                if cls is None:
                    return UNKNOWN
        if cls is not None:
            hit = self._find_fn(cls, name)
            if hit is not None:
                return FuncV(hit[1], hit[0].file, hit[0], obj, name=name)
        if recv_n is None and fr.file.main is not None and fr.file.main.funcs.get(name):
            return FuncV(fr.file.main.funcs[name][0], fr.file.main.file, fr.file.main, None, name=name)
        return UNKNOWN

    @staticmethod
    def _callable_part(v) -> bool:
        return isinstance(v, FuncV) or (isinstance(v, Choice) and any(isinstance(a, (FuncV, ClosureV)) for a in v.alts))

    def _call_value(self, target: Val, args: list[Val], fr: KFrame) -> Val:
        alts = target.alts if isinstance(target, Choice) else [target]
        outs = []
        for a in alts:
            if isinstance(a, FuncV):
                outs.append(self._run_fn(a.cls, a.node, a.self_obj, list(a.bound) + list(args), fr))
            elif isinstance(a, ClosureV):
                outs.append(self._invoke(a, args, fr))
            else:
                outs.append(self._opaque(None, args))
        return join_all(outs)

    def _find_fn(self, cls: KtClass, name: str):
        seen: set[int] = set()
        cur: KtClass | None = cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if cur.funcs.get(name):
                return cur, cur.funcs[name][0]
            cur = self._base(cur)
        return None

    def _unqualified(self, name: str, args: list[Val], fr: KFrame, lam: Node | None, node: Node) -> Val:
        if isinstance(fr.env.get(name), ClosureV):
            return self._invoke(fr.env[name], args, fr)
        if self._callable_part(fr.env.get(name)):
            return self._call_value(fr.env[name], args, fr)
        if fr.cls is not None:
            hit = self._find_fn(fr.cls, name)
            if hit is not None:
                return self._run_fn(hit[0], hit[1], fr.this_obj, args, fr)
        if fr.file.main is not None and fr.file.main.funcs.get(name):
            return self._run_fn(fr.file.main, fr.file.main.funcs[name][0], None, args, fr)
        cls = self.index.cls(name, fr.file) if name[:1].isupper() else None
        if cls is not None:
            return self._instantiate(cls, args, fr)
        if name in ("listOf", "mutableListOf", "arrayListOf", "arrayOf", "setOf", "mutableSetOf", "hashSetOf", "linkedSetOf", "sequenceOf", "listOfNotNull"):
            return ListV(list(args), birth_depth=fr.branch)
        if name in ("emptyList", "emptySet", "ArrayList", "LinkedList", "HashSet", "LinkedHashSet", "mutableListOf_x"):
            return ListV([], birth_depth=fr.branch, dirty=bool(args and not isinstance(args[0], Const)))
        if name in ("mapOf", "mutableMapOf", "hashMapOf", "linkedMapOf", "emptyMap", "HashMap", "LinkedHashMap", "TreeMap", "ConcurrentHashMap"):
            m = MapV({}, birth_depth=fr.branch)
            for a in args:
                if isinstance(a, ListV) and len(a.items) == 2 and isinstance(a.items[0], Const) and isinstance(a.items[0].value, (str, int)) and not isinstance(a.items[0].value, bool):
                    m.entries[a.items[0].value] = a.items[1]
                else:
                    m.dirty = True
            return m
        if name == "Pair":
            return ListV(list(args), birth_depth=fr.branch)
        if name in ("StringBuilder", "StringBuffer"):
            return BuilderV(list(args[:1]) if args and not (isinstance(args[0], Const) and isinstance(args[0].value, int)) else [], birth_depth=fr.branch)
        if name in ("String",) and args:
            return args[0]
        if name in ("listOf_x",):
            return UNKNOWN
        if name in ("println", "print", "require", "check", "error", "TODO", "assert", "lazy", "listOf_x"):
            return SAFE
        if name == "format" and args:
            return self._format(args[0], args[1:])
        if lam is not None:
            self._run_lambda(lam, [UNKNOWN], fr)
        return self._opaque(None, args)

    def _instantiate(self, cls: KtClass, args: list[Val], fr: KFrame) -> Val:
        obj = Obj(cls=cls, type_name=cls.name)
        inner = KFrame(cls.file, cls, obj, fr.depth + 1)
        if fr.depth + 1 > self.MAX_DEPTH:
            return obj
        pos = list(args)
        for p in cls.ctor_params:
            nm = _pname(p)
            v = pos.pop(0) if pos else UNKNOWN
            inner.env[nm] = v
            if any(x.type in ("val", "var") for x in p.children):
                obj.fields[nm] = v
        for nm, init in cls.props.items():
            if init is not None:
                obj.fields[nm] = self.eval(init, inner)
        for blk in cls.inits:
            self.exec(next((c for c in blk.children if c.type == "block"), None), inner)
        return obj

    def make_this(self, cls: KtClass, kf: KtFile) -> Obj:
        try:
            obj = self._instantiate(cls, [], KFrame(kf, cls, None, 0))
        except (BudgetExceeded, RecursionError):
            return Obj(cls=cls, type_name=cls.name)
        return obj if isinstance(obj, Obj) else Obj(cls=cls, type_name=cls.name)

    def _static(self, cname: str, name: str, args: list[Val], fr: KFrame, lam: Node | None) -> Val:
        cls = self.index.cls(cname, fr.file)
        if cls is not None:
            hit = self._find_fn(cls, name)
            if hit is not None:
                return self._run_fn(hit[0], hit[1], Obj(cls=hit[0], type_name=hit[0].name), args, fr)
        if cname == "String" and name == "format" and args:
            return self._format(args[0], args[1:])
        if cname == "String" and name in ("valueOf", "join", "copyValueOf"):
            return self._combine(*args) if args else UNKNOWN
        if cname in ("Paths", "Path") and name in ("get", "of"):
            return self._combine(*args) if args else UNKNOWN
        if name in SAFE_METHODS and cname in PURE_TYPES:
            return SAFE
        if cname in PURE_TYPES and name not in ("readAllBytes", "readAllLines", "readString", "lines", "forName", "exec", "getenv_x"):
            return self._opaque(None, args, pure=True)
        if lam is not None:
            self._run_lambda(lam, [UNKNOWN], fr)
        return self._opaque(None, args)

    def _format(self, fmt: Val, vals: list[Val]) -> Val:
        if isinstance(fmt, Const) and isinstance(fmt.value, str) and all(isinstance(v, Const) for v in vals):
            try:
                py = re.sub(r"%(\d+\$)?([-#+ 0,(]*\d*(?:\.\d+)?)([sdfxXobeEgGc%n])", lambda m: "%" + m.group(2) + ("s" if m.group(3) == "s" else m.group(3)) if m.group(3) != "n" else "\n", fmt.value)
                return Const(py % tuple(v.value for v in vals))
            except (TypeError, ValueError):
                return SAFE
        return self._combine(fmt, *vals)

    def _method(self, recv: Val, name: str, args: list[Val], fr: KFrame, recv_n: Node | None, lam: Node | None) -> Val:
        if isinstance(recv, Choice):
            return join_all(self._method(a, name, args, fr, recv_n, lam) for a in recv.alts)
        if name in ("getNextEntry", "getNextJarEntry", "getNextTarEntry", "getNextZipEntry") and not args:
            return _zip_entry()
        if name == "entries" and not args and recv_n is not None and "zip" in _t(recv_n).lower():
            return ListV([_zip_entry()], birth_depth=fr.branch)
        if isinstance(recv, ClosureV) and name == "invoke":
            return self._invoke(recv, args, fr)
        if isinstance(recv, FuncV) and name == "invoke":
            return self._call_value(recv, args, fr)
        if name in LOOP_FUNS and args and isinstance(args[-1], FuncV):
            held = join_all(recv.items) if isinstance(recv, ListV) and recv.items else (Tainted("element") if classify(recv) == "tainted" else recv if name in ("let", "also", "run", "apply") and not isinstance(recv, (ListV, MapV)) else None)
            if held is not None:
                self._call_value(args[-1], [held], fr)
        if isinstance(recv, Obj):
            if recv.kind == "request":
                return SAFE if name in SERVLET_BENIGN else Tainted("request." + name)
            if recv.cls is not None:
                hit = self._find_fn(recv.cls, name)
                if hit is not None:
                    return self._run_fn(hit[0], hit[1], recv, args, fr)
            if lam is not None:
                self._run_lambda(lam, [recv], fr)
            return self._opaque(recv, args)
        if isinstance(recv, ListV):
            return self._list_method(recv, name, args, fr, lam)
        if isinstance(recv, MapV):
            return self._map_method(recv, name, args, fr, lam)
        if isinstance(recv, BuilderV):
            if name == "append" and args:
                self._mutating(fr, recv)
                recv.parts.append(args[0])
                return recv
            if name in ("insert", "replace", "delete", "setLength", "appendLine", "clear"):
                self._mutating(fr, recv)
                recv.parts.extend(args)
                recv.dirty = True
                return recv
            if name == "toString":
                return Const("".join(_s(p.value) for p in recv.parts)) if not recv.dirty and not recv.opaque and all(isinstance(p, Const) for p in recv.parts) \
                    else self._combine(*recv.parts, *([UNKNOWN] if recv.opaque else []))
            return self._opaque(recv, args)
        if isinstance(recv, Const) and isinstance(recv.value, str):
            if lam is not None:
                self._run_lambda(lam, [recv], fr)
            return self._string_method(recv, name, args)
        if name in SAFE_METHODS:
            return SAFE
        if lam is not None:
            res = self._run_lambda(lam, [Tainted("derived") if classify(recv) == "tainted" else UNKNOWN], fr)
            if name in ("let", "run", "also", "apply", "map", "with"):
                return res if name in ("let", "run", "with") else (recv if name in ("also", "apply") else UNKNOWN)
        if classify(recv) == "tainted":
            return Tainted("derived")
        return self._opaque(recv, args)

    def _string_method(self, recv: Const, name: str, args: list[Val]) -> Val:
        s = recv.value
        if name in SAFE_METHODS:
            return SAFE
        consts = all(isinstance(a, Const) for a in args)
        if name in ("split", "lines", "toList", "toCharArray", "chunked", "windowed"):
            return ListV([self._combine(recv, *args)], birth_depth=0, dirty=True)
        if not consts:
            return self._combine(recv, *args)
        v = [a.value for a in args]
        try:
            if name in ("toString", "trimIndent", "trimMargin", "intern", "orEmpty", "toLowerCaseX"):
                return recv
            if name in ("lowercase", "toLowerCase"):
                return Const(s.lower())
            if name in ("uppercase", "toUpperCase"):
                return Const(s.upper())
            if name == "trim":
                return Const(s.strip())
            if name == "plus":
                return Const(s + "".join(_s(x) for x in v))
            if name == "replace" and len(v) == 2 and isinstance(v[0], str):
                return Const(s.replace(v[0], _s(v[1])))
            if name == "substring" and v and isinstance(v[0], int):
                return Const(s[v[0]:v[1]] if len(v) > 1 and isinstance(v[1], int) else s[v[0]:])
            if name == "format":
                return self._format(recv, args)
            if name == "removePrefix" and v:
                return Const(s[len(_s(v[0])):] if s.startswith(_s(v[0])) else s)
            if name == "removeSuffix" and v:
                return Const(s[:-len(_s(v[0]))] if v[0] and s.endswith(_s(v[0])) else s)
            if name == "repeat" and v and isinstance(v[0], int):
                return Const(s * min(v[0], 1000))
        except (TypeError, ValueError, IndexError):
            return SAFE
        return SAFE

    def _list_method(self, lst: ListV, name: str, args: list[Val], fr: KFrame, lam: Node | None) -> Val:
        if name in ("add", "addLast", "addFirst", "push", "offer", "append"):
            self._mutating(fr, lst)
            if len(args) == 2:
                lst.dirty = True
            lst.items.append(args[-1] if args else UNKNOWN)
            return SAFE
        if name in ("addAll", "plusAssign", "removeAll", "retainAll", "remove", "removeAt", "clear", "set", "sort", "reverse", "shuffle", "removeIf"):
            self._mutating(fr, lst)
            lst.dirty = True
            for a in args:
                if isinstance(a, ListV):
                    lst.items.extend(a.items)
                elif name in ("set",):
                    lst.items.append(a)
            return SAFE
        if name in ("contains", "size", "isEmpty", "isNotEmpty", "indexOf", "containsAll"):
            return SAFE
        if name in LOOP_FUNS and lam is not None:
            elem = join_all(lst.items + ([UNKNOWN] if lst.dirty or lst.opaque else [])) if lst.items else UNKNOWN
            res = self._run_lambda(lam, [elem, UNKNOWN] if name in ("forEachIndexed", "mapIndexed", "fold") else [elem], fr)
            if name in ("map", "mapNotNull", "flatMap", "mapIndexed"):
                return ListV([res], birth_depth=fr.branch, dirty=True)
            if name in ("filter", "filterNot", "onEach", "sortedBy"):
                return ListV(list(lst.items), birth_depth=fr.branch, dirty=True)
            return UNKNOWN
        if name in ("joinToString",):
            sep = args[0] if args and isinstance(args[0], Const) else Const(", ")
            if not lst.dirty and not lst.opaque and all(isinstance(i, Const) for i in lst.items) and isinstance(sep, Const):
                return Const(_s(sep.value).join(_s(i.value) for i in lst.items))
            return self._combine(*lst.items, *([UNKNOWN] if lst.opaque else []))
        if name in ("get", "first", "last", "firstOrNull", "lastOrNull", "elementAt", "getOrNull", "random", "single", "min", "max", "removeFirst", "removeLast"):
            return join_all(lst.items + [UNKNOWN]) if lst.items else UNKNOWN
        if name in ("toList", "toMutableList", "toTypedArray", "toSet", "distinct", "sorted", "reversed", "asList", "toSortedSet", "take", "drop", "plus", "filterNotNull", "asSequence", "subList"):
            return ListV(list(lst.items), birth_depth=fr.branch, dirty=True, opaque=lst.opaque)
        return self._opaque(lst, args)

    def _map_method(self, m: MapV, name: str, args: list[Val], fr: KFrame, lam: Node | None) -> Val:
        key = args[0].value if args and isinstance(args[0], Const) and isinstance(args[0].value, (str, int)) and not isinstance(args[0].value, bool) else None
        if name in ("put", "set", "putIfAbsent", "getOrPut") and len(args) >= 2:
            self._mutating(fr, m)
            if key is not None:
                m.entries[key] = args[1]
            else:
                m.entries[("*", id(args[0]))] = args[1]
                m.dirty = True
            return SAFE
        if name in ("get", "getOrDefault", "getValue", "getOrElse"):
            if key is not None and not m.dirty and not m.opaque and key in m.entries:
                return m.entries[key]
            return join_all(list(m.entries.values()) + [UNKNOWN])
        if name in ("containsKey", "containsValue", "size", "isEmpty", "isNotEmpty"):
            return SAFE
        if name in ("remove", "clear", "putAll", "plusAssign", "replace"):
            self._mutating(fr, m)
            m.dirty = True
            return SAFE
        if name in ("keys", "values", "entries", "toList"):
            return ListV(list(m.entries.values()), birth_depth=fr.branch, dirty=True, opaque=m.opaque or m.dirty)
        if name in LOOP_FUNS and lam is not None:
            self._run_lambda(lam, [UNKNOWN, join_all(list(m.entries.values()) + [UNKNOWN])], fr)
            return UNKNOWN
        return self._opaque(m, args)

    # --- lambdas & functions
    def _invoke(self, clo: ClosureV, args: list[Val], fr: KFrame) -> Val:
        """Call a lambda value: its captured variables are visible, and writes to variables of the calling frame stick."""
        key = (id(clo.file), clo.node.start_byte, "lambda")
        if key in self._active or fr.depth + 1 > self.MAX_DEPTH:
            return self._opaque(None, args)
        outer = fr.env
        fr.env = {**clo.env, **outer}
        self._active.add(key)
        try:
            res = self._run_lambda(clo.node, args, fr)
        finally:
            self._active.discard(key)
            inner, fr.env = fr.env, outer
            for k in outer:
                if k in inner:
                    outer[k] = inner[k]
        return res

    def _run_lambda(self, lam: Node | None, params: list[Val], fr: KFrame) -> Val:
        if lam is None:
            return UNKNOWN
        lp = next((c for c in lam.children if c.type == "lambda_parameters"), None)
        names = [_t(next((x for x in c.children if x.type == "identifier"), None)) for c in (lp.named_children if lp is not None else []) if c.type in ("variable_declaration", "multi_variable_declaration")] or ["it"]
        saved = {k: fr.env.get(k) for k in names}
        had = {k: (k in fr.env) for k in names}
        for i, k in enumerate(names):
            fr.env[k] = params[i] if i < len(params) else UNKNOWN
        stmts = [c for c in lam.named_children if c.type not in ("lambda_parameters",)]
        fr.branch += 1
        before = fr.last
        fr.last = UNKNOWN
        try:
            for st in stmts:
                if st.type == "statements":
                    self.exec(st, fr)
                else:
                    self.exec(st, fr)
            last = fr.last
        finally:
            fr.branch -= 1
            fr.last = before
            for k in names:
                if had[k]:
                    fr.env[k] = saved[k]
                else:
                    fr.env.pop(k, None)
        return last

    def _run_fn(self, cls: KtClass, fn: Node, recv: Obj | None, args: list[Val], fr: KFrame) -> Val:
        key = (id(cls.file), fn.start_byte)
        if fr.depth + 1 > self.MAX_DEPTH or key in self._active:
            return self._opaque(recv, args)
        inner = KFrame(cls.file, cls, recv, fr.depth + 1)
        pos = list(args)
        for p in _params(fn):
            default = next((c for c in p.named_children if c.start_byte > max((x.end_byte for x in p.named_children if x.type in ("user_type", "nullable_type")), default=-1)), None)
            if pos:
                inner.env[_pname(p)] = pos.pop(0)
            else:
                inner.env[_pname(p)] = self.eval(default, inner) if default is not None else UNKNOWN
        body = next((c for c in fn.children if c.type == "function_body"), None)
        self._active.add(key)
        try:
            if body is not None:
                inner_expr = next((c for c in body.named_children), None)
                if inner_expr is not None and inner_expr.type != "block":
                    inner.last = self.eval(inner_expr, inner)
                else:
                    self.exec(inner_expr, inner)
        finally:
            self._active.discard(key)
        return join_all(inner.rets + [inner.last]) if inner.rets else inner.last

    def bind_entry(self, fn: Node, fr: KFrame, action) -> None:
        lib, action = action == "lib", action is True
        for p in _params(fn):
            nm, tn = _pname(p), _ptype(p)
            ann = annotations(p)
            if tn in REQUEST_TYPES:
                fr.env[nm] = Obj(kind="request", type_name=tn)
            elif ann & PARAM_ANNOTATIONS:
                fr.env[nm] = SAFE if tn in SAFE_PARAM_TYPES else Tainted("param")
            elif lib and tn in ("String", "CharSequence"):
                fr.env[nm] = Tainted("library parameter")  # a public function of a library: its caller chooses the arguments
            elif action and tn in SAFE_PARAM_TYPES:
                fr.env[nm] = SAFE
            elif action and tn in ("String", "CharSequence", "Map", "List", "MultipartFile", "") or (action and tn and tn[0].isupper() and tn not in ("HttpServletResponse", "Model", "BindingResult", "Principal", "HttpSession", "Authentication", "RedirectAttributes", "Pageable", "Locale", "ModelMap", "Logger")):
                fr.env[nm] = Tainted("param") if tn in ("String", "CharSequence", "Map", "List", "MultipartFile") else UNKNOWN
            else:
                fr.env[nm] = UNKNOWN

    # ------------------------------------------------------------------ statements
    def exec(self, n: Node | None, fr: KFrame) -> bool:
        if n is None:
            return False
        self._tick()
        t = n.type
        if t in ("block", "statements", "control_structure_body", "function_body", "source_file"):
            for c in n.named_children:
                if self.exec(c, fr):
                    return True
            return False
        if t == "property_declaration":
            vd = next((c for c in n.children if c.type in ("variable_declaration", "multi_variable_declaration")), None)
            init = next((c for c in n.named_children if vd is not None and c.start_byte > vd.end_byte and c.type != "property_delegate"), None)
            val = self.eval(init, fr) if init is not None else UNKNOWN
            if vd is not None and vd.type == "variable_declaration":
                fr.env[_t(next((c for c in vd.children if c.type == "identifier"), None))] = val
            elif vd is not None:
                names = [_t(next((x for x in c.children if x.type == "identifier"), None)) for c in vd.named_children if c.type == "variable_declaration"]
                for i, nm in enumerate(names):
                    if isinstance(val, ListV) and not val.dirty and not val.opaque and i < len(val.items):
                        fr.env[nm] = val.items[i]
                    else:
                        fr.env[nm] = Tainted("derived") if classify(val) == "tainted" else UNKNOWN
            fr.last = Const(None)
            return False
        if t == "return_expression":
            kids = [c for c in n.named_children if c.type != "label"]
            v = self.eval(kids[0], fr) if kids else Const(None)
            fr.rets.append(v)
            fr.last = v
            return True
        if t == "throw_expression":
            for c in n.named_children:
                self.eval(c, fr)
            fr.last = UNKNOWN
            return True
        if t == "if_expression":
            return self._if(n, fr)
        if t == "when_expression":
            return self._when(n, fr)
        if t == "try_expression":
            return self._try(n, fr)
        if t in ("for_statement", "while_statement", "do_while_statement"):
            return self._loop(n, fr)
        if t == "jump_expression":
            fr.last = UNKNOWN
            return True
        if t in ("function_declaration", "class_declaration", "object_declaration", "comment", "line_comment", "multiline_comment", "import", "package_header"):
            return False
        if t == "identifier" and _t(n) in ("break", "continue"):
            return True
        fr.last = self.eval(n, fr)
        return False

    def _join_envs(self, fr: KFrame, a: dict, b: dict) -> None:
        out = {}
        for k in set(a) | set(b):
            out[k] = join(a[k], b[k]) if (k in a and k in b) else (a[k] if k in a else b[k])
        fr.env = out

    # ------------------------------------------------------------------ guards
    def _call_facts(self, name: str, args: list, fr: KFrame):
        """`if (!ok(v))` where ok returns a check on its parameter."""
        if name.startswith("!"):  # the grammar folds the `!` into the callee's name
            got = self._call_facts(name.lstrip("!").strip(), args, fr)
            return G.negate(got) if got is not None else None
        hit = self._find_fn(fr.cls, name) if fr.cls is not None else None
        if hit is None and fr.file.main is not None and fr.file.main.funcs.get(name):
            hit = (fr.file.main, fr.file.main.funcs[name][0])
        if hit is None or id(hit[1]) in self._active:
            return None
        owner, fn = hit
        pl = next((c for c in fn.children if c.type == "function_value_parameters"), None)
        names = []
        for p in (pl.named_children if pl is not None else []):
            ident = next((c for c in p.children if c.type == "identifier"), None)
            names.append(_t(ident) if p.type == "parameter" and ident is not None else "")
        body = next((c for c in fn.children if c.type == "function_body"), None)
        if body is None:
            return None
        expr = [c for c in body.named_children if c.type != "block"]
        if expr:
            rets = [expr[-1]]  # `fun ok(v: String) = ...`
        else:
            rets, stack = [], [body]
            while stack:
                cur = stack.pop()
                if cur.type == "return_expression" and cur.named_children:
                    rets.append(cur.named_children[-1])
                elif cur.type not in ("lambda_literal", "annotated_lambda"):
                    stack.extend(cur.children)
        if len(rets) != 1:
            return None
        cf = KFrame(owner.file, owner, None, fr.depth + 1, env={x: Tainted("param") for x in names if x})
        self._active.add(id(fn))
        try:
            facts = self._engine(cf).facts(rets[0])
        finally:
            self._active.discard(id(fn))
        return G.map_facts(facts, args, names, lambda n: _t(n) if n is not None and n.type == "identifier" and _t(n) in fr.env else None)

    def _engine(self, fr: KFrame) -> G.GuardEngine:
        def norm(n):
            t = n.type
            if t == "parenthesized_expression":
                return ("paren", n.named_children[0] if n.named_children else None)
            if t in ("prefix_expression", "unary_expression") and n.children and _t(n.children[0]) == "!":
                return ("not", n.named_children[-1] if n.named_children else None)
            if t == "in_expression":
                op = next((_t(c) for c in n.children if _t(c) in ("in", "!in")), "in")
                r = n.child_by_field_name("right")
                return ("call" if op == "in" else "neg_call", "contains", r, [n.child_by_field_name("left")], _t(r))
            if t == "binary_expression":
                op = _t(n.child_by_field_name("operator"))
                l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
                if op in ("in", "!in"):
                    return ("call" if op == "in" else "neg_call", "contains", r, [l], _t(r))
                return {"&&": ("and", l, r), "||": ("or", l, r), "==": ("eq", l, r), "===": ("eq", l, r), "!=": ("ne", l, r), "!==": ("ne", l, r)}.get(op, ("other",))
            if t == "call_expression":
                callee = n.children[0] if n.children else None
                va = next((c for c in n.children if c.type == "value_arguments"), None)
                args = []
                for a in (va.named_children if va is not None else []):
                    kids = [c for c in a.named_children if c.type != "annotation"]
                    if a.type == "value_argument" and kids:
                        args.append(kids[-1])
                lam_text = _t(next((c for c in n.children if c.type == "annotated_lambda"), None))
                if callee is not None and callee.type == "navigation_expression" and lam_text and self._split_nav(callee)[1] == "all" \
                        and re.search(r"\.(isDigit|isLetter|isLetterOrDigit|isUpperCase|isLowerCase)\(\)", lam_text):
                    recv0 = self._split_nav(callee)[0]
                    if recv0 is not None and recv0.type in ("prefix_expression", "unary_expression") and recv0.children and _t(recv0.children[0]) == "!":
                        recv0 = recv0.named_children[-1]
                    return ("neg_call" if _t(n).lstrip().startswith("!") else "call", "isNumeric", None, [recv0], "")  # name.all { it.isDigit() }
                if callee is not None and callee.type == "navigation_expression":
                    recv_n, nm, _ = self._split_nav(callee)
                    neg = _t(n).lstrip().startswith("!")  # the grammar binds `!` tighter than `.` and a call: `!x.f(..)` parses as `(!x).f(..)`
                    inner = recv_n
                    if recv_n is not None and recv_n.type in ("prefix_expression", "unary_expression") and recv_n.children and _t(recv_n.children[0]) == "!":
                        inner = recv_n.named_children[-1] if recv_n.named_children else None
                    return ("neg_call" if neg else "call", nm, inner, args, _t(inner).lstrip("!"))
                return ("call", _t(callee), None, args, "")
            return ("other",)

        def local(n):
            return _t(n) if n is not None and n.type == "identifier" and _t(n) in fr.env else None

        def const(n):
            if n is None:
                return None
            v = self.eval(n, fr)
            if isinstance(v, Const) and isinstance(v.value, str):
                return v.value
            if n.type == "call_expression" and n.children and _t(n.children[0]) == "Regex":
                return self._regex_arg(n, fr)
            return None

        def safe(n):
            return classify(self.eval(n, fr)) == "safe"

        def coll(n):
            v = self.eval(n, fr)
            return isinstance(v, ListV) and bool(v.items) and not v.dirty and not v.opaque and all(classify(i) == "safe" for i in v.items)

        def pattern_source(n):
            if n is None:
                return None
            if n.type == "call_expression" and n.children and _t(n.children[0]).lstrip("!") == "Regex":
                return self._regex_arg(n, fr)
            if n.type == "navigation_expression":
                recv_n, nm, _ = self._split_nav(n)
                if nm == "toRegex" and recv_n is not None:
                    v = self.eval(recv_n, fr)
                    return v.value if isinstance(v, Const) and isinstance(v.value, str) else None
            if n.type == "identifier":
                c = fr.cls
                while c is not None:
                    init = c.consts.get(_t(n)) or c.props.get(_t(n))
                    if init is not None:
                        return pattern_source(init)
                    c = self._base(c)
                if fr.file.main is not None:
                    init = fr.file.main.consts.get(_t(n))
                    if init is not None:
                        return pattern_source(init)
            return None

        def attr(n):
            if n is None or n.type != "navigation_expression":
                return None
            recv_n, nm, _ = self._split_nav(n)
            if local(recv_n):
                return (local(recv_n), nm)
            if recv_n is not None and recv_n.type == "call_expression" and recv_n.children and _t(recv_n.children[0]) in ("URI", "URL"):  # URI(x).host
                va = next((c for c in recv_n.children if c.type == "value_arguments"), None)
                first = va.named_children[0] if va is not None and va.named_children else None
                kids = [c for c in (first.named_children if first is not None else []) if c.type != "annotation"]
                if kids and local(kids[-1]):
                    return (local(kids[-1]), nm)
            return None

        return G.GuardEngine(norm, local, const, safe, coll, pattern_source, self.cwes, anchored=("matches",), attr=attr, call_facts=lambda name, args: self._call_facts(name, args, fr), canon=lambda var: G.canonical_assignment(fr.file.src, var), untainted=lambda n: n is not None and classify(self.eval(n, fr)) != "tainted")

    def _regex_arg(self, call: Node, fr: KFrame) -> str | None:
        va = next((c for c in call.children if c.type == "value_arguments"), None)
        first = va.named_children[0] if va is not None and va.named_children else None
        kids = [c for c in (first.named_children if first is not None else []) if c.type != "annotation"]
        v = self.eval(kids[-1], fr) if kids else None
        return v.value if isinstance(v, Const) and isinstance(v.value, str) else None

    def _narrow(self, names: set, fr: KFrame) -> None:
        G.apply(names, self.cwes, fr.env, SAFE)

    def _if(self, n: Node, fr: KFrame) -> bool:
        cond_n = n.child_by_field_name("condition")
        cond = self.eval(cond_n, fr)
        else_tok = next((c for c in n.children if c.type == "else"), None)
        bodies = [c for c in n.named_children if c is not cond_n and c.start_byte >= (cond_n.end_byte if cond_n is not None else 0)]
        cons = next((c for c in bodies if else_tok is None or c.start_byte < else_tok.start_byte), None)
        alt = next((c for c in bodies if else_tok is not None and c.start_byte > else_tok.start_byte), None)
        if isinstance(cond, Const) and isinstance(cond.value, bool):
            self._dead(fr, alt if cond.value else cons)
            branch = cons if cond.value else alt
            if branch is None:
                fr.last = Const(None)
                return False
            return self.exec(branch, fr)
        base = dict(fr.env)
        facts = self._engine(fr).facts(cond_n)
        fr.branch += 1
        try:
            fr.last = UNKNOWN
            self._narrow(facts[0], fr)
            ta = self.exec(cons, fr) if cons is not None else False
            ea, la = fr.env, fr.last if cons is not None else Const(None)
            fr.env = dict(base)
            fr.last = UNKNOWN
            self._narrow(facts[1], fr)
            tb = self.exec(alt, fr) if alt is not None else False
            eb, lb = fr.env, fr.last if alt is not None else Const(None)
        finally:
            fr.branch -= 1
        if ta and tb:
            fr.env = ea
            return True
        if ta:
            fr.env, fr.last = eb, lb
        elif tb:
            fr.env, fr.last = ea, la
        else:
            self._join_envs(fr, ea, eb)
            fr.last = join(la, lb)
        return False

    def _when(self, n: Node, fr: KFrame) -> bool:
        subj = next((c for c in n.children if c.type == "when_subject"), None)
        if subj is not None:
            for c in subj.named_children:
                self.eval(c, fr)
        base = dict(fr.env)
        envs, lasts, has_else = [], [], False
        fr.branch += 1
        try:
            for e in [c for c in n.named_children if c.type == "when_entry"]:
                fr.env = dict(base)
                arrow = next((c for c in e.children if c.type == "->"), None)
                if any(c.type == "else" for c in e.children):
                    has_else = True
                for c in e.named_children:
                    if arrow is not None and c.start_byte < arrow.start_byte:
                        if c.type != "when_condition":
                            self.eval(c, fr)
                fr.last = UNKNOWN
                body = next((c for c in e.named_children if arrow is not None and c.start_byte > arrow.start_byte), None)
                term = self.exec(body, fr) if body is not None else False
                if not term:
                    envs.append(fr.env)
                    lasts.append(fr.last)
        finally:
            fr.branch -= 1
        if not has_else:
            envs.append(base)
            lasts.append(Const(None))
        if not envs:
            fr.env = base
            return True
        fr.env = envs[0]
        for e in envs[1:]:
            self._join_envs(fr, fr.env, e)
        fr.last = join_all(lasts)
        return False

    def _try(self, n: Node, fr: KFrame) -> bool:
        base = dict(fr.env)
        body = next((c for c in n.children if c.type == "block"), None)
        term = self.exec(body, fr)
        after, last_main = fr.env, fr.last
        envs = [] if term else [after]
        lasts = [] if term else [last_main]
        all_term = term
        for c in n.named_children:
            if c.type == "catch_block":
                fr.env = {k: join(base.get(k, v), v) if k in base else v for k, v in after.items()}
                var = next((x for x in c.children if x.type == "identifier"), None)
                if var is not None:
                    fr.env[_t(var)] = UNKNOWN
                fr.branch += 1
                try:
                    fr.last = UNKNOWN
                    cterm = self.exec(next((x for x in c.children if x.type == "block"), None), fr)
                finally:
                    fr.branch -= 1
                all_term = all_term and cterm
                if not cterm:
                    envs.append(fr.env)
                    lasts.append(fr.last)
        if envs:
            fr.env = envs[0]
            for e in envs[1:]:
                self._join_envs(fr, fr.env, e)
            fr.last = join_all(lasts)
        else:
            fr.env = after
        for c in n.named_children:
            if c.type == "finally_block":
                keep = fr.last
                self.exec(next((x for x in c.children if x.type == "block"), None), fr)
                fr.last = keep
        return all_term and not envs

    def _loop(self, n: Node, fr: KFrame) -> bool:
        base = dict(fr.env)
        fr.branch += 1
        try:
            if n.type == "for_statement":
                var = next((c for c in n.children if c.type in ("variable_declaration", "multi_variable_declaration")), None)
                in_tok = next((c for c in n.children if c.type == "in"), None)
                it_n = next((c for c in n.named_children if in_tok is not None and c.start_byte > in_tok.start_byte and c.type != "block"), None)
                it = self.eval(it_n, fr)
                elem = join_all(it.items + ([UNKNOWN] if it.dirty or it.opaque else [])) if isinstance(it, ListV) and it.items else (Tainted("derived") if classify(it) == "tainted" else UNKNOWN)
                if var is not None:
                    names = [_t(c) for c in var.children if c.type == "identifier"] if var.type == "variable_declaration" else [_t(next((x for x in c.children if x.type == "identifier"), None)) for c in var.named_children]
                    for nm in names:
                        fr.env[nm] = elem
            else:
                self.eval(n.child_by_field_name("condition"), fr)
            body = next((c for c in n.named_children if c.type in ("block", "control_structure_body")), None)
            if body is None:
                # a brace-less body (`for (i in xs) x = y`) is the statement after the closing parenthesis
                rp = [c for c in n.children if c.type == ")"]
                if rp and n.type != "do_while_statement":
                    body = next((c for c in n.named_children if c.start_byte > rp[-1].end_byte), None)
            self.exec(body, fr)
        finally:
            fr.branch -= 1
        self._join_envs(fr, base, fr.env)
        fr.last = Const(None)
        return False
