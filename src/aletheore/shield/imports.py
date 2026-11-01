"""Extract the third-party package names a Python or JS/TS source file imports."""

import ast
import re
import sys

PY_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

NODE_BUILTINS = frozenset(
    """assert async_hooks buffer child_process cluster console constants crypto dgram diagnostics_channel dns domain
    events fs http http2 https inspector module net os path perf_hooks process punycode querystring readline repl
    stream string_decoder sys timers tls trace_events tty url util v8 vm wasi worker_threads zlib""".split()
)
# Namespace roots shared by many distributions; the import name alone says nothing about the package.
PY_NAMESPACE_ROOTS = frozenset({"google", "azure", "zope", "jaraco", "ruamel", "sphinxcontrib", "backports"})

_NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")


def python_imports(source: str) -> set[str]:
    """Top-level names of absolute imports; empty when the file does not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return {n for n in found if n and not n.startswith("_") and n not in sys.stdlib_module_names}


def _package_of(specifier: str) -> str | None:
    if not specifier or specifier[0] in "./~#!" or ":" in specifier or specifier.startswith("@/"):
        return None
    parts = specifier.split("/")
    name = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
    if not _NPM_NAME.match(name) or name in NODE_BUILTINS:
        return None
    return name


def js_imports(source: str) -> set[str]:
    """Bare package names from actual syntax, excluding comments and string contents."""
    import tree_sitter_typescript
    from tree_sitter import Language, Parser

    raw = source.encode("utf-8", "replace")
    parser = Parser(Language(tree_sitter_typescript.language_tsx()))
    tree = parser.parse(raw)
    found: set[str] = set()
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        literal = None
        if node.type in ("import_statement", "export_statement"):
            literal = node.child_by_field_name("source")
        elif node.type == "import_require_clause":
            literal = next((n for n in node.named_children if n.type == "string"), None)
        elif node.type == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if fn is not None and (fn.type == "import" or
                    fn.type == "identifier" and raw[fn.start_byte:fn.end_byte] == b"require"):
                literal = args.named_children[0] if args is not None and args.named_children else None
        if literal is not None and (literal.type == "string" or literal.type == "template_string"
                and not any(c.type == "template_substitution" for c in literal.named_children)):
            package = _package_of(raw[literal.start_byte + 1:literal.end_byte - 1].decode("utf-8", "replace"))
            if package:
                found.add(package)
        stack.extend(node.named_children)
    return found
