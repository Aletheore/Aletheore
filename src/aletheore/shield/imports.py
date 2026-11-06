"""Extract the third-party package names a Python or JS/TS source file imports.

Neither language is matched with a regex over the raw text: Python goes through `ast` (and `tokenize` for a file that
does not parse, such as one in the middle of an edit), JS/TS through the tree-sitter TSX grammar, a real parser that
knows strings, template literals, comments, regular-expression literals and JSX and runs in time linear in the file.
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
    """Reuse a parser within a thread; parsers must not be shared across concurrent checks."""
    import tree_sitter_typescript
    from tree_sitter import Language, Parser

    if not hasattr(_JS_PARSERS, "parser"):
        _JS_PARSERS.parser = Parser(Language(tree_sitter_typescript.language_tsx()))
    return _JS_PARSERS.parser


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
# tree-sitter calls the progress hook only while it reads; the rest of a parse is linear but cannot be stopped (about
# 0.75 s for 1 MB of "{"). Larger sources are not parsed at all, so the overshoot past the time limit stays small.
JS_MAX_PARSE_BYTES = 512 * 1024
_READ_CHUNK = 64 * 1024


def _parse_js(raw: bytes, deadline: float):
    """The tree for `raw`, or ParseTimeout once time.monotonic() passes `deadline`. tree-sitter is linear on real code, but its error
    recovery can go quadratic on hostile text (`x=/[` repeated: about 6 s for 48 KB), so the parse is stopped from
    its progress callback, which tree-sitter calls only when reading through a callback."""
    parser = _js_parser()
    try:
        return parser.parse(lambda offset, _point: raw[offset:offset + _READ_CHUNK],
                            progress_callback=lambda *_: time.monotonic() > deadline)
    except ValueError:  # tree-sitter's "Parsing failed": the callback stopped it
        parser.reset()
        raise ParseTimeout("not parsed in time") from None


def js_imports(source: str, timeout: float = JS_PARSE_TIMEOUT_S) -> set[str]:
    """Bare package names from actual syntax, excluding comments and string contents. Raises ParseTimeout when the
    source cannot be parsed, and its tree walked, within `timeout` seconds."""
    if "import" not in source and "require" not in source and "export" not in source:  # every form read below holds one
        return set()
    raw = source.encode("utf-8", "replace")
    if len(raw) > JS_MAX_PARSE_BYTES:
        raise ParseTimeout("too large to parse in time")
    deadline = time.monotonic() + timeout
    tree = _parse_js(raw, deadline)
    found: set[str] = set()
    stack = [tree.root_node]
    visited = 0
    while stack:
        node = stack.pop()
        visited += 1
        if not visited % 4096 and time.monotonic() > deadline:  # a huge tree's walk is bounded too
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
