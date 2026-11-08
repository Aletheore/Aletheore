"""An abstract interpreter for Ruby, built on tree-sitter. Same stance as the other interpreters: follow request data
(`params`, `cookies`, `request.*`) to sinks and prove values safe; anything it cannot model is UNKNOWN. Ruby specifics:
every statement is an expression with an implicit return value, strings are mutable (`<<`), `#{}` interpolation and
heredocs, keyword-argument hashes, blocks, attr accessors, and `private` sections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter import Node

from aletheore.flow.rb_index import RbClass, RbFile, RbIndex, const_name
from aletheore.flow.rb_sinks import (
    DB_FINDERS, PURE_RECEIVERS, REQUEST_NAMES, REQUEST_OBJ_NAMES, SAFE_METHODS, SANITIZERS, SINATRA_VERBS,
)
from aletheore.flow.values import (
    SAFE, UNKNOWN, BuilderV, Choice, Const, ListV, MapV, Obj, Tainted, Val, classify, join, join_all,
)

BENIGN_REQ = {"host", "host_with_port", "base_url", "protocol", "scheme", "port", "remote_ip", "ip", "ssl?", "get?", "post?", "put?", "delete?", "xhr?", "xml_http_request?", "local?", "request_method", "method", "request_id", "format_x"}
STR_PASS = {"to_s", "to_str", "freeze", "dup", "clone", "+@", "-@", "itself", "to_sym", "to_param", "html_safe_x", "unicode_normalize", "encode", "force_encoding", "b",
            "scrub", "chomp", "chop", "chr", "strip", "lstrip", "rstrip", "squeeze", "reverse", "swapcase", "upcase", "downcase", "capitalize", "succ", "next"}
STR_ARGS = {"gsub", "sub", "tr", "delete", "slice", "ljust", "rjust", "center", "[]", "%", "*", "+", "concat", "prepend", "insert", "squeeze", "tr_s", "delete_prefix", "delete_suffix",
            "truncate", "first", "last", "chomp", "partition_x", "format", "sprintf", "each_char_x", "join", "titleize", "camelize", "underscore", "humanize", "parameterize", "pluralize", "singularize"}
MUTATORS = {"concat", "prepend", "insert", "replace", "gsub!", "sub!", "tr!", "squeeze!", "upcase!", "downcase!", "strip!", "chomp!", "capitalize!", "swapcase!", "reverse!", "<<", "clear", "force_encoding", "delete!", "slice!", "lstrip!", "rstrip!", "chop!", "succ!", "next!", "unicode_normalize!", "tr_s!"}
LOOP_METHODS = {"each", "map", "collect", "flat_map", "each_with_index", "each_with_object", "select", "filter", "reject", "find", "detect", "each_char", "each_line", "times", "upto", "downto", "loop", "tap", "then", "yield_self", "each_pair", "each_key", "each_value", "inject", "reduce", "sum", "group_by", "partition", "sort_by", "min_by", "max_by", "find_all", "each_slice", "each_cons", "zip_x", "step", "each_index", "delete_if", "keep_if", "find_index", "count", "take_while", "drop_while", "each_entry"}
MAP_METHODS = {"map", "collect", "flat_map", "filter_map"}


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class RFrame:
    file: RbFile
    cls: RbClass | None
    this_obj: Obj | None
    depth: int
    env: dict = field(default_factory=dict)
    rets: list = field(default_factory=list)
    branch: int = 0
    last: Val = UNKNOWN
    singleton: bool = False


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _s(v: object) -> str:
    return "" if v is None else ("true" if v is True else "false" if v is False else str(v))


def _truthy(c: Const) -> bool:
    return not (c.value is None or c.value is False)


_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "s": " ", "e": "\x1b", "\\": "\\", '"': '"', "'": "'", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def _unesc(raw: str) -> str:
    if raw.startswith("\\") and len(raw) >= 2:
        return _ESC.get(raw[1], raw[1:] if raw[1] not in "xuU01234567" else "\x00")
    return raw


def _params(m: Node) -> Node | None:
    return m.child_by_field_name("parameters") or next((c for c in m.children if c.type in ("method_parameters", "parameters")), None)


def _arity_range(m: Node) -> tuple[int, int]:
    p = _params(m)
    lo = hi = 0
    for c in (p.named_children if p is not None else []):
        if c.type == "identifier" or c.type == "destructured_parameter":
            lo += 1
            hi += 1
        elif c.type == "optional_parameter":
            hi += 1
        elif c.type in ("splat_parameter",):
            hi += 1000
    return lo, hi


class RbInterp:
    MAX_STEPS = 60000
    MAX_DEPTH = 6

    def __init__(self, index: RbIndex, cwes: set[int] | None = None, sink_hook=None, store_hook=None):
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
        if self.steps > self.MAX_STEPS:
            raise BudgetExceeded

    def _key(self, fr: RFrame, n: Node) -> tuple:
        return (id(fr.file), n.start_byte, n.end_byte)

    def _dead(self, fr: RFrame, n: Node | None) -> None:
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
        return {"safe": SAFE, "unknown": UNKNOWN, "tainted": Tainted("derived")}[worst]

    def _mutating(self, fr: RFrame, c) -> None:
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
    def eval(self, node: Node | None, fr: RFrame) -> Val:
        if node is None:
            return UNKNOWN
        self._tick()
        val = self._eval(node, fr)
        key = self._key(fr, node)
        if key in self.probe_keys:
            self.probes.setdefault(key, []).append((val, classify(val)))
        return val

    def _eval(self, n: Node, fr: RFrame) -> Val:
        t = n.type
        if t == "string":
            return self._string(n, fr)
        if t == "chained_string":
            return self._concat_vals([self.eval(c, fr) for c in n.named_children])
        if t == "heredoc_beginning":
            return self._heredoc(n, fr)
        if t == "subshell":
            v = self._string(n, fr)
            if self.sink_hook is not None:
                self.sink_hook(n, "<subshell>", "", [v], None, fr)
            return v if classify(v) == "tainted" else UNKNOWN
        if t in ("simple_symbol", "hash_key_symbol"):
            return Const(_t(n).lstrip(":"))
        if t == "delimited_symbol":
            return self._string(n, fr)
        if t == "integer":
            try:
                return Const(int(_t(n).replace("_", ""), 0))
            except ValueError:
                return SAFE
        if t == "float":
            try:
                return Const(float(_t(n).replace("_", "")))
            except ValueError:
                return SAFE
        if t == "true":
            return Const(True)
        if t == "false":
            return Const(False)
        if t == "nil":
            return Const(None)
        if t == "self":
            return fr.this_obj or UNKNOWN
        if t == "identifier":
            return self._ident(_t(n), fr, n)
        if t == "constant":
            return self._constant(_t(n), fr)
        if t == "scope_resolution":
            return self._scope(n, fr)
        if t == "instance_variable":
            return fr.this_obj.fields.get(_t(n), UNKNOWN) if fr.this_obj is not None else UNKNOWN
        if t in ("global_variable", "class_variable"):
            return UNKNOWN
        if t == "parenthesized_statements":
            v: Val = Const(None)
            for c in n.named_children:
                v = self.eval(c, fr)
            return v
        if t == "element_reference":
            return self._element(n, fr)
        if t == "call":
            return self._call(n, fr)
        if t == "binary":
            return self._binary(n, fr)
        if t == "unary":
            return self._unary(n, fr)
        if t == "conditional":
            return self._ternary(n, fr)
        if t == "assignment":
            return self._assign(n, fr)
        if t == "operator_assignment":
            return self._op_assign(n, fr)
        if t in ("array",):
            return ListV([self.eval(c, fr) for c in n.named_children if c.type != "comment"], birth_depth=fr.branch)
        if t in ("string_array", "symbol_array"):
            return ListV([self._string(c, fr) if c.type == "bare_string" else Const(_t(c).lstrip(":")) for c in n.named_children], birth_depth=fr.branch)
        if t == "hash":
            return self._hash(n, fr)
        if t == "range":
            a, b = n.child_by_field_name("begin"), n.child_by_field_name("end")
            return SAFE if all(classify(self.eval(x, fr)) == "safe" for x in (a, b) if x is not None) else UNKNOWN
        if t in ("regex", "lambda_x"):
            for c in n.named_children:
                if c.type == "interpolation":
                    self.eval(c, fr)
            return SAFE if t == "regex" else UNKNOWN
        if t in ("splat_argument", "block_argument", "hash_splat_argument"):
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t in ("if", "unless", "elsif", "if_modifier", "unless_modifier", "case", "begin", "while", "until", "while_modifier", "until_modifier", "for", "return", "then", "else",
                 "body_statement", "rescue_modifier", "do_block", "block", "break", "next", "redo", "retry", "case_match", "yield"):
            self.exec(n, fr)
            return fr.last
        if t == "interpolation":
            return self.eval(n.named_children[0], fr) if n.named_children else Const("")
        if t == "lambda":
            return UNKNOWN
        return UNKNOWN

    # --- literals
    def _concat_vals(self, parts: list[Val]) -> Val:
        if all(isinstance(p, Const) for p in parts):
            return Const("".join(_s(p.value) for p in parts))
        return self._combine(*parts)

    def _string(self, n: Node, fr: RFrame) -> Val:
        parts: list[Val] = []
        for c in n.children:
            if c.type in ("string_content", "heredoc_content"):
                parts.append(Const(_t(c)))
            elif c.type == "escape_sequence":
                parts.append(Const(_unesc(_t(c))))
            elif c.type == "interpolation":
                expr = next((x for x in c.named_children), None)
                v = self.eval(expr, fr) if expr is not None else Const("")
                parts.append(v)
        return self._concat_vals(parts)

    def _heredoc(self, n: Node, fr: RFrame) -> Val:
        cur: Node | None = n
        while cur is not None:
            sib = cur.next_named_sibling
            while sib is not None:
                if sib.type == "heredoc_body" and sib.start_byte >= n.end_byte:
                    return self._string(sib, fr)
                sib = sib.next_named_sibling
            cur = cur.parent
        return UNKNOWN

    def _hash(self, n: Node, fr: RFrame) -> Val:
        m = MapV({}, birth_depth=fr.branch)
        for c in n.named_children:
            if c.type == "pair":
                k = c.child_by_field_name("key") or (c.named_children[0] if c.named_children else None)
                v = c.child_by_field_name("value") or (c.named_children[-1] if len(c.named_children) > 1 else None)
                kv = self.eval(k, fr)
                vv = self.eval(v, fr)
                if isinstance(kv, Const) and isinstance(kv.value, (str, int)) and not isinstance(kv.value, bool):
                    m.entries[kv.value] = vv
                else:
                    m.entries[("*", id(c))] = vv
                    m.dirty = True
            elif c.type == "hash_splat_argument":
                self.eval(c, fr)
                m.dirty = True
                m.opaque = True
        return m

    # --- names
    def _ident(self, name: str, fr: RFrame, n: Node | None = None) -> Val:
        if name in fr.env:
            return fr.env[name]
        if name in REQUEST_NAMES:
            return Tainted("params" if name == "params" else name)
        if name in REQUEST_OBJ_NAMES:
            return Obj(kind="request", type_name="Request")
        if name == "session":
            return Obj(type_name="session")
        # a bare identifier is a call with no arguments
        return self._call_unqualified(name, [], fr, n)

    def _constant(self, name: str, fr: RFrame) -> Val:
        c = fr.cls
        seen: set[int] = set()
        while c is not None and id(c) not in seen:
            seen.add(id(c))
            if name in c.consts and c.consts[name] is not None:
                return self._const_value(c, name, fr)
            c = self._base(c)
        if fr.file.main is not None and name in fr.file.main.consts:
            return self._const_value(fr.file.main, name, fr)
        return UNKNOWN

    def _base(self, c: RbClass) -> RbClass | None:
        for b in c.bases:
            found = self.index.cls(b, c.file)
            if found is not None and found is not c:
                return found
        return None

    def _const_value(self, cls: RbClass, name: str, fr: RFrame) -> Val:
        init = cls.consts.get(name)
        key = (id(cls.file), init.start_byte if init is not None else -1)
        if init is None or key in self._active or fr.depth > self.MAX_DEPTH:
            return UNKNOWN
        self._active.add(key)
        try:
            v = self.eval(init, RFrame(cls.file, cls, None, fr.depth + 1))
        finally:
            self._active.discard(key)
        return v if classify(v) == "safe" else UNKNOWN

    def _scope(self, n: Node, fr: RFrame) -> Val:
        scope, name = n.child_by_field_name("scope"), _t(n.child_by_field_name("name"))
        if scope is not None:
            cls = self.index.cls(const_name(scope), fr.file)
            if cls is not None and name in cls.consts:
                return self._const_value(cls, name, fr)
        return UNKNOWN

    # --- element access
    def _element(self, n: Node, fr: RFrame) -> Val:
        obj = n.child_by_field_name("object")
        base = self.eval(obj, fr)
        idx_n = next((c for c in n.named_children if c is not obj and c.start_byte > (obj.end_byte if obj is not None else -1)), None)
        idx = self.eval(idx_n, fr) if idx_n is not None else UNKNOWN
        if isinstance(base, Obj):
            if base.kind == "request":
                return Tainted("request[...]")
            if base.kind == "request_env":
                key = str(idx.value) if isinstance(idx, Const) else ""
                return Tainted("request.env") if key.startswith("HTTP_") or "origin" in key or "referer" in key.lower() else UNKNOWN
            return UNKNOWN
        if isinstance(base, ListV):
            if isinstance(idx, Const) and isinstance(idx.value, int) and not isinstance(idx.value, bool) and not base.dirty and not base.opaque and -len(base.items) <= idx.value < len(base.items):
                return base.items[idx.value]
            return join_all(base.items + [UNKNOWN]) if base.items else UNKNOWN
        if isinstance(base, MapV):
            if isinstance(idx, Const) and not base.dirty and not base.opaque and idx.value in base.entries:
                return base.entries[idx.value]
            if isinstance(idx, Const) and not base.dirty and not base.opaque:
                return Const(None)
            return join_all(list(base.entries.values()) + [UNKNOWN])
        if isinstance(base, Const) and isinstance(base.value, str):
            return self._combine(base, idx)
        if classify(base) == "tainted":
            return Tainted("params[...]") if isinstance(base, Tainted) and base.why.startswith("params") else Tainted("derived")
        return UNKNOWN

    # --- operators
    def _binary(self, n: Node, fr: RFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        left_n, right_n = n.child_by_field_name("left"), n.child_by_field_name("right")
        a = self.eval(left_n, fr)
        if op in ("&&", "and", "||", "or"):
            if isinstance(a, Const):
                short = (op in ("&&", "and") and not _truthy(a)) or (op in ("||", "or") and _truthy(a))
                if short:
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
        if op == "<<":
            return self._append(left_n, a, b, fr)
        if isinstance(a, Const) and isinstance(b, Const):
            x, y = a.value, b.value
            try:
                if op == "+" and isinstance(x, str) and isinstance(y, str):
                    return Const(x + y)
                if op == "*" and isinstance(x, str) and isinstance(y, int) and not isinstance(y, bool):
                    return Const(x * min(y, 1000))
                if op == "%" and isinstance(x, str):
                    return self._format(x, [b])
                num = (int, float)
                if isinstance(x, num) and isinstance(y, num) and not isinstance(x, bool) and not isinstance(y, bool):
                    table = {"+": lambda: x + y, "-": lambda: x - y, "*": lambda: x * y, "/": lambda: x // y if isinstance(x, int) and isinstance(y, int) else x / y,
                             "%": lambda: x % y, "<": lambda: x < y, ">": lambda: x > y, "<=": lambda: x <= y, ">=": lambda: x >= y, "==": lambda: x == y, "!=": lambda: x != y}
                    if op in table and not (op in ("/", "%") and y == 0):
                        return Const(table[op]())
                if op in ("==", "!=", "===", "eql?"):
                    return Const((x == y) if op in ("==", "===", "eql?") else (x != y))
            except (TypeError, ValueError, OverflowError):
                pass
            return SAFE
        if op in ("==", "!=", "===", "=~", "!~", "<", ">", "<=", ">=", "<=>"):
            return SAFE
        if op == "+" and isinstance(a, ListV) and isinstance(b, ListV):
            return ListV(a.items + b.items, birth_depth=fr.branch, dirty=a.dirty or b.dirty, opaque=a.opaque or b.opaque)
        return self._combine(a, b)

    def _append(self, target: Node | None, a: Val, b: Val, fr: RFrame) -> Val:
        if isinstance(a, ListV):
            self._mutating(fr, a)
            a.items.append(b)
            return a
        if isinstance(a, BuilderV):
            self._mutating(fr, a)
            a.parts.append(b)
            return a
        if isinstance(a, Const) and isinstance(a.value, str) and isinstance(b, Const):
            new: Val = Const(a.value + _s(b.value))
        else:
            new = self._combine(a, b)
        if target is not None and target.type in ("identifier", "instance_variable"):
            self._store(target, new, fr)
        elif target is not None and target.type in ("element_reference", "call"):
            self._store(target, new, fr)
        return new

    def _unary(self, n: Node, fr: RFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        v = self.eval(n.child_by_field_name("operand"), fr)
        if op in ("!", "not") and isinstance(v, Const):
            return Const(not _truthy(v))
        if op in ("-", "+", "~"):
            return SAFE if classify(v) == "safe" else self._combine(v)
        return SAFE if op in ("!", "not", "defined?") else self._combine(v)

    def _ternary(self, n: Node, fr: RFrame) -> Val:
        cond = self.eval(n.child_by_field_name("condition"), fr)
        cons_n, alt_n = n.child_by_field_name("consequence"), n.child_by_field_name("alternative")
        if isinstance(cond, Const):
            self._dead(fr, alt_n if _truthy(cond) else cons_n)
            return self.eval(cons_n if _truthy(cond) else alt_n, fr)
        fr.branch += 1
        try:
            return join(self.eval(cons_n, fr), self.eval(alt_n, fr))
        finally:
            fr.branch -= 1

    # --- assignment
    def _assign(self, n: Node, fr: RFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        if left is not None and left.type == "left_assignment_list":
            val = self.eval(right, fr)
            targets = [c for c in left.named_children]
            if isinstance(val, ListV) and not val.dirty and not val.opaque and len(val.items) == len(targets):
                for tgt, item in zip(targets, val.items):
                    self._store(tgt, item, fr)
            else:
                elem = Tainted("derived") if classify(val) == "tainted" else UNKNOWN
                for tgt in targets:
                    self._store(tgt, elem, fr)
            return val
        if right is not None and right.type == "right_assignment_list":
            items = [self.eval(c, fr) for c in right.named_children]
            val = ListV(items, birth_depth=fr.branch)
        else:
            val = self.eval(right, fr)
        self._store(left, val, fr)
        return val

    def _op_assign(self, n: Node, fr: RFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        op = _t(n.child_by_field_name("operator"))
        cur = self.eval(left, fr)
        if op in ("||=", "&&="):
            fr.branch += 1
            try:
                val = self.eval(right, fr)
            finally:
                fr.branch -= 1
            if isinstance(cur, Const) and ((op == "||=" and _truthy(cur)) or (op == "&&=" and not _truthy(cur))):
                return cur
            new = join(cur, val)
        else:
            val = self.eval(right, fr)
            if op == "+=" and isinstance(cur, Const) and isinstance(val, Const) and isinstance(cur.value, str) and isinstance(val.value, str):
                new = Const(cur.value + val.value)
            elif op == "<<=" and isinstance(cur, ListV):
                new = cur
            else:
                new = self._combine(cur, val)
        self._store(left, new, fr)
        return new

    def _store(self, target: Node | None, val: Val, fr: RFrame) -> None:
        if target is None:
            return
        t = target.type
        if t == "identifier":
            fr.env[_t(target)] = val
        elif t == "instance_variable":
            if fr.this_obj is not None:
                name = _t(target)
                old = fr.this_obj.fields.get(name)
                fr.this_obj.fields[name] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "call":
            recv_n, meth = target.child_by_field_name("receiver"), _t(target.child_by_field_name("method"))
            base = self.eval(recv_n, fr)
            if self.store_hook is not None:
                self.store_hook(target, _t(recv_n), Const(meth), val, fr)
            if isinstance(base, Obj):
                old = base.fields.get("@" + meth)
                base.fields["@" + meth] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "element_reference":
            obj = target.child_by_field_name("object")
            base = self.eval(obj, fr)
            idx_n = next((c for c in target.named_children if c is not obj and c.start_byte > (obj.end_byte if obj is not None else -1)), None)
            idx = self.eval(idx_n, fr) if idx_n is not None else UNKNOWN
            if self.store_hook is not None:
                self.store_hook(target, _t(obj), idx, val, fr)
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
        elif t in ("mlhs", "left_assignment_list", "rest_assignment", "destructured_left_assignment"):
            for c in target.named_children:
                self._store(c, UNKNOWN if classify(val) != "tainted" else Tainted("derived"), fr)

    # ------------------------------------------------------------------ calls
    def _args(self, args_n: Node | None, fr: RFrame) -> list[Val]:
        out: list[Val] = []
        kw: MapV | None = None
        for a in (args_n.named_children if args_n is not None else []):
            if a.type == "pair":
                if kw is None:
                    kw = MapV({}, birth_depth=fr.branch)
                k = a.child_by_field_name("key") or (a.named_children[0] if a.named_children else None)
                v = a.child_by_field_name("value") or (a.named_children[-1] if len(a.named_children) > 1 else None)
                kv, vv = self.eval(k, fr), self.eval(v, fr)
                if isinstance(kv, Const) and isinstance(kv.value, (str, int)) and not isinstance(kv.value, bool):
                    kw.entries[kv.value] = vv
                else:
                    kw.entries[("*", id(a))] = vv
                    kw.dirty = True
            elif a.type == "comment":
                continue
            else:
                v = self.eval(a, fr)
                if a.type == "splat_argument":
                    if isinstance(v, ListV) and not v.dirty and not v.opaque:
                        out.extend(v.items)
                    else:
                        out.append(Tainted("derived") if classify(v) == "tainted" else UNKNOWN)
                else:
                    out.append(v)
        if kw is not None:
            out.append(kw)
        return out

    def _hook(self, node, name, recv_text, args, recv, fr) -> None:
        if self.sink_hook is not None:
            self.sink_hook(node, name, recv_text, args, recv, fr)

    def _call(self, n: Node, fr: RFrame) -> Val:
        recv_n = n.child_by_field_name("receiver")
        meth_n = n.child_by_field_name("method")
        args_n = n.child_by_field_name("arguments")
        block = n.child_by_field_name("block")
        name = _t(meth_n)
        if meth_n is None:
            return UNKNOWN
        if recv_n is None:
            args = self._args(args_n, fr)
            self._hook(n, name, "", args, None, fr)
            return self._call_unqualified(name, args, fr, n, block)
        recv_text = _t(recv_n)
        args = self._args(args_n, fr)
        if self._sanitizer(name):
            self.eval(recv_n, fr)
            if block is not None:
                self._run_block(block, [UNKNOWN], fr)
            return SAFE
        cls_name = None
        if recv_n.type in ("constant", "scope_resolution"):
            cls_name = const_name(recv_n)
        if cls_name is not None and not self._shadowed(cls_name, fr):
            self._hook(n, name, recv_text, args, None, fr)
            return self._static_call(cls_name, name, args, fr, recv_text, block)
        recv = self.eval(recv_n, fr)
        self._hook(n, name, recv_text, args, recv, fr)
        if "arel" in recv_text.lower() or "Arel" in recv_text:
            return SAFE
        return self._method(recv, name, args, fr, recv_n, block)

    def _shadowed(self, cname: str, fr: RFrame) -> bool:
        return cname in fr.env

    def _find_method(self, cls: RbClass, name: str, argc: int, singleton: bool = False):
        seen: set[int] = set()
        cur: RbClass | None = cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            table = cur.smethods if singleton else cur.methods
            for m in table.get(name, []):
                lo, hi = _arity_range(m)
                if lo <= argc or True:
                    return cur, m
            cur = self._base(cur)
        return None

    def _call_unqualified(self, name: str, args: list[Val], fr: RFrame, node: Node | None = None, block: Node | None = None) -> Val:
        if fr.cls is not None:
            hit = self._find_method(fr.cls, name, len(args), singleton=fr.singleton)
            if hit is None and fr.singleton:
                hit = self._find_method(fr.cls, name, len(args), singleton=False)
            if hit is not None:
                return self._run_method(hit[0], hit[1], fr.this_obj, args, fr, singleton=fr.singleton)
            if fr.this_obj is not None and not args and ("@" + name) in fr.this_obj.fields:
                return fr.this_obj.fields["@" + name]
        if fr.file.main is not None:
            hit = self._find_method(fr.file.main, name, len(args))
            if hit is not None:
                return self._run_method(hit[0], hit[1], None, args, fr)
        if name in ("raise", "fail", "exit", "abort", "throw"):
            return UNKNOWN
        if name in DB_FINDERS or name.startswith("find_by"):
            return UNKNOWN
        if name in ("format", "sprintf") and args:
            return self._format_call(args)
        if name in ("Integer", "Float", "Rational", "Complex"):
            return SAFE
        if name in ("loop", "lambda", "proc") and block is not None:
            self._run_block(block, [], fr)
            return UNKNOWN
        if name in ("puts", "print", "p", "pp", "require", "require_relative", "load", "attr_accessor", "attr_reader", "attr_writer", "before_action", "after_action", "skip_before_action",
                    "protect_from_forgery", "helper_method", "layout", "include", "extend", "private", "public", "protected", "logger", "sleep", "srand", "warn", "binding_x"):
            return SAFE
        if block is not None:
            self._run_block(block, [UNKNOWN], fr)
        return self._opaque(None, args)

    def _format_call(self, args: list[Val]) -> Val:
        if isinstance(args[0], Const) and isinstance(args[0].value, str):
            return self._format(args[0].value, args[1:])
        return self._combine(*args)

    def _format(self, fmt: str, vals: list[Val]) -> Val:
        if not all(isinstance(v, Const) for v in vals):
            if isinstance(vals[0] if vals else None, ListV):
                return self._combine(*(vals[0].items + vals[1:]))
            return self._combine(Const(fmt), *vals)
        try:
            return Const(fmt % tuple(v.value for v in vals) if "%" in fmt else fmt)
        except (TypeError, ValueError, KeyError):
            return SAFE

    def _static_call(self, cname: str, name: str, args: list[Val], fr: RFrame, recv_text: str, block: Node | None) -> Val:
        cls = self.index.cls(cname, fr.file)
        if cls is not None:
            if name == "new":
                return self._instantiate(cls, args, fr)
            hit = self._find_method(cls, name, len(args), singleton=True)
            if hit is not None:
                return self._run_method(hit[0], hit[1], Obj(cls=hit[0], type_name=hit[0].name), args, fr, singleton=True)
        if cname in ("String",) and name == "new":
            return args[0] if args and isinstance(args[0], Const) else (Const("") if not args else self._combine(*args))
        if cname in ("Array", "Set") and name == "new":
            return ListV([], birth_depth=fr.branch, dirty=bool(args))
        if cname == "Hash" and name == "new":
            return MapV({}, birth_depth=fr.branch, dirty=bool(args))
        if cname == "StringIO" and name == "new":
            return BuilderV(list(args[:1]), birth_depth=fr.branch)
        if cname == "File" and name in ("join", "expand_path", "dirname", "absolute_path", "realpath", "extname_x"):
            return self._combine(*args) if args else UNKNOWN
        if cname == "File" and name == "basename":
            return SAFE
        if name in SAFE_METHODS and cname in PURE_RECEIVERS:
            return SAFE
        if (name in DB_FINDERS or name.startswith("find_by")) and cname not in PURE_RECEIVERS and cls is None:
            return UNKNOWN
        if cname == "Time" or cname == "Date" or cname == "SecureRandom" or cname == "Digest":
            return SAFE
        if cname in PURE_RECEIVERS and name not in ("load", "read", "open", "eval", "popen", "system", "exec", "spawn", "get", "post"):
            return self._opaque(None, args, pure=True)
        if block is not None:
            self._run_block(block, [UNKNOWN], fr)
        return self._opaque(None, args)

    def _instantiate(self, cls: RbClass, args: list[Val], fr: RFrame) -> Val:
        obj = Obj(cls=cls, type_name=cls.name)
        init = self._find_method(cls, "initialize", len(args))
        if init is not None:
            self._run_method(init[0], init[1], obj, args, fr)
        return obj

    def _method(self, recv: Val, name: str, args: list[Val], fr: RFrame, recv_n: Node | None, block: Node | None) -> Val:
        if isinstance(recv, Choice):
            return join_all(self._method(a, name, args, fr, recv_n, block) for a in recv.alts)
        if isinstance(recv, Obj):
            if recv.kind == "request":
                if name == "env":
                    return Obj(kind="request_env", type_name="RequestEnv")
                return SAFE if name in BENIGN_REQ else Tainted("request." + name)
            if recv.kind == "request_env":
                return UNKNOWN
            if recv.cls is not None:
                m = self._find_method(recv.cls, name, len(args))
                if m is not None:
                    return self._run_method(m[0], m[1], recv, args, fr)
                if not args and ("@" + name) in recv.fields:
                    return recv.fields["@" + name]
            if recv.type_name == "session":
                return UNKNOWN
            if name in ("class", "name", "to_s", "inspect") and recv.cls is not None:
                return SAFE
            return self._opaque(recv, args)
        if isinstance(recv, ListV):
            return self._list_method(recv, name, args, fr, block)
        if isinstance(recv, MapV):
            return self._map_method(recv, name, args, fr, block)
        if isinstance(recv, BuilderV):
            if name in ("<<", "write", "print", "puts", "concat") and args:
                self._mutating(fr, recv)
                recv.parts.append(args[0])
                return recv
            if name in ("string", "read", "to_s", "string_x"):
                return Const("".join(_s(p.value) for p in recv.parts)) if not recv.dirty and not recv.opaque and all(isinstance(p, Const) for p in recv.parts) \
                    else self._combine(*recv.parts, *([UNKNOWN] if recv.opaque else []))
            return self._opaque(recv, args)
        if name in MUTATORS and recv_n is not None and recv_n.type in ("identifier", "instance_variable"):
            new = self._string_method(recv, name.rstrip("!") if name != "<<" else "concat", args) if isinstance(recv, Const) else self._combine(recv, *args)
            if name.endswith("!") or name in ("concat", "prepend", "insert", "replace", "<<", "clear", "force_encoding"):
                self._store(recv_n, new, fr)
            return new
        if isinstance(recv, Const) and isinstance(recv.value, str):
            if block is not None:
                self._run_block(block, [recv], fr)
            return self._string_method(recv, name, args)
        if name in SAFE_METHODS:
            return SAFE
        if block is not None:
            self._run_block(block, [Tainted("derived") if classify(recv) == "tainted" else UNKNOWN], fr)
        if classify(recv) == "tainted":
            if name in ("each", "map", "select", "each_with_index", "each_pair"):
                return Tainted("derived")
            return Tainted(recv.why if isinstance(recv, Tainted) and recv.why.startswith("params") else "derived")
        return self._opaque(recv, args)

    def _string_method(self, recv: Const, name: str, args: list[Val]) -> Val:
        s = recv.value
        if name in SAFE_METHODS:
            return SAFE
        consts = all(isinstance(a, Const) for a in args)
        if name in ("split", "chars", "lines", "bytes", "scan", "partition", "rpartition", "each_char", "each_line"):
            return ListV([self._combine(recv, *args)], birth_depth=0, dirty=True)
        if not consts:
            return self._combine(recv, *args)
        v = [a.value for a in args]
        try:
            if name in ("to_s", "to_str", "freeze", "dup", "clone", "itself", "+@", "-@", "b", "scrub", "encode", "force_encoding", "unicode_normalize"):
                return recv
            if name == "upcase":
                return Const(s.upper())
            if name == "downcase":
                return Const(s.lower())
            if name == "capitalize":
                return Const(s.capitalize())
            if name == "strip":
                return Const(s.strip())
            if name == "lstrip":
                return Const(s.lstrip())
            if name == "rstrip":
                return Const(s.rstrip())
            if name == "chomp":
                return Const(s.rstrip("\n") if not v else (s[:-len(_s(v[0]))] if v and s.endswith(_s(v[0])) else s))
            if name in ("+", "concat", "prepend"):
                return Const(s + "".join(_s(x) for x in v)) if name != "prepend" else Const("".join(_s(x) for x in v) + s)
            if name == "*" and v and isinstance(v[0], int):
                return Const(s * min(v[0], 1000))
            if name in ("gsub", "sub") and len(v) == 2 and isinstance(v[0], str):
                return Const(s.replace(v[0], _s(v[1])) if name == "gsub" else s.replace(v[0], _s(v[1]), 1))
            if name == "reverse":
                return Const(s[::-1])
            if name == "to_sym":
                return recv
            if name == "delete" and v:
                return Const("".join(ch for ch in s if ch not in "".join(_s(x) for x in v)))
            if name in ("%", "format"):
                return self._format(s, args)
            if name in ("[]", "slice") and v and isinstance(v[0], int):
                return Const(s[v[0]:v[0] + (v[1] if len(v) > 1 and isinstance(v[1], int) else 1)])
        except (TypeError, ValueError, IndexError):
            return SAFE
        return SAFE

    def _list_method(self, lst: ListV, name: str, args: list[Val], fr: RFrame, block: Node | None) -> Val:
        if name in ("push", "append", "<<", "unshift", "prepend"):
            self._mutating(fr, lst)
            lst.items.extend(args)
            return lst
        if name in ("concat", "insert", "delete", "delete_at", "clear", "pop", "shift", "compact!", "uniq!", "map!", "sort!", "reverse!", "replace", "fill", "flatten!", "shuffle!", "select!", "reject!", "delete_if", "keep_if"):
            self._mutating(fr, lst)
            lst.dirty = True
            for a in args:
                if isinstance(a, ListV):
                    lst.items.extend(a.items)
                else:
                    lst.items.append(a)
            return UNKNOWN if name in ("pop", "shift") and not lst.items else SAFE
        if name in ("include?", "length", "size", "count", "empty?", "any?", "all?", "none?", "index_x"):
            if block is not None:
                self._run_block(block, [join_all(lst.items + [UNKNOWN])], fr)
            return SAFE
        if name in LOOP_METHODS and block is not None:
            elem = join_all(lst.items + ([UNKNOWN] if lst.dirty or lst.opaque else [])) if lst.items else UNKNOWN
            params = [elem, UNKNOWN] if name in ("each_with_index", "each_with_object") else [elem]
            res = self._run_block(block, params, fr)
            if name in MAP_METHODS:
                return ListV([res], birth_depth=fr.branch, dirty=True)
            return ListV(list(lst.items), birth_depth=fr.branch, dirty=True) if name in ("select", "filter", "reject", "sort_by", "find_all") else (res if name in ("inject", "reduce") else UNKNOWN)
        if name == "join":
            sep = args[0] if args else Const("")
            if not lst.dirty and not lst.opaque and all(isinstance(i, Const) for i in lst.items) and isinstance(sep, Const):
                return Const(_s(sep.value).join(_s(i.value) for i in lst.items))
            return self._combine(*lst.items, sep, *([UNKNOWN] if lst.opaque else []))
        if name in ("first", "last", "min", "max", "sample", "shift_x", "find", "detect", "take", "fetch", "dig", "[]", "at", "slice"):
            return join_all(lst.items + [UNKNOWN]) if lst.items else UNKNOWN
        if name in ("to_a", "dup", "clone", "compact", "uniq", "sort", "reverse", "flatten", "shuffle", "take", "drop", "freeze", "sort_by", "rotate", "zip"):
            return ListV(list(lst.items), birth_depth=fr.branch, dirty=True, opaque=lst.opaque)
        return self._opaque(lst, args)

    def _map_method(self, m: MapV, name: str, args: list[Val], fr: RFrame, block: Node | None) -> Val:
        key = args[0].value if args and isinstance(args[0], Const) and isinstance(args[0].value, (str, int)) and not isinstance(args[0].value, bool) else None
        if name in ("[]=", "store") and len(args) == 2:
            self._mutating(fr, m)
            if key is not None:
                m.entries[key] = args[1]
            else:
                m.entries[("*", id(args[0]))] = args[1]
                m.dirty = True
            return args[1]
        if name in ("fetch", "dig", "[]", "key?", "has_key?", "include?", "member?"):
            if name in ("key?", "has_key?", "include?", "member?"):
                return SAFE
            if key is not None and not m.dirty and not m.opaque:
                if key in m.entries:
                    return m.entries[key]
                return args[1] if len(args) > 1 else Const(None)
            return join_all(list(m.entries.values()) + [UNKNOWN])
        if name in ("merge!", "update", "delete", "clear", "replace", "reject!", "select!", "delete_if", "keep_if", "compact!", "transform_values!", "transform_keys!"):
            self._mutating(fr, m)
            m.dirty = True
            for a in args:
                if isinstance(a, MapV):
                    m.entries.update({("*", id(a), k): v for k, v in a.entries.items()})
            return UNKNOWN
        if name in ("merge", "to_h", "dup", "clone", "slice", "except", "select", "reject", "symbolize_keys", "stringify_keys", "with_indifferent_access", "deep_dup", "compact", "transform_values", "freeze"):
            out = MapV(dict(m.entries), birth_depth=fr.branch, dirty=True, opaque=m.opaque)
            for a in args:
                if isinstance(a, MapV):
                    out.entries.update({("*", id(a), k): v for k, v in a.entries.items()})
            return out
        if name in ("keys", "values", "to_a", "values_at"):
            return ListV(list(m.entries.values()), birth_depth=fr.branch, dirty=True, opaque=m.opaque or m.dirty)
        if name in ("size", "length", "count", "empty?", "any?"):
            return SAFE
        if name in LOOP_METHODS and block is not None:
            elem = join_all(list(m.entries.values()) + [UNKNOWN])
            self._run_block(block, [UNKNOWN, elem], fr)
            return UNKNOWN
        return self._opaque(m, args)

    # --- blocks & methods
    def _run_block(self, block: Node | None, params: list[Val], fr: RFrame) -> Val:
        if block is None:
            return UNKNOWN
        bp = block.child_by_field_name("parameters") or next((c for c in block.named_children if c.type == "block_parameters"), None)
        names = []
        for c in (bp.named_children if bp is not None else []):
            if c.type == "identifier":
                names.append(_t(c))
            elif c.type == "destructured_parameter":
                names.extend(_t(x) for x in c.named_children if x.type == "identifier")
            elif c.type in ("optional_parameter", "splat_parameter", "keyword_parameter"):
                inner = next((x for x in c.named_children if x.type == "identifier"), None)
                if inner is not None:
                    names.append(_t(inner))
        saved = {k: fr.env.get(k) for k in names}
        had = {k: (k in fr.env) for k in names}
        for i, k in enumerate(names):
            fr.env[k] = params[i] if i < len(params) else (params[0] if len(params) == 1 and len(names) > 1 and classify(params[0]) == "tainted" else UNKNOWN)
        body = block.child_by_field_name("body") or next((c for c in block.named_children if c.type in ("block_body", "body_statement")), None)
        fr.branch += 1
        last: Val = UNKNOWN
        try:
            before = fr.last
            fr.last = UNKNOWN
            self.exec(body, fr)
            last = fr.last
            fr.last = before
        finally:
            fr.branch -= 1
            for k in names:
                if had[k]:
                    fr.env[k] = saved[k]
                else:
                    fr.env.pop(k, None)
        return last

    def _bind_params(self, m: Node, args: list[Val], inner: RFrame) -> None:
        p = _params(m)
        pos = list(args)
        kwargs: MapV | None = None
        has_kw = any(c.type in ("keyword_parameter", "hash_splat_parameter") for c in (p.named_children if p is not None else []))
        if has_kw and pos and isinstance(pos[-1], MapV):
            kwargs = pos.pop()
        for c in (p.named_children if p is not None else []):
            ty = c.type
            if ty == "identifier":
                inner.env[_t(c)] = pos.pop(0) if pos else UNKNOWN
            elif ty == "optional_parameter":
                name = _t(c.child_by_field_name("name"))
                inner.env[name] = pos.pop(0) if pos else self.eval(c.child_by_field_name("value"), inner)
            elif ty == "splat_parameter":
                nm = next((x for x in c.named_children if x.type == "identifier"), None)
                if nm is not None:
                    inner.env[_t(nm)] = ListV(list(pos), birth_depth=0, dirty=True)
                pos = []
            elif ty == "keyword_parameter":
                name = _t(c.child_by_field_name("name"))
                dflt = c.child_by_field_name("value")
                if kwargs is not None and name in kwargs.entries and not kwargs.dirty:
                    inner.env[name] = kwargs.entries[name]
                elif kwargs is not None and (kwargs.dirty or kwargs.opaque):
                    inner.env[name] = join_all(list(kwargs.entries.values()) + [UNKNOWN])
                else:
                    inner.env[name] = self.eval(dflt, inner) if dflt is not None else UNKNOWN
            elif ty == "hash_splat_parameter":
                nm = next((x for x in c.named_children if x.type == "identifier"), None)
                if nm is not None:
                    inner.env[_t(nm)] = kwargs if kwargs is not None else MapV({}, birth_depth=0)
            elif ty == "block_parameter":
                nm = next((x for x in c.named_children if x.type == "identifier"), None)
                if nm is not None:
                    inner.env[_t(nm)] = UNKNOWN

    def _run_method(self, cls: RbClass, m: Node, recv: Obj | None, args: list[Val], fr: RFrame, singleton: bool = False) -> Val:
        key = (id(cls.file), m.start_byte)
        if fr.depth + 1 > self.MAX_DEPTH or key in self._active:
            return self._opaque(recv, args)
        body = m.child_by_field_name("body")
        inner = RFrame(cls.file, cls, recv, fr.depth + 1, singleton=singleton)
        self._bind_params(m, args, inner)
        self._active.add(key)
        try:
            if body is None:
                inner.last = Const(None)
            elif body.type in ("body_statement", "begin"):
                self.exec(body, inner)
            else:
                inner.last = self.eval(body, inner)
        finally:
            self._active.discard(key)
        return join_all(inner.rets + [inner.last]) if inner.rets else inner.last

    def bind_entry(self, m: Node, fr: RFrame, action: bool = False) -> None:
        """Entry point. Plain method parameters of an action are route segments only when named (Rails passes none)."""
        p = _params(m) if m.type in ("method", "singleton_method") else None
        for c in (p.named_children if p is not None else []):
            nm = c if c.type == "identifier" else next((x for x in c.named_children if x.type == "identifier"), None) if c.type in ("optional_parameter", "keyword_parameter", "splat_parameter") else None
            if nm is not None:
                fr.env[_t(nm)] = UNKNOWN if not action else Tainted("route-param")

    # ------------------------------------------------------------------ statements
    def exec(self, n: Node | None, fr: RFrame) -> bool:
        if n is None:
            return False
        self._tick()
        t = n.type
        if t in ("body_statement", "begin", "block_body", "program", "then", "else", "do", "ensure"):
            return self._seq(n, fr)
        if t == "return":
            vals = [self.eval(c, fr) for c in n.named_children]
            v = vals[0] if len(vals) == 1 else (ListV(vals, birth_depth=fr.branch) if vals else Const(None))
            if len(n.named_children) == 1 and n.named_children[0].type == "argument_list":
                al = [self.eval(c, fr) for c in n.named_children[0].named_children]
                v = al[0] if len(al) == 1 else (ListV(al, birth_depth=fr.branch) if al else Const(None))
            fr.rets.append(v)
            fr.last = v
            return True
        if t in ("break", "next", "redo", "retry"):
            for c in n.named_children:
                self.eval(c, fr)
            fr.last = UNKNOWN
            return True
        if t in ("if", "unless", "elsif"):
            return self._if(n, fr, invert=t == "unless")
        if t in ("if_modifier", "unless_modifier"):
            return self._if(n, fr, invert=t == "unless_modifier", modifier=True)
        if t in ("while", "until", "while_modifier", "until_modifier", "for"):
            return self._loop(n, fr)
        if t == "case":
            return self._case(n, fr)
        if t == "rescue_modifier":
            body = n.named_children[0] if n.named_children else None
            resc = n.named_children[-1] if len(n.named_children) > 1 else None
            a = self.eval(body, fr)
            fr.branch += 1
            try:
                b = self.eval(resc, fr)
            finally:
                fr.branch -= 1
            fr.last = join(a, b)
            return False
        if t in ("do_block", "block"):
            self._run_block(n, [], fr)
            return False
        if t == "yield":
            for c in n.named_children:
                self.eval(c, fr)
            fr.last = UNKNOWN
            return False
        if t in ("method", "singleton_method", "class", "module", "comment", "alias", "undef", "singleton_class"):
            fr.last = Const(None) if t != "comment" else fr.last
            return False
        fr.last = self.eval(n, fr)
        if t == "call" and n.child_by_field_name("receiver") is None and _t(n.child_by_field_name("method")) in ("raise", "fail", "exit", "abort") and not self._raise_is_conditional(n):
            return True
        return False

    @staticmethod
    def _raise_is_conditional(n: Node) -> bool:
        return False

    def _seq(self, n: Node, fr: RFrame) -> bool:
        main = [c for c in n.named_children if c.type not in ("rescue", "ensure", "else_x", "comment")]
        rescues = [c for c in n.named_children if c.type == "rescue"]
        ensures = [c for c in n.named_children if c.type == "ensure"]
        elses = [c for c in n.named_children if c.type == "else" and n.type in ("body_statement", "begin")]
        main = [c for c in main if c not in elses]
        base = dict(fr.env)
        term = False
        for c in main:
            if self.exec(c, fr):
                term = True
                break
        after = fr.env
        last_main = fr.last
        if elses and not term:
            for e in elses:
                if self.exec(e, fr):
                    term = True
            after = fr.env
            last_main = fr.last
        if rescues:
            envs = [] if term else [after]
            lasts = [] if term else [last_main]
            all_term = term
            for r in rescues:
                fr.env = {k: join(base[k], v) if k in base else v for k, v in after.items()}
                fr.branch += 1
                try:
                    var = r.child_by_field_name("variable")
                    if var is not None:
                        for x in var.named_children:
                            if x.type == "identifier":
                                fr.env[_t(x)] = UNKNOWN
                    fr.last = UNKNOWN
                    rterm = self.exec(r.child_by_field_name("body") or next((x for x in r.named_children if x.type == "then"), None), fr)
                finally:
                    fr.branch -= 1
                all_term = all_term and rterm
                if not rterm:
                    envs.append(fr.env)
                    lasts.append(fr.last)
            if envs:
                fr.env = envs[0]
                for e in envs[1:]:
                    self._join_envs(fr, fr.env, e)
                fr.last = join_all(lasts)
            else:
                fr.env = after
            term = all_term and not envs
        for e in ensures:
            keep = fr.last
            self.exec(e, fr)
            fr.last = keep
        return term

    def _join_envs(self, fr: RFrame, a: dict, b: dict) -> None:
        out = {}
        for k in set(a) | set(b):
            out[k] = join(a[k], b[k]) if (k in a and k in b) else (a[k] if k in a else b[k])
        fr.env = out

    def _if(self, n: Node, fr: RFrame, invert: bool = False, modifier: bool = False) -> bool:
        cond = self.eval(n.child_by_field_name("condition"), fr)
        if modifier:
            cons, alt = n.child_by_field_name("body"), None
        else:
            cons, alt = n.child_by_field_name("consequence"), n.child_by_field_name("alternative")
        if invert:
            cons, alt = alt, cons
        if isinstance(cond, Const):
            take_cons = _truthy(cond)
            self._dead(fr, alt if take_cons else cons)
            branch = cons if take_cons else alt
            if branch is None:
                fr.last = Const(None)
                return False
            return self.exec(branch, fr)
        base = dict(fr.env)
        fr.branch += 1
        try:
            fr.last = UNKNOWN
            ta = self.exec(cons, fr) if cons is not None else False
            ea, la = fr.env, fr.last if cons is not None else Const(None)
            fr.env = dict(base)
            fr.last = UNKNOWN
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

    def _loop(self, n: Node, fr: RFrame) -> bool:
        base = dict(fr.env)
        fr.branch += 1
        try:
            if n.type == "for":
                it = self.eval(n.child_by_field_name("value"), fr)
                pat = n.child_by_field_name("pattern")
                elem = join_all(it.items + ([UNKNOWN] if it.dirty or it.opaque else [])) if isinstance(it, ListV) and it.items else (Tainted("derived") if classify(it) == "tainted" else UNKNOWN)
                if pat is not None:
                    self._store(pat, elem, fr)
            else:
                self.eval(n.child_by_field_name("condition"), fr)
            body = n.child_by_field_name("body") or next((c for c in n.named_children if c.type in ("do", "then", "body_statement")), None)
            if n.type.endswith("_modifier"):
                body = n.child_by_field_name("body")
            self.exec(body, fr)
        finally:
            fr.branch -= 1
        self._join_envs(fr, base, fr.env)
        fr.last = Const(None)
        return False

    def _case(self, n: Node, fr: RFrame) -> bool:
        self.eval(n.child_by_field_name("value"), fr)
        base = dict(fr.env)
        envs, lasts = [], []
        has_else = False
        fr.branch += 1
        try:
            for c in n.named_children:
                if c.type in ("when", "in_clause"):
                    fr.env = dict(base)
                    for pat in c.named_children:
                        if pat.type == "pattern":
                            self.eval(pat.named_children[0] if pat.named_children else None, fr)
                    fr.last = UNKNOWN
                    body = c.child_by_field_name("body") or next((x for x in c.named_children if x.type == "then"), None)
                    term = self.exec(body, fr) if body is not None else False
                    if not term:
                        envs.append(fr.env)
                        lasts.append(fr.last)
                elif c.type == "else":
                    has_else = True
                    fr.env = dict(base)
                    fr.last = UNKNOWN
                    if not self.exec(c, fr):
                        envs.append(fr.env)
                        lasts.append(fr.last)
        finally:
            fr.branch -= 1
        if not has_else:
            envs.append(base)
            lasts.append(Const(None))
        fr.env = envs[0]
        for e in envs[1:]:
            self._join_envs(fr, fr.env, e)
        fr.last = join_all(lasts)
        return False
