import json
import math
import os
import posixpath
import re
import stat
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import lru_cache
from pathlib import Path

import tree_sitter_c as tsc
import tree_sitter_c_sharp as tscsharp
import tree_sitter_cpp as tscpp
import tree_sitter_go as tsgo
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_kotlin as tskotlin
import tree_sitter_php as tsphp
import tree_sitter_python as tspython
import tree_sitter_ruby as tsruby
import tree_sitter_rust as tsrust
import tree_sitter_swift as tsswift
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser, Tree

from aletheore.safe_io import read_text_lenient
from aletheore.repo_config import is_ignored
from aletheore.scanner.detect import IGNORED_DIRS, _nested_git_roots, git_visible_paths, visible_key

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())
GO_LANGUAGE = Language(tsgo.language())
RUST_LANGUAGE = Language(tsrust.language())
JAVA_LANGUAGE = Language(tsjava.language())
KOTLIN_LANGUAGE = Language(tskotlin.language())
RUBY_LANGUAGE = Language(tsruby.language())
PHP_LANGUAGE = Language(tsphp.language_php())
C_LANGUAGE = Language(tsc.language())
CPP_LANGUAGE = Language(tscpp.language())
CSHARP_LANGUAGE = Language(tscsharp.language())
SWIFT_LANGUAGE = Language(tsswift.language())

LANGUAGE_BY_EXTENSION = {
    ".py": ("python", PY_LANGUAGE),
    ".js": ("javascript", JS_LANGUAGE),
    ".jsx": ("javascript", JS_LANGUAGE),
    ".ts": ("typescript", TS_LANGUAGE),
    ".tsx": ("typescript", TSX_LANGUAGE),
    ".mjs": ("javascript", JS_LANGUAGE),
    ".cjs": ("javascript", JS_LANGUAGE),
    ".mts": ("typescript", TS_LANGUAGE),
    ".cts": ("typescript", TS_LANGUAGE),
    ".go": ("go", GO_LANGUAGE),
    ".rs": ("rust", RUST_LANGUAGE),
    ".java": ("java", JAVA_LANGUAGE),
    ".kt": ("kotlin", KOTLIN_LANGUAGE),
    ".kts": ("kotlin", KOTLIN_LANGUAGE),
    ".rb": ("ruby", RUBY_LANGUAGE),
    ".php": ("php", PHP_LANGUAGE),
    ".c": ("c", C_LANGUAGE),
    # Headers are ambiguous C-or-C++; the C++ grammar (a superset) parses valid C
    # too, so .h is treated as C++ rather than needing its own heuristic.
    ".h": ("cpp", CPP_LANGUAGE),
    ".hpp": ("cpp", CPP_LANGUAGE),
    ".hh": ("cpp", CPP_LANGUAGE),
    ".hxx": ("cpp", CPP_LANGUAGE),
    ".cxx": ("cpp", CPP_LANGUAGE),
    ".cpp": ("cpp", CPP_LANGUAGE),
    ".cc": ("cpp", CPP_LANGUAGE),
    ".cs": ("csharp", CSHARP_LANGUAGE),
    ".swift": ("swift", SWIFT_LANGUAGE),
}

# Extensions that are recognizable programming languages we don't yet have a grammar
# for. Only these count as "unparseable" coverage gaps. Everything else (assets, docs,
# configs, lock files, tool caches not already excluded by IGNORED_DIRS) was never
# source code and is skipped silently rather than reported as a gap - otherwise
# unparseable_files balloons with noise (a real repo scan turned up 19k+ .json files
# from an untracked cache directory before IGNORED_DIRS was widened, none of which
# were ever "unparseable source").
KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR = {
    ".m", ".mm", ".scala",
}

MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024


def _iter_source_files(repo_path: Path, ignored_paths: list[str] | None = None):
    # os.walk(followlinks=False) rather than Path.rglob("*") - a symlinked
    # directory in the tree would otherwise have its contents walked and
    # parsed as if they were part of this repo. followlinks only stops
    # descent into symlinked *directories* - a symlinked file sitting
    # directly in a real directory still needs its own symlink check.
    #
    # Every scan stage calls this, so it works on strings rather than Path
    # objects (pathlib's relative_to/parents/is_file were most of
    # map_api_endpoints' time on the Linux kernel): one lstat per file instead
    # of is_symlink + is_file, rel paths built from the walk instead of
    # relative_to, and a string prefix test for nested repos instead of
    # Path.parents. Same files, same order.
    nested_git_roots = [str(root) for root in _nested_git_roots(repo_path)]
    patterns = ignored_paths or []
    root_str = str(repo_path)
    root_prefix_len = len(root_str) + 1
    sep = os.sep
    visible = git_visible_paths(repo_path)
    found: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        if dirpath == root_str:
            rel_dir = "."
        else:
            rel_dir = dirpath[root_prefix_len:].replace(sep, "/") if dirpath.startswith(root_str + sep) else (
                Path(dirpath).relative_to(repo_path).as_posix()
            )
        dirnames[:] = [
            d
            for d in dirnames
            if d not in IGNORED_DIRS
            and not is_ignored(f"{rel_dir}/{d}" if rel_dir != "." else d, patterns)
            and (visible is None or visible_key(f"{rel_dir}/{d}" if rel_dir != "." else d) in visible[1])
        ]
        if any(dirpath == root or dirpath.startswith(root + sep) for root in nested_git_roots):
            dirnames[:] = []
            continue
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            try:
                mode = os.lstat(full).st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):
                continue  # symlinks (to files or dirs), dirs, sockets, fifos
            rel_path = f"{rel_dir}/{filename}" if rel_dir != "." else filename
            if is_ignored(rel_path, patterns):
                continue
            if visible is not None and visible_key(rel_path) not in visible[0]:
                continue  # ignored by a .gitignore (and not tracked)
            found.append((rel_path, full))
    if os.name == "nt":
        # Windows paths compare case-insensitively; keep pathlib's own order.
        yield from sorted(Path(full) for _rel_path, full in found)
        return
    # Same order as sorting the Path objects: pathlib compares part by part,
    # not as one string ("a/b" sorts before "a-b/x").
    found.sort(key=lambda item: item[0].split("/"))
    for _rel_path, full in found:
        yield Path(full)


def _rel(repo_path: Path, path: Path) -> str | None:
    """None when path resolves outside repo_path entirely - a relative
    import/include that climbs above the repo root (e.g. `#include
    "../../../../etc/passwd"`, or excess `super::` segments in Rust)
    resolves to a real file on disk that simply isn't part of this repo.
    Treated the same as an unresolved/external import rather than letting
    path.relative_to()'s ValueError crash the whole scan.
    """
    path_str = str(path)
    root_str = str(repo_path)
    if path_str.startswith(root_str + os.sep):
        return path_str[len(root_str) + 1:].replace(os.sep, "/")
    try:
        return path.relative_to(repo_path).as_posix()
    except ValueError:
        return None


def _params_text(source: bytes, enclosing_node: Node) -> str | None:
    """Raw source text of a function/method's parameter list, whitespace-
    normalized so purely cosmetic reformatting (wrapping a long parameter
    list across lines, extra spaces) doesn't look like a signature change.
    None for symbols with no parameter list at all (classes, interfaces).

    Every grammar checked (Python, JS/TS, Go, Rust, Java, Ruby, PHP, C#)
    names this field "parameters" directly on the function/method node,
    despite the node TYPE differing (parameters, formal_parameters,
    parameter_list, method_parameters) - confirmed empirically per
    language, not assumed. C/C++ is the one exception: the parameter list
    hangs off a nested function_declarator ("declarator" field) rather
    than the function_definition node directly. Kotlin is a second
    exception, of a different shape: tree-sitter-kotlin names no field on
    function_declaration at all except "name" (confirmed via
    field_name_for_child on every child) - parameters, return type, and
    body are purely positional. The type-name fallback below finds the
    parameter list by its node TYPE instead of a field lookup, which is
    safe to try unconditionally after the field lookup fails: no other
    grammar checked here has a node literally typed
    "function_value_parameters", so this can never accidentally match on
    the wrong grammar. Swift is a third exception, of yet another shape:
    its grammar has no wrapping parameter-list node at all - individual
    `parameter` nodes sit as direct children of function_declaration,
    separated by bare "(", ",", ")" tokens (confirmed empirically) - so
    the span is reconstructed from the outermost "(" and ")" tokens
    themselves rather than read off a single child node.

    Kotlin and Swift's enclosing node TYPE is confirmed identical - both
    grammars literally call it "function_declaration" - so the two
    fallbacks below must not gate on that type name (an earlier merge of
    these two independently-developed fallbacks did exactly that, which
    would have silently broken Kotlin's params by always taking Swift's
    branch first and returning early). Ordered by trying each fallback's
    own node shape in turn instead: Kotlin's function_value_parameters
    child is looked for first (absent on a Swift node, so harmless there),
    and only if that comes up empty does the bare "(...)" token scan run.
    """
    params_node = enclosing_node.child_by_field_name("parameters")
    if params_node is None:
        declarator = enclosing_node.child_by_field_name("declarator")
        if declarator is not None:
            params_node = declarator.child_by_field_name("parameters")
    if params_node is None:
        params_node = next(
            (c for c in enclosing_node.children if c.type == "function_value_parameters"), None
        )
    if params_node is None and enclosing_node.type == "function_declaration":
        open_paren = next((c for c in enclosing_node.children if c.type == "("), None)
        close_paren = next((c for c in reversed(enclosing_node.children) if c.type == ")"), None)
        if open_paren is not None and close_paren is not None:
            raw = source[open_paren.start_byte:close_paren.end_byte].decode(errors="ignore")
            return " ".join(raw.split())
        return None
    if params_node is None:
        return None
    raw = source[params_node.start_byte:params_node.end_byte].decode(errors="ignore")
    return " ".join(raw.split())


def _is_public_symbol(name: str, language: str) -> bool:
    """Best-effort public/private classification from naming convention
    alone - only for languages where visibility genuinely IS a naming
    convention, not a keyword or a separate statement.

    Deliberately conservative: languages whose visibility is a modifier
    keyword (private/public in Java, C#, C++) rather than a naming
    convention are NOT classified from the name here - _symbol_entry has
    no modifier text to inspect without a second, per-call-site AST
    lookup, out of scope for this pass. Those languages default every
    symbol to public; a later pass could read the modifier node
    directly, the way Rust's visibility_modifier check already does
    (Rust is NOT naming-convention-based either - "pub" is a keyword -
    but its check is cheap enough, one child-node scan, that it's done
    directly at _extract_rust's call sites instead of being deferred
    like Java/C#/C++).

    Ruby is deliberately excluded too, for a different reason: real Ruby
    visibility is set by a `private`/`protected` *statement* earlier in
    the class body, not by how a method is named - a leading-underscore
    check would misclassify idiomatically-named-but-actually-private
    methods as public far more often than it would help, which is worse
    than the honest "unknown, default public" every keyword-based
    language already gets.
    """
    if language == "go":
        return name[:1].isupper()
    if language == "python":
        return not name.startswith("_")
    return True


_FUNCTION_LIKE_NODE_TYPES = frozenset({
    # Named functions/methods - a real declaration with its own name.
    "function_definition", "function_declaration", "method_declaration",
    "function_item", "function_signature_item", "constructor_declaration", "method",
    # Anonymous closures - just as much a "nested, not top-level" boundary
    # as a named function, but easy to miss: a symbol nested only inside
    # one of these (no named function anywhere further up) still needs
    # catching. Confirmed empirically against each grammar rather than
    # assumed - e.g. `const outer = () => { function inner() {} }` in
    # JS/TS has no function_declaration/method ancestor for `inner` at
    # all, only arrow_function, so this class of gap is real and was
    # initially missed entirely for every language that has anonymous
    # closures (every one of these ten except Go, where a named function
    # declaration nested in another function's body is a parse error -
    # only func_literal, always anonymous, can nest at all - and Python,
    # where a lambda's body is a single expression and can never contain
    # a def in the first place).
    "arrow_function", "function_expression", "generator_function",  # JS/TS
    "closure_expression",  # Rust
    "lambda_expression",  # Java, C++, C#
    "anonymous_method_expression",  # C# (older `delegate(){}` form)
    "do_block", "lambda",  # Ruby: `do...end`, `->{...}`
    "anonymous_function",  # PHP: `function(){}` (PHP's `fn() =>` is the
    # same "arrow_function" type name already listed above for JS/TS -
    # no separate entry needed, one shared set, no cross-grammar collision)
    "lambda_literal",  # Kotlin AND Swift's closure node - both grammars use
    # this exact name (confirmed empirically, not assumed - checked no
    # other already-supported grammar uses it first, the same way "block"
    # below was checked and excluded for colliding with Python's).
    #
    # Deliberately NOT here: Ruby's plain `{...}` block, node type
    # "block" - confirmed empirically to be the exact same type name
    # Python's own grammar uses for a class's body wrapper
    # (class_definition -> block -> function_definition), so adding it
    # broke "a method inside a class is not nested" for every Python
    # file. One shared set only works because these strings don't
    # collide across grammars (see the docstring below) - "block" is the
    # one real exception, so Ruby's `{...}` form (as opposed to
    # `do...end`, which already works via do_block) stays uncovered
    # rather than fixing one gap by opening a worse one.
})


def _is_nested_in_function(node: Node) -> bool:
    """Whether `node` (a function/class/type node about to become a
    symbol) sits inside another function/method's BODY, named or
    anonymous - a closure, not a real top-level or class-level symbol.

    Caught via dogfooding `aletheore docs` against this repo's own
    scanner code: the `walk`/`text` helper functions defined inside
    every `_extract_*` function were being extracted as top-level
    public symbols, since nothing previously distinguished "top-level
    function" from "closure defined inside another function." A method
    inside a class correctly does NOT count as nested here - only
    class/namespace-shaped ancestors sit between a method and the module
    root, none of which are in _FUNCTION_LIKE_NODE_TYPES, so the walk
    passes straight through them to the root without matching.

    One shared node-type set works across every language rather than a
    set per language: each grammar's function/method node type strings
    don't collide with another grammar's differently-shaped node of the
    same name, since a given file is only ever parsed with one grammar.
    """
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in _FUNCTION_LIKE_NODE_TYPES:
            return True
        ancestor = ancestor.parent
    return False


# Fields a symbol entry only carries when they say something: params,
# docstring and return_type when set, is_pure_declaration when True. On the
# Linux kernel's 2.9M #define constants these were always null/False and made
# up ~45% of every entry (most of air.json). Every reader uses .get() with the
# same default, so leaving them out reads the same (EVIDENCE_VERSION 0.8.1).
_OMIT_WHEN_EMPTY = {"params": None, "docstring": None, "return_type": None, "is_pure_declaration": False}


def _compact_symbol(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key not in _OMIT_WHEN_EMPTY or value != _OMIT_WHEN_EMPTY[key]}


def _symbol_entry(
    source: bytes,
    name_node: Node,
    enclosing_node: Node,
    docstring: str | None = None,
    return_type: str | None = None,
    is_public: bool = True,
    is_pure_declaration: bool = False,
) -> dict:
    return _compact_symbol({
        "name": source[name_node.start_byte:name_node.end_byte].decode(errors="ignore"),
        "start_line": enclosing_node.start_point[0] + 1,
        "end_line": enclosing_node.end_point[0] + 1,
        "params": _params_text(source, enclosing_node),
        "docstring": docstring,
        "return_type": return_type,
        "is_public": is_public,
        # True only for a symbol that is ITSELF an interface/annotation-type,
        # regardless of what else lives in the same file. Separate from the
        # file-level is_declaration_only in build_chunks: that flag answers
        # "is this whole file pure contract", which is correctly False for a
        # file like AutoMapper's MapperConfiguration.cs that pairs a small
        # embedded interface with a large concrete class - but the embedded
        # interface's OWN chunk is still pure contract on its own terms, and
        # measured to independently attract abstractly-worded queries the
        # same way a dedicated declaration-only file does (AutoMapper cs02/
        # cs09/cs10, gson java06 - see build_chunks for the per-chunk use).
        "is_pure_declaration": is_pure_declaration,
    })


_DOCSTRING_QUOTE_PREFIXES = ('"""', "'''", '"', "'")


def _strip_docstring_quotes(raw: str) -> str:
    text = raw.strip()
    # Strip a leading string-prefix letter (r/u/b/f, case-insensitive) tree-sitter
    # includes as part of the "string" node's own text, before the quote itself.
    if text[:1].isalpha():
        text = text[1:]
    for quote in _DOCSTRING_QUOTE_PREFIXES:
        if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
            text = text[len(quote):-len(quote)]
            break
    return text.strip()


def _python_docstring(source: bytes, enclosing_node: Node) -> str | None:
    """The docstring-as-first-statement convention: a function/class body
    whose first statement is a bare string expression. Confirmed empirically
    (not assumed) that tree-sitter-python exposes this as body.children[0]
    being an expression_statement wrapping a string node.
    """
    body = enclosing_node.child_by_field_name("body")
    if body is None or not body.children:
        return None
    first = body.children[0]
    if first.type != "expression_statement" or not first.children:
        return None
    string_node = first.children[0]
    if string_node.type != "string":
        return None
    raw = source[string_node.start_byte:string_node.end_byte].decode(errors="ignore")
    return _strip_docstring_quotes(raw) or None


def _python_return_type(source: bytes, enclosing_node: Node) -> str | None:
    """function_definition's own "return_type" field (confirmed empirically -
    node type "type", text with no leading "->"). Classes have no such field.
    """
    return_type_node = enclosing_node.child_by_field_name("return_type")
    if return_type_node is None:
        return None
    return source[return_type_node.start_byte:return_type_node.end_byte].decode(errors="ignore").strip()


def _extract_python(
    node: Node, source: bytes
) -> tuple[list[str], list[tuple[str, list[str]]], list[dict], list[dict], list[dict]]:
    """Return plain imports, from-imports, functions, classes, and constants.

    "Constants" are module-level name bindings - `X = ...` and `X: T = ...` at
    the top level of the file. They are extracted because a file can define a
    substantial public API without a single `def` or `class`: Flask's
    `signals.py` is 17 lines of `template_rendered = _signals.signal(...)`
    exporting ten public names, and with only functions/classes recorded it
    looked like an empty module to every consumer of this evidence - no wiki
    page, and nothing for the search index to embed. The same shape covers
    settings modules, registries, enums and route tables.
    """
    plain_imports: list[str] = []
    from_imports: list[tuple[str, list[str]]] = []
    functions: list[dict] = []
    classes: list[dict] = []
    constants: list[dict] = []

    def walk(root: Node):
        # Iterative, not recursive - a deeply-nested real-world AST (confirmed on
        # Linux kernel C source) can exceed Python's recursion limit and crash the
        # whole scan. reversed(children) before pushing preserves the same
        # left-to-right visiting order a recursive walk would produce.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "import_from_statement":
                module_node = n.child_by_field_name("module_name")
                module_name = (
                    source[module_node.start_byte:module_node.end_byte].decode(errors="ignore")
                    if module_node is not None
                    else ""
                )
                names: list[str] = []
                for child in n.named_children:
                    if child == module_node:
                        continue
                    if child.type in ("dotted_name", "identifier"):
                        names.append(source[child.start_byte:child.end_byte].decode(errors="ignore"))
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        if name_node is not None:
                            names.append(source[name_node.start_byte:name_node.end_byte].decode(errors="ignore"))
                from_imports.append((module_name, names))
            elif n.type == "import_statement":
                for child in n.named_children:
                    if child.type == "dotted_name":
                        plain_imports.append(source[child.start_byte:child.end_byte].decode(errors="ignore"))
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        if name_node is not None:
                            plain_imports.append(
                                source[name_node.start_byte:name_node.end_byte].decode(errors="ignore")
                            )
            elif n.type == "function_definition":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    name = source[name_node.start_byte:name_node.end_byte].decode(errors="ignore")
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_python_docstring(source, n),
                        return_type=_python_return_type(source, n),
                        is_public=_is_public_symbol(name, "python") and not _is_nested_in_function(n),
                    ))
            elif n.type == "class_definition":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    name = source[name_node.start_byte:name_node.end_byte].decode(errors="ignore")
                    classes.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_python_docstring(source, n),
                        is_public=_is_public_symbol(name, "python") and not _is_nested_in_function(n),
                    ))
            elif n.type == "expression_statement" and n.parent is not None and n.parent.type == "module":
                # Module level only. An assignment inside a function or a class
                # body is a local or an attribute, not a module export, and
                # recording those would bury the real API in noise.
                for child in n.named_children:
                    if child.type != "assignment":
                        continue
                    target = child.child_by_field_name("left")
                    # Only plain `NAME = ...`. Tuple unpacking and subscript or
                    # attribute targets are deliberately skipped: their "name"
                    # is not a single identifier a reader could look up.
                    if target is None or target.type != "identifier":
                        continue
                    name = source[target.start_byte:target.end_byte].decode(errors="ignore")
                    constants.append(_symbol_entry(
                        source, target, n,
                        is_public=_is_public_symbol(name, "python"),
                    ))
            stack.extend(reversed(n.children))

    walk(node)
    return plain_imports, from_imports, functions, classes, constants


def _extract_module_constants(node: Node, source: bytes, language: str) -> list[dict]:
    """Top-level named constants, for every language except Python.

    Python is handled inside `_extract_python`, which already had the walk.
    This is a separate pass rather than a change to eight extractor signatures,
    because the value is the same in each language and the risk of reworking
    every extractor is not.

    Why this exists: a file can export a substantial public API without a single
    function or class. Flask's `signals.py` is ten module-level assignments and
    was invisible to every consumer of the evidence. The same shape is
    everywhere - `export const` in JS/TS, Go `const` blocks, Rust `pub const`,
    Java `public static final`, C `#define`, C# `const`.

    Only definitions a reader could look up by name are recorded: destructuring,
    computed names and inline `mod`-style bodies are skipped.
    """
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def add(name_node: Node, enclosing: Node, public: bool = True) -> None:
        name = source[name_node.start_byte:name_node.end_byte].decode(errors="ignore").strip()
        if not name or not name.replace("_", "").replace("$", "").isalnum():
            return
        key = (name, name_node.start_point[0] + 1)
        if key in seen:
            return
        seen.add(key)
        out.append(_symbol_entry(source, name_node, enclosing, is_public=public))

    def is_top_level(n: Node) -> bool:
        """Module scope, allowing the wrappers each language puts around it."""
        parent = n.parent
        hops = 0
        while parent is not None and hops < 4:
            if parent.type in (
                "source_file", "program", "translation_unit", "compilation_unit",
                "module", "namespace_definition", "file_scoped_namespace_declaration",
                "declaration_list", "namespace_declaration",
            ):
                return True
            if parent.type in ("export_statement", "expression_statement", "const_block"):
                parent = parent.parent
                hops += 1
                continue
            return False
        return parent is not None

    def has_modifier(n: Node, *words: str) -> bool:
        text = source[n.start_byte:min(n.end_byte, n.start_byte + 400)].decode(errors="ignore")
        head = text.split("=")[0]
        # Word-boundary match, not substring: plain `w in head` misclassifies
        # any declaration whose identifier merely contains a modifier word,
        # e.g. `int construct_id = 5;` or `Handler constants_registry = ...;`
        # both false-positive on "const".
        return all(re.search(rf"\b{re.escape(w)}\b", head) for w in words)

    stack = [node]
    while stack:
        n = stack.pop()
        t = n.type

        if language in ("javascript", "typescript"):
            # `const X = ...` / `export const X = ...`
            if t == "lexical_declaration" and is_top_level(n) and source[
                n.start_byte:n.start_byte + 5
            ] == b"const":
                for child in n.named_children:
                    if child.type == "variable_declarator":
                        nm = child.child_by_field_name("name")
                        if nm is not None and nm.type == "identifier":
                            add(nm, child)
        elif language == "go":
            if t in ("const_declaration", "var_declaration") and is_top_level(n):
                for spec in n.named_children:
                    if spec.type in ("const_spec", "var_spec"):
                        nm = spec.child_by_field_name("name")
                        if nm is not None:
                            add(nm, spec, public=nm.text[:1].isupper() if nm.text else True)
        elif language == "rust":
            if t in ("const_item", "static_item"):
                nm = n.child_by_field_name("name")
                if nm is not None:
                    add(nm, n, public=any(c.type == "visibility_modifier" for c in n.children))
        elif language == "kotlin":
            # tree-sitter-kotlin uses one node type, property_declaration, for
            # every top-level val/var/const val binding - no separate
            # "const_declaration" the way Go/Rust have. Confirmed empirically:
            # `const val MAX_TASKS = 100` and a plain `val x = 1` at file scope
            # both parse as property_declaration, distinguished only by
            # whether `modifiers` contains a property_modifier "const" - not
            # filtered on here, matching Go's broad const-or-var precedent
            # rather than Rust's narrower const/static-only one, since a
            # Kotlin file-scope `val` is itself the idiomatic constant shape,
            # with or without the explicit `const` keyword.
            if t == "property_declaration" and is_top_level(n):
                var_decl = next((c for c in n.children if c.type == "variable_declaration"), None)
                if var_decl is not None:
                    # No named "name" field here either (see _kotlin_return_
                    # type's docstring on this grammar's sparse field names) -
                    # variable_declaration's only child in the common case is
                    # the identifier itself.
                    nm = next((c for c in var_decl.children if c.type == "identifier"), None)
                    if nm is not None:
                        modifiers = next((c for c in n.children if c.type == "modifiers"), None)
                        public = (
                            not ({"private", "protected", "internal"} & _kotlin_modifier_keywords(modifiers))
                            if modifiers is not None else True
                        )
                        add(nm, n, public=public)
        elif language in ("java", "csharp"):
            # Class members: `static final` / `const` are the module-constant
            # equivalent in languages with no file-level scope.
            #
            # Deliberately NOT every field: an earlier version of this branch
            # extracted every Java field regardless of modifier (1,075 of them
            # in google/gson) to give private/instance fields the same
            # by-name lookup as constants get. Measured, not shipped: every
            # one of those 1,075 chunks was provably unreachable anyway - see
            # build_chunks below, "constants" are only chunked for a file with
            # no functions or classes, and every real Java file with fields
            # also has at least one method - so the change was pure dead
            # weight with zero effect on the index, in either direction.
            if t == "field_declaration" and (
                has_modifier(n, "static", "final") or has_modifier(n, "const")
            ):
                # Java puts variable_declarator directly under field_declaration;
                # C# wraps it in a variable_declaration first.
                candidates = list(n.named_children)
                for child in list(candidates):
                    if child.type == "variable_declaration":
                        candidates.extend(child.named_children)
                for child in candidates:
                    if child.type == "variable_declarator":
                        nm = child.child_by_field_name("name") or (
                            child.named_children[0] if child.named_children else None
                        )
                        if nm is not None and nm.type == "identifier":
                            add(nm, child, public=has_modifier(n, "public"))
        elif language == "ruby":
            # Ruby constants are capitalised assignments - idiomatically
            # declared inside a module or class body (Sinatra::Base's
            # DROP_BODY_RESPONSES, not top-level: a real repo scan found 10
            # constants indented inside module/class bodies and 0 at file
            # scope), not only at true top level. A constant assigned
            # inside a def body is a method-local, not part of the type's
            # API surface, and must stay excluded - confirmed empirically
            # via spike that tree-sitter-ruby wraps both shapes the same
            # way (assignment -> body_statement), so it's the
            # body_statement's own parent that tells them apart: class or
            # module for a type's own body, method for a def's.
            if t == "assignment":
                lhs = n.child_by_field_name("left")
                if lhs is not None and lhs.type == "constant":
                    # Walk up through Ruby's conditional/exception-handling
                    # wrapper nodes (if/then/elsif/else/unless/case/when/
                    # begin/rescue/ensure) to find whether this assignment
                    # ultimately sits in a class/module's own body_statement,
                    # not just checking one level up. Real bug this fixes: a
                    # constant gated on a RUBY_VERSION/platform check, or
                    # wrapped in begin/rescue for a defensive fallback, is
                    # exactly as idiomatic a class-body constant as the flat
                    # case above - confirmed live against tree-sitter-ruby's
                    # actual parse tree - but its immediate parent is "then"
                    # or "begin"/"rescue", never "body_statement" directly,
                    # so it was silently invisible. A constant nested the
                    # same way inside a def's body still correctly stays
                    # excluded: the walk stops at the first body_statement
                    # it reaches (a method's own, not a skippable wrapper),
                    # and that one's parent is "method", not class/module.
                    parent = n.parent
                    hops = 0
                    while parent is not None and hops < 6 and parent.type in (
                        "then", "else", "elsif", "unless", "if", "case", "when",
                        "begin", "rescue", "ensure", "rescue_modifier",
                    ):
                        parent = parent.parent
                        hops += 1
                    in_type_body = (
                        parent is not None
                        and parent.type == "body_statement"
                        and parent.parent is not None
                        and parent.parent.type in ("class", "module")
                    )
                    if in_type_body or is_top_level(n):
                        add(lhs, n)
        elif language == "swift":
            # Only immutable ("let") bindings, at module scope or as a type's
            # `static let` - matching the same deliberate restraint as Java's
            # `static final`-only branch above (a mutable `static var` is a
            # class-level field, not really a "constant", and top-level `var`
            # is script-local mutable state, not a lookup-by-name API
            # surface). Confirmed empirically: tree-sitter-swift always wraps
            # a name in "property_declaration -> pattern -> simple_identifier"
            # for both shapes.
            if t == "property_declaration":
                binding = next((c for c in n.children if c.type == "value_binding_pattern"), None)
                is_let = binding is not None and any(c.type == "let" for c in binding.children)
                modifiers = next((c for c in n.children if c.type == "modifiers"), None)
                is_static = modifiers is not None and any(
                    mc.type == "property_modifier" and any(c.type == "static" for c in mc.children)
                    for mc in modifiers.children
                )
                is_public = modifiers is not None and any(
                    mc.type == "visibility_modifier" and any(c.type in ("public", "open") for c in mc.children)
                    for mc in modifiers.children
                )
                if is_let and (is_static or is_top_level(n)):
                    pattern = next((c for c in n.children if c.type == "pattern"), None)
                    nm = next(
                        (c for c in pattern.children if c.type == "simple_identifier"), None
                    ) if pattern is not None else None
                    if nm is not None:
                        add(nm, n, public=is_public)
        elif language == "php":
            if t == "const_declaration":
                for child in n.named_children:
                    if child.type == "const_element":
                        nm = child.named_children[0] if child.named_children else None
                        if nm is not None:
                            add(nm, child)
        elif language in ("c", "cpp"):
            if t == "preproc_def":
                nm = n.child_by_field_name("name")
                if nm is not None:
                    add(nm, n)
            elif t == "declaration" and is_top_level(n) and has_modifier(n, "const"):
                # `const int X = 42;` at file scope - the C++ idiom that replaced
                # #define, and the only constant form in a header-only library
                # that avoids the preprocessor.
                for child in n.named_children:
                    target = child
                    if child.type == "init_declarator":
                        target = child.child_by_field_name("declarator") or child
                    if target.type == "identifier":
                        add(target, n)
            if t == "compound_statement" and b"define" not in source[n.start_byte:n.end_byte]:
                # A function body can't hold a top-level declaration
                # (is_top_level fails on its children), so the only thing worth
                # finding in one is a #define. Skipping bodies without one keeps
                # the output identical and skips most of the tree: this pass was
                # ~37% of module-graph time on the Linux kernel. Matches
                # "define", not "#define": `#  define X` is valid C (fmt uses it).
                continue

        stack.extend(reversed(n.children))

    out.sort(key=lambda e: e["start_line"])
    return out


