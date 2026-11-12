"""An abstract interpreter for PHP, built on tree-sitter.

Same stance as the other interpreters: follow request data (superglobals) to sinks, and prove values
safe; anything it cannot model is UNKNOWN. PHP specifics: arrays are one ordered map type, strings
interpolate variables, `include`/`require` with a literal path runs that file in the current scope, and a
guard such as `is_numeric($octet[0])` makes that exact array element safe inside the guarded branch.
"""

from __future__ import annotations

from aletheore.flow.budget import STEPS as _STEPS

import re
from dataclasses import dataclass, field

from tree_sitter import Node

from aletheore.flow import extra_sinks as _X
from aletheore.flow.php_index import PhpClass, PhpFile, PhpIndex
from aletheore.flow.php_sinks import (
    CAST_SAFE, INCLUDE_KINDS, NAME_SANITIZERS, SAFE_FUNCS, SANITIZERS, SERVER_TAINTED_PREFIXES, SUPERGLOBALS,
)
from aletheore.flow.values import ClosureV, add_prefix, SAFE, UNKNOWN, Choice, Const, MapV, Obj, Tainted, Val, classify, join, join_all

PROPAGATING = {
    "str_replace", "str_ireplace", "preg_replace", "substr", "mb_substr", "trim", "rtrim", "ltrim", "chop", "strtolower", "strtoupper",
    "mb_strtolower", "mb_strtoupper", "ucfirst", "lcfirst", "ucwords", "nl2br", "stripslashes", "html_entity_decode", "htmlspecialchars_decode",
    "urldecode", "rawurldecode", "base64_decode", "json_decode", "json_encode", "serialize", "strrev", "str_pad", "str_repeat", "wordwrap",
    "addcslashes", "utf8_encode", "utf8_decode", "sprintf", "vsprintf", "implode", "join", "strval", "print_r", "var_export", "strtr", "substr_replace",
    "mb_convert_encoding", "iconv", "nl_langinfo", "money_format", "lcfirst", "preg_quote", "quotemeta", "chunk_split", "strstr", "stristr",
    "strrchr", "array_pop", "array_shift", "current", "reset", "end", "next", "array_rand_x", "reset",
}
ARRAY_FUNCS = {"array_merge", "array_values", "array_unique", "array_slice", "array_reverse", "array_filter", "array_map", "array_combine",
               "array_flip", "array_diff", "array_intersect", "array_replace", "array_column", "array_merge_recursive", "array_pad", "array_chunk"}
SOURCE_FUNCS = {"getallheaders", "apache_request_headers", "filter_input", "filter_input_array", "readline", "fgets", "fgetc", "stream_get_contents",
                "http_get_request_body", "get_headers_x"}
TERMINATORS = {"die", "exit"}
SAFE_REGEX = re.compile(r"^/\^?\[([^\]]+)\][+*]?\$?/[a-zA-Z]*$|^/\^\\(?:d|w)[+*]?\$/[a-zA-Z]*$")
SAFE_CLASS_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- ")


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class PFrame:
    file: PhpFile
    cls: PhpClass | None
    this_obj: Obj | None
    depth: int
    env: dict = field(default_factory=dict)
    rets: list = field(default_factory=list)
    branch: int = 0
    safe_exprs: set = field(default_factory=set)
    included: set = field(default_factory=set)
    validated: set = field(default_factory=set)  # ids of tainted values a guard has validated


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _vname(n: Node) -> str:
    return _t(n.named_children[0]) if n.named_children else _t(n).lstrip("$")


def _s(v: object) -> str:
    if v is None:
        return ""
    if v is True:
        return "1"
    if v is False:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _falsy(v: object) -> bool:
    return v is None or v is False or v == 0 or v == "" or v == "0"


