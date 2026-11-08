"""A lazy index of Go packages: functions, methods and struct types, found by directory.

A Go package is a directory. `f(x)` resolves to a function of the same directory, `pkg.F(x)` through an import
whose path ends with the directory's path inside the repository. Methods are resolved by the receiver's struct
type when it is known. A name that matches more than one candidate resolves to nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.scanner.graph import GO_LANGUAGE

_EXTRA_SKIP = {"vendor", "testdata", ".worktrees", ".repowise", "node_modules", "third_party"}
MAX_FILES = 20000


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


@dataclass(eq=False)
class GoFile:
    path: Path
    src: bytes
    root: Node
    imports: dict[str, str] = field(default_factory=dict)       # alias -> import path
    functions: dict[str, Node] = field(default_factory=dict)
    methods: dict[tuple[str, str], Node] = field(default_factory=dict)  # (receiver type, name) -> node
    types: set[str] = field(default_factory=set)


def _recv_type(method: Node) -> str:
    recv = method.child_by_field_name("receiver")
    if recv is None:
        return ""
    text = _t(recv).strip("()")
    parts = text.split()
    return parts[-1].lstrip("*").split("[")[0] if parts else ""


def _scan(f: GoFile) -> None:
    stack = [f.root]
    while stack:
        n = stack.pop()
        t = n.type
        if t == "function_declaration":
            f.functions[_t(n.child_by_field_name("name"))] = n
        elif t == "method_declaration":
            f.methods[(_recv_type(n), _t(n.child_by_field_name("name")))] = n
        elif t == "type_spec":
            f.types.add(_t(n.child_by_field_name("name")))
        elif t == "import_spec":
            path = _t(n.child_by_field_name("path")).strip('"`')
            name = n.child_by_field_name("name")
            alias = _t(name) if name is not None else path.rsplit("/", 1)[-1]
            if alias not in ("_", "."):
                f.imports[alias] = path
        stack.extend(n.children)


class GoIndex:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self._files: dict[Path, GoFile] = {}
        self._by_dir: dict[Path, list[Path]] | None = None
        self._parser = Parser(GO_LANGUAGE)

    def file(self, path: Path) -> GoFile | None:
        path = path.resolve()
        if path in self._files:
            return self._files[path]
        try:
            src = path.read_bytes()
        except OSError:
            return None
        gf = GoFile(path=path, src=src, root=self._parser.parse(src).root_node)
        _scan(gf)
        self._files[path] = gf
        return gf

    def _dirs(self) -> dict[Path, list[Path]]:
        if self._by_dir is None:
            self._by_dir = {}
            skip = set(IGNORED_DIRS) | _EXTRA_SKIP
            n = 0
            for p in self.repo.rglob("*.go"):
                if skip.intersection(p.relative_to(self.repo).parts) or p.name.endswith("_test.go"):
                    continue
                n += 1
                if n > MAX_FILES:
                    break
                self._by_dir.setdefault(p.parent, []).append(p)
        return self._by_dir

    def package_files(self, directory: Path) -> list[GoFile]:
        return [f for f in (self.file(p) for p in self._dirs().get(directory.resolve(), [])) if f is not None]

    def function(self, name: str, from_file: GoFile):
        """A function of the same package: (file, node) or None when absent or ambiguous."""
        hits = [(f, f.functions[name]) for f in self.package_files(from_file.path.parent) if name in f.functions]
        return hits[0] if len(hits) == 1 else None

    def method(self, recv_type: str, name: str, from_file: GoFile):
        hits = [(f, f.methods[(recv_type, name)]) for f in self.package_files(from_file.path.parent) if (recv_type, name) in f.methods]
        return hits[0] if len(hits) == 1 else None

    def package_function(self, alias: str, name: str, from_file: GoFile):
        """`pkg.F`: resolve the alias through the imports to a directory inside the repository."""
        imp = from_file.imports.get(alias)
        if not imp:
            return None
        directory = self._resolve_import(imp)
        if directory is None:
            return None
        hits = [(f, f.functions[name]) for f in self.package_files(directory) if name in f.functions]
        return hits[0] if len(hits) == 1 else None

    def _resolve_import(self, imp: str) -> Path | None:
        """The one repository directory an import path points at, or None. Cached: this runs on every call."""
        cache = self.__dict__.setdefault("_imp_cache", {})
        if imp in cache:
            return cache[imp]
        if "_rel_dirs" not in self.__dict__:
            self._rel_dirs = {d: (d.relative_to(self.repo).as_posix() if d.is_relative_to(self.repo) else d.as_posix()) for d in self._dirs()}
        parts = imp.split("/")
        found: Path | None = None
        # The longest suffix of the import path that names exactly one repository directory.
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            matches = [d for d, rel in self._rel_dirs.items() if rel == suffix or rel.endswith("/" + suffix)]
            if len(matches) == 1:
                found = matches[0]
                break
            if len(matches) > 1:
                break
        cache[imp] = found
        return found