def _leading_block_comment(
    enclosing_node: Node, source: bytes, marker: str = "/**", comment_type: str = "comment"
) -> str | None:
    """Text of a "/** ... */"-style comment node immediately preceding
    enclosing_node - checking the node's own previous sibling first, then
    (for a declaration wrapped in an export/modifier statement, whose own
    prev_sibling is the "export" keyword, not the comment) the parent's
    previous sibling. Confirmed empirically for JS/TS via spike: an
    "export function f() {}" nests function_declaration inside
    export_statement, and the comment sits before export_statement, not
    before the nested function_declaration. Java has no such wrapper (a
    method_declaration's own prev_sibling is the block_comment directly,
    also confirmed empirically) but checking the parent too is harmless
    there since it simply won't match.

    `comment_type` differs per grammar - JS/TS calls every comment
    "comment" and distinguishes "/** */" from "//" only by text; Java's
    grammar gives block comments their own node type, "block_comment",
    entirely separate from line comments.

    Any matching-type comment node is accepted whose raw text starts with
    `marker`, so a plain "// line comment" is correctly treated as not a
    doc comment.
    """
    candidates = [enclosing_node.prev_sibling]
    if enclosing_node.parent is not None:
        candidates.append(enclosing_node.parent.prev_sibling)
    for candidate in candidates:
        if candidate is not None and candidate.type == comment_type:
            raw = source[candidate.start_byte:candidate.end_byte].decode(errors="ignore")
            if raw.startswith(marker):
                return raw
    return None


def _strip_jsdoc_stars(raw: str) -> str | None:
    """"/** ... */" -> its text, with the comment delimiters and each
    line's leading "* " stripped.
    """
    text = raw.strip()
    if text.startswith("/**"):
        text = text[3:]
    elif text.startswith("/*"):
        text = text[2:]
    if text.endswith("*/"):
        text = text[:-2]
    lines = [line.strip().lstrip("*").strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned or None


def _ts_return_type(source: bytes, enclosing_node: Node) -> str | None:
    """TypeScript's own "return_type" field (node type "type_annotation",
    raw text including a leading ": " - confirmed empirically via spike).
    Always None when parsed with the plain JS grammar, since the field
    simply doesn't exist there - safe to call unconditionally for both.
    """
    node = enclosing_node.child_by_field_name("return_type")
    if node is None:
        return None
    text = source[node.start_byte:node.end_byte].decode(errors="ignore").strip()
    return text[1:].strip() if text.startswith(":") else text


def _extract_javascript(node: Node, source: bytes) -> tuple[list[str], list[dict], list[dict]]:
    imports: list[str] = []
    functions: list[dict] = []
    classes: list[dict] = []

    def string_literal(n: Node | None) -> str | None:
        if n is None or n.type != "string":
            return None
        raw = source[n.start_byte:n.end_byte].decode(errors="ignore")
        return raw[1:-1] if len(raw) >= 2 and raw[0] in "'\"" else None

    def walk(root: Node):
        # Iterative, not recursive - see _extract_python's walk for why.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "import_statement":
                source_node = n.child_by_field_name("source")
                if source_node is not None:
                    raw = source[source_node.start_byte:source_node.end_byte].decode(errors="ignore")
                    imports.append(raw.strip("'\""))
                else:
                    # Import-equals ("import foo = require('./foo');") is
                    # still an import_statement node, but has no "source"
                    # field - the path lives inside a nested
                    # import_require_clause instead, a declaration form
                    # distinct from (and not caught by) the
                    # call_expression-based require() handling below.
                    # Confirmed present in real code (3 occurrences in the
                    # axios repo) - without this, the whole statement
                    # silently produced zero imports (audit finding 32).
                    require_clause = next(
                        (c for c in n.children if c.type == "import_require_clause"), None
                    )
                    if require_clause is not None:
                        string_node = next(
                            (c for c in require_clause.children if c.type == "string"), None
                        )
                        specifier = string_literal(string_node)
                        if specifier is not None:
                            imports.append(specifier)
            elif n.type == "export_statement":
                # Re-export barrels ("export { x } from './x'", "export * from
                # './x'") get their own export_statement node type with a
                # "source" field - a walk that only matches import_statement
                # never sees them, so a file only ever referenced through one
                # is invisible to imported_by/dead-code despite being live.
                specifier = string_literal(n.child_by_field_name("source"))
                if specifier is not None:
                    imports.append(specifier)
            elif n.type == "call_expression":
                # CommonJS `require('./x')` and dynamic `import('./x')`. Handling
                # only ESM `import` left the dependency graph of every CommonJS
                # codebase completely empty - measured on expressjs/express: 141
                # modules, 0 resolved imports, so community detection saw no
                # edges and emitted one cluster per file. CommonJS is still most
                # of npm, so this is not a legacy edge case. Dynamic import()'s
                # callee is its own "import" node type (not an identifier), same
                # tree-sitter shape as the "import" keyword in a static
                # import_statement.
                fn = n.child_by_field_name("function")
                is_require = (
                    fn is not None
                    and fn.type == "identifier"
                    and source[fn.start_byte:fn.end_byte] == b"require"
                )
                is_dynamic_import = fn is not None and fn.type == "import"
                if is_require or is_dynamic_import:
                    args = n.child_by_field_name("arguments")
                    if args is not None:
                        for arg in args.named_children:
                            # A backtick literal with no ${} is as static as a quoted one: import(`./x`).
                            if arg.type == "string" or (
                                arg.type == "template_string"
                                and not any(c.type == "template_substitution" for c in arg.children)
                            ):
                                spec = source[arg.start_byte:arg.end_byte].decode(errors="ignore").strip("'\"`")
                                if spec:
                                    imports.append(spec)
                                break
            elif n.type == "function_declaration":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    raw_doc = _leading_block_comment(n, source)
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        return_type=_ts_return_type(source, n),
                        is_public=not _is_nested_in_function(n),
                    ))
            elif n.type == "class_declaration":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    raw_doc = _leading_block_comment(n, source)
                    classes.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        is_public=not _is_nested_in_function(n),
                    ))
            elif n.type in ("interface_declaration", "type_alias_declaration"):
                # In TypeScript these ARE the public API surface, especially
                # for a type-centric library: colinhacks/zod has 972
                # `export type`/`export interface` declarations in its core
                # src, and 39 files with zero other symbols contained 210 of
                # them - entirely invisible to everything downstream before
                # this. Folded into `classes` rather than a new symbol group,
                # the same way Java's and C#'s own interface_declaration
                # already is - a type declaration is structurally the same
                # kind of thing as a class here. No special-casing needed for
                # a namespace/module body (`export namespace Foo { ... }`):
                # this walk is unconditional over every child, so a
                # type_alias_declaration nested inside one is visited the
                # same as a top-level one.
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    raw_doc = _leading_block_comment(n, source)
                    classes.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        is_public=not _is_nested_in_function(n),
                    ))
            elif n.type in ("variable_declarator", "assignment_expression"):
                # Functions assigned to a name rather than declared. Counting only
                # `function f(){}` and `class C{}` left most of a real CommonJS
                # codebase with no symbols at all: expressjs/express defines its
                # whole surface as `app.use = function use(fn) {...}`, and 103 of
                # its 141 files came out empty, so the search index had nothing to
                # embed but a fallback chunk. Covers `const f = () => {}`,
                # `exports.f = function(){}` and `Foo.prototype.bar = function(){}`.
                value = n.child_by_field_name("value") if n.type == "variable_declarator" else n.child_by_field_name("right")
                if value is not None and value.type in (
                    "function_expression", "arrow_function", "function", "generator_function"
                ):
                    target = (
                        n.child_by_field_name("name") if n.type == "variable_declarator"
                        else n.child_by_field_name("left")
                    )
                    if target is not None:
                        # `a.b.c = fn` is named by its last segment; a bare
                        # identifier by itself.
                        name_node = target
                        if target.type == "member_expression":
                            name_node = target.child_by_field_name("property") or target
                        if name_node.type in ("identifier", "property_identifier"):
                            functions.append(_symbol_entry(
                                source, name_node, n,
                                docstring=_strip_jsdoc_stars(_leading_block_comment(n, source) or "") or None,
                                return_type=_ts_return_type(source, value),
                                is_public=not _is_nested_in_function(n),
                            ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, classes


def _leading_go_doc_comment(source: bytes, enclosing_node: Node) -> str | None:
    """Go's doc-comment convention: one or more contiguous "//" line
    comments immediately above the declaration, with no blank line in
    between (a blank line makes it "just a comment", not documentation -
    the same rule `go doc` itself follows). tree-sitter-go emits each line
    as its own separate "comment" sibling node (confirmed empirically),
    so this walks backward collecting contiguous ones rather than
    expecting one merged node.

    A type_spec ("type Foo struct {...}") nests inside a wrapping
    type_declaration the same way Python's export_statement wraps a
    function_declaration - the comment sits before the wrapper, not
    before type_spec itself (also confirmed empirically) - so the walk
    starts from the parent when the node's own prev_sibling isn't a
    comment.
    """
    anchor = enclosing_node
    node = anchor.prev_sibling
    if (node is None or node.type != "comment") and anchor.parent is not None:
        anchor = anchor.parent
        node = anchor.prev_sibling

    lines: list[str] = []
    expected_end_row = anchor.start_point[0] - 1
    while node is not None and node.type == "comment" and node.end_point[0] == expected_end_row:
        raw = source[node.start_byte:node.end_byte].decode(errors="ignore").strip()
        if raw.startswith("//"):
            raw = raw[2:].strip()
        elif raw.startswith("/*") and raw.endswith("*/"):
            raw = raw[2:-2].strip()
        lines.append(raw)
        expected_end_row = node.start_point[0] - 1
        node = node.prev_sibling

    if not lines:
        return None
    lines.reverse()
    return "\n".join(lines) or None


def _extract_go(node: Node, source: bytes) -> tuple[list[str], list[dict], list[dict]]:
    """Return raw import path strings, function/method names, and type names."""
    imports: list[str] = []
    functions: list[dict] = []
    types: list[dict] = []

    def string_content(n: Node) -> str | None:
        for child in n.children:
            if child.type == "interpreted_string_literal_content":
                return source[child.start_byte:child.end_byte].decode(errors="ignore")
        return None

    def walk(root: Node):
        # Iterative, not recursive - see _extract_python's walk for why.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "import_spec":
                # import_spec is either just a string literal ("fmt") or an alias followed
                # by one ("svc2 \"pkg/path\"") - the alias identifier itself is never the
                # thing we resolve, only the string literal's content is a real import path.
                for child in n.children:
                    if child.type == "interpreted_string_literal":
                        content = string_content(child)
                        if content is not None:
                            imports.append(content)
            elif n.type in ("function_declaration", "method_declaration"):
                name_node = n.child_by_field_name("name")
                if name_node is None:
                    # method_declaration names the method via a field_identifier child
                    # rather than a "name"-labeled field.
                    for child in n.children:
                        if child.type == "field_identifier":
                            name_node = child
                            break
                if name_node is not None:
                    name = source[name_node.start_byte:name_node.end_byte].decode(errors="ignore")
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_go_doc_comment(source, n),
                        is_public=_is_public_symbol(name, "go") and not _is_nested_in_function(n),
                    ))
            elif n.type == "type_spec":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    name = source[name_node.start_byte:name_node.end_byte].decode(errors="ignore")
                    types.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_go_doc_comment(source, n),
                        is_public=_is_public_symbol(name, "go") and not _is_nested_in_function(n),
                    ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, types


def _load_go_module_prefix(repo_path: Path) -> str | None:
    go_mod = repo_path / "go.mod"
    if not go_mod.exists():
        return None
    for line in read_text_lenient(go_mod).splitlines():
        line = line.strip()
        if line.startswith("module "):
            return line[len("module "):].strip()
    return None


def _go_directives(text: str, keyword: str) -> list[list[str]]:
    """Token lists of every `keyword` directive in a go.mod / go.work file, single-line
    (`use ./a`) and block (`use ( ... )`) form alike, comments and quotes removed."""
    directives: list[list[str]] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if in_block:
            if line.startswith(")"):
                in_block = False
            else:
                directives.append([t.strip('"`') for t in line.split()])
            continue
        parts = line.split()
        if parts[0] != keyword:
            continue
        rest = line[len(keyword):].strip()
        if rest == "(":
            in_block = True
        elif rest:
            directives.append([t.strip('"`') for t in rest.split()])
    return directives


def _go_local_dir(repo_path: Path, base_rel_dir: str, target: str) -> str | None:
    """Repo-relative POSIX dir for a local path in a go.work `use` / go.mod `replace`
    (relative to the file that names it), or None when it points outside the repo."""
    if not (target.startswith(("./", "../", ".\\", "..\\")) or target in (".", "..")):
        return None
    joined = posixpath.normpath(posixpath.join(base_rel_dir, target.replace("\\", "/")))
    if joined == ".." or joined.startswith("../") or posixpath.isabs(joined):
        return None
    return joined


def _load_go_modules(repo_path: Path) -> tuple[tuple, ...]:
    """Every Go module in the repository as (module path, repo-relative dir),
    longest module path first. A local `replace` is a third element, the directory
    whose files it applies to ("." for go.work and the root go.mod), because Go applies
    a go.mod's replaces to that module only. A repo can hold several modules (a go.mod in a
    subdirectory, several side by side, or a go.work listing them); go.mod
    files are found at any depth, skipping vendored and ignored directories.
    Directories a go.work `use`s are added even when discovery skipped them, and
    a `replace` that points a module path at a local directory maps that path to
    the directory (the imports name the replaced path)."""
    from aletheore.manifests import discover_manifests

    modules: dict[str, str] = {}
    replaces: dict[tuple[str, str], str] = {}
    go_files: list[tuple[str, str]] = []  # (repo-relative dir, "go.mod" | "go.work")

    def add(module_path: str, rel_dir: str) -> None:
        modules.setdefault(module_path.strip('"'), rel_dir)

    discovery = discover_manifests(repo_path)
    for rel_dir in discovery["dirs"]:
        names = discovery["files"][rel_dir]
        directory = repo_path if rel_dir == "." else repo_path / rel_dir
        if "go.mod" in names:
            go_files.append((rel_dir, "go.mod"))
            module_path = _load_go_module_prefix(directory)
            if module_path:
                add(module_path, rel_dir)
    # go.work is not a manifest file; look for it at the root, where Go requires it
    # to live for the workspace to apply.
    if (repo_path / "go.work").is_file():
        go_files.append((".", "go.work"))

    for rel_dir, name in list(go_files):
        directory = repo_path if rel_dir == "." else repo_path / rel_dir
        text = read_text_lenient(directory / name)
        if name == "go.work":
            for tokens in _go_directives(text, "use"):
                used = _go_local_dir(repo_path, rel_dir, tokens[0]) if tokens else None
                if used is None:
                    continue
                used_dir = repo_path if used == "." else repo_path / Path(*used.split("/"))
                module_path = _load_go_module_prefix(used_dir) if (used_dir / "go.mod").is_file() else None
                if module_path:
                    add(module_path, used)
        for tokens in _go_directives(text, "replace"):
            if "=>" not in tokens:
                continue
            arrow = tokens.index("=>")
            if arrow < 1 or arrow + 1 >= len(tokens):
                continue
            local = _go_local_dir(repo_path, rel_dir, tokens[arrow + 1])
            if local is None:
                continue
            local_dir = repo_path if local == "." else repo_path / Path(*local.split("/"))
            if local_dir.is_dir():
                replaces.setdefault((tokens[0].strip('"'), "*" if name == "go.work" else rel_dir), local)
                inner = _load_go_module_prefix(local_dir) if (local_dir / "go.mod").is_file() else None
                if inner:
                    add(inner, local)

    entries = [(path, rel_dir) for path, rel_dir in modules.items()]
    entries.extend((path, local, scope) for (path, scope), local in replaces.items())
    return tuple(sorted(entries, key=lambda item: (-len(item[0]), item[2:] != ("*",), item[1], item[2:])))


def _go_replace_applies(scope: str, from_rel: str | None, module_prefix=()) -> bool:
    """A replace declared in the go.mod at `scope` applies to files of that module: under its
    directory and not under a deeper module of their own. "*" (go.work) applies everywhere."""
    if scope == "*" or from_rel is None:
        return True
    if scope != "." and not from_rel.startswith(scope + "/"):
        return False
    for entry in module_prefix:
        inner = entry[1]
        if len(entry) == 2 and inner != scope and inner != "." and from_rel.startswith(inner + "/"):
            if scope == "." or inner.startswith(scope + "/"):
                return False
    return True


def _resolve_go_import(repo_path: Path, module_prefix, import_path: str, from_rel: str | None = None) -> list[str]:
    # Go doesn't import individual files, it imports whole packages (directories) - every
    # non-test .go file in that directory is part of what gets pulled in, so one import
    # statement can fan out to several edges. An import that doesn't start with the
    # module's own declared prefix is external (stdlib or a third-party module) and never
    # resolves to a local file, matching how an unresolved Python/JS import is silently
    # dropped rather than treated as an error.
    #
    # module_prefix is either the single root module path (str, the original
    # form) or a tuple of (module path, repo-relative dir) pairs from
    # _load_go_modules, longest module path first; the first module whose path
    # is the import path or a "/"-bounded prefix of it owns the import.
    if not module_prefix:
        return []
    if isinstance(module_prefix, str):
        candidates = (
            [(module_prefix, ".")]
            if import_path == module_prefix or import_path.startswith(module_prefix.rstrip("/") + "/")
            else []
        )
    else:
        candidates = [
            (entry[0], entry[1])
            for entry in module_prefix
            if (import_path == entry[0] or import_path.startswith(entry[0].rstrip("/") + "/"))
            and (len(entry) < 3 or _go_replace_applies(entry[2], from_rel, module_prefix))
        ]
    if not candidates:
        return []
    owner_path, owner_dir = candidates[0]
    remainder = import_path[len(owner_path):].lstrip("/")
    base_dir = repo_path if owner_dir == "." else repo_path / Path(*owner_dir.split("/"))
    package_dir = base_dir if not remainder else base_dir / Path(*remainder.split("/"))
    if not package_dir.is_dir():
        return []

    targets = []
    for candidate in sorted(package_dir.glob("*.go")):
        if candidate.name.endswith("_test.go"):
            continue
        targets.append(_rel(repo_path, candidate))
    return targets


def _rust_use_paths(node: Node, source: bytes) -> list[str]:
    """A use_declaration's path child, flattened to one or more full path strings.

    tree-sitter's scoped_identifier span already covers the whole "a::b::c" text
    (the "::" tokens are literal source between the segments), so a plain slice of
    the source bytes is the flattened path - no manual tree-walking-and-rejoining
    needed for the common case. The four special forms (aliased, grouped, wildcard)
    each need their own handling before falling back to that slice.
    """

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    if node.type == "use_as_clause":
        # "crate::foo::Bar as MyBar" - only the path before "as" matters for
        # resolution; the alias is a local name, never a thing to resolve.
        return _rust_use_paths(node.children[0], source)

    if node.type == "use_wildcard":
        # "std::io::*" - drop the "*", resolve the prefix module itself.
        prefix_nodes = [c for c in node.children if c.type not in ("::", "*")]
        return [text(prefix_nodes[0])] if prefix_nodes else []

    if node.type == "scoped_use_list":
        # "crate::foo::{Bar, Baz}" - both names share the same prefix module; each
        # becomes prefix::name so the existing crate/self/super resolver can treat
        # them exactly like any other full path.
        #
        # A list item can itself be a nested group ("crate::foo::{Bar,
        # baz::{Qux, self}}"), an aliased entry ("Bar as MyBar"), or a
        # nested wildcard ("bar::*") rather than a bare identifier/self
        # leaf - recursing for those instead of only accepting leaves is
        # what makes nested use grouping (idiomatic, rustfmt's default
        # output in many configs) resolve at all, instead of silently
        # dropping everything below the first brace level. Confirmed
        # directly: "use std::{fmt, io::{self, Write}};" only ever
        # produced "std::fmt" - io and io::Write both vanished (audit
        # finding 33). The nested-wildcard case ("use foo::{bar::*, Baz};")
        # has the identical failure shape - confirmed live against the
        # real tree-sitter-rust grammar: bar::* parses to a use_wildcard
        # node as a direct use_list child, matching neither the leaf check
        # nor the original recursion check, so it silently vanished too.
        # Each recursive result is already fully qualified relative to ITS
        # OWN nested prefix (e.g. "io::Write", or "bar" for a nested
        # wildcard per the use_wildcard branch above), so prepending this
        # level's prefix once, the same as for a leaf name, is enough to
        # cascade correctly through any depth of nesting.
        prefix_node = node.children[0]
        list_node = node.children[-1]
        prefix = text(prefix_node)
        names: list[str] = []
        for c in list_node.children:
            if c.type in ("identifier", "self"):
                names.append(text(c))
            elif c.type in ("scoped_use_list", "use_as_clause", "use_wildcard"):
                names.extend(_rust_use_paths(c, source))
        return [f"{prefix}::{name}" for name in names]

    if node.type == "use_list":
        # "use {a, b};" - rare, no prefix at all.
        return [text(c) for c in node.children if c.type in ("identifier", "self")]

    # scoped_identifier, identifier, crate, self, or super used directly.
    return [text(node)]


def _leading_rust_doc_comment(source: bytes, enclosing_node: Node) -> str | None:
    """Rust's dominant doc-comment convention: one or more contiguous "///"
    line comments immediately above the item, with no blank line in
    between (the same rule rustdoc itself follows for turning "///" into
    documentation). "/** */" block doc comments and #[doc = "..."]
    attributes are real but much rarer in practice - out of scope for
    this pass, matching the plan's "confirmed empirically, not assumed"
    approach: only what was actually verified gets implemented.

    Confirmed empirically via spike that tree-sitter-rust's line_comment
    node span INCLUDES the trailing newline - unlike Go, where a
    comment's end_point row equals its own last content row, here it
    equals the NEXT row (comment.end_point[0] == following_node.
    start_point[0] for two truly adjacent lines, not off by one).
    """
    lines: list[str] = []
    node = enclosing_node.prev_sibling
    expected_end_row = enclosing_node.start_point[0]
    while node is not None and node.type == "line_comment" and node.end_point[0] == expected_end_row:
        raw = source[node.start_byte:node.end_byte].decode(errors="ignore").strip()
        if not raw.startswith("///"):
            break
        lines.append(raw[3:].strip())
        expected_end_row = node.start_point[0]
        node = node.prev_sibling

    if not lines:
        return None
    lines.reverse()
    return "\n".join(lines) or None


def _rust_inline_mod_depth(node: Node) -> int:
    """How many inline `mod x { ... }` bodies enclose this node (they change what self:: and super:: mean)."""
    depth = 0
    parent = node.parent
    while parent is not None:
        if parent.type == "declaration_list" and parent.parent is not None and parent.parent.type == "mod_item":
            depth += 1
        parent = parent.parent
    return depth


def _rust_inline_mod_names(node: Node, source: bytes) -> list[str]:
    """Names of the inline `mod x { ... }` bodies enclosing this node, outermost first."""
    names: list[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type == "declaration_list" and parent.parent is not None and parent.parent.type == "mod_item":
            name_node = parent.parent.child_by_field_name("name")
            if name_node is not None:
                names.append(source[name_node.start_byte:name_node.end_byte].decode(errors="ignore"))
        parent = parent.parent
    return list(reversed(names))


def _rust_adjust_for_inline_mod(path: str, depth: int) -> str | None:
    """Rewrite a use path written inside `depth` inline modules so it is relative to the enclosing file's
    module. None when the path only names something defined in the same file."""
    if depth == 0:
        return path
    segments = path.split("::")
    if segments[0] == "self":
        return None
    if segments[0] != "super":
        return path
    supers = 0
    while supers < len(segments) and segments[supers] == "super":
        supers += 1
    if supers < depth:
        return None
    rest = segments[supers:]
    head = ["super"] * (supers - depth) or ["self"]
    return "::".join(head + rest)


def _extract_rust(node: Node, source: bytes) -> tuple[list[str], list[dict], list[dict]]:
    """Return flattened use-path strings, function/method names, and type names."""
    imports: list[str] = []
    functions: list[dict] = []
    types: list[dict] = []

    def walk(root: Node):
        # Iterative, not recursive - see _extract_python's walk for why.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "use_declaration":
                # use_declaration's single meaningful child is whichever path-shaped
                # node follows the "use" keyword and precedes the ";" - a "pub"/
                # "pub(crate)" use_declaration also carries a leading
                # visibility_modifier child, which must be skipped too or it's
                # mistaken for the path itself: confirmed directly,
                # "pub use crate::foo::Bar;" produced imports: ['pub'] instead
                # of the real target, silently dropping the dependency (audit
                # finding 21 - pub use is the standard idiom for a crate's
                # public re-export surface, not a corner case).
                for child in n.children:
                    if child.type not in ("use", ";", "visibility_modifier"):
                        inline_depth = _rust_inline_mod_depth(n)
                        for use_path in _rust_use_paths(child, source):
                            adjusted = _rust_adjust_for_inline_mod(use_path, inline_depth)
                            if adjusted is not None:
                                imports.append(adjusted)
                        break
            elif n.type == "mod_item" and not any(
                c.type == "declaration_list" for c in n.children
            ):
                # `mod foo;` (no inline body) is how a Rust crate declares its
                # module tree, and it is a real file dependency: the module lives
                # in foo.rs or foo/mod.rs beside this file. Extracting only `use`
                # missed it entirely, so even a correct single-crate layout
                # resolved zero edges from its lib.rs. Emitted as a self-relative
                # path so the existing `self::` resolution handles the two
                # possible on-disk shapes.
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    name = source[name_node.start_byte:name_node.end_byte].decode(errors="ignore")
                    if name:
                        declared_in = _rust_inline_mod_names(n, source)
                        if declared_in:
                            # `mod outer { mod x; }` lives in <module dir>/outer/x.rs (or x/mod.rs)
                            imports.append(f"@imod:{'/'.join(declared_in)}|{name}")
                        else:
                            imports.append(f"self::{name}")
                        # #[path = "..."] (also inside cfg_attr) names the module file explicitly.
                        sibling = n.prev_sibling
                        while sibling is not None and sibling.type in ("attribute_item", "line_comment", "block_comment"):
                            if sibling.type == "attribute_item":
                                attr_text = source[sibling.start_byte:sibling.end_byte].decode(errors="ignore")
                                for attr_path in re.findall(r'\bpath\s*=\s*"([^"]+)"', attr_text):
                                    inline_names = _rust_inline_mod_names(n, source)
                                    if inline_names:
                                        # inside `mod a { ... }` the path is relative to <module dir>/a/
                                        imports.append(f"@ipath:{'/'.join(inline_names)}|{attr_path}")
                                    else:
                                        imports.append(f"@path:{attr_path}")
                            sibling = sibling.prev_sibling
            elif n.type in ("function_item", "function_signature_item"):
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    return_type_node = n.child_by_field_name("return_type")
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_rust_doc_comment(source, n),
                        return_type=(
                            source[return_type_node.start_byte:return_type_node.end_byte].decode(errors="ignore").strip()
                            if return_type_node is not None else None
                        ),
                        is_public=(
                            any(c.type == "visibility_modifier" for c in n.children)
                            and not _is_nested_in_function(n)
                        ),
                    ))
            elif n.type in ("struct_item", "enum_item", "trait_item"):
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    types.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_rust_doc_comment(source, n),
                        is_public=(
                            any(c.type == "visibility_modifier" for c in n.children)
                            and not _is_nested_in_function(n)
                        ),
                    ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, types


