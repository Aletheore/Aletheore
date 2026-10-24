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


def _base_type_name(source: bytes, node: Node | None) -> str | None:
    """`T`, `*T`, `T[int]`, `*T[int]` -> "T"; a type from another package (`pkg.T`) -> "pkg.T" (never matches a local type)."""
    while node is not None and node.type in ("pointer_type", "parenthesized_type"):
        inner = [c for c in node.children if c.is_named]
        node = inner[0] if inner else None
    if node is None:
        return None
    if node.type == "generic_type":
        base = node.child_by_field_name("type")
        return _base_type_name(source, base)
    if node.type == "type_identifier":
        return _text(source, node)
    if node.type == "qualified_type":
        return _text(source, node)
    return None


def analyse_members(source: bytes) -> tuple:
    """Methods and struct/interface fields of the file.

    Returns (member names declared, (type, member) declared, selector member names used,
    (receiver description, member) used, member names whose receiver type is evident or local,
    (function, result type) declared, ((type, method), result type) declared).

    A receiver description is a small tuple that is resolved against the whole package later:
    ("T", name) a type named in this file, ("call", f) the result of calling package function f,
    ("mcall", description, m) the result of calling method m on a described receiver."""
    parser = Parser(_LANGUAGE)
    root = parser.parse(source).root_node
    decl_names: set[str] = set()
    decl_typed: set[tuple[str, str]] = set()
    func_ret: set[tuple[str, str]] = set()
    method_ret: set[tuple[tuple[str, str], str]] = set()
    embeds: set[tuple[str, str]] = set()

    def single_result(node: Node) -> str | None:
        result = node.child_by_field_name("result")
        if result is None or result.type == "parameter_list":
            return None
        return _base_type_name(source, result)

    for child in root.children:
        if child.type == "function_declaration":
            name = child.child_by_field_name("name")
            ret = single_result(child)
            if name is not None and ret:
                func_ret.add((_text(source, name), ret))
        elif child.type == "method_declaration":
            name = child.child_by_field_name("name")
            receiver = child.child_by_field_name("receiver")
            if name is None:
                continue
            decl_names.add(_text(source, name))
            if receiver is not None:
                for p in receiver.children:
                    if p.type == "parameter_declaration":
                        t = _base_type_name(source, p.child_by_field_name("type"))
                        if t:
                            decl_typed.add((t, _text(source, name)))
                            ret = single_result(child)
                            if ret:
                                method_ret.add(((t, _text(source, name)), ret))
        elif child.type == "type_declaration":
            for spec in child.children:
                if spec.type != "type_spec":
                    continue
                tname_node = spec.child_by_field_name("name")
                body = spec.child_by_field_name("type")
                if tname_node is None or body is None:
                    continue
                tname = _text(source, tname_node)
                stack = [body]
                while stack:
                    n = stack.pop()
                    if n.type == "field_declaration":
                        if not n.children_by_field_name("name"):
                            embedded = _base_type_name(source, n.child_by_field_name("type"))
                            if embedded:
                                embeds.add((tname, embedded))
                        for f in n.children_by_field_name("name"):
                            decl_names.add(_text(source, f))
                            decl_typed.add((tname, _text(source, f)))
                    elif n.type == "method_elem":
                        f = n.child_by_field_name("name")
                        if f is not None:
                            decl_names.add(_text(source, f))
                            decl_typed.add((tname, _text(source, f)))
                    else:
                        stack.extend(n.children)

    var_desc: dict[str, tuple | None] = {}
    conflicted: set[str] = set()
    _MISSING = object()

    def note(var: str, desc: tuple | None) -> None:
        if var in var_desc and var_desc[var] != desc:
            var_desc[var] = None  # conflicting evidence: unknown
        else:
            var_desc[var] = desc

    def type_desc(node: Node | None) -> tuple | None:
        name = _base_type_name(source, node)
        return ("T", name) if name else None

    def expr_desc(node: Node | None) -> tuple | None:
        if node is None:
            return None
        t = node.type
        if t == "identifier":
            return var_desc.get(_text(source, node))
        if t == "parenthesized_expression":
            inner = [c for c in node.children if c.is_named]
            return expr_desc(inner[0]) if inner else None
        if t == "unary_expression" and node.children and node.children[0].type in ("&", "*"):
            return expr_desc(node.children[-1])
        if t == "composite_literal":
            return type_desc(node.child_by_field_name("type"))
        if t == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is None:
                return None
            if fn.type == "identifier":
                if _text(source, fn) == "new":
                    args = node.child_by_field_name("arguments")
                    named = [c for c in (args.children if args is not None else []) if c.is_named]
                    return type_desc(named[0]) if len(named) == 1 else None
                return ("call", _text(source, fn))
            if fn.type == "selector_expression":
                inner = expr_desc(fn.child_by_field_name("operand"))
                field = fn.child_by_field_name("field")
                if inner is not None and field is not None:
                    return ("mcall", inner, _text(source, field))
        return None

    stack = [root]
    selectors: list[Node] = []
    short_decls: list[Node] = []
    local_fields: set[str] = set()
    while stack:
        n = stack.pop()
        t = n.type
        if t in ("parameter_declaration", "variadic_parameter_declaration"):
            desc = type_desc(n.child_by_field_name("type"))
            for ident in n.children_by_field_name("name"):
                note(_text(source, ident), desc)
        elif t == "var_spec":
            desc = type_desc(n.child_by_field_name("type"))
            if desc is not None:
                for ident in n.children_by_field_name("name"):
                    note(_text(source, ident), desc)
        elif t == "short_var_declaration":
            short_decls.append(n)
        elif t == "selector_expression":
            selectors.append(n)
        elif t == "field_declaration":
            # fields of anonymous or function-local structs (table-driven tests) are not package members
            anc = n.parent
            while anc is not None and anc.type not in ("function_declaration", "method_declaration", "func_literal", "type_spec"):
                anc = anc.parent
            if anc is None or anc.type != "type_spec" or _inside_function(anc):
                local_fields.update(_text(source, f) for f in n.children_by_field_name("name"))
        stack.extend(n.children)

    # `x := expr` after parameters and declared vars are known; two passes let `a := New(); b := a.Group()` resolve
    for _ in range(2):
        for n in short_decls:
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is None or right is None:
                continue
            lefts = [c for c in left.children if c.is_named]
            rights = [c for c in right.children if c.is_named]
            for i, target in enumerate(lefts):
                if target.type != "identifier":
                    continue
                var = _text(source, target)
                desc = expr_desc(rights[i]) if len(lefts) == len(rights) else None
                if var in conflicted:
                    continue
                current = var_desc.get(var, _MISSING)
                if desc is None:
                    if current is _MISSING:
                        var_desc[var] = None
                elif current is _MISSING or current is None:
                    var_desc[var] = desc
                elif current != desc:
                    var_desc[var] = None
                    conflicted.add(var)

    used_names: set[str] = set()
    used_typed: set[tuple[tuple, str]] = set()
    known: set[str] = set()
    for sel in selectors:
        field = sel.child_by_field_name("field")
        operand = sel.child_by_field_name("operand")
        if field is None or field.type != "field_identifier":
            continue
        name = _text(source, field)
        used_names.add(name)
        desc = expr_desc(operand)
        if desc is not None:
            known.add(name)  # receiver type evident from the file: decided by (type, name) only
            used_typed.add((desc, name))
    return decl_names, decl_typed, used_names, used_typed, known, func_ret, method_ret, embeds, local_fields


