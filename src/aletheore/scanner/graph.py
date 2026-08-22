import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import tree_sitter_c as tsc
import tree_sitter_c_sharp as tscsharp
import tree_sitter_cpp as tscpp
import tree_sitter_go as tsgo
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_php as tsphp
import tree_sitter_python as tspython
import tree_sitter_ruby as tsruby
import tree_sitter_rust as tsrust
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser, Tree

from aletheore.repo_config import is_ignored
from aletheore.scanner.detect import IGNORED_DIRS, _nested_git_roots

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())
GO_LANGUAGE = Language(tsgo.language())
RUST_LANGUAGE = Language(tsrust.language())
JAVA_LANGUAGE = Language(tsjava.language())
RUBY_LANGUAGE = Language(tsruby.language())
PHP_LANGUAGE = Language(tsphp.language_php())
C_LANGUAGE = Language(tsc.language())
CPP_LANGUAGE = Language(tscpp.language())
CSHARP_LANGUAGE = Language(tscsharp.language())

LANGUAGE_BY_EXTENSION = {
    ".py": ("python", PY_LANGUAGE),
    ".js": ("javascript", JS_LANGUAGE),
    ".jsx": ("javascript", JS_LANGUAGE),
    ".ts": ("typescript", TS_LANGUAGE),
    ".tsx": ("typescript", TSX_LANGUAGE),
    ".go": ("go", GO_LANGUAGE),
    ".rs": ("rust", RUST_LANGUAGE),
    ".java": ("java", JAVA_LANGUAGE),
    ".rb": ("ruby", RUBY_LANGUAGE),
    ".php": ("php", PHP_LANGUAGE),
    ".c": ("c", C_LANGUAGE),
    # Headers are ambiguous C-or-C++; the C++ grammar (a superset) parses valid C
    # too, so .h is treated as C++ rather than needing its own heuristic.
    ".h": ("cpp", CPP_LANGUAGE),
    ".hpp": ("cpp", CPP_LANGUAGE),
    ".cpp": ("cpp", CPP_LANGUAGE),
    ".cc": ("cpp", CPP_LANGUAGE),
    ".cs": ("csharp", CSHARP_LANGUAGE),
}

# Extensions that are recognizable programming languages we don't yet have a grammar
# for. Only these count as "unparseable" coverage gaps. Everything else (assets, docs,
# configs, lock files, tool caches not already excluded by IGNORED_DIRS) was never
# source code and is skipped silently rather than reported as a gap - otherwise
# unparseable_files balloons with noise (a real repo scan turned up 19k+ .json files
# from an untracked cache directory before IGNORED_DIRS was widened, none of which
# were ever "unparseable source").
KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR = {
    ".swift",
    ".kt", ".kts", ".m", ".mm", ".scala",
}

MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024


def _iter_source_files(repo_path: Path, ignored_paths: list[str] | None = None):
    # os.walk(followlinks=False) rather than Path.rglob("*") - a symlinked
    # directory in the tree would otherwise have its contents walked and
    # parsed as if they were part of this repo. followlinks only stops
    # descent into symlinked *directories* - a symlinked file sitting
    # directly in a real directory still needs its own is_symlink() check.
    nested_git_roots = _nested_git_roots(repo_path)
    patterns = ignored_paths or []
    paths = []
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        current_dir = Path(dirpath)
        rel_dir = current_dir.relative_to(repo_path).as_posix()
        dirnames[:] = [
            d
            for d in dirnames
            if d not in IGNORED_DIRS
            and not is_ignored(f"{rel_dir}/{d}" if rel_dir != "." else d, patterns)
        ]
        if any(root in current_dir.parents or root == current_dir for root in nested_git_roots):
            dirnames[:] = []
            continue
        for filename in filenames:
            path = current_dir / filename
            if path.is_symlink() or not path.is_file():
                continue
            rel_path = path.relative_to(repo_path).as_posix()
            if is_ignored(rel_path, patterns):
                continue
            paths.append(path)
    yield from sorted(paths)


