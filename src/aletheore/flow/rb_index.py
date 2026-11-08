"""A lazy repository-wide index of Ruby classes, modules, methods and constants, so calls resolve across files.

Ruby namespaces are ignored: a class or module is found by its last name segment anywhere in the repository,
preferring the one in the same file, and a name that matches more than one resolves to nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.scanner.graph import RUBY_LANGUAGE

_EXTRA_SKIP = {"spec", "test", "tests", "vendor", "db", "node_modules", ".worktrees", ".repowise", "tmp", "features", "bin"}
MAX_FILES = 20000


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def const_name(n: Node | None) -> str:
    """Last segment of a constant or A::B scope resolution."""
    if n is None:
        return ""
    if n.type == "scope_resolution":
        return _t(n.child_by_field_name("name"))
    return _t(n)


@dataclass(eq=False)
class RbClass:
    name: str
    node: Node
    file: "RbFile"
    bases: list[str] = field(default_factory=list)
    methods: dict[str, list[Node]] = field(default_factory=dict)    # instance methods
    smethods: dict[str, list[Node]] = field(default_factory=dict)   # class methods (def self.x, class << self)
    consts: dict[str, Node] = field(default_factory=dict)
    private: set[int] = field(default_factory=set)                   # start_byte of private / protected method nodes
    is_module: bool = False


@dataclass(eq=False)
class RbFile:
    path: Path
    src: bytes
    root: Node
    classes: list[RbClass] = field(default_factory=list)
    main: RbClass | None = None                                       # top-level methods live here


def _collect_body(body: Node | None, cls: RbClass, f: "RbFile", singleton: bool = False) -> None:
    if body is None:
        return
    private = False
    for c in body.named_children:
        t = c.type
        if t == "method":
            name = _t(c.child_by_field_name("name"))
            (cls.smethods if singleton else cls.methods).setdefault(name, []).append(c)
            if private:
                cls.private.add(c.start_byte)
        elif t == "singleton_method":
            cls.smethods.setdefault(_t(c.child_by_field_name("name")), []).append(c)
        elif t == "identifier" and _t(c) in ("private", "protected"):
            private = True
        elif t == "identifier" and _t(c) == "public":
            private = False
        elif t == "call" and c.child_by_field_name("receiver") is None and _t(c.child_by_field_name("method")) in ("private", "protected", "public", "private_class_method"):
            arg = c.child_by_field_name("arguments")
            m = _t(c.child_by_field_name("method"))
            if arg is None and m != "private_class_method":
                private = m != "public"
            elif arg is not None and m in ("private", "protected"):
                for a in arg.named_children:
                    if a.type in ("method",):
                        cls.private.add(a.start_byte)
                    elif a.type in ("simple_symbol", "string"):
                        for ms in cls.methods.get(_t(a).strip(":\"'"), []):
                            cls.private.add(ms.start_byte)
        elif t == "assignment" and c.child_by_field_name("left") is not None and c.child_by_field_name("left").type == "constant":
            cls.consts[_t(c.child_by_field_name("left"))] = c.child_by_field_name("right")
        elif t in ("class", "module"):
            _collect_class(c, f)
        elif t == "singleton_class":
            _collect_body(c.child_by_field_name("body"), cls, f, singleton=True)
        elif t in ("body_statement", "begin"):
            _collect_body(c, cls, f, singleton)


def _collect_class(node: Node, f: RbFile) -> None:
    name = const_name(node.child_by_field_name("name"))
    cls = RbClass(name, node, f, is_module=node.type == "module")
    sup = node.child_by_field_name("superclass")
    if sup is not None and sup.named_children:
        cls.bases = [const_name(sup.named_children[0])]
    body = node.child_by_field_name("body")
    _collect_body(body, cls, f)
    # `include Foo` mixes a module's methods in: treat the module as a base.
    for c in (body.named_children if body is not None else []):
        if c.type == "call" and c.child_by_field_name("receiver") is None and _t(c.child_by_field_name("method")) in ("include", "extend", "prepend"):
            arg = c.child_by_field_name("arguments")
            for a in (arg.named_children if arg is not None else []):
                if a.type in ("constant", "scope_resolution"):
                    cls.bases.append(const_name(a))
    f.classes.append(cls)


def _collect_file(root: Node, f: RbFile) -> None:
    main = RbClass("<main>", root, f)
    for c in root.named_children:
        if c.type in ("class", "module"):
            _collect_class(c, f)
        elif c.type == "method":
            main.methods.setdefault(_t(c.child_by_field_name("name")), []).append(c)
        elif c.type == "assignment" and c.child_by_field_name("left") is not None and c.child_by_field_name("left").type == "constant":
            main.consts[_t(c.child_by_field_name("left"))] = c.child_by_field_name("right")
    f.main = main
    f.classes.append(main)


class RbIndex:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self._files: dict[Path, RbFile] = {}
        self._names: dict[str, list[Path]] | None = None
        self._cls_cache: dict[tuple, RbClass | None] = {}
        self._parser = Parser(RUBY_LANGUAGE)

    def file(self, path: Path) -> RbFile | None:
        path = path.resolve()
        if path in self._files:
            return self._files[path]
        try:
            src = path.read_bytes()
        except OSError:
            return None
        rf = RbFile(path=path, src=src, root=self._parser.parse(src).root_node)
        _collect_file(rf.root, rf)
        self._files[path] = rf
        return rf

    def _scan(self) -> dict[str, list[Path]]:
        names: dict[str, list[Path]] = {}
        skip = set(IGNORED_DIRS) | _EXTRA_SKIP
        n = 0
        rx = re.compile(rb"^\s*(?:class|module)\s+(?:[A-Z]\w*::)*([A-Z]\w*)", re.M)
        for p in self.repo.rglob("*.rb"):
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

    def cls(self, name: str, from_file: RbFile | None) -> RbClass | None:
        key = (name, id(from_file))
        if key not in self._cls_cache:
            self._cls_cache[key] = self._cls(name, from_file)
        return self._cls_cache[key]

    def _cls(self, name: str, from_file: RbFile | None) -> RbClass | None:
        name = name.split("::")[-1]
        if not name:
            return None
        if from_file is not None:
            local = [c for c in from_file.classes if c.name == name]
            if len(local) == 1:
                return local[0]
        if self._names is None:
            self._names = self._scan()
        found: list[RbClass] = []
        for p in self._names.get(name, []):
            f = self.file(p)
            if f is not None:
                found.extend(c for c in f.classes if c.name == name)
        return found[0] if len(found) == 1 else None
