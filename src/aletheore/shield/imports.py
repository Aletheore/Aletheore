"""Extract the third-party package names a Python or JS/TS source file imports.

Neither language is matched with a regex over the raw text: Python goes through `ast` (and `tokenize` for a file that
does not parse, such as one in the middle of an edit), JS/TS through the tree-sitter TSX grammar, a real parser that
knows strings, template literals, comments, regular-expression literals and JSX. It is linear on real code; on hostile
text its error recovery is not, so large text is parsed in a child process with a time limit (see JS_IN_PROCESS_BYTES).
Import-shaped text inside a string, docstring or comment is never an import, and a comment marker inside a string
(`"//"`, `"/*"`, `"#"`) never hides the code after it.
"""

import ast
import io
import re
import sys
import threading
import time
import tokenize

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
_JS_ESCAPE = re.compile(r"\\(?:u\{[0-9a-fA-F]{1,6}\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])")
_JS_PARSERS = threading.local()


# ---- Python ------------------------------------------------------------------------------------


def _keep_python(names: set[str]) -> set[str]:
    return {n for n in names if n and not n.startswith("_") and n not in sys.stdlib_module_names}


def _top(dotted: str) -> str:
    """The import name a distribution is looked up by: the first part, or the first two under a namespace root
    (`google.protobuf.json_format` -> `google.protobuf`), since the root alone says nothing about the package."""
    parts = dotted.split(".")
    return ".".join(parts[:2]) if parts[0] in PY_NAMESPACE_ROOTS else parts[0]