def _inside_function(node: Node) -> bool:
    anc = node.parent
    while anc is not None:
        if anc.type in ("function_declaration", "method_declaration", "func_literal"):
            return True
        anc = anc.parent
    return False


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
        members = analyse_members(data) if MEMBERS_MODE_DEFAULT != "off" or os.environ.get("ALETHEORE_GO_MEMBERS") else ((),) * 9
        out[name] = (package, frozenset(declared), frozenset(uses), frozenset(local), build_constrained(name, source_head),
                     tuple(frozenset(m) for m in members))
    return out


# Methods and struct fields (the receiver's type resolved from the file and the package's own function and method
# result types, embedded types followed; plus unexported names, which can only belong to this package). Measured on
# gin against go/types: precision 1.000 and recall 0.906 -> 0.961 (see the round-3 tests). "off" disables it, as
# does ALETHEORE_GO_MEMBERS=off. Variants that were rejected: any unique name ("name": precision 0.95 because
# exported names collide with other packages' methods), typed only (recall 0.953), receiver-type mention (no gain).
MEMBERS_MODE_DEFAULT = "typed+unexp"
# Method names that nearly every package defines on some type or gets from the standard library.
_COMMON_MEMBERS = frozenset(
    "String Error Len Less Swap Read Write Close Get Set Next Reset Unwrap Name Type Value Key Add Delete Remove Clear "
    "Run Start Stop Init Do Put Push Pop Copy Clone Lock Unlock Is As Format Bytes Len Cap Seek Flush Header Context "
    "Handler Handle ServeHTTP Marshal Unmarshal MarshalJSON UnmarshalJSON Bind New Use Done Err Status Method Path "
    "Request Response Body Keys Values Data ID Id Min Max Sort Find Has Contains Load Store Send Recv Open".split()
)


