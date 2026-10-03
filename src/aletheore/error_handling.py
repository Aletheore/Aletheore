"""Where a codebase raises, defines and catches errors.

The scan records symbols and imports but nothing about failure: which error types
exist, where they are raised, and what catches them. A question such as "how does
this codebase handle errors" therefore had no evidence behind it, and AIRview
answered it from test files. This module adds that evidence, deterministically, as
`repository.error_handling`: three lists (error types, raise sites, catch
handlers) and a per-type summary. Covered: Python, C/C++, JavaScript/TypeScript, Java, C#,
PHP, Kotlin, Ruby and Swift (exceptions), plus Go and Rust, where errors are values: there
a type counts when it has an `Error()` method (Go) or derives/implements `Error` (Rust),
`errors.New`/`fmt.Errorf` and `panic` are the raise sites, and `recover`/`errors.Is` the handlers.

Everything is read from the syntax tree, never inferred, so each entry is a real
`file:line`. Lists are capped to keep air.json and the TOON evidence bounded; the
summary counts every site, and `truncated` says when a list was cut.
"""
import re
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.graph import LANGUAGE_BY_EXTENSION, _iter_source_files, _rel

MAX_ERROR_TYPES = 200
MAX_RAISE_SITES = 500
MAX_HANDLERS = 300
MAX_SUMMARY = 60

_PY_ERROR_SEEDS = {"BaseException", "KeyboardInterrupt", "StopIteration", "SystemExit"}
_CPP_ERROR_SEEDS = {
    "exception", "runtime_error", "logic_error", "system_error", "out_of_range", "invalid_argument",
    "domain_error", "length_error", "range_error", "overflow_error", "underflow_error",
    "bad_alloc", "bad_cast", "bad_optional_access", "bad_variant_access",
}
_ERRORISH_SUFFIX = re.compile(r"(Error|Exception|error|exception)$")
_FUNCTION_NODES = {
    "function_definition", "function_declaration", "method_declaration", "constructor_declaration",
    "method_definition", "arrow_function", "function_expression", "function_item", "method",
    "singleton_method",
}
# Test-framework assertion macros (EXPECT_THROW, ASSERT_NO_THROW, ...) mention THROW but
# check that something throws; they are not throw sites.
_TEST_MACRO = re.compile(r"^(EXPECT|ASSERT|CHECK|REQUIRE|TEST)_|NO_THROW|ANY_THROW")
# A class whose declaration the grammar cannot parse (an attribute macro with arguments in
# front of the name, e.g. `class FMT_API(x) name : public base`) is recovered from its text.
_CPP_CLASS_TEXT = re.compile(
    r"^\s*(?:class|struct)\s+(?:[A-Za-z_][\w]*\([^)]*\)\s+|[A-Z_][A-Z0-9_]*\s+)*"
    r"([A-Za-z_]\w*)\s*(?:final\s*)?:\s*(?:public|protected|private)?\s*([A-Za-z_][\w:]*)"
)
_NON_TYPES = {"(re-raise)", "(any)", "(value)", "(expression)", "(unknown)", "..."}


def _last(name: str) -> str:
    if name in ("errors.New", "fmt.Errorf"):  # Go constructors read better whole than as "New"
        return name
    return re.split(r"::|\.", name)[-1]


def _is_errorish(base: str, seeds: set[str]) -> bool:
    short = _last(base)
    return short in seeds or bool(_ERRORISH_SUFFIX.search(short))


def _enclosing_function(node: Node) -> str:
    current = node.parent
    while current is not None:
        if current.type in _FUNCTION_NODES:
            if current.type in ("arrow_function", "function_expression") and current.child_by_field_name("name") is None:
                holder = current.parent
                label = holder.child_by_field_name("name") if holder is not None else None
                return label.text.decode(errors="replace") if label is not None else ""
            declarator = current.child_by_field_name("declarator")
            while declarator is not None and declarator.child_by_field_name("declarator") is not None:
                declarator = declarator.child_by_field_name("declarator")
            if declarator is not None:
                return declarator.text.decode(errors="replace")
            name = current.child_by_field_name("name")
            return name.text.decode(errors="replace") if name is not None else ""
        current = current.parent
    return ""