def _rust_crate_root(repo_path: Path) -> Path | None:
    for candidate in (repo_path / "src" / "lib.rs", repo_path / "src" / "main.rs"):
        if candidate.exists():
            return candidate
    return None


def _rust_crate_src_dir(repo_path: Path, from_file: Path) -> Path:
    """The `src/` of the crate that owns `from_file`.

    Cargo workspaces put each crate in its own subdirectory with its own
    Cargo.toml and src/, and `crate::` inside one of them means *that* crate,
    not the repo. Resolving against a single repo-root src/ meant every
    workspace resolved nothing at all: serde-rs/serde scanned as 208 modules
    with 0 import edges, so community detection produced 208 one-file
    subsystems. Workspaces are the normal layout for large Rust projects
    (serde, tokio, rust-analyzer), not an edge case.

    Falls back to the repo-root src/ for a single-crate repo.
    """
    try:
        current = from_file.parent.resolve()
        root = repo_path.resolve()
    except OSError:
        return repo_path / "src"
    while True:
        if (current / "Cargo.toml").exists() and (current / "src").is_dir():
            return current / "src"
        if current == root or current.parent == current:
            break
        current = current.parent
    return repo_path / "src"


def _rust_has_any_crate_root(repo_path: Path) -> bool:
    """True if the repo has at least one crate, workspace member or otherwise."""
    if _rust_crate_root(repo_path) is not None:
        return True
    for cargo in repo_path.glob("*/Cargo.toml"):
        if (cargo.parent / "src").is_dir():
            return True
    for cargo in repo_path.glob("*/*/Cargo.toml"):
        if (cargo.parent / "src").is_dir():
            return True
    return False


def _rust_module_search_dir(repo_path: Path, file_path: Path) -> Path:
    """The directory this file's own OWN submodules would live in.

    Directory structure is assumed to mirror the module tree (true for the vast
    majority of real Rust code; #[path = "..."] escape hatches aren't supported).
    The crate root's submodules live directly in src/; a foo/mod.rs's submodules
    live in that same foo/ directory; a leaf foo.rs's submodules live in an
    adjacent foo/ directory (the 2018-edition convention that doesn't require
    foo/mod.rs to exist just to hold further submodules).
    """
    # Resolved against the owning crate, not the repo root, so a workspace
    # member's lib.rs is recognised as a crate root. Missing this meant serde's
    # own `mod integer128;` looked like a submodule of a file named lib rather
    # than a top-level module of the crate, and resolved to nothing.
    src_dir = _rust_crate_src_dir(repo_path, file_path)
    if file_path.name in ("lib.rs", "main.rs") and file_path.parent == src_dir:
        return src_dir
    # Cargo target roots (tests/*.rs, examples/*.rs, benches/*.rs, src/bin/*.rs, build.rs) are crate roots of
    # their own, so their `mod x;` files sit beside them.
    parent = file_path.parent
    if (parent.name in ("tests", "examples", "benches") and (parent.parent / "Cargo.toml").exists()) or (
        parent.name == "bin" and parent.parent == src_dir
    ) or (file_path.name == "build.rs" and (parent / "Cargo.toml").exists()):
        return parent
    if file_path.name == "mod.rs":
        return file_path.parent
    return file_path.parent / file_path.stem


def _resolve_rust_module_dir(search_dir: Path, name: str) -> Path | None:
    # Exact-case directory entries, not Path.exists(): on a case-insensitive filesystem a type name such as
    # `Ctxt` matched `ctxt.rs` and produced an edge to a module path that does not exist.
    from aletheore.scanner import rust_resolve

    if rust_resolve.file_exists_exact(search_dir, f"{name}.rs"):
        return search_dir / f"{name}.rs"
    if rust_resolve.dir_exists_exact(search_dir, name) and rust_resolve.file_exists_exact(search_dir / name, "mod.rs"):
        return search_dir / name / "mod.rs"
    return None


def _walk_rust_segments(search_dir: Path, segments: list[str]) -> Path | None:
    # Walks as far as it can and returns whatever was last resolved - which
    # naturally gives the right answer for both cases: every segment resolving
    # as a further submodule (the walk completes), and the last segment being an
    # item (a struct/fn/const/etc) rather than a submodule (the walk stops one
    # short and returns the containing module's own file, matching how a Python
    # from-import of a plain name falls back to the containing package).
    resolved_file: Path | None = None
    current_dir = search_dir
    for segment in segments:
        candidate = _resolve_rust_module_dir(current_dir, segment)
        if candidate is None:
            break
        resolved_file = candidate
        current_dir = candidate.parent if candidate.name == "mod.rs" else candidate.parent / candidate.stem
    return resolved_file


def _rust_reexport_targets(
    repo_path: Path, from_file: Path, use_path: str, target_rel: str, depth: int = 0
) -> list[str]:
    """Files that actually define `Item` when `use some::module::Item` lands on a module that only re-exports
    it with `pub use` (rustc resolves the name to the defining file, so that file is a dependency too)."""
    from aletheore.scanner import rust_resolve

    if depth > 4 or use_path.startswith("@"):
        return []
    segments = use_path.split("::")
    item = segments[-1]
    if len(segments) < 2 or item in ("*", "self", "super", "crate"):
        return []
    parent = _resolve_rust_path(repo_path, from_file, "::".join(segments[:-1]))
    if parent != target_rel:
        return []  # the last segment is itself a module, nothing to look through
    if len(segments) > 2:
        # The module segment before the item must really be this file (foo.rs or foo/mod.rs); a name that is
        # only re-exported somewhere (`crate::ser` -> `pub use other_crate::ser`) is not a file module.
        module_name = segments[-2]
        target_posix = Path(target_rel)
        stem, folder = target_posix.stem, target_posix.parent.name
        if module_name not in ("crate", "self", "super") and module_name != stem and not (
            stem in ("mod", "lib", "main") and module_name == folder
        ):
            return []
    target_file = Path(repo_path) / target_rel
    declared, reexports = rust_resolve.module_items(str(target_file))
    if item in declared:
        return []
    found: list[str] = []
    for reexport in reexports:
        is_glob = reexport.endswith("::*")
        base = reexport[:-3] if is_glob else reexport
        if not is_glob and base.split("::")[-1] != item:
            continue
        resolved = _resolve_rust_path(repo_path, target_file, base)
        if resolved is None or resolved == target_rel:
            continue
        if is_glob:
            glob_declared, _ = rust_resolve.module_items(str(Path(repo_path) / resolved))
            if item not in glob_declared:
                continue
            found.append(resolved)
        else:
            found.append(resolved)
            found.extend(_rust_reexport_targets(repo_path, target_file, base, resolved, depth + 1))
    return found


def _resolve_rust_path(repo_path: Path, from_file: Path, path: str) -> str | None:
    segments = path.split("::")
    if not segments:
        return None

    head = segments[0]
    rest = segments[1:]

    from aletheore.scanner import rust_resolve

    if path.startswith("@imod:"):
        inline_part, _, mod_name = path[len("@imod:"):].partition("|")
        base = _rust_module_search_dir(repo_path, from_file) / inline_part
        found = _resolve_rust_module_dir(base, mod_name)
        return _rel(repo_path, found) if found is not None else None

    if path.startswith("@ipath:"):
        inline_part, _, attr_path = path[len("@ipath:"):].partition("|")
        base = _rust_module_search_dir(repo_path, from_file) / inline_part
        candidate = os.path.realpath(os.path.join(base, attr_path))
        real_root = os.path.realpath(repo_path)
        if candidate.endswith(".rs") and os.path.isfile(candidate) and candidate.startswith(real_root + os.sep):
            return candidate[len(real_root) + 1:].replace(os.sep, "/")
        return None

    if head == "@path:" or path.startswith("@path:"):
        # #[path = "..."] on a `mod x;`: relative to the declaring file's directory, symlinks followed.
        candidate = os.path.realpath(os.path.join(os.path.dirname(from_file), path[len("@path:"):]))
        real_root = os.path.realpath(repo_path)
        if candidate.endswith(".rs") and os.path.isfile(candidate) and candidate.startswith(real_root + os.sep):
            return candidate[len(real_root) + 1:].replace(os.sep, "/")
        return None

    if head == "crate":
        src_dir = _rust_crate_src_dir(repo_path, from_file)
        target = _walk_rust_segments(src_dir, rest)
        if target is None and rest:
            # `use crate::Item;` where Item is defined in the crate root file itself.
            target = rust_resolve.crate_root_file(src_dir)
    elif head == "self":
        target = _walk_rust_segments(_rust_module_search_dir(repo_path, from_file), rest)
    elif head == "super":
        # "super" always climbs at least one level; each further leading "super"
        # segment climbs one more - directory mirrors the module tree, so that's
        # one more parent directory each time.
        search_dir = _rust_module_search_dir(repo_path, from_file).parent
        while rest and rest[0] == "super":
            search_dir = search_dir.parent
            rest = rest[1:]
        target = _walk_rust_segments(search_dir, rest)
        if target is None:
            # `use super::Item;` / `use super::*;`: Item lives in the parent module's own file.
            target = rust_resolve.module_file_for_dir(search_dir, _rust_crate_src_dir(repo_path, from_file))
    else:
        # Names relative to the current module (2018 uniform paths), then a workspace crate named in this
        # package's Cargo.toml (`use serde_core::de::Deserialize`).
        target = _walk_rust_segments(_rust_module_search_dir(repo_path, from_file), segments)
        if target is None:
            aliases = rust_resolve.crate_aliases(repo_path, from_file)
            if head in aliases:
                lib_root = aliases[head]
                target = _walk_rust_segments(lib_root.parent, rest) or lib_root
        if target is not None:
            return _rel(repo_path, target)
        # No crate/self/super prefix: could be an implicit crate-relative path
        # ("use handlers::Handler;" from the crate root, valid since the 2018
        # edition) or an external crate name (std, or a real third-party
        # dependency) - both look identical syntactically. Walking the whole
        # path from src/ disambiguates them the only way possible without a
        # full Cargo.toml dependency parse: if the first segment doesn't exist
        # on disk, nothing resolves, exactly as an external import should.
        target = _walk_rust_segments(repo_path / "src", segments)

    return _rel(repo_path, target) if target is not None else None


def _extract_java_package(node: Node, source: bytes) -> str | None:
    for child in node.children:
        if child.type == "package_declaration":
            for grandchild in child.children:
                if grandchild.type in ("scoped_identifier", "identifier"):
                    return source[grandchild.start_byte:grandchild.end_byte].decode(errors="ignore")
            return None
    return None


def _java_return_type(source: bytes, enclosing_node: Node) -> str | None:
    """method_declaration's own "type" field (confirmed empirically - e.g.
    node type "integral_type" for "int", "void_type" for "void"). No such
    field on a class/interface/enum/record declaration.
    """
    node = enclosing_node.child_by_field_name("type")
    if node is None:
        return None
    return source[node.start_byte:node.end_byte].decode(errors="ignore").strip()


def _java_is_public(node: Node) -> bool:
    """Java's real visibility, read from its own modifiers.

    Previously computed as `not _is_nested_in_function(node)` - a fair proxy
    for Python, which has no access modifiers, but simply wrong for Java,
    which states visibility right there in a `modifiers` node. It matters
    beyond cosmetics: docs_reference.py filters the generated API reference on
    is_public, so every `private` method was being published as public API.

    The subtlety is the *absent* modifier. A member of an interface or an
    annotation type is implicitly public and carries no `modifiers` node at
    all - `interface Bar { void f(); }` parses with none - so treating "no
    public keyword" as "not public" would mark every interface method private,
    which is a worse error than the one being fixed here (google/gson's
    TypeAdapterFactory.create is exactly that shape). Absent a modifier,
    visibility comes from the enclosing body: implicitly public inside an
    interface or annotation, package-private inside a class or enum, and
    package-private at file scope.
    """
    if _is_nested_in_function(node):
        return False
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is not None:
        kinds = {c.type for c in modifiers.children}
        if "public" in kinds:
            return True
        if "private" in kinds or "protected" in kinds:
            return False
    parent = node.parent
    while parent is not None:
        if parent.type in ("interface_body", "annotation_type_body"):
            return True
        if parent.type in ("class_body", "enum_body"):
            return False
        parent = parent.parent
    return False


def _extract_java(
    node: Node, source: bytes
) -> tuple[list[tuple[str, bool, bool]], list[dict], list[dict]]:
    """Return (import path, is_static, is_wildcard) tuples, method names, and type names."""
    imports: list[tuple[str, bool, bool]] = []
    functions: list[dict] = []
    types: list[dict] = []

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    def walk(root: Node):
        # Iterative, not recursive - see _extract_python's walk for why.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "import_declaration":
                is_static = any(c.type == "static" for c in n.children)
                is_wildcard = any(c.type == "asterisk" for c in n.children)
                for child in n.children:
                    if child.type in ("scoped_identifier", "identifier"):
                        imports.append((text(child), is_static, is_wildcard))
                        break
            elif n.type == "method_declaration":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    raw_doc = _leading_block_comment(n, source, comment_type="block_comment")
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        return_type=_java_return_type(source, n),
                        is_public=_java_is_public(n),
                    ))
            elif n.type in (
                "class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
            ):
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    raw_doc = _leading_block_comment(n, source, comment_type="block_comment")
                    types.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        is_public=_java_is_public(n),
                        is_pure_declaration=n.type == "interface_declaration",
                    ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, types


def _kotlin_modifier_keywords(modifiers_node: Node) -> set[str]:
    """tree-sitter-kotlin nests each modifier in a category node
    (visibility_modifier, class_modifier, property_modifier, ...) whose
    own child is the actual keyword token - unlike Java, where a
    modifiers node's children ARE the keyword tokens directly. Collecting
    one level deeper here, not modifiers.children itself, is what makes
    text like "enum"/"data"/"private" visible at all. Confirmed
    empirically by parsing real Kotlin source and printing the AST.
    """
    keywords: set[str] = set()
    for category in modifiers_node.children:
        for leaf in category.children:
            keywords.add(leaf.type)
    return keywords


def _kotlin_is_public(node: Node) -> bool:
    """Kotlin's default visibility is public - the OPPOSITE of Java's
    package-private default - unless a declaration is explicitly marked
    private/internal/protected. Confirmed empirically: a function/class/
    property with no `modifiers` node at all parses as fully public,
    same as an interface member in Java, but here it's the ordinary case
    for every top-level and class-member declaration, not a special case.

    Also excludes a local (nested) function: tree-sitter-kotlin uses the
    exact same node type, function_declaration, for a top-level function
    and one declared inside another function's body - and Kotlin's local
    functions can't even carry an explicit visibility modifier (it's a
    compile error), so every one of them would otherwise fall straight
    into the "no modifiers node -> public" default above. Real bug this
    fixes: the same "closure extracted as a top-level public symbol" gap
    the shared _is_nested_in_function check exists for (see its own
    docstring), just never wired into this language's own is_public -
    confirmed directly, not assumed: a `fun outer() { fun inner() {} }`
    reported inner as is_public=True.
    """
    if _is_nested_in_function(node):
        return False
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return True
    return not ({"private", "protected", "internal"} & _kotlin_modifier_keywords(modifiers))


def _kotlin_class_kind(node: Node) -> str:
    """'interface' | 'enum' | 'data' | 'class' - tree-sitter-kotlin uses
    ONE node type, class_declaration, for all four; there is no separate
    interface_declaration the way Java has. The actual keyword token
    (`interface` vs `class` as a direct child) and, for class specifically,
    a class_modifier's keyword ("enum"/"data") inside `modifiers` are what
    distinguish them - confirmed empirically, not assumed from Java's shape.
    """
    if any(c.type == "interface" for c in node.children):
        return "interface"
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is not None:
        keywords = _kotlin_modifier_keywords(modifiers)
        if "enum" in keywords:
            return "enum"
        if "data" in keywords:
            return "data"
    return "class"


def _kotlin_return_type(source: bytes, enclosing_node: Node) -> str | None:
    """function_declaration's return type, positional (no named field -
    see _params_text's docstring). Concatenates every child between the
    ':' token and 'function_body' (or end of node, for a body-less
    interface method) rather than taking just the first one, since a
    nullable marker on the return type is not guaranteed to be part of
    the same node as the base type - joining with no separator matches
    how tree-sitter's byte ranges already exclude the whitespace between
    sibling nodes, so genuinely adjacent tokens (a type and a trailing
    '?') read back correctly without a spurious gap.
    """
    found_colon = False
    parts: list[str] = []
    for child in enclosing_node.children:
        if child.type == ":":
            found_colon = True
            continue
        if not found_colon:
            continue
        if child.type == "function_body":
            break
        parts.append(source[child.start_byte:child.end_byte].decode(errors="ignore"))
    text = "".join(parts).strip()
    return text or None


def _extract_kotlin(
    node: Node, source: bytes
) -> tuple[list[tuple[str, bool, bool]], list[dict], list[dict]]:
    """Return (import path, is_static, is_wildcard) tuples, function
    names, and type names (classes, data classes, interfaces, enum
    classes, and objects all land in `types` - is_pure_declaration
    distinguishes an interface).

    is_static is always False here - Kotlin has no "import static"
    equivalent (the closest analogue, importing a companion object's
    member, is a normal qualified import with no distinct grammar shape)
    - kept in the tuple only for structural parity with _extract_java's
    return shape, which the generic import-resolution caller also
    consumes.
    """
    imports: list[tuple[str, bool, bool]] = []
    functions: list[dict] = []
    types: list[dict] = []

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    def walk(root: Node):
        # Iterative, not recursive - see _extract_python's walk for why.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "import":
                # Confirmed empirically: a wildcard import's last child is
                # a literal '*' token (no distinct "asterisk" node type
                # the way Java's grammar has one) - "import a.b.*" parses
                # as qualified_identifier("a.b") + '.' + '*'.
                is_wildcard = bool(n.children) and n.children[-1].type == "*"
                for child in n.children:
                    if child.type == "qualified_identifier":
                        imports.append((text(child), False, is_wildcard))
                        break
            elif n.type == "function_declaration":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    raw_doc = _leading_block_comment(n, source, comment_type="block_comment")
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        return_type=_kotlin_return_type(source, n),
                        is_public=_kotlin_is_public(n),
                    ))
            elif n.type in ("class_declaration", "object_declaration"):
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    raw_doc = _leading_block_comment(n, source, comment_type="block_comment")
                    kind = _kotlin_class_kind(n) if n.type == "class_declaration" else "object"
                    types.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        is_public=_kotlin_is_public(n),
                        is_pure_declaration=kind == "interface",
                    ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, types


def _kotlin_package(node: Node, source: bytes) -> str | None:
    for child in node.children:
        if child.type == "package_header":
            for grandchild in child.children:
                if grandchild.type == "qualified_identifier":
                    return source[grandchild.start_byte:grandchild.end_byte].decode(errors="ignore")
            return None
    return None


def _kotlin_source_root_for(file_path: Path, package: str | None) -> Path | None:
    """Same inference _java_source_root_for uses - the directory such that
    source_root / package-as-a-path is this file's own containing
    directory. Kotlin's package system is directory-based the same way
    Java's is (Gradle's conventional src/main/kotlin, or a bare src/, or
    a flat repo root all work the same way this derives it per-file).
    Unlike Java, Kotlin does not require a file's name to match any
    top-level declaration - multiple top-level classes/functions per file
    are idiomatic and common (confirmed against android/architecture-
    samples) - which only affects _kotlin_class_file below, not this.
    """
    if not package:
        return file_path.parent
    segments = package.split(".")
    parts = file_path.parent.parts
    if len(parts) < len(segments) or list(parts[-len(segments):]) != segments:
        return None
    root = file_path.parent
    for _ in range(len(segments)):
        root = root.parent
    return root


def _kotlin_class_file(root: Path, segments: list[str]) -> Path | None:
    """Unlike Java, a Kotlin file's name need not match the class being
    imported - multiple top-level declarations commonly share one file
    (confirmed against android/architecture-samples: e.g. a single
    TaskRepository.kt exporting both TasksRepository and
    DefaultTasksRepository). So a same-named .kt file is tried first as
    the common case, but the containing directory is also searched for
    any .kt/.kts file that actually declares the imported name - the
    only way to resolve the common "class doesn't match filename" shape
    at all, at the real cost of a real file read per candidate rather
    than a filename check.
    """
    if not segments:
        return None
    same_name = root.joinpath(*segments[:-1], f"{segments[-1]}.kt")
    if same_name.is_file():
        return same_name
    directory = root.joinpath(*segments[:-1])
    if not directory.is_dir():
        return None
    target = segments[-1]
    escaped = re.escape(target)
    # A top-level Kotlin function is as common an import target as a
    # class - Jetpack Compose's @Composable functions are the dominant
    # shape in real UI code (confirmed against android/architecture-
    # samples: ComposeUtils.kt's `fun LoadingContent(...)`, imported by
    # name from three other files, resolved to nothing before this). The
    # optional `\S+\.` handles an extension function's receiver type
    # (`fun LocalTask.toExternal()`), which sits between `fun` and the
    # name this import actually names.
    #
    # A top-level `val`/`var` is the same shape one level simpler - a
    # module-level constant or computed property with no parameter list
    # at all (confirmed against the same real repo:
    # CoroutinesUtils.kt's `val WhileUiSubscribed: SharingStarted = ...`,
    # imported by name, same failure as the function case above before
    # this).
    needle = re.compile(
        rf"\b(?:class|interface|object)\s+{escaped}\b"
        rf"|\bfun\s+(?:\S+\.)?{escaped}\s*\("
        rf"|\b(?:val|var)\s+{escaped}\b"
    )
    for candidate in sorted(directory.glob("*.kt")) + sorted(directory.glob("*.kts")):
        try:
            if needle.search(candidate.read_text(errors="ignore")):
                return candidate
        except OSError:
            continue
    return None


def _resolve_kotlin_import(
    source_roots: list[Path], dotted: str, is_wildcard: bool
) -> tuple[list[Path], bool]:
    """Returns (resolved files, was_ambiguous) - was_ambiguous is True
    when more than one source root had a matching candidate and a fixed
    tiebreak (source_roots' own order) had to pick one.
    """
    segments = dotted.split(".")
    if not segments:
        return [], False

    if is_wildcard:
        matching_roots = [root for root in source_roots if root.joinpath(*segments).is_dir()]
        if not matching_roots:
            return [], False
        directory = matching_roots[0].joinpath(*segments)
        files = sorted(directory.glob("*.kt")) + sorted(directory.glob("*.kts"))
        return files, len(matching_roots) > 1

    matches = [target for root in source_roots if (target := _kotlin_class_file(root, segments)) is not None]
    if matches:
        return [matches[0]], len(matches) > 1

    # One segment short: the imported name might be a nested class/object
    # rather than its own top-level file - same fallback Java/Python/Rust
    # already use.
    if len(segments) > 1:
        matches = [
            target for root in source_roots if (target := _kotlin_class_file(root, segments[:-1])) is not None
        ]
        if matches:
            return [matches[0]], len(matches) > 1

    return [], False


def _java_source_root_for(file_path: Path, package: str | None) -> Path | None:
    """Infer the source root from what this file itself declares: the directory such
    that source_root / package-as-a-path is this file's own containing directory.
    Convention-agnostic on purpose - works for Maven/Gradle's src/main/java, a bare
    src/, or a flat repo root, since it's derived per-file rather than assumed
    upfront, and different files (main vs test source sets) can imply different
    roots that all get tried when resolving any given import.
    """
    if not package:
        return file_path.parent
    segments = package.split(".")
    parts = file_path.parent.parts
    if len(parts) < len(segments) or list(parts[-len(segments):]) != segments:
        return None
    root = file_path.parent
    for _ in range(len(segments)):
        root = root.parent
    return root


def _java_class_file(root: Path, segments: list[str]) -> Path | None:
    if not segments:
        return None
    candidate = root.joinpath(*segments[:-1], f"{segments[-1]}.java")
    return candidate if candidate.is_file() else None


def _path_closeness(a: Path, b: Path) -> int:
    """Number of leading path components `a` and `b` share."""
    n = 0
    for x, y in zip(a.parts, b.parts):
        if x != y:
            break
        n += 1
    return n


def _nearest_first(
    importer: Path | None, candidates: list[Path], coverage: dict[Path, int] | None = None
) -> list[Path]:
    """Candidates ordered by how much path the importing file shares with them (stable, so
    with no importer or a tie the caller's own order still decides). A repo can carry two
    source sets declaring the same packages (lib/java and a trimmed lib/javame copy); a file
    belongs to the set it lives in, not to whichever root happens to be shallowest.

    `coverage` (source root -> how many of the importer's own imports that root can resolve) breaks the
    tie for a file equidistant from several source sets (an example under tutorial/ importing lib/java and
    lib/javame): one compilation uses one classpath, so the set that can satisfy the most of the file's imports
    is the one it was written against."""
    if importer is None or len(candidates) < 2:
        return candidates

    def covered(candidate: Path) -> int:
        if not coverage:
            return 0
        best = [r for r in coverage if candidate == r or candidate.is_relative_to(r)]
        return coverage[max(best, key=lambda r: len(r.parts))] if best else 0

    return sorted(candidates, key=lambda c: (-_path_closeness(importer, c), -covered(c)))


def _resolve_java_import(
    source_roots: list[Path],
    dotted: str,
    is_static: bool,
    is_wildcard: bool,
    importer: Path | None = None,
    coverage: dict[Path, int] | None = None,
) -> tuple[list[Path], bool]:
    """Returns (resolved files, was_ambiguous) - was_ambiguous is True when
    more than one source root had a matching candidate and a fixed tiebreak
    had to pick one, as opposed to there only ever having been one candidate.
    The tiebreak is the candidate nearest the importing file (when `importer`
    is given), then source_roots' own order - see its sort in build_module_graph.
    """
    segments = dotted.split(".")
    if not segments:
        return [], False

    if is_static:
        # "import static a.b.C.MEMBER" - MEMBER is a field or method, not a
        # class; the file that actually exists is a.b.C.java. A static
        # WILDCARD import ("import static a.b.C.*;") is different: its
        # dotted text is already just "a.b.C" - the "*" isn't part of
        # dotted at all, tracked separately via is_wildcard - so there is
        # no extra trailing segment to trim here; trimming it anyway would
        # cut the real class name "C" itself. Confirmed directly against
        # the real tree-sitter-java grammar: the scoped_identifier for
        # "a.b.C.*" is "a.b.C" (3 segments), for "a.b.C.MEMBER" it's
        # "a.b.C.MEMBER" (4 segments).
        #
        # Checked before is_wildcard, unlike before - a static wildcard
        # used to fall into that branch first and get treated exactly like
        # a plain (non-static) wildcard import, looking for a directory
        # a/b/C/ that doesn't exist, since C is a class file, not a
        # package. Confirmed: "import static a.b.C.*;" always returned []
        # even when C was local to the repo (audit finding 29).
        if not is_wildcard:
            segments = segments[:-1]
            if not segments:
                return [], False
    elif is_wildcard:
        # dotted is a package path with no class name - only reached here
        # for a plain, non-static wildcard now. Every .java file directly
        # in that package's directory is what a wildcard import pulls in,
        # the same "import the whole package" fan-out Go's package-level
        # imports need.
        matching_roots = _nearest_first(
            importer, [root for root in source_roots if root.joinpath(*segments).is_dir()], coverage
        )
        if not matching_roots:
            # `import a.b.Outer.*;` names the nested types of a class, not a package directory.
            nested = _nearest_first(
                importer,
                [t for root in source_roots if (t := _java_class_file(root, segments)) is not None],
                coverage,
            )
            return nested[:1], len(nested) > 1
        return sorted(matching_roots[0].joinpath(*segments).glob("*.java")), len(matching_roots) > 1

    matches = _nearest_first(
        importer,
        [target for root in source_roots if (target := _java_class_file(root, segments)) is not None],
        coverage,
    )
    if matches:
        return [matches[0]], len(matches) > 1

    # One segment short: the same fallback Python/Rust already use - the last
    # segment might be a nested class rather than its own top-level file, in
    # which case the containing class's own file is the real target.
    if len(segments) > 1:
        matches = _nearest_first(
            importer,
            [target for root in source_roots if (target := _java_class_file(root, segments[:-1])) is not None],
            coverage,
        )
        if matches:
            return [matches[0]], len(matches) > 1

    return [], False


