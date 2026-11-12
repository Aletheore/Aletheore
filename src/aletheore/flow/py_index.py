"""A lazy repository-wide index of Python modules, functions and classes, so calls resolve across files.

`from pkg.mod import helper`, `import pkg.mod as m` and relative imports are resolved to files in the
repository. A module name that matches more than one file resolves to nothing, which the interpreter
treats as unknown rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.scanner.graph import PY_LANGUAGE

_EXTRA_SKIP = {".worktrees", ".repowise", "site-packages", "migrations"}
MAX_FILES = 40000


def _t(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


@dataclass(eq=False)
class PyClass:
    name: str
    node: Node
    file: "PyFile"
    bases: list[str] = field(default_factory=list)
    methods: dict[str, Node] = field(default_factory=dict)


@dataclass(eq=False)
class PyFile:
    path: Path
    src: bytes
    root: Node
    functions: dict[str, Node] = field(default_factory=dict)
    classes: dict[str, PyClass] = field(default_factory=dict)
    # local name -> (module dotted name or "", symbol or None, relative level)
    imports: dict[str, tuple[str, str | None, int]] = field(default_factory=dict)
    constants: dict[str, Node] = field(default_factory=dict)
    # module-level container name -> contributions: (kind, key node, value node, def node, env)
    # kind "value": evaluate `value`; "func": the function `def`; env maps registrar parameter -> argument node
    registry: dict[str, list] = field(default_factory=dict)
    registrars: dict | None = None  # name -> specs, computed on first use


def _decorated(node: Node) -> Node:
    return node.child_by_field_name("definition") if node.type == "decorated_definition" else node


def _scan(file: PyFile) -> None:
    def top(n: Node) -> None:
        for c in n.named_children:
            d = _decorated(c)
            if d is None:
                continue
            if d.type == "function_definition":
                file.functions[_t(d.child_by_field_name("name"))] = d
            elif d.type == "class_definition":
                cls = PyClass(_t(d.child_by_field_name("name")), d, file)
                sup = d.child_by_field_name("superclasses")
                if sup is not None:
                    cls.bases = [_t(x).split(".")[-1] for x in sup.named_children]
                body = d.child_by_field_name("body")
                if body is not None:
                    for m in body.named_children:
                        md = _decorated(m)
                        if md is not None and md.type == "function_definition":
                            cls.methods[_t(md.child_by_field_name("name"))] = md
                file.classes[cls.name] = cls
            elif d.type == "expression_statement":
                for a in d.named_children:
                    if a.type == "assignment":
                        left = a.child_by_field_name("left")
                        right = a.child_by_field_name("right")
                        if left is not None and left.type == "identifier" and right is not None:
                            file.constants[_t(left)] = right
            elif d.type in ("if_statement", "try_statement", "with_statement"):
                for blk in d.named_children:
                    if blk.type == "block":
                        top(blk)

    top(file.root)
    _collect_imports(file)
    _collect_registry(file)


def _collect_imports(file: PyFile) -> None:
    """Imports anywhere in the file, including inside functions. Scope is ignored: a name imported in
    one function is treated as resolvable file-wide, which only ever helps follow a call."""
    stack = [file.root]
    while stack:
        n = stack.pop()
        if n.type == "import_statement":
            for item in n.named_children:
                if item.type == "aliased_import":
                    file.imports.setdefault(_t(item.child_by_field_name("alias")), (_t(item.child_by_field_name("name")), None, 0))
                elif item.type == "dotted_name":
                    name = _t(item)
                    file.imports.setdefault(name.split(".")[0], (name.split(".")[0], None, 0))
        elif n.type == "import_from_statement":
            mod = n.child_by_field_name("module_name")
            text = _t(mod)
            level = len(text) - len(text.lstrip("."))
            dotted = text.lstrip(".")
            for item in n.named_children:
                if item is mod:
                    continue
                if item.type == "aliased_import":
                    file.imports.setdefault(_t(item.child_by_field_name("alias")), (dotted, _t(item.child_by_field_name("name")), level))
                elif item.type == "dotted_name":
                    file.imports.setdefault(_t(item), (dotted, _t(item), level))
        stack.extend(n.children)


class PyIndex:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self._resolved: dict[Path, Path] = {}  # raw path -> Path.resolve(), which walks the filesystem
        self._files: dict[Path, PyFile] = {}
        self._suffix: dict[str, list[Path]] | None = None
        self._modules: dict[tuple, PyFile | None] = {}
        self._xreg: dict[Path, dict] = {}
        self._parser = Parser(PY_LANGUAGE)

    def _build_suffix(self) -> dict[str, list[Path]]:
        out: dict[str, list[Path]] = {}
        skip = set(IGNORED_DIRS) | _EXTRA_SKIP
        n = 0
        for p in self.repo.rglob("*.py"):
            rel = p.relative_to(self.repo)
            if skip.intersection(rel.parts):
                continue
            n += 1
            if n > MAX_FILES:
                break
            parts = list(rel.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                out.setdefault(".".join(parts[i:]), []).append(p.resolve())
        return out

    def file(self, path: Path) -> PyFile | None:
        path = self._resolved.get(path) or self._resolved.setdefault(path, path.resolve())
        if path in self._files:
            return self._files[path]
        try:
            src = path.read_bytes()
        except OSError:
            return None
        pf = PyFile(path=path, src=src, root=self._parser.parse(src).root_node)
        _scan(pf)
        self._files[path] = pf
        return pf

    def cross_registry(self, file: PyFile) -> dict[str, list]:
        """Registrations other modules make through this file's registrars: `from registry import on` and then `@on('go')` elsewhere."""
        got = self._xreg.get(file.path)
        if got is not None:
            return got
        out: dict[str, list] = {}
        self._xreg[file.path] = out
        table = _registrar_table(file)
        if not table:
            return out
        needles = [n.encode() for n in table]
        if self._suffix is None:
            self._suffix = self._build_suffix()
        paths = sorted({p for ps in self._suffix.values() for p in ps})
        for p in paths:
            if p == file.path:
                continue
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if not any(nd in data for nd in needles):
                continue
            pf = self.file(p)
            if pf is None:
                continue

            def lookup(callee, pf=pf):
                if callee is None:
                    return None
                if callee.type == "identifier":
                    imp = pf.imports.get(_t(callee))
                    if imp and imp[1] and imp[1] in table and self.resolve_module(imp[0], imp[2], pf) is file:
                        return file, imp[1]
                elif callee.type == "attribute":
                    obj, nm = callee.child_by_field_name("object"), _t(callee.child_by_field_name("attribute"))
                    imp = pf.imports.get(_t(obj)) if obj is not None and obj.type == "identifier" else None
                    if imp and not imp[1] and nm in table and self.resolve_module(imp[0], imp[2], pf) is file:
                        return file, nm
                return None

            for container, entry in sites(pf, lookup):
                out.setdefault(container, []).append(entry)
        return out

    def resolve_module(self, dotted: str, level: int, from_file: PyFile) -> PyFile | None:
        # (the answer depends only on these three, and every import is resolved on every call that uses it)
        key = (dotted, level, from_file.path)
        if key not in self._modules:
            self._modules[key] = self._resolve_module(dotted, level, from_file)
        return self._modules[key]

    def _resolve_module(self, dotted: str, level: int, from_file: PyFile) -> PyFile | None:
        if level:
            base = from_file.path.parent
            for _ in range(level - 1):
                base = base.parent
            cands = [base / (dotted.replace(".", "/") + ".py"), base / dotted.replace(".", "/") / "__init__.py"] if dotted else [base / "__init__.py"]
            for c in cands:
                if c.exists():
                    return self.file(c)
            return None
        # A script-style import resolves against the importing file's own directory first, then its
        # parents, so two copies of a module in different directories (bad/ and good/) do not clash.
        rel = dotted.replace(".", "/")
        d = from_file.path.parent
        while True:
            for cand in (d / (rel + ".py"), d / rel / "__init__.py"):
                if cand.exists():
                    return self.file(cand)
            if d == self.repo or d == d.parent:
                break
            d = d.parent
        if self._suffix is None:
            self._suffix = self._build_suffix()
        paths = self._suffix.get(dotted, [])
        return self.file(paths[0]) if len(paths) == 1 else None

    def lookup(self, name: str, from_file: PyFile):
        """Resolve a bare name to ("function"|"class", node/PyClass, file) via local defs and imports."""
        if name in from_file.functions:
            return "function", from_file.functions[name], from_file
        if name in from_file.classes:
            return "class", from_file.classes[name], from_file
        imp = from_file.imports.get(name)
        if imp and imp[1]:
            mod = self.resolve_module(imp[0], imp[2], from_file)
            if mod is not None and mod is not from_file:
                if imp[1] in mod.functions:
                    return "function", mod.functions[imp[1]], mod
                if imp[1] in mod.classes:
                    return "class", mod.classes[imp[1]], mod
        return None

    def module_attr(self, base: str, attr: str, from_file: PyFile):
        """`mod.func` where `mod` is an imported module."""
        imp = from_file.imports.get(base)
        if not imp:
            return None
        dotted = imp[0] if imp[1] is None else (imp[0] + "." + imp[1] if imp[0] else imp[1])
        mod = self.resolve_module(dotted, imp[2], from_file)
        if mod is None:
            return None
        if attr in mod.functions:
            return "function", mod.functions[attr], mod
        if attr in mod.classes:
            return "class", mod.classes[attr], mod
        return None