def _walk(root: Node):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _py_name(node: Node | None) -> str:
    if node is None:
        return "(expression)"
    if node.type == "identifier":
        return node.text.decode()
    if node.type == "attribute":
        return _last(node.text.decode())
    if node.type == "call":
        return _py_name(node.child_by_field_name("function"))
    return "(expression)"


def _python(root: Node, rel: str, classes: list, raises: list, handlers: list) -> None:
    for node in _walk(root):
        if node.type == "class_definition":
            name = node.child_by_field_name("name")
            bases_node = node.child_by_field_name("superclasses")
            bases = []
            if bases_node is not None:
                for child in bases_node.named_children:
                    if child.type in ("identifier", "attribute"):
                        bases.append(_last(child.text.decode()))
            classes.append({"name": name.text.decode(), "file": rel, "line": node.start_point[0] + 1,
                            "bases": bases, "seeds": _PY_ERROR_SEEDS})
        elif node.type == "raise_statement":
            raised = next((c for c in node.named_children), None)
            raises.append({"file": rel, "line": node.start_point[0] + 1,
                           "error_type": "(re-raise)" if raised is None else _py_name(raised),
                           "function": _enclosing_function(node)})
        elif node.type == "except_clause":
            caught = [c for c in node.named_children if c.type != "block"]
            if not caught:
                names = ["(any)"]
            else:
                first = caught[0]
                if first.type == "as_pattern":
                    first = first.named_children[0] if first.named_children else first
                if first.type == "tuple":
                    names = [_py_name(c) for c in first.named_children]
                else:
                    names = [_py_name(first)]
            handlers.append({"file": rel, "line": node.start_point[0] + 1, "catches": names,
                             "function": _enclosing_function(node)})


def _cpp_type_name(node: Node | None) -> str:
    if node is None:
        return "(unknown)"
    if node.type in ("identifier", "type_identifier", "qualified_identifier", "namespace_identifier"):
        return node.text.decode()
    if node.type == "call_expression":
        return _cpp_type_name(node.child_by_field_name("function"))
    if node.type == "new_expression":
        return _cpp_type_name(node.child_by_field_name("type"))
    if node.type in ("string_literal", "number_literal", "true", "false"):
        return "(value)"
    return "(expression)"


def _cpp(root: Node, rel: str, classes: list, raises: list, handlers: list, text: str = "") -> None:
    if root.has_error and text:
        parsed = {(c["file"], c["name"]) for c in classes}
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            match = _CPP_CLASS_TEXT.match(line)
            if match and (rel, match.group(1)) not in parsed:
                classes.append({"name": match.group(1), "file": rel, "line": number,
                                "bases": [match.group(2)], "seeds": _CPP_ERROR_SEEDS})
    for node in _walk(root):
        if node.type in ("class_specifier", "struct_specifier"):
            name = node.child_by_field_name("name")
            if name is None:
                continue
            bases = []
            for child in node.children:
                if child.type == "base_class_clause":
                    for part in child.named_children:
                        if part.type in ("type_identifier", "qualified_identifier", "template_type"):
                            bases.append(part.text.decode())
            classes.append({"name": name.text.decode(), "file": rel, "line": node.start_point[0] + 1,
                            "bases": bases, "seeds": _CPP_ERROR_SEEDS})
        elif node.type == "throw_statement":
            raised = next((c for c in node.named_children), None)
            raises.append({"file": rel, "line": node.start_point[0] + 1,
                           "error_type": "(re-raise)" if raised is None else _cpp_type_name(raised),
                           "function": _enclosing_function(node)})
        elif node.type == "call_expression":
            callee = node.child_by_field_name("function")
            if (callee is not None and callee.type == "identifier" and "THROW" in callee.text.decode()
                    and not _TEST_MACRO.search(callee.text.decode())):
                # A throw hidden behind a macro (FMT_THROW(format_error(...)), BOOST_THROW_EXCEPTION).
                args = node.child_by_field_name("arguments")
                first = args.named_children[0] if args is not None and args.named_children else None
                raises.append({"file": rel, "line": node.start_point[0] + 1,
                               "error_type": _cpp_type_name(first), "function": _enclosing_function(node)})
        elif node.type == "catch_clause":
            params = node.child_by_field_name("parameters")
            if params is None:
                continue
            declaration = next((c for c in params.named_children if c.type == "parameter_declaration"), None)
            if declaration is None:
                names = ["..."]
            else:
                kind = declaration.child_by_field_name("type")
                names = [_cpp_type_name(kind)]
            handlers.append({"file": rel, "line": node.start_point[0] + 1, "catches": names,
                             "function": _enclosing_function(node)})


