"""A lazy, repository-wide index of Java classes, so calls can be followed across files.

Class names are found with a cheap regex pass over the source files; a file is only parsed with
tree-sitter when something actually needs it. Name resolution prefers the class defined in the
same file (nested classes included), then a class that is unique in the repository. An ambiguous
name resolves to nothing, which the interpreter treats as unknown rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.scanner.graph import JAVA_LANGUAGE

_CLASS_NAME = re.compile(rb"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)")
_DERIVES = re.compile(
    rb"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)[^{;]*?\b(?:implements|extends)\s+([^{]+)\{"
)
_TYPE_NODES = {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
_EXTRA_SKIP = {"target", ".worktrees", ".repowise"}
MAX_FILES = 60000


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


def simple_type_name(node_or_text) -> str:
    """The last identifier of a possibly qualified or generic type: java.util.List<String> -> List."""
    text = node_or_text if isinstance(node_or_text, str) else _text(node_or_text)
    text = text.split("<", 1)[0].strip().rstrip("[]").strip()
    short = text.rsplit(".", 1)[-1]
    if "." in text and text.replace(".", "").replace("_", "").replace("$", "").isalnum():
        return TypeName(short, text)  # remember how it was written: `beta.UserMapper` is not `alpha.UserMapper`
    return short


class TypeName(str):
    """A simple type name that also carries the qualified spelling it was declared with (`.full`). It compares and
    hashes as the simple name, so every existing lookup is unchanged."""

    full: str

    def __new__(cls, short: str, full: str):
        obj = super().__new__(cls, short)
        obj.full = full
        return obj


@dataclass(eq=False)
class ClassInfo:
    name: str
    node: Node
    file: "JavaFile"
    outer: "ClassInfo | None" = None
    superclass: str | None = None
    interfaces: list[str] = field(default_factory=list)
    methods: dict[str, list[Node]] = field(default_factory=dict)
    ctors: list[Node] = field(default_factory=list)
    field_inits: dict[str, Node | None] = field(default_factory=dict)
    field_types: dict[str, str] = field(default_factory=dict)  # declared type of each field (simple name)
    static_finals: set[str] = field(default_factory=set)


@dataclass(eq=False)
class JavaFile:
    path: Path
    src: bytes
    root: Node
    classes: list[ClassInfo] = field(default_factory=list)


def _modifiers(node: Node) -> str:
    for child in node.children:
        if child.type == "modifiers":
            return _text(child)
    return ""


def _collect(node: Node, jf: JavaFile, outer: ClassInfo | None, out: list[ClassInfo]) -> None:
    for child in node.children:
        if child.type in _TYPE_NODES:
            name = _text(child.child_by_field_name("name"))
            info = ClassInfo(name=name, node=child, file=jf, outer=outer)
            sup = child.child_by_field_name("superclass")
            if sup is not None:
                info.superclass = simple_type_name(_text(sup).replace("extends", "", 1))
            ifs = child.child_by_field_name("interfaces")
            if ifs is not None:
                raw = _text(ifs).replace("implements", "", 1).replace("extends", "", 1)
                info.interfaces = [simple_type_name(p) for p in raw.split(",") if p.strip()]
            body = child.child_by_field_name("body")
            if body is not None:
                for member in body.named_children:
                    if member.type == "method_declaration":
                        info.methods.setdefault(_text(member.child_by_field_name("name")), []).append(member)
                    elif member.type == "constructor_declaration":
                        info.ctors.append(member)
                    elif member.type == "field_declaration":
                        mods = _modifiers(member)
                        for decl in member.named_children:
                            if decl.type == "variable_declarator":
                                fname = _text(decl.child_by_field_name("name"))
                                info.field_inits[fname] = decl.child_by_field_name("value")
                                info.field_types[fname] = simple_type_name(_text(member.child_by_field_name("type")))
                                if "static" in mods and "final" in mods:
                                    info.static_finals.add(fname)
            out.append(info)
            if body is not None:
                _collect(body, jf, info, out)
        else:
            _collect(child, jf, outer, out)


class JavaIndex:
    def __init__(self, repo: Path):
        self.repo = repo
        self._resolved: dict[Path, Path] = {}  # raw path -> Path.resolve(), which walks the filesystem
        self._files: dict[Path, JavaFile] = {}
        self._by_name: dict[str, list[Path]] | None = None
        self._derived: dict[str, set[Path]] = {}
        self._implementers_cache: dict[str, list] = {}
        self._parser = Parser(JAVA_LANGUAGE)

    def _scan_names(self) -> dict[str, list[Path]]:
        names: dict[str, list[Path]] = {}
        skip = set(IGNORED_DIRS) | _EXTRA_SKIP
        count = 0
        for path in self.repo.rglob("*.java"):
            if skip.intersection(path.relative_to(self.repo).parts):
                continue
            count += 1
            if count > MAX_FILES:
                break
            try:
                data = path.read_bytes()
            except OSError:
                continue
            for match in set(_CLASS_NAME.findall(data)):
                names.setdefault(match.decode("utf-8", "replace"), []).append(path)
            for _derived_name, bases in _DERIVES.findall(data):
                for base in bases.decode("utf-8", "replace").split(","):
                    base = simple_type_name(base.strip().split(" ")[0])
                    if base:
                        self._derived.setdefault(base, set()).add(path)
        return names

    def file(self, path: Path) -> JavaFile | None:
        path = self._resolved.get(path) or self._resolved.setdefault(path, path.resolve())
        if path in self._files:
            return self._files[path]
        try:
            src = path.read_bytes()
        except OSError:
            return None
        root = self._parser.parse(src).root_node
        jf = JavaFile(path=path, src=src, root=root)
        _collect(root, jf, None, jf.classes)
        self._files[path] = jf
        return jf

    def resolve_class(self, name: str, from_file: JavaFile | None) -> ClassInfo | None:
        name = simple_type_name(name)
        if from_file is not None:
            local = [c for c in from_file.classes if c.name == name]
            if len(local) == 1:
                return local[0]
        if self._by_name is None:
            self._by_name = self._scan_names()
        paths = self._by_name.get(name, [])
        found: list[ClassInfo] = []
        for p in paths:
            jf = self.file(p)
            if jf is not None:
                found.extend(c for c in jf.classes if c.name == name)
        return found[0] if len(found) == 1 else None

    def implementers(self, iface: str, from_file: JavaFile | None) -> list[ClassInfo]:
        """Classes that implement or extend `iface`, from the map built during the name scan."""
        if iface in self._implementers_cache:
            return self._implementers_cache[iface]
        if self._by_name is None:
            self._by_name = self._scan_names()
        out: list[ClassInfo] = []
        for p in sorted(self._derived.get(iface, ())):
            jf = self.file(p)
            if jf:
                out.extend(c for c in jf.classes if iface in c.interfaces or c.superclass == iface)
        self._implementers_cache[iface] = out
        return out
