"""Extract the third-party package names a Python or JS/TS source file imports.

Neither language is matched with a regex over the raw text: Python goes through `ast` (and `tokenize` for a file that
does not parse, such as one in the middle of an edit), JS/TS through a small tokenizer that knows strings, template
literals, comments and regular-expression literals. Import-shaped text inside a string, docstring or comment is never
an import, and a comment marker inside a string (`"//"`, `"/*"`, `"#"`) never hides the code after it.
"""

import ast
import io
import re
import sys
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


# ---- Python ------------------------------------------------------------------------------------


def _keep_python(names: set[str]) -> set[str]:
    return {n for n in names if n and not n.startswith("_") and n not in sys.stdlib_module_names}


def python_imports(source: str) -> set[str]:
    """Top-level names of absolute imports. A file that does not parse is read token by token instead, so the imports
    it does hold are still seen (strings and comments are tokens of their own, never names)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return _keep_python(_python_imports_from_tokens(source))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return _keep_python(found)


_SKIPPED_TOKENS = frozenset({tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING})


def _python_statements(source: str):
    """The logical lines of `source` as lists of (type, text) tokens, as far as the tokenizer gets."""
    statement: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in _SKIPPED_TOKENS:
                continue
            if token.type in (tokenize.NEWLINE, tokenize.ENDMARKER) or (token.type == tokenize.OP and token.string == ";"):
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

_JS_TOKEN = re.compile(
    r"""
    (?P<space>\s+)
    | (?P<comment>//[^\n]*|/\*.*?(?:\*/|\Z))
    | (?P<string>'(?:[^'\\\n]|\\.)*(?:'|(?=\n)|\Z)|"(?:[^"\\\n]|\\.)*(?:"|(?=\n)|\Z))
    | (?P<name>[A-Za-z_$][\w$]*)
    | (?P<number>\d[\w.]*)
    | (?P<punct>.)
    """,
    re.S | re.X,
)
_JS_REGEX_BODY = re.compile(r"(?:[^/\\\[\n]|\\.|\[(?:[^\]\\\n]|\\.)*\])*/[A-Za-z]*")
# After these words a "/" starts a regular expression, not a division.
_JS_REGEX_AFTER_WORDS = frozenset(
    "return typeof instanceof in of new delete void throw case do else yield await".split()
)
_STRING, _NAME, _PUNCT = "s", "n", "p"


def _template(text: str, position: int) -> tuple[int, bool, str]:
    """Scan a template literal from just after its opening backtick: (end position, ends at a `${`, its raw text)."""
    start = position
    while position < len(text):
        char = text[position]
        if char == "\\":
            position += 2
        elif char == "`":
            return position + 1, False, text[start:position]
        elif char == "$" and text.startswith("${", position):
            return position + 2, True, text[start:position]
        else:
            position += 1
    return position, False, text[start:]


def _js_tokens(text: str) -> list[tuple[str, str]]:
    """(kind, text) for every string, name and punctuation token; comments, regex literals and template text are
    dropped (a template with no substitution is a string token, like a quoted one). Never raises."""
    tokens: list[tuple[str, str]] = []
    braces: list[int] = []  # for each open `${`, the depth of plain braces inside it
    position = 0
    while position < len(text):
        match = _JS_TOKEN.match(text, position)
        if match is None:  # cannot happen ("." matches any character), but never loop forever
            break
        kind = match.lastgroup
        value = match.group()
        position = match.end()
        if kind in ("space", "comment", "number"):
            if kind == "number":
                tokens.append((_PUNCT, "0"))  # a value: a following "/" divides
            continue
        if kind == "string":
            tokens.append((_STRING, value[1:-1] if len(value) > 1 and value[-1] == value[0] else value[1:]))
            continue
        if kind == "name":
            tokens.append((_NAME, value))
            continue
        if value == "`" or (value == "}" and braces and braces[-1] == 0):
            if value == "}":
                braces.pop()  # end of a `${...}`: back inside the template
            position, opened, raw = _template(text, position)
            if opened:
                braces.append(0)
                tokens.append((_PUNCT, "`"))  # a template is a value, but its text is never a specifier
            elif value == "`":
                tokens.append((_STRING, raw))
            else:
                tokens.append((_PUNCT, "`"))
            continue
        if value == "{" and braces:
            braces[-1] += 1
        elif value == "}" and braces:
            braces[-1] -= 1
        if value == "/":
            previous = tokens[-1] if tokens else None
            regex_allowed = previous is None or (
                previous[0] == _PUNCT and previous[1] not in (")", "]", "}", "0", "`")
            ) or (previous[0] == _NAME and previous[1] in _JS_REGEX_AFTER_WORDS)
            if regex_allowed:
                body = _JS_REGEX_BODY.match(text, position)
                if body is not None:
                    position = body.end()
                    tokens.append((_PUNCT, "0"))  # a value
                    continue
        tokens.append((_PUNCT, value))
    return tokens


def _package_of(specifier: str) -> str | None:
    if not specifier or specifier[0] in "./~#!" or ":" in specifier or specifier.startswith("@/"):
        return None
    parts = specifier.split("/")
    name = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
    if not _NPM_NAME.match(name) or name in NODE_BUILTINS:
        return None
    return name


_STATEMENT_LOOKAHEAD = 400  # tokens an import/export clause may span before "from"


def _from_specifier(tokens: list[tuple[str, str]], start: int) -> str | None:
    """The string after the first `from` at or after `start` within the same import/export clause."""
    for index in range(start, min(len(tokens) - 1, start + _STATEMENT_LOOKAHEAD)):
        kind, value = tokens[index]
        if kind == _PUNCT and value in (";", "=", "(", ")"):
            return None
        if kind == _NAME and value in ("import", "export") and index > start:
            return None
        if kind == _NAME and value == "from" and tokens[index + 1][0] == _STRING:
            return tokens[index + 1][1]
    return None


def _js_specifiers(tokens: list[tuple[str, str]]):
    for index, (kind, value) in enumerate(tokens):
        if kind != _NAME:
            continue
        following = tokens[index + 1 : index + 4]
        call = (
            len(following) >= 3 and following[0] == (_PUNCT, "(") and following[1][0] == _STRING
            and following[2][0] == _PUNCT and following[2][1] in (")", ",")
        )
        if value == "require" and call:
            yield following[1][1]
        elif value == "import":
            if call:
                yield following[1][1]  # import("x")
            elif following and following[0][0] == _STRING:
                yield following[0][1]  # import "x"
            elif following and following[0] not in ((_PUNCT, "("), (_PUNCT, ".")):  # not import(expr) or import.meta
                specifier = _from_specifier(tokens, index + 1)
                if specifier is not None:
                    yield specifier
        elif value == "export" and following:
            first = following[0]
            if first in ((_PUNCT, "*"), (_PUNCT, "{")) or first == (_NAME, "type"):
                specifier = _from_specifier(tokens, index + 1)
                if specifier is not None:
                    yield specifier


def js_imports(source: str) -> set[str]:
    """Bare package names from import/export-from/require/dynamic import."""
    if "import" not in source and "require" not in source and "export" not in source:
        return set()
    found: set[str] = set()
    for specifier in _js_specifiers(_js_tokens(source)):
        package = _package_of(specifier)
        if package:
            found.add(package)
    return found