def _text(node: Node | None) -> str:
    return node.text.decode(errors="replace") if node is not None else ""


def _type_of(node: Node | None) -> str:
    """The name of the thing being thrown or caught, from whatever expression names it."""
    if node is None:
        return "(unknown)"
    kind = node.type
    if kind in ("identifier", "type_identifier", "constant", "name", "simple_identifier", "qualified_name",
                "scoped_type_identifier", "scoped_identifier", "qualified_identifier", "namespace_identifier",
                "user_type", "named_type", "predefined_type"):
        return _text(node)
    if kind in ("new_expression", "object_creation_expression", "constructor_invocation"):
        target = (node.child_by_field_name("constructor") or node.child_by_field_name("type")
                  or next((c for c in node.named_children if c.type != "arguments"), None))
        return _type_of(target)
    if kind in ("call_expression", "call"):
        target = node.child_by_field_name("function") or node.child_by_field_name("receiver")
        if kind == "call" and node.child_by_field_name("method") is not None and _text(node.child_by_field_name("method")) != "new":
            target = node.child_by_field_name("method")
        return _type_of(target or (node.named_children[0] if node.named_children else None))
    if kind == "navigation_expression":  # Swift: AppErr.bad
        return _type_of(node.child_by_field_name("target"))
    if kind in ("member_expression", "attribute", "selector_expression"):
        return _text(node)
    if kind in ("string", "string_literal", "interpreted_string_literal", "number_literal", "true", "false"):
        return "(value)"
    return "(expression)"


def _add_class(classes: list, rel: str, node: Node, name: str, bases: list[str], seeds: set[str]) -> None:
    if name:
        classes.append({"name": name, "file": rel, "line": node.start_point[0] + 1, "bases": bases, "seeds": seeds})


def _add_raise(raises: list, rel: str, node: Node, error_type: str) -> None:
    raises.append({"file": rel, "line": node.start_point[0] + 1, "error_type": error_type,
                   "function": _enclosing_function(node)})


def _add_handler(handlers: list, rel: str, node: Node, catches: list[str]) -> None:
    handlers.append({"file": rel, "line": node.start_point[0] + 1, "catches": catches or ["(any)"],
                     "function": _enclosing_function(node)})


def _inside(node: Node, kinds: set[str]) -> bool:
    current = node.parent
    while current is not None:
        if current.type in kinds:
            return True
        current = current.parent
    return False


_THROWABLE_SEEDS = {"Error", "Exception", "Throwable", "RuntimeException", "StandardError", "RuntimeError",
                    "SystemException", "ApplicationException", "LocalizedError", "CustomNSError"}


def _js(root: Node, rel: str, classes: list, raises: list, handlers: list) -> None:
    for node in _walk(root):
        if node.type == "class_declaration":
            heritage = next((c for c in node.children if c.type == "class_heritage"), None)
            bases = []
            if heritage is not None:
                clause = next((c for c in heritage.named_children if c.type == "extends_clause"), heritage)
                value = clause.child_by_field_name("value") or (clause.named_children[0] if clause.named_children else None)
                if value is not None:
                    bases.append(_last(_text(value)))
            _add_class(classes, rel, node, _text(node.child_by_field_name("name")), bases, _THROWABLE_SEEDS)
        elif node.type == "throw_statement":
            raised = next(iter(node.named_children), None)
            if raised is not None and raised.type == "identifier" and _inside(node, {"catch_clause"}):
                kind = "(re-raise)"
            elif raised is not None and raised.type == "identifier":
                kind = "(value)"
            else:
                kind = _type_of(raised)
            _add_raise(raises, rel, node, kind)
        elif node.type == "catch_clause":
            annotation = node.child_by_field_name("type")
            names = [_text(annotation.named_children[0])] if annotation is not None and annotation.named_children else []
            _add_handler(handlers, rel, node, names if names and names[0] not in ("unknown", "any") else [])


