"""A lazy repository-wide index of Rust types, impl blocks, functions and constants, so calls resolve across files.

Modules and crates are ignored: a type is found by its simple name anywhere in the repository, preferring the one in
the same file, and a name that matches more than one type resolves to nothing. Every `impl` block, including trait
impls, contributes its functions to the type it implements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.scanner.graph import RUST_LANGUAGE

_EXTRA_SKIP = {"target", "tests", "benches", "examples", ".worktrees", ".repowise", "node_modules", "vendor"}
MAX_FILES = 20000


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def type_name(n: Node | None) -> str:
    """Simple name of a type node: `web::Query<P>` -> `Query`, `&mut Foo` -> `Foo`."""
    while n is not None and n.type in ("reference_type", "generic_type", "scoped_type_identifier", "pointer_type"):
        if n.type == "scoped_type_identifier":
            return _t(n.child_by_field_name("name"))
        inner = n.child_by_field_name("type") or next((c for c in n.named_children if c.type not in ("mutable_specifier", "lifetime")), None)
        if inner is None:
            break
        n = inner
    return _t(n)


def attrs_before(n: Node) -> list[str]:
    out: list[str] = []
    sib = n.prev_named_sibling
    while sib is not None and sib.type in ("attribute_item", "line_comment", "block_comment"):
        if sib.type == "attribute_item":
            out.append(_t(sib))
        sib = sib.prev_named_sibling
    return out


@dataclass(eq=False)
class RsType:
    name: str
    node: Node
    file: "RsFile"
    funcs: dict[str, list[Node]] = field(default_factory=dict)
    consts: dict[str, Node] = field(default_factory=dict)
    fields: list[str] = field(default_factory=list)


@dataclass(eq=False)
class RsFile:
    path: Path
    src: bytes
    root: Node
    types: list[RsType] = field(default_factory=list)
    main: RsType | None = None


def _get(f: RsFile, name: str, node: Node) -> RsType:
    for t in f.types:
        if t.name == name:
            return t
    t = RsType(name, node, f)
    f.types.append(t)
    return t


def _items(node: Node, f: RsFile, owner: RsType | None) -> None:
    for c in node.named_children:
        t = c.type
        if t == "function_item":
            (owner or f.main).funcs.setdefault(_t(c.child_by_field_name("name")), []).append(c)
        elif t in ("const_item", "static_item"):
            v = c.child_by_field_name("value")
            if v is not None:
                (owner or f.main).consts[_t(c.child_by_field_name("name"))] = v
        elif t in ("struct_item", "enum_item", "union_item"):
            ty = _get(f, _t(c.child_by_field_name("name")), c)
            body = c.child_by_field_name("body")
            for fd in (body.named_children if body is not None else []):
                if fd.type == "field_declaration":
                    ty.fields.append(_t(fd.child_by_field_name("name")))
        elif t == "impl_item":
            ty = _get(f, type_name(c.child_by_field_name("type")), c)
            body = c.child_by_field_name("body")
            if body is not None:
                _items(body, f, ty)
        elif t == "trait_item":
            body = c.child_by_field_name("body")
            ty = _get(f, _t(c.child_by_field_name("name")), c)
            if body is not None:
                _items(body, f, ty)
        elif t == "mod_item":
            body = c.child_by_field_name("body")
            if body is not None:
                _items(body, f, None)


class RsIndex:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self._files: dict[Path, RsFile] = {}
        self._names: dict[str, list[Path]] | None = None
        self._cache: dict[tuple, RsType | None] = {}
        self._fn_names: dict[str, list[Path]] | None = None
        self._fn_cache: dict[tuple, tuple | None] = {}
        self._parser = Parser(RUST_LANGUAGE)

    def file(self, path: Path) -> RsFile | None:
        path = path.resolve()
        if path in self._files:
            return self._files[path]
        try:
            src = path.read_bytes()
        except OSError:
            return None
        rf = RsFile(path=path, src=src, root=self._parser.parse(src).root_node)
        rf.main = RsType("<main>", rf.root, rf)
        _items(rf.root, rf, None)
        rf.types.append(rf.main)
        self._files[path] = rf
        return rf

    def _scan(self) -> dict[str, list[Path]]:
        names: dict[str, list[Path]] = {}
        skip = set(IGNORED_DIRS) | _EXTRA_SKIP
        rx = re.compile(rb"\b(?:struct|enum|trait|union)\s+([A-Za-z_]\w*)|\bimpl(?:<[^>]*>)?\s+(?:[\w:<>]+\s+for\s+)?([A-Z]\w*)")
        n = 0
        for p in self.repo.rglob("*.rs"):
            if skip.intersection(p.relative_to(self.repo).parts):
                continue
            n += 1
            if n > MAX_FILES:
                break
            try:
                data = p.read_bytes()
            except OSError:
                continue
            for a, b in set(rx.findall(data)):
                nm = (a or b).decode("utf-8", "replace")
                names.setdefault(nm, []).append(p)
        return names

    def ty(self, name: str, from_file: RsFile | None) -> list[RsType]:
        """All definitions of a type name (struct plus impls may live in different files)."""
        key = (name, id(from_file))
        if key not in self._cache:
            self._cache[key] = self._ty(name, from_file)
        return self._cache[key]

    def _ty(self, name: str, from_file: RsFile | None):
        name = name.split("::")[-1].split("<")[0]
        found: list[RsType] = []
        if self._names is None:
            self._names = self._scan()
        paths = list(self._names.get(name, []))
        if from_file is not None and from_file.path not in paths and any(t.name == name for t in from_file.types):
            paths.append(from_file.path)
        for p in paths:
            f = self.file(p)
            if f is not None:
                found.extend(t for t in f.types if t.name == name)
        return found

    def free_fn(self, name: str, from_file: RsFile | None):
        """(RsType main, fn node) of the single free function called `name`, same file first, else unique in the repo."""
        key = (name, id(from_file))
        if key in self._fn_cache:
            return self._fn_cache[key]
        hit = None
        if from_file is not None and from_file.main is not None and from_file.main.funcs.get(name):
            hit = (from_file.main, from_file.main.funcs[name][0])
        else:
            if self._fn_names is None:
                self._fn_names = {}
                skip = set(IGNORED_DIRS) | _EXTRA_SKIP
                rx = re.compile(rb"\bfn\s+([A-Za-z_]\w*)")
                n = 0
                for p in self.repo.rglob("*.rs"):
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
                        self._fn_names.setdefault(m.decode("utf-8", "replace"), []).append(p)
            found = []
            for p in self._fn_names.get(name, []):
                f = self.file(p)
                if f is not None and f.main is not None and f.main.funcs.get(name):
                    found.append((f.main, f.main.funcs[name][0]))
            if len(found) == 1:
                hit = found[0]
        self._fn_cache[key] = hit
        return hit
