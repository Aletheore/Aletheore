"""Go same-package file coupling.

Go imports are package level, so two files of one package never import each other even though one calls the
other's functions. This module adds a conservative file-to-file edge for that case: file A depends on file B of
the same package when A uses an identifier that is declared at package level in B and in no other file of the
package. Anything uncertain is left out:

  - selector names (`x.Name`), struct-literal keys and qualified type names are not identifier uses,
  - a name that A also declares locally anywhere (parameter, `:=`, local var/const/type, range or type-switch
    binding, type parameter) is treated as shadowed for the whole file,
  - a name declared in two or more files (build-tag variants, per-OS files) is ambiguous and dropped,
  - methods and struct fields are not tracked (their names collide across types).

Non-test files only see non-test declarations; test files see every file of the package.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import tree_sitter_go as tsgo
from tree_sitter import Language, Node, Parser

ENABLED = os.environ.get("ALETHEORE_GO_SAMEPKG", "1") != "0"
_LANGUAGE = Language(tsgo.language())
_IGNORED_NAMES = {"_", "init", "main"}


def _text(source: bytes, node: Node) -> str:
    return source[node.start_byte:node.end_byte].decode(errors="ignore")


def _spec_names(spec: Node, source: bytes) -> list[str]:
    return [_text(source, c) for c in spec.children_by_field_name("name") if c.type == "identifier"]


def analyse_file(source: bytes) -> tuple[str, set[str], set[str], set[str]]:
    """(package name, package-level declared names, identifier uses, locally declared names)."""
    parser = Parser(_LANGUAGE)
    tree = parser.parse(source)
    root = tree.root_node
    package = ""
    declared: set[str] = set()
    for child in root.children:
        if child.type == "package_clause":
            for c in child.children:
                if c.type == "package_identifier":
                    package = _text(source, c)
        elif child.type == "function_declaration":
            name = child.child_by_field_name("name")
            if name is not None:
                declared.add(_text(source, name))
        elif child.type == "type_declaration":
            for spec in child.children:
                if spec.type in ("type_spec", "type_alias"):
                    name = spec.child_by_field_name("name")
                    if name is not None:
                        declared.add(_text(source, name))
        elif child.type in ("const_declaration", "var_declaration"):
            stack = list(child.children)
            while stack:
                spec = stack.pop()
                if spec.type in ("const_spec", "var_spec"):
                    declared.update(_spec_names(spec, source))
                elif spec.type in ("var_spec_list", "const_spec_list"):
                    stack.extend(spec.children)

    uses: set[str] = set()
    local: set[str] = set()
    stack = [(root, False)]  # (node, inside a function/method body or literal)
    while stack:
        node, inside = stack.pop()
        t = node.type
        enter = inside or t in ("function_declaration", "method_declaration", "func_literal")
        if t in ("identifier", "type_identifier"):
            parent = node.parent
            ptype = parent.type if parent is not None else ""
            name = _text(source, node)
            if ptype == "qualified_type" and parent.child_by_field_name("name") is not None and parent.child_by_field_name("name").id == node.id:
                pass  # pkg.Type: the type name belongs to another package
            elif ptype == "literal_element" and parent.parent is not None and parent.parent.type == "keyed_element" and parent.parent.children and parent.parent.children[0].id == parent.id:
                pass  # struct-literal key (or map key; skipped conservatively)
            elif ptype in ("parameter_declaration", "variadic_parameter_declaration", "type_parameter_declaration") and parent.child_by_field_name("type") is not None and parent.child_by_field_name("type").id != node.id:
                local.add(name)
            elif ptype == "labeled_statement" or ptype in ("break_statement", "continue_statement", "goto_statement"):
                pass
            else:
                uses.add(name)
        # local declarations
        if t == "short_var_declaration":
            left = node.child_by_field_name("left")
            if left is not None:
                for c in left.children:
                    if c.type == "identifier":
                        local.add(_text(source, c))
        elif t in ("var_spec", "const_spec") and inside:
            local.update(_spec_names(node, source))
        elif t == "type_spec" and inside:
            name = node.child_by_field_name("name")
            if name is not None:
                local.add(_text(source, name))
        elif t == "range_clause":
            left = node.child_by_field_name("left")
            if left is not None:
                for c in left.children:
                    if c.type == "identifier":
                        local.add(_text(source, c))
        elif t == "type_switch_statement":
            alias = node.child_by_field_name("alias")
            if alias is not None:
                for c in alias.children if alias.type == "expression_list" else [alias]:
                    if c.type == "identifier":
                        local.add(_text(source, c))
        elif t == "receive_statement":
            left = node.child_by_field_name("left")
            if left is not None:
                for c in left.children if left.type == "expression_list" else [left]:
                    if c.type == "identifier":
                        local.add(_text(source, c))
        for child in node.children:
            stack.append((child, enter))
    return package, declared, uses, local


_KNOWN_OS_ARCH = (
    "aix|android|darwin|dragonfly|freebsd|hurd|illumos|ios|js|linux|nacl|netbsd|openbsd|plan9|solaris|wasip1|windows|zos|"
    "386|amd64|amd64p32|arm|armbe|arm64|arm64be|loong64|mips|mipsle|mips64|mips64le|mips64p32|mips64p32le|ppc|ppc64|"
    "ppc64le|riscv|riscv64|s390|s390x|sparc|sparc64|wasm"
)
_GOOS_ARCH_SUFFIX = re.compile(rf"_({_KNOWN_OS_ARCH})(_({_KNOWN_OS_ARCH}))?(_test)?\.go$")
_BUILD_LINE = re.compile(r"(?m)^\s*//\s*(?:go:build|\+build)\b")


def build_constrained(filename: str, head: str) -> bool:
    """True when the file is only built under some tags/platforms (a `//go:build` line before the package
    clause, or a _GOOS/_GOARCH filename suffix). Such files are alternatives to each other, not additions."""
    before_package = head.split("\npackage ", 1)[0]
    return bool(_BUILD_LINE.search(before_package)) or bool(_GOOS_ARCH_SUFFIX.search(filename))


@lru_cache(maxsize=512)
def _package_files(directory: str) -> dict[str, tuple[str, frozenset, frozenset, frozenset]]:
    out = {}
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".go"))
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(directory, name), "rb") as handle:
                data = handle.read()
            source_head = data[:2000].decode(errors="ignore")
            package, declared, uses, local = analyse_file(data)
        except OSError:
            continue
        out[name] = (package, frozenset(declared), frozenset(uses), frozenset(local), build_constrained(name, source_head))
    return out


def same_package_targets(repo_path: Path, file_path: Path) -> list[str]:
    """Repo-relative paths of the sibling files `file_path` uses package-level names from."""
    if not ENABLED:
        return []
    directory = str(file_path.parent)
    files = _package_files(directory)
    me = files.get(file_path.name)
    if me is None or not me[0]:
        return []
    package = me[0]
    is_test = file_path.name.endswith("_test.go")
    peers = {
        n: v for n, v in files.items()
        if v[0] == package and (is_test or not n.endswith("_test.go"))
    }
    declared_in: dict[str, list[str]] = {}
    for n, v in peers.items():
        for ident in v[1]:
            declared_in.setdefault(ident, []).append(n)
    targets: set[str] = set()
    for ident in me[2]:
        if ident in _IGNORED_NAMES or ident in me[3] or ident in me[1]:
            continue
        owners = declared_in.get(ident)
        if not owners:
            continue
        # Build-tag variants declare the same name in several files. One unconditional file wins; if every
        # declaring file is conditional, they are alternatives and all of them are the dependency.
        plain = [o for o in owners if not peers[o][4]]
        if len(owners) == 1:
            chosen = owners
        elif len(plain) == 1:
            chosen = plain
        elif not plain:
            chosen = owners
        else:
            continue
        targets.update(o for o in chosen if o != file_path.name)
    root = str(repo_path)
    result = []
    for n in sorted(targets):
        full = os.path.join(directory, n)
        if full.startswith(root + os.sep):
            result.append(full[len(root) + 1:].replace(os.sep, "/"))
    return result


def clear_cache() -> None:
    _package_files.cache_clear()


def is_build_constrained(repo_path: Path, rel_target: str) -> bool:
    full = Path(repo_path) / rel_target
    info = _package_files(str(full.parent)).get(full.name)
    return bool(info and info[4])