def _java_like(root: Node, rel: str, classes: list, raises: list, handlers: list, language: str) -> None:
    for node in _walk(root):
        if node.type == "class_declaration":
            bases: list[str] = []
            if language == "java":
                superclass = node.child_by_field_name("superclass")
                if superclass is not None and superclass.named_children:
                    bases.append(_text(superclass.named_children[0]))
            elif language == "csharp":
                base_list = next((c for c in node.children if c.type == "base_list"), None)
                bases = [_text(c) for c in (base_list.named_children if base_list is not None else [])]
            elif language == "php":
                clause = next((c for c in node.children if c.type == "base_clause"), None)
                bases = [_last(_text(c)) for c in (clause.named_children if clause is not None else [])]
            name_node = node.child_by_field_name("name")
            _add_class(classes, rel, node, _text(name_node), [_last(b) for b in bases], _THROWABLE_SEEDS)
        elif node.type in ("throw_statement", "throw_expression"):
            raised = next((c for c in node.named_children), None)
            if raised is None:
                _add_raise(raises, rel, node, "(re-raise)")
            elif raised.type in ("identifier", "variable_name"):
                _add_raise(raises, rel, node, "(re-raise)" if _inside(node, {"catch_clause"}) else "(value)")
            else:
                _add_raise(raises, rel, node, _type_of(raised))
        elif node.type == "catch_clause":
            if language == "java":
                formal = next((c for c in node.named_children if c.type == "catch_formal_parameter"), None)
                catch_type = next((c for c in (formal.named_children if formal is not None else []) if c.type == "catch_type"), None)
                names = [_text(c) for c in (catch_type.named_children if catch_type is not None else [])]
            elif language == "csharp":
                declaration = next((c for c in node.named_children if c.type == "catch_declaration"), None)
                names = [_text(declaration.child_by_field_name("type"))] if declaration is not None else []
            else:  # php
                type_list = node.child_by_field_name("type")
                names = [_last(_text(c)) for c in (type_list.named_children if type_list is not None else [])]
            _add_handler(handlers, rel, node, names)


def _kotlin(root: Node, rel: str, classes: list, raises: list, handlers: list) -> None:
    for node in _walk(root):
        if node.type == "class_declaration":
            bases = []
            specs = next((c for c in node.children if c.type == "delegation_specifiers"), None)
            for spec in (specs.named_children if specs is not None else []):
                inner = spec.named_children[0] if spec.named_children else None
                if inner is not None:
                    bases.append(_last(_type_of(inner)))
            _add_class(classes, rel, node, _text(node.child_by_field_name("name")), bases, _THROWABLE_SEEDS)
        elif node.type == "throw_expression":
            _add_raise(raises, rel, node, _type_of(next((c for c in node.named_children), None)))
        elif node.type == "infix_expression":  # `throw` written inline inside an expression
            kids = node.children
            for index, child in enumerate(kids):
                if child.type == "identifier" and _text(child) == "throw" and index + 1 < len(kids):
                    _add_raise(raises, rel, node, _type_of(kids[index + 1]))
                    break
        elif node.type == "catch_block":
            user_type = next((c for c in node.named_children if c.type == "user_type"), None)
            _add_handler(handlers, rel, node, [_text(user_type)] if user_type is not None else [])


def _ruby(root: Node, rel: str, classes: list, raises: list, handlers: list) -> None:
    for node in _walk(root):
        if node.type == "class" and node.is_named:
            superclass = node.child_by_field_name("superclass")
            bases = [_last(_text(c)) for c in (superclass.named_children if superclass is not None else [])]
            _add_class(classes, rel, node, _text(node.child_by_field_name("name")), bases, _THROWABLE_SEEDS)
        elif node.type == "call" and _text(node.child_by_field_name("method")) in ("raise", "fail") \
                and node.child_by_field_name("receiver") is None:
            args = node.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            _add_raise(raises, rel, node, "(re-raise)" if first is None else _type_of(first))
        elif node.type == "identifier" and _text(node) in ("raise", "fail") and node.parent is not None \
                and node.parent.type in ("then", "body_statement", "begin", "else"):
            _add_raise(raises, rel, node, "(re-raise)")
        elif node.type == "rescue" and node.is_named:
            exceptions = node.child_by_field_name("exceptions")
            _add_handler(handlers, rel, node, [_last(_text(c)) for c in (exceptions.named_children if exceptions is not None else [])])


