"""An abstract interpreter for Java method bodies, built on tree-sitter.

What it models: constants and arithmetic, string and char operations on constants, if/ternary/
switch with constant conditions (dead arms are pruned), loops (one pass, joined with "no pass"),
try/catch, local lists, maps and StringBuilders, object creation and field assignment, and calls to
methods defined in the repository (followed across files, to a bounded depth).

Soundness stance: this exists to PROVE a value safe. Whenever it cannot model something it returns
UNKNOWN, never SAFE, and a mutation inside a branch that may not run marks the container "dirty" so
later reads are not trusted. A finding is only dropped on a proof.
"""

from __future__ import annotations

from functools import lru_cache

from aletheore.flow.budget import STEPS as _STEPS

from dataclasses import dataclass, field

from tree_sitter import Node

from aletheore.flow import extra_sinks as _X
from aletheore.flow.java_index import ClassInfo, JavaFile, JavaIndex, simple_type_name
from aletheore.flow import guards as G
from aletheore.flow.values import (
    CallableV, FuncV,
    RANDOM_MARK,
    add_prefix, SAFE,
    UNKNOWN,
    BuilderV,
    Char,
    Choice,
    Const,
    ListV,
    MapV,
    Obj,
    Tainted,
    Val,
    classify,
    join,
    join_all,
)

REQUEST_TYPES = {"HttpServletRequest", "ServletRequest", "HttpServletRequestWrapper", "MultipartHttpServletRequest"}
REQUEST_SOURCES = {
    "getParameter", "getParameterValues", "getParameterMap", "getParameterNames", "getHeader", "getHeaders",
    "getHeaderNames", "getQueryString", "getCookies", "getInputStream", "getReader", "getPathInfo",
    "getRequestURI", "getRequestURL", "getPart", "getParts",
}
# Methods whose result is a number or boolean about the input, not the input itself.
BENIGN_RESULT_METHODS = {
    "length", "size", "isEmpty", "equals", "equalsIgnoreCase", "contains", "startsWith", "endsWith",
    "indexOf", "lastIndexOf", "hashCode", "compareTo", "compareToIgnoreCase", "matches", "isBlank",
    "hasNext", "hasMoreElements", "ordinal",
    # Numbers, booleans and dates read from a result set or a boxed value: no markup, quote or path can ride in them.
    "getInt", "getLong", "getDouble", "getFloat", "getShort", "getByte", "getBoolean", "getBigDecimal", "getRow",
    "intValue", "longValue", "doubleValue", "floatValue", "shortValue", "byteValue", "booleanValue", "getColumnCount",
    "getTimestamp", "getDate",
    # Metadata about the program itself, never request data: `this.getClass().getClassLoader()`, `Foo.class.getName()`.
    "getClass", "getClassLoader", "getSimpleName", "getCanonicalName", "getPackage", "getModifiers",
}
PARSERS = {
    ("Integer", "parseInt"), ("Integer", "valueOf"), ("Long", "parseLong"), ("Long", "valueOf"),
    ("Double", "parseDouble"), ("Double", "valueOf"), ("Float", "parseFloat"), ("Boolean", "parseBoolean"),
    ("Short", "parseShort"), ("Byte", "parseByte"), ("Integer", "toString"), ("Long", "toString"),
}
LIST_TYPES = {"ArrayList", "LinkedList", "Vector", "Stack", "CopyOnWriteArrayList"}
MAP_TYPES = {"HashMap", "LinkedHashMap", "TreeMap", "Hashtable", "ConcurrentHashMap", "WeakHashMap", "IdentityHashMap"}
BUILDER_TYPES = {"StringBuilder", "StringBuffer"}
# (receiver type, method) -> type of the object it returns, for sink matching.
MODELED_FACTORIES = {
    ("HttpServletResponse", "getWriter"): "PrintWriter",
    ("HttpServletResponse", "getOutputStream"): "ServletOutputStream",
    ("ServletResponse", "getWriter"): "PrintWriter",
    ("Connection", "createStatement"): "Statement",
    ("Connection", "prepareStatement"): "PreparedStatement",
    ("HttpSession", "getServletContext"): "ServletContext",
    ("XPathFactory", "newXPath"): "XPath",
    ("Runtime", "getRuntime"): "Runtime",
}
ANNOTATED_SOURCE_PARAMS = {
    "RequestParam", "PathVariable", "RequestBody", "RequestHeader", "CookieValue",
    "QueryParam", "PathParam", "FormParam", "HeaderParam", "CookieParam", "BeanParam", "MatrixParam",
}
# Library classes whose static methods and constructors cannot manufacture request data from safe
# inputs. Anything outside these lists stays UNKNOWN, so for example a thread-local request holder
# is never mistaken for a safe value.
PURE_STATIC_CLASSES = {
    "Math", "Integer", "Long", "Double", "Float", "Short", "Byte", "Boolean", "Character", "String", "Objects",
    "Arrays", "Collections", "UUID", "Paths", "Path", "Files", "Locale", "Charset", "StandardCharsets",
    "Instant", "LocalDate", "LocalDateTime", "TimeUnit", "Pattern",
    # encoders and digests: taint passes through, nothing else is introduced
    "Base64", "Hex", "DigestUtils", "URLEncoder", "URLDecoder", "StringUtils", "NumberUtils", "FilenameUtils",
}
PURE_STATIC_METHODS = {("System", "getProperty"), ("System", "getenv"), ("System", "lineSeparator"),
                       ("System", "currentTimeMillis"), ("System", "nanoTime")}
PURE_CTOR_CLASSES = {
    "File", "URL", "URI", "Locale", "Random", "SecureRandom", "Date", "Properties", "Object", "ProcessBuilder",
    "FileInputStream", "FileOutputStream", "FileReader", "FileWriter", "BufferedReader", "InputStreamReader",
    "PrintWriter", "StringReader", "StringWriter", "ByteArrayOutputStream", "ByteArrayInputStream",
    "SimpleDateFormat", "Integer", "Long", "Double", "Boolean", "String", "Exception", "RuntimeException",
    "ServletException", "IOException", "IllegalArgumentException", "Cookie",
}

# Context encoders per CWE: a value passed through one is safe for that sink class only.
SANITIZERS: dict[int, set[str]] = {
    79: {"encodeForHTML", "encodeForHTMLAttribute", "encodeForJavaScript", "encodeForCSS", "encodeForURL",
         "encodeForXML", "forHtml", "forHtmlContent", "forHtmlAttribute", "forHtmlUnquotedAttribute",
         "forJavaScript", "forJavaScriptAttribute", "forJavaScriptBlock", "forCssString", "forUriComponent",
         "htmlEscape", "escapeHtml", "escapeHtml4", "escapeEcmaScript", "escapeXml", "escapeXml10"},
    89: {"encodeForSQL"},
    78: {"encodeForOS"},
    90: {"encodeForLDAP", "encodeForDN"},
    643: {"encodeForXPath"},
}


class BudgetExceeded(Exception):
    pass


@dataclass(eq=False)
class LambdaV(CallableV):
    """A lambda with the scope it was written in: calling it later (c.accept(x), f.apply(x), r.run()) runs its body with the arguments."""

    node: Node
    file: JavaFile
    cls: object
    this_obj: object
    env: dict
    types: dict


# methods of the functional interfaces that are not "run the function"
_FN_OTHER = frozenset(("equals", "hashCode", "toString", "andThen", "compose", "negate", "and", "or", "reversed", "thenComparing", "identity", "getClass"))
_ELEMENT_CALLBACKS = frozenset(("forEach", "map", "filter", "flatMap", "anyMatch", "allMatch", "noneMatch", "peek", "ifPresent", "removeIf",
                                "mapToInt", "mapToObj"))


@dataclass(eq=False)
class Frame:
    file: JavaFile
    cls: ClassInfo | None
    this_obj: Obj | None
    depth: int
    env: dict = field(default_factory=dict)
    types: dict = field(default_factory=dict)
    rets: list = field(default_factory=list)
    branch: int = 0  # depth of "may not execute" nesting