def _params(fn: Node) -> list[str]:
    ps = fn.child_by_field_name("parameters")
    out = []
    if ps is not None:
        for p in ps.named_children:
            if p.type == "identifier":
                out.append(_t(p))
            elif p.type in ("default_parameter", "typed_parameter", "typed_default_parameter"):
                nm = p.child_by_field_name("name") or (p.named_children[0] if p.named_children else None)
                if nm is not None and nm.type == "identifier":
                    out.append(_t(nm))
    return out


def _registrar_specs(file: PyFile, name: str) -> list[tuple]:
    """What a module-level function does to module-level containers: (container, key, value param, owner),
    where key is ("param", name), ("const", node) or None, and owner is "outer" when the registered value is a
    parameter of the function itself (a plain registrar) or "inner" when it is a parameter of a function nested in
    it (a decorator factory)."""
    fn = file.functions.get(name)
    if fn is None:
        return []
    outer = _params(fn)
    specs: list[tuple] = []

    def note(container: str, key_n: Node | None, val_n: Node, scopes: list[list[str]]) -> None:
        if container not in file.constants or container in outer or val_n.type != "identifier":
            return
        v = _t(val_n)
        owner = "outer" if v in outer and len(scopes) == 1 else ("inner" if len(scopes) > 1 and v in scopes[-1] else None)
        if owner is None:
            return
        key = None
        if key_n is not None:
            if key_n.type == "identifier" and _t(key_n) in outer:
                key = ("param", _t(key_n))
            elif key_n.type == "string":
                key = ("const", key_n)
        specs.append((container, key, v, owner))

    def walk(n: Node, scopes: list[list[str]]) -> None:
        if n.type == "function_definition" and n is not fn:
            scopes = scopes + [_params(n)]
        if n.type == "call":
            f = n.child_by_field_name("function")
            a = n.child_by_field_name("arguments")
            if f is not None and f.type == "attribute" and a is not None and a.named_children:
                obj = f.child_by_field_name("object")
                meth = _t(f.child_by_field_name("attribute"))
                if obj is not None and obj.type == "identifier" and meth in ("append", "add"):
                    note(_t(obj), None, a.named_children[0], scopes)
        elif n.type == "assignment":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is not None and left.type == "subscript" and right is not None:
                base = left.child_by_field_name("value")
                idx = left.children_by_field_name("subscript")
                if base is not None and base.type == "identifier" and idx:
                    note(_t(base), idx[0], right, scopes)
        for c in n.named_children:
            walk(c, scopes)

    body = fn.child_by_field_name("body")
    if body is not None:
        walk(body, [outer])
    return specs