def _swift(root: Node, rel: str, classes: list, raises: list, handlers: list) -> None:
    for node in _walk(root):
        if node.type == "class_declaration":
            bases = [_last(_text(c.child_by_field_name("inherits_from") or c))
                     for c in node.children if c.type == "inheritance_specifier"]
            _add_class(classes, rel, node, _text(node.child_by_field_name("name")), bases, _THROWABLE_SEEDS)
        elif node.type == "control_transfer_statement" and any(c.type == "throw_keyword" for c in node.children):
            raised = next((c for c in node.named_children if c.type != "throw_keyword"), None)
            _add_raise(raises, rel, node, _type_of(raised) if raised is not None else "(re-raise)")
        elif node.type == "catch_block":
            pattern = next((c for c in node.named_children if c.type == "pattern"), None)
            user_type = next((c for c in (pattern.named_children if pattern is not None else []) if c.type == "user_type"), None)
            _add_handler(handlers, rel, node, [_text(user_type)] if user_type is not None else [])


_GO_CREATORS = {"errors.New", "fmt.Errorf"}
_GO_SEEDS = {"error", "errors.New", "fmt.Errorf"}


def _go(root: Node, rel: str, classes: list, raises: list, handlers: list) -> None:
    for node in _walk(root):
        if node.type == "method_declaration" and _text(node.child_by_field_name("name")) == "Error":
            receiver = node.child_by_field_name("receiver")
            declaration = next((c for c in (receiver.named_children if receiver is not None else [])), None)
            kind = declaration.child_by_field_name("type") if declaration is not None else None
            if kind is not None and kind.type == "pointer_type" and kind.named_children:
                kind = kind.named_children[0]
            _add_class(classes, rel, node, _text(kind), ["error"], _GO_SEEDS)
        elif node.type == "var_spec" or node.type == "const_spec":
            value = node.child_by_field_name("value")
            call = value.named_children[0] if value is not None and value.named_children else None
            if call is not None and call.type == "call_expression" and _text(call.child_by_field_name("function")) in _GO_CREATORS:
                _add_class(classes, rel, node, _text(node.child_by_field_name("name")),
                           [_text(call.child_by_field_name("function"))], _GO_SEEDS)
        elif node.type == "call_expression":
            callee = _text(node.child_by_field_name("function"))
            args = node.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            if callee == "panic":
                _add_raise(raises, rel, node, _type_of(first) if first is not None else "(unknown)")
            elif callee in _GO_CREATORS and not _inside(node, {"var_spec", "const_spec"}):
                _add_raise(raises, rel, node, callee)
            elif callee == "recover":
                _add_handler(handlers, rel, node, [])
            elif callee in ("errors.Is", "errors.As") and args is not None and len(args.named_children) >= 2:
                target = args.named_children[1]
                if target.type == "unary_expression" and target.named_children:
                    target = target.named_children[0]
                _add_handler(handlers, rel, node, [_text(target)])


_RUST_PANIC_MACROS = {"panic", "unreachable", "todo", "unimplemented", "bail", "ensure"}


def _rust(root: Node, rel: str, classes: list, raises: list, handlers: list) -> None:
    for node in _walk(root):
        if node.type in ("struct_item", "enum_item"):
            derived = False
            sibling = node.prev_named_sibling
            while sibling is not None and sibling.type == "attribute_item":
                if "derive" in _text(sibling) and re.search(r"\bError\b", _text(sibling)):
                    derived = True
                sibling = sibling.prev_named_sibling
            if derived:
                _add_class(classes, rel, node, _text(node.child_by_field_name("name")), ["Error"], {"Error"})
        elif node.type == "impl_item":
            trait = node.child_by_field_name("trait")
            if trait is not None and _last(_text(trait)) == "Error":
                _add_class(classes, rel, node, _text(node.child_by_field_name("type")), ["Error"], {"Error"})
        elif node.type == "macro_invocation":
            macro = _text(node.child_by_field_name("macro"))
            if macro in _RUST_PANIC_MACROS:
                _add_raise(raises, rel, node, f"{macro}!")
        elif node.type == "call_expression":
            callee = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if callee is not None and _text(callee) == "Err" and args is not None and args.named_children:
                value = args.named_children[0]
                if value.type == "call_expression":
                    value = value.child_by_field_name("function") or value
                elif value.type == "struct_expression":
                    value = value.child_by_field_name("name") or value
                label = _text(value)
                label = label.rsplit("::", 1)[0] if "::" in label else label
                _add_raise(raises, rel, node, _last(label) if re.match(r"^[A-Za-z_][\w:]*$", label) else "(expression)")
            elif callee is not None and _last(_text(callee)) == "catch_unwind":
                _add_handler(handlers, rel, node, [])


