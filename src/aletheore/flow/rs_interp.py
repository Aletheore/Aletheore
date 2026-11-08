"""An abstract interpreter for Rust, built on tree-sitter. Same stance as the other interpreters: follow request data
(web framework extractors, request objects) to sinks and prove values safe; anything it cannot model is UNKNOWN. Rust
specifics: `format!` and other macros (their arguments are re-parsed from tokens), `&`/`?`/`.unwrap()` pass values
through, `impl` blocks, struct literals, `match` and `if let` patterns, closures passed to iterator methods."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter import Node, Parser

from aletheore.flow import guards as G
from aletheore.flow.rs_index import RsFile, RsIndex, RsType, type_name
from aletheore.flow.rs_sinks import (
    EXTRACTORS, PASS_METHODS, REQUEST_BENIGN, REQUEST_TYPES, SAFE_METHODS, SAFE_PARAM_TYPES, SANITIZERS,
)
from aletheore.flow.values import (
    add_prefix, SAFE, UNKNOWN, BuilderV, Choice, Const, ListV, MapV, Obj, Tainted, Val, classify, join, join_all,
)
from aletheore.scanner.graph import RUST_LANGUAGE

ITER_METHODS = {"map", "for_each", "filter", "filter_map", "and_then", "unwrap_or_else", "map_or", "map_or_else", "flat_map", "any", "all", "find", "position", "fold",
                "then", "inspect", "take_while", "skip_while", "or_else", "map_err", "ok_or_else", "unwrap_or_else", "get_or_insert_with", "entry_or_insert_with_x"}
PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*|\d+)?(?::[^}]*)?\}")
_FRAG_PARSER = Parser(RUST_LANGUAGE)


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class SFrame:
    file: RsFile
    cls: RsType | None
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
    table = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "'": "'", "\\": "\\"}
    return table.get(raw[1], raw[1:]) if raw.startswith("\\") and len(raw) >= 2 else raw


def _split_top(text: str, sep: str = ",") -> list[str]:
    out, depth, cur, i, in_str = [], 0, [], 0, False
    while i < len(text):
        ch = text[i]
        if in_str:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur))
    return [x.strip() for x in out]


_frag_cache: dict[str, Node | None] = {}


def _fragment(text: str) -> Node | None:
    if text in _frag_cache:
        return _frag_cache[text]
    src = ("fn _f(){ " + text + " ; }").encode()
    tree = _FRAG_PARSER.parse(src)
    node = None
    body = tree.root_node.named_children[0].child_by_field_name("body") if tree.root_node.named_children else None
    if body is not None and not tree.root_node.has_error:
        st = body.named_children[0] if body.named_children else None
        node = st.named_children[0] if st is not None and st.type == "expression_statement" and st.named_children else st
    _frag_cache[text] = node
    if len(_frag_cache) > 4000:
        _frag_cache.clear()
    return node


def _pattern_names(p: Node | None) -> list[str]:
    out: list[str] = []
    stack = [p] if p is not None else []
    while stack:
        n = stack.pop()
        if n.type == "identifier":
            nm = _t(n)
            if nm[:1].islower() or nm[:1] == "_":
                out.append(nm)
        elif n.type in ("scoped_identifier", "string_literal", "integer_literal", "boolean_literal", "char_literal", "type_identifier"):
            continue
        else:
            stack.extend(n.children)
    return [x for x in out if x != "_"]


class RsInterp:
    MAX_STEPS = 60000
    MAX_DEPTH = 6

    def __init__(self, index: RsIndex, cwes: set[int] | None = None, sink_hook=None):
        self.index = index
        self.cwes = cwes or set()
        self.sink_hook = sink_hook    # hook(node, name, recv_text, args, recv, fr)
        self.steps = 0
        self.probes: dict[tuple, list] = {}
        self.probe_keys: set[tuple] = set()
        self.dead: list[tuple] = []
        self._active: set[tuple] = set()
        self._frag = 0

    # ------------------------------------------------------------------ helpers
    def _tick(self) -> None:
        self.steps += 1
        if self.steps > self.MAX_STEPS:
            raise BudgetExceeded

    def _key(self, fr: SFrame, n: Node) -> tuple:
        return (id(fr.file), n.start_byte, n.end_byte)

    def _dead(self, fr: SFrame, n: Node | None) -> None:
        if n is not None and not self._frag:
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

    def _mutating(self, fr: SFrame, c) -> None:
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

    def _hook(self, node, name, recv_text, args, recv, fr) -> None:
        if self.sink_hook is not None and not self._frag:
            self.sink_hook(node, name, recv_text, args, recv, fr)

    # ------------------------------------------------------------------ expressions
    def eval(self, node: Node | None, fr: SFrame) -> Val:
        if node is None:
            return UNKNOWN
        self._tick()
        val = self._eval(node, fr)
        if not self._frag:
            key = self._key(fr, node)
            if key in self.probe_keys:
                self.probes.setdefault(key, []).append((val, classify(val)))
        return val

    def _eval(self, n: Node, fr: SFrame) -> Val:
        t = n.type
        if t == "string_literal":
            return Const("".join(_unesc(_t(c)) if c.type == "escape_sequence" else _t(c) for c in n.children if c.type in ("string_content", "escape_sequence")))
        if t == "raw_string_literal":
            return Const("".join(_t(c) for c in n.children if c.type == "string_content"))
        if t == "integer_literal":
            try:
                return Const(int(re.sub(r"(_|[iu](8|16|32|64|128|size))", "", _t(n)) or "0", 0))
            except ValueError:
                return SAFE
        if t == "float_literal":
            return SAFE
        if t == "boolean_literal":
            return Const(_t(n) == "true")
        if t == "char_literal":
            return Const(_t(n)[1:-1])
        if t == "identifier":
            return self._ident(_t(n), fr)
        if t == "self":
            return fr.this_obj or UNKNOWN
        if t == "scoped_identifier":
            return self._scoped(n, fr)
        if t == "parenthesized_expression":
            return self.eval(n.named_children[0], fr) if n.named_children else UNKNOWN
        if t == "field_expression":
            return self._field(n, fr)
        if t == "call_expression":
            return self._call(n, fr)
        if t == "macro_invocation":
            return self._macro(n, fr)
        if t == "binary_expression":
            return self._binary(n, fr)
        if t == "unary_expression":
            v = self.eval(n.named_children[-1], fr) if n.named_children else UNKNOWN
            op = _t(n.children[0]) if n.children else ""
            if op == "!" and isinstance(v, Const) and isinstance(v.value, bool):
                return Const(not v.value)
            return v if op == "*" else (SAFE if op in ("-", "!") else self._combine(v))
        if t in ("reference_expression", "try_expression", "await_expression", "type_cast_expression", "unsafe_block", "async_block", "const_block"):
            kid = next((c for c in n.named_children if c.type not in ("mutable_specifier", "primitive_type", "type_identifier", "generic_type", "scoped_type_identifier", "reference_type")), None)
            if t == "type_cast_expression":
                return self.eval(n.child_by_field_name("value"), fr)
            if t in ("unsafe_block", "async_block", "const_block"):
                self.exec(next((c for c in n.named_children if c.type == "block"), None), fr)
                return fr.last
            return self.eval(kid, fr)
        if t == "tuple_expression" or t == "array_expression":
            return ListV([self.eval(c, fr) for c in n.named_children], birth_depth=fr.branch)
        if t == "struct_expression":
            return self._struct(n, fr)
        if t == "index_expression":
            return self._index(n, fr)
        if t == "range_expression":
            return SAFE
        if t in ("assignment_expression", "compound_assignment_expr"):
            return self._assign(n, fr)
        if t in ("if_expression", "match_expression", "block", "for_expression", "while_expression", "loop_expression", "return_expression", "let_declaration"):
            self.exec(n, fr)
            return fr.last
        if t == "generic_function":
            return self.eval(n.child_by_field_name("function"), fr)
        if t == "closure_expression":
            return UNKNOWN
        return UNKNOWN

    def _ident(self, name: str, fr: SFrame) -> Val:
        if name in fr.env:
            return fr.env[name]
        c = fr.cls
        if c is not None and name in c.consts:
            return self._const(c, name, fr)
        if fr.file.main is not None and name in fr.file.main.consts:
            return self._const(fr.file.main, name, fr)
        return UNKNOWN

    def _const(self, cls: RsType, name: str, fr: SFrame) -> Val:
        init = cls.consts.get(name)
        key = (id(cls.file), init.start_byte if init is not None else -1)
        if init is None or key in self._active or fr.depth > self.MAX_DEPTH:
            return UNKNOWN
        self._active.add(key)
        try:
            v = self.eval(init, SFrame(cls.file, cls, None, fr.depth + 1))
        finally:
            self._active.discard(key)
        return v if classify(v) == "safe" else UNKNOWN

    def _scoped(self, n: Node, fr: SFrame) -> Val:
        path, name = n.child_by_field_name("path"), _t(n.child_by_field_name("name"))
        tn = _t(path).split("::")[-1]
        types = [fr.cls] if tn == "Self" and fr.cls is not None else self.index.ty(tn, fr.file)
        for ty in types:
            if name in ty.consts:
                return self._const(ty, name, fr)
        if tn in ("i32", "u32", "i64", "u64", "usize", "f64", "u8", "i8", "u16", "i16", "isize") or name in ("MAX", "MIN", "EPSILON"):
            return SAFE
        if tn == "String" and name == "new":
            return Const("")
        return UNKNOWN

    def _field(self, n: Node, fr: SFrame) -> Val:
        base = self.eval(n.child_by_field_name("value"), fr)
        name = _t(n.child_by_field_name("field"))
        if isinstance(base, Obj):
            if base.kind == "request":
                return Tainted("request." + name)
            return base.fields.get(name, UNKNOWN)
        if isinstance(base, ListV) and name.isdigit() and not base.dirty and not base.opaque and int(name) < len(base.items):
            return base.items[int(name)]
        if classify(base) == "tainted":
            return Tainted("derived")
        return UNKNOWN

    def _index(self, n: Node, fr: SFrame) -> Val:
        kids = n.named_children
        base = self.eval(kids[0], fr) if kids else UNKNOWN
        idx = self.eval(kids[1], fr) if len(kids) > 1 else UNKNOWN
        if isinstance(base, ListV):
            if isinstance(idx, Const) and isinstance(idx.value, int) and not isinstance(idx.value, bool) and not base.dirty and not base.opaque and 0 <= idx.value < len(base.items):
                return base.items[idx.value]
            return join_all(base.items + [UNKNOWN]) if base.items else UNKNOWN
        if isinstance(base, MapV):
            if isinstance(idx, Const) and not base.dirty and not base.opaque and idx.value in base.entries:
                return base.entries[idx.value]
            return join_all(list(base.entries.values()) + [UNKNOWN])
        if classify(base) == "tainted":
            return Tainted("derived")
        return UNKNOWN

    def _struct(self, n: Node, fr: SFrame) -> Val:
        name = type_name(n.child_by_field_name("name"))
        body = n.child_by_field_name("body")
        tys = self.index.ty(name, fr.file) if name not in ("Self",) else ([fr.cls] if fr.cls else [])
        obj = Obj(cls=tys[0] if tys else None, type_name=name)
        for c in (body.named_children if body is not None else []):
            if c.type == "shorthand_field_initializer":
                obj.fields[_t(c)] = self._ident(_t(c), fr)
            elif c.type == "field_initializer":
                obj.fields[_t(c.child_by_field_name("field"))] = self.eval(c.child_by_field_name("value"), fr)
        return obj

    def _binary(self, n: Node, fr: SFrame) -> Val:
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
        b = self.eval(right_n, fr)
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

    def _assign(self, n: Node, fr: SFrame) -> Val:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        op = _t(n.child_by_field_name("operator")) or "="
        val = self.eval(right, fr)
        if op != "=":
            cur = self.eval(left, fr)
            if op == "+=" and isinstance(cur, Const) and isinstance(val, Const) and isinstance(cur.value, str) and isinstance(val.value, str):
                val = Const(cur.value + val.value)
            else:
                val = self._combine(cur, val)
        self._store(left, val, fr)
        return val

    def _store(self, target: Node | None, val: Val, fr: SFrame) -> None:
        if target is None:
            return
        t = target.type
        if t == "identifier":
            fr.env[_t(target)] = val
        elif t == "field_expression":
            base = self.eval(target.child_by_field_name("value"), fr)
            nm = _t(target.child_by_field_name("field"))
            if isinstance(base, Obj):
                old = base.fields.get(nm)
                base.fields[nm] = join(old, val) if (fr.branch > 0 and old is not None) else val
        elif t == "index_expression":
            kids = target.named_children
            base = self.eval(kids[0], fr) if kids else UNKNOWN
            idx = self.eval(kids[1], fr) if len(kids) > 1 else UNKNOWN
            if isinstance(base, MapV):
                self._mutating(fr, base)
                if isinstance(idx, Const) and isinstance(idx.value, (str, int)):
                    base.entries[idx.value] = val
                else:
                    base.entries[("*", id(target))] = val
                    base.dirty = True
            elif isinstance(base, ListV):
                self._mutating(fr, base)
                base.items.append(val)
                base.dirty = True
        elif t == "unary_expression" and target.named_children:
            self._store(target.named_children[-1], val, fr)
        elif t in ("tuple_expression", "tuple_pattern"):
            for c in target.named_children:
                self._store(c, Tainted("derived") if classify(val) == "tainted" else UNKNOWN, fr)

    # ------------------------------------------------------------------ macros
    def _macro_parts(self, n: Node) -> list[str]:
        tt = next((c for c in n.children if c.type == "token_tree"), None)
        if tt is None:
            return []
        txt = _t(tt)
        return _split_top(txt[1:-1]) if len(txt) >= 2 else []

    def _eval_text(self, text: str, fr: SFrame) -> Val:
        node = _fragment(text)
        if node is None:
            return UNKNOWN
        self._frag += 1
        try:
            return self.eval(node, fr)
        finally:
            self._frag -= 1

    def _macro(self, n: Node, fr: SFrame) -> Val:
        name = _t(n.child_by_field_name("macro")).split("::")[-1]
        parts = self._macro_parts(n)
        if name in ("format", "format_args", "print", "println", "eprint", "eprintln", "write", "writeln", "panic", "unreachable", "todo", "unimplemented", "assert", "debug_assert", "assert_eq", "assert_ne", "info", "warn", "error", "debug", "trace", "log", "anyhow", "bail"):
            skip = 1 if name in ("write", "writeln", "log") else 0
            if name in ("assert", "debug_assert"):
                skip = 1
            if name in ("assert_eq", "assert_ne"):
                skip = 2
            if len(parts) <= skip:
                return Const("") if name in ("format", "format_args") else SAFE
            fmt_text = parts[skip]
            rest = parts[skip + 1:]
            fmt = self._eval_text(fmt_text, fr)
            if name not in ("format", "format_args"):
                return SAFE
            if not (isinstance(fmt, Const) and isinstance(fmt.value, str)):
                return self._combine(fmt, *(self._eval_text(r, fr) for r in rest))
            positional: list[Val] = []
            named: dict[str, Val] = {}
            for r in rest:
                m = re.match(r"^([A-Za-z_]\w*)\s*=\s*(?!=)(.*)$", r, re.S)
                if m:
                    named[m.group(1)] = self._eval_text(m.group(2), fr)
                else:
                    positional.append(self._eval_text(r, fr))
            out: list[Val] = []
            pos = 0
            last = 0
            for m in PLACEHOLDER.finditer(fmt.value):
                if m.start() > last:
                    out.append(Const(fmt.value[last:m.start()]))
                last = m.end()
                tok = m.group(0)
                if tok == "{{":
                    out.append(Const("{"))
                elif tok == "}}":
                    out.append(Const("}"))
                else:
                    arg = m.group(1)
                    if arg is None:
                        out.append(positional[pos] if pos < len(positional) else UNKNOWN)
                        pos += 1
                    elif arg.isdigit():
                        out.append(positional[int(arg)] if int(arg) < len(positional) else UNKNOWN)
                    else:
                        out.append(named[arg] if arg in named else self._ident(arg, fr))
            if last < len(fmt.value):
                out.append(Const(fmt.value[last:]))
            if all(isinstance(p, Const) for p in out):
                return Const("".join(_s(p.value) for p in out))
            return self._combine(*out)
        if name == "vec":
            if len(parts) == 1 and ";" in parts[0]:
                segs = _split_top(parts[0], ";")
                return ListV([self._eval_text(segs[0], fr)], birth_depth=fr.branch, dirty=True)
            return ListV([self._eval_text(p, fr) for p in parts], birth_depth=fr.branch)
        if name in ("concat", "stringify"):
            vals = [self._eval_text(p, fr) for p in parts]
            return Const("".join(_s(v.value) for v in vals)) if all(isinstance(v, Const) for v in vals) else self._combine(*vals)
        if name in ("include_str", "env", "option_env", "line", "file", "column", "module_path", "cfg", "matches"):
            return SAFE
        return UNKNOWN

    # ------------------------------------------------------------------ calls
    def _args(self, call: Node, fr: SFrame) -> list[Val]:
        a = call.child_by_field_name("arguments")
        return [self.eval(c, fr) for c in (a.named_children if a is not None else []) if c.type not in ("line_comment", "block_comment")]

    def _call(self, n: Node, fr: SFrame) -> Val:
        fn = n.child_by_field_name("function")
        if fn is None:
            return UNKNOWN
        if fn.type == "generic_function":
            tf = _t(fn.child_by_field_name("type_arguments"))
            fn = fn.child_by_field_name("function")
            turbofish = tf
        else:
            turbofish = ""
        if fn.type == "identifier":
            name = _t(fn)
            args = self._args(n, fr)
            self._hook(n, name, "", args, None, fr)
            return self._unqualified(name, args, fr)
        if fn.type == "scoped_identifier":
            path, name = _t(fn.child_by_field_name("path")), _t(fn.child_by_field_name("name"))
            args = self._args(n, fr)
            self._hook(n, name, path, args, None, fr)
            return self._static(path, name, args, fr)
        if fn.type == "field_expression":
            recv_n, name = fn.child_by_field_name("value"), _t(fn.child_by_field_name("field"))
            recv_text = _t(recv_n)
            args = self._args_with_closures(n, fr)
            if self._sanitizer(name):
                self.eval(recv_n, fr)
                return SAFE
            recv = self.eval(recv_n, fr)
            self._hook(n, name, recv_text, [a for a in args if a is not None], recv, fr)
            return self._method(recv, name, args, fr, recv_n, n, turbofish)
        self.eval(fn, fr)
        return self._opaque(None, self._args(n, fr))

    def _args_with_closures(self, call: Node, fr: SFrame) -> list[Val]:
        return self._args(call, fr)

    def _find_fn(self, types: list[RsType], name: str):
        for ty in types:
            if ty.funcs.get(name):
                return ty, ty.funcs[name][0]
        return None

    def _unqualified(self, name: str, args: list[Val], fr: SFrame) -> Val:
        if name in ("Some", "Ok", "Err", "Box", "Rc", "Arc") and len(args) == 1:
            return args[0]
        hit = self.index.free_fn(name, fr.file)
        if hit is not None:
            return self._run_fn(hit[0], hit[1], None, args, fr)
        if name in ("drop", "println", "dbg"):
            return SAFE
        return self._opaque(None, args)

    def _static(self, path: str, name: str, args: list[Val], fr: SFrame) -> Val:
        tn = path.split("::")[-1]
        types = [fr.cls] if tn == "Self" and fr.cls is not None else self.index.ty(tn, fr.file)
        hit = self._find_fn(types, name)
        if hit is not None:
            return self._run_fn(hit[0], hit[1], None, args, fr, assoc=True)
        if not types and tn[:1].islower() and tn not in ("fs", "std", "reqwest", "ureq", "surf", "isahc", "attohttpc", "minreq", "sqlx", "diesel", "env", "io", "process", "mem", "ptr", "str", "string", "vec", "path", "thread", "time", "json", "serde_json", "fmt", "char", "i32", "u32", "i64", "u64", "f64", "usize", "u8", "bool", "libloading", "html_escape", "ammonia", "shlex"):
            fhit = self.index.free_fn(name, fr.file)
            if fhit is not None:
                return self._run_fn(fhit[0], fhit[1], None, args, fr)
        if tn == "String":
            if name in ("from", "from_utf8_lossy", "from_utf8") and args:
                return args[0]
            if name in ("new", "with_capacity"):
                return Const("")
        if tn in ("Vec", "VecDeque", "HashSet", "BTreeSet", "BinaryHeap") and name in ("new", "with_capacity", "from"):
            return ListV(list(args[0].items) if name == "from" and args and isinstance(args[0], ListV) else [], birth_depth=fr.branch, dirty=bool(name == "from" and args and not isinstance(args[0], ListV)))
        if tn in ("HashMap", "BTreeMap") and name in ("new", "with_capacity", "from"):
            return MapV({}, birth_depth=fr.branch, dirty=name == "from")
        if tn == "Regex" and name in ("new", "from") and args and isinstance(args[0], Const) and isinstance(args[0].value, str):
            return Obj(type_name="Regex", fields={"_re": args[0]})  # remembered so a later `re.is_match(&x)` guard can read the pattern
        if tn in ("PathBuf", "Path", "OsString", "Cow") and name in ("from", "new") and args:
            return args[0]
        if tn in ("Option", "Result") and name in ("Some", "Ok") and args:
            return args[0]
        if tn in ("i8", "i16", "i32", "i64", "i128", "isize", "u8", "u16", "u32", "u64", "u128", "usize", "f32", "f64", "bool", "char") and name in ("from_str", "from_str_radix", "from", "try_from", "parse"):
            return SAFE
        if tn == "Uuid" or tn == "Instant" or tn == "SystemTime" or tn == "Utc" or tn == "Local" or tn == "Duration":
            return SAFE
        if name in SAFE_METHODS and tn in ("String", "str", "Path", "fs"):
            return SAFE
        if tn in ("Default",) and name == "default":
            return UNKNOWN
        return self._opaque(None, args)

    def _method(self, recv: Val, name: str, args: list[Val], fr: SFrame, recv_n: Node | None, call: Node, turbofish: str) -> Val:
        if isinstance(recv, Choice):
            return join_all(self._method(a, name, args, fr, recv_n, call, turbofish) for a in recv.alts)
        closure = next((c for c in (call.child_by_field_name("arguments").named_children if call.child_by_field_name("arguments") is not None else []) if c.type == "closure_expression"), None)
        if isinstance(recv, Obj):
            if recv.kind == "request":
                return SAFE if name in REQUEST_BENIGN else Tainted("request." + name)
            if recv.type_name == "Regex" and name in PASS_METHODS:
                return recv
            if recv.cls is not None:
                hit = self._find_fn(self.index.ty(recv.cls.name, fr.file) or [recv.cls], name)
                if hit is not None:
                    return self._run_fn(hit[0], hit[1], recv, args, fr)
            if closure is not None:
                self._run_closure(closure, [UNKNOWN], fr)
            return self._opaque(recv, args)
        if isinstance(recv, ListV):
            return self._list_method(recv, name, args, fr, closure)
        if isinstance(recv, MapV):
            return self._map_method(recv, name, args, fr, closure)
        if name == "parse":
            return SAFE if not any(x in turbofish for x in ("String", "PathBuf")) else self._combine(recv)
        if name in SAFE_METHODS:
            return SAFE
        if name in ("push_str", "push", "insert_str", "extend", "append") and recv_n is not None and recv_n.type == "identifier" and args:
            cur = recv
            if isinstance(cur, Const) and isinstance(cur.value, str) and isinstance(args[0], Const):
                new: Val = Const(cur.value + _s(args[0].value))
            else:
                new = self._combine(cur, *args)
            self._store(recv_n, new, fr)
            return SAFE
        if isinstance(recv, Const) and isinstance(recv.value, str):
            if name in PASS_METHODS:
                if name in ("to_lowercase", "to_ascii_lowercase"):
                    return Const(recv.value.lower())
                if name in ("to_uppercase", "to_ascii_uppercase"):
                    return Const(recv.value.upper())
                if name in ("trim",):
                    return Const(recv.value.strip())
                if name in ("replace",) and len(args) == 2 and all(isinstance(a, Const) for a in args):
                    return Const(recv.value.replace(_s(args[0].value), _s(args[1].value)))
                if name in ("join", "concat", "repeat", "replacen", "strip_prefix", "strip_suffix", "trim_matches"):
                    return self._combine(recv, *args)
                return recv
            if closure is not None:
                self._run_closure(closure, [recv], fr)
            return self._combine(recv, *args)
        if name in PASS_METHODS and not (closure is not None):
            if name in ("join", "concat", "repeat", "replace", "replacen"):
                return self._combine(recv, *args)
            return recv
        if closure is not None:
            res = self._run_closure(closure, [recv if classify(recv) == "tainted" else UNKNOWN], fr)
            if name in ("map", "and_then", "map_or", "map_or_else"):
                return res if classify(res) != "unknown" else (Tainted("derived") if classify(recv) == "tainted" else UNKNOWN)
            if name in ("unwrap_or_else", "or_else"):
                return join(recv, res)
        if classify(recv) == "tainted":
            return Tainted("derived")
        return self._opaque(recv, args)

    def _list_method(self, lst: ListV, name: str, args: list[Val], fr: SFrame, closure: Node | None) -> Val:
        if name in ("push", "push_back", "push_front", "insert") and args:
            self._mutating(fr, lst)
            lst.items.append(args[-1])
            if name == "insert":
                lst.dirty = True
            return SAFE
        if name in ("extend", "append", "extend_from_slice", "clear", "remove", "pop", "pop_front", "pop_back", "truncate", "retain", "sort", "reverse", "dedup", "drain", "swap", "swap_remove", "resize"):
            self._mutating(fr, lst)
            lst.dirty = True
            for a in args:
                if isinstance(a, ListV):
                    lst.items.extend(a.items)
            return SAFE
        if name in ("len", "is_empty", "contains", "iter_x", "capacity"):
            return SAFE
        if name in ("iter", "into_iter", "iter_mut", "clone", "to_vec", "cloned", "copied", "rev", "skip", "take", "chain", "collect", "as_slice", "into_boxed_slice", "windows", "chunks", "peekable", "sorted_x"):
            return ListV(list(lst.items), birth_depth=fr.branch, dirty=True, opaque=lst.opaque) if name != "collect" or not lst.opaque else ListV(list(lst.items), birth_depth=fr.branch, dirty=True, opaque=True)
        if name in ITER_METHODS and closure is not None:
            elem = join_all(lst.items + ([UNKNOWN] if lst.dirty or lst.opaque else [])) if lst.items else UNKNOWN
            res = self._run_closure(closure, [elem, UNKNOWN] if name == "fold" else [elem], fr)
            if name in ("map", "filter_map", "flat_map"):
                return ListV([res], birth_depth=fr.branch, dirty=True)
            return UNKNOWN
        if name in ("join", "concat"):
            sep = args[0] if args else Const("")
            if not lst.dirty and not lst.opaque and all(isinstance(i, Const) for i in lst.items) and isinstance(sep, Const):
                return Const(_s(sep.value).join(_s(i.value) for i in lst.items))
            return self._combine(*lst.items, sep, *([UNKNOWN] if lst.opaque else []))
        if name in ("get", "first", "last", "unwrap", "next", "nth", "pop_x", "get_mut", "unwrap_or", "expect", "min", "max", "find"):
            return join_all(lst.items + [UNKNOWN]) if lst.items else UNKNOWN
        return self._opaque(lst, args)

    def _map_method(self, m: MapV, name: str, args: list[Val], fr: SFrame, closure: Node | None) -> Val:
        key = args[0].value if args and isinstance(args[0], Const) and isinstance(args[0].value, (str, int)) and not isinstance(args[0].value, bool) else None
        if name in ("insert", "entry", "or_insert") and len(args) >= 2:
            self._mutating(fr, m)
            if key is not None:
                m.entries[key] = args[1]
            else:
                m.entries[("*", id(args[0]))] = args[1]
                m.dirty = True
            return SAFE
        if name in ("get", "get_mut", "unwrap_or", "index"):
            if key is not None and not m.dirty and not m.opaque and key in m.entries:
                return m.entries[key]
            return join_all(list(m.entries.values()) + [UNKNOWN])
        if name in ("contains_key", "len", "is_empty"):
            return SAFE
        if name in ("remove", "clear", "extend", "retain", "drain"):
            self._mutating(fr, m)
            m.dirty = True
            return SAFE
        if name in ("values", "keys", "iter", "into_iter", "values_cloned"):
            return ListV(list(m.entries.values()), birth_depth=fr.branch, dirty=True, opaque=m.opaque or m.dirty)
        return self._opaque(m, args)

    # --- closures & functions
    def _run_closure(self, cl: Node | None, params: list[Val], fr: SFrame) -> Val:
        if cl is None:
            return UNKNOWN
        pn = cl.child_by_field_name("parameters")
        names = []
        for c in (pn.named_children if pn is not None else []):
            names.extend(_pattern_names(c.child_by_field_name("pattern") if c.type == "parameter" else c))
        saved = {k: fr.env.get(k) for k in names}
        had = {k: (k in fr.env) for k in names}
        for i, k in enumerate(names):
            fr.env[k] = params[i] if i < len(params) else UNKNOWN
        fr.branch += 1
        before = fr.last
        fr.last = UNKNOWN
        try:
            body = cl.child_by_field_name("body")
            if body is not None and body.type == "block":
                self.exec(body, fr)
                last = fr.last
            else:
                last = self.eval(body, fr)
        finally:
            fr.branch -= 1
            fr.last = before
            for k in names:
                if had[k]:
                    fr.env[k] = saved[k]
                else:
                    fr.env.pop(k, None)
        return last

    def _run_fn(self, cls: RsType, fn: Node, recv: Obj | None, args: list[Val], fr: SFrame, assoc: bool = False) -> Val:
        key = (id(cls.file), fn.start_byte)
        if fr.depth + 1 > self.MAX_DEPTH or key in self._active:
            return self._opaque(recv, args)
        inner = SFrame(cls.file, cls, recv, fr.depth + 1)
        pn = fn.child_by_field_name("parameters")
        pos = list(args)
        for p in (pn.named_children if pn is not None else []):
            if p.type == "self_parameter":
                if assoc and pos:
                    inner.this_obj = pos.pop(0) if isinstance(pos[0], Obj) else inner.this_obj
                continue
            if p.type == "parameter":
                v = pos.pop(0) if pos else UNKNOWN
                for nm in _pattern_names(p.child_by_field_name("pattern")):
                    inner.env[nm] = v
        body = fn.child_by_field_name("body")
        self._active.add(key)
        try:
            self.exec(body, inner)
        finally:
            self._active.discard(key)
        return join_all(inner.rets + [inner.last]) if inner.rets else inner.last

    def bind_entry(self, fn: Node, fr: SFrame, action: bool) -> None:
        pn = fn.child_by_field_name("parameters")
        for p in (pn.named_children if pn is not None else []):
            if p.type != "parameter":
                continue
            tn = type_name(p.child_by_field_name("type"))
            names = _pattern_names(p.child_by_field_name("pattern"))
            if tn in REQUEST_TYPES:
                v: Val = Obj(kind="request", type_name=tn)
            elif tn in EXTRACTORS:
                inner = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _t(p.child_by_field_name("type")).split("<", 1)[1])) if "<" in _t(p.child_by_field_name("type")) else set()
                v = SAFE if inner and inner <= SAFE_PARAM_TYPES else Tainted("param")
            elif tn in SAFE_PARAM_TYPES:
                v = SAFE
            elif action and tn in ("String", "str"):
                v = Tainted("param")
            else:
                v = UNKNOWN
            for nm in names:
                fr.env[nm] = v

    # ------------------------------------------------------------------ statements
    def exec(self, n: Node | None, fr: SFrame) -> bool:
        if n is None:
            return False
        self._tick()
        t = n.type
        if t == "block":
            fr.last = Const(None)
            for c in n.named_children:
                if self.exec(c, fr):
                    return True
            return False
        if t == "expression_statement":
            for c in n.named_children:
                if self.exec(c, fr):
                    return True
            fr.last = Const(None) if _t(n).rstrip().endswith(";") and n.named_children and n.named_children[0].type not in ("if_expression", "match_expression") else fr.last
            return False
        if t == "let_declaration":
            val_n = n.child_by_field_name("value")
            val = self.eval(val_n, fr) if val_n is not None else UNKNOWN
            pat = n.child_by_field_name("pattern")
            if pat is not None and pat.type == "identifier":
                fr.env[_t(pat)] = val
            else:
                for nm in _pattern_names(pat):
                    fr.env[nm] = val if classify(val) in ("tainted", "safe") else UNKNOWN
            fr.last = Const(None)
            return False
        if t == "return_expression":
            kid = n.named_children[0] if n.named_children else None
            v = self.eval(kid, fr) if kid is not None else Const(None)
            fr.rets.append(v)
            fr.last = v
            return True
        if t in ("break_expression", "continue_expression"):
            for c in n.named_children:
                self.eval(c, fr)
            return True
        if t == "if_expression":
            return self._if(n, fr)
        if t == "match_expression":
            return self._match(n, fr)
        if t in ("for_expression", "while_expression", "loop_expression"):
            return self._loop(n, fr)
        if t in ("function_item", "struct_item", "enum_item", "impl_item", "use_declaration", "const_item", "static_item", "mod_item", "line_comment", "block_comment", "empty_statement", "attribute_item", "macro_definition"):
            return False
        if t == "macro_invocation" and _t(n.child_by_field_name("macro")).split("::")[-1] in ("panic", "unreachable", "todo", "unimplemented", "bail"):
            fr.last = self.eval(n, fr)
            return True
        fr.last = self.eval(n, fr)
        return False

    def _join_envs(self, fr: SFrame, a: dict, b: dict) -> None:
        out = {}
        for k in set(a) | set(b):
            out[k] = join(a[k], b[k]) if (k in a and k in b) else (a[k] if k in a else b[k])
        fr.env = out

    def _cond(self, cond_n: Node | None, fr: SFrame) -> Val:
        """Evaluate an `if` condition; `if let PAT = EXPR` binds PAT's names to EXPR's value and is unknown."""
        if cond_n is not None and cond_n.type in ("let_condition",):
            v = self.eval(cond_n.child_by_field_name("value"), fr)
            for nm in _pattern_names(cond_n.child_by_field_name("pattern")):
                fr.env[nm] = v if classify(v) in ("tainted", "safe") else UNKNOWN
            return UNKNOWN
        if cond_n is not None and cond_n.type == "let_chain":
            for c in cond_n.named_children:
                self._cond(c, fr)
            return UNKNOWN
        return self.eval(cond_n, fr)

    # ------------------------------------------------------------------ guards
    def _engine(self, fr: SFrame) -> G.GuardEngine:
        def strip_ref(n):
            while n is not None:
                if n.type in ("reference_expression", "parenthesized_expression") and n.named_children:
                    n = n.named_children[-1]
                elif n.type == "call_expression" and n.child_by_field_name("function") is not None and n.child_by_field_name("function").type == "field_expression" \
                        and _t(n.child_by_field_name("function").child_by_field_name("field")) in ("as_str", "as_ref", "clone", "to_string", "to_owned", "as_bytes"):
                    n = n.child_by_field_name("function").child_by_field_name("value")  # same text, same validity
                else:
                    break
            return n

        def norm(n):
            t = n.type
            if t == "parenthesized_expression":
                return ("paren", n.named_children[0] if n.named_children else None)
            if t == "unary_expression" and n.children and _t(n.children[0]) == "!":
                return ("not", n.named_children[-1] if n.named_children else None)
            if t == "binary_expression":
                op = _t(n.child_by_field_name("operator"))
                l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
                return {"&&": ("and", l, r), "||": ("or", l, r), "==": ("eq", strip_ref(l), strip_ref(r)), "!=": ("ne", strip_ref(l), strip_ref(r))}.get(op, ("other",))
            if t == "call_expression":
                fn = n.child_by_field_name("function")
                a = n.child_by_field_name("arguments")
                args = [strip_ref(c) for c in (a.named_children if a is not None else []) if c.type not in ("line_comment", "block_comment")]
                if fn is not None and fn.type == "generic_function":
                    fn = fn.child_by_field_name("function")
                if fn is not None and fn.type == "field_expression":
                    recv_n, name = fn.child_by_field_name("value"), _t(fn.child_by_field_name("field"))
                    if name in ("is_ok", "is_some") and recv_n is not None and recv_n.type == "call_expression":
                        inner = recv_n.child_by_field_name("function")
                        if inner is not None and inner.type == "generic_function":
                            inner = inner.child_by_field_name("function")
                        if inner is not None and inner.type == "field_expression" and _t(inner.child_by_field_name("field")) == "parse":
                            return ("call", "isNumeric", None, [strip_ref(inner.child_by_field_name("value"))], "")  # x.parse::<u32>().is_ok()
                    if name == "all" and recv_n is not None and recv_n.type == "call_expression" and a is not None and a.named_children \
                            and a.named_children[0].type == "closure_expression":
                        body = _t(a.named_children[0].child_by_field_name("body"))
                        src = recv_n.child_by_field_name("function")
                        if re.search(r"\.(is_alphanumeric|is_ascii_alphanumeric|is_numeric|is_ascii_digit|is_alphabetic|is_ascii_alphabetic)\(\)", body) and src is not None \
                                and src.type == "field_expression" and _t(src.child_by_field_name("field")) in ("chars", "bytes"):
                            return ("call", "isNumeric", None, [strip_ref(src.child_by_field_name("value"))], "")  # x.chars().all(|c| c.is_alphanumeric())
                    return ("call", name, recv_n, args, _t(recv_n))
                if fn is not None and fn.type == "scoped_identifier":
                    return ("call", _t(fn.child_by_field_name("name")), None, args, _t(fn.child_by_field_name("path")))
                return ("call", _t(fn), None, args, "")
            return ("other",)

        def local(n):
            n = strip_ref(n)
            return _t(n) if n is not None and n.type == "identifier" and _t(n) in fr.env else None

        def const(n):
            if n is None:
                return None
            v = self.eval(strip_ref(n), fr)
            return v.value if isinstance(v, Const) and isinstance(v.value, str) else None

        def safe(n):
            return classify(self.eval(strip_ref(n), fr)) == "safe"

        def coll(n):
            v = self.eval(strip_ref(n), fr)
            return isinstance(v, ListV) and bool(v.items) and not v.dirty and not v.opaque and all(classify(i) == "safe" for i in v.items)

        def pattern_source(n):
            if n is not None and n.type == "identifier" and isinstance(fr.env.get(_t(n)), Obj) and fr.env[_t(n)].type_name == "Regex":
                v = fr.env[_t(n)].fields.get("_re")
                return v.value if isinstance(v, Const) else None
            while n is not None and n.type == "call_expression":
                fn = n.child_by_field_name("function")
                if fn is not None and fn.type == "field_expression" and _t(fn.child_by_field_name("field")) in ("unwrap", "expect", "clone"):
                    n = fn.child_by_field_name("value")
                    continue
                if fn is not None and fn.type == "scoped_identifier" and _t(fn.child_by_field_name("path")).endswith("Regex") and _t(fn.child_by_field_name("name")) in ("new", "from"):
                    a = n.child_by_field_name("arguments")
                    first = a.named_children[0] if a is not None and a.named_children else None
                    return const(first)
                return None
            return None

        def attr(n):
            n = strip_ref(n)
            if n is not None and n.type == "call_expression" and n.child_by_field_name("function") is not None and n.child_by_field_name("function").type == "field_expression":
                fn = n.child_by_field_name("function")  # u.host_str()
                v = local(fn.child_by_field_name("value"))
                return (v, _t(fn.child_by_field_name("field"))) if v else None
            return None

        return G.GuardEngine(norm, local, const, safe, coll, pattern_source, self.cwes, attr=attr)

    def _narrow(self, names: set, fr: SFrame) -> None:
        G.apply(names, self.cwes, fr.env, SAFE)

    def _if(self, n: Node, fr: SFrame) -> bool:
        cond = self._cond(n.child_by_field_name("condition"), fr)
        cons, alt_c = n.child_by_field_name("consequence"), n.child_by_field_name("alternative")
        alt = next((c for c in (alt_c.named_children if alt_c is not None else [])), None)
        if isinstance(cond, Const) and isinstance(cond.value, bool):
            self._dead(fr, alt if cond.value else cons)
            branch = cons if cond.value else alt
            if branch is None:
                fr.last = Const(None)
                return False
            return self.exec(branch, fr)
        base = dict(fr.env)
        facts = self._engine(fr).facts(n.child_by_field_name("condition"))
        fr.branch += 1
        try:
            fr.last = UNKNOWN
            self._narrow(facts[0], fr)
            ta = self.exec(cons, fr) if cons is not None else False
            ea, la = fr.env, fr.last
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

    def _match(self, n: Node, fr: SFrame) -> bool:
        subj = self.eval(n.child_by_field_name("value"), fr)
        body = n.child_by_field_name("body")
        base = dict(fr.env)
        envs, lasts = [], []
        fr.branch += 1
        try:
            for arm in [c for c in (body.named_children if body is not None else []) if c.type == "match_arm"]:
                fr.env = dict(base)
                pat = arm.child_by_field_name("pattern")
                for nm in _pattern_names(pat):
                    fr.env[nm] = subj if classify(subj) in ("tainted", "safe") else UNKNOWN
                guard = next((c for c in (pat.named_children if pat is not None else []) if c.type == "condition"), None)
                if guard is not None:
                    self.eval(guard.named_children[0] if guard.named_children else None, fr)
                fr.last = UNKNOWN
                val_n = arm.child_by_field_name("value")
                term = self.exec(val_n, fr) if val_n is not None and val_n.type in ("block", "return_expression") else False
                if val_n is not None and not (val_n.type in ("block", "return_expression")):
                    fr.last = self.eval(val_n, fr)
                if not term:
                    envs.append(fr.env)
                    lasts.append(fr.last)
        finally:
            fr.branch -= 1
        if not envs:
            fr.env = base
            return bool(body is not None and body.named_children)
        fr.env = envs[0]
        for e in envs[1:]:
            self._join_envs(fr, fr.env, e)
        fr.last = join_all(lasts)
        return False

    def _loop(self, n: Node, fr: SFrame) -> bool:
        base = dict(fr.env)
        fr.branch += 1
        try:
            if n.type == "for_expression":
                it = self.eval(n.child_by_field_name("value"), fr)
                elem = join_all(it.items + ([UNKNOWN] if it.dirty or it.opaque else [])) if isinstance(it, ListV) and it.items else (Tainted("derived") if classify(it) == "tainted" else UNKNOWN)
                for nm in _pattern_names(n.child_by_field_name("pattern")):
                    fr.env[nm] = elem
            elif n.type == "while_expression":
                self._cond(n.child_by_field_name("condition"), fr)
            self.exec(n.child_by_field_name("body"), fr)
        finally:
            fr.branch -= 1
        self._join_envs(fr, base, fr.env)
        fr.last = Const(None)
        return False
