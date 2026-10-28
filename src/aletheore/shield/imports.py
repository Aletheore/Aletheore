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
_JS_PATTERNS = (
    re.compile(r"""\bimport\s+(?:type\s+)?(?:[^'"();]*?\sfrom\s*)?['"]([^'"\n]+)['"]"""),
    re.compile(r"""\bexport\s+(?:type\s+)?[^'"();]*?\sfrom\s*['"]([^'"\n]+)['"]"""),
    re.compile(r"""\brequire\s*\(\s*['"]([^'"\n]+)['"]\s*\)"""),
    re.compile(r"""\bimport\s*\(\s*['"]([^'"\n]+)['"]\s*\)"""),
)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?m)(^|\s)//[^\n]*")


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
    """Bare package names from import/export-from/require/dynamic import."""
    text = _LINE_COMMENT.sub(r"\1", _BLOCK_COMMENT.sub(" ", source))
    found: set[str] = set()
    for pattern in _JS_PATTERNS:
        for match in pattern.finditer(text):
            package = _package_of(match.group(1))
            if package:
                found.add(package)
    return found