_JAVA_TYPE_DECLS = (
    "class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
    "annotation_type_declaration",
)


def _java_top_level_type_names(root: Node, source: bytes) -> list[str]:
    """Names of the types declared at the top level of this file (what other files of the same package can name)."""
    names: list[str] = []
    for child in root.children:
        if child.type in _JAVA_TYPE_DECLS:
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                names.append(source[name_node.start_byte:name_node.end_byte].decode(errors="ignore"))
    return names


def _java_type_references(root: Node, source: bytes) -> tuple[set[str], set[str], set[str]]:
    """(capitalized identifiers used in the body, type names declared anywhere in the file, type parameters).

    The identifier rule that keeps same-package edges conservative: only identifiers that start with an
    uppercase letter count; the package and import lines are skipped; in `a.B.c` / `A.b` / `pkg.Type` only the
    leftmost part can name a same-package type; a method's own name is not a reference. Comments and string
    literals are never identifiers in the tree.
    """
    used: set[str] = set()
    declared: set[str] = set()
    tparams: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        t = n.type
        if t in ("import_declaration", "package_declaration"):
            continue
        if t in _JAVA_TYPE_DECLS:
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                declared.add(source[name_node.start_byte:name_node.end_byte].decode(errors="ignore"))
        elif t == "type_parameter":
            for c in n.children:
                if c.type in ("type_identifier", "identifier"):
                    tparams.add(source[c.start_byte:c.end_byte].decode(errors="ignore"))
                    break
        if t in ("identifier", "type_identifier"):
            text = source[n.start_byte:n.end_byte].decode(errors="ignore")
            if text[:1].isupper():
                used.add(text)
            continue
        if t in ("scoped_type_identifier", "scoped_identifier") and n.children:
            stack.append(n.children[0])
            continue
        if t == "field_access":
            obj = n.child_by_field_name("object")
            if obj is not None:
                stack.append(obj)
            continue
        if t == "method_invocation":
            name_node = n.child_by_field_name("name")
            stack.extend(c for c in n.children if name_node is None or c.id != name_node.id)
            continue
        stack.extend(n.children)
    return used, declared, tparams


_JAVA_QUALIFIED_RE = re.compile(r"^[a-z_]\w*(?:\.[a-z_]\w*)*\.[A-Z]\w*")


def _java_inline_qualified_targets(
    root: Node, source: bytes, importer: Path, package_types: dict[str, dict[str, list[Path]]],
    coverage: dict[Path, int] | None,
) -> list[tuple[Path, bool]]:
    """Files named by a fully qualified type written inline (`org.apache.thrift.TException e`), which needs
    no import. The dotted text must start lower-case, as packages do, and name a top-level type that a scanned
    file declares in exactly that package."""
    found: dict[Path, bool] = {}
    stack = [root]
    scoped = ("scoped_type_identifier", "scoped_identifier", "field_access")
    while stack:
        n = stack.pop()
        if n.type in ("import_declaration", "package_declaration"):
            continue
        if n.type in scoped:
            dotted = source[n.start_byte:n.end_byte].decode(errors="ignore")
            dotted = "".join(dotted.split())
            if _JAVA_QUALIFIED_RE.match(dotted):
                parts = dotted.split(".")
                for k in range(len(parts) - 1, 0, -1):
                    files = package_types.get(".".join(parts[:k]), {}).get(parts[k])
                    if files:
                        best = _nearest_first(importer, [f for f in files if f != importer], coverage)
                        if best:
                            found.setdefault(best[0], len(best) > 1)
                        break
        stack.extend(n.children)
    return list(found.items())


def _java_same_package_targets(
    path: Path,
    package: str | None,
    tree_root: Node,
    source: bytes,
    explicit_imports: set[str],
    package_types: dict[str, dict[str, list[Path]]],
    refs: tuple[set[str], set[str], set[str]] | None = None,
) -> list[tuple[Path, bool]]:
    """Files this one depends on without any import: Java needs none for a type of the same package.

    Rule (conservative, so a wrong edge stays rarer than a missing one): the file's capitalized identifiers
    are matched against types declared at the top level of other files of the SAME package. Not an edge when
    the file declares a type of that name itself (nested, local), the name is a type parameter, or a
    single-type import brings a different class of that simple name in (an import shadows the package).
    When several source sets declare the type, the file nearest to the importer wins. The default package
    only matches files in the same directory.
    """
    pkg = package or ""
    candidates = package_types.get(pkg)
    if not candidates:
        return []
    used, declared, tparams = refs if refs is not None else _java_type_references(tree_root, source)
    out: list[tuple[Path, bool]] = []
    for name in sorted(used):
        if name in declared or name in tparams or name in explicit_imports:
            continue
        files = candidates.get(name)
        if not files:
            continue
        if not pkg:
            files = [f for f in files if f.parent == path.parent]
            if not files:
                continue
        best = _nearest_first(path, [f for f in files if f != path])
        if best:
            out.append((best[0], len(best) > 1))
    return out


def _leading_swift_doc_comment(source: bytes, enclosing_node: Node) -> str | None:
    """Swift's dominant doc-comment convention (DocC): one or more contiguous
    "///" line comments immediately above the declaration, no blank line in
    between - same rule as Rust's "///", not Go's plainer "//". Confirmed
    empirically that tree-sitter-swift's "comment" node span ends on its own
    content row (matches Go's adjacency arithmetic, not Rust's off-by-one).
    """
    lines: list[str] = []
    node = enclosing_node.prev_sibling
    expected_end_row = enclosing_node.start_point[0] - 1
    while node is not None and node.type == "comment" and node.end_point[0] == expected_end_row:
        raw = source[node.start_byte:node.end_byte].decode(errors="ignore").strip()
        if not raw.startswith("///"):
            break
        lines.append(raw[3:].strip())
        expected_end_row = node.start_point[0] - 1
        node = node.prev_sibling

    if not lines:
        return None
    lines.reverse()
    return "\n".join(lines) or None


def _swift_return_type(source: bytes, enclosing_node: Node) -> str | None:
    node = enclosing_node.child_by_field_name("return_type")
    if node is None:
        return None
    return source[node.start_byte:node.end_byte].decode(errors="ignore").strip()


def _swift_is_public(node: Node) -> bool:
    """Real visibility, read from Swift's own access-level keywords.

    Absent any modifier, Swift's actual default is `internal` - visible
    within the same module/target but NOT across a module boundary the way
    `public`/`open` are. Since this field feeds cross-target import
    resolution (see _resolve_swift_import) as well as docs filtering, the
    correct default here is False, not True - an `internal` symbol is real
    but not part of what another target can actually see.
    """
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return False
    return any(
        c.type == "visibility_modifier" and any(gc.type in ("public", "open") for gc in c.children)
        for c in modifiers.children
    )


def _extract_swift(node: Node, source: bytes) -> tuple[list[str], list[dict], list[dict]]:
    """Return imported module names, function names, and type names.

    Unlike every other extractor here, "imports" is flat module-name
    strings, not paths or dotted specs - a Swift `import Foo` names a whole
    compiled target, not a file, so there is nothing more specific to
    extract at this stage; _resolve_swift_import does the whole-target
    fan-out. A submodule-qualified import ("import UIKit.UIView") only
    keeps the first (module) segment - the vast majority of real imports
    are unqualified, and the qualifier never names anything this scanner
    could resolve differently anyway.
    """
    imports: list[str] = []
    functions: list[dict] = []
    types: list[dict] = []

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    def walk(root: Node):
        # Iterative, not recursive - see _extract_python's walk for why.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "import_declaration":
                ident = next((c for c in n.children if c.type == "identifier"), None)
                first_segment = next(
                    (c for c in ident.children if c.type == "simple_identifier"), None
                ) if ident is not None else None
                if first_segment is not None:
                    imports.append(text(first_segment))
            elif n.type == "function_declaration":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_swift_doc_comment(source, n),
                        return_type=_swift_return_type(source, n),
                        is_public=_swift_is_public(n),
                    ))
            elif n.type == "protocol_declaration":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    types.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_swift_doc_comment(source, n),
                        is_public=_swift_is_public(n),
                        is_pure_declaration=True,
                    ))
            elif n.type == "class_declaration":
                # tree-sitter-swift uses this one node type for class, struct,
                # AND enum - and also, confusingly, for `extension` (adding
                # members to an EXISTING type, not declaring a new one), all
                # distinguished only by the keyword token itself, found among
                # the node's direct children rather than assumed to be the
                # first one - a visibility `modifiers` node (e.g. `public
                # class Foo`) sits before it when present (confirmed
                # empirically). An extension's body is still walked below for
                # its own function_declarations - real, often public API - it
                # just doesn't get its own "type" entry here, since it isn't one.
                keyword = next(
                    (c.type for c in n.children if c.type in ("class", "struct", "enum", "extension")), None
                )
                if keyword in ("class", "struct", "enum"):
                    name_node = n.child_by_field_name("name")
                    if name_node is not None:
                        types.append(_symbol_entry(
                            source, name_node, n,
                            docstring=_leading_swift_doc_comment(source, n),
                            is_public=_swift_is_public(n),
                        ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, types


_SWIFT_TARGET_CALL_NAMES = {
    "target", "executableTarget", "testTarget", "binaryTarget", "systemLibraryTarget", "macro", "plugin",
}


def _swift_call_arg_strings(call_expr: Node, source: bytes) -> dict[str, str]:
    """label -> string-literal text for a `.someCall(label: "value", ...)`
    call_expression's own labeled arguments - used to read Package.swift's
    `.target(name: "...", path: "...")`-shaped declarations. Only
    string-literal argument values are extracted; every real manifest
    passes name:/path: as plain string literals.
    """
    result: dict[str, str] = {}
    suffix = next((c for c in call_expr.children if c.type == "call_suffix"), None)
    args = next((c for c in suffix.children if c.type == "value_arguments"), None) if suffix is not None else None
    if args is None:
        return result
    for arg in args.children:
        if arg.type != "value_argument":
            continue
        label_node = next((c for c in arg.children if c.type == "value_argument_label"), None)
        label_id = next(
            (c for c in label_node.children if c.type == "simple_identifier"), None
        ) if label_node is not None else None
        if label_id is None:
            continue
        label = source[label_id.start_byte:label_id.end_byte].decode(errors="ignore")
        str_lit = next((c for c in arg.children if c.type == "line_string_literal"), None)
        if str_lit is None:
            continue
        # A string with interpolation (e.g. "./Lambdas/\(name)", the shape a
        # programmatic manifest's factory function uses to build several
        # targets from one call site) isn't a pure literal - `name` is a
        # runtime value, never resolvable from source alone. Skipping it
        # here is deliberate: picking the first line_str_text child instead
        # (the old behavior) silently returned a truncated fragment of the
        # real value ("./Lambdas/", missing the interpolated suffix) as if
        # it were the whole thing - confirmed on a real repo (vapor/
        # penny-bot), where this merged eight distinct Lambda targets into
        # one fictitious "Lambda" target spanning the entire Lambdas/
        # directory. An unresolvable value should fall through to
        # _infer_swift_targets's own directory-convention scan instead of
        # being reported as this.
        if any(c.type == "interpolated_expression" for c in str_lit.children):
            continue
        text_node = next((c for c in str_lit.children if c.type == "line_str_text"), None)
        if text_node is not None:
            result[label] = source[text_node.start_byte:text_node.end_byte].decode(errors="ignore")
    return result


def _parse_swift_package_manifest(repo_path: Path) -> dict[str, str]:
    """Best-effort read of Package.swift for explicit target `path:`
    overrides only - SwiftPM's own default (Sources/<Name>/, or a
    Tests/<Name>/ test target) needs no manifest at all and is handled
    entirely by directory-convention scanning in _infer_swift_targets; this
    only matters for the minority of targets that actually override it.
    Not a full Package.swift interpreter - that file is executable Swift,
    not data - just a walk for the one call shape every real manifest uses
    for this (`.target(name: "...", path: "...")` and its
    executableTarget/testTarget/etc. siblings), never executed.
    """
    manifest = repo_path / "Package.swift"
    if not manifest.is_file():
        return {}
    try:
        source = manifest.read_bytes()
    except OSError:
        return {}
    parser = Parser()
    parser.language = SWIFT_LANGUAGE
    tree = parser.parse(source)

    overrides: dict[str, str] = {}
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            prefix = next((c for c in n.children if c.type == "prefix_expression"), None)
            ident = next(
                (c for c in prefix.children if c.type == "simple_identifier"), None
            ) if prefix is not None else None
            callee_name = source[ident.start_byte:ident.end_byte].decode(errors="ignore") if ident else None
            if callee_name in _SWIFT_TARGET_CALL_NAMES:
                call_args = _swift_call_arg_strings(n, source)
                name, path_str = call_args.get("name"), call_args.get("path")
                if name and path_str:
                    overrides[name] = path_str
        stack.extend(n.children)
    return overrides


_PBXPROJ_TOKEN_RE = re.compile(
    r"""
      \s+
    | /\*.*?\*/
    | //[^\n]*
    | "(?:\\.|[^"\\])*"
    | [{}()=;,]
    | [^\s{}()=;,"]+
    """,
    re.VERBOSE | re.DOTALL,
)


def _tokenize_pbxproj(text: str) -> list[str]:
    """Tokenizer for the OpenStep-plist dialect .pbxproj files actually use -
    NOT the same format Python's stdlib `plistlib` reads (that only handles
    binary and XML plists; confirmed empirically - plistlib has no OpenStep
    format constant at all). The grammar itself is simple and regular
    (brace/paren-delimited dicts and arrays, semicolon-terminated pairs,
    bare-or-quoted string values, C-style comments) - this single regex
    alternation classifies each token; whitespace and comments are dropped,
    quoted strings have their escapes unescaped, everything else (including
    every UUID and bare identifier) passes through as-is.
    """
    tokens = []
    pos, n = 0, len(text)
    while pos < n:
        match = _PBXPROJ_TOKEN_RE.match(text, pos)
        if match is None:
            raise ValueError(f"unparseable pbxproj content at byte {pos}")
        pos = match.end()
        piece = match.group()
        if not piece.strip() or piece.startswith(("/*", "//")):
            continue
        if piece.startswith('"'):
            piece = piece[1:-1].replace('\\"', '"')
        tokens.append(piece)
    return tokens


def _parse_pbxproj_tokens(tokens: list[str], pos: int) -> tuple[object, int]:
    """Recursive-descent parse of one value at tokens[pos] - a dict ("{...}"),
    an array ("(...)"), or a bare/quoted string token. Returns (value,
    next_pos). Object identity (which "isa" type each UUID names) is
    resolved later by the caller, not here - this only reconstructs the
    plain dict/list/str structure.
    """
    token = tokens[pos]
    if token == "{":
        d: dict[str, object] = {}
        pos += 1
        while tokens[pos] != "}":
            key = tokens[pos]
            pos += 1
            assert tokens[pos] == "="
            pos += 1
            value, pos = _parse_pbxproj_tokens(tokens, pos)
            d[key] = value
            if tokens[pos] == ";":
                pos += 1
        return d, pos + 1
    if token == "(":
        items: list[object] = []
        pos += 1
        while tokens[pos] != ")":
            value, pos = _parse_pbxproj_tokens(tokens, pos)
            items.append(value)
            if tokens[pos] == ",":
                pos += 1
        return items, pos + 1
    return token, pos + 1


def _parse_pbxproj(pbxproj_path: Path) -> dict | None:
    try:
        text = pbxproj_path.read_text(encoding="utf-8", errors="ignore")
        tokens = _tokenize_pbxproj(text)
        data, _ = _parse_pbxproj_tokens(tokens, 0)
    except (OSError, IndexError, AssertionError, ValueError):
        # A real project.pbxproj is machine-written by Xcode and never
        # malformed in practice - these are defensive only, the same
        # tolerance every other manifest parser here (tomllib.TOMLDecodeError
        # etc.) already has for a file that doesn't match its expected shape.
        return None
    return data if isinstance(data, dict) else None


def _infer_xcodeproj_swift_targets(
    repo_path: Path, ignored_paths: list[str] | None
) -> dict[str, list[Path]]:
    """Target membership read directly from an Xcode project's own
    project.pbxproj - the real ground-truth source Xcode itself uses,
    verified against a real, large, modern multi-target app
    (wordpress-mobile/WordPress-iOS, objectVersion 77, 685 objects, 15 real
    PBXNativeTargets) before being trusted. Two real membership mechanisms
    exist and both are handled, confirmed on that same real project:

    - Classic: PBXNativeTarget.buildPhases -> a PBXSourcesBuildPhase's
      "files" -> each PBXBuildFile's "fileRef" -> a PBXFileReference's
      "path". Resolved by basename search under the project root rather
      than reconstructing the full PBXGroup nesting path (which does not
      have to mirror the filesystem) - a deliberate, bounded heuristic
      matching this codebase's existing style elsewhere (e.g. Java/Rust's
      own nested-class fallback resolution), not a claim of perfect
      precision; a duplicate basename across the repo is skipped rather
      than guessed at.
    - Modern (Xcode 16+): PBXNativeTarget.fileSystemSynchronizedGroups ->
      each PBXFileSystemSynchronizedRootGroup's "path" names a whole
      directory Xcode syncs automatically, with no per-file PBXBuildFile
      entries at all - confirmed as the primary mechanism WordPress-iOS's
      own main "WordPress" target actually uses today (1 file via the
      classic path, the real bulk via four synchronized groups).
    """
    matches = list(repo_path.glob("*.xcodeproj")) + list(repo_path.glob("*/*.xcodeproj"))
    targets: dict[str, list[Path]] = {}
    for xcodeproj in matches:
        pbxproj_path = xcodeproj / "project.pbxproj"
        data = _parse_pbxproj(pbxproj_path)
        if data is None:
            continue
        objects = data.get("objects")
        if not isinstance(objects, dict):
            continue
        project_root = xcodeproj.parent

        # Built once per xcodeproj, not once per PBXBuildFile entry: the
        # classic mechanism names one Swift file per build-phase entry, and
        # a project still using it (not yet on Xcode 16+'s synchronized
        # groups) can have one entry per source file in the target - a
        # fresh project_root.rglob(name) for every single one is an
        # O(files x repo size) walk. One walk, indexed by basename, turns
        # that into O(repo size + files).
        swift_basename_index: dict[str, list[Path]] = {}
        for p in project_root.rglob("*.swift"):
            if not is_ignored(_rel(repo_path, p), ignored_paths or []):
                swift_basename_index.setdefault(p.name, []).append(p)

        def resolve_classic(phase_uuid: object) -> list[Path]:
            phase = objects.get(phase_uuid)
            if not isinstance(phase, dict) or phase.get("isa") != "PBXSourcesBuildPhase":
                return []
            found = []
            for build_file_uuid in phase.get("files", []) or []:
                build_file = objects.get(build_file_uuid)
                file_ref = objects.get(build_file.get("fileRef")) if isinstance(build_file, dict) else None
                path_str = file_ref.get("path") if isinstance(file_ref, dict) else None
                if not path_str or not path_str.endswith(".swift"):
                    continue
                candidates = swift_basename_index.get(Path(path_str).name, [])
                if len(candidates) == 1:
                    found.append(candidates[0])
            return found

        def resolve_synced(group_uuid: object) -> list[Path]:
            group = objects.get(group_uuid)
            path_str = group.get("path") if isinstance(group, dict) else None
            if not path_str:
                return []
            target_dir = project_root / path_str
            if not target_dir.is_dir():
                return []
            return [
                p for p in target_dir.rglob("*.swift")
                if not is_ignored(_rel(repo_path, p), ignored_paths or [])
            ]

        for obj in objects.values():
            if not isinstance(obj, dict) or obj.get("isa") != "PBXNativeTarget":
                continue
            name = obj.get("name")
            if not isinstance(name, str):
                continue
            files: list[Path] = []
            for phase_uuid in obj.get("buildPhases", []) or []:
                files.extend(resolve_classic(phase_uuid))
            for group_uuid in obj.get("fileSystemSynchronizedGroups", []) or []:
                files.extend(resolve_synced(group_uuid))
            if files:
                targets.setdefault(name, []).extend(files)
    return targets


def _infer_swift_targets(repo_path: Path, ignored_paths: list[str] | None) -> dict[str, list[Path]]:
    """Whole-target file membership for Swift's import graph.

    Unlike every other supported language, a Swift `import Foo` names a
    compiled module, not a file - there is no source-level statement that
    resolves to one specific file the way there is for Python/JS/Go/Java,
    so an import edge has to fan out to every file belonging to that
    target instead (see _resolve_swift_import). Three sources, merged: an
    explicit `path:` override read from Package.swift (the minority case)
    takes priority per target name; every other SwiftPM target - the large
    majority in a real SwiftPM repo, which almost never overrides the
    default - falls back to the convention itself (every direct
    subdirectory of Sources/ or Tests/ is its own target); and, separately,
    any Xcode project's own project.pbxproj is read directly for its real
    PBXNativeTarget membership (see _infer_xcodeproj_swift_targets) - a
    multi-target .xcodeproj app with no SwiftPM structure at all still gets
    real cross-target edges this way, not just a same-directory guess.
    """
    overrides = _parse_swift_package_manifest(repo_path)
    targets: dict[str, list[Path]] = {}

    def add_dir_as_target(name: str, target_dir: Path) -> None:
        if not target_dir.is_dir():
            return
        files = [
            p for p in target_dir.rglob("*.swift")
            if not is_ignored(_rel(repo_path, p), ignored_paths or [])
        ]
        if files:
            targets.setdefault(name, []).extend(files)

    for name, rel_path in overrides.items():
        add_dir_as_target(name, repo_path / rel_path)

    for parent_name in ("Sources", "Tests"):
        parent = repo_path / parent_name
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if child.is_dir() and child.name not in overrides:
                add_dir_as_target(child.name, child)

    for name, files in _infer_xcodeproj_swift_targets(repo_path, ignored_paths).items():
        existing = targets.setdefault(name, [])
        existing.extend(f for f in files if f not in existing)

    return targets


def _resolve_swift_import(swift_target_files: dict[str, list[Path]], module_name: str) -> list[Path]:
    return swift_target_files.get(module_name, [])


def _leading_ruby_doc_comment(source: bytes, enclosing_node: Node) -> str | None:
    """Ruby has no compiler-enforced doc-comment syntax (RDoc/YARD are
    tooling conventions, not grammar) - the de facto convention this
    supports is one or more contiguous "#" line comments immediately
    above the def/class/module, same adjacency rule as Go.

    Confirmed empirically via spike a real tree-sitter-ruby surprise: a
    method's doc comment, when the method is nested inside a class body,
    is NOT a sibling of the method at all - it's a child of the
    surrounding class/module node, sitting before that node's
    body_statement. So method.prev_sibling is None even with a comment
    directly above it in the source; the comment only turns up at
    method.parent.prev_sibling (parent being body_statement). A top-level
    method with no enclosing class has no such wrapper and the comment
    is directly method.prev_sibling, same as Go.
    """
    anchor = enclosing_node
    node = anchor.prev_sibling
    if (node is None or node.type != "comment") and anchor.parent is not None:
        anchor = anchor.parent
        node = anchor.prev_sibling

    lines: list[str] = []
    expected_end_row = anchor.start_point[0] - 1
    while node is not None and node.type == "comment" and node.end_point[0] == expected_end_row:
        raw = source[node.start_byte:node.end_byte].decode(errors="ignore").strip()
        if raw.startswith("#"):
            raw = raw[1:].strip()
        lines.append(raw)
        expected_end_row = node.start_point[0] - 1
        node = node.prev_sibling

    if not lines:
        return None
    lines.reverse()
    return "\n".join(lines) or None


def _extract_ruby(node: Node, source: bytes) -> tuple[list[tuple[str, str]], list[dict], list[dict]]:
    """Return (require/require_relative, path) tuples, method names, and type names."""
    imports: list[tuple[str, str]] = []
    functions: list[dict] = []
    types: list[dict] = []

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    def walk(root: Node):
        # Iterative, not recursive - see _extract_python's walk for why.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "call":
                method_node = n.child_by_field_name("method")
                receiver_node = n.child_by_field_name("receiver")
                # require/require_relative are plain top-level function calls (no
                # receiver) - "@store.require(...)" or "Foo.require(...)" wouldn't be
                # the stdlib Kernel#require this resolver means to handle.
                if receiver_node is None and method_node is not None and method_node.type == "identifier":
                    method_name = text(method_node)
                    if method_name in ("require", "require_relative", "require_all", "autoload"):
                        args_node = n.child_by_field_name("arguments")
                        if args_node is not None:
                            for arg in args_node.children:
                                if arg.type == "string":
                                    for part in arg.children:
                                        if part.type == "string_content":
                                            imports.append((method_name, text(part)))
                                    if method_name in ("autoload", "require_all"):
                                        # autoload :Const, "path" carries one path;
                                        # require_all "dir" one directory.
                                        break
            elif n.type == "method":
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_ruby_doc_comment(source, n),
                        is_public=not _is_nested_in_function(n),
                    ))
            elif n.type in ("class", "module"):
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    types.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_ruby_doc_comment(source, n),
                        is_public=not _is_nested_in_function(n),
                    ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, types


def _resolve_ruby_require(repo_path: Path, from_file: Path, kind: str, spec: str) -> Path | None:
    spec_with_ext = spec if spec.endswith(".rb") else f"{spec}.rb"
    if kind == "require_relative":
        # Always relative to the current file's own directory - unambiguous,
        # exactly like a relative JS import.
        candidate = (from_file.parent / spec_with_ext).resolve()
        return candidate if candidate.is_file() else None
    # Plain "require" / autoload is genuinely ambiguous - the overwhelming majority
    # of real-world uses are gems (external), but a project's own lib/ directory
    # is the near-universal Ruby convention for what else a bare require can
    # name (that's what ends up on $LOAD_PATH for a gem's own internal
    # requires). test/ and spec/ are put on the load path by rake/rspec
    # (`require "helper"`, `require "spec_helper"`). The bare repo root is
    # deliberately NOT searched: it is only on the load path with an explicit
    # `-I.`, and a root-level json.rb or app.rb must not capture `require "json"`
    # (tests/test_graph_ruby.py asserts exactly that).
    # Tried in that order; the first existing file wins. Nothing found ->
    # treated as external the same way an unrecognized Go/Rust/Java import is.
    for base_name in _RUBY_LOAD_PATHS:
        base_dir = repo_path / base_name if base_name else repo_path
        candidate = (base_dir / spec_with_ext).resolve()
        if candidate.is_file():
            return candidate
    return None


_RUBY_LOAD_PATHS = ("lib", "test", "spec")


def _resolve_ruby_require_all(repo_path: Path, spec: str) -> list[Path]:
    """`require_all "jekyll/commands"` (a project-defined helper that requires every
    .rb file under a directory): all .rb files under that directory, first load
    path that has it."""
    for base_name in _RUBY_LOAD_PATHS:
        base_dir = (repo_path / base_name if base_name else repo_path) / spec
        if base_dir.is_dir():
            return sorted(p.resolve() for p in base_dir.rglob("*.rb") if p.is_file())
    return []


def _php_string_content(n: Node, source: bytes) -> str | None:
    # Works for both single-quoted ("string") and double-quoted ("encapsed_string")
    # PHP string literals - both wrap their text in an identically-named child.
    for child in n.children:
        if child.type == "string_content":
            return source[child.start_byte:child.end_byte].decode(errors="ignore")
    return None


def _php_require_path(n: Node, source: bytes) -> str | None:
    """Recursively pull the string literal out of a require/include argument."""
    if n.type == "parenthesized_expression":
        for child in n.children:
            if child.type not in ("(", ")"):
                return _php_require_path(child, source)
        return None
    if n.type in ("string", "encapsed_string"):
        return _php_string_content(n, source)
    if n.type == "binary_expression":
        # The idiomatic "__DIR__ . '/../lib/util.php'" form - __DIR__ already IS
        # "this file's own directory", exactly this resolver's relative-to-current-
        # file base, so only the trailing string literal matters.
        operands = [c for c in n.children if c.type != "."]
        if len(operands) == 2:
            return _php_require_path(operands[1], source)
    return None


def _php_return_type(source: bytes, enclosing_node: Node) -> str | None:
    """function_definition/method_declaration's own "return_type" field
    (confirmed empirically - raw text has no leading ":", unlike TS's
    equivalent field).
    """
    node = enclosing_node.child_by_field_name("return_type")
    if node is None:
        return None
    return source[node.start_byte:node.end_byte].decode(errors="ignore").strip()