_EXTRACTORS = {
    "python": lambda *a, **k: _python(*a[:5]),
    "javascript": lambda *a, **k: _js(*a[:5]),
    "typescript": lambda *a, **k: _js(*a[:5]),
    "java": lambda *a, **k: _java_like(*a[:5], "java"),
    "csharp": lambda *a, **k: _java_like(*a[:5], "csharp"),
    "php": lambda *a, **k: _java_like(*a[:5], "php"),
    "kotlin": lambda *a, **k: _kotlin(*a[:5]),
    "ruby": lambda *a, **k: _ruby(*a[:5]),
    "swift": lambda *a, **k: _swift(*a[:5]),
    "go": lambda *a, **k: _go(*a[:5]),
    "rust": lambda *a, **k: _rust(*a[:5]),
}


def _error_type_names(classes: list[dict]) -> set[str]:
    known: set[str] = set()
    changed = True
    while changed:
        changed = False
        for cls in classes:
            if cls["name"] in known:
                continue
            if any(_is_errorish(b, cls["seeds"]) or _last(b) in known for b in cls["bases"]):
                known.add(cls["name"])
                changed = True
    return known


def map_error_handling(repo_path: Path, ignored_paths: list[str] | None = None) -> dict:
    parsers: dict[str, Parser] = {}
    classes: list[dict] = []
    raises: list[dict] = []
    handlers: list[dict] = []

    for path in _iter_source_files(repo_path, ignored_paths):
        entry = LANGUAGE_BY_EXTENSION.get(path.suffix)
        if entry is None or (entry[0] not in _EXTRACTORS and entry[0] not in ("c", "cpp")):
            continue
        rel = _rel(repo_path, path)
        if rel is None:
            continue
        language_name, language = entry
        parser = parsers.get(language_name)
        if parser is None:
            parser = Parser()
            parser.language = language
            parsers[language_name] = parser
        try:
            source = path.read_bytes()
        except OSError:
            continue
        root = parser.parse(source).root_node
        if language_name in ("c", "cpp"):
            _cpp(root, rel, classes, raises, handlers, source.decode(errors="replace"))
        else:
            _EXTRACTORS[language_name](root, rel, classes, raises, handlers)

    error_names = _error_type_names(classes)
    error_types = [
        {"name": c["name"], "file": c["file"], "line": c["line"], "bases": c["bases"]}
        for c in classes if c["name"] in error_names
    ]
    error_types = list({(t["file"], t["line"], t["name"]): t for t in error_types}.values())
    error_types.sort(key=lambda t: (t["file"], t["line"], t["name"]))
    raises.sort(key=lambda s: (s["file"], s["line"]))
    handlers.sort(key=lambda h: (h["file"], h["line"]))

    summary: dict[str, dict] = {}

    def _entry_for(name: str) -> dict:
        return summary.setdefault(name, {"name": name, "defined_in": "", "raised": 0, "caught": 0})

    for definition in error_types:
        _entry_for(definition["name"])["defined_in"] = definition["file"]
    for site in raises:
        if site["error_type"] not in _NON_TYPES:
            _entry_for(_last(site["error_type"]))["raised"] += 1
    for handler in handlers:
        for caught in handler["catches"]:
            if caught not in _NON_TYPES:
                _entry_for(_last(caught))["caught"] += 1
    ranked = sorted(summary.values(), key=lambda e: (-(e["raised"] + e["caught"]), e["name"]))
    ranked = [e for e in ranked if e["raised"] or e["caught"] or e["defined_in"]]

    truncated = (
        len(error_types) > MAX_ERROR_TYPES or len(raises) > MAX_RAISE_SITES
        or len(handlers) > MAX_HANDLERS or len(ranked) > MAX_SUMMARY
    )
    return {
        "checked": True,
        "error_types": error_types[:MAX_ERROR_TYPES],
        "raise_sites": raises[:MAX_RAISE_SITES],
        "handlers": handlers[:MAX_HANDLERS],
        "by_error_type": ranked[:MAX_SUMMARY],
        "truncated": truncated,
    }
