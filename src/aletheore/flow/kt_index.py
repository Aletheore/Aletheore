"""A lazy repository-wide index of Kotlin classes, objects and functions, so calls resolve across files.

Packages are ignored: a class is found by its simple name anywhere in the repository, preferring the one in the
same file, and a name that matches more than one class resolves to nothing. Companion-object members are merged
into their class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.scanner.graph import KOTLIN_LANGUAGE

_TYPES = ("class_declaration", "object_declaration", "interface_declaration")
_EXTRA_SKIP = {"build", "out", "target", ".gradle", ".worktrees", ".repowise", "node_modules", "test", "tests", "androidTest", "generated"}
MAX_FILES = 20000


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def name_of(n: Node) -> str:
    f = n.child_by_field_name("name")
    if f is not None:
        return _t(f)
    return next((_t(c) for c in n.children if c.type == "identifier"), "")


def annotations(n: Node) -> set[str]:
    out: set[str] = set()
    mods = next((c for c in n.children if c.type in ("modifiers", "parameter_modifiers")), None)
    for a in (mods.children if mods is not None else []):
        if a.type == "annotation":
            for x in a.children:
                if x.type in ("user_type", "constructor_invocation"):
                    ident = next((i for i in x.children if i.type == "identifier"), None) or next((i for i in x.named_children[0].children if i.type == "identifier"), None) if x.type == "constructor_invocation" and x.named_children else next((i for i in x.children if i.type == "identifier"), None)
                    out.add(_t(ident))
    return out


@dataclass(eq=False)
class KtClass:
    name: str
    node: Node
    file: "KtFile"
    bases: list[str] = field(default_factory=list)
    funcs: dict[str, list[Node]] = field(default_factory=dict)
    props: dict[str, Node | None] = field(default_factory=dict)       # property initializers (class and companion)
    consts: dict[str, Node] = field(default_factory=dict)             # const val / companion vals with initializers
    ctor_params: list[Node] = field(default_factory=list)
    inits: list[Node] = field(default_factory=list)
    annots: set[str] = field(default_factory=set)
    is_object: bool = False


@dataclass(eq=False)
class KtFile:
    path: Path
    src: bytes
    root: Node
    classes: list[KtClass] = field(default_factory=list)
    main: KtClass | None = None


def _members(body: Node | None, cls: KtClass, in_companion: bool = False) -> None:
    for m in (body.named_children if body is not None else []):
        t = m.type
        if t == "function_declaration":
            cls.funcs.setdefault(name_of(m), []).append(m)
        elif t == "property_declaration":
            vd = next((c for c in m.children if c.type == "variable_declaration"), None)
            init = next((c for c in m.named_children if c.start_byte > (vd.end_byte if vd is not None else -1) and c.type not in ("modifiers", "property_delegate")), None)
            if vd is not None:
                nm = _t(next((c for c in vd.children if c.type == "identifier"), None))
                cls.props[nm] = init
                if init is not None and (in_companion or cls.is_object or "const" in _t(next((c for c in m.children if c.type == "modifiers"), None)) or any(c.type == "val" for c in m.children)):
                    cls.consts[nm] = init
        elif t == "companion_object":
            _members(next((c for c in m.children if c.type == "class_body"), None), cls, True)
        elif t == "anonymous_initializer":
            cls.inits.append(m)


def _collect(node: Node, f: KtFile) -> None:
    for c in node.children:
        if c.type in _TYPES:
            cls = KtClass(name_of(c), c, f, is_object=c.type == "object_declaration", annots=annotations(c))
            ds = next((x for x in c.children if x.type == "delegation_specifiers"), None)
            for d in (ds.named_children if ds is not None else []):
                stack = [d]
                ident = None
                while stack and ident is None:
                    cur = stack.pop(0)
                    if cur.type == "user_type":
                        ident = next((x for x in cur.children if x.type == "identifier"), None)
                    stack.extend(cur.children)
                cls.bases.append(_t(ident))
            pc = next((x for x in c.children if x.type == "primary_constructor"), None)
            cp = next((x for x in (pc.children if pc is not None else []) if x.type == "class_parameters"), None)
            cls.ctor_params = [p for p in (cp.named_children if cp is not None else []) if p.type == "class_parameter"]
            for p in cls.ctor_params:
                if any(x.type in ("val", "var") for x in p.children):
                    cls.props[_t(next((x for x in p.children if x.type == "identifier"), None))] = None
            _members(next((x for x in c.children if x.type == "class_body"), None), cls)
            f.classes.append(cls)
            body = next((x for x in c.children if x.type == "class_body"), None)
            if body is not None:
                for x in body.named_children:
                    if x.type in _TYPES:
                        _collect(body, f)
                        break
        elif c.type not in ("function_declaration", "property_declaration"):
            _collect(c, f)


def _collect_file(root: Node, f: KtFile) -> None:
    main = KtClass("<main>", root, f)
    for c in root.named_children:
        if c.type == "function_declaration":
            main.funcs.setdefault(name_of(c), []).append(c)
        elif c.type == "property_declaration":
            _members(type("B", (), {"named_children": [c]})(), main, True)
    _collect(root, f)
    f.main = main
    f.classes.append(main)


class KtIndex:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self._resolved: dict[Path, Path] = {}  # raw path -> Path.resolve(), which walks the filesystem
        self._files: dict[Path, KtFile] = {}
        self._names: dict[str, list[Path]] | None = None
        self._cache: dict[tuple, KtClass | None] = {}
        self._parser = Parser(KOTLIN_LANGUAGE)

    def file(self, path: Path) -> KtFile | None:
        path = self._resolved.get(path) or self._resolved.setdefault(path, path.resolve())
        if path in self._files:
            return self._files[path]
        try:
            src = path.read_bytes()
        except OSError:
            return None
        kf = KtFile(path=path, src=src, root=self._parser.parse(src).root_node)
        _collect_file(kf.root, kf)
        self._files[path] = kf
        return kf

    def _scan(self) -> dict[str, list[Path]]:
        names: dict[str, list[Path]] = {}
        skip = set(IGNORED_DIRS) | _EXTRA_SKIP
        rx = re.compile(rb"\b(?:class|object|interface)\s+([A-Za-z_]\w*)")
        n = 0
        for p in self.repo.rglob("*.kt"):
            if skip.intersection(p.relative_to(self.repo).parts):
                continue
            n += 1
            if n > MAX_FILES:
                break
            try:
                data = p.read_bytes()
            except OSError:
                continue
            for m in set(rx.findall(data)):
                names.setdefault(m.decode("utf-8", "replace"), []).append(p)
        return names

    def cls(self, name: str, from_file: KtFile | None) -> KtClass | None:
        key = (name, id(from_file))
        if key not in self._cache:
            self._cache[key] = self._cls(name, from_file)
        return self._cache[key]

    def _cls(self, name: str, from_file: KtFile | None) -> KtClass | None:
        name = name.split(".")[-1].split("<")[0]
        if from_file is not None:
            local = [c for c in from_file.classes if c.name == name]
            if len(local) == 1:
                return local[0]
        if self._names is None:
            self._names = self._scan()
        found: list[KtClass] = []
        for p in self._names.get(name, []):
            f = self.file(p)
            if f is not None:
                found.extend(c for c in f.classes if c.name == name)
        return found[0] if len(found) == 1 else None