def _extract_php(node: Node, source: bytes) -> tuple[list[tuple[str, str]], list[dict], list[dict]]:
    """Return (kind, path) tuples ("use" or "include"), function/method names, and type names."""
    imports: list[tuple[str, str]] = []
    functions: list[dict] = []
    types: list[dict] = []

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    include_keywords = ("require", "require_once", "include", "include_once")

    def walk(root: Node):
        # Iterative, not recursive - see _extract_python's walk for why.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type in (
                "require_expression", "require_once_expression",
                "include_expression", "include_once_expression",
            ):
                for child in n.children:
                    if child.type not in include_keywords:
                        path = _php_require_path(child, source)
                        if path:
                            imports.append(("include", path))
                        break
            elif n.type == "namespace_use_declaration":
                # A single unprefixed clause ("use Foo\Bar;" or "use Foo\Bar
                # as Baz;") is a direct namespace_use_clause child. A
                # grouped statement ("use Foo\Bar\{ClassA, ClassB as B};")
                # nests its clauses one level deeper instead, inside a
                # namespace_use_group sibling that carries the shared
                # prefix from the declaration's own namespace_name - neither
                # is a namespace_use_declaration's direct child, so scanning
                # only direct namespace_use_clause children silently dropped
                # the entire grouped statement (audit finding 28). Grouped
                # use is valid PHP 7+ syntax and common enough that zeroing
                # it out entirely is a real gap, not a corner case.
                prefix = ""
                clauses: list[Node] = []
                for child in n.children:
                    if child.type == "namespace_use_clause":
                        clauses.append(child)
                    elif child.type == "namespace_name":
                        prefix = text(child)
                    elif child.type == "namespace_use_group":
                        clauses.extend(c for c in child.children if c.type == "namespace_use_clause")
                for clause in clauses:
                    # "use Foo\Bar as Baz;"'s alias target is also a bare
                    # "name" node - the same node type as an unaliased
                    # import's own path - so matching on type alone appended
                    # a second, phantom "Baz" entry alongside the real path
                    # every time an alias was used, grouped or not (audit
                    # finding 31). child_by_field_name("alias") is how the
                    # grammar itself distinguishes the two.
                    alias_node = clause.child_by_field_name("alias")
                    for grandchild in clause.children:
                        if grandchild is alias_node:
                            continue
                        if grandchild.type in ("qualified_name", "name"):
                            clause_path = text(grandchild)
                            imports.append(
                                ("use", f"{prefix}\\{clause_path}" if prefix else clause_path)
                            )
                            break
            elif n.type in ("function_definition", "method_declaration"):
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    raw_doc = _leading_block_comment(n, source)
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        return_type=_php_return_type(source, n),
                        is_public=not _is_nested_in_function(n),
                    ))
            elif n.type in (
                "class_declaration", "interface_declaration", "trait_declaration", "enum_declaration",
            ):
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    raw_doc = _leading_block_comment(n, source)
                    types.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        is_public=not _is_nested_in_function(n),
                    ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, types


def _load_php_psr4_map(repo_path: Path) -> dict[str, Path]:
    composer_json = repo_path / "composer.json"
    if not composer_json.exists():
        return {}
    try:
        data = json.loads(read_text_lenient(composer_json))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    mapping: dict[str, Path] = {}
    for section in ("autoload", "autoload-dev"):
        psr4 = data.get(section, {})
        if not isinstance(psr4, dict):
            continue
        psr4 = psr4.get("psr-4", {})
        if not isinstance(psr4, dict):
            continue
        for prefix, rel_dir in psr4.items():
            if isinstance(prefix, str) and isinstance(rel_dir, str):
                mapping[prefix] = repo_path / rel_dir
    return mapping


def _resolve_php_use(psr4_map: dict[str, Path], qualified_name: str) -> tuple[Path | None, bool]:
    """Returns (resolved file, was_ambiguous) - was_ambiguous is True when
    more than one registered PSR-4 prefix matched and the longest-prefix rule
    had to pick one.
    """
    qualified_name = qualified_name.lstrip("\\")

    # PSR-4 requires the longest matching prefix to win when more than one could
    # apply (e.g. "App\\" -> src/ and "App\\Tests\\" -> tests/ both matching
    # "App\Tests\Foo" - the more specific one is correct).
    matching_prefixes: list[str] = []
    for prefix in psr4_map:
        normalized = prefix.rstrip("\\")
        if qualified_name == normalized or qualified_name.startswith(normalized + "\\"):
            matching_prefixes.append(prefix)

    if not matching_prefixes:
        return None, False
    best_prefix = max(matching_prefixes, key=lambda p: len(p.rstrip("\\")))

    remainder = qualified_name[len(best_prefix.rstrip("\\")):].lstrip("\\")
    if not remainder:
        return None, False

    parts = remainder.split("\\")
    candidate = psr4_map[best_prefix].joinpath(*parts[:-1], f"{parts[-1]}.php")
    if not candidate.is_file():
        return None, False
    return candidate, len(matching_prefixes) > 1


def _resolve_php_include(from_file: Path, spec: str) -> Path | None:
    # A leading "/" here is virtually always the separator in the idiomatic
    # "__DIR__ . '/../lib/util.php'" pattern (verified: Path("/a/b") / "/x" discards
    # "/a/b" entirely and resolves to "/x", which would silently break that pattern
    # if not stripped first), not a genuine filesystem-absolute path - PHP code
    # depending on a real hardcoded absolute path wouldn't be portable anyway.
    spec = spec.lstrip("/")
    spec_with_ext = spec if spec.endswith(".php") else f"{spec}.php"
    candidate = (from_file.parent / spec_with_ext).resolve()
    return candidate if candidate.is_file() else None


def _extract_c_family(node: Node, source: bytes) -> tuple[list[str], list[dict], list[dict]]:
    """Return quoted #include paths (angle-bracket ones are never resolvable, so
    they're dropped here rather than carried through and rejected later), function
    names, and type names (struct/class/union/enum)."""
    imports: list[str] = []
    functions: list[dict] = []
    types: list[dict] = []

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    def function_name(declarator: Node) -> str | None:
        # The name-bearing function_declarator can be wrapped in pointer_declarator
        # (a pointer return type), reference_declarator, etc. - search for it rather
        # than assuming a fixed nesting depth.
        if declarator.type == "function_declarator":
            for child in declarator.children:
                if child.type in ("identifier", "field_identifier"):
                    return text(child)
                if child.type == "qualified_identifier":
                    # Out-of-class definition ("Logger::info(...)") - the class
                    # qualifier is a namespace_identifier/type_identifier (already
                    # captured separately as a type), only the final plain
                    # "identifier" segment is the actual method name.
                    for grandchild in child.children:
                        if grandchild.type == "identifier":
                            return text(grandchild)
            return None
        for child in declarator.children:
            found = function_name(child)
            if found is not None:
                return found
        return None

    def walk(root: Node):
        # Iterative, not recursive - a deeply-nested real-world AST (confirmed on
        # Linux kernel C source, which crashed the previous recursive version with
        # "RecursionError: maximum recursion depth exceeded") can exceed Python's
        # recursion limit. reversed(children) before pushing preserves the same
        # left-to-right visiting order a recursive walk would produce.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "preproc_include":
                for child in n.children:
                    if child.type == "string_literal":
                        for grandchild in child.children:
                            if grandchild.type == "string_content":
                                imports.append(text(grandchild))
                        break
                    # system_lib_string (<foo.h>) is deliberately not collected at all -
                    # always external by convention, never resolvable without knowing a
                    # build system's own -I search paths.
            elif n.type == "function_definition":
                declarator_node = n.child_by_field_name("declarator")
                if declarator_node is not None:
                    name = function_name(declarator_node)
                    if name is not None:
                        raw_doc = _leading_block_comment(n, source)
                        type_node = n.child_by_field_name("type")
                        functions.append(
                            _compact_symbol({
                                "name": name,
                                "start_line": n.start_point[0] + 1,
                                "end_line": n.end_point[0] + 1,
                                "params": _params_text(source, n),
                                "docstring": _strip_jsdoc_stars(raw_doc) if raw_doc else None,
                                "return_type": (
                                    text(type_node) if type_node is not None else None
                                ),
                                "is_public": not _is_nested_in_function(n),
                            })
                        )
            elif n.type in ("struct_specifier", "class_specifier", "union_specifier", "enum_specifier"):
                # A forward declaration ("struct Foo;") or a plain type reference
                # ("struct Foo* getFoo();") matches this node type too, with no body -
                # only count it as a type genuinely defined in this file when it
                # actually has one (verified this distinction is real: both of those
                # produce the same struct_specifier node type with no
                # field_declaration_list child, confirmed directly rather than assumed).
                has_body = any(
                    c.type in ("field_declaration_list", "enumerator_list") for c in n.children
                )
                name_node = n.child_by_field_name("name")
                if name_node is not None and has_body:
                    raw_doc = _leading_block_comment(n, source)
                    types.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_strip_jsdoc_stars(raw_doc) if raw_doc else None,
                        is_public=not _is_nested_in_function(n),
                    ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, types


def _resolve_c_include(from_file: Path, spec: str) -> Path | None:
    # A quoted #include's default search order tries the current file's own
    # directory first - the only part of that order knowable without a build
    # system's -I flags, and the one that covers the overwhelming majority of real
    # local includes.
    candidate = (from_file.parent / spec).resolve()
    return candidate if candidate.is_file() else None


def _extract_csharp_namespace(node: Node, source: bytes) -> str | None:
    for child in node.children:
        if child.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
            for grandchild in child.children:
                if grandchild.type in ("qualified_name", "identifier"):
                    return source[grandchild.start_byte:grandchild.end_byte].decode(errors="ignore")
            return None
    return None


_CSHARP_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)


def _leading_csharp_xmldoc(source: bytes, enclosing_node: Node) -> str | None:
    """C#'s XML doc comment convention: one or more contiguous "///" line
    comments immediately above the declaration (confirmed empirically -
    tree-sitter-c-sharp emits each line as its own separate "comment"
    sibling, same shape as Go's "//" doc comments, no wrapper-node issue
    like Java/JS have for exported/modified declarations).

    Returns the <summary>...</summary> text when the comment uses XML doc
    tags (the idiomatic C# convention); falls back to the raw concatenated
    "///" lines when it doesn't (a plain "/// One-line description.").
    """
    lines: list[str] = []
    node = enclosing_node.prev_sibling
    expected_end_row = enclosing_node.start_point[0] - 1
    while node is not None and node.type == "comment" and node.end_point[0] == expected_end_row:
        raw = source[node.start_byte:node.end_byte].decode(errors="ignore").strip()
        if not raw.startswith("///"):
            break
        lines.append(raw[3:].strip())
        expected_end_row = node.start_point[0] - 1
        node = node.prev_sibling

    if not lines:
        return None
    lines.reverse()
    joined = "\n".join(lines)

    match = _CSHARP_SUMMARY_RE.search(joined)
    if match:
        summary_lines = [line.strip() for line in match.group(1).strip().splitlines()]
        return "\n".join(line for line in summary_lines if line) or None
    return joined or None


def _csharp_return_type(source: bytes, enclosing_node: Node) -> str | None:
    """method_declaration's own "returns" field (confirmed empirically -
    NOT "type", unlike Java's equivalent field). constructor_declaration
    has no such field (constructors have no return type) and correctly
    gets None.
    """
    node = enclosing_node.child_by_field_name("returns")
    if node is None:
        return None
    return source[node.start_byte:node.end_byte].decode(errors="ignore").strip()


def _extract_csharp(node: Node, source: bytes) -> tuple[list[str], list[dict], list[dict]]:
    imports: list[str] = []
    functions: list[dict] = []
    types: list[dict] = []

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    def walk(root: Node):
        # Iterative, not recursive - see _extract_python's walk for why.
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "using_directive":
                # Plain ("using App.Store;"), aliased ("using Foo = App.Store.Store;"),
                # and static ("using static System.Console;") forms all put the actual
                # qualified path as the LAST qualified_name/identifier child - an alias
                # name, when present, is a plain identifier that comes before it, so
                # taking the last match is correct for all three forms without needing
                # to special-case any of them (verified directly: child_by_field_name
                # ("name") on this node type returns the ALIAS name, not the path, the
                # opposite of what it looks like it should return).
                path_node = None
                for child in n.children:
                    if child.type in ("qualified_name", "identifier"):
                        path_node = child
                if path_node is not None:
                    imports.append(text(path_node))
            elif n.type in (
                "class_declaration", "interface_declaration", "struct_declaration",
                "record_declaration", "enum_declaration",
            ):
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    types.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_csharp_xmldoc(source, n),
                        is_public=not _is_nested_in_function(n),
                        is_pure_declaration=n.type == "interface_declaration",
                    ))
            elif n.type in ("method_declaration", "constructor_declaration"):
                name_node = n.child_by_field_name("name")
                if name_node is not None:
                    functions.append(_symbol_entry(
                        source, name_node, n,
                        docstring=_leading_csharp_xmldoc(source, n),
                        return_type=_csharp_return_type(source, n),
                        is_public=not _is_nested_in_function(n),
                    ))
            stack.extend(reversed(n.children))

    walk(node)
    return imports, functions, types


_CSHARP_TYPE_DECLS = (
    "class_declaration", "interface_declaration", "struct_declaration",
    "record_declaration", "enum_declaration", "delegate_declaration",
)

# Short names collide with locals, parameters and generic placeholders far too
# often to be worth an edge ("Map", "Id", "T"). Four is where real C# type names
# start in practice, and a wrong edge is worse than a missing one: it invents a
# dependency the wiki will then explain.
_CSHARP_MIN_TYPE_NAME = 4

# Bounded so one file referencing hundreds of types cannot dominate the graph or
# the importance ranking. Ordered by first appearance, so what survives the cap
# is what the file leads with.
#
# 40 was sized for the name-only rule, where every hit might be a coincidence. Hits are now filtered by project
# and visible namespace, so they are real dependencies, and 40 silently dropped them: AutoMapper's
# MapAtRuntime.cs names 52 entity types and lost the last dozen edges. 150 still bounds a pathological file.
_CSHARP_MAX_TYPE_EDGES = 150

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _csharp_declared_type_names(node: Node, source: bytes) -> list[str]:
    """Type names this file declares - the index for type-reference edges."""
    names: list[str] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in _CSHARP_TYPE_DECLS:
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                names.append(source[name_node.start_byte:name_node.end_byte].decode(errors="ignore"))
        stack.extend(n.children)
    return names


_CSHARP_TOP_TYPE_DECLS = _CSHARP_TYPE_DECLS + ("record_struct_declaration",)
_CSHARP_GLOBAL_USING_RE = re.compile(r"^\s*global\s+using\s+(?!static\b)(?:\w+\s*=\s*)?([\w.]+)\s*;", re.MULTILINE)
_CSHARP_EXTENSION_RE = re.compile(r"\b([A-Za-z_]\w*)\s*(<[^>()]*>)?\s*\(\s*this\s+[A-Za-z_]")


def _csharp_file_summary(root: Node, source: bytes) -> dict:
    """Namespaces this file declares, its TOP-LEVEL types (name -> namespace; nested types are reachable only
    through their outer type), whether it defines extension methods, and its `global using` namespaces."""

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    namespaces: list[str] = []
    types: dict[str, str] = {}

    def walk(children, ns: str) -> str:
        for c in children:
            if c.type.startswith("preproc_"):
                # `#if FLAG ... #endif` wraps whole namespaces and types (AutoMapper's Polyfill.cs is entirely
                # inside one); every branch is indexed, as a compiler configured with that flag would see it.
                ns = walk(c.children, ns)
            elif c.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
                name_node = c.child_by_field_name("name")
                if name_node is None:
                    continue
                full = f"{ns}.{text(name_node)}" if ns else text(name_node)
                namespaces.append(full)
                if c.type == "file_scoped_namespace_declaration":
                    ns = full
                    walk(c.children, full)
                else:
                    body = c.child_by_field_name("body")
                    walk(body.children if body is not None else c.children, full)
            elif c.type in _CSHARP_TOP_TYPE_DECLS:
                name_node = c.child_by_field_name("name")
                if name_node is not None:
                    types.setdefault(text(name_node), ns)
            elif c.type == "declaration_list":
                walk(c.children, ns)
        return ns

    walk(root.children, "")
    decoded = source.decode("utf-8", "ignore")
    ext_found = _CSHARP_EXTENSION_RE.findall(decoded) if types and "static class" in decoded else []
    ext_methods = sorted({name for name, _generic in ext_found})
    ext_generic = sorted({name for name, generic in ext_found if generic})
    return {
        "ns": namespaces,
        "types": types,
        "ext_methods": ext_methods,
        "ext_generic": ext_generic,
        "ext_ns": next(iter(types.values())) if types else "",
        "global_usings": _CSHARP_GLOBAL_USING_RE.findall(decoded),
    }


def _csharp_code_identifiers(root: Node, source: bytes) -> list[str]:
    """Identifier tokens of the code in first-appearance order. `using` directives, namespace names, comments and
    string literals are not part of it, so `Microsoft.Extensions.X` does not look like a use of a type `Extensions`."""
    out: list[str] = []
    seen: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "using_directive":
            continue
        if n.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
            name_node = n.child_by_field_name("name")
            stack.extend(reversed([c for c in n.children if name_node is None or c.id != name_node.id]))
            continue
        if n.type == "identifier":
            text = source[n.start_byte:n.end_byte].decode(errors="ignore")
            if text not in seen:
                seen.add(text)
                out.append(text)
            continue
        stack.extend(reversed(n.children))
    return out