def python_imports(source: str) -> set[str]:
    """Top-level names of absolute imports (two parts under a namespace root, see `_top`). A file that does not parse
    is read token by token instead, so the imports it does hold are still seen (strings and comments are tokens of
    their own, never names)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return _keep_python(_python_imports_from_tokens(source))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(_top(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in PY_NAMESPACE_ROOTS:  # from google import protobuf
                found.update(f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*")
            else:
                found.add(_top(node.module))
    return _keep_python(found)


_SKIPPED_TOKENS = frozenset({tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING})


def _python_statements(source: str):
    """The logical lines of `source` as lists of (type, text) tokens, as far as the tokenizer gets."""
    statement: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in _SKIPPED_TOKENS:
                continue
            ends = token.type in (tokenize.NEWLINE, tokenize.ENDMARKER)
            if ends or (token.type == tokenize.OP and token.string == ";"):
                if statement:
                    yield statement
                statement = []
                continue
            statement.append((token.type, token.string))
    except (tokenize.TokenError, SyntaxError, ValueError):
        pass  # an unterminated string or bracket: everything before it was already yielded or is below
    if statement:
        yield statement


def _python_imports_from_tokens(source: str) -> set[str]:
    found: set[str] = set()
    for statement in _python_statements(source):
        kind, word = statement[0]
        if kind != tokenize.NAME:
            continue
        if word == "from" and len(statement) > 1 and statement[1][0] == tokenize.NAME:
            found.add(statement[1][1])  # "from . import x" starts with an OP: relative, skipped
        elif word == "import":
            expect_name = True
            for kind, text in statement[1:]:
                if expect_name and kind == tokenize.NAME:
                    found.add(text)
                    expect_name = False
                elif kind == tokenize.OP and text == ",":
                    expect_name = True
                elif kind == tokenize.OP and text not in (".", "(", ")"):
                    break
    return found


# ---- JavaScript / TypeScript ------------------------------------------------------------------


def _package_of(specifier: str) -> str | None:
    if not specifier or specifier[0] in "./~#!" or ":" in specifier or specifier.startswith("@/"):
        return None
    parts = specifier.split("/")
    name = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
    if not _NPM_NAME.match(name) or name in NODE_BUILTINS:
        return None
    return name


def _js_parser():
    """A new TSX parser. A parser is never stopped mid-parse (stopping one from tree-sitter's progress callback crashed
    the process on Python 3.11-3.13), so it is never left in a half-parsed state either."""
    import tree_sitter_typescript
    from tree_sitter import Language, Parser

    if not hasattr(_JS_PARSERS, "language"):
        _JS_PARSERS.language = Language(tree_sitter_typescript.language_tsx())
    return Parser(_JS_PARSERS.language)


def _js_specifier(text: str) -> str | None:
    def decode(match):
        escape = match.group()[1:]
        if escape.startswith("u{"):
            return chr(int(escape[2:-1], 16))
        if escape[0] in "ux":
            return chr(int(escape[1:], 16))
        if escape in ("\n", "\r", "\r\n", "\u2028", "\u2029"):
            return ""
        if escape in "0123456789bfnrtv":
            raise ValueError("control/octal escape cannot be a package name")
        return escape

    try:
        return _JS_ESCAPE.sub(decode, text)
    except ValueError:
        return None


class ParseTimeout(Exception):
    """The source could not be parsed within the time given; nothing is known about its imports."""


JS_PARSE_TIMEOUT_S = 1.0
# tree-sitter's error recovery goes quadratic on some hostile text (`x=/[` or `a<b,` repeated: about 0.2 s for 8 KB,
# 3 s for 32 KB, 6 s for 48 KB), and a parse cannot be stopped safely once started. Text up to JS_IN_PROCESS_BYTES is
# parsed here, where the worst case known stays near 0.2 s; anything larger is parsed in a child process that is
# killed at the time limit. Text over JS_MAX_PARSE_BYTES is not parsed at all.
JS_IN_PROCESS_BYTES = 8 * 1024
JS_MAX_PARSE_BYTES = 512 * 1024


def _js_imports_from_raw(raw: bytes, deadline: float | None = None) -> set[str]:
    """Parse `raw` here, to the end, and read its imports. The tree walk stops with ParseTimeout past `deadline`."""
    tree = _js_parser().parse(raw)
    found: set[str] = set()
    stack = [tree.root_node]
    visited = 0
    while stack:
        node = stack.pop()
        visited += 1
        if deadline is not None and not visited % 4096 and time.monotonic() > deadline:
            raise ParseTimeout("not walked in time")
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
            specifier = _js_specifier(raw[literal.start_byte + 1:literal.end_byte - 1].decode("utf-8", "replace"))
            package = _package_of(specifier) if specifier is not None else None
            if package:
                found.add(package)
        stack.extend(node.named_children)
    return found


# The child reads {"path": [...], "source": "..."} on stdin and prints the sorted package names as JSON. It runs
# isolated (-I: no PYTHON* variables, no user site, no current directory or script folder on sys.path) and is given
# the folder this package came from plus the absolute entries of this process's sys.path, never the current directory
# (a hook's current directory is the repository), so a repository's own files can never be imported by it.
_CHILD = (
    "import json, sys\n"
    "request = json.loads(sys.stdin.buffer.read())\n"
    "sys.path[:] = request['path']\n"
    "from aletheore.shield.imports import _js_imports_from_raw\n"
    "sys.stdout.write(json.dumps(sorted(_js_imports_from_raw(request['source'].encode('utf-8', 'replace')))))\n"
)


def _js_imports_in_child(source: str, timeout: float) -> set[str]:
    import json
    import os
    import subprocess  # nosec B404 - runs this Python only, with an argv list and never a shell

    # The folder this aletheore package was imported from comes first: it is the code already running here.
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = [package_root, *(entry for entry in sys.path if entry and os.path.isabs(entry) and entry != package_root)]
    request = json.dumps({"path": path, "source": source}).encode("utf-8")
    try:
        # The executable is this interpreter and the argv is fixed; the source travels on stdin, never in argv.
        done = subprocess.run(  # noqa: S603  # nosec B603
            [sys.executable, "-I", "-c", _CHILD], input=request, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise ParseTimeout("not parsed in time") from None
    except OSError:
        raise ParseTimeout("could not start the parser") from None
    try:
        names = json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        names = None
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ParseTimeout("the parser failed")
    return set(names)


def js_imports(source: str, timeout: float = JS_PARSE_TIMEOUT_S) -> set[str]:
    """Bare package names from actual syntax, excluding comments and string contents. Raises ParseTimeout when the
    source cannot be parsed, and its tree walked, within `timeout` seconds."""
    if "import" not in source and "require" not in source and "export" not in source:  # every form read below holds one
        return set()
    raw = source.encode("utf-8", "replace")
    if len(raw) > JS_MAX_PARSE_BYTES:
        raise ParseTimeout("too large to parse in time")
    if len(raw) <= JS_IN_PROCESS_BYTES:
        return _js_imports_from_raw(raw, time.monotonic() + timeout)
    return _js_imports_in_child(raw.decode("utf-8"), max(timeout, 0.001))
