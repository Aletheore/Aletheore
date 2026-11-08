"""A lazy repository-wide index of PHP functions, classes and includes.

PHP functions share one global namespace, so a function is found by name anywhere in the repository (a
name defined in more than one file resolves to nothing). Classes are found by name the same way. An
`include`/`require` with a literal path is resolved relative to the including file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.scanner.graph import PHP_LANGUAGE

_EXTRA_SKIP = {"vendor", ".worktrees", ".repowise", "node_modules", "external"}
MAX_FILES = 40000
EXTS = (".php", ".inc", ".phtml", ".php5", ".php7")


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


@dataclass(eq=False)
class PhpClass:
    name: str
    node: Node
    file: "PhpFile"
    parent: str | None = None
    methods: dict[str, Node] = field(default_factory=dict)


@dataclass(eq=False)
class PhpFile:
    path: Path
    src: bytes
    root: Node
    functions: dict[str, Node] = field(default_factory=dict)
    classes: dict[str, PhpClass] = field(default_factory=dict)


def _scan(f: PhpFile) -> None:
    stack = [f.root]
    while stack:
        n = stack.pop()
        if n.type == "function_definition":
            f.functions[_t(n.child_by_field_name("name"))] = n
        elif n.type in ("class_declaration", "trait_declaration", "interface_declaration"):
            cls = PhpClass(_t(n.child_by_field_name("name")), n, f)
            for c in n.children:
                if c.type == "base_clause" and c.named_children:
                    cls.parent = _t(c.named_children[0]).split("\\")[-1]
            body = n.child_by_field_name("body")
            for m in (body.named_children if body is not None else []):
                if m.type == "method_declaration":
                    cls.methods[_t(m.child_by_field_name("name"))] = m
            f.classes[cls.name] = cls
        stack.extend(n.children)


class PhpIndex:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self._files: dict[Path, PhpFile] = {}
        self._fn: dict[str, list[tuple[Path, Node]]] | None = None
        self._cls: dict[str, list[Path]] | None = None
        self._parser = Parser(PHP_LANGUAGE)

    def file(self, path: Path) -> PhpFile | None:
        path = path.resolve()
        if path in self._files:
            return self._files[path]
        try:
            src = path.read_bytes()
        except OSError:
            return None
        pf = PhpFile(path=path, src=src, root=self._parser.parse(src).root_node)
        _scan(pf)
        self._files[path] = pf
        return pf

    def _build(self) -> None:
        self._fn, self._cls = {}, {}
        skip = set(IGNORED_DIRS) | _EXTRA_SKIP
        n = 0
        for p in self.repo.rglob("*"):
            if p.suffix not in EXTS:
                continue
            if skip.intersection(p.relative_to(self.repo).parts):
                continue
            n += 1
            if n > MAX_FILES:
                break
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if b"function" not in data and b"class" not in data:
                continue
            pf = self.file(p)
            if pf is None:
                continue
            for name, node in pf.functions.items():
                self._fn.setdefault(name.lower(), []).append((pf.path, node))
            for name in pf.classes:
                self._cls.setdefault(name.lower(), []).append(pf.path)

    def function(self, name: str, from_file: PhpFile):
        if name in from_file.functions:
            return from_file, from_file.functions[name]
        if self._fn is None:
            self._build()
        hits = self._fn.get(name.lower(), [])
        if len(hits) == 1:
            pf = self.file(hits[0][0])
            return (pf, hits[0][1]) if pf is not None else None
        return None

    def cls(self, name: str, from_file: PhpFile) -> PhpClass | None:
        name = name.split("\\")[-1]
        if name in from_file.classes:
            return from_file.classes[name]
        if self._cls is None:
            self._build()
        paths = self._cls.get(name.lower(), [])
        if len(paths) == 1:
            pf = self.file(paths[0])
            return pf.classes.get(name) if pf is not None else None
        return None

    def include_suffix(self, suffix: str, from_file: PhpFile) -> PhpFile | None:
        """A repository file whose path ends with `suffix` (a leading constant prefix was unknown)."""
        suffix = suffix.replace("\\", "/").lstrip("./")
        if len(suffix) < 4 or "/" not in suffix:
            return None
        matches = []
        skip = set(IGNORED_DIRS) | _EXTRA_SKIP
        for p in self.repo.rglob("*" + Path(suffix).suffix):
            if skip.intersection(p.relative_to(self.repo).parts):
                continue
            if p.as_posix().endswith("/" + suffix):
                matches.append(p)
                if len(matches) > 1:
                    return None
        return self.file(matches[0]) if matches else None

    def include(self, rel: str, from_file: PhpFile) -> PhpFile | None:
        cand = (from_file.path.parent / rel)
        if cand.is_file():
            return self.file(cand)
        cand = self.repo / rel.lstrip("/")
        return self.file(cand) if cand.is_file() else None