def _csharp_project_index(repo_path: Path, source_paths: list[Path]) -> tuple[dict[Path, Path | None], dict]:
    """(project dir of each .cs file, project dir -> set of project dirs it can see).

    A file belongs to the project of its nearest ancestor directory holding a .csproj. A project sees itself and,
    transitively, the projects its <ProjectReference> items name. A repo with no .csproj has no boundary
    information (every file maps to None and sees everything)."""
    project_dirs: dict[Path, Path] = {}
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            if filename.endswith(".csproj"):
                project_dirs.setdefault(Path(dirpath), Path(dirpath) / filename)
    direct: dict[Path, set[Path]] = {}
    for directory, csproj in project_dirs.items():
        refs: set[Path] = set()
        try:
            root = ET.parse(csproj).getroot()
        except (ET.ParseError, OSError):
            direct[directory] = refs
            continue
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == "ProjectReference":
                include = element.attrib.get("Include", "").strip().replace("\\", "/")
                if include:
                    target = Path(os.path.normpath(directory / include)).parent
                    if target in project_dirs:
                        refs.add(target)
        direct[directory] = refs
    visible: dict[Path, set[Path]] = {}
    for directory in project_dirs:
        seen = {directory}
        queue = [directory]
        while queue:
            for nxt in direct.get(queue.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        visible[directory] = seen
    project_of: dict[Path, Path | None] = {}
    for source_path in source_paths:
        project: Path | None = None
        for ancestor in (source_path.parent, *source_path.parent.parents):
            if ancestor in project_dirs:
                project = ancestor
                break
        project_of[source_path] = project
    return project_of, visible


def _build_csharp_index(repo_path: Path, summaries: dict[Path, dict]) -> dict:
    project_of, visible = _csharp_project_index(repo_path, list(summaries))
    global_usings: dict[Path | None, set[str]] = {}
    ext_owners: dict[str, set[Path]] = {}
    ext_generic_owners: dict[str, set[Path]] = {}
    for path, summary in summaries.items():
        global_usings.setdefault(project_of.get(path), set()).update(summary["global_usings"])
        for method in summary["ext_methods"]:
            ext_owners.setdefault(method, set()).add(path)
        for method in summary["ext_generic"]:
            ext_generic_owners.setdefault(method, set()).add(path)
    # Names under four characters (Add, Has) are mostly BCL instance methods, so a bare `.Add(` proves nothing.
    # A short name counts only when exactly one file declares it, it is a GENERIC extension, and the caller
    # supplies explicit type arguments (`.Has<Attr>()`), which an instance List.Add never takes.
    ext_short_generic = {
        name for name, owners in ext_generic_owners.items()
        if len(name) < _CSHARP_MIN_TYPE_NAME and len(ext_owners.get(name, ())) == 1
    }
    return {
        "files": summaries, "project_of": project_of, "visible": visible,
        "global_usings": global_usings, "ext_owners": ext_owners, "ext_short_generic": ext_short_generic,
    }


def _csharp_project_sees(index: dict, importer: Path, other: Path) -> bool:
    p_imp = index["project_of"].get(importer)
    p_oth = index["project_of"].get(other)
    if p_imp is None or p_oth is None:
        return True
    return p_oth in index["visible"].get(p_imp, {p_imp})


def _csharp_visibility_scope(importer: Path, index: dict, usings: list[str] | None) -> dict:
    info = index["files"].get(importer) or {"ns": []}
    chain: dict[str, int] = {"": 0}
    for ns in info["ns"]:
        parts = ns.split(".")
        for k in range(1, len(parts) + 1):
            chain[".".join(parts[:k])] = k
    imported = set(usings or [])
    imported |= index["global_usings"].get(index["project_of"].get(importer), set())
    imported |= index["global_usings"].get(None, set())
    return {"chain": chain, "imported": imported}


def _csharp_visible_owners(
    name: str, owners: set[Path], importer: Path, index: dict, scope: dict, text: str
) -> list[Path]:
    """The declaring files of `name` the importer can reach (see _csharp_type_reference_targets)."""
    best_rank: tuple[int, int] | None = None
    best: list[Path] = []
    for owner in sorted(owners):
        if owner == importer or not _csharp_project_sees(index, importer, owner):
            continue
        owner_info = index["files"].get(owner) or {"types": {}, "ext_methods": []}
        ns = owner_info["types"].get(name)
        if ns is None and name in owner_info["ext_methods"]:
            ns = owner_info["ext_ns"]
        if ns is None:
            continue
        if ns in scope["chain"]:
            rank = (2, scope["chain"][ns])
        elif ns in scope["imported"]:
            rank = (1, 0)
        elif f"{ns}.{name}" in text:
            rank = (0, 0)
        else:
            continue
        if best_rank is None or rank > best_rank:
            best_rank, best = rank, [owner]
        elif rank == best_rank:
            best.append(owner)
    return best


def _csharp_type_reference_targets(
    source: bytes,
    own_types: set[str],
    type_owners: dict[str, set[Path]],
    importer: Path | None = None,
    index: dict | None = None,
    usings: list[str] | None = None,
    tree_root: Node | None = None,
) -> list[tuple[Path, bool]]:
    """Files this one depends on by *naming their types*, not by importing them.

    With `index` (see _build_csharp_index) the owners of a name are filtered by what the importing file can
    actually see: the owner's project must be the importer's or one it references, and the type's namespace must
    be one of the importer's enclosing namespaces, a namespace it `using`s (or a global using), or written out in
    full at the use. Of the owners left, the innermost enclosing namespace wins over a `using`; every part of a
    partial type is linked. Without `index` the old name-only rule applies.

    C# needs no `using` to reference a type in the same namespace, so an
    import-derived graph is near-empty for the language. Measured on
    AutoMapper/AutoMapper: 512 .cs files, 230 `using` directives in the entire
    repository, 156 of them `System.*` - so 419 of 512 files declared no
    dependency at all and community detection returned 474 clusters for 513
    modules, one per file. That is not a parsing bug; there is genuinely nothing
    to parse. The dependency exists in the body, where a type is named.

    Returns (target, was_ambiguous) pairs. A name declared in more than one
    file in the repository still produces an edge - to a deterministically
    chosen owner - flagged ambiguous, rather than silently contributing
    nothing (the previous behavior: dropping it outright, which loses
    strictly more information than keeping a flagged guess does).
    """
    referenced: list[tuple[Path, bool]] = []
    seen: set[str] = set()
    text = source.decode("utf-8", "ignore")
    scope = _csharp_visibility_scope(importer, index, usings) if index is not None and importer is not None else None
    if scope is not None and tree_root is not None:
        # Identifier tokens of the code only: comments, strings and `using`/namespace names are not references.
        names = _csharp_code_identifiers(tree_root, source)
    else:
        names = (m.group(0) for m in _IDENTIFIER_RE.finditer(text))
    for name in names:
        if name in seen or name in own_types:
            continue
        if len(name) < _CSHARP_MIN_TYPE_NAME:
            # only a unique generic extension called with explicit type arguments (see _build_csharp_index)
            if (
                scope is None
                or name not in index["ext_short_generic"]
                or not re.search(r"\.\s*" + re.escape(name) + r"\s*<[^>()]*>\s*\(", text)
            ):
                continue
        owners = type_owners.get(name) if len(name) >= _CSHARP_MIN_TYPE_NAME else None
        if scope is not None:
            # an extension method is "owned" by the file that declares it and reached by calling it by name
            owners = (owners or set()) | index["ext_owners"].get(name, set())
        if not owners:
            continue
        seen.add(name)
        if scope is not None:
            chosen = _csharp_visible_owners(name, owners, importer, index, scope, text)
            for owner in chosen:
                referenced.append((owner, len(chosen) > 1))
            if len(referenced) >= _CSHARP_MAX_TYPE_EDGES:
                break
            continue
        # More than one declaring file: still an edge (more informative than
        # dropping it - the reader loses nothing that a dropped edge would
        # have preserved), just to a deterministically-chosen one, flagged
        # ambiguous rather than presented as certain.
        referenced.append((sorted(owners)[0], len(owners) > 1))
        if len(referenced) >= _CSHARP_MAX_TYPE_EDGES:
            break
    return referenced


def _csharp_prefix_and_root_for(file_path: Path, namespace: str | None) -> tuple[str, Path] | None:
    """Returns (implicit prefix ending in "." or "", the root that prefix resolves
    against) for this one file - discovered per-file the same way Java's source
    root is, but accounting for something Java has no equivalent of: a real .NET
    project's csproj almost always sets a <RootNamespace> (every "dotnet new"
    template does this by default) that prepends an implicit prefix to every
    file's effective namespace with NO corresponding directory on disk at all.
    Verified directly against a real dotnet-built project: RootNamespace="App"
    with Handler.cs living at Handlers/Handler.cs (no "App" folder anywhere) and
    declaring "namespace App.Handlers" - requiring the FULL namespace to mirror
    the directory (which is exactly what Java's version of this does, correctly,
    since Java has no such implicit-prefix feature) silently resolved nothing at
    all here. Matching only the longest trailing suffix that DOES correspond to
    real directories, and treating whatever's left over as the implicit prefix,
    handles this the same way PHP's PSR-4 handles a namespace prefix that maps to
    a directory other than where the prefix "logically" starts.
    """
    if not namespace:
        return "", file_path.parent
    segments = namespace.split(".")
    parts = file_path.parent.parts
    for take in range(len(segments), 0, -1):
        if len(parts) >= take and list(parts[-take:]) == segments[-take:]:
            root = file_path.parent
            for _ in range(take):
                root = root.parent
            prefix_segments = segments[: len(segments) - take]
            prefix = ".".join(prefix_segments) + "." if prefix_segments else ""
            return prefix, root

    # No trailing segment corresponds to a real directory - a flat project whose
    # namespace comes entirely from <RootNamespace> with no mirroring folders at
    # all. Returning None here meant such a file contributed nothing to the
    # prefix map, so every `using` in a flat C# project resolved to nothing.
    # Treat the whole namespace as the implicit prefix, rooted at the file's own
    # directory: `using App.Lib;` then matches this file's namespace exactly and
    # fans out to that directory, the same granularity a using operates at.
    #
    # Trailing "." matters here, not just cosmetically: without it, prefix
    # matching in _resolve_csharp_using is a bare string startswith, so a
    # namespace "App.Data" would also match an unrelated sibling namespace
    # "App.DataAccess" (the same "." boundary every other return path in
    # this function already enforces). This does mean a file can no longer
    # self-match its own exact namespace via `using` with no further
    # nesting - consistent with how a directory-mirrored prefix's own bare
    # parent segment already never self-matches either.
    return namespace + ".", file_path.parent


def _resolve_csharp_using(prefix_map: dict[str, Path], dotted: str) -> tuple[list[Path], bool]:
    """Returns (resolved files, was_ambiguous) - was_ambiguous is True when
    more than one registered namespace prefix matched and the longest-prefix
    rule had to pick one.
    """
    # "using Namespace;" imports the WHOLE namespace, not any specific type -
    # unlike Java's import (which explicitly names one class to bring in), C#
    # offers no way to tell which specific class is actually depended on from the
    # using statement alone; the referenced type only appears later, in the code
    # body. Verified this the hard way: an earlier version tried to resolve
    # straight to a single ClassName.cs file the way Java's import does, and it
    # returned nothing at all for every using statement in a real dotnet-built
    # project, because the file (Store.cs) and the class inside it (UserStore)
    # don't share a name - nothing Java-style could ever have matched. Resolved
    # instead the same way Go's package-level import already is: fan out to
    # every file in the corresponding directory, since a namespace is the actual
    # granularity "using" operates at.
    matching_prefixes = [prefix for prefix in prefix_map if dotted.startswith(prefix)]
    if not matching_prefixes:
        return [], False
    best_prefix = max(matching_prefixes, key=len)

    remainder = dotted[len(best_prefix):]
    root = prefix_map[best_prefix]
    namespace_dir = root if not remainder else root.joinpath(*remainder.split("."))
    if not namespace_dir.is_dir():
        return [], False

    return sorted(namespace_dir.glob("*.cs")), len(matching_prefixes) > 1


def _load_csharp_implicit_usings(repo_path: Path, source_paths: list[Path]) -> dict[Path, list[str]]:
    """Load explicit MSBuild ``Using`` items for each C# file's scope."""
    config_paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        current = Path(dirpath)
        for filename in filenames:
            if filename == "Directory.Build.props" or filename.endswith(".csproj"):
                config_paths.append(current / filename)

    values_by_config: dict[Path, list[str]] = {}
    for config_path in config_paths:
        try:
            root = ET.parse(config_path).getroot()
        except (ET.ParseError, OSError):
            continue
        values = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == "Using":
                include = element.attrib.get("Include", "").strip()
                if include:
                    values.append(include)
        values_by_config[config_path] = values

    result: dict[Path, list[str]] = {}
    for source_path in source_paths:
        # A set, not a list: `config.parent in ancestors` below is checked
        # once per config_path for every source_path, so a repo with many
        # .cs files and many csproj/Directory.Build.props configs pays this
        # membership test O(source_paths x config_paths) times - a list scan
        # for each one adds a further O(depth) factor a set lookup avoids.
        ancestors = {source_path.parent, *source_path.parent.parents}
        applicable_props = [
            config for config in config_paths
            if config.parent in ancestors
            and config.name == "Directory.Build.props"
        ]
        project_configs = [
            config for config in config_paths
            if config.parent in ancestors and config.suffix == ".csproj"
        ]
        applicable = applicable_props
        if project_configs:
            applicable.append(max(project_configs, key=lambda p: (len(p.parts), str(p))))
        imports: list[str] = []
        for config in sorted(applicable, key=lambda p: (len(p.parts), str(p))):
            for value in values_by_config.get(config, []):
                if value not in imports:
                    imports.append(value)
        if imports:
            result[source_path] = imports
    return result


def _python_source_roots(repo_path: Path) -> list[Path]:
    # A monorepo can hold several independent Python projects, each with its own
    # top-level package one or more directories below repo_path (src/aletheore/,
    # github-app/app_server/) rather than directly inside it (a plain app/ at the repo
    # root). Absolute imports inside each project resolve against that project's own
    # root, not repo_path itself - walk each __init__.py chain up to the first ancestor
    # that isn't itself a package, and use its parent as a resolution root. repo_path is
    # always included too, so single-project repos (top-level package directly under
    # repo_path) keep resolving exactly as before.
    #
    # Walked the same way _iter_source_files walks (IGNORED_DIRS + nested git
    # roots excluded), not a raw rglob - an unfiltered rglob("__init__.py")
    # previously found a stale worktree's own copy of, say, github-app/, added
    # its path as a second, bogus root, and since roots is an unordered set,
    # resolution could nondeterministically pick that bogus root over the
    # real one - a real file's own imports would then resolve to the
    # worktree's duplicate path instead of the real target, so the real file
    # never showed up in anything's imported_by and got misreported as dead
    # code. Confirmed via a real self-scan with an active worktree at
    # .claude/worktrees/<name>/ - every app_server module main.py imports
    # (webhook handlers, API routers) was wrongly flagged unreachable this
    # way, even though _iter_source_files' own nested-git-root exclusion
    # already kept the worktree's files out of the module list itself.
    roots: set[Path] = {repo_path}
    nested_git_roots = _nested_git_roots(repo_path)
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        current_dir = Path(dirpath)
        if any(root in current_dir.parents or root == current_dir for root in nested_git_roots):
            dirnames[:] = []
            continue
        if "__init__.py" not in filenames:
            continue
        directory = current_dir
        while directory != repo_path and (directory.parent / "__init__.py").exists():
            directory = directory.parent
        roots.add(directory.parent)
    # Built from a set, whose iteration order depends on Path hashing -
    # stable within one process but not across separate interpreter runs
    # (PYTHONHASHSEED is randomized per-process by default), which made an
    # ambiguous absolute import (resolvable via more than one root) resolve
    # differently between runs of the same repo - _resolve_python_module
    # below tries roots in order and returns on the first match. Sorting
    # can't know which root a real `python` interpreter would prefer for
    # such an import, but it does make the same repo always produce the
    # same answer. Shallower roots first: a root closer to repo_path is
    # more likely to be the actual configured import root (repo root
    # itself, or a top-level src/ layout) than one nested deep inside a
    # single subproject.
    return sorted(roots, key=lambda p: (len(p.parts), str(p)))


@lru_cache(maxsize=8192)
def _listing_at(directory: str, mtime_ns: int) -> frozenset[str]:
    # Keyed by the directory's mtime, which changes on every add, remove or rename inside it, so a long-lived
    # process (the MCP watcher re-scanning) never reads a stale listing while a scan still lists each
    # directory once instead of once per import candidate.
    return frozenset(os.listdir(directory))


def _names_in(directory: Path) -> frozenset[str]:
    return _listing_at(str(directory), directory.stat().st_mtime_ns)


def _exists_exact_case(repo_path: Path, candidate: Path) -> bool:
    """True only if candidate exists AND every path component under repo_path
    matches the on-disk spelling. A plain exists() is case-insensitive on
    macOS/Windows, so `from pkg import Point` (a class re-exported by
    pkg/__init__.py) would otherwise "resolve" to pkg/Point.py when the real
    module is pkg/point.py - attaching the edge to the wrong file name.

    A candidate outside repo_path (an import root beside it) is checked from the nearest ancestor it shares
    with repo_path, so the components that were discovered on disk are all verified and the part of the path
    the caller typed is not.
    """
    if not candidate.exists():
        return False
    try:
        anchor = repo_path
        parts = candidate.relative_to(anchor).parts
    except ValueError:
        try:
            anchor = Path(os.path.commonpath([repo_path, candidate]))
            parts = candidate.relative_to(anchor).parts
        except ValueError:  # different drives: nothing shared to anchor on
            anchor = candidate.parent
            parts = candidate.parts[-1:]
    directory = anchor
    for part in parts:
        if part in (".", ".."):
            directory = directory / part
            continue
        try:
            if part not in _names_in(directory):
                # Flash Review catch: _listing_at is keyed on the directory's mtime, which assumes mtime
                # changes on every add/remove/rename - true in general, but coarse on some filesystems
                # (1-second resolution on HFS+, some network mounts). A module created and imported within
                # the same mtime tick as the cached listing would otherwise report a real file as missing.
                # Re-listing live (uncached) once before giving up keeps the common case cheap (this only
                # runs when the cache already says "not found") while closing the race - os.listdir, not
                # exists(), so the exact-case guarantee this function exists for still holds.
                if part not in frozenset(os.listdir(directory)):
                    return False
        except OSError:
            return False
        directory = directory / part
    return True


def _module_or_package_path(repo_path: Path, as_path: Path) -> str | None:
    candidate_module = Path(as_path.as_posix() + ".py")
    candidate_package = as_path / "__init__.py"
    if _exists_exact_case(repo_path, candidate_module):
        return _rel(repo_path, candidate_module)
    if _exists_exact_case(repo_path, candidate_package):
        return _rel(repo_path, candidate_package)
    return None


def _resolve_python_module(
    repo_path: Path,
    dotted: str,
    from_file: Path | None = None,
    source_roots: list[Path] | None = None,
) -> tuple[str | None, bool]:
    """Returns (resolved module path, was_ambiguous) - was_ambiguous is True
    when more than one source root resolved this dotted path and a fixed
    tiebreak (source_roots' own order - see its sort in _python_source_roots)
    had to pick one, as opposed to there only ever having been one root that
    could resolve it.
    """
    if not dotted:
        return None, False

    if dotted.startswith("."):
        # Relative import ("from ..services.sessions import x"). tree-sitter hands us
        # the leading dots as literal text in the dotted string, so dot_count is how
        # many levels up from from_file's own package to resolve from: one dot means
        # "the package containing from_file" (from_file.parent itself), each
        # additional dot goes up one more parent directory.
        if from_file is None:
            return None, False
        dot_count = len(dotted) - len(dotted.lstrip("."))
        remainder = dotted[dot_count:]
        base_dir = from_file.parent
        for _ in range(dot_count - 1):
            base_dir = base_dir.parent
        as_path = base_dir if not remainder else base_dir / Path(*remainder.split("."))
        return _module_or_package_path(repo_path, as_path), False

    roots = source_roots if source_roots is not None else [repo_path]
    matches: list[str] = []
    for root in roots:
        target = _module_or_package_path(repo_path, root / Path(*dotted.split(".")))
        if target is not None:
            matches.append(target)
            if len(roots) == 1:
                # Only one root exists at all - it can't be ambiguous, so
                # there is nothing to gain from continuing to check others.
                break
    if not matches:
        return None, False
    return matches[0], len(matches) > 1


def _resolve_python_from_import(
    repo_path: Path,
    module_name: str,
    imported_name: str,
    from_file: Path,
    source_roots: list[Path] | None = None,
) -> tuple[str | None, bool]:
    # A relative module_name already ends in the dots that separate it from what
    # follows ("." or ".." or "..services.sessions"); appending imported_name with an
    # extra "." separator only when module_name does NOT already end in a dot avoids
    # turning "from . import helpers" (single dot: current package) into an
    # accidental double dot (parent package) - which silently resolves to the wrong
    # file rather than raising an error, so it's easy to miss without a real repo to
    # test against.
    if module_name and not module_name.endswith("."):
        submodule_dotted = f"{module_name}.{imported_name}"
    else:
        submodule_dotted = f"{module_name}{imported_name}"
    target, ambiguous = _resolve_python_module(repo_path, submodule_dotted, from_file, source_roots)
    if target is not None:
        return target, ambiguous
    return _resolve_python_module(repo_path, module_name, from_file, source_roots)


JS_FAMILY_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")


def _load_js_workspaces(repo_path: Path) -> dict[str, str]:
    """name -> repo-relative dir for every package.json that declares a "name"
    (npm/yarn/pnpm workspace packages, or any in-repo package). A bare import
    naming one of them is an import of that package's source, not of a
    published copy."""
    import json

    from aletheore.manifests import discover_manifests

    found: dict[str, str] = {}
    discovery = discover_manifests(repo_path)
    for rel_dir in discovery["dirs"]:
        if rel_dir == "." or "package.json" not in discovery["files"][rel_dir]:
            continue
        try:
            data = json.loads(read_text_lenient(repo_path / rel_dir / "package.json"))
        except json.JSONDecodeError:
            continue
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name:
            found.setdefault(name, rel_dir)
    return found


def _strip_jsonc(text: str) -> str:
    """JSON with comments and trailing commas (tsconfig.json's dialect) as plain JSON."""
    text = text.lstrip("﻿")
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
        elif ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] not in "\r\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(ch)
            i += 1
    stripped = "".join(out)
    # Trailing commas: a comma whose next non-space character closes an object or array.
    result: list[str] = []
    in_string = False
    i, n = 0, len(stripped)
    while i < n:
        ch = stripped[i]
        if in_string:
            result.append(ch)
            if ch == "\\" and i + 1 < n:
                result.append(stripped[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            result.append(ch)
        elif ch == ",":
            j = i + 1
            while j < n and stripped[j] in " \t\r\n":
                j += 1
            if j >= n or stripped[j] not in "}]":
                result.append(ch)
        else:
            result.append(ch)
        i += 1
    return "".join(result)


def _read_tsconfig(path: Path) -> dict | None:
    try:
        data = json.loads(_strip_jsonc(read_text_lenient(path)))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _tsconfig_extends_targets(config_dir: Path, value) -> list[Path]:
    """Config files named by an `extends` value (a string, or a list since TS 5.0).
    Only relative and absolute paths are followed; a bare package name lives in
    node_modules, which is never scanned."""
    if isinstance(value, str):
        names = [value]
    elif isinstance(value, list):
        names = [v for v in value if isinstance(v, str)]
    else:
        names = []
    targets: list[Path] = []
    for name in names:
        if not (name.startswith(".") or os.path.isabs(name)):
            continue
        base = Path(os.path.normpath(config_dir / name))
        for candidate in (base, base.with_name(base.name + ".json")):
            if candidate.is_file():
                targets.append(candidate)
                break
    return targets


def _effective_ts_options(path: Path, top_dir: Path, seen: frozenset, depth: int = 0) -> dict:
    """{"base_url", "paths", "paths_dir"} of one tsconfig after resolving `extends`
    (a child's baseUrl / paths replace the parent's wholesale, as in TypeScript)."""
    options: dict = {"base_url": None, "paths": None, "paths_dir": None}
    key = os.path.normcase(str(path))
    if key in seen or depth > 10:
        return options
    data = _read_tsconfig(path)
    if data is None:
        return options
    config_dir = path.parent
    template = top_dir.as_posix()
    for parent in _tsconfig_extends_targets(config_dir, data.get("extends")):
        inherited = _effective_ts_options(parent, top_dir, seen | {key}, depth + 1)
        for field in ("base_url", "paths", "paths_dir"):
            if inherited[field] is not None:
                options[field] = inherited[field]
    compiler = data.get("compilerOptions")
    if isinstance(compiler, dict):
        base_url = compiler.get("baseUrl")
        if isinstance(base_url, str):
            options["base_url"] = os.path.normpath(config_dir / base_url.replace("${configDir}", template))
        paths = compiler.get("paths")
        if isinstance(paths, dict):
            options["paths"] = {
                pattern: [t.replace("${configDir}", template) for t in targets if isinstance(t, str)]
                for pattern, targets in paths.items()
                if isinstance(pattern, str) and isinstance(targets, list)
            }
            options["paths_dir"] = str(config_dir)
    return options


def _load_ts_aliases(repo_path: Path) -> list[dict]:
    """One entry per tsconfig.json / jsconfig.json that defines `paths` or `baseUrl`
    (its own or inherited through `extends`), deepest directory first:
    {"dir": repo-relative dir, "base_url": absolute dir or None,
     "paths": [(pattern, [absolute target paths])]}. A file is resolved against the
    configs in its own ancestor directories, so a package's tsconfig wins over the root's."""
    from aletheore.manifests import discover_named_files

    scopes: list[dict] = []
    seen_dirs: set[str] = set()
    found = discover_named_files(repo_path, frozenset({"tsconfig.json", "jsconfig.json"}))
    # tsconfig.json wins over jsconfig.json in the same directory.
    found.sort(key=lambda rel: (rel.count("/"), rel.endswith("jsconfig.json"), rel))
    for rel in found:
        rel_dir = rel.rpartition("/")[0] or "."
        if rel_dir in seen_dirs:
            continue
        config_path = repo_path / Path(*rel.split("/"))
        options = _effective_ts_options(config_path, config_path.parent, frozenset())
        if options["base_url"] is None and not options["paths"]:
            continue
        seen_dirs.add(rel_dir)
        paths_root = options["base_url"] or options["paths_dir"]
        entries = []
        for pattern, targets in (options["paths"] or {}).items():
            entries.append((pattern, [os.path.normpath(os.path.join(paths_root, t)) for t in targets]))
        scopes.append({"dir": rel_dir, "base_url": options["base_url"], "paths": entries})
    scopes.sort(key=lambda scope: (-(0 if scope["dir"] == "." else scope["dir"].count("/") + 1), scope["dir"]))
    return scopes


def _alias_file(repo_path: Path, base: str) -> str | None:
    """The repo file an alias target names: as written, with a JS-family extension
    added, or as a directory's index file. Never inside node_modules."""
    candidates = [base]
    candidates.extend(base + ext for ext in JS_FAMILY_EXTENSIONS)
    candidates.extend(os.path.join(base, "index" + ext) for ext in JS_FAMILY_EXTENSIONS)
    for candidate in candidates:
        if os.path.isfile(candidate):
            rel = _rel(repo_path, Path(candidate))
            if rel is not None and "node_modules" not in rel.split("/"):
                return rel
    return None


def _match_ts_pattern(pattern: str, spec: str) -> str | None:
    """The text a `*` in the pattern stands for ("" with no star), or None if no match."""
    if "*" not in pattern:
        return "" if pattern == spec else None
    prefix, _, suffix = pattern.partition("*")
    if len(spec) >= len(prefix) + len(suffix) and spec.startswith(prefix) and spec.endswith(suffix):
        return spec[len(prefix): len(spec) - len(suffix)]
    return None


def _resolve_ts_alias(repo_path: Path, from_file: Path, spec: str, scopes: list[dict]) -> str | None:
    from_rel = _rel(repo_path, from_file)
    if from_rel is None:
        return None
    for scope in scopes:
        scope_dir = scope["dir"]
        if scope_dir != "." and not from_rel.startswith(scope_dir + "/"):
            continue
        best = None
        for pattern, targets in scope["paths"]:
            star = _match_ts_pattern(pattern, spec)
            prefix_len = len(pattern.partition("*")[0])
            if star is not None and (best is None or prefix_len > best[0]):
                best = (prefix_len, star, targets)
        if best is not None:
            _, star, targets = best
            for target in targets:
                hit = _alias_file(repo_path, target.replace("*", star, 1))
                if hit:
                    return hit
        if scope["base_url"] and not spec.startswith("/"):
            hit = _alias_file(repo_path, os.path.normpath(os.path.join(scope["base_url"], spec)))
            if hit:
                return hit
    return None


_BUILD_OUTPUT_DIRS = ("dist", "build", "out", "lib", "esm", "cjs", "types")


def _js_file_candidates(base: Path) -> list[Path]:
    candidates = [base]
    for ext in JS_FAMILY_EXTENSIONS:
        candidates.append(base.with_suffix(ext) if base.suffix else Path(str(base) + ext))
        candidates.append(base / f"index{ext}")
    return candidates


def _first_existing_file(repo_path: Path, base: Path) -> str | None:
    for candidate in _js_file_candidates(base):
        if candidate.is_file():
            return _rel(repo_path, candidate)
    return None


def _export_target(value) -> str | None:
    """A string target out of a package.json `exports`/`main`-style value,
    preferring source-ish conditions."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("source", "import", "module", "default", "require", "types"):
            if key in value:
                target = _export_target(value[key])
                if target:
                    return target
    return None


def _resolve_js_workspace_import(
    repo_path: Path, spec: str, workspaces: dict[str, str]
) -> str | None:
    import json

    name = None
    for candidate in workspaces:
        if spec == candidate or spec.startswith(candidate + "/"):
            if name is None or len(candidate) > len(name):
                name = candidate
    if name is None:
        return None
    package_dir = repo_path / workspaces[name]
    subpath = spec[len(name):].lstrip("/")

    try:
        manifest = json.loads(read_text_lenient(package_dir / "package.json"))
    except json.JSONDecodeError:
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}

    def under_package(relative: str) -> Path:
        return (package_dir / relative).resolve()

    def prefer_source(relative: str) -> str | None:
        """Resolve a manifest-declared entry, mapping a build-output path
        (dist/index.js) back to the package's source (src/index.ts)."""
        relative = relative[2:] if relative.startswith("./") else relative
        parts = relative.split("/")
        base = under_package(relative)
        if parts and parts[0] in _BUILD_OUTPUT_DIRS:
            rest = "/".join(parts[1:])
            for source_root in ("src", "."):
                stem = Path(rest).with_suffix("") if Path(rest).suffix in (".js", ".mjs", ".cjs", ".ts", ".d.ts") else Path(rest)
                hit = _first_existing_file(repo_path, under_package(f"{source_root}/{stem}" if source_root != "." else str(stem)))
                if hit:
                    return hit
        return _first_existing_file(repo_path, base)

    if not subpath:
        exports = manifest.get("exports")
        entries: list[str] = []
        if isinstance(exports, dict):
            target = _export_target(exports.get(".", exports if "import" in exports or "default" in exports else None))
            if target:
                entries.append(target)
        elif isinstance(exports, str):
            entries.append(exports)
        for key in ("source", "module", "main", "types"):
            value = manifest.get(key)
            if isinstance(value, str):
                entries.append(value)
        for entry in entries:
            hit = prefer_source(entry)
            if hit:
                return hit
        for fallback in ("src/index", "index", "lib/index"):
            hit = _first_existing_file(repo_path, package_dir / fallback)
            if hit:
                return hit
        return None

    exports = manifest.get("exports")
    if isinstance(exports, dict):
        target = _export_target(exports.get("./" + subpath))
        if target:
            hit = prefer_source(target)
            if hit:
                return hit
    for root in ("", "src/"):
        hit = _first_existing_file(repo_path, package_dir / f"{root}{subpath}")
        if hit:
            return hit
    return None


def _resolve_js_import(
    repo_path: Path,
    from_file: Path,
    spec: str,
    js_workspaces: dict[str, str] | None = None,
    ts_aliases: list[dict] | None = None,
) -> str | None:
    if not spec.startswith("."):
        # tsconfig `paths` / `baseUrl` win over a same-named package, as in TypeScript.
        if ts_aliases and not spec.startswith("/"):
            hit = _resolve_ts_alias(repo_path, from_file, spec, ts_aliases)
            if hit is not None:
                return hit
        if js_workspaces and not spec.startswith("/"):
            hit = _resolve_js_workspace_import(repo_path, spec, js_workspaces)
            if hit:
                return hit
        # self-referenced package names, exports conditions and tsconfig paths (see js_resolve)
        from aletheore.scanner.js_resolve import resolve_bare_import

        return resolve_bare_import(repo_path, from_file, spec)
    base = (from_file.parent / spec).resolve()
    # TypeScript order: a ".js"-style spec names the compiled output of a source file, so the source
    # sibling (foo.js -> foo.ts) is tried first; then the path as written, then the extension appended
    # (NOT swapped: "./foo.test" is foo.test.ts, never foo.ts), then a directory index.
    # Only code files are import-graph targets: a css/json/png import is an asset, not a module.
    candidates = []
    if base.suffix in JS_FAMILY_EXTENSIONS:
        swaps = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
        candidates.extend(base.with_suffix(ext) for ext in swaps.get(base.suffix, ()))
        candidates.append(base)
    candidates.extend(Path(f"{base}{ext}") for ext in JS_FAMILY_EXTENSIONS)
    candidates.extend(base / f"index{ext}" for ext in JS_FAMILY_EXTENSIONS)
    for candidate in candidates:
        if candidate.is_file():
            return _rel(repo_path, candidate)
    return None


def _extract_module(
    path: Path,
    repo_path: Path,
    tree: Tree,
    source: bytes,
    language_name: str,
    *,
    python_source_roots: list[Path] | None = None,
    go_module_prefix: str | None = None,
    has_rust_crate_root: bool = False,
    java_source_roots: list[Path] | None = None,
    kotlin_source_roots: list[Path] | None = None,
    php_psr4_map: dict[str, Path] | None = None,
    csharp_prefix_map: dict[str, Path] | None = None,
    csharp_type_owners: dict[str, set[Path]] | None = None,
    csharp_implicit_usings: dict[Path, list[str]] | None = None,
    swift_target_files: dict[str, list[Path]] | None = None,
    java_package_types: dict[str, dict[str, list[Path]]] | None = None,
    java_root_sizes: dict[Path, int] | None = None,
    csharp_index: dict | None = None,
    js_workspaces: dict[str, str] | None = None,
    ts_aliases: list[dict] | None = None,
) -> dict:
    """Turns one already-parsed (tree, source) into a module dict - the
    per-language dispatch every file eventually goes through, whether it
    got here via the sequential path, a parallel worker, or the inline
    java/csharp handling in build_module_graph's main loop (the only three
    callers). Deliberately has no side effects on any caller's edges/
    imported_by_map - those are reconstructed uniformly from every
    collected module's own "imports" list afterward, the same way the
    unchanged_modules cache-hit path already did before this function
    existed, so a module produced here needs no special-casing at the
    merge step regardless of which path produced it.
    """
    rel_path = _rel(repo_path, path)
    resolved_imports: list[str] = []
    # Populated only for a resolved edge whose resolution wasn't a single
    # deterministic outcome - a source root/namespace-prefix/PSR-4-prefix
    # tiebreak among genuine multiple candidates ("inferred"), or a C#
    # type-reference kept despite more than one file declaring that type
    # name ("ambiguous"). Deliberately omitted (both the key and, when
    # empty, the whole field on the returned module dict below) for the
    # common single-candidate case, so this costs nothing in evidence-packet
    # size for the large majority of edges and the six languages whose
    # resolvers are never ambiguous at all (js/ts, go, rust, ruby, c/cpp).
    import_confidence: dict[str, str] = {}

    constants: list[dict] = []
    if language_name != "python":
        # Python's bindings come out of _extract_python, which already has
        # the walk; every other language gets the shared pass.
        constants = _extract_module_constants(tree.root_node, source, language_name)

    if language_name == "python":
        plain_imports, from_imports, functions, classes, constants = _extract_python(tree.root_node, source)

        # target != rel_path is applied here and in the from-import loop
        # below - every other language branch already guards against a
        # file importing itself this way; Python's didn't (audit finding
        # 35). dead_code.py's unreachable-file test is
        # `if not module.get("imported_by", [])`, so a Python file that
        # happens to statically self-import (an absolute `import pkg.mod`
        # from inside pkg/mod.py itself) got a non-empty imported_by purely
        # from itself and silently escaped dead-code detection even if
        # genuinely unused by everything else.
        for dotted in plain_imports:
            target, ambiguous = _resolve_python_module(repo_path, dotted, path, python_source_roots)
            if target is not None and target != rel_path:
                resolved_imports.append(target)
                if ambiguous:
                    import_confidence[target] = "inferred"

        for module_name, names in from_imports:
            targets: set[str] = set()
            if names:
                for name in names:
                    target, ambiguous = _resolve_python_from_import(
                        repo_path, module_name, name, path, python_source_roots
                    )
                    if target is not None and target != rel_path:
                        targets.add(target)
                        if ambiguous:
                            import_confidence[target] = "inferred"
            else:
                target, ambiguous = _resolve_python_module(repo_path, module_name, path, python_source_roots)
                if target is not None and target != rel_path:
                    targets.add(target)
                    if ambiguous:
                        import_confidence[target] = "inferred"
            resolved_imports.extend(sorted(targets))
    elif language_name == "go":
        raw_imports, functions, classes = _extract_go(tree.root_node, source)
        for spec in raw_imports:
            for target in _resolve_go_import(repo_path, go_module_prefix, spec, rel_path):
                if target != rel_path:
                    resolved_imports.append(target)
        # Same-package coupling: Go files of one package never import each other.
        from aletheore.scanner import go_resolve

        for target in go_resolve.same_package_targets(repo_path, path):
            if target != rel_path and target not in resolved_imports:
                resolved_imports.append(target)
        # Files only built under some tags/platforms are alternatives (all variants are linked): mark them.
        if go_resolve.ENABLED:
            for target in resolved_imports:
                if go_resolve.is_build_constrained(repo_path, target):
                    import_confidence[target] = "inferred"
    elif language_name == "rust":
        raw_imports, functions, classes = _extract_rust(tree.root_node, source)
        if has_rust_crate_root:
            from aletheore.scanner import rust_resolve

            # Macro-declared modules and inline `crate::a::b` / `workspace_crate::a::b` paths are not `use` items.
            extra_paths = rust_resolve.extra_imports(repo_path, path, source.decode(errors="ignore"))
            for use_path in [*raw_imports, *extra_paths]:
                target = _resolve_rust_path(repo_path, path, use_path)
                if target is not None and target != rel_path:
                    resolved_imports.append(target)
                if target is not None:
                    for defining in _rust_reexport_targets(repo_path, path, use_path, target):
                        if defining != rel_path:
                            resolved_imports.append(defining)
    elif language_name == "java":
        raw_imports, functions, classes = _extract_java(tree.root_node, source)
        java_refs = _java_type_references(tree.root_node, source) if java_package_types else None
        java_coverage: dict[Path, int] | None = None
        if len(java_source_roots) > 1:
            # how many of this file's imports each source root can satisfy (tie-break between source sets)
            # the fraction (<1) is the root's share of all Java files: equal coverage prefers the primary set
            total_files = sum((java_root_sizes or {}).values()) + 1
            java_coverage = {r: (java_root_sizes or {}).get(r, 0) / total_files for r in java_source_roots}
            for dotted, is_static, is_wildcard in raw_imports:
                segs = dotted.split(".")
                for root in java_source_roots:
                    if is_wildcard and not is_static:
                        hit = root.joinpath(*segs).is_dir()
                    else:
                        hit = _java_class_file(root, segs[:-1] if is_static and not is_wildcard else segs) is not None
                    if hit:
                        java_coverage[root] = java_coverage.get(root, 0) + 1
        for dotted, is_static, is_wildcard in raw_imports:
            targets, ambiguous = _resolve_java_import(
                java_source_roots, dotted, is_static, is_wildcard, importer=path, coverage=java_coverage
            )
            if is_wildcard and not is_static and java_refs is not None:
                # `import a.b.*;` makes every type of the package nameable, not every file a dependency: keep
                # the files whose top-level types this file actually uses (same identifier rule as same-package).
                wanted = java_refs[0]
                names_by_file: dict[Path, set[str]] = {}
                for type_name, files in java_package_types.get(dotted, {}).items():
                    for declaring in files:
                        names_by_file.setdefault(declaring, set()).add(type_name)
                if names_by_file:
                    targets = [t for t in targets if names_by_file.get(t, set()) & wanted]
            for target_path in targets:
                target = _rel(repo_path, target_path)
                if target is not None and target != rel_path:
                    resolved_imports.append(target)
                    if ambiguous:
                        import_confidence[target] = "inferred"
        if java_package_types:
            # Same-package references need no import - see _java_same_package_targets.
            explicit_names = {
                dotted.rsplit(".", 1)[-1] for dotted, is_static, is_wildcard in raw_imports
                if not is_static and not is_wildcard
            }
            already = set(resolved_imports)
            for target_path, tie in _java_same_package_targets(
                path, _extract_java_package(tree.root_node, source), tree.root_node, source,
                explicit_names, java_package_types, refs=java_refs,
            ):
                target = _rel(repo_path, target_path)
                if target is not None and target != rel_path and target not in already:
                    already.add(target)
                    resolved_imports.append(target)
                    if tie:
                        import_confidence[target] = "inferred"
            for target_path, tie in _java_inline_qualified_targets(
                tree.root_node, source, path, java_package_types, java_coverage
            ):
                target = _rel(repo_path, target_path)
                if target is not None and target != rel_path and target not in already:
                    already.add(target)
                    resolved_imports.append(target)
                    if tie:
                        import_confidence[target] = "inferred"
    elif language_name == "kotlin":
        raw_imports, functions, classes = _extract_kotlin(tree.root_node, source)
        for dotted, _is_static, is_wildcard in raw_imports:
            targets, ambiguous = _resolve_kotlin_import(kotlin_source_roots, dotted, is_wildcard)
            for target_path in targets:
                target = _rel(repo_path, target_path)
                if target is not None and target != rel_path:
                    resolved_imports.append(target)
                    if ambiguous:
                        import_confidence[target] = "inferred"
    elif language_name == "ruby":
        raw_imports, functions, classes = _extract_ruby(tree.root_node, source)
        for kind, spec in raw_imports:
            if kind == "require_all":
                for extra_path in _resolve_ruby_require_all(repo_path, spec):
                    extra = _rel(repo_path, extra_path)
                    if extra is not None and extra != rel_path and extra not in resolved_imports:
                        resolved_imports.append(extra)
                continue
            target_path = _resolve_ruby_require(repo_path, path, kind, spec)
            if target_path is not None:
                target = _rel(repo_path, target_path)
                if target is not None and target != rel_path:
                    resolved_imports.append(target)
    elif language_name == "php":
        raw_imports, functions, classes = _extract_php(tree.root_node, source)
        for kind, spec in raw_imports:
            ambiguous = False
            if kind == "use":
                target_path, ambiguous = _resolve_php_use(php_psr4_map, spec)
            else:
                target_path = _resolve_php_include(path, spec)
            if target_path is not None:
                target = _rel(repo_path, target_path)
                if target is not None and target != rel_path:
                    resolved_imports.append(target)
                    if ambiguous:
                        import_confidence[target] = "inferred"
    elif language_name in ("c", "cpp"):
        raw_imports, functions, classes = _extract_c_family(tree.root_node, source)
        for spec in raw_imports:
            target_path = _resolve_c_include(path, spec)
            if target_path is not None:
                target = _rel(repo_path, target_path)
                if target is not None and target != rel_path:
                    resolved_imports.append(target)
    elif language_name == "csharp":
        raw_imports, functions, classes = _extract_csharp(tree.root_node, source)
        raw_imports.extend((csharp_implicit_usings or {}).get(path, []))
        for dotted in raw_imports:
            if csharp_index is not None:
                # `using X;` names a namespace, not a file, so it creates no edge of its own here. What the file
                # really depends on is found by _csharp_type_reference_targets below: a type it names, or an
                # extension method it calls, in a namespace it can see.
                targets = []
                ambiguous = False
            else:
                targets, ambiguous = _resolve_csharp_using(csharp_prefix_map or {}, dotted)
            for target_path in targets:
                target = _rel(repo_path, target_path)
                if target is not None and target != rel_path:
                    resolved_imports.append(target)
                    if ambiguous:
                        import_confidence[target] = "inferred"
        # Same-namespace references need no `using`, so usings alone leave
        # the graph near-empty - see _csharp_type_reference_targets.
        #
        # own_type_names uses _csharp_declared_type_names (the same index
        # that builds csharp_type_owners below), not the local `classes`
        # list: _extract_csharp's own type-walking omits
        # delegate_declaration, so a file that both declares and
        # references its own delegate had that name treated as an
        # external reference - each self-reference silently consumed a
        # slot of the type-edge cap that a real cross-file edge needed,
        # since the self-edge itself is only filtered out afterward
        # (target != rel_path), too late to free the slot back up.
        own_type_names = set(_csharp_declared_type_names(tree.root_node, source))
        already = set(resolved_imports)
        for target_path, type_ref_ambiguous in _csharp_type_reference_targets(
            source, own_type_names, csharp_type_owners or {},
            importer=path, index=csharp_index, usings=list(raw_imports), tree_root=tree.root_node,
        ):
            target = _rel(repo_path, target_path)
            if target is not None and target != rel_path and target not in already:
                already.add(target)
                resolved_imports.append(target)
                if type_ref_ambiguous:
                    import_confidence[target] = "ambiguous"
    elif language_name == "swift":
        raw_imports, functions, classes = _extract_swift(tree.root_node, source)
        for module_name in raw_imports:
            for target_path in _resolve_swift_import(swift_target_files or {}, module_name):
                target = _rel(repo_path, target_path)
                if target is not None and target != rel_path:
                    resolved_imports.append(target)
    else:
        raw_imports, functions, classes = _extract_javascript(tree.root_node, source)
        for spec in raw_imports:
            target = _resolve_js_import(repo_path, path, spec, js_workspaces, ts_aliases)
            # Same target != rel_path guard every other language branch
            # already has - see the Python branch's comment above (audit
            # finding 35). A JS file require()-ing or importing its own
            # path is the same false-negative risk for dead_code.py's
            # `imported_by` check.
            if target is not None and target != rel_path:
                resolved_imports.append(target)

    module: dict = {
        "path": rel_path,
        "language": language_name,
        "imports": resolved_imports,
        "imported_by": [],
        "symbols": {"functions": functions, "classes": classes, "constants": constants},
    }
    if import_confidence:
        module["import_confidence"] = import_confidence
    if language_name == "python" and tree.root_node.has_error:
        # Python's grammar is complete enough that a parse error means the
        # file really has a syntax error (or cannot be parsed by 3.x rules);
        # the symbols found around it are kept, but the file is reported in
        # unparseable_files so it is not mistaken for a clean index.
        module["has_syntax_errors"] = True
    return module


def _read_and_parse(path: Path, parser: Parser, ts_language: Language) -> tuple[bytes, Tree]:
    parser.language = ts_language
    source = path.read_bytes()
    return source, parser.parse(source)


def _parse_and_extract_one(
    path: Path,
    repo_path: Path,
    parser: Parser,
    python_source_roots: list[Path],
    go_module_prefix: str | None,
    has_rust_crate_root: bool,
    php_psr4_map: dict[str, Path],
    js_workspaces: dict[str, str] | None = None,
    ts_aliases: list[dict] | None = None,
) -> dict:
    """Reads, parses, and extracts one file - java/csharp excluded by
    construction (see build_module_graph: only non-java/csharp paths ever
    reach this function, whether called directly for the sequential
    fallback or via _worker_parse_and_extract_one inside a pool worker),
    so no java_source_roots/csharp_* state is threaded through here."""
    language_name, ts_language = LANGUAGE_BY_EXTENSION[path.suffix]
    source, tree = _read_and_parse(path, parser, ts_language)
    return _extract_module(
        path,
        repo_path,
        tree,
        source,
        language_name,
        python_source_roots=python_source_roots,
        go_module_prefix=go_module_prefix,
        has_rust_crate_root=has_rust_crate_root,
        php_psr4_map=php_psr4_map,
        js_workspaces=js_workspaces,
        ts_aliases=ts_aliases,
    )


# Below this many files still needing a fresh parse, build_module_graph stays
# fully sequential (no ProcessPoolExecutor at all) - measured directly
# (2026-08-27): creating and tearing down a pool costs ~150ms fixed, wall
# clock, regardless of how little work is inside it, while a typical source
# file's own parse+extract (measured on this project's own files) runs
# 0.5-30ms depending on size. A repo with only a couple hundred files to
# parse would finish sequentially before a pool even finishes spinning up;
# this threshold keeps that common case on exactly today's code path, with
# real parallelism only kicking in where there's enough work for process-
# spawn overhead to be clearly worth it.
PARALLEL_PARSE_MIN_FILES = 200

# Escape hatch for memory-constrained hosts: spawning os.cpu_count() worker
# processes multiplies memory (each independently loads the tree-sitter
# grammar libraries and holds its own in-flight ASTs), which is a clear win
# on a developer's own machine but can be a net negative on a container with
# a tight memory limit. No CLI flag for this - scan_worker/jobs.py's
# _run_scan sets this env var on the hosted scan subprocess specifically
# (same convention as ALETHEORE_DISABLE_LOCAL_SCAN_CACHE and friends
# alongside it), leaving local CLI users on the parallel default untouched.
_DISABLE_PARALLEL_PARSE_ENV = "ALETHEORE_DISABLE_PARALLEL_PARSE"


def _parallel_parse_disabled() -> bool:
    return bool(os.environ.get(_DISABLE_PARALLEL_PARSE_ENV))

# Set once per worker process by _init_worker, read by
# _worker_parse_and_extract_one - never touched by the main process. Module-
# level rather than passed as a function argument because every argument to
# a function submitted to ProcessPoolExecutor gets pickled on every single
# call; this way the read-only per-scan state (source roots, prefix maps)
# and the worker's own Parser are pickled once at pool-startup time via
# initargs, not once per file.
_worker_state: dict = {}


def _init_worker(
    repo_path: Path,
    python_source_roots: list[Path],
    go_module_prefix: str | None,
    has_rust_crate_root: bool,
    php_psr4_map: dict[str, Path],
    js_workspaces: dict[str, str] | None = None,
    ts_aliases: list[dict] | None = None,
) -> None:
    _worker_state["repo_path"] = repo_path
    _worker_state["python_source_roots"] = python_source_roots
    _worker_state["go_module_prefix"] = go_module_prefix
    _worker_state["has_rust_crate_root"] = has_rust_crate_root
    _worker_state["php_psr4_map"] = php_psr4_map
    _worker_state["js_workspaces"] = js_workspaces
    _worker_state["ts_aliases"] = ts_aliases
    # One Parser per worker process, reused across every file that worker
    # ever handles - not recreated per file, same discipline the sequential
    # path's own single `parser` variable already follows today.
    _worker_state["parser"] = Parser()


def _worker_parse_and_extract_one(path: Path) -> tuple[bool, dict]:
    # Caught here, inside the worker, rather than left to propagate through
    # the pool boundary: ProcessPoolExecutor.map() re-raises a worker's
    # exception when the caller's iteration reaches that result, which
    # would crash the entire scan on one unreadable file (permission
    # denied, a mid-scan race, or a path exceeding Windows' legacy MAX_PATH
    # limit) instead of degrading to an unparseable-files entry the way
    # every other per-file failure class in this module already does.
    #
    # Flash Review finding on an earlier version of this fix: a magic dict
    # key (e.g. "__marker__") distinguishing a failure payload from a real
    # module dict is only safe as long as no real module ever happens to
    # have that key - a real, if unlikely, footgun if this dict's shape
    # ever grows a colliding field. A (success, payload) tuple makes success
    # a structural property of the return value (its own position) rather
    # than a convention about payload content, so it can never collide with
    # anything _extract_module's dict shape does or ever will contain.
    try:
        return True, _parse_and_extract_one(
            path,
            _worker_state["repo_path"],
            _worker_state["parser"],
            _worker_state["python_source_roots"],
            _worker_state["go_module_prefix"],
            _worker_state["has_rust_crate_root"],
            _worker_state["php_psr4_map"],
            _worker_state.get("js_workspaces"),
            _worker_state.get("ts_aliases"),
        )
    except OSError as exc:
        return False, {
            "path": _rel(_worker_state["repo_path"], path),
            "reason": f"could not read file: {exc}",
        }


# Explicit override, matching PARALLEL_PARSE_MIN_FILES's "no CLI flag"
# convention for scan-tuning knobs. Always wins over every auto-detected
# signal below.
_PARALLEL_PARSE_JOBS_ENV = "ALETHEORE_PARALLEL_PARSE_JOBS"

_CGROUP_V2_CPU_MAX_PATH = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_CFS_QUOTA_PATH = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_CFS_PERIOD_PATH = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def _cgroup_v2_cpu_quota(path: Path = _CGROUP_V2_CPU_MAX_PATH) -> int | None:
    """cgroup v2's own CPU quota (Docker `--cpus`, most Kubernetes CPU
    *limits*) - the only signal that reflects a quota-based restriction
    (CPU time throttling), verified directly against real containers
    (2026-08-27): a container capped at `docker run --cpus=2` on a
    10-core host still reports os.cpu_count() == 10 AND
    len(os.sched_getaffinity(0)) == 10 - sched_getaffinity only sees
    explicit core pinning (`--cpuset-cpus`), a different, rarer
    mechanism (see _combined_cpu_quota below). cpu.max holds
    "<quota> <period>" in microseconds, or "max <period>" when
    unrestricted.
    """
    try:
        raw = path.read_text().split()
    except OSError:
        return None
    if len(raw) != 2 or raw[0] == "max":
        return None
    try:
        quota, period = int(raw[0]), int(raw[1])
    except ValueError:
        return None
    if period <= 0:
        return None
    return max(1, math.ceil(quota / period))


def _cgroup_v1_cpu_quota(
    quota_path: Path = _CGROUP_V1_CFS_QUOTA_PATH, period_path: Path = _CGROUP_V1_CFS_PERIOD_PATH
) -> int | None:
    """cgroup v1 fallback (cpu.max is v2-only) - same quota/period shape,
    split across two files instead of one, quota=-1 meaning unrestricted."""
    try:
        quota = int(quota_path.read_text().strip())
        period = int(period_path.read_text().strip())
    except (OSError, ValueError):
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, math.ceil(quota / period))


def _available_parallelism() -> int:
    """How many worker processes it's actually sensible to spawn.

    os.cpu_count() (ProcessPoolExecutor's own default) reports the
    host/VM's raw core count - not what this process is actually allowed
    to use inside a resource-limited container. A repo scanned inside a
    CI job capped at 2 CPUs on a 32-core host would otherwise spawn 32
    worker processes, each independently loading the tree-sitter grammar
    libraries, for a container that can only actually run 2 of them at
    once - wasted memory and no real speedup, the opposite of this
    feature's purpose.

    Takes the tightest of every real restriction mechanism, since a
    container can have any combination active: a cgroup CPU quota
    (invisible to both os.cpu_count() and os.sched_getaffinity - see
    _cgroup_v2_cpu_quota's docstring) and CPU-set pinning
    (os.sched_getaffinity - k8s static CPU manager policy, invisible to
    the cgroup quota check). Verified directly against real `docker run`
    containers across all 4 combinations (unrestricted, quota-only,
    pinning-only, both together) before this was written, not guessed.

    ALETHEORE_PARALLEL_PARSE_JOBS always wins over all of this when set.
    """
    override = os.environ.get(_PARALLEL_PARSE_JOBS_ENV)
    if override:
        try:
            parsed = int(override)
        except ValueError:
            parsed = None
        if parsed is not None and parsed > 0:
            return parsed

    candidates = [os.cpu_count() or 1]
    for quota_fn in (_cgroup_v2_cpu_quota, _cgroup_v1_cpu_quota):
        quota = quota_fn()
        if quota is not None:
            candidates.append(quota)
    if hasattr(os, "sched_getaffinity"):
        candidates.append(len(os.sched_getaffinity(0)))
    return max(1, min(candidates))


def _map_in_pool_with_recovery(func, items: list, *, chunksize: int = 1, **executor_kwargs) -> tuple[list, bool]:
    """executor.map over a process pool, with results in input order, that
    survives a worker dying outright (OOM-killer, SIGKILL, segfault).

    A per-file try/except inside `func` only covers an exception a LIVE worker
    raises; a worker that is gone raises BrokenProcessPool out of the whole
    map(), which used to abort the entire scan and discard every result already
    collected. So: keep what was already yielded, and retry only the remaining
    items in a fresh pool with half as many workers. That recovers the common
    case (memory pressure from too many workers) with real results for every
    file. If a single worker still dies, return what was collected and
    complete=False: the caller must either fail loudly or mark the rest as
    unprocessed. It must never invent a per-file result, because a stage that
    caches per file (secrets, error handling) would then store a fabricated
    "nothing found" under that file's content hash and report it clean on every
    later scan without ever having scanned it.

    Returns (results, complete). results is always an in-order prefix of the
    inputs; complete is True only when every item has a real result.
    """
    results: list = []
    workers = _available_parallelism()
    while True:
        try:
            with ProcessPoolExecutor(max_workers=workers, **executor_kwargs) as executor:
                results.extend(executor.map(func, items[len(results):], chunksize=chunksize))
            return results, True
        except BrokenProcessPool:
            if workers <= 1:
                return results, False
            workers = max(1, workers // 2)


def _parse_many_in_parallel(
    paths: list[Path],
    repo_path: Path,
    python_source_roots: list[Path],
    go_module_prefix: str | None,
    has_rust_crate_root: bool,
    php_psr4_map: dict[str, Path],
    js_workspaces: dict[str, str] | None = None,
    ts_aliases: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    # map (not submit+as_completed) so results come back in input order - not
    # load-bearing for correctness (module/edge order was already unasserted
    # anywhere - see the design doc), just keeps output deterministic across
    # runs rather than depending on which worker happens to finish first.
    results, complete = _map_in_pool_with_recovery(
        _worker_parse_and_extract_one,
        paths,
        initializer=_init_worker,
        initargs=(repo_path, python_source_roots, go_module_prefix, has_rust_crate_root, php_psr4_map, js_workspaces, ts_aliases),
    )
    if not complete:
        # Workers kept dying even one at a time, so these files were never
        # parsed. Report them as unparseable (the same contract
        # _worker_parse_and_extract_one's own OSError catch gives a single
        # unreadable file), which the output already surfaces, instead of
        # losing the whole scan. Not cached anywhere, so the next scan retries.
        for path in paths[len(results):]:
            results.append((False, {
                "path": _rel(repo_path, path),
                "reason": "worker process died while parsing (BrokenProcessPool)",
            }))
    modules = []
    failures = []
    for ok, payload in results:
        if ok:
            modules.append(payload)
        else:
            failures.append(payload)
    return modules, failures


# --- Cross-file edge pass for PHP (implicit class references) and C/C++ (include search paths) ---
#
# Both need the whole module list (a class index / a header index), so they run once after
# every file is parsed instead of inside the per-file resolver. They only ever ADD targets
# that are modules of this scan, and never add a self edge.

# Heredoc/nowdoc bodies come first so quotes and `//` inside them cannot start another match; the
# closing tag may be indented (PHP 7.3+ flexible heredoc) and the body may be empty.
_PHP_NOISE_RE = re.compile(
    r"<<<[ \t]*(?P<q>[\"']?)(?P<tag>[A-Za-z_]\w*)(?P=q)[^\n]*\n(?:.*?\n)?[ \t]*(?P=tag)\b"
    r"|/\*.*?\*/|//[^\n]*|#(?!\[)[^\n]*|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"",
    re.DOTALL,
)
_PHP_NAMESPACE_RE = re.compile(r"(?m)^\s*namespace\s+([\w\\]+)\s*[;{]")
_PHP_DECL_RE = re.compile(
    r"(?m)^\s*(?:(?:final|abstract|readonly)\s+)*(?:class|interface|trait|enum)\s+(\w+)"
)
# Column-0 `use` is a namespace import (already an edge); an indented one is a trait use, which
# is a real reference, so it stays in the scanned text.
_PHP_TOP_USE_RE = re.compile(r"(?m)^use\s+[^;]+;")
_PHP_NS_LINE_RE = re.compile(r"(?m)^\s*namespace\s+[^;{\n]+[;{]")
_PHP_NAME_RE = re.compile(r"(?<![\w$>:\\])(\\?[A-Z]\w*(?:\\\w+)*)")


def _php_code_only(source: str) -> str:
    """Source with comments and string literals blanked out."""
    return _PHP_NOISE_RE.sub(" ", source)


def _php_aliases(code: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for match in re.finditer(r"(?m)^\s*use\s+(?!function\b|const\b)([^;]+);", code):
        body = match.group(1)
        if "{" in body:
            prefix, _, group = body.partition("{")
            prefix = prefix.strip().lstrip("\\")
            names = [prefix + item.strip() for item in group.rstrip("} \n").split(",") if item.strip()]
        else:
            names = [item.strip().lstrip("\\") for item in body.split(",")]
        for name in names:
            parsed = re.match(r"([\w\\]+)(?:\s+as\s+(\w+))?$", name)
            if parsed and "\\" in parsed.group(1):
                aliases[parsed.group(2) or parsed.group(1).split("\\")[-1]] = parsed.group(1)
    return aliases


def _add_php_implicit_edges(repo_path: Path, modules: list[dict], php_psr4_map: dict[str, Path]) -> None:
    """Edges for class references that need no `use`: a bare name resolved in the file's own
    namespace (`new RequestOptions`, `extends Base`, a type hint), a fully qualified inline name
    (`\\Foo\\Bar::x()`), or a `use`d namespace alias followed by a sub-name (`Bar\\Baz`).

    False-edge rule: a name only becomes an edge when it is outside comments and string
    literals, starts with an uppercase letter, is not a member access (`->x`, `::X`, `$x`), and
    resolves to a class/interface/trait/enum that exactly one scanned file declares (by the
    declarations' namespace) or that the composer.json PSR-4 map places in a scanned file. Names
    that resolve to nothing in the repo (framework classes, builtins) add nothing.
    """
    php_modules = [m for m in modules if m.get("language") == "php"]
    if not php_modules:
        return
    known = {m["path"] for m in modules}
    parsed: dict[str, tuple[str, str]] = {}
    index: dict[str, set[str]] = {}
    for module in php_modules:
        try:
            raw = (repo_path / module["path"]).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        code = _php_code_only(raw)
        ns_match = _PHP_NAMESPACE_RE.search(code)
        namespace = ns_match.group(1) if ns_match else ""
        parsed[module["path"]] = (code, namespace)
        for decl in _PHP_DECL_RE.finditer(code):
            index.setdefault((namespace + "\\" if namespace else "") + decl.group(1), set()).add(module["path"])

    def lookup(fqcn: str) -> str | None:
        owners = index.get(fqcn)
        if owners:
            return next(iter(owners)) if len(owners) == 1 else None
        if php_psr4_map:
            target, _ = _resolve_php_use(php_psr4_map, fqcn)
            if target is not None:
                rel = _rel(repo_path, target)
                if rel in known:
                    return rel
        return None

    for module in php_modules:
        entry = parsed.get(module["path"])
        if entry is None:
            continue
        code, namespace = entry
        aliases = _php_aliases(code)
        body = _PHP_NS_LINE_RE.sub(" ", _PHP_TOP_USE_RE.sub(" ", code))
        present = set(module.get("imports", []))
        for match in _PHP_NAME_RE.finditer(body):
            name = match.group(1)
            if name.startswith("\\"):
                fqcn = name[1:]
            else:
                first, _, rest = name.partition("\\")
                if first in aliases:
                    fqcn = aliases[first] + ("\\" + rest if rest else "")
                else:
                    fqcn = (namespace + "\\" if namespace else "") + name
            target = lookup(fqcn)
            if target and target != module["path"] and target not in present:
                present.add(target)
                module.setdefault("imports", []).append(target)


# Ruby constants that exist without any require, plus the stdlib ones nearly every project uses.
# A repo that reopens one of these (`class Hash`) must not capture every file that mentions Hash.
_RUBY_BUILTIN_CONSTANTS = frozenset(
    "ARGF ARGV ArgumentError Array BasicObject Binding Class Comparable Complex ConditionVariable Data Dir ENV EOFError "
    "Encoding EncodingError Enumerable Enumerator Errno Exception FalseClass Fiber FiberError File FileTest Float "
    "FloatDomainError FrozenError GC Hash IO IOError IndexError Integer Interrupt Kernel KeyError LoadError "
    "LocalJumpError Marshal MatchData Math Method Module Mutex NameError NilClass NoMemoryError NoMethodError "
    "NotImplementedError Numeric Object ObjectSpace Proc Process Queue Random Range RangeError Rational Regexp "
    "RegexpError RuntimeError STDERR STDIN STDOUT ScriptError SecurityError Signal SignalException SizedQueue "
    "StandardError StopIteration String Struct Symbol SyntaxError SystemCallError SystemExit SystemStackError "
    "Thread ThreadError Time TracePoint TrueClass TypeError UnboundMethod UncaughtThrowError Warning ZeroDivisionError "
    "Set FileUtils Pathname JSON YAML Psych Date DateTime Logger Forwardable Singleton Tempfile Open3 Digest Benchmark "
    "ERB CGI Net URI OpenStruct Timeout Shellwords Zlib OptionParser SecureRandom StringIO Gem RbConfig Delegator "
    "SimpleDelegator Monitor MonitorMixin".split()
)


def _ruby_const_path(node: Node, source: bytes) -> list[str] | None:
    """Segments of `A::B::C` when every part is a constant; a leading `::` yields ["", "A", ...]."""
    segments: list[str] = []
    current: Node | None = node
    while current is not None:
        if current.type == "constant":
            segments.append(source[current.start_byte:current.end_byte].decode(errors="ignore"))
            segments.reverse()
            return segments
        if current.type != "scope_resolution":
            return None
        name = current.child_by_field_name("name")
        if name is None or name.type != "constant":
            return None
        segments.append(source[name.start_byte:name.end_byte].decode(errors="ignore"))
        scope = current.child_by_field_name("scope")
        if scope is None:  # `::Foo`
            segments.append("")
            segments.reverse()
            return segments
        current = scope
    return None


def _ruby_constant_facts(source: bytes, parser: Parser) -> tuple[set[str], set[tuple[str, tuple[str, ...]]], list[tuple[str, str]]]:
    """(constants defined, (reference, lexical nesting) pairs, (autoload constant, path) pairs) of one file.

    Nesting entries are fully qualified, innermost last, one per enclosing `class`/`module`
    (`class A::B` is a single entry), which is Ruby's lexical scope chain."""
    parser.language = RUBY_LANGUAGE
    tree = parser.parse(source)
    defs: set[str] = set()
    refs: set[tuple[str, tuple[str, ...]]] = set()
    autoloads: list[tuple[str, str]] = []

    def qualify(segments: list[str], nest: tuple[str, ...]) -> str:
        if segments and segments[0] == "":
            return "::".join(segments[1:])
        return (nest[-1] + "::" if nest else "") + "::".join(segments)

    stack: list[tuple[Node, tuple[str, ...]]] = [(tree.root_node, ())]
    while stack:
        n, nest = stack.pop()
        t = n.type
        if t in ("class", "module"):
            name = n.child_by_field_name("name")
            segments = _ruby_const_path(name, source) if name is not None else None
            if segments:
                fq = qualify(segments, nest)
                defs.add(fq)
                for child in n.children:
                    if child == name:
                        continue
                    stack.append((child, nest if child.type == "superclass" else nest + (fq,)))
                continue
        elif t == "assignment":
            left = n.child_by_field_name("left")
            segments = _ruby_const_path(left, source) if left is not None else None
            if segments:
                defs.add(qualify(segments, nest))
                right = n.child_by_field_name("right")
                if right is not None:
                    stack.append((right, nest))
                continue
        elif t == "call":
            method = n.child_by_field_name("method")
            if (n.child_by_field_name("receiver") is None and method is not None and method.type == "identifier"
                    and source[method.start_byte:method.end_byte] == b"autoload"):
                args = n.child_by_field_name("arguments")
                sym = path = None
                if args is not None:
                    for arg in args.children:
                        if arg.type == "simple_symbol" and sym is None:
                            sym = source[arg.start_byte + 1:arg.end_byte].decode(errors="ignore")
                        elif arg.type == "string" and path is None:
                            path = "".join(
                                source[p.start_byte:p.end_byte].decode(errors="ignore")
                                for p in arg.children if p.type == "string_content"
                            )
                if sym and path:
                    fq = (nest[-1] + "::" if nest else "") + sym
                    # Not added to `defs`: the autoloading file does not define X, the file it
                    # points at does (the caller registers that file as a definer).
                    autoloads.append((fq, path))
        elif t in ("constant", "scope_resolution"):
            segments = _ruby_const_path(n, source)
            if segments:
                ref = "::".join(segments[1:]) if segments[0] == "" else "::".join(segments)
                refs.add((("::" + ref) if segments[0] == "" else ref, nest))
                continue
            if t == "scope_resolution":
                # `expr::NAME` (e.g. `Serve.singleton_class::LIVERELOAD_DIR`): NAME is looked up
                # on a runtime value, not lexically, so only the scope expression is scanned.
                scope = n.child_by_field_name("scope")
                if scope is not None:
                    stack.append((scope, nest))
                continue
        stack.extend((child, nest) for child in n.children)
    return defs, refs, autoloads


def _add_ruby_constant_edges(repo_path: Path, modules: list[dict]) -> None:
    """Edges for constants referenced in one file and defined in exactly one other file.

    A reference resolves through Ruby's lexical scope chain (innermost enclosing class/module
    first, then the top level), stopping at the first level where the name is defined anywhere.
    No edge when: that name is defined in more than one file (a reopened `module Jekyll`), is
    defined in the referencing file itself, is a Ruby/stdlib constant, or is defined nowhere in
    the repo. `autoload :X, "path"` counts as a definition of X in the file it points at.
    Ancestor (inheritance) lookup is not modelled, so a constant only reachable through a
    superclass adds no edge.
    """
    ruby_modules = [m for m in modules if m.get("language") == "ruby"]
    if len(ruby_modules) < 2:
        return
    known = {m["path"] for m in modules}
    parser = Parser()
    per_file: dict[str, tuple[set[str], set[tuple[str, tuple[str, ...]]]]] = {}
    definers: dict[str, set[str]] = {}
    for module in ruby_modules:
        file_path = repo_path / module["path"]
        try:
            if file_path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                continue
            defs, refs, autoloads = _ruby_constant_facts(file_path.read_bytes(), parser)
        except OSError:
            continue
        per_file[module["path"]] = (defs, refs)
        for fq in defs:
            definers.setdefault(fq, set()).add(module["path"])
        for fq, spec in autoloads:
            target = _resolve_ruby_require(repo_path, file_path, "require", spec)
            rel = _rel(repo_path, target) if target is not None else None
            if rel in known:
                definers.setdefault(fq, set()).add(rel)
    for fq in [k for k in definers if k.split("::")[0] in _RUBY_BUILTIN_CONSTANTS]:
        del definers[fq]
    for module in ruby_modules:
        entry = per_file.get(module["path"])
        if entry is None:
            continue
        own, refs = entry
        present = set(module.get("imports", []))
        for ref, nest in refs:
            if ref.startswith("::"):
                candidates = [ref[2:]]
            else:
                candidates = [f"{scope}::{ref}" for scope in reversed(nest)] + [ref]
            for candidate in candidates:
                if candidate in own:
                    break
                owners = definers.get(candidate)
                if owners:
                    if len(owners) == 1:
                        target = next(iter(owners))
                        if target != module["path"] and target not in present:
                            present.add(target)
                            module.setdefault("imports", []).append(target)
                    break


def _retag_c_headers(modules: list[dict]) -> None:
    """`.h` is parsed with the C++ grammar (a superset) and tagged "cpp" by the shared extension
    map, which mislabels the headers of a C project. Decide per header from the code that uses it,
    leaving the map and every `.hpp`/`.hh` untouched.

    Votes: a same-stem sibling source (`jv.c` for `jv.h`, weight 2) and every non-header source
    that includes it (weight 1 each). More C votes than C++ votes makes it "c". Headers that only
    other headers include take the majority of those headers' already-decided tags. No votes at all
    keeps "cpp", so a header-only C++ library is unaffected.
    """
    by_path = {m["path"]: m for m in modules}
    headers = [m for m in modules if m["path"].endswith(".h") and m.get("language") == "cpp"]
    if not headers:
        return
    importers: dict[str, list[str]] = {}
    for module in modules:
        if module.get("language") in ("c", "cpp"):
            for target in module.get("imports", []):
                importers.setdefault(target, []).append(module["path"])
    stems: dict[str, list[str]] = {}
    for module in modules:
        path = module["path"]
        if module.get("language") in ("c", "cpp") and not path.endswith((".h", ".hpp", ".hh", ".hxx")):
            stems.setdefault(path.rsplit("/", 1)[-1].rsplit(".", 1)[0], []).append(path)

    def tally(header: dict, include_headers: bool) -> tuple[int, int]:
        path = header["path"]
        c_votes = cpp_votes = 0
        for sibling in stems.get(path.rsplit("/", 1)[-1][:-2], []):
            if by_path[sibling]["language"] == "c":
                c_votes += 2
            else:
                cpp_votes += 2
        for importer in importers.get(path, []):
            is_header = importer.endswith((".h", ".hpp", ".hh", ".hxx"))
            if is_header and not include_headers:
                continue
            if by_path[importer]["language"] == "c":
                c_votes += 1
            else:
                cpp_votes += 1
        return c_votes, cpp_votes

    decided: set[str] = set()
    for header in headers:
        c_votes, cpp_votes = tally(header, False)
        if c_votes or cpp_votes:
            decided.add(header["path"])
            if c_votes > cpp_votes:
                header["language"] = "c"
    for _ in range(3):
        changed = False
        for header in headers:
            if header["path"] in decided:
                continue
            c_votes, cpp_votes = tally(header, True)
            # only headers already tagged "c" can outvote; undecided cpp headers carry no evidence
            if c_votes > cpp_votes and any(
                by_path[i]["language"] == "c" for i in importers.get(header["path"], [])
            ):
                header["language"] = "c"
                decided.add(header["path"])
                changed = True
        if not changed:
            break


_C_INCLUDE_RE = re.compile(r'(?m)^[ \t]*#[ \t]*include[ \t]*([<"])([^>"\n]+)[>"]')
_C_HEADER_EXTS = (".h", ".hh", ".hpp", ".hxx", ".inc")
_C_SEARCH_DIRS = ("include", "src", "")


def _add_c_include_edges(repo_path: Path, modules: list[dict]) -> None:
    """Resolve #include lines the per-file resolver (includer's directory only) cannot.

    Search order: the includer's directory (quoted form only), then include/, src/, the repo
    root, then any scanned header whose path ends with the spec (only when exactly one does, or
    one shares the longest directory prefix with the includer). Angle includes skip the
    includer's directory and src/ (include/ and the root only), and only use the suffix fallback
    when the spec has a directory part (`<fmt/base.h>`), so `<stdio.h>`-style system headers
    never bind to a same-named file under src/. Unresolved includes are dropped.
    Also scans lines the AST walk misses (an #include inside an initializer or other odd spot).
    """
    c_modules = [m for m in modules if m.get("language") in ("c", "cpp")]
    if not c_modules:
        return
    known = {m["path"] for m in modules}
    by_base: dict[str, list[str]] = {}
    for path in known:
        if path.endswith(_C_HEADER_EXTS):
            by_base.setdefault(path.rsplit("/", 1)[-1], []).append(path)

    for module in c_modules:
        includer = module["path"]
        includer_dir = includer.rsplit("/", 1)[0] if "/" in includer else ""
        try:
            source = (repo_path / includer).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        present = set(module.get("imports", []))
        for match in _C_INCLUDE_RE.finditer(source):
            quoted = match.group(1) == '"'
            spec = match.group(2).strip()
            # Angle form: include/ and the root only, so <config.h>/<util.h> style system
            # headers do not bind to a same-named file under src/.
            bases = [includer_dir, *_C_SEARCH_DIRS] if quoted else ["include", ""]
            target = None
            for base in bases:
                candidate = os.path.normpath(f"{base}/{spec}" if base else spec).replace(os.sep, "/")
                if candidate in known and not candidate.startswith(".."):
                    target = candidate
                    break
            if target is None and (quoted or "/" in spec):
                suffix = os.path.normpath(spec).replace(os.sep, "/")
                matches = [p for p in by_base.get(suffix.rsplit("/", 1)[-1], []) if p == suffix or p.endswith("/" + suffix)]
                if len(matches) == 1:
                    target = matches[0]
                elif len(matches) > 1:
                    def shared(p: str) -> int:
                        n = 0
                        for a, b in zip(p.split("/")[:-1], includer_dir.split("/")):
                            if a != b:
                                break
                            n += 1
                        return n
                    best = max(shared(p) for p in matches)
                    top = [p for p in matches if shared(p) == best]
                    if len(top) == 1:
                        target = top[0]
            if target and target != includer and target not in present:
                present.add(target)
                module.setdefault("imports", []).append(target)


def _compute_resolution_signatures(
    repo_path: Path,
    all_source_paths: list[Path],
    *,
    java_source_roots: list[Path],
    java_package_types: dict[str, dict[str, list[Path]]],
    csharp_summaries: dict[Path, dict],
    csharp_prefix_map: dict[str, Path],
    csharp_index: dict | None,
    csharp_implicit_usings: dict[Path, list[str]],
    php_psr4_map: dict[str, Path],
) -> dict[str, str]:
    """language -> fingerprint of everything OTHER files contribute to how this language's imports resolve.

    A cached module keeps the import list that was resolved against the repo as it was then. That list is only
    still right if the cross-file facts it was resolved against are unchanged, so each cached module records no
    state of its own; instead the scan compares these fingerprints with the ones saved beside the cache:
    - every language: the set of its files (an import resolves by a file existing or not);
    - java: source roots and every package's top-level types (same-package edges, wildcard and import targets);
    - csharp: each file's namespaces, top-level types, extension methods and global usings, the namespace prefix
      map, the csproj project index and implicit usings (type-reference edges);
    - php: each file's namespace and declared classes, and the PSR-4 map (implicit class-reference edges).
    An edit that changes none of these leaves the fingerprint, and so every cached module, untouched."""
    import hashlib

    def rel(p: Path) -> str:
        return _rel(repo_path, p) or str(p)

    paths_by_language: dict[str, list[str]] = {}
    for path in all_source_paths:
        info = LANGUAGE_BY_EXTENSION.get(path.suffix)
        if info is not None:
            paths_by_language.setdefault(info[0], []).append(rel(path))
    extras: dict[str, object] = {}
    if "java" in paths_by_language:
        extras["java"] = [
            sorted(rel(r) for r in java_source_roots),
            {pkg: {name: sorted(rel(p) for p in files) for name, files in sorted(types.items())}
             for pkg, types in sorted(java_package_types.items())},
        ]
    if "csharp" in paths_by_language:
        extras["csharp"] = [
            {rel(p): [s["ns"], sorted(s["types"].items()), s["ext_methods"], s["ext_generic"], s["global_usings"]]
             for p, s in sorted(csharp_summaries.items(), key=lambda kv: rel(kv[0]))},
            sorted((k, rel(v)) for k, v in csharp_prefix_map.items()),
            sorted((rel(p), rel(q) if q else None) for p, q in (csharp_index or {}).get("project_of", {}).items()),
            sorted((rel(p), sorted(rel(q) for q in vs)) for p, vs in (csharp_index or {}).get("visible", {}).items()),
            sorted((rel(p), v) for p, v in csharp_implicit_usings.items()),
        ]
    if "php" in paths_by_language:
        decls: dict[str, list] = {}
        for path in all_source_paths:
            if path.suffix != ".php":
                continue
            try:
                code = _php_code_only(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            ns_match = _PHP_NAMESPACE_RE.search(code)
            decls[rel(path)] = [ns_match.group(1) if ns_match else "", sorted({m.group(1) for m in _PHP_DECL_RE.finditer(code)})]
        extras["php"] = [decls, sorted((k, rel(v)) for k, v in php_psr4_map.items())]
    out: dict[str, str] = {}
    for language, paths in paths_by_language.items():
        digest = hashlib.blake2b(digest_size=12)
        digest.update(json.dumps([sorted(paths), extras.get(language)], default=str, sort_keys=True).encode())
        out[language] = digest.hexdigest()
    return out


def build_module_graph(
    repo_path: Path,
    *,
    unchanged_modules: dict[str, dict] | None = None,
    ignored_paths: list[str] | None = None,
    prior_signatures: dict[str, str] | None = None,
    signatures_out: dict[str, str] | None = None,
) -> tuple[list[dict], dict, list[dict]]:
    """unchanged_modules: path -> a previously-computed module dict (same
    shape this function itself produces) for files known not to have
    changed since that data was computed - skips tree-sitter parsing for
    those paths entirely, reusing the cached dict as-is. A stale entry
    for a file no longer present in repo_path is simply never consulted
    (the walk below only ever looks paths up as it encounters them on
    disk). Defaults to None: fully backward compatible, every file parsed
    fresh, matching this function's behavior before this parameter
    existed.

    prior_signatures / signatures_out: a cached module's imports were resolved against OTHER files (see
    _compute_resolution_signatures). When `prior_signatures` is a dict (the signatures saved with the cache,
    possibly empty), a cached module is reused only if its language's current signature equals the saved one;
    otherwise that language's cached modules are re-extracted so the edges equal a clean scan's. None keeps the
    old reuse-always behavior (callers that cannot supply signatures). `signatures_out`, when given, receives the
    current signatures for the caller to save with its cache."""
    modules: list[dict] = []
    unparseable: list[dict] = []
    imported_by_map: dict[str, list[str]] = {}
    edges: list[list[str]] = []
    from aletheore.scanner import js_resolve as _js_resolve

    _js_resolve.clear_cache()  # package.json / tsconfig are re-read once per scan
    from aletheore.scanner import rust_resolve as _rr; _rr.clear_cache()  # noqa: E702  Rust/Go listings, Cargo.toml
    go_module_prefix = _load_go_modules(repo_path) or _load_go_module_prefix(repo_path)
    has_rust_crate_root = _rust_has_any_crate_root(repo_path)
    python_source_roots = _python_source_roots(repo_path)

    # Walked once, here, and reused by every pre-pass below plus the main
    # loop - these used to each call _iter_source_files(repo_path,
    # ignored_paths) again independently, the exact same full os.walk
    # (each with its own internal _nested_git_roots walk too), every scan,
    # even for a repo with none of that pre-pass's language present and
    # never skipped by unchanged_modules (which only skips per-file
    # parsing). Measured ~35% of a real scan's wall time spent re-walking
    # for languages that aren't even present (audit finding, 2026-10-05).
    all_source_paths = list(_iter_source_files(repo_path, ignored_paths))

    # Java has no single repo-root config naming a module prefix (no go.mod, no
    # Cargo.toml equivalent) - the source root (src/main/java, a bare src/, or the
    # repo root itself) has to be inferred from what each file's own package
    # declaration implies about its directory, so every .java file needs a quick
    # pre-parse before any of them can have their imports resolved.
    java_source_roots: list[Path] = []
    # package -> top-level type name -> declaring files, for same-package references (no import needed).
    java_package_types: dict[str, dict[str, list[Path]]] = {}
    java_root_sizes: dict[Path, int] = {}  # files per source root: the larger set is the primary one on a tie
    oversized_paths: set[Path] = set()
    # Every .java file's (source, Tree) used to be kept alive here for the
    # main loop below to reuse, parsing each file once per scan instead of
    # twice - real, but the wrong trade for a hosted worker: it means every
    # tree in the repo is held simultaneously the moment this pre-pass
    # finishes, before the main loop has consumed even one of them.
    # tree-sitter trees run ~37x their source size; scan-worker/scan-worker-2
    # are capped at a 1GB container memory limit (docker-compose.yml), so a
    # large enough Java repo could OOM-crash the whole scan right here,
    # before any partial result exists to fall back to - a strictly worse
    # failure mode than the extra CPU cost of parsing twice (audit finding
    # 15). Nothing here is retained past its own loop iteration now - each
    # file's tree is parsed, its package read, and then falls out of scope
    # to be freed before the next file's parse even starts, so peak memory
    # stays roughly one file's tree, not every file's.
    pre_parser = Parser()
    pre_parser.language = JAVA_LANGUAGE
    for path in all_source_paths:
        if path.suffix != ".java":
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                oversized_paths.add(path)
                unparseable.append({"path": _rel(repo_path, path), "reason": "file exceeds size limit"})
                continue
            pre_source = path.read_bytes()
        except OSError as exc:
            # A per-file OSError here (permission denied, a race with the
            # file being removed mid-scan, or - the real, reported trigger -
            # a path exceeding Windows' legacy MAX_PATH limit) used to
            # propagate uncaught and crash the entire scan on one
            # unreadable file, unlike every other per-file failure class in
            # this function (size limit, missing grammar), which already
            # degrades to an unparseable-files entry instead.
            unparseable.append({"path": _rel(repo_path, path), "reason": f"could not read file: {exc}"})
            continue
        tree = pre_parser.parse(pre_source)
        package = _extract_java_package(tree.root_node, pre_source)
        root = _java_source_root_for(path, package)
        if root is not None and root not in java_source_roots:
            java_source_roots.append(root)
        if root is not None:
            java_root_sizes[root] = java_root_sizes.get(root, 0) + 1
        for declared_type in _java_top_level_type_names(tree.root_node, pre_source):
            java_package_types.setdefault(package or "", {}).setdefault(declared_type, []).append(path)
    # Appended in _iter_source_files walk order, which is filesystem-dependent
    # and not guaranteed stable across runs/platforms - an ambiguous import
    # (resolvable via more than one root) could otherwise resolve differently
    # between two scans of the identical repo. Same fix, same reasoning, as
    # _python_source_roots' own sort below: shallower roots first, since a
    # root closer to repo_path is more likely to be the actually-configured
    # one than one nested deep inside a single module.
    java_source_roots.sort(key=lambda p: (len(p.parts), str(p)))

    # Same reasoning and mechanism as Java's pre-pass above - Kotlin's package
    # declaration is the only signal for its own source root (Gradle's
    # conventional src/main/kotlin, a bare src/, or the repo root), and .kts
    # script files (build.gradle.kts, etc.) carry no package_header at all, so
    # they fall back to their own containing directory via the same `not
    # package` branch _kotlin_source_root_for shares with _java_source_root_for.
    kotlin_source_roots: list[Path] = []
    kotlin_pre_parser = Parser()
    kotlin_pre_parser.language = KOTLIN_LANGUAGE
    for path in all_source_paths:
        if path.suffix not in (".kt", ".kts"):
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                oversized_paths.add(path)
                unparseable.append({"path": _rel(repo_path, path), "reason": "file exceeds size limit"})
                continue
            pre_source = path.read_bytes()
        except OSError as exc:
            # Same reasoning as the Java pre-pass above.
            unparseable.append({"path": _rel(repo_path, path), "reason": f"could not read file: {exc}"})
            continue
        tree = kotlin_pre_parser.parse(pre_source)
        package = _kotlin_package(tree.root_node, pre_source)
        root = _kotlin_source_root_for(path, package)
        if root is not None and root not in kotlin_source_roots:
            kotlin_source_roots.append(root)
    kotlin_source_roots.sort(key=lambda p: (len(p.parts), str(p)))

    php_psr4_map = _load_php_psr4_map(repo_path)
    js_workspaces = _load_js_workspaces(repo_path)
    ts_aliases = _load_ts_aliases(repo_path)

    # Same reasoning and mechanism as Java's pre-pass above - C# has no repo-root
    # config either, and namespace-mirrors-directory is only a convention here, not
    # compiler-enforced, so it's an even softer heuristic than Java's version. A
    # (prefix -> root) map rather than a plain root list, PSR-4-style, to handle
    # <RootNamespace>'s implicit prefix (see _csharp_prefix_and_root_for).
    csharp_prefix_map: dict[str, Path] = {}
    csharp_source_paths: list[Path] = []
    # Which file declares each type name, for the type-reference edges below.
    csharp_type_owners: dict[str, set[Path]] = {}
    csharp_summaries: dict[Path, dict] = {}
    # Same reasoning as java_source_roots' pre-pass above - nothing here is
    # retained past its own loop iteration either, for the same audit
    # finding 15 memory-peak reason.
    cs_pre_parser = Parser()
    cs_pre_parser.language = CSHARP_LANGUAGE
    for path in all_source_paths:
        if path.suffix != ".cs":
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                oversized_paths.add(path)
                unparseable.append({"path": _rel(repo_path, path), "reason": "file exceeds size limit"})
                continue
            pre_source = path.read_bytes()
        except OSError as exc:
            # Same reasoning as the Java pre-pass above. Not added to
            # csharp_source_paths below - _load_csharp_implicit_usings only
            # needs paths it can actually read.
            unparseable.append({"path": _rel(repo_path, path), "reason": f"could not read file: {exc}"})
            continue
        csharp_source_paths.append(path)
        tree = cs_pre_parser.parse(pre_source)
        namespace = _extract_csharp_namespace(tree.root_node, pre_source)
        result = _csharp_prefix_and_root_for(path, namespace)
        if result is not None:
            prefix, root = result
            csharp_prefix_map.setdefault(prefix, root)
        summary = _csharp_file_summary(tree.root_node, pre_source)
        csharp_summaries[path] = summary
        # Top-level types only: a nested type is reached through its outer type, and indexing it made every
        # test-local `Source`/`Destination` a candidate owner for the whole repo.
        for declared in summary["types"]:
            csharp_type_owners.setdefault(declared, set()).add(path)
    csharp_implicit_usings = _load_csharp_implicit_usings(repo_path, csharp_source_paths)
    csharp_index = _build_csharp_index(repo_path, csharp_summaries) if csharp_summaries else None

    # Swift needs no per-file pre-parse the way Java/C# do - target membership
    # is directory/manifest-derived (see _infer_swift_targets), not read off
    # each file's own declarations - so this is a single cheap call, not a
    # per-file loop.
    swift_target_files = _infer_swift_targets(repo_path, ignored_paths)

    current_signatures = _compute_resolution_signatures(
        repo_path,
        all_source_paths,
        java_source_roots=java_source_roots,
        java_package_types=java_package_types,
        csharp_summaries=csharp_summaries,
        csharp_prefix_map=csharp_prefix_map,
        csharp_index=csharp_index,
        csharp_implicit_usings=csharp_implicit_usings,
        php_psr4_map=php_psr4_map,
    )
    if signatures_out is not None:
        signatures_out.clear()
        signatures_out.update(current_signatures)

    parser = Parser()
    # Non-java/kotlin/csharp files still needing a fresh parse - collected
    # here instead of being parsed inline, so they can go through the
    # process pool below (in one batch) when there's enough of them, or
    # the sequential fallback otherwise. Java/Kotlin/C# are never added
    # here: they're either resolved from their pre-parsed tree below, or
    # (the rare pre-pass/main-walk-saw-different-filesystem-state case)
    # parsed inline immediately, in both cases without ever leaving this
    # process - the worker pool has no java_source_roots/
    # kotlin_source_roots/csharp_prefix_map to resolve those languages'
    # imports with, by design (see the design doc).
    paths_needing_parse: list[Path] = []

    for path in all_source_paths:
        rel_path = _rel(repo_path, path)

        if unchanged_modules is not None and rel_path in unchanged_modules:
            cached_language = unchanged_modules[rel_path].get("language")
            if prior_signatures is None or (
                prior_signatures.get(cached_language) is not None
                and prior_signatures.get(cached_language) == current_signatures.get(cached_language)
            ):
                modules.append(unchanged_modules[rel_path])
                continue
            # The cross-file facts this language resolves against changed: fall through and re-extract.

        language_info = LANGUAGE_BY_EXTENSION.get(path.suffix)
        if language_info is None:
            if path.suffix in KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR:
                unparseable.append(
                    {"path": rel_path, "reason": f"no grammar registered for {path.suffix}"}
                )
            continue

        language_name, ts_language = language_info
        if path in oversized_paths:
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                oversized_paths.add(path)
                unparseable.append({"path": rel_path, "reason": "file exceeds size limit"})
                continue

            if language_name in ("java", "kotlin", "csharp", "swift"):
                # Re-parsed here rather than reusing a tree the pre-pass above
                # held onto - see that pre-pass's own comment (audit finding
                # 15). The pre-pass already had to parse every .java/.kt/.cs
                # file once to read its package/namespace before any of them
                # could have a source root inferred at all; this is
                # deliberately a second parse per file, trading CPU for never
                # holding more than about one file's tree in memory at a time.
                # Swift never needed a per-file pre-parse in the first place
                # (see swift_target_files above) but is processed here too,
                # serially in-process, for the same reason the others are: the
                # worker pool below has no java_source_roots/kotlin_source_roots/
                # csharp_prefix_map/swift_target_files to resolve any of these
                # four languages' imports with.
                parser.language = ts_language
                source = path.read_bytes()
                tree = parser.parse(source)
            else:
                paths_needing_parse.append(path)
                continue
        except OSError as exc:
            # Same reasoning as the pre-pass loops above - a per-file
            # OSError (permission denied, a mid-scan race, or a path
            # exceeding Windows' legacy MAX_PATH limit) must degrade to an
            # unparseable-files entry, not crash the whole scan.
            unparseable.append({"path": rel_path, "reason": f"could not read file: {exc}"})
            continue

        modules.append(
            _extract_module(
                path,
                repo_path,
                tree,
                source,
                language_name,
                java_source_roots=java_source_roots,
                kotlin_source_roots=kotlin_source_roots,
                csharp_prefix_map=csharp_prefix_map,
                csharp_type_owners=csharp_type_owners,
                csharp_implicit_usings=csharp_implicit_usings,
                swift_target_files=swift_target_files,
                java_package_types=java_package_types,
                java_root_sizes=java_root_sizes,
                csharp_index=csharp_index,
            )
        )

    # Three independent gates, all must pass: the file-count threshold
    # (pool-creation cost isn't worth it below PARALLEL_PARSE_MIN_FILES),
    # not explicitly disabled (scan_worker/jobs.py sets
    # ALETHEORE_DISABLE_PARALLEL_PARSE on the hosted scan-worker's memory-
    # constrained containers), and _available_parallelism() > 1 - a
    # container whose real CPU quota only allows one worker anyway (see
    # _available_parallelism's docstring - confirmed against this
    # project's own hosted scan-worker containers, cpus: "1.0" in
    # docker-compose.yml) gets zero benefit from a pool sized at 1: only
    # the ~150ms pool-creation cost plus a second process independently
    # loading every tree-sitter grammar library, for exactly the same
    # work the sequential path below already does in this process.
    if (
        len(paths_needing_parse) >= PARALLEL_PARSE_MIN_FILES
        and not _parallel_parse_disabled()
        and _available_parallelism() > 1
    ):
        parsed_modules, parse_failures = _parse_many_in_parallel(
            paths_needing_parse,
            repo_path,
            python_source_roots,
            go_module_prefix,
            has_rust_crate_root,
            php_psr4_map,
            js_workspaces,
            ts_aliases,
        )
        modules.extend(parsed_modules)
        unparseable.extend(parse_failures)
    else:
        for path in paths_needing_parse:
            try:
                module = _parse_and_extract_one(
                    path,
                    repo_path,
                    parser,
                    python_source_roots,
                    go_module_prefix,
                    has_rust_crate_root,
                    php_psr4_map,
                    js_workspaces,
                    ts_aliases,
                )
            except OSError as exc:
                # Same reasoning as the pre-pass loops above.
                unparseable.append(
                    {"path": _rel(repo_path, path), "reason": f"could not read file: {exc}"}
                )
                continue
            modules.append(module)

    # edges/imported_by_map reconstructed uniformly from every module's own
    # "imports" list, regardless of which of the four paths above produced
    # it (cache hit, java/csharp pre-parsed, java/csharp rare-fallback, or
    # sequential/parallel fresh parse) - the same reconstruction the
    # unchanged_modules cache-hit branch always did, just applied once here
    # instead of inline per module.
    _add_php_implicit_edges(repo_path, modules, php_psr4_map)
    _add_c_include_edges(repo_path, modules)
    _add_ruby_constant_edges(repo_path, modules)
    _retag_c_headers(modules)

    for module in modules:
        rel_path = module["path"]
        # De-duplicated, order kept: `from . import a, b` and a plain import
        # of the same file both resolve to one target, and listing it twice
        # doubled the edge as well.
        if "imports" in module:
            module["imports"] = list(dict.fromkeys(module["imports"]))
        for target in module.get("imports", []):
            edges.append([rel_path, target])
            imported_by_map.setdefault(target, []).append(rel_path)

    for module in modules:
        module["imported_by"] = sorted(imported_by_map.get(module["path"], []))

    for module in modules:
        if module.get("has_syntax_errors"):
            unparseable.append(
                {"path": module["path"], "reason": "syntax error (symbols found around it are still indexed)"}
            )

    nodes = sorted({m["path"] for m in modules})
    dependency_graph = {"nodes": nodes, "edges": edges}

    return modules, dependency_graph, unparseable
