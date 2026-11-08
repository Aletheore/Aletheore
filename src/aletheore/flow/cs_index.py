"""A lazy repository-wide index of C# classes, methods and constants, so calls resolve across files.

C# namespaces are ignored: a class is found by its simple name anywhere in the repository, preferring the one
in the same file, and a name that matches more than one class resolves to nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.scanner.graph import CSHARP_LANGUAGE

_TYPES = ("class_declaration", "struct_declaration", "record_declaration", "interface_declaration")
_EXTRA_SKIP = {"obj", "packages", "bin", ".worktrees", ".repowise", "node_modules", "migrations", "Migrations"}
MAX_FILES = 30000


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


@dataclass(eq=False)
class CsClass:
    name: str
    node: Node
    file: "CsFile"
    bases: list[str] = field(default_factory=list)
    methods: dict[str, list[Node]] = field(default_factory=dict)
    ctors: list[Node] = field(default_factory=list)
    consts: dict[str, Node | None] = field(default_factory=dict)       # const / static readonly / static fields with initializers
    fields: dict[str, Node | None] = field(default_factory=dict)       # all field initializers


@dataclass(eq=False)
class CsFile:
    path: Path
    src: bytes
    root: Node
    classes: list[CsClass] = field(default_factory=list)


def _mods(node: Node) -> str:
    return " ".join(_t(c) for c in node.children if c.type == "modifier")


def _collect(node: Node, f: CsFile) -> None:
    for c in node.children:
        if c.type in _TYPES:
            cls = CsClass(_t(c.child_by_field_name("name")) or next((_t(x) for x in c.children if x.type == "identifier"), ""), c, f)
            bl = next((x for x in c.children if x.type == "base_list"), None)
            if bl is not None:
                cls.bases = [_t(x).split("<")[0].split(".")[-1] for x in bl.named_children]
            body = c.child_by_field_name("body") or next((x for x in c.children if x.type == "declaration_list"), None)
            for m in (body.named_children if body is not None else []):
                if m.type == "method_declaration":
                    cls.methods.setdefault(_t(m.child_by_field_name("name")) or next((_t(x) for x in m.children if x.type == "identifier"), ""), []).append(m)
                elif m.type == "constructor_declaration":
                    cls.ctors.append(m)
                elif m.type == "field_declaration":
                    mods = _mods(m)
                    vd = next((x for x in m.named_children if x.type == "variable_declaration"), None)
                    for d in (vd.named_children if vd is not None else []):
                        if d.type == "variable_declarator":
                            kids = d.named_children
                            name = _t(kids[0]) if kids else ""
                            init = kids[-1] if len(kids) > 1 else None
                            cls.fields[name] = init
                            if "const" in mods or "static" in mods or "readonly" in mods:
                                cls.consts[name] = init
            f.classes.append(cls)
            if body is not None:
                _collect(body, f)
        else:
            _collect(c, f)


class CsIndex:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self._files: dict[Path, CsFile] = {}
        self._names: dict[str, list[Path]] | None = None
        self._parser = Parser(CSHARP_LANGUAGE)

    def file(self, path: Path) -> CsFile | None:
        path = path.resolve()
        if path in self._files:
            return self._files[path]
        try:
            src = path.read_bytes()
        except OSError:
            return None
        cf = CsFile(path=path, src=src, root=self._parser.parse(src).root_node)
        _collect(cf.root, cf)
        self._files[path] = cf
        return cf

    def _scan(self) -> dict[str, list[Path]]:
        import re

        names: dict[str, list[Path]] = {}
        skip = set(IGNORED_DIRS) | _EXTRA_SKIP
        n = 0
        rx = re.compile(rb"\b(?:class|struct|record|interface)\s+([A-Za-z_]\w*)")
        for p in self.repo.rglob("*.cs"):
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

    def cls(self, name: str, from_file: CsFile | None) -> CsClass | None:
        name = name.split(".")[-1].split("<")[0]
        if from_file is not None:
            local = [c for c in from_file.classes if c.name == name]
            if len(local) == 1:
                return local[0]
        if self._names is None:
            self._names = self._scan()
        found: list[CsClass] = []
        for p in self._names.get(name, []):
            f = self.file(p)
            if f is not None:
                found.extend(c for c in f.classes if c.name == name)
        return found[0] if len(found) == 1 else None