def _args_env(call: Node | None, params: list[str]) -> dict[str, Node]:
    env: dict[str, Node] = {}
    a = call.child_by_field_name("arguments") if call is not None else None
    if a is None:
        return env
    i = 0
    for c in a.named_children:
        if c.type == "keyword_argument":
            nm = c.child_by_field_name("name")
            vv = c.child_by_field_name("value")
            if nm is not None and vv is not None:
                env[_t(nm)] = vv
        elif i < len(params):
            env[params[i]] = c
            i += 1
    return env


def _registrar_table(file: PyFile) -> dict[str, list]:
    """name -> specs for every module-level function in the file that fills a module-level container."""
    if file.registrars is None:
        file.registrars = {n: sp for n in file.functions if (sp := _registrar_specs(file, n))}
    return file.registrars


def _collect_registry(file: PyFile) -> None:
    """Module-level statements that fill a module-level list or dict: `REG.append(f)`, `TABLE['k'] = f`,
    `register(f)` and `@register` / `@on('k')` on a function (the registrar defined in this file)."""
    def lookup(callee: Node):
        if callee is not None and callee.type == "identifier" and _t(callee) in file.functions:
            return file, _t(callee)
        return None

    for container, entry in sites(file, lookup):
        file.registry.setdefault(container, []).append(entry)
    for c in file.root.named_children:
        if c.type != "expression_statement":
            continue
        for a in c.named_children:
            if a.type == "call":
                f = a.child_by_field_name("function")
                args = a.child_by_field_name("arguments")
                if f is not None and f.type == "attribute" and args is not None and args.named_children:
                    obj = f.child_by_field_name("object")
                    if obj is not None and obj.type == "identifier" and _t(f.child_by_field_name("attribute")) in ("append", "add"):
                        file.registry.setdefault(_t(obj), []).append(("value", None, args.named_children[0], None, {}, file))
            elif a.type == "assignment":
                left, right = a.child_by_field_name("left"), a.child_by_field_name("right")
                if left is not None and left.type == "subscript" and right is not None:
                    base = left.child_by_field_name("value")
                    idx = left.children_by_field_name("subscript")
                    if base is not None and base.type == "identifier" and idx:
                        file.registry.setdefault(_t(base), []).append(("value", idx[0], right, None, {}, file))