class PhpInterp:
    MAX_STEPS = 60000
    MAX_DEPTH = 6

    def __init__(self, index: PhpIndex, cwes: set[int] | None = None, sink_hook=None, store_hook=None):
        self.index = index
        self.cwes = cwes or set()
        self.sink_hook = sink_hook    # hook(node, name, recv_text, args, recv, frame)
        self.store_hook = store_hook  # hook(node, base_text, key, value, frame)
        self.steps = 0
        self._outs: list | None = None
        self.static_props: dict[str, Val] = {}
        self.probes: dict[tuple, list] = {}
        self.probe_keys: set[tuple] = set()
        self.dead: list[tuple] = []
        self._active: set[int] = set()
        self._tid = 0
        self.parents: dict[int, frozenset[int]] = {}

    # ------------------------------------------------------------------ helpers
    def _fresh(self, why: str, parents=()) -> Tainted:
        """A tainted value with its own identity and the identities it was derived from."""
        self._tid += 1
        self.parents[self._tid] = frozenset(parents)
        return Tainted(why, self._tid)

    def _tids(self, v: Val) -> set[int] | None:
        """Identities of the tainted parts of `v`; None when some tainted part is untracked."""
        alts = v.alts if isinstance(v, Choice) else (v,)
        out: set[int] = set()
        for a in alts:
            if isinstance(a, Tainted):
                if not a.tid:
                    return None
                out.add(a.tid)
        return out

    def _safe_tid(self, tid: int, fr: "PFrame", depth: int = 0) -> bool:
        """Validated directly, or derived only from validated values (the unvalidated parent stays tainted)."""
        if tid in fr.validated:
            return True
        ps = self.parents.get(tid)
        if not ps or depth > 8:
            return False
        return all(self._safe_tid(p, fr, depth + 1) for p in ps)

    def _revalidate(self, v: Val, fr: "PFrame") -> Val:
        if isinstance(v, Tainted):
            return SAFE if v.tid and self._safe_tid(v.tid, fr) else v
        if isinstance(v, Choice) and any(isinstance(a, Tainted) for a in v.alts):
            alts = [(SAFE if isinstance(a, Tainted) and a.tid and self._safe_tid(a.tid, fr) else a) for a in v.alts]
            return join_all(alts)
        return v
    def _tick(self) -> None:
        self.steps += 1
        _STEPS[0] += 1
        if self.steps > self.MAX_STEPS:
            raise BudgetExceeded

    def _key(self, fr: PFrame, n: Node) -> tuple:
        return (id(fr.file), n.start_byte, n.end_byte)

    def _dead(self, fr: PFrame, n: Node | None) -> None:
        if n is not None:
            self.dead.append(self._key(fr, n))

    def _sanitizer(self, name: str, recv: str = "") -> bool:
        low = name.lower()
        for c in self.cwes:
            if low in {x.lower() for x in SANITIZERS.get(c, ())}:
                return True
            pat = NAME_SANITIZERS.get(c)
            if pat is not None and pat.match(low):
                return True
        return _X.sanitizes("php", self.cwes, name, recv)

    def _combine(self, *vals: Val) -> Val:
        rank = {"safe": 0, "unknown": 1, "tainted": 2}
        worst = "safe"
        for v in vals:
            c = classify(v)
            if rank[c] > rank[worst]:
                worst = c
        if worst != "tainted":
            return SAFE if worst == "safe" else UNKNOWN
        parents: set[int] = set()
        for v in vals:
            ids = self._tids(v)
            if ids is None:
                parents.add(-1)  # an untracked tainted input: never claim this derived value safe
            else:
                parents |= ids
        return add_prefix(self._fresh("derived", parents), vals)

    def _mutating(self, fr: PFrame, c) -> None:
        if fr.branch > c.birth_depth:
            c.dirty = True

    @staticmethod
    def _opaque(recv: Val | None, args: list[Val], pure: bool = False) -> Val:
        vals = ([recv] if recv is not None else []) + list(args)
        if not pure:
            for v in vals:
                if isinstance(v, MapV):
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
        val = self._revalidate(self._eval(node, fr), fr)
        key = self._key(fr, node)
        if key in self.probe_keys:
            self.probes.setdefault(key, []).append((val, classify(val)))
        return val

    def _eval(self, n: Node, fr: PFrame) -> Val:
        t = n.type
        if t == "variable_name":
            return self._var(_vname(n), fr)
        if t in ("string", "encapsed_string", "heredoc", "nowdoc"):
            return self._string(n, fr)
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
        if t == "boolean":
            return Const(_t(n).lower() == "true")
        if t == "null":
            return Const(None)
        if t in ("name", "qualified_name", "relative_name"):
            return SAFE  # a defined constant (PHP_EOL, DVWA_WEB_PAGE_TO_ROOT)
        if t == "class_constant_access_expression":
            return SAFE
        if t == "parenthesized_expression":
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t == "binary_expression":
            return self._binary(n, fr)
        if t == "unary_op_expression":
            return self._unary(n, fr)
        if t == "error_suppression_expression":
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t == "cast_expression":
            return self._cast(n, fr)
        if t == "conditional_expression":
            return self._ternary(n, fr)
        if t == "assignment_expression":
            return self._assign(n, fr)
        if t == "augmented_assignment_expression":
            return self._augassign(n, fr)
        if t in ("update_expression",):
            return SAFE
        if t == "array_creation_expression":
            return self._array(n, fr)
        if t == "subscript_expression":
            return self._subscript(n, fr)
        if t == "scoped_property_access_expression":
            return self.static_props.get(self._static_key(n, fr), UNKNOWN)
        if t == "member_access_expression":
            return self._member(n, fr)
        if t == "function_call_expression":
            return self._call(n, fr)
        if t in ("member_call_expression", "nullsafe_member_call_expression"):
            return self._method_call(n, fr)
        if t == "scoped_call_expression":
            return self._static_call(n, fr)
        if t == "object_creation_expression":
            return self._new(n, fr)
        if t in INCLUDE_KINDS:
            return self._include(n, fr)
        if t == "shell_command_expression":
            parts = self._interp_parts(n, fr)
            val = self._combine(*parts) if parts else Const("")
            self._hook(n, "shell_exec", "", [val], None, fr)
            return UNKNOWN
        if t == "print_intrinsic":
            vals = [self.eval(c, fr) for c in n.named_children]
            self._hook(n, "echo", "", vals, None, fr)
            return Const(1)
        if t in ("anonymous_function", "arrow_function"):
            return ClosureV(n, fr.file, dict(fr.env))  # keeps the variables it was created with
        if t in ("anonymous_function_creation_expression",):
            return UNKNOWN
        if t == "reference_assignment_expression":
            return self.eval(n.child_by_field_name("right"), fr)
        return UNKNOWN

    def _interp_parts(self, n: Node, fr: PFrame) -> list[Val]:
        parts: list[Val] = []
        for c in n.children:
            if c.type in ("string_content", "escape_sequence", "string_value", "heredoc_body"):
                if c.type == "heredoc_body":
                    parts.extend(self._interp_parts(c, fr))
                else:
                    parts.append(Const(_t(c)))
            elif c.is_named and c.type not in ("string_start", "string_end", "heredoc_start", "heredoc_end", "comment"):
                parts.append(self.eval(c, fr))
        return parts

    @staticmethod
    def _const_alts(v: Val) -> list[str] | None:
        """The possible string values of a constant or a small choice of constants, else None."""
        if isinstance(v, Const):
            return [_s(v.value)]
        if isinstance(v, Choice) and len(v.alts) <= 8 and all(isinstance(a, Const) for a in v.alts):
            return sorted({_s(a.value) for a in v.alts})
        return None

    def _concat(self, parts: list[Val]) -> Val:
        """Concatenate values; a small choice of constants stays a choice of constants."""
        combos = [""]
        for p in parts:
            alts = self._const_alts(p)
            if alts is None or len(combos) * len(alts) > 16:
                return self._combine(*parts)
            combos = [c + a for c in combos for a in alts]
        return Const(combos[0]) if len(combos) == 1 else Choice(frozenset(Const(c) for c in combos))

    def _string(self, n: Node, fr: PFrame) -> Val:
        if n.type == "nowdoc":
            return UNKNOWN
        parts = self._interp_parts(n, fr)
        if not parts:
            return Const("")
        return self._concat(parts)

    def _var(self, name: str, fr: PFrame) -> Val:
        if name in fr.safe_exprs:
            return SAFE
        if name in fr.env:
            return fr.env[name]
        if name in SUPERGLOBALS:
            return self._fresh("request." + name)
        if name == "_SERVER":
            return Obj(type_name="Server")
        if name == "_SESSION":
            return Obj(type_name="Session")
        if name == "this":
            return fr.this_obj or UNKNOWN
        return UNKNOWN

    def _subscript(self, n: Node, fr: PFrame) -> Val:
        kids = n.named_children
        if not kids:
            return UNKNOWN
        base_n = kids[0]
        idx_n = kids[1] if len(kids) > 1 else None
        base = self.eval(base_n, fr)
        idx = self.eval(idx_n, fr) if idx_n is not None else UNKNOWN
        if base_n.type == "variable_name" and isinstance(idx, Const):
            if f"{_vname(base_n)}[{_s(idx.value)}]" in fr.safe_exprs:
                return SAFE
        if isinstance(base, Obj) and base.type_name == "Server":
            key = _s(idx.value) if isinstance(idx, Const) else ""
            return self._fresh("request._SERVER") if key.startswith(SERVER_TAINTED_PREFIXES) else UNKNOWN
        if isinstance(base, Obj) and base.type_name == "Session":
            return UNKNOWN
        if isinstance(base, MapV):
            k = idx.value if isinstance(idx, Const) else None
            if k is not None and not base.dirty and not base.opaque and k in base.entries:
                return base.entries[k]
            return join_all(list(base.entries.values()) + [UNKNOWN])
        if isinstance(base, Tainted) and base.why.startswith("request._FILES") and isinstance(idx, Const) \
                and idx.value in ("tmp_name", "size", "error"):
            return SAFE  # server-generated temp path and numbers, not attacker data
        if classify(base) == "tainted":
            return self._fresh(base.why if isinstance(base, Tainted) and base.why.startswith("request._") else "derived",
                               [base.tid] if isinstance(base, Tainted) and base.tid else [-1])
        return UNKNOWN

    def _static_key(self, n: Node, fr: PFrame) -> str:
        scope = _t(n.child_by_field_name("scope"))
        if scope in ("self", "static") and fr.cls is not None:
            scope = fr.cls.name
        return f"{scope.split(chr(92))[-1]}::{_t(n.child_by_field_name('name'))}"

    def _member(self, n: Node, fr: PFrame) -> Val:
        obj_n, name_n = n.child_by_field_name("object"), n.child_by_field_name("name")
        base = self.eval(obj_n, fr)
        prop = _t(name_n)
        if prop in ("query", "data", "cookies", "files", "params", "headers", "body") and "request" in _t(obj_n).lower() \
                and not (isinstance(base, Obj) and prop in base.fields):
            # `$request->query`, `Flight::request()->query`, `$this->request()->data`: framework request collections
            return Tainted("request." + prop)
        if isinstance(base, Obj) and prop in base.fields:
            if "*dynamic*" in base.fields:  # `$this->{$k} = v` may have overwritten any property
                return join(base.fields[prop], base.fields["*dynamic*"])
            return base.fields[prop]
        if isinstance(base, Obj) and "*dynamic*" in base.fields:
            return join(base.fields["*dynamic*"], UNKNOWN)
        if isinstance(base, Obj) and base.cls is not None and base.kind is None:
            return UNKNOWN  # a property of a modelled class that was never written: not request data
        if classify(base) == "tainted":
            return self._combine(base)
        return UNKNOWN

    def _binary(self, n: Node, fr: PFrame) -> Val:
        op = _t(n.child_by_field_name("operator"))
        left_n, right_n = n.child_by_field_name("left"), n.child_by_field_name("right")
        a = self.eval(left_n, fr)
        low = op.lower()
        if low in ("&&", "and", "||", "or", "??") and isinstance(a, Const):
            stop = (low in ("&&", "and") and _falsy(a.value)) or (low in ("||", "or") and not _falsy(a.value)) or (low == "??" and a.value is not None)
            if stop:
                self._dead(fr, right_n)
                return a
            return self.eval(right_n, fr)
        b = self.eval(right_n, fr)
        if low in ("&&", "and", "||", "or", "??", "xor"):
            return join(a, b)
        if isinstance(a, Const) and isinstance(b, Const):
            x, y = a.value, b.value
            try:
                if op == ".":
                    return Const(_s(x) + _s(y))
                num = (int, float)
                if isinstance(x, num) and isinstance(y, num) and not isinstance(x, bool) and not isinstance(y, bool):
                    table = {"+": lambda: x + y, "-": lambda: x - y, "*": lambda: x * y, "/": lambda: x / y, "%": lambda: x % y,
                             "<": lambda: x < y, ">": lambda: x > y, "<=": lambda: x <= y, ">=": lambda: x >= y, "==": lambda: x == y,
                             "===": lambda: x == y, "!=": lambda: x != y, "!==": lambda: x != y, "<>": lambda: x != y}
                    if op in table and not (op in ("/", "%") and y == 0):
                        return Const(table[op]())
                if op in ("===", "!==") and type(x) is type(y):
                    return Const((x == y) if op == "===" else (x != y))
                if op in ("==", "!=") and isinstance(x, str) and isinstance(y, str):
                    return Const((x == y) if op == "==" else (x != y))
            except (TypeError, ValueError, OverflowError):
                pass
            return SAFE
        if op == ".":
            return self._concat([a, b])
        return self._combine(a, b) if op not in ("==", "===", "!=", "!==", "<", ">", "<=", ">=", "<>", "<=>", "instanceof") else SAFE

    def _unary(self, n: Node, fr: PFrame) -> Val:
        op = _t(n.child_by_field_name("operator")) if n.child_by_field_name("operator") else ""
        kids = [c for c in n.named_children]
        v = self.eval(kids[-1], fr) if kids else UNKNOWN
        if op == "!" and isinstance(v, Const):
            return Const(_falsy(v.value))
        if op in ("-", "+", "~"):
            return SAFE
        if op == "!":
            return SAFE
        return self._combine(v)

    def _cast(self, n: Node, fr: PFrame) -> Val:
        kids = n.named_children
        ctype = _t(n.child_by_field_name("type")).lower().strip("() ") if n.child_by_field_name("type") else ""
        v = self.eval(kids[-1], fr) if kids else UNKNOWN
        return SAFE if ctype in CAST_SAFE else v

    def _ternary(self, n: Node, fr: PFrame) -> Val:
        cond_n, body_n, alt_n = n.child_by_field_name("condition"), n.child_by_field_name("body"), n.child_by_field_name("alternative")
        cond = self.eval(cond_n, fr)
        if isinstance(cond, Const):
            take = not _falsy(cond.value)
            self._dead(fr, alt_n if take else body_n)
            return (self.eval(body_n, fr) if body_n is not None else cond) if take else self.eval(alt_n, fr)
        fr.branch += 1
        try:
            return join(self.eval(body_n, fr) if body_n is not None else cond, self.eval(alt_n, fr))
        finally:
            fr.branch -= 1

    def _array(self, n: Node, fr: PFrame) -> Val:
        m = MapV({}, birth_depth=fr.branch)
        i = 0
        for el in n.named_children:
            kids = el.named_children
            if el.type != "array_element_initializer" or not kids:
                continue
            if len(kids) >= 2 and kids[0].type != "by_ref":
                k = self.eval(kids[0], fr)
                v = self.eval(kids[1], fr)
                if isinstance(k, Const) and isinstance(k.value, (str, int)):
                    m.entries[k.value] = v
                else:
                    m.entries[("*", id(el))] = v
                    m.dirty = True
            else:
                m.entries[i] = self.eval(kids[-1], fr)
                i += 1
        return m

    # ------------------------------------------------------------------ assignment
    def _assign(self, n: Node, fr: PFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        val = self.eval(right, fr)
        self._bind(left, val, fr)
        return val

    def _augassign(self, n: Node, fr: PFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        op = _t(n.child_by_field_name("operator"))
        cur, rhs = self.eval(left, fr), self.eval(right, fr)
        if op == ".=" and isinstance(cur, Const) and isinstance(rhs, Const):
            val = Const(_s(cur.value) + _s(rhs.value))
        else:
            val = self._combine(cur, rhs)
        self._bind(left, val, fr)
        return val

    def _bind(self, target: Node | None, val: Val, fr: PFrame) -> None:
        if target is None:
            return
        t = target.type
        if t == "variable_name":
            name = _vname(target)
            fr.env[name] = val
            fr.safe_exprs.discard(name)
            fr.safe_exprs = {k for k in fr.safe_exprs if not k.startswith(name + "[")}
        elif t in ("list_literal", "array_creation_expression"):
            elems = [c for c in target.named_children]
            for i, el in enumerate(elems):
                tgt = el.named_children[-1] if el.named_children else None
                src = val.entries.get(i) if isinstance(val, MapV) and not val.dirty else (Tainted("derived") if classify(val) == "tainted" else UNKNOWN)
                self._bind(tgt, src if src is not None else UNKNOWN, fr)
        elif t == "scoped_property_access_expression":
            key = self._static_key(target, fr)
            old = self.static_props.get(key)
            self.static_props[key] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "member_access_expression":
            base = self.eval(target.child_by_field_name("object"), fr)
            name_n = target.child_by_field_name("name")
            if name_n is not None and name_n.type != "name":
                # `$this->{$k} = v` / `$this->$k = v`: the property written is whatever `$k` holds.
                key = self.eval(name_n, fr)
                names = [key] if isinstance(key, Const) else list(key.alts) if isinstance(key, Choice) and all(isinstance(a, Const) for a in key.alts) else None
                if isinstance(base, Obj):
                    if names is None:
                        # A request-controlled name can hit any property; a name that merely comes from other program data
                        # (a config array) is not treated as reaching properties it was never written to.
                        if classify(key) == "tainted":
                            old = base.fields.get("*dynamic*")
                            base.fields["*dynamic*"] = join(old, val) if old is not None else val
                    else:
                        for k in names:
                            old = base.fields.get(_s(k.value))
                            base.fields[_s(k.value)] = join(old, val) if old is not None else val
                return
            prop = _t(name_n)
            if isinstance(base, Obj):
                old = base.fields.get(prop)
                base.fields[prop] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "subscript_expression":
            kids = target.named_children
            base_n = kids[0] if kids else None
            idx_n = kids[1] if len(kids) > 1 else None
            base = self.eval(base_n, fr) if base_n is not None else UNKNOWN
            idx = self.eval(idx_n, fr) if idx_n is not None else None
            if self.store_hook is not None and base_n is not None:
                self.store_hook(target, _t(base_n), idx, val, fr)
            if isinstance(base, MapV):
                self._mutating(fr, base)
                if idx is None:
                    nxt = max([k for k in base.entries if isinstance(k, int)] + [-1]) + 1
                    base.entries[nxt] = val
                elif isinstance(idx, Const) and isinstance(idx.value, (str, int)):
                    base.entries[idx.value] = val
                else:
                    base.entries[("*", id(target))] = val
                    base.dirty = True
            elif base_n is not None and base_n.type == "variable_name" and base is UNKNOWN or (isinstance(base, Const) and base.value is None):
                m = MapV({}, birth_depth=fr.branch)
                if idx is None:
                    m.entries[0] = val
                elif isinstance(idx, Const) and isinstance(idx.value, (str, int)):
                    m.entries[idx.value] = val
                else:
                    m.entries[("*", id(target))] = val
                    m.dirty = True
                fr.env[_vname(base_n)] = m

    # ------------------------------------------------------------------ calls
    def _args(self, args_n: Node | None, fr: PFrame) -> list[Val]:
        out: list[Val] = []
        for a in (args_n.named_children if args_n is not None else []):
            expr = a.named_children[-1] if a.type == "argument" and a.named_children else a
            out.append(self.eval(expr, fr))
        return out

    def _hook(self, node, name, recv_text, args, recv, fr) -> None:
        if self.sink_hook is not None:
            self.sink_hook(node, name, recv_text, args, recv, fr)

    def _call(self, n: Node, fr: PFrame) -> Val:
        fn_n = n.child_by_field_name("function")
        args_n = n.child_by_field_name("arguments")
        if fn_n is None:
            return UNKNOWN
        if fn_n.type not in ("name", "qualified_name"):
            callee = self.eval(fn_n, fr)
            args = self._args(args_n, fr)
            if isinstance(callee, ClosureV):
                return self._call_closure(callee, args, fr)
            return self._opaque(None, args)
        name = _t(fn_n).split("\\")[-1]
        low = name.lower()
        args = self._args(args_n, fr)
        self._hook(n, name, "", args, None, fr)
        if low in TERMINATORS:
            return UNKNOWN
        if self._sanitizer(low):
            return SAFE
        hit = self.index.function(name, fr.file)
        if hit is not None:
            self._outs = self._ref_args(args_n)
            return self._run_function(hit[0], hit[1], None, None, args, fr)
        return self._builtin(low, n, args, fr)

    @staticmethod
    def _ref_args(args_n: Node | None) -> list | None:
        """(position, expression node) of every argument that is a plain variable or element: candidates for by-reference parameters."""
        out = []
        for i, a in enumerate(args_n.named_children if args_n is not None else []):
            expr = a.named_children[-1] if a.type == "argument" and a.named_children else a
            if expr.type in ("variable_name", "subscript_expression", "member_access_expression"):
                out.append((i, expr))
        return out or None

    def _call_closure(self, c: ClosureV, args: list[Val], fr: PFrame) -> Val:
        if fr.depth + 1 > self.MAX_DEPTH or id(c.node) in self._active:
            return self._opaque(None, args)
        inner = PFrame(c.file, fr.cls, fr.this_obj, fr.depth + 1)
        inner.env.update(c.env)
        self._bind_params(c.node, args, inner)
        body = c.node.child_by_field_name("body")
        self._active.add(id(c.node))
        try:
            if body is not None and body.type == "compound_statement":
                self.exec(body, inner)
                return join_all(inner.rets) if inner.rets else Const(None)
            return self.eval(body, inner)
        finally:
            self._active.discard(id(c.node))

    def _builtin(self, low: str, n: Node, args: list[Val], fr: PFrame) -> Val:
        if low in SAFE_FUNCS:
            return SAFE
        if low == "zip_entry_name":
            return Tainted("archive.entry")
        if low == "filter_var":
            flt = _t(n.child_by_field_name("arguments").named_children[1]) if n.child_by_field_name("arguments") and len(n.child_by_field_name("arguments").named_children) > 1 else ""
            if any(k in flt for k in ("FILTER_VALIDATE_INT", "FILTER_VALIDATE_FLOAT", "FILTER_VALIDATE_BOOL", "FILTER_VALIDATE_IP", "FILTER_SANITIZE_NUMBER_INT",
                                      "FILTER_SANITIZE_NUMBER_FLOAT", "FILTER_VALIDATE_DOMAIN")):
                return SAFE
            return self._combine(*args)
        if low in SOURCE_FUNCS:
            return self._fresh("request.input")
        if low == "file_get_contents" and args and isinstance(args[0], Const) and str(args[0].value).startswith("php://input"):
            return self._fresh("request.input")
        if low in ("explode", "str_split", "preg_split", "mb_split") and args:
            src = args[1] if low == "explode" and len(args) > 1 else args[0]
            if isinstance(src, Const) and isinstance(args[0], Const) and low == "explode" and isinstance(src.value, str) and isinstance(args[0].value, str) and args[0].value:
                return MapV({i: Const(p) for i, p in enumerate(src.value.split(args[0].value))}, birth_depth=fr.branch)
            return MapV({0: self._combine(src)}, birth_depth=fr.branch, dirty=True)
        if low in ("str_replace", "str_ireplace") and len(args) >= 3 and all(isinstance(a, Const) for a in args[:3]) and all(isinstance(a.value, str) for a in args[:3]):
            return Const(args[2].value.replace(args[0].value, args[1].value))
        if low in ("strtolower", "mb_strtolower") and args and isinstance(args[0], Const) and isinstance(args[0].value, str):
            return Const(args[0].value.lower())
        if low in ("strtoupper", "mb_strtoupper") and args and isinstance(args[0], Const) and isinstance(args[0].value, str):
            return Const(args[0].value.upper())
        if low in ("trim", "ltrim", "rtrim") and len(args) == 1 and isinstance(args[0], Const) and isinstance(args[0].value, str):
            return Const(args[0].value.strip())
        if low in ARRAY_FUNCS:
            items = []
            for a in args:
                items.extend(a.entries.values() if isinstance(a, MapV) else [a])
            return MapV({i: v for i, v in enumerate(items)}, birth_depth=fr.branch, dirty=True)
        if low in ("array_keys",):
            return MapV({0: Tainted("derived") if any(classify(a) == "tainted" for a in args) else UNKNOWN}, birth_depth=fr.branch, dirty=True)
        if low == "array_push" and args and isinstance(args[0], MapV):
            return SAFE
        if low in PROPAGATING:
            return self._combine(*args) if args else UNKNOWN
        if low in ("array", "compact", "extract", "define", "constant", "get_defined_vars"):
            return UNKNOWN
        return self._opaque(None, args)

    def _method_call(self, n: Node, fr: PFrame) -> Val:
        obj_n, name_n, args_n = n.child_by_field_name("object"), n.child_by_field_name("name"), n.child_by_field_name("arguments")
        name = _t(name_n)
        recv_text = _t(obj_n)
        recv = self.eval(obj_n, fr)
        args = self._args(args_n, fr)
        rtype = recv.type_name if isinstance(recv, Obj) else None
        self._hook(n, name, recv_text, args, recv, fr)
        if self._sanitizer(name, recv_text):
            return SAFE
        if name in ("getNameIndex", "getNameIndex_x") and not (isinstance(recv, Obj) and recv.cls is not None):
            return Tainted("archive.entry")  # ZipArchive entry name: chosen by whoever made the archive (zip slip)
        if isinstance(recv, Obj) and recv.kind == "request":
            # Laravel / Symfony / PSR-7 request object: $request->input('x'), ->get(), ->header(), ->getContent()
            return SAFE if name in ("hasHeader", "has", "filled", "isMethod", "expectsJson", "wantsJson", "ip", "method", "ajax", "secure") else Tainted("request." + name)
        if isinstance(recv, Obj) and recv.cls is not None:
            m = self._find_method(recv.cls, name)
            if m is not None:
                return self._run_function(m[0].file, m[1], m[0], recv, args, fr)
        if isinstance(recv, Choice):
            outs = []
            for a in recv.alts:
                if isinstance(a, Obj) and a.cls is not None:
                    m = self._find_method(a.cls, name)
                    outs.append(self._run_function(m[0].file, m[1], m[0], a, args, fr) if m else UNKNOWN)
                else:
                    outs.append(self._opaque(a, args))
            return join_all(outs)
        if isinstance(recv, Tainted) or (isinstance(recv, Choice) and any(isinstance(a, Tainted) for a in recv.alts)):
            return self._combine(recv)
        if rtype in ("PDO", "mysqli", "wpdb", "DOMXPath", "SimpleXMLElement", "DOMDocument", "Session", "Server"):
            return UNKNOWN if rtype != "wpdb" else UNKNOWN
        return self._opaque(recv, args)

    def _static_call(self, n: Node, fr: PFrame) -> Val:
        scope, name_n, args_n = n.child_by_field_name("scope"), n.child_by_field_name("name"), n.child_by_field_name("arguments")
        name = _t(name_n)
        scope_text = _t(scope)
        args = self._args(args_n, fr)
        self._hook(n, name, scope_text, args, None, fr)
        if self._sanitizer(name):
            return SAFE
        cls: PhpClass | None
        if scope_text in ("self", "static") and fr.cls is not None:
            cls = fr.cls
        elif scope_text == "parent" and fr.cls is not None and fr.cls.parent:
            cls = self.index.cls(fr.cls.parent, fr.file)
        else:
            cls = self.index.cls(scope_text, fr.file)
        if cls is not None:
            m = self._find_method(cls, name)
            if m is not None:
                this = fr.this_obj if scope_text in ("parent", "self", "static") else None
                return self._run_function(m[0].file, m[1], m[0], this, args, fr)
        return self._opaque(None, args)

    def _find_method(self, cls: PhpClass, name: str):
        seen: set[int] = set()
        cur: PhpClass | None = cls
        low = name.lower()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            for mname, node in cur.methods.items():
                if mname.lower() == low:
                    return cur, node
            cur = self.index.cls(cur.parent, cur.file) if cur.parent else None
        return None

    def _new(self, n: Node, fr: PFrame) -> Val:
        cname_n = next((c for c in n.named_children if c.type in ("name", "qualified_name")), None)
        args_n = next((c for c in n.named_children if c.type == "arguments"), None)
        cname = _t(cname_n).split("\\")[-1]
        args = self._args(args_n, fr)
        self._hook(n, "<new>", cname, args, None, fr)
        cls = self.index.cls(cname, fr.file) if cname else None
        if cls is not None:
            obj = Obj(cls=cls, type_name=cls.name)
            self._init_props(cls, obj, fr)
            ctor = self._find_method(cls, "__construct")
            if ctor is not None:
                self._run_function(ctor[0].file, ctor[1], ctor[0], obj, args, fr)
            return obj
        return Obj(type_name=cname, fields={"v": self._combine(*args)} if args else {})

    def _init_props(self, cls: PhpClass, obj: Obj, fr: PFrame) -> None:
        """Declared instance properties start with their default (`private $encode = false;`), parents first, so a
        write inside one branch of the constructor joins with the default instead of looking like the only value."""
        chain, seen, cur = [], set(), cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            chain.append(cur)
            cur = self.index.cls(cur.parent, cur.file) if cur.parent else None
        for c in reversed(chain):
            body = next((x for x in c.node.children if x.type == "declaration_list"), None)
            for decl in (body.named_children if body is not None else []):
                if decl.type != "property_declaration" or any(m.type == "static_modifier" for m in decl.children):
                    continue
                for el in decl.named_children:
                    if el.type != "property_element":
                        continue
                    name_n = next((x for x in el.named_children if x.type == "variable_name"), None)
                    init = next((x for x in el.named_children if x is not name_n), None)
                    if name_n is None:
                        continue
                    nm = _t(name_n).lstrip("$")
                    try:
                        obj.fields[nm] = self.eval(init, PFrame(c.file, c, obj, fr.depth + 1)) if init is not None else Const(None)
                    except (BudgetExceeded, RecursionError):
                        raise
                    except Exception:
                        obj.fields[nm] = UNKNOWN

    def make_this(self, cls: PhpClass, pf: PhpFile) -> Obj:
        obj = Obj(cls=cls, type_name=cls.name)
        self._init_props(cls, obj, PFrame(pf, cls, obj, 0))
        ctor = self._find_method(cls, "__construct")
        if ctor is not None:
            try:
                self._run_function(ctor[0].file, ctor[1], ctor[0], obj, [], PFrame(pf, cls, obj, 0))
            except (BudgetExceeded, RecursionError):
                pass
        return obj

    def _run_function(self, file: PhpFile, fn: Node, cls: PhpClass | None, this_obj: Obj | None, args: list[Val], fr: PFrame) -> Val:
        if fr.depth + 1 > self.MAX_DEPTH or id(fn) in self._active:
            return self._opaque(this_obj, args)
        body = fn.child_by_field_name("body")
        if body is None:
            return self._opaque(this_obj, args)
        inner = PFrame(file, cls, this_obj, fr.depth + 1)
        self._bind_params(fn, args, inner)
        outs, self._outs = self._outs, None
        self._active.add(id(fn))
        try:
            self.exec(body, inner)
        finally:
            self._active.discard(id(fn))
        if outs:
            params = fn.child_by_field_name("parameters")
            plist = [p for p in (params.named_children if params is not None else []) if p.type in ("simple_parameter", "property_promotion_parameter", "variadic_parameter")]
            for i, target in outs:  # by-reference parameters: the callee's final value flows back into the caller's variable
                if i < len(plist) and any(c.type == "reference_modifier" for c in plist[i].children):
                    nm = next((c for c in plist[i].named_children if c.type == "variable_name"), None)
                    if nm is not None and _vname(nm) in inner.env:
                        self._bind(target, inner.env[_vname(nm)], fr)
        return join_all(inner.rets) if inner.rets else Const(None)

    def _bind_params(self, fn: Node, args: list[Val], inner: PFrame) -> None:
        params = fn.child_by_field_name("parameters")
        if params is None:
            return
        pos = list(args)
        for p in params.named_children:
            if p.type in ("simple_parameter", "property_promotion_parameter"):
                nm = p.child_by_field_name("name")
                name = _vname(nm) if nm is not None and nm.type == "variable_name" else ""
                if not name:
                    nm = next((c for c in p.named_children if c.type == "variable_name"), None)
                    name = _vname(nm) if nm is not None else ""
                default = p.child_by_field_name("default_value")
                val = pos.pop(0) if pos else (self.eval(default, inner) if default is not None else UNKNOWN)
                if name:
                    inner.env[name] = val
            elif p.type == "variadic_parameter":
                nm = next((c for c in p.named_children if c.type == "variable_name"), None)
                if nm is not None:
                    inner.env[_vname(nm)] = MapV({i: v for i, v in enumerate(pos)}, dirty=True)
                pos = []

    def bind_entry(self, fn: Node, fr: PFrame) -> None:
        params = fn.child_by_field_name("parameters")
        for p in (params.named_children if params is not None else []):
            nm = next((c for c in p.named_children if c.type == "variable_name"), None)
            if nm is not None:
                ptype = _t(next((c for c in p.named_children if c.type in ("named_type", "qualified_name", "union_type", "optional_type")), None)).lstrip("?")
                if ptype.split("\\")[-1] in ("Request", "ServerRequestInterface", "FormRequest", "HttpRequest", "ServerRequest"):
                    fr.env[_vname(nm)] = Obj(kind="request", type_name="Request")
                else:
                    fr.env[_vname(nm)] = UNKNOWN

    def _include(self, n: Node, fr: PFrame) -> Val:
        kids = n.named_children
        val = self.eval(kids[0], fr) if kids else UNKNOWN
        self._hook(n, "__include__", "", [val], None, fr)
        if fr.depth >= 4:
            return UNKNOWN
        alts = self._const_alts(val)
        suffix_only = False
        if alts is None:
            alts, suffix_only = self._path_suffix_alts(kids[0], fr) if kids else (None, False)
        if not alts:
            return UNKNOWN
        targets = []
        for rel in alts:
            target = self.index.include_suffix(rel, fr.file) if suffix_only else self.index.include(rel, fr.file)
            if target is not None and (target.path,) not in fr.included and all(t.path != target.path for t in targets):
                targets.append(target)
        if not targets:
            return UNKNOWN
        base_env, base_safe = dict(fr.env), set(fr.safe_exprs)
        envs = []
        for target in targets:
            fr.included.add((target.path,))
            inner = PFrame(target, fr.cls, fr.this_obj, fr.depth + 1, env=dict(base_env), safe_exprs=set(base_safe), included=fr.included)
            self.exec(target.root, inner)
            envs.append(inner.env)
        fr.env = envs[0]
        for e in envs[1:]:
            self._join_envs(fr, fr.env, e)
        return SAFE

    def _path_suffix_alts(self, node: Node, fr: PFrame) -> tuple[list[str] | None, bool]:
        """For `PREFIX . "dir/" . $choice`, where PREFIX is a constant we cannot evaluate: the trailing
        part that IS known, as suffix alternatives (resolved against the repository by path ending)."""
        if node.type == "binary_expression" and _t(node.child_by_field_name("operator")) == ".":
            right = self._const_alts(self.eval(node.child_by_field_name("right"), fr))
            if right is None:
                return None, False
            left_alts, left_suffix = self._path_suffix_alts(node.child_by_field_name("left"), fr)
            left_val = self._const_alts(self.eval(node.child_by_field_name("left"), fr))
            if left_val is not None:
                return [l + r for l in left_val for r in right][:16], False
            if left_alts:
                return [l + r for l in left_alts for r in right][:16], True
            return right, True
        v = self.eval(node, fr)
        alts = self._const_alts(v)
        return (alts, False) if alts is not None else (None, False)

    # ------------------------------------------------------------------ guards
    def _text_follows(self, var_n: Node, cond: Node) -> bool:
        """Somewhere after the check, the checked value is concatenated with literal text containing a letter."""
        fn = cond
        while fn is not None and fn.type not in ("function_definition", "method_declaration", "program"):
            fn = fn.parent
        if fn is None:
            return True
        after = fn.text[max(0, cond.end_byte - fn.start_byte):].decode("utf-8", "replace")
        var = re.escape(_t(var_n))
        return re.search(var + r"""\s*\.\s*(['"])[^'"]*[A-Za-z][^'"]*\1""", after) is not None or \
            re.search(r"""(['"])[^'"]*""" + var + r"""\s*[A-Za-z]""", after) is not None

    def _guard_keys(self, cond: Node | None, fr: PFrame, negated: bool = False) -> set[str]:
        """Variables or array elements proven safe when `cond` holds (negated: when it does not hold)."""
        if cond is None:
            return set()
        t = cond.type
        if t == "parenthesized_expression":
            return self._guard_keys(cond.named_children[0] if cond.named_children else None, fr, negated)
        if t == "unary_op_expression" and _t(cond.child_by_field_name("operator")) == "!":
            kids = cond.named_children
            return self._guard_keys(kids[-1] if kids else None, fr, not negated)
        if t == "binary_expression":
            op = _t(cond.child_by_field_name("operator")).lower()
            left, right = cond.child_by_field_name("left"), cond.child_by_field_name("right")
            if op in ("&&", "and", "||", "or"):
                a, b = self._guard_keys(left, fr, negated), self._guard_keys(right, fr, negated)
                both = (op in ("&&", "and")) != negated
                return a | b if both else a & b
            if op in ("===", "==", "!==", "!="):
                eq = op in ("===", "==")
                if eq == negated:
                    return set()
                for var_n, other in ((left, right), (right, left)):
                    key = self._expr_key(var_n, fr)
                    if key and classify(self.eval(other, fr)) == "safe":
                        return {key}
            return set()
        if t == "function_call_expression" and not negated:
            fn = _t(cond.child_by_field_name("function")).lower()
            args_n = cond.child_by_field_name("arguments")
            args = [a.named_children[-1] for a in (args_n.named_children if args_n is not None else []) if a.named_children]
            if fn == "is_numeric" and self.cwes & {78, 88} and args and self._text_follows(args[0], cond):
                # is_numeric("1\n") is true on PHP 8: the newline ends the shell command, and constant text written after
                # the value (`'echo ' . $v . 'id'`) runs as a second one. With nothing but digits, dots or other numbers
                # after it (DVWA's `$o[0] . '.' . $o[1]`), the attacker can only split the line into numbers.
                return set()
            if fn in ("is_numeric", "is_int", "is_float", "ctype_digit", "ctype_alnum", "ctype_alpha", "ctype_xdigit", "is_bool") and args:
                key = self._expr_key(args[0], fr)
                return {key} if key else set()
            if fn == "in_array" and len(args) >= 2 and self._host_key(args[0]) is not None:
                coll = self.eval(args[1], fr)
                if isinstance(coll, MapV) and not coll.dirty and not coll.opaque and all(classify(v) == "safe" for v in coll.entries.values()):
                    return {self._host_key(args[0]) + "@918"}  # the URL's host is one of the allowed hosts
            if fn == "in_array" and len(args) >= 2:
                key = self._expr_key(args[0], fr)
                coll = self.eval(args[1], fr)
                if key and isinstance(coll, MapV) and not coll.dirty and not coll.opaque and all(classify(v) == "safe" for v in coll.entries.values()):
                    return {key}
            if fn == "preg_match" and len(args) >= 2:
                pat = self.eval(args[0], fr)
                key = self._expr_key(args[1], fr)
                if key and isinstance(pat, Const) and isinstance(pat.value, str) and self._safe_regex(pat.value):
                    return {key}
            if fn == "filter_var" and len(args) >= 2:
                key = self._expr_key(args[0], fr)
                if key and any(k in _t(args[1]) for k in ("FILTER_VALIDATE_INT", "FILTER_VALIDATE_FLOAT", "FILTER_VALIDATE_BOOL", "FILTER_VALIDATE_IP")):
                    return {key}
        return set()

    def _safe_regex(self, pattern: str) -> bool:
        """An anchored pattern made only of simple character classes (identifier-like text), so a match proves the value harmless."""
        if re.fullmatch(r"/\^\\[dw][+*]?\$/[a-zA-Z]*", pattern):
            return True
        m = re.fullmatch(r"/\^((?:\[[^\]]+\](?:[+*]|\{\d*,?\d*\})?)+)\$/[a-zA-Z]*", pattern)
        if not m:
            return False
        allowed = ["a-z", "A-Z", "0-9", "\\w", "\\d", "_", "-", " ", "$"]
        if 22 not in self.cwes:
            allowed.append(".")  # a dot is harmless except for path traversal
        for cls in re.findall(r"\[([^\]]+)\]", m.group(1)):
            rest = cls
            for tok in allowed:
                rest = rest.replace(tok, "")
            if rest != "":
                return False
        return True

    def _expr_key(self, node: Node | None, fr: PFrame) -> str | None:
        if node is None:
            return None
        if node.type == "variable_name":
            return _vname(node)
        if node.type == "function_call_expression" and _t(node.child_by_field_name("function")).lower() in ("strtolower", "strtoupper", "mb_strtolower", "mb_strtoupper", "trim"):
            args_n = node.child_by_field_name("arguments")
            inner = args_n.named_children[0].named_children[-1] if args_n is not None and args_n.named_children and args_n.named_children[0].named_children else None
            return self._expr_key(inner, fr)  # a case-folded or trimmed value that equals a constant is that constant
        if node.type == "subscript_expression" and len(node.named_children) == 2 and node.named_children[0].type == "variable_name":
            idx = self.eval(node.named_children[1], fr)
            if isinstance(idx, Const):
                return f"{_vname(node.named_children[0])}[{_s(idx.value)}]"
        return None

    @staticmethod
    def _host_key(node: Node | None) -> str | None:
        """`parse_url($u, PHP_URL_HOST)` and `parse_url($u)['host']`: the variable whose URL host this reads."""
        if node is None:
            return None
        call = node
        if node.type == "subscript_expression" and node.named_children and node.named_children[0].type == "function_call_expression" \
                and "host" in _t(node.named_children[-1]).lower():
            call = node.named_children[0]
        if call.type != "function_call_expression" or _t(call.child_by_field_name("function")).lower() != "parse_url":
            return None
        a = call.child_by_field_name("arguments")
        items = [x.named_children[-1] for x in (a.named_children if a is not None else []) if x.named_children]
        if not items or items[0].type != "variable_name":
            return None
        if call is node and not (len(items) >= 2 and "PHP_URL_HOST" in _t(items[1])):
            return None
        return _vname(items[0])

    def _apply_guard(self, keys: set[str], fr: PFrame) -> None:
        for k in keys:
            if k.endswith("@918"):
                if 918 in self.cwes:
                    ids = self._tids(fr.env.get(k[:-4], UNKNOWN))
                    if ids:
                        fr.validated |= ids
                    fr.env[k[:-4]] = SAFE
                continue
            if "[" in k:
                fr.safe_exprs.add(k)
            else:
                ids = self._tids(fr.env.get(k, UNKNOWN))
                if ids:
                    fr.validated |= ids  # values derived only from this one are safe too
                fr.env[k] = SAFE

    # ------------------------------------------------------------------ statements
    def exec(self, n: Node | None, fr: PFrame) -> bool:
        if n is None:
            return False
        self._tick()
        t = n.type
        if t in ("compound_statement", "program", "colon_block"):
            for c in n.named_children:
                if self.exec(c, fr):
                    return True
            return False
        if t == "expression_statement":
            terminated = False
            for c in n.named_children:
                self.eval(c, fr)
                if c.type == "throw_expression":
                    terminated = True  # PHP 8: `throw new X;` is an expression statement
                if c.type == "function_call_expression" and _t(c.child_by_field_name("function")).lower() in TERMINATORS:
                    args_n = c.child_by_field_name("arguments")
                    vals = [self.eval(a.named_children[-1], fr) for a in (args_n.named_children if args_n is not None else []) if a.named_children]
                    if vals:
                        self._hook(c, "echo", "", vals, None, fr)
                    terminated = True
            return terminated
        if t == "echo_statement":
            vals = [self.eval(c, fr) for c in n.named_children]
            self._hook(n, "echo", "", vals, None, fr)
            return False
        if t == "exit_statement":
            for c in n.named_children:
                self.eval(c, fr)
            return True
        if t == "return_statement":
            fr.rets.append(self.eval(n.named_children[0], fr) if n.named_children else Const(None))
            return True
        if t == "throw_expression" or t == "throw_statement":
            for c in n.named_children:
                self.eval(c, fr)
            return True
        if t == "if_statement":
            return self._if(n, fr)
        if t in ("foreach_statement", "for_statement", "while_statement", "do_statement"):
            return self._loop(n, fr)
        if t == "switch_statement":
            return self._switch(n, fr)
        if t == "try_statement":
            return self._try(n, fr)
        if t == "global_declaration":
            for c in n.named_children:
                if c.type == "variable_name":
                    fr.env.setdefault(_vname(c), UNKNOWN)
            return False
        if t in ("function_definition", "class_declaration", "interface_declaration", "trait_declaration", "namespace_definition",
                 "namespace_use_declaration", "php_tag", "text", "comment", "break_statement", "continue_statement", "declare_statement",
                 "unset_statement", "const_declaration", "enum_declaration", "empty_statement", "text_interpolation"):
            if t == "namespace_definition":
                for c in n.named_children:
                    if c.type == "compound_statement":
                        self.exec(c, fr)
            return False
        for c in n.named_children:
            if c.type.endswith("statement") or c.type in ("compound_statement", "colon_block"):
                self.exec(c, fr)
            else:
                self.eval(c, fr)
        return False

    def _join_envs(self, fr: PFrame, a: dict, b: dict) -> None:
        out = {}
        for k in set(a) | set(b):
            out[k] = join(a[k], b[k]) if (k in a and k in b) else (a[k] if k in a else b[k])
        fr.env = out

    def _if(self, n: Node, fr: PFrame) -> bool:
        clauses: list[tuple[Node | None, Node | None]] = [(n.child_by_field_name("condition"), n.child_by_field_name("body"))]
        has_else = False
        for c in n.named_children:
            if c.type == "else_if_clause":
                clauses.append((c.child_by_field_name("condition"), c.child_by_field_name("body")))
            elif c.type == "else_clause":
                clauses.append((None, c.child_by_field_name("body") or (c.named_children[-1] if c.named_children else None)))
                has_else = True
        results: list[dict] = []
        early_guard: set[str] = set()
        all_term = True
        i = 0
        while i < len(clauses):
            cond_n, block = clauses[i]
            cond = self.eval(cond_n, fr) if cond_n is not None else Const(True)
            if isinstance(cond, Const):
                take = not _falsy(cond.value)
                if take:
                    for _, later in clauses[i + 1:]:
                        self._dead(fr, later)
                    alternative = cond_n is None and (results or i > 0)  # an `else` after branches that ran
                    if alternative:
                        fr.branch += 1  # so its object-field writes join the other branches' instead of replacing them
                    try:
                        term = self.exec(block, fr)
                    finally:
                        if alternative:
                            fr.branch -= 1
                    if results:
                        if not term:
                            results.append(fr.env)
                            all_term = False
                        fr.env = results[0]
                        for e in results[1:]:
                            self._join_envs(fr, fr.env, e)
                        return all_term and term
                    return term
                self._dead(fr, block)
                i += 1
                continue
            base, base_safe, base_valid = dict(fr.env), set(fr.safe_exprs), set(fr.validated)
            self._apply_guard(self._guard_keys(cond_n, fr), fr)
            fr.branch += 1
            try:
                term = self.exec(block, fr)
            finally:
                fr.branch -= 1
            if not term:
                results.append(fr.env)
                all_term = False
            elif i == 0 and not has_else and len(clauses) == 1:
                early_guard = self._guard_keys(cond_n, fr, negated=True)
            fr.env, fr.safe_exprs, fr.validated = base, base_safe, base_valid
            i += 1
        if not has_else:
            env = fr.env
            if len(clauses) == 1 and not early_guard:
                # the implicit else: the condition was false (`if (!in_array($p, [...])) { $p = 'x'; }` leaves $p validated on this path)
                env = dict(env)
                for k in self._guard_keys(clauses[0][0], fr, negated=True):
                    if "[" not in k and k in env:
                        env[k] = SAFE
            results.append(env)
            all_term = False
        if not results:
            return True
        fr.env = results[0]
        for e in results[1:]:
            self._join_envs(fr, fr.env, e)
        self._apply_guard(early_guard, fr)
        return all_term and has_else

    def _loop(self, n: Node, fr: PFrame) -> bool:
        base = dict(fr.env)
        if n.type == "foreach_statement":
            kids = n.named_children
            it = self.eval(kids[0], fr) if kids else UNKNOWN
            key_t = val_t = None
            for c in kids[1:]:
                if c.type == "pair" and len(c.named_children) == 2:
                    key_t, val_t = c.named_children
                elif c.type in ("variable_name", "by_ref", "list_literal") and val_t is None:
                    val_t = c.named_children[-1] if c.type == "by_ref" and c.named_children else c
            if isinstance(it, MapV) and not it.entries and not it.dirty and not it.opaque:
                return False
            def parts(v):
                if isinstance(v, MapV):
                    exact = not v.dirty and not v.opaque
                    e_ = join_all(list(v.entries.values()) + ([UNKNOWN] if v.dirty or v.opaque else []))
                    k_ = join_all([Const(k) if not isinstance(k, tuple) else UNKNOWN for k in v.entries] + ([] if exact and v.entries else [UNKNOWN]))
                    return e_, k_
                if classify(v) == "tainted":
                    return Tainted("derived"), Tainted("derived")
                return UNKNOWN, UNKNOWN
            if isinstance(it, Choice):
                ps = [parts(a) for a in it.alts]
                elem, keys = join_all([p_[0] for p_ in ps]), join_all([p_[1] for p_ in ps])
            else:
                elem, keys = parts(it)
            fr.branch += 1
            try:
                if key_t is not None:
                    self._bind(key_t, keys, fr)
                if val_t is not None:
                    self._bind(val_t, elem, fr)
                body = n.child_by_field_name("body") or next((c for c in kids if c.type in ("compound_statement", "colon_block")), None)
                self.exec(body, fr)
            finally:
                fr.branch -= 1
        else:
            cond_n = n.child_by_field_name("condition")
            if n.type == "while_statement" and cond_n is not None:
                cond = self.eval(cond_n, fr)
                if isinstance(cond, Const) and _falsy(cond.value):
                    self._dead(fr, n.child_by_field_name("body"))
                    return False
            fr.branch += 1
            try:
                body = n.child_by_field_name("body") or next((c for c in n.named_children if c.type in ("compound_statement", "colon_block")), None)
                self.exec(body, fr)
            finally:
                fr.branch -= 1
        self._join_envs(fr, base, fr.env)
        return False

    def _switch(self, n: Node, fr: PFrame) -> bool:
        self.eval(n.child_by_field_name("condition"), fr)
        body = n.child_by_field_name("body")
        groups = [c for c in (body.named_children if body is not None else []) if c.type in ("case_statement", "default_statement")]
        base = dict(fr.env)
        envs = []
        has_default = any(g.type == "default_statement" for g in groups)
        fr.branch += 1
        try:
            for i in range(len(groups)):
                fr.env = dict(base)
                for j in range(i, len(groups)):
                    ended = False
                    for s in groups[j].named_children[(1 if groups[j].type == "case_statement" else 0):]:
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
        fr.env = envs[0]
        for e in envs[1:]:
            self._join_envs(fr, fr.env, e)
        return False

    def _try(self, n: Node, fr: PFrame) -> bool:
        base = dict(fr.env)
        body = n.child_by_field_name("body") or next((c for c in n.named_children if c.type == "compound_statement"), None)
        term = self.exec(body, fr)
        after = fr.env
        envs = [] if term else [after]
        all_term = term
        for c in n.named_children:
            if c.type == "catch_clause":
                fr.env = {k: join(base.get(k, v), v) if k in base else v for k, v in after.items()}
                nm = next((x for x in c.named_children if x.type in ("variable_name",)), None)
                if nm is not None:
                    fr.env[_vname(nm)] = UNKNOWN
                fr.branch += 1
                try:
                    cterm = self.exec(c.child_by_field_name("body") or next((x for x in c.named_children if x.type == "compound_statement"), None), fr)
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
                self.exec(next((x for x in c.named_children if x.type == "compound_statement"), None), fr)
        return all_term and not envs