def _t(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


@lru_cache(maxsize=65536)
def _unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "'": "'", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _to_str(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    return str(v)


def _java_int_div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def jf_src(fr) -> bytes:
    return fr.file.src


# Entries of zip/jar/tar archives: their name is chosen by whoever made the archive.
ARCHIVE_ENTRY_TYPES = {"ZipEntry", "JarEntry", "TarArchiveEntry", "ZipArchiveEntry", "ArchiveEntry", "SevenZArchiveEntry",
                       "ArjArchiveEntry", "CpioArchiveEntry", "TarEntry", "FileHeader", "ArArchiveEntry"}


def _path_typed(n, fr) -> bool:
    """java.nio Path.startsWith compares whole path components, so it needs no trailing separator."""
    return n is not None and n.type == "identifier" and fr.types.get(_t(n)) == "Path"


class Interp:
    MAX_STEPS = 60000
    MAX_DEPTH = 6

    def __init__(self, index: JavaIndex, cwes: set[int] | None = None, sink_hook=None, annotated_sources: bool = False):
        self.index = index
        self.cwes = cwes or set()
        self.sink_hook = sink_hook  # called as hook(node, name, cname, recv_type, recv_text, args, frame, recv)
        self.annotated_sources = annotated_sources
        self.steps = 0
        self.probes: dict[tuple, list[Val]] = {}
        self.probe_keys: set[tuple] = set()
        self.dead: list[tuple] = []
        self._active: set[int] = set()

    # ------------------------------------------------------------------ helpers
    def _tick(self) -> None:
        self.steps += 1
        _STEPS[0] += 1
        if self.steps > self.MAX_STEPS:
            raise BudgetExceeded

    def _key(self, fr: Frame, node: Node) -> tuple:
        return (id(fr.file), node.start_byte, node.end_byte)

    def _mark_dead(self, fr: Frame, node: Node | None) -> None:
        if node is not None:
            self.dead.append(self._key(fr, node))

    def _sanitizer(self, name: str, recv: str = "") -> bool:
        return any(name in SANITIZERS.get(c, ()) for c in self.cwes) or _X.sanitizes("java", self.cwes, name, recv)

    @staticmethod
    def _combine(*vals: Val) -> Val:
        """Result of mixing values (concatenation and similar): worst classification wins."""
        worst = "safe"
        rank = {"safe": 0, "unknown": 1, "tainted": 2}
        for v in vals:
            c = classify(v)
            if rank[c] > rank[worst]:
                worst = c
        return add_prefix({"safe": SAFE, "unknown": UNKNOWN, "tainted": Tainted("derived")}[worst], vals)

    def _mutating(self, fr: Frame, container) -> None:
        if fr.branch > container.birth_depth:
            container.dirty = True

    # ------------------------------------------------------------------ expressions
    def eval(self, node: Node | None, fr: Frame) -> Val:
        if node is None:
            return UNKNOWN
        self._tick()
        val = self._eval(node, fr)
        key = self._key(fr, node) if self.probe_keys else None  # (nothing to record in a normal scan)
        if key is not None and key in self.probe_keys:
            # Classified now: a later call that is handed the same container must not change what
            # the sink saw.
            self.probes.setdefault(key, []).append((val, classify(val)))
        return val

    def _eval(self, node: Node, fr: Frame) -> Val:
        t = node.type
        if t == "string_literal":
            raw = _t(node)
            if raw.startswith('"""'):
                return UNKNOWN
            return Const(_unescape(raw[1:-1]))
        if t == "character_literal":
            return Const(Char(_unescape(_t(node)[1:-1])))
        if t in ("decimal_integer_literal", "hex_integer_literal", "octal_integer_literal", "binary_integer_literal"):
            text = _t(node).replace("_", "").rstrip("lL")
            try:
                return Const(int(text, 0) if t != "octal_integer_literal" else int(text, 8))
            except ValueError:
                return UNKNOWN
        if t in ("decimal_floating_point_literal", "hex_floating_point_literal"):
            try:
                return Const(float(_t(node).rstrip("fFdD")))
            except ValueError:
                return UNKNOWN
        if t == "true":
            return Const(True)
        if t == "false":
            return Const(False)
        if t == "null_literal":
            return Const(None)
        if t == "identifier":
            return self._lookup(_t(node), fr)
        if t == "this":
            return fr.this_obj or UNKNOWN
        if t == "parenthesized_expression":
            return self.eval(node.named_children[0], fr) if node.named_children else UNKNOWN
        if t == "class_literal":
            return SAFE
        if t == "cast_expression":
            inner = self.eval(node.child_by_field_name("value"), fr)
            tname = simple_type_name(node.child_by_field_name("type"))
            if inner is UNKNOWN and self.index.resolve_class(tname, fr.file) is not None:
                return Obj(type_name=tname)  # some implementation of a repository type
            return inner
        if t == "binary_expression":
            return self._binary(node, fr)
        if t == "unary_expression":
            return self._unary(node, fr)
        if t == "ternary_expression":
            return self._ternary(node, fr)
        if t == "assignment_expression":
            return self._assign(node, fr)
        if t == "update_expression":
            return self._update(node, fr)
        if t == "method_invocation":
            return self._call(node, fr)
        if t == "object_creation_expression":
            return self._new(node, fr)
        if t == "field_access":
            return self._field(node, fr)
        if t == "array_access":
            arr = self.eval(node.child_by_field_name("array"), fr)
            idx = self.eval(node.child_by_field_name("index"), fr)
            if isinstance(arr, ListV):
                if isinstance(idx, Const) and isinstance(idx.value, int) and 0 <= idx.value < len(arr.items) and not arr.dirty:
                    return arr.items[idx.value]
                return join_all(arr.items + [UNKNOWN]) if arr.items else UNKNOWN
            return self._combine(arr)
        if t == "array_creation_expression":
            init = node.child_by_field_name("value")
            if init is not None:
                return self._array_init(init, fr)
            return ListV([], birth_depth=fr.branch, dirty=True)
        if t == "array_initializer":
            return self._array_init(node, fr)
        if t == "lambda_expression":
            self._run_lambda(node, fr)
            return LambdaV(node, fr.file, fr.cls, fr.this_obj, dict(fr.env), dict(fr.types))
        if t == "method_reference":
            return self._method_ref(node, fr)
        if t in ("instanceof_expression", "switch_expression_value"):
            return UNKNOWN
        if t == "switch_expression":
            return self._switch_expr(node, fr)
        return UNKNOWN

    def _array_init(self, node: Node, fr: Frame) -> Val:
        return ListV([self.eval(c, fr) for c in node.named_children], birth_depth=fr.branch)

    def make_this(self, cls: ClassInfo, jf: JavaFile) -> Obj:
        """`this` for an entry point: the object as a constructor would have left it (fields set by the constructor)."""
        obj = Obj(cls=cls, type_name=cls.name)
        if cls.ctors:
            fr = Frame(file=jf, cls=cls, this_obj=obj, depth=0)
            try:
                self._run_method(cls, cls.ctors[0], obj, [], fr, ctor=True)
            except (BudgetExceeded, RecursionError):
                pass
        return obj

    def _method_ref(self, node: Node, fr: Frame) -> Val:
        """A::staticMethod, this::method, obj::method as a value."""
        kids = [c for c in node.named_children]
        if len(kids) < 2:
            return UNKNOWN
        target, mname = kids[0], _t(kids[-1])
        if target.type == "this":
            cls, obj = fr.cls, fr.this_obj
        else:
            cname = self._class_ref(target, fr) if target.type in ("identifier", "field_access") else None
            if cname is not None:
                cls, obj = self.index.resolve_class(cname.split(".")[-1], fr.file), None
            else:
                v = self.eval(target, fr)
                cls, obj = (v.cls, v) if isinstance(v, Obj) and v.cls is not None else (None, None)
        if cls is None:
            return UNKNOWN
        owner = self._find_method_any(cls, mname)
        return FuncV(owner[1][0], owner[0].file, owner[0], obj, name=mname) if owner is not None else UNKNOWN

    def _find_method_any(self, cls: ClassInfo, name: str):
        seen: set[int] = set()
        cur: ClassInfo | None = cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if cur.methods.get(name):
                return cur, cur.methods[name]
            cur = self.index.resolve_class(cur.superclass, cur.file) if cur.superclass else None
        return None

    def _call_callable(self, f: Val, args: list[Val], fr: Frame) -> Val:
        if isinstance(f, FuncV):
            owner = self._find_method_any(f.cls, f.name)
            if owner is None:
                return self._opaque(None, args)
            return self._user_call(owner[0], owner[1], f.self_obj, args, fr)
        if isinstance(f, LambdaV):
            if fr.depth + 1 > self.MAX_DEPTH or id(f.node) in self._active:
                return self._opaque(None, args)
            inner = Frame(f.file, f.cls, f.this_obj, fr.depth + 1, env=dict(f.env), types=dict(f.types))
            params = f.node.child_by_field_name("parameters")
            names: list[str] = []
            if params is not None:
                if params.type == "identifier":
                    names.append(_t(params))
                else:
                    for c in params.named_children:
                        nm = c if c.type == "identifier" else c.child_by_field_name("name")
                        if nm is not None:
                            names.append(_t(nm))
            for i, nm in enumerate(names):
                inner.env[nm] = args[i] if i < len(args) else UNKNOWN
            self._active.add(id(f.node))
            try:
                body = f.node.child_by_field_name("body")
                if body is not None and body.type == "block":
                    self.exec(body, inner)
                    return join_all(inner.rets) if inner.rets else Const(None)
                return self.eval(body, inner) if body is not None else UNKNOWN
            finally:
                self._active.discard(id(f.node))
        return self._opaque(None, args)

    @staticmethod
    def _element_of(recv: Val) -> Val | None:
        if isinstance(recv, ListV) and recv.items:
            return join_all(recv.items)
        if classify(recv) == "tainted":
            return Tainted("element")
        return None

    def _run_lambda(self, node: Node, fr: Frame) -> None:
        """Run a lambda body where it is written (a Thread/executor/stream callback): what it does with captured request data
        counts. Its own parameters are unknown, and nothing it writes or returns leaks into the enclosing method."""
        if fr.depth > self.MAX_DEPTH or id(node) in self._active:
            return
        params = node.child_by_field_name("parameters")
        names: list[str] = []
        if params is not None:
            if params.type == "identifier":
                names.append(_t(params))
            else:
                for c in params.named_children:
                    nm = c if c.type == "identifier" else c.child_by_field_name("name")
                    if nm is not None:
                        names.append(_t(nm))
        saved_env, saved_types, saved_rets, saved_branch = dict(fr.env), dict(fr.types), len(fr.rets), fr.branch
        for nm in names:
            fr.env[nm] = UNKNOWN
        fr.branch += 1
        self._active.add(id(node))
        try:
            body = node.child_by_field_name("body")
            if body is not None and body.type == "block":
                self.exec(body, fr)
            else:
                self.eval(body, fr)
        finally:
            self._active.discard(id(node))
            fr.branch = saved_branch
            fr.env, fr.types = saved_env, saved_types
            del fr.rets[saved_rets:]

    def _lookup(self, name: str, fr: Frame) -> Val:
        if name in fr.env:
            return fr.env[name]
        if fr.this_obj is not None and name in fr.this_obj.fields:
            return fr.this_obj.fields[name]
        cls = fr.cls
        while cls is not None:
            if name in cls.static_finals:
                return self._static_init_value(cls, name, fr)
            cls = cls.outer
        lazy = self._lazy_field(name, fr)
        return lazy if lazy is not None else UNKNOWN

    def _lazy_field(self, name: str, fr: Frame) -> Val | None:
        """An instance field with an initializer (`private Db db = new Db();`) that an entry point reads before anything wrote it."""
        obj, cls = fr.this_obj, fr.cls
        init = cls.field_inits.get(name) if cls is not None else None
        if obj is None or init is None or id(init) in self._active or fr.depth > self.MAX_DEPTH:
            return None
        self._active.add(id(init))
        try:
            val = self.eval(init, Frame(cls.file, cls, obj, fr.depth + 1))
        finally:
            self._active.discard(id(init))
        obj.fields[name] = val
        return val

    def _field(self, node: Node, fr: Frame) -> Val:
        obj_node = node.child_by_field_name("object")
        fname = _t(node.child_by_field_name("field"))
        if obj_node is not None and obj_node.type == "this":
            if fr.this_obj is not None and fname in fr.this_obj.fields:
                return fr.this_obj.fields[fname]
            lazy = self._lazy_field(fname, fr)
            return lazy if lazy is not None else UNKNOWN
        cname = self._class_ref(obj_node, fr) if obj_node is not None else None
        if cname is not None:
            return self._static_field(cname, fname, fr)
        base = self.eval(obj_node, fr) if obj_node is not None and obj_node.type != "identifier" else None
        if obj_node is not None and obj_node.type == "identifier" and _t(obj_node) in fr.env:
            base = fr.env[_t(obj_node)]
        if isinstance(base, Obj) and fname in base.fields:
            return base.fields[fname]
        if fname == "length" and isinstance(base, ListV):
            return Const(len(base.items)) if not base.dirty else SAFE
        if base is not None and classify(base) == "tainted":
            return Tainted("field")
        return UNKNOWN

    def _static_init_value(self, cls: ClassInfo, fname: str, fr: Frame) -> Val:
        """Value of a `static final` field: its initializer, but only when that evaluates to something
        safe (a static initializer cannot see a request, so a safe result is a real constant)."""
        init = cls.field_inits.get(fname)
        if init is None or id(init) in self._active or fr.depth > self.MAX_DEPTH:
            return UNKNOWN
        self._active.add(id(init))
        try:
            val = self.eval(init, Frame(cls.file, cls, None, fr.depth + 1))
        finally:
            self._active.discard(id(init))
        return val if classify(val) == "safe" else UNKNOWN

    def _static_field(self, cname: str, fname: str, fr: Frame) -> Val:
        cls = self.index.resolve_class(cname, fr.file)
        if cls is not None:
            if fname in cls.static_finals:
                return self._static_init_value(cls, fname, fr)
            return UNKNOWN
        # A constant of a library class (Statement.RETURN_GENERATED_KEYS, File.separator).
        return SAFE if fname.isupper() or fname in ("separator", "pathSeparator", "separatorChar") else UNKNOWN

    def _binary(self, node: Node, fr: Frame) -> Val:
        op = _t(node.child_by_field_name("operator"))
        left_n, right_n = node.child_by_field_name("left"), node.child_by_field_name("right")
        a = self.eval(left_n, fr)
        if op in ("&&", "||") and isinstance(a, Const) and isinstance(a.value, bool):
            if op == "&&" and a.value is False:
                self._mark_dead(fr, right_n)
                return a
            if op == "||" and a.value is True:
                self._mark_dead(fr, right_n)
                return a
            return self.eval(right_n, fr)
        b = self.eval(right_n, fr)
        if isinstance(a, Const) and isinstance(b, Const):
            return self._fold(op, a.value, b.value)
        return self._combine(a, b)

    @staticmethod
    def _fold(op: str, x, y) -> Val:
        try:
            if op == "+" and (isinstance(x, str) or isinstance(y, str)):
                return Const(_to_str(x) + _to_str(y))
            xi = ord(x) if isinstance(x, Char) and isinstance(y, int) and not isinstance(y, bool) else x
            yi = ord(y) if isinstance(y, Char) and isinstance(x, int) and not isinstance(x, bool) else y
            num = (int, float)
            if isinstance(xi, num) and isinstance(yi, num) and not isinstance(xi, bool) and not isinstance(yi, bool):
                if op == "+": return Const(xi + yi)
                if op == "-": return Const(xi - yi)
                if op == "*": return Const(xi * yi)
                if op == "/":
                    if yi == 0:
                        return UNKNOWN
                    return Const(_java_int_div(xi, yi) if isinstance(xi, int) and isinstance(yi, int) else xi / yi)
                if op == "%" and isinstance(xi, int) and isinstance(yi, int) and yi != 0:
                    return Const(xi - yi * _java_int_div(xi, yi))
                if op == "<": return Const(xi < yi)
                if op == ">": return Const(xi > yi)
                if op == "<=": return Const(xi <= yi)
                if op == ">=": return Const(xi >= yi)
                if op == "==": return Const(xi == yi)
                if op == "!=": return Const(xi != yi)
            if isinstance(x, bool) and isinstance(y, bool):
                if op == "&&" or op == "&": return Const(x and y)
                if op == "||" or op == "|": return Const(x or y)
                if op == "==": return Const(x == y)
                if op == "!=": return Const(x != y)
                if op == "^": return Const(x != y)
            if isinstance(x, Char) and isinstance(y, Char):
                if op == "==": return Const(str(x) == str(y))
                if op == "!=": return Const(str(x) != str(y))
        except (TypeError, ValueError):
            pass
        return SAFE

    def _unary(self, node: Node, fr: Frame) -> Val:
        op = _t(node.child_by_field_name("operator")) if node.child_by_field_name("operator") else ""
        operand = self.eval(node.child_by_field_name("operand"), fr)
        if isinstance(operand, Const):
            if op == "!" and isinstance(operand.value, bool):
                return Const(not operand.value)
            if op == "-" and isinstance(operand.value, (int, float)) and not isinstance(operand.value, bool):
                return Const(-operand.value)
            if op == "+":
                return operand
        return self._combine(operand)

    def _ternary(self, node: Node, fr: Frame) -> Val:
        cond = self.eval(node.child_by_field_name("condition"), fr)
        cons_n, alt_n = node.child_by_field_name("consequence"), node.child_by_field_name("alternative")
        if isinstance(cond, Const) and isinstance(cond.value, bool):
            if cond.value:
                self._mark_dead(fr, alt_n)
                return self.eval(cons_n, fr)
            self._mark_dead(fr, cons_n)
            return self.eval(alt_n, fr)
        fr.branch += 1
        try:
            return join(self.eval(cons_n, fr), self.eval(alt_n, fr))
        finally:
            fr.branch -= 1

    def _assign(self, node: Node, fr: Frame) -> Val:
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        op = _t(node.child_by_field_name("operator")) or "="
        rhs = self.eval(right, fr)
        if op != "=":
            cur = self.eval(left, fr)
            bop = op[:-1]
            rhs = self._fold(bop, cur.value, rhs.value) if isinstance(cur, Const) and isinstance(rhs, Const) else self._combine(cur, rhs)
        self._store(left, rhs, fr)
        return rhs

    def _store(self, target: Node, val: Val, fr: Frame) -> None:
        if target.type == "identifier":
            name = _t(target)
            is_field = fr.this_obj is not None and (name in fr.this_obj.fields or (name not in fr.env and fr.cls is not None and name in fr.cls.field_inits))
            if name in fr.env or not is_field:
                fr.env[name] = self._guard(fr, name, val)
            else:
                fr.this_obj.fields[name] = self._guard_field(fr, fr.this_obj.fields.get(name), val)
        elif target.type == "field_access":
            obj_n = target.child_by_field_name("object")
            fname = _t(target.child_by_field_name("field"))
            if obj_n is not None and obj_n.type == "this" and fr.this_obj is not None:
                fr.this_obj.fields[fname] = self._guard_field(fr, fr.this_obj.fields.get(fname), val)
            else:
                base = self.eval(obj_n, fr)
                if isinstance(base, Obj):
                    base.fields[fname] = self._guard_field(fr, base.fields.get(fname), val)
        elif target.type == "array_access":
            arr = self.eval(target.child_by_field_name("array"), fr)
            idx = self.eval(target.child_by_field_name("index"), fr)
            if isinstance(arr, ListV):
                if isinstance(idx, Const) and isinstance(idx.value, int) and 0 <= idx.value < len(arr.items) and fr.branch <= arr.birth_depth:
                    arr.items[idx.value] = val
                else:
                    arr.items.append(val)
                    arr.dirty = True

    def _guard(self, fr: Frame, name: str, val: Val) -> Val:
        return val

    def _guard_field(self, fr: Frame, old: Val | None, val: Val) -> Val:
        # A field written under a condition that may not hold keeps its old value as an option.
        if fr.branch > 0 and old is not None:
            return join(old, val)
        return val

    def _update(self, node: Node, fr: Frame) -> Val:
        target = node.named_children[0] if node.named_children else None
        if target is not None and target.type == "identifier":
            cur = fr.env.get(_t(target))
            delta = 1 if "++" in _t(node) else -1
            if isinstance(cur, Const) and isinstance(cur.value, int) and not isinstance(cur.value, bool):
                fr.env[_t(target)] = Const(cur.value + delta)
                return cur
            fr.env[_t(target)] = self._combine(cur) if cur is not None else UNKNOWN
        return UNKNOWN

    # ------------------------------------------------------------------ calls
    def _args(self, node: Node | None, fr: Frame) -> list[Val]:
        if node is None:
            return []
        return [self.eval(c, fr) for c in node.named_children]

    def _call(self, node: Node, fr: Frame) -> Val:
        name = _t(node.child_by_field_name("name"))
        obj_n = node.child_by_field_name("object")
        args_n = node.child_by_field_name("arguments")
        if self._sanitizer(name, _t(obj_n) if obj_n is not None else ""):
            self._args(args_n, fr)
            if obj_n is not None:
                self.eval(obj_n, fr)
            return SAFE
        # A freshly generated unguessable name: `Generators.timeBasedGenerator().generate()`, `UUID.randomUUID()`.
        if name == "generate" and obj_n is not None and _t(obj_n).startswith("Generators."):
            return Const(RANDOM_MARK)
        # Static call on a class name?
        if obj_n is not None:
            cname = self._class_ref(obj_n, fr)
            if cname is not None:
                args = self._args(args_n, fr)
                self._hook(node, name, cname, None, _t(obj_n), args, fr)
                return self._static_call(cname, name, args, fr, node)
        if obj_n is None:
            args = self._args(args_n, fr)
            return self._unqualified(name, args, fr)
        recv = self.eval(obj_n, fr)
        args = self._args(args_n, fr)
        rtype = self._recv_type(obj_n, recv, fr)
        self._hook(node, name, None, rtype, _t(obj_n), args, fr, recv)
        if name in ("getName", "getFileName") and not args and rtype in ARCHIVE_ENTRY_TYPES:
            return Tainted("archive.entry")  # an entry name inside an uploaded or downloaded archive: zip slip
        if name in ("getNextEntry", "getNextJarEntry", "getNextZipEntry", "getNextTarEntry") and not args:
            return Obj(type_name="ZipEntry")
        return self._invoke(recv, name, args, fr, node)

    def _hook(self, node, name, cname, recv_type, recv_text, args, fr, recv=None) -> None:
        if self.sink_hook is not None:
            self.sink_hook(node, name, cname, recv_type, recv_text, args, fr, recv)

    def _recv_type(self, obj_n: Node, recv: Val, fr: Frame) -> str | None:
        """Best-known type name of a receiver: modeled objects first, then the declared local type."""
        if isinstance(recv, Choice):
            names = {a.type_name for a in recv.alts if isinstance(a, Obj) and a.type_name}
            if len(names) == 1:
                return names.pop()
        if isinstance(recv, Obj) and recv.type_name:
            return recv.type_name
        if obj_n is not None and obj_n.type == "identifier":
            local = fr.types.get(_t(obj_n))
            if local is not None:
                return local
            cls = fr.cls
            while cls is not None:  # a field: `private UserMapper userMapper;`
                if _t(obj_n) in cls.field_types:
                    return cls.field_types[_t(obj_n)]
                cls = cls.outer
        if obj_n is not None and obj_n.type == "field_access" and _t(obj_n.child_by_field_name("object")) == "this" and fr.cls is not None:
            return fr.cls.field_types.get(_t(obj_n.child_by_field_name("field")))
        return None

    def _class_ref(self, obj_n: Node, fr: Frame) -> str | None:
        """The class name when `obj_n` names a class (Integer, a.b.ThingFactory), not a variable."""
        if obj_n.type == "identifier":
            name = _t(obj_n)
            if name in fr.env or (fr.this_obj and name in fr.this_obj.fields):
                return None
            if name[:1].isupper():
                if name.isupper() and len(name) > 1 and name not in ("UUID", "URL", "URI", "URN", "XML", "JSON", "SQL", "DOM", "SAX", "ZIP") and self.index.resolve_class(name, fr.file) is None:
                    return None  # an ALL_CAPS constant, not a class
                return name
            return None
        if obj_n.type == "field_access":
            fname = _t(obj_n.child_by_field_name("field"))
            inner = obj_n.child_by_field_name("object")
            if fname.isupper() and len(fname) > 1 and inner is not None and _t(inner).split(".")[-1][:1].isupper() and self.index.resolve_class(fname, fr.file) is None:
                return None  # Utils.TESTFILES_DIR: a constant of a class, not a class (java.util.UUID: a class of a package)
            if fname[:1].isupper() and inner is not None and inner.type in ("identifier", "field_access"):
                first = _t(inner).split(".")[0]
                if first in fr.env or (fr.this_obj and first in fr.this_obj.fields):
                    return None
                return fname
        return None

    def _static_call(self, cname: str, name: str, args: list[Val], fr: Frame, node: Node) -> Val:
        if (cname, name) in PARSERS:
            return SAFE
        if cname == "UUID" and name == "randomUUID" and not args:
            return Const(RANDOM_MARK)
        cls = self.index.resolve_class(cname, fr.file)
        if cls is not None and name in cls.methods:
            return self._user_call(cls, cls.methods[name], None, args, fr)
        if cname == "String" and name in ("valueOf", "copyValueOf", "format", "join"):
            return self._combine(*args)
        if cname in ("Arrays", "List", "Set") and name in ("asList", "of", "copyOf"):
            return ListV(list(args), birth_depth=fr.branch)
        return self._opaque(None, args, pure=cname in PURE_STATIC_CLASSES or (cname, name) in PURE_STATIC_METHODS)

    def _unqualified(self, name: str, args: list[Val], fr: Frame) -> Val:
        cls = fr.cls
        while cls is not None:
            if name in cls.methods:
                return self._user_call(cls, cls.methods[name], fr.this_obj, args, fr)
            sup = self.index.resolve_class(cls.superclass, cls.file) if cls.superclass else None
            seen = {id(cls)}  # `class Foo extends other.Foo` resolves to itself by simple name: stop, do not spin
            while sup is not None and id(sup) not in seen:
                seen.add(id(sup))
                if name in sup.methods:
                    return self._user_call(sup, sup.methods[name], fr.this_obj, args, fr)
                sup = self.index.resolve_class(sup.superclass, sup.file) if sup.superclass else None
            cls = cls.outer
        return self._opaque(None, args)

    @staticmethod
    def _opaque(recv: Val | None, args: list[Val], pure: bool = False) -> Val:
        """An unmodeled call: taint passes through; safe stays safe only for allowlisted pure calls
        or when called on a value that is itself safe and given only safe arguments."""
        vals = ([recv] if recv is not None else []) + list(args)
        if not pure:
            for v in vals:
                if isinstance(v, (ListV, MapV, BuilderV)):
                    v.opaque = True  # the callee may add anything to a container it is handed
        classes = [classify(v) for v in vals]
        if "tainted" in classes:
            if pure:  # `new File(path)`: the same text, so what it was known to start with still holds
                first = next((v for v in vals if isinstance(v, Tainted)), None)
                if first is not None and (first.prefix or first.lead):
                    return Tainted("through-call", 0, first.prefix, first.lead)
            return Tainted("through-call")
        if all(c == "safe" for c in classes) and (pure or recv is not None):
            return SAFE
        return UNKNOWN

    def _invoke(self, recv: Val, name: str, args: list[Val], fr: Frame, node: Node) -> Val:
        if isinstance(recv, Choice):
            return join_all(self._invoke(a, name, args, fr, node) for a in recv.alts)
        if isinstance(recv, (LambdaV, FuncV)) and name not in _FN_OTHER:
            return self._call_callable(recv, args, fr)
        if name in _ELEMENT_CALLBACKS and args and isinstance(args[-1], (LambdaV, FuncV)):
            held = self._element_of(recv)
            if held is not None:
                self._call_callable(args[-1], [held], fr)
        if isinstance(recv, Obj):
            if recv.kind == "request":
                if name in REQUEST_SOURCES:
                    return Tainted("request." + name)
                if name == "getSession":
                    return Obj(type_name="HttpSession")
                return UNKNOWN
            modeled = MODELED_FACTORIES.get((recv.type_name, name))
            if modeled is not None:
                return Obj(type_name=modeled)
            if recv.cls is not None:
                m = self._find_method(recv.cls, name, len(args))
                if m is not None:
                    owner, methods = m
                    return self._user_call(owner, methods, recv, args, fr)
            impls = self._maybe_impls(recv)
            if impls:
                outs = []
                for c in impls:
                    m = self._find_method(c, name, len(args))
                    outs.append(self._user_call(m[0], m[1], Obj(cls=c, type_name=c.name), args, fr) if m else UNKNOWN)
                return join_all(outs)
            return self._opaque(recv, args)
        if isinstance(recv, ListV):
            return self._list_call(recv, name, args, fr)
        if isinstance(recv, MapV):
            return self._map_call(recv, name, args, fr)
        if isinstance(recv, BuilderV):
            return self._builder_call(recv, name, args, fr)
        if isinstance(recv, Const) and isinstance(recv.value, str):
            return self._string_call(recv, name, args)
        if name in BENIGN_RESULT_METHODS:
            return SAFE
        if name == "getName" and isinstance(recv, Tainted):
            return Tainted("through-call")
        return self._opaque(recv, args)

    def _maybe_impls(self, recv: Obj) -> list[ClassInfo]:
        if recv.type_name and recv.cls is None:
            impls = self.index.implementers(recv.type_name, None)
            return impls[:8]
        return []

    def _find_method(self, cls: ClassInfo, name: str, arity: int):
        seen: set[int] = set()
        cur: ClassInfo | None = cls
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            ms = [m for m in cur.methods.get(name, []) if self._arity(m) == arity]
            if ms:
                return cur, ms
            cur = self.index.resolve_class(cur.superclass, cur.file) if cur.superclass else None
        return None

    @staticmethod
    def _arity(method: Node) -> int:
        params = method.child_by_field_name("parameters")
        return len(params.named_children) if params is not None else 0

    def _string_call(self, recv: Const, name: str, args: list[Val]) -> Val:
        s = recv.value
        consts = all(isinstance(a, Const) for a in args)
        if name == "charAt" and consts and isinstance(args[0].value, int):
            return Const(Char(s[args[0].value])) if 0 <= args[0].value < len(s) else UNKNOWN
        if name == "length":
            return Const(len(s))
        if name in ("toUpperCase", "toLowerCase", "trim", "strip", "intern", "toString"):
            return Const({"toUpperCase": s.upper, "toLowerCase": s.lower, "trim": s.strip, "strip": s.strip}.get(name, lambda: s)())
        if name == "isEmpty":
            return Const(len(s) == 0)
        if consts and name in ("equals", "equalsIgnoreCase", "contains", "startsWith", "endsWith", "indexOf") and args:
            a = _to_str(args[0].value)
            return Const({"equals": s == a, "equalsIgnoreCase": s.lower() == a.lower(), "contains": a in s,
                          "startsWith": s.startswith(a), "endsWith": s.endswith(a), "indexOf": s.find(a)}[name])
        if consts and name == "substring" and args and isinstance(args[0].value, int):
            try:
                return Const(s[args[0].value:args[1].value] if len(args) > 1 else s[args[0].value:])
            except (IndexError, TypeError):
                return UNKNOWN
        if consts and name == "concat" and args:
            return Const(s + _to_str(args[0].value))
        if consts and name == "replace" and len(args) == 2:
            return Const(s.replace(_to_str(args[0].value), _to_str(args[1].value)))
        if name in BENIGN_RESULT_METHODS:
            return SAFE
        return self._combine(recv, *args) if not consts else SAFE

    @staticmethod
    def _untrusted_read(container) -> Val:
        """What a read of a container returns after an unmodeled call may have changed it."""
        held = getattr(container, "items", None)
        if held is None:
            held = getattr(container, "entries", {}).values() if hasattr(container, "entries") else getattr(container, "parts", [])
        return join_all(list(held) + [UNKNOWN])

    def _list_call(self, lst: ListV, name: str, args: list[Val], fr: Frame) -> Val:
        if lst.opaque and name not in ("add", "addAll", "clear", "set", "remove", "removeElementAt", "insertElementAt"):
            return self._untrusted_read(lst)
        idx = args[0].value if args and isinstance(args[0], Const) and isinstance(args[0].value, int) and not isinstance(args[0].value, bool) else None
        if name in ("add", "addLast", "push", "offer", "addElement") and len(args) == 1:
            self._mutating(fr, lst)
            lst.items.append(args[0])
            return Const(True)
        if name in ("add", "insertElementAt") and len(args) == 2:
            self._mutating(fr, lst)
            if idx is not None and 0 <= idx <= len(lst.items):
                lst.items.insert(idx, args[1])
            else:
                lst.items.append(args[1])
                lst.dirty = True
            return Const(None)
        if name == "addAll" and args:
            self._mutating(fr, lst)
            if isinstance(args[-1], ListV):
                lst.items.extend(args[-1].items)
            else:
                lst.items.append(args[-1])
                lst.dirty = True
            return Const(True)
        if name in ("remove", "removeElementAt") and len(args) == 1:
            self._mutating(fr, lst)
            if idx is not None and 0 <= idx < len(lst.items) and not lst.dirty:
                return lst.items.pop(idx)
            if isinstance(args[0], Const) and isinstance(args[0].value, str):
                for i, item in enumerate(lst.items):
                    if item == args[0]:
                        lst.items.pop(i)
                        return Const(True)
            lst.dirty = True
            return UNKNOWN
        if name in ("get", "elementAt") and len(args) == 1:
            if idx is not None and 0 <= idx < len(lst.items) and not lst.dirty:
                return lst.items[idx]
            return join_all(lst.items + [UNKNOWN]) if lst.items else UNKNOWN
        if name == "set" and len(args) == 2:
            self._mutating(fr, lst)
            if idx is not None and 0 <= idx < len(lst.items) and not lst.dirty:
                old = lst.items[idx]
                lst.items[idx] = args[1]
                return old
            lst.items.append(args[1])
            lst.dirty = True
            return UNKNOWN
        if name in ("size",) and not lst.dirty:
            return Const(len(lst.items))
        if name == "isEmpty" and not lst.dirty:
            return Const(len(lst.items) == 0)
        if name == "clear":
            self._mutating(fr, lst)
            lst.items.clear()
            return Const(None)
        if name in ("contains", "indexOf") or name in BENIGN_RESULT_METHODS:
            return SAFE
        if name in ("peek", "pop", "getFirst", "getLast", "firstElement", "lastElement", "poll", "element"):
            self._mutating(fr, lst)
            lst.dirty = lst.dirty or name in ("pop", "poll")
            return join_all(lst.items + [UNKNOWN]) if lst.items else UNKNOWN
        return self._opaque(lst, args)

    def _map_call(self, m: MapV, name: str, args: list[Val], fr: Frame) -> Val:
        if m.opaque and name not in ("put", "putAll", "remove"):
            return self._untrusted_read(m)
        key = args[0].value if args and isinstance(args[0], Const) and isinstance(args[0].value, (str, int)) else None
        if name == "put" and len(args) == 2:
            self._mutating(fr, m)
            if key is not None:
                old = m.entries.get(key, Const(None))
                m.entries[key] = args[1]
                return old
            m.entries[("*", id(args[0]))] = args[1]
            m.dirty = True
            return UNKNOWN
        if name in ("get", "getOrDefault") and args:
            if key is not None and not m.dirty:
                if key in m.entries:
                    return m.entries[key]
                return args[1] if name == "getOrDefault" and len(args) > 1 else Const(None)
            return join_all(list(m.entries.values()) + [UNKNOWN])
        if name in ("containsKey", "size", "isEmpty", "containsValue"):
            return SAFE
        if name == "remove" and args:
            self._mutating(fr, m)
            if key is not None and not m.dirty:
                return m.entries.pop(key, Const(None))
            m.dirty = True
            return UNKNOWN
        if name == "putAll":
            self._mutating(fr, m)
            m.dirty = True
            return Const(None)
        return self._opaque(m, args)

    def _builder_call(self, b: BuilderV, name: str, args: list[Val], fr: Frame) -> Val:
        if b.opaque and name == "toString":
            return self._untrusted_read(b)
        if name == "append" and args:
            self._mutating(fr, b)
            b.parts.append(args[0])
            return b
        if name in ("toString",):
            return self._concat_parts(b)
        if name in ("length",):
            return SAFE
        if name in ("insert", "replace", "setCharAt", "setLength", "delete", "deleteCharAt", "reverse", "appendCodePoint"):
            self._mutating(fr, b)
            b.parts.extend(a for a in args if not (isinstance(a, Const) and isinstance(a.value, int) and not isinstance(a.value, bool)))
            b.dirty = True
            return b
        return self._opaque(b, args)

    def _concat_parts(self, b: BuilderV) -> Val:
        if b.dirty or b.opaque:
            return self._combine(*b.parts, UNKNOWN)
        if all(isinstance(p, Const) for p in b.parts):
            return Const("".join(_to_str(p.value) for p in b.parts))
        return self._combine(*b.parts)

    # ------------------------------------------------------------------ object creation
    def _new(self, node: Node, fr: Frame) -> Val:
        tname = simple_type_name(node.child_by_field_name("type"))
        args = self._args(node.child_by_field_name("arguments"), fr)
        self._hook(node, "<init>", tname, None, "", args, fr)
        if node.child_by_field_name("body") is not None or any(c.type == "class_body" for c in node.children):
            return self._opaque(None, args)
        if tname in LIST_TYPES:
            items = list(args[0].items) if args and isinstance(args[0], ListV) else []
            lst = ListV(items, birth_depth=fr.branch)
            if args and not isinstance(args[0], (ListV, Const)):
                lst.dirty = True
                lst.items.append(args[0])
            return lst
        if tname in MAP_TYPES:
            m = MapV({}, birth_depth=fr.branch)
            if args and isinstance(args[0], MapV):
                m.entries = dict(args[0].entries)
            elif args and not isinstance(args[0], Const):
                m.dirty = True
            return m
        if tname in BUILDER_TYPES:
            return BuilderV(list(args[:1]) if args and not (isinstance(args[0], Const) and isinstance(args[0].value, int)) else [], birth_depth=fr.branch)
        if tname == "String" and args:
            return args[0] if len(args) == 1 else self._combine(*args)
        cls = self.index.resolve_class(tname, fr.file)
        if cls is None:
            return self._opaque(None, args, pure=tname in PURE_CTOR_CLASSES)
        obj = Obj(cls=cls, type_name=cls.name)
        for fname, init in cls.field_inits.items():
            if init is not None and "static" not in _t(cls.node):
                obj.fields[fname] = self.eval(init, Frame(cls.file, cls, obj, fr.depth + 1))
        ctors = [c for c in cls.ctors if self._arity(c) == len(args)]
        if ctors:
            self._run_method(cls, ctors[0], obj, args, fr, ctor=True)
        return obj

    # ------------------------------------------------------------------ user-defined calls
    def _user_call(self, cls: ClassInfo, methods: list[Node], recv: Obj | None, args: list[Val], fr: Frame) -> Val:
        m = next((x for x in methods if self._arity(x) == len(args)), None)
        if m is None:
            return self._opaque(None, args)
        return self._run_method(cls, m, recv, args, fr)

    def _run_method(self, cls: ClassInfo, method: Node, recv: Obj | None, args: list[Val], fr: Frame, ctor: bool = False) -> Val:
        if fr.depth + 1 > self.MAX_DEPTH or id(method) in self._active:
            return self._opaque(recv, args)
        body = method.child_by_field_name("body")
        if body is None:
            return self._opaque(recv, args)
        inner = Frame(cls.file, cls, recv, fr.depth + 1)
        self._bind_params(method, args, inner)
        self._active.add(id(method))
        try:
            self.exec(body, inner)
        finally:
            self._active.discard(id(method))
        if ctor:
            return UNKNOWN
        return join_all(inner.rets) if inner.rets else Const(None)

    def _bind_params(self, method: Node, args: list[Val], inner: Frame) -> None:
        params = method.child_by_field_name("parameters")
        if params is None:
            return
        for p, a in zip(params.named_children, args + [UNKNOWN] * len(params.named_children)):
            pname = _t(p.child_by_field_name("name"))
            if not pname:
                continue
            inner.env[pname] = a
            inner.types[pname] = simple_type_name(p.child_by_field_name("type"))

    def bind_entry_params(self, method: Node, fr: Frame) -> None:
        """Parameters of the method under analysis: request objects are sources, the rest unknown."""
        params = method.child_by_field_name("parameters")
        if params is None:
            return
        for p in params.named_children:
            pname = _t(p.child_by_field_name("name"))
            tname = simple_type_name(p.child_by_field_name("type"))
            if not pname:
                continue
            fr.types[pname] = tname
            annotations = {_t(c.child_by_field_name("name")) for m in p.children if m.type == "modifiers" for c in m.children
                           if c.type in ("annotation", "marker_annotation")}
            if tname in REQUEST_TYPES:
                fr.env[pname] = Obj(kind="request", type_name=tname)
            elif self.annotated_sources and annotations & ANNOTATED_SOURCE_PARAMS:
                fr.env[pname] = Tainted("annotated-param")
            else:
                fr.env[pname] = Obj(type_name=tname) if tname[:1].isupper() and tname not in ("String", "Object") else UNKNOWN

    # ------------------------------------------------------------------ statements
    def exec(self, node: Node | None, fr: Frame) -> bool:
        """Run a statement. Returns True when control cannot continue past it (return or throw)."""
        if node is None:
            return False
        self._tick()
        t = node.type
        if t == "block":
            for stmt in node.named_children:
                if self.exec(stmt, fr):
                    return True
            return False
        if t == "local_variable_declaration":
            tname = simple_type_name(node.child_by_field_name("type"))
            for decl in node.named_children:
                if decl.type != "variable_declarator":
                    continue
                name = _t(decl.child_by_field_name("name"))
                value_n = decl.child_by_field_name("value")
                fr.types[name] = tname
                fr.env[name] = self.eval(value_n, fr) if value_n is not None else UNKNOWN
            return False
        if t == "expression_statement":
            for c in node.named_children:
                self.eval(c, fr)
            return False
        if t == "return_statement":
            val = self.eval(node.named_children[0], fr) if node.named_children else Const(None)
            fr.rets.append(val)
            return True
        if t == "throw_statement":
            for c in node.named_children:
                self.eval(c, fr)
            return True
        if t == "if_statement":
            return self._if(node, fr)
        if t in ("switch_expression", "switch_statement"):
            self._switch(node, fr)
            return False
        if t in ("for_statement", "while_statement", "do_statement", "enhanced_for_statement"):
            return self._loop(node, fr)
        if t in ("try_statement", "try_with_resources_statement"):
            return self._try(node, fr)
        if t in ("labeled_statement", "synchronized_statement"):
            for c in node.named_children[1:] if t == "labeled_statement" else node.named_children[-1:]:
                if self.exec(c, fr):
                    return True
            return False
        if t in ("break_statement", "continue_statement", "empty_statement", "local_class_declaration"):
            return False
        # Anything else that can contain expressions: evaluate them so probes inside are seen.
        for c in node.named_children:
            if c.type.endswith("statement") or c.type == "block":
                self.exec(c, fr)
            else:
                self.eval(c, fr)
        return False

    def _fork(self, fr: Frame) -> dict:
        return dict(fr.env)

    def _join_envs(self, fr: Frame, a: dict, b: dict) -> None:
        out = {}
        for k in set(a) | set(b):
            if k in a and k in b:
                out[k] = join(a[k], b[k])
            else:
                out[k] = join(a.get(k, b.get(k)), UNKNOWN) if False else (a[k] if k in a else b[k])
        fr.env = out

    def _if(self, node: Node, fr: Frame) -> bool:
        cond_n = node.child_by_field_name("condition")
        cons_n, alt_n = node.child_by_field_name("consequence"), node.child_by_field_name("alternative")
        cond = self.eval(cond_n, fr)
        if isinstance(cond, Const) and isinstance(cond.value, bool):
            if cond.value:
                self._mark_dead(fr, alt_n)
                return self.exec(cons_n, fr)
            self._mark_dead(fr, cons_n)
            return self.exec(alt_n, fr) if alt_n is not None else False
        base = self._fork(fr)
        facts = self._facts(cond_n, fr)
        fr.branch += 1
        try:
            self._narrow(facts[0], fr)
            term_a = self.exec(cons_n, fr)
            env_a = fr.env
            fr.env = dict(base)
            self._narrow(facts[1], fr)
            term_b = self.exec(alt_n, fr) if alt_n is not None else False
            if alt_n is None and not term_a:
                pass
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

    def _narrow(self, names: set, fr: Frame) -> None:
        for v in G.resolve(names, self.cwes):
            if v in fr.env:
                fr.env[v] = SAFE

    def _facts(self, cond: Node | None, fr: Frame) -> G.Facts:
        """What a condition proves about local variables on each outcome (see guards.py)."""
        if cond is None:
            return G.EMPTY
        t = cond.type
        if t == "parenthesized_expression":
            return self._facts(cond.named_children[0] if cond.named_children else None, fr)
        if t == "unary_expression" and _t(cond.child_by_field_name("operator")) == "!":
            return G.negate(self._facts(cond.child_by_field_name("operand"), fr))
        if t == "binary_expression":
            op = _t(cond.child_by_field_name("operator"))
            a, b = self._facts(cond.child_by_field_name("left"), fr), self._facts(cond.child_by_field_name("right"), fr)
            if op == "&&":
                return G.both(a, b)
            if op == "||":
                return G.either(a, b)
            return G.EMPTY
        if t != "method_invocation":
            return G.EMPTY
        name = _t(cond.child_by_field_name("name"))
        obj = cond.child_by_field_name("object")
        args_n = cond.child_by_field_name("arguments")
        args = list(args_n.named_children) if args_n is not None else []

        def local(n: Node | None) -> str | None:
            return _t(n) if n is not None and n.type == "identifier" and _t(n) in fr.env else None

        def const_of(n: Node | None) -> Const | None:
            if n is None:
                return None
            v = self.eval(n, fr)
            return v if isinstance(v, Const) and isinstance(v.value, str) else None

        def host_of(n: Node | None) -> str | None:
            """The local variable whose URL host `n` reads: `u.getHost()`, `new URL(u).getHost()`, `URI.create(u).getHost()`."""
            if n is None or n.type != "method_invocation" or _t(n.child_by_field_name("name")) not in ("getHost", "getAuthority", "getHostName"):
                return None
            o = n.child_by_field_name("object")
            if local(o):
                return local(o)
            if o is not None and o.type in ("object_creation_expression", "method_invocation"):
                a = o.child_by_field_name("arguments")
                first = a.named_children[0] if a is not None and a.named_children else None
                return local(first) if first is not None else None
            return None

        if name in ("equals", "equalsIgnoreCase", "contentEquals") and len(args) == 1:
            for var_n, other in ((obj, args[0]), (args[0], obj)):
                hv = host_of(var_n)
                if hv and other is not None and classify(self.eval(other, fr)) == "safe":
                    return ({hv + "@918"}, set())
                v = local(var_n)
                if v and other is not None and classify(self.eval(other, fr)) == "safe":
                    return ({v}, set())
            return G.EMPTY
        if name == "matches" and len(args) == 1 and local(obj):
            rx = const_of(args[0])
            return ({local(obj)}, set()) if rx is not None and G.safe_regex(str(rx.value), self.cwes, implicit_anchor=True) else G.EMPTY
        if name == "matches" and obj is not None and _t(obj) == "Pattern" and len(args) == 2 and local(args[1]):
            rx = const_of(args[0])
            return ({local(args[1])}, set()) if rx is not None and G.safe_regex(str(rx.value), self.cwes, implicit_anchor=True) else G.EMPTY
        if name == "matches" and not args and obj is not None and obj.type == "method_invocation" and _t(obj.child_by_field_name("name")) in ("matcher",):
            m_args = list(obj.child_by_field_name("arguments").named_children) if obj.child_by_field_name("arguments") is not None else []
            target = local(m_args[0]) if len(m_args) == 1 else None
            rx = self._pattern_source(obj.child_by_field_name("object"), fr)
            return ({target}, set()) if target and rx is not None and G.safe_regex(rx, self.cwes, implicit_anchor=True) else G.EMPTY
        if name == "contains" and len(args) == 1 and obj is not None:
            tok = const_of(args[0])
            v = local(obj)
            if v and tok is not None and tok.value in G.PATH_TOKENS:
                return (set(), {f"{v}#{tok.value}"})
            v2 = local(args[0])
            hv = host_of(args[0])
            if v2 or hv:
                coll = self.eval(obj, fr)
                if isinstance(coll, ListV) and coll.items and not coll.dirty and not coll.opaque and all(classify(i) == "safe" for i in coll.items):
                    return ({v2} if v2 else {hv + "@918"}, set())
            return G.EMPTY
        if name == "startsWith" and len(args) == 1 and local(obj) and classify(self.eval(args[0], fr)) == "safe" and G.canonical_assignment(jf_src(fr), local(obj)) \
                and (G.prefix_has_separator(_t(args[0])) or _path_typed(obj, fr)):
            return ({local(obj) + "@canon"}, set())  # a canonical path that starts with the fixed root stays inside it
        if name == "startsWith" and len(args) == 1 and obj is not None and obj.type == "method_invocation":
            # out.getCanonicalPath().startsWith(root): the check is on the canonical form of `out`, against a root the
            # attacker does not choose (a root built from request data proves nothing)
            inner = obj
            canon = component = False
            while inner is not None and inner.type == "method_invocation":
                m = _t(inner.child_by_field_name("name"))
                canon = canon or m in ("getCanonicalPath", "getCanonicalFile", "toRealPath", "normalize")
                component = component or m in ("toRealPath", "normalize", "toPath")  # a java.nio Path: startsWith compares components
                inner = inner.child_by_field_name("object")
            v = local(inner) if inner is not None else None
            if canon and v and classify(self.eval(args[0], fr)) != "tainted" and (component or G.prefix_has_separator(_t(args[0]))):
                return ({v + "@canon"}, set())
        if name in ("isNumeric", "isDigits", "isCreatable", "isParsable", "isNumber") and len(args) == 1 and local(args[0]):
            return ({local(args[0])}, set())
        return G.EMPTY

    def _noop(self):
        return None

    def _pattern_source(self, node: Node | None, fr: Frame) -> str | None:
        """The constant regex behind `PATTERN` (a static final Pattern.compile("...")) or an inline Pattern.compile("...")."""
        if node is None:
            return None
        if node.type == "identifier":
            cls = fr.cls
            while cls is not None:
                init = cls.field_inits.get(_t(node))
                if init is not None:
                    return self._pattern_source(init, fr)
                cls = cls.outer
            return None
        if node.type == "method_invocation" and _t(node.child_by_field_name("name")) == "compile":
            args_n = node.child_by_field_name("arguments")
            first = args_n.named_children[0] if args_n is not None and args_n.named_children else None
            v = self.eval(first, fr) if first is not None else None
            return str(v.value) if isinstance(v, Const) and isinstance(v.value, str) else None
        return None

    def _case_matches(self, label: Node, sel: Val, fr: Frame) -> bool | None:
        """True/False when decidable, None when the selector or label is not a constant."""
        exprs = list(label.named_children)
        if not exprs or not isinstance(sel, Const):
            return None
        for e in exprs:
            v = self.eval(e, fr)
            if not isinstance(v, Const):
                return None
            a, b = v.value, sel.value
            if isinstance(a, Char) or isinstance(b, Char):
                a = ord(a) if isinstance(a, str) and len(a) == 1 else a
                b = ord(b) if isinstance(b, str) and len(b) == 1 else b
            if a == b:
                return True
        return False

    def _switch_groups(self, node: Node) -> list[Node]:
        block = next((c for c in node.children if c.type == "switch_block"), None)
        return [c for c in block.named_children if c.type == "switch_block_statement_group"] if block else []

    def _switch(self, node: Node, fr: Frame) -> None:
        sel_n = node.child_by_field_name("condition")
        sel = self.eval(sel_n, fr)
        groups = self._switch_groups(node)
        if not groups and any(c.type == "switch_block" and any(x.type == "switch_rule" for x in c.named_children) for c in node.children):
            # Arrow-form switch: not modeled, so anything it assigns is unknown.
            fr.env = {k: (v if False else v) for k, v in fr.env.items()}
            self._taint_all_assigned(node, fr)
            return
        info = []
        for g in groups:
            labels = [c for c in g.named_children if c.type == "switch_label"]
            stmts = [c for c in g.named_children if c.type != "switch_label"]
            is_default = any(_t(l).strip().startswith("default") for l in labels)
            info.append((labels, stmts, is_default))
        has_default = any(d for _, _, d in info)
        if isinstance(sel, Const):
            start = None
            for i, (labels, _, is_default) in enumerate(info):
                for l in labels:
                    if _t(l).strip().startswith("default"):
                        continue
                    m = self._case_matches(l, sel, fr)
                    if m:
                        start = i
                        break
                if start is not None:
                    break
            if start is None:
                start = next((i for i, (_, _, d) in enumerate(info) if d), None)
            for i, (_, stmts, _) in enumerate(info):
                if start is None or i < start:
                    for s in stmts:
                        self._mark_dead(fr, s)
            if start is None:
                return
            for i in range(start, len(info)):
                brk = False
                for s in info[i][1]:
                    if s.type == "break_statement":
                        brk = True
                        break
                    if self.exec(s, fr):
                        return
                    if any(c.type == "break_statement" for c in [s]):
                        brk = True
                if brk:
                    # statements after the matching group are dead only when it ended in break
                    for j in range(i + 1, len(info)):
                        for s in info[j][1]:
                            self._mark_dead(fr, s)
                    return
            return
        # Unknown selector: every group may run (with fallthrough), or none.
        base = self._fork(fr)
        envs = []
        fr.branch += 1
        try:
            for i in range(len(info)):
                fr.env = dict(base)
                for j in range(i, len(info)):
                    ended = False
                    for s in info[j][1]:
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

    def _taint_all_assigned(self, node: Node, fr: Frame) -> None:
        for sub in self._walk(node):
            if sub.type == "assignment_expression":
                left = sub.child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    fr.env[_t(left)] = UNKNOWN

    def _walk(self, node: Node):
        yield node
        for c in node.children:
            yield from self._walk(c)

    def _switch_expr(self, node: Node, fr: Frame) -> Val:
        self._switch(node, fr)
        return UNKNOWN

    def _loop(self, node: Node, fr: Frame) -> bool:
        t = node.type
        if t == "enhanced_for_statement":
            name = _t(node.child_by_field_name("name"))
            fr.types[name] = simple_type_name(node.child_by_field_name("type"))
            it = self.eval(node.child_by_field_name("value"), fr)
            if isinstance(it, ListV) and not it.items and not it.dirty:
                return False
            if isinstance(it, ListV):
                elem = join_all(it.items + ([UNKNOWN] if it.dirty else []))
            elif classify(it) == "tainted":
                elem = Tainted("element")
            else:
                elem = UNKNOWN
            body = node.child_by_field_name("body")
            base = self._fork(fr)
            fr.branch += 1
            try:
                fr.env[name] = elem
                self.exec(body, fr)
            finally:
                fr.branch -= 1
            self._join_envs(fr, base, fr.env)
            return False
        if t == "for_statement":
            for c in node.children_by_field_name("init"):
                self.exec(c, fr) if c.type.endswith("statement") or c.type == "local_variable_declaration" else self.eval(c, fr)
        cond_n = node.child_by_field_name("condition")
        cond = self.eval(cond_n, fr) if cond_n is not None and t != "do_statement" else UNKNOWN
        if isinstance(cond, Const) and cond.value is False:
            self._mark_dead(fr, node.child_by_field_name("body"))
            return False
        base = self._fork(fr)
        fr.branch += 1
        try:
            self.exec(node.child_by_field_name("body"), fr)
            for c in node.children_by_field_name("update"):
                self.eval(c, fr)
        finally:
            fr.branch -= 1
        if t == "do_statement":
            return False
        self._join_envs(fr, base, fr.env)
        return False

    def _try(self, node: Node, fr: Frame) -> bool:
        for res in node.children:
            if res.type == "resource_specification":
                for r in res.named_children:
                    nm = r.child_by_field_name("name")
                    val = r.child_by_field_name("value")
                    if nm is not None and val is not None:
                        fr.env[_t(nm)] = self.eval(val, fr)
        body = node.child_by_field_name("body")
        base = self._fork(fr)
        term = self.exec(body, fr)
        after_try = fr.env
        envs = [] if term else [after_try]
        all_term = term
        for c in node.named_children:
            if c.type == "catch_clause":
                fr.env = {k: join(base.get(k, v), v) if k in base else v for k, v in after_try.items()}
                cparam = next((x for x in c.named_children if x.type == "catch_formal_parameter"), None)
                if cparam is not None:
                    nm = cparam.child_by_field_name("name")
                    if nm is not None:
                        fr.env[_t(nm)] = UNKNOWN
                fr.branch += 1
                try:
                    cterm = self.exec(c.child_by_field_name("body"), fr)
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
            fr.env = after_try
        for c in node.named_children:
            if c.type == "finally_clause":
                self.exec(next((x for x in c.named_children if x.type == "block"), None), fr)
        return all_term and not envs