def _rel(repo_path: Path, path: Path) -> str | None:
    """None when path resolves outside repo_path entirely - a relative
    import/include that climbs above the repo root (e.g. `#include
    "../../../../etc/passwd"`, or excess `super::` segments in Rust)
    resolves to a real file on disk that simply isn't part of this repo.
    Treated the same as an unresolved/external import rather than letting
    path.relative_to()'s ValueError crash the whole scan.
    """
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
    than the function_definition node directly.
    """
    params_node = enclosing_node.child_by_field_name("parameters")
    if params_node is None:
        declarator = enclosing_node.child_by_field_name("declarator")
        if declarator is not None:
            params_node = declarator.child_by_field_name("parameters")
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


def _symbol_entry(
    source: bytes,
    name_node: Node,
    enclosing_node: Node,
    docstring: str | None = None,
    return_type: str | None = None,
    is_public: bool = True,
    is_pure_declaration: bool = False,
) -> dict:
    return {
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
    }


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
                    in_type_body = (
                        n.parent is not None
                        and n.parent.type == "body_statement"
                        and n.parent.parent is not None
                        and n.parent.parent.type in ("class", "module")
                    )
                    if in_type_body or is_top_level(n):
                        add(lhs, n)
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
                            if arg.type == "string":
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
    for line in go_mod.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("module "):
            return line[len("module "):].strip()
    return None


def _resolve_go_import(repo_path: Path, module_prefix: str | None, import_path: str) -> list[str]:
    # Go doesn't import individual files, it imports whole packages (directories) - every
    # non-test .go file in that directory is part of what gets pulled in, so one import
    # statement can fan out to several edges. An import that doesn't start with the
    # module's own declared prefix is external (stdlib or a third-party module) and never
    # resolves to a local file, matching how an unresolved Python/JS import is silently
    # dropped rather than treated as an error.
    if not module_prefix or not import_path.startswith(module_prefix):
        return []

    remainder = import_path[len(module_prefix):].lstrip("/")
    package_dir = repo_path if not remainder else repo_path / Path(*remainder.split("/"))
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
        prefix_node = node.children[0]
        list_node = node.children[-1]
        prefix = text(prefix_node)
        names = [text(c) for c in list_node.children if c.type in ("identifier", "self")]
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
                # node follows the "use" keyword and precedes the ";".
                for child in n.children:
                    if child.type not in ("use", ";"):
                        imports.extend(_rust_use_paths(child, source))
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
                        imports.append(f"self::{name}")
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
    if file_path.name == "mod.rs":
        return file_path.parent
    return file_path.parent / file_path.stem


def _resolve_rust_module_dir(search_dir: Path, name: str) -> Path | None:
    if (search_dir / f"{name}.rs").exists():
        return search_dir / f"{name}.rs"
    if (search_dir / name / "mod.rs").exists():
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


def _resolve_rust_path(repo_path: Path, from_file: Path, path: str) -> str | None:
    segments = path.split("::")
    if not segments:
        return None

    head = segments[0]
    rest = segments[1:]

    if head == "crate":
        target = _walk_rust_segments(_rust_crate_src_dir(repo_path, from_file), rest)
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
    else:
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


def _resolve_java_import(
    source_roots: list[Path], dotted: str, is_static: bool, is_wildcard: bool
) -> list[Path]:
    segments = dotted.split(".")
    if not segments:
        return []

    if is_wildcard:
        # dotted is a package path with no class name - every .java file directly
        # in that package's directory is what a wildcard import pulls in, the same
        # "import the whole package" fan-out Go's package-level imports need.
        for root in source_roots:
            package_dir = root.joinpath(*segments)
            if package_dir.is_dir():
                return sorted(package_dir.glob("*.java"))
        return []

    if is_static:
        # "import static a.b.C.MEMBER" - MEMBER is a field or method, not a class;
        # the file that actually exists is a.b.C.java.
        segments = segments[:-1]
        if not segments:
            return []

    for root in source_roots:
        target = _java_class_file(root, segments)
        if target is not None:
            return [target]

    # One segment short: the same fallback Python/Rust already use - the last
    # segment might be a nested class rather than its own top-level file, in
    # which case the containing class's own file is the real target.
    if len(segments) > 1:
        for root in source_roots:
            target = _java_class_file(root, segments[:-1])
            if target is not None:
                return [target]

    return []


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
                    if method_name in ("require", "require_relative"):
                        args_node = n.child_by_field_name("arguments")
                        if args_node is not None:
                            for arg in args_node.children:
                                if arg.type == "string":
                                    for part in arg.children:
                                        if part.type == "string_content":
                                            imports.append((method_name, text(part)))
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
    if kind == "require_relative":
        # Always relative to the current file's own directory - unambiguous,
        # exactly like a relative JS import.
        base_dir = from_file.parent
    else:
        # Plain "require" is genuinely ambiguous - the overwhelming majority of
        # real-world uses are gems (external), but a project's own lib/ directory
        # is the near-universal Ruby convention for what else a bare require can
        # name (that's what ends up on $LOAD_PATH for a gem's own internal
        # requires). No lib/ directory at all -> nothing local to resolve to,
        # treated as external the same way an unrecognized Go/Rust/Java import is.
        base_dir = repo_path / "lib"
        if not base_dir.is_dir():
            return None

    spec_with_ext = spec if spec.endswith(".rb") else f"{spec}.rb"
    candidate = (base_dir / spec_with_ext).resolve()
    return candidate if candidate.is_file() else None


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
                for clause in n.children:
                    if clause.type == "namespace_use_clause":
                        for grandchild in clause.children:
                            if grandchild.type in ("qualified_name", "name"):
                                imports.append(("use", text(grandchild)))
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
        data = json.loads(composer_json.read_text(encoding="utf-8", errors="ignore"))
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


def _resolve_php_use(psr4_map: dict[str, Path], qualified_name: str) -> Path | None:
    qualified_name = qualified_name.lstrip("\\")

    # PSR-4 requires the longest matching prefix to win when more than one could
    # apply (e.g. "App\\" -> src/ and "App\\Tests\\" -> tests/ both matching
    # "App\Tests\Foo" - the more specific one is correct).
    best_prefix: str | None = None
    for prefix in psr4_map:
        normalized = prefix.rstrip("\\")
        if qualified_name == normalized or qualified_name.startswith(normalized + "\\"):
            if best_prefix is None or len(normalized) > len(best_prefix.rstrip("\\")):
                best_prefix = prefix

    if best_prefix is None:
        return None

    remainder = qualified_name[len(best_prefix.rstrip("\\")):].lstrip("\\")
    if not remainder:
        return None

    parts = remainder.split("\\")
    candidate = psr4_map[best_prefix].joinpath(*parts[:-1], f"{parts[-1]}.php")
    return candidate if candidate.is_file() else None


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
                            {
                                "name": name,
                                "start_line": n.start_point[0] + 1,
                                "end_line": n.end_point[0] + 1,
                                "params": _params_text(source, n),
                                "docstring": _strip_jsdoc_stars(raw_doc) if raw_doc else None,
                                "return_type": (
                                    text(type_node) if type_node is not None else None
                                ),
                                "is_public": not _is_nested_in_function(n),
                            }
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
_CSHARP_MAX_TYPE_EDGES = 40

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


def _csharp_type_reference_targets(
    source: bytes,
    own_types: set[str],
    type_owners: dict[str, set[Path]],
) -> list[Path]:
    """Files this one depends on by *naming their types*, not by importing them.

    C# needs no `using` to reference a type in the same namespace, so an
    import-derived graph is near-empty for the language. Measured on
    AutoMapper/AutoMapper: 512 .cs files, 230 `using` directives in the entire
    repository, 156 of them `System.*` - so 419 of 512 files declared no
    dependency at all and community detection returned 474 clusters for 513
    modules, one per file. That is not a parsing bug; there is genuinely nothing
    to parse. The dependency exists in the body, where a type is named.

    Deliberately conservative, because a false edge invents a relationship the
    wiki will then confidently explain: only names declared in exactly ONE file
    in the repository count, so an ambiguous name contributes nothing.
    """
    referenced: list[Path] = []
    seen: set[str] = set()
    for match in _IDENTIFIER_RE.finditer(source.decode("utf-8", "ignore")):
        name = match.group(0)
        if (
            name in seen
            or name in own_types
            or len(name) < _CSHARP_MIN_TYPE_NAME
        ):
            continue
        owners = type_owners.get(name)
        # Exactly one declaring file, or we cannot say which one is meant.
        if owners is None or len(owners) != 1:
            continue
        seen.add(name)
        referenced.append(next(iter(owners)))
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


def _resolve_csharp_using(prefix_map: dict[str, Path], dotted: str) -> list[Path]:
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
    best_prefix: str | None = None
    for prefix in prefix_map:
        if dotted.startswith(prefix):
            if best_prefix is None or len(prefix) > len(best_prefix):
                best_prefix = prefix

    if best_prefix is None:
        return []

    remainder = dotted[len(best_prefix):]
    root = prefix_map[best_prefix]
    namespace_dir = root if not remainder else root.joinpath(*remainder.split("."))
    if not namespace_dir.is_dir():
        return []

    return sorted(namespace_dir.glob("*.cs"))


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
        ancestors = [source_path.parent, *source_path.parent.parents]
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


def _module_or_package_path(repo_path: Path, as_path: Path) -> str | None:
    candidate_module = Path(as_path.as_posix() + ".py")
    candidate_package = as_path / "__init__.py"
    if candidate_module.exists():
        return _rel(repo_path, candidate_module)
    if candidate_package.exists():
        return _rel(repo_path, candidate_package)
    return None


def _resolve_python_module(
    repo_path: Path,
    dotted: str,
    from_file: Path | None = None,
    source_roots: list[Path] | None = None,
) -> str | None:
    if not dotted:
        return None

    if dotted.startswith("."):
        # Relative import ("from ..services.sessions import x"). tree-sitter hands us
        # the leading dots as literal text in the dotted string, so dot_count is how
        # many levels up from from_file's own package to resolve from: one dot means
        # "the package containing from_file" (from_file.parent itself), each
        # additional dot goes up one more parent directory.
        if from_file is None:
            return None
        dot_count = len(dotted) - len(dotted.lstrip("."))
        remainder = dotted[dot_count:]
        base_dir = from_file.parent
        for _ in range(dot_count - 1):
            base_dir = base_dir.parent
        as_path = base_dir if not remainder else base_dir / Path(*remainder.split("."))
        return _module_or_package_path(repo_path, as_path)

    for root in (source_roots if source_roots is not None else [repo_path]):
        target = _module_or_package_path(repo_path, root / Path(*dotted.split(".")))
        if target is not None:
            return target
    return None


def _resolve_python_from_import(
    repo_path: Path,
    module_name: str,
    imported_name: str,
    from_file: Path,
    source_roots: list[Path] | None = None,
) -> str | None:
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
    target = _resolve_python_module(repo_path, submodule_dotted, from_file, source_roots)
    if target is not None:
        return target
    return _resolve_python_module(repo_path, module_name, from_file, source_roots)


JS_FAMILY_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx")


def _resolve_js_import(repo_path: Path, from_file: Path, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = (from_file.parent / spec).resolve()
    candidates = [base]
    for ext in JS_FAMILY_EXTENSIONS:
        candidates.append(base.with_suffix(ext))
        candidates.append(base / f"index{ext}")
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return _rel(repo_path, candidate)
    return None


def build_module_graph(
    repo_path: Path,
    *,
    unchanged_modules: dict[str, dict] | None = None,
    ignored_paths: list[str] | None = None,
) -> tuple[list[dict], dict, list[dict]]:
    """unchanged_modules: path -> a previously-computed module dict (same
    shape this function itself produces) for files known not to have
    changed since that data was computed - skips tree-sitter parsing for
    those paths entirely, reusing the cached dict as-is. A stale entry
    for a file no longer present in repo_path is simply never consulted
    (the walk below only ever looks paths up as it encounters them on
    disk). Defaults to None: fully backward compatible, every file parsed
    fresh, matching this function's behavior before this parameter
    existed."""
    modules: list[dict] = []
    unparseable: list[dict] = []
    imported_by_map: dict[str, list[str]] = {}
    edges: list[list[str]] = []
    go_module_prefix = _load_go_module_prefix(repo_path)
    has_rust_crate_root = _rust_has_any_crate_root(repo_path)
    python_source_roots = _python_source_roots(repo_path)

    # Java has no single repo-root config naming a module prefix (no go.mod, no
    # Cargo.toml equivalent) - the source root (src/main/java, a bare src/, or the
    # repo root itself) has to be inferred from what each file's own package
    # declaration implies about its directory, so every .java file needs a quick
    # pre-parse before any of them can have their imports resolved.
    java_source_roots: list[Path] = []
    oversized_paths: set[Path] = set()
    # Reused by the main loop below so every .java file is parsed once per
    # scan, not twice - this pre-pass already has to parse it to read the
    # package declaration, and tree-sitter parsing is the expensive part of
    # this whole function.
    java_pre_parsed: dict[Path, tuple[bytes, Tree]] = {}
    pre_parser = Parser()
    pre_parser.language = JAVA_LANGUAGE
    for path in _iter_source_files(repo_path, ignored_paths):
        if path.suffix != ".java":
            continue
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            oversized_paths.add(path)
            unparseable.append({"path": _rel(repo_path, path), "reason": "file exceeds size limit"})
            continue
        pre_source = path.read_bytes()
        tree = pre_parser.parse(pre_source)
        java_pre_parsed[path] = (pre_source, tree)
        package = _extract_java_package(tree.root_node, pre_source)
        root = _java_source_root_for(path, package)
        if root is not None and root not in java_source_roots:
            java_source_roots.append(root)

    php_psr4_map = _load_php_psr4_map(repo_path)

    # Same reasoning and mechanism as Java's pre-pass above - C# has no repo-root
    # config either, and namespace-mirrors-directory is only a convention here, not
    # compiler-enforced, so it's an even softer heuristic than Java's version. A
    # (prefix -> root) map rather than a plain root list, PSR-4-style, to handle
    # <RootNamespace>'s implicit prefix (see _csharp_prefix_and_root_for).
    csharp_prefix_map: dict[str, Path] = {}
    csharp_source_paths: list[Path] = []
    # Which file declares each type name, for the type-reference edges below.
    csharp_type_owners: dict[str, set[Path]] = {}
    # Same reasoning as java_pre_parsed above.
    csharp_pre_parsed: dict[Path, tuple[bytes, Tree]] = {}
    cs_pre_parser = Parser()
    cs_pre_parser.language = CSHARP_LANGUAGE
    for path in _iter_source_files(repo_path, ignored_paths):
        if path.suffix != ".cs":
            continue
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            oversized_paths.add(path)
            unparseable.append({"path": _rel(repo_path, path), "reason": "file exceeds size limit"})
            continue
        csharp_source_paths.append(path)
        pre_source = path.read_bytes()
        tree = cs_pre_parser.parse(pre_source)
        csharp_pre_parsed[path] = (pre_source, tree)
        namespace = _extract_csharp_namespace(tree.root_node, pre_source)
        result = _csharp_prefix_and_root_for(path, namespace)
        if result is not None:
            prefix, root = result
            csharp_prefix_map.setdefault(prefix, root)
        for declared in _csharp_declared_type_names(tree.root_node, pre_source):
            csharp_type_owners.setdefault(declared, set()).add(path)
    csharp_implicit_usings = _load_csharp_implicit_usings(repo_path, csharp_source_paths)

    parser = Parser()

    for path in _iter_source_files(repo_path, ignored_paths):
        rel_path = _rel(repo_path, path)

        if unchanged_modules is not None and rel_path in unchanged_modules:
            cached_module = unchanged_modules[rel_path]
            modules.append(cached_module)
            for target in cached_module.get("imports", []):
                edges.append([rel_path, target])
                imported_by_map.setdefault(target, []).append(rel_path)
            continue

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
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            oversized_paths.add(path)
            unparseable.append({"path": rel_path, "reason": "file exceeds size limit"})
            continue
        if language_name == "java" and path in java_pre_parsed:
            # pop, not a plain lookup: java_pre_parsed/csharp_pre_parsed hold
            # the full (source, Tree) for every .java/.cs file simultaneously
            # once the pre-pass above finishes - unavoidable, since the
            # pre-pass needs every file's package/namespace before it can
            # infer a source root at all. But nothing forces this loop to
            # keep holding a file's entry once it's been consumed here -
            # tree-sitter trees run roughly 37x their source size, so on a
            # large Java/C# repo this loop's the rest of its run (every
            # other-language file remaining) previously held every already-
            # consumed tree alive for no reason. Popping lets each entry's
            # memory free as soon as this loop passes it, instead of only
            # when build_module_graph returns.
            source, tree = java_pre_parsed.pop(path)
        elif language_name == "csharp" and path in csharp_pre_parsed:
            source, tree = csharp_pre_parsed.pop(path)
        else:
            parser.language = ts_language
            source = path.read_bytes()
            tree = parser.parse(source)

        constants: list[dict] = []
        if language_name != "python":
            # Python's bindings come out of _extract_python, which already has
            # the walk; every other language gets the shared pass.
            constants = _extract_module_constants(tree.root_node, source, language_name)
        if language_name == "python":
            plain_imports, from_imports, functions, classes, constants = _extract_python(
                tree.root_node, source
            )
            resolved_imports: list[str] = []

            for dotted in plain_imports:
                target = _resolve_python_module(repo_path, dotted, path, python_source_roots)
                if target is not None:
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)

            for module_name, names in from_imports:
                targets: set[str] = set()
                if names:
                    for name in names:
                        target = _resolve_python_from_import(
                            repo_path, module_name, name, path, python_source_roots
                        )
                        if target is not None:
                            targets.add(target)
                else:
                    target = _resolve_python_module(repo_path, module_name, path, python_source_roots)
                    if target is not None:
                        targets.add(target)
                for target in sorted(targets):
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name == "go":
            raw_imports, functions, classes = _extract_go(tree.root_node, source)
            resolved_imports = []
            for spec in raw_imports:
                for target in _resolve_go_import(repo_path, go_module_prefix, spec):
                    if target == rel_path:
                        continue
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name == "rust":
            raw_imports, functions, classes = _extract_rust(tree.root_node, source)
            resolved_imports = []
            if has_rust_crate_root:
                for use_path in raw_imports:
                    target = _resolve_rust_path(repo_path, path, use_path)
                    if target is not None and target != rel_path:
                        resolved_imports.append(target)
                        edges.append([rel_path, target])
                        imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name == "java":
            raw_imports, functions, classes = _extract_java(tree.root_node, source)
            resolved_imports = []
            for dotted, is_static, is_wildcard in raw_imports:
                for target_path in _resolve_java_import(
                    java_source_roots, dotted, is_static, is_wildcard
                ):
                    target = _rel(repo_path, target_path)
                    if target is None or target == rel_path:
                        continue
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name == "ruby":
            raw_imports, functions, classes = _extract_ruby(tree.root_node, source)
            resolved_imports = []
            for kind, spec in raw_imports:
                target_path = _resolve_ruby_require(repo_path, path, kind, spec)
                if target_path is not None:
                    target = _rel(repo_path, target_path)
                    if target is not None and target != rel_path:
                        resolved_imports.append(target)
                        edges.append([rel_path, target])
                        imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name == "php":
            raw_imports, functions, classes = _extract_php(tree.root_node, source)
            resolved_imports = []
            for kind, spec in raw_imports:
                target_path = (
                    _resolve_php_use(php_psr4_map, spec)
                    if kind == "use"
                    else _resolve_php_include(path, spec)
                )
                if target_path is not None:
                    target = _rel(repo_path, target_path)
                    if target is not None and target != rel_path:
                        resolved_imports.append(target)
                        edges.append([rel_path, target])
                        imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name in ("c", "cpp"):
            raw_imports, functions, classes = _extract_c_family(tree.root_node, source)
            resolved_imports = []
            for spec in raw_imports:
                target_path = _resolve_c_include(path, spec)
                if target_path is not None:
                    target = _rel(repo_path, target_path)
                    if target is not None and target != rel_path:
                        resolved_imports.append(target)
                        edges.append([rel_path, target])
                        imported_by_map.setdefault(target, []).append(rel_path)
        elif language_name == "csharp":
            raw_imports, functions, classes = _extract_csharp(tree.root_node, source)
            raw_imports.extend(csharp_implicit_usings.get(path, []))
            resolved_imports = []
            for dotted in raw_imports:
                for target_path in _resolve_csharp_using(csharp_prefix_map, dotted):
                    target = _rel(repo_path, target_path)
                    if target is None or target == rel_path:
                        continue
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)
            # Same-namespace references need no `using`, so usings alone leave
            # the graph near-empty - see _csharp_type_reference_targets.
            own_type_names = {c["name"] for c in classes if c.get("name")}
            already = set(resolved_imports)
            for target_path in _csharp_type_reference_targets(
                source, own_type_names, csharp_type_owners
            ):
                target = _rel(repo_path, target_path)
                if target is None or target == rel_path or target in already:
                    continue
                already.add(target)
                resolved_imports.append(target)
                edges.append([rel_path, target])
                imported_by_map.setdefault(target, []).append(rel_path)
        else:
            raw_imports, functions, classes = _extract_javascript(tree.root_node, source)
            resolved_imports = []
            for spec in raw_imports:
                target = _resolve_js_import(repo_path, path, spec)
                if target is not None:
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)

        modules.append(
            {
                "path": rel_path,
                "language": language_name,
                "imports": resolved_imports,
                "imported_by": [],
                "symbols": {"functions": functions, "classes": classes, "constants": constants},
            }
        )

    for module in modules:
        module["imported_by"] = sorted(imported_by_map.get(module["path"], []))

    nodes = sorted({m["path"] for m in modules})
    dependency_graph = {"nodes": nodes, "edges": edges}

    return modules, dependency_graph, unparseable
