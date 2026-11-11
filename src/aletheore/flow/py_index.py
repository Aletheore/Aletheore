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