def sites(site_file: PyFile, lookup) -> list[tuple[str, tuple]]:
    """(container, entry) for every `register(f)` call and `@register` / `@on(k)` decorator in site_file whose callee `lookup` maps to a
    registrar (reg_file, name). An entry is (kind, key, value node, def node, argument environment, the file the nodes belong to)."""
    out: list[tuple[str, tuple]] = []

    def call_site(callee: Node, call: Node, defn: Node | None, bare: bool) -> None:
        hit = lookup(callee)
        if hit is None:
            return
        reg_file, reg = hit
        outer = _params(reg_file.functions[reg])
        for container, key, vparam, owner in _registrar_table(reg_file).get(reg, []):
            if defn is None:
                if owner != "outer":
                    continue
                env = _args_env(call, outer)
                if vparam in env:
                    out.append((container, ("value", key, env[vparam], None, env, site_file)))
            elif bare and owner == "outer":
                out.append((container, ("func", key, None, defn, {vparam: None}, site_file)))
            elif not bare and owner == "inner":
                out.append((container, ("func", key, None, defn, _args_env(call, outer), site_file)))

    for c in site_file.root.named_children:
        d = _decorated(c)
        if c.type == "decorated_definition" and d is not None and d.type == "function_definition":
            for dec in c.named_children:
                if dec.type != "decorator" or not dec.named_children:
                    continue
                e = dec.named_children[0]
                if e.type in ("identifier", "attribute"):
                    call_site(e, e, d, True)
                elif e.type == "call":
                    call_site(e.child_by_field_name("function"), e, d, False)
        elif c.type == "expression_statement":
            for a in c.named_children:
                if a.type == "call":
                    f = a.child_by_field_name("function")
                    if f is not None and f.type in ("identifier", "attribute"):
                        call_site(f, a, None, False)
    return out