def _owners(peers: dict, predicate) -> list[str]:
    owners = [n for n, v in peers.items() if predicate(v)]
    plain = [o for o in owners if not peers[o][4]]
    if len(owners) == 1:
        return owners
    if len(plain) == 1:
        return plain
    if not plain:
        return owners
    return []


def _member_targets(me_name: str, me: tuple, peers: dict, mode: str) -> set[str]:
    """Files declaring the methods/fields `me` selects. `mode` combines: "typed" (receiver type resolved from
    the file and package function/method result types), "unexp" (unexported names, which can only belong to a
    type of this package) and "name" (any unique name, riskier because the receiver may be another package's)."""
    decl_names, _decl_typed, used_names, used_typed, known_names = me[5][:5] if len(me) > 5 and len(me[5]) >= 5 else ((),) * 5
    package_level: set[str] = set()
    func_ret: dict[str, set[str]] = {}
    method_ret: dict[tuple[str, str], set[str]] = {}
    embedded_in: dict[str, set[str]] = {}
    for v in peers.values():
        package_level.update(v[1])
        if len(v[5]) >= 8:
            for f, r in v[5][5]:
                func_ret.setdefault(f, set()).add(r)
            for k, r in v[5][6]:
                method_ret.setdefault(k, set()).add(r)
            for outer, inner in v[5][7]:
                embedded_in.setdefault(outer, set()).add(inner)

    def type_of(desc, depth: int = 0) -> str | None:
        if desc is None or depth > 6:
            return None
        if desc[0] == "T":
            return desc[1]
        if desc[0] == "call":
            results = func_ret.get(desc[1], set())
            return next(iter(results)) if len(results) == 1 else None
        if desc[0] == "mcall":
            base = type_of(desc[1], depth + 1)
            results = method_ret.get((base, desc[2]), set()) if base else set()
            return next(iter(results)) if len(results) == 1 else None
        return None

    out: set[str] = set()
    local_fields = me[5][8] if len(me[5]) >= 9 else frozenset()
    resolved_names: set[str] = set()  # selector names whose receiver type is known and decided by (type, name)
    for desc, name in used_typed:
        if type_of(desc):
            resolved_names.add(name)
    known_names = resolved_names | set(local_fields)
    if "typed" in mode:
        for desc, name in used_typed:
            type_name = type_of(desc)
            if type_name and "." not in type_name:
                # the type itself, else the types it embeds (promoted methods and fields), nearest level first
                level, seen = {type_name}, set()
                for _depth in range(4):
                    found: set[str] = set()
                    for candidate in level:
                        found.update(_owners(peers, lambda v, k=(candidate, name): k in v[5][1]))
                    if found:
                        out.update(o for o in found if o != me_name)
                        break
                    seen |= level
                    level = {e for c in level for e in embedded_in.get(c, ())} - seen
                    if not level:
                        break
    candidates = set(used_names) - set(known_names)
    if "name" not in mode:
        candidates = {n for n in candidates if n[:1].islower() or n[:1] == "_"} if "unexp" in mode else set()
    for name in candidates:
        if name in _COMMON_MEMBERS or name in package_level or name in decl_names:
            continue
        out.update(o for o in _owners(peers, lambda v, n=name: n in v[5][0]) if o != me_name)
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
    mode = os.environ.get("ALETHEORE_GO_MEMBERS", MEMBERS_MODE_DEFAULT)
    if mode and mode != "off":
        targets.update(_member_targets(file_path.name, me, peers, mode))
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
