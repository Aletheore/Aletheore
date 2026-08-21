from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.graph import (
    CSHARP_LANGUAGE,
    GO_LANGUAGE,
    JAVA_LANGUAGE,
    JS_LANGUAGE,
    PHP_LANGUAGE,
    PY_LANGUAGE,
    RUBY_LANGUAGE,
    RUST_LANGUAGE,
    TS_LANGUAGE,
    TSX_LANGUAGE,
    _iter_source_files,
    _python_source_roots,
    _rel,
    _resolve_python_from_import,
)

_ROUTE_VERB_METHODS = {"get", "post", "put", "delete", "patch"}
_DJANGO_ROUTE_FUNCS = {"path", "re_path"}
_EXPRESS_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "all"}
_GO_HANDLE_FIELDS = {"HandleFunc", "Handle"}
_HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
_GIN_VERB_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
_RUST_COMBINATOR_METHODS = {"get", "post", "put", "delete", "patch"}
_SPRING_VERB_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}
_RAILS_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
_LARAVEL_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "any"}
_ASPNET_ATTRIBUTE_METHODS = {
    "HttpGet": "GET",
    "HttpPost": "POST",
    "HttpPut": "PUT",
    "HttpDelete": "DELETE",
    "HttpPatch": "PATCH",
}
_ASPNET_MINIMAL_METHODS = {
    "MapGet": "GET",
    "MapPost": "POST",
    "MapPut": "PUT",
    "MapDelete": "DELETE",
    "MapPatch": "PATCH",
}


def _string_literal_text(node: Node, source: bytes) -> str:
    raw = source[node.start_byte : node.end_byte].decode()
    if raw.startswith(("r'", 'r"', "R'", 'R"')):
        raw = raw[1:]
    return raw.strip("'\"")


def _resolve_router_definition_file(
    root: Node, source: bytes, name: str, repo_path: Path, path: Path, source_roots: list[Path]
) -> str:
    """Finds which file `name` (the identifier passed as include_router's
    router argument) actually originates from, by following a `from
    <module> import name [as alias]` statement in this same file - "router"
    is the idiomatic FastAPI variable name, so without this, two different
    files' routers both imported under that same conventional bare name
    would be indistinguishable to the caller (see before_launch_fixes.md
    Batch 4 finding #4). Falls back to this file's own rel_path when no
    such import binds the name - the pre-existing, still-correct same-file
    case (`router = APIRouter()` and `include_router(router, ...)` both
    here)."""
    for n in _walk_tree(root):
        if n.type != "import_from_statement":
            continue
        module_node = n.child_by_field_name("module_name")
        if module_node is None:
            continue
        for name_node in n.children_by_field_name("name"):
            if name_node.type == "aliased_import":
                imported_node = name_node.child_by_field_name("name")
                alias_node = name_node.child_by_field_name("alias")
                local_name = (
                    source[alias_node.start_byte:alias_node.end_byte].decode() if alias_node else None
                )
            else:
                imported_node = name_node
                local_name = source[name_node.start_byte:name_node.end_byte].decode()
            if local_name != name or imported_node is None:
                continue
            module_name = source[module_node.start_byte:module_node.end_byte].decode()
            imported_name = source[imported_node.start_byte:imported_node.end_byte].decode()
            target = _resolve_python_from_import(repo_path, module_name, imported_name, path, source_roots)
            if target is not None:
                return target
    return _rel(repo_path, path)


def _collect_fastapi_include_prefixes(
    root: Node, source: bytes, repo_path: Path, path: Path, source_roots: list[Path]
) -> dict[tuple[str, str], list[str]]:
    prefixes: dict[tuple[str, str], list[str]] = {}
    for n in _walk_tree(root):
        if n.type != "call":
            continue
        function = n.child_by_field_name("function")
        args = n.child_by_field_name("arguments")
        if function is None or args is None or function.type != "attribute":
            continue
        name = function.child_by_field_name("attribute")
        if name is None or source[name.start_byte:name.end_byte].decode() != "include_router":
            continue
        positional = [arg for arg in args.named_children if arg.type != "keyword_argument"]
        if not positional or positional[0].type != "identifier":
            continue
        prefix_arg = next(
            (arg for arg in args.named_children if arg.type == "keyword_argument"
             and source[arg.child_by_field_name("name").start_byte:arg.child_by_field_name("name").end_byte].decode() == "prefix"),
            None,
        )
        if prefix_arg is None:
            continue
        value = prefix_arg.child_by_field_name("value")
        if value is None or value.type != "string":
            continue
        router = source[positional[0].start_byte:positional[0].end_byte].decode()
        defining_file = _resolve_router_definition_file(root, source, router, repo_path, path, source_roots)
        prefixes.setdefault((defining_file, router), []).append(_string_literal_text(value, source))
    return prefixes


def _walk_tree(root: Node):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _extract_flask_fastapi_routes(
    root: Node,
    source: bytes,
    rel_path: str,
    external_router_mount_prefixes: dict[tuple[str, str], list[str]] | None = None,
) -> list[dict]:
    entries: list[dict] = []

    def literal(node: Node | None) -> str | None:
        if node is None or node.type != "string":
            return None
        return _string_literal_text(node, source)

    def join_prefix(prefix: str, path: str) -> str:
        if not prefix:
            return path
        if not path:
            return prefix
        return f"/{prefix.strip('/')}/{path.strip('/')}" if path != "/" else f"/{prefix.strip('/')}/"

    router_prefixes: dict[str, str] = {}
    # external_router_mount_prefixes is keyed by (defining_file, router_name)
    # - "router" is the idiomatic FastAPI variable name, so two different
    # files' routers both imported under that same conventional bare name
    # would otherwise collide (see before_launch_fixes.md Batch 4 finding
    # #4). Filtering to entries whose defining file is THIS file before
    # dropping down to a bare-name dict is what disambiguates them: this
    # file's own "router" only ever picks up mounts resolved back to it.
    router_mount_prefixes: dict[str, list[str]] = {
        name: list(prefixes)
        for (defining_file, name), prefixes in (external_router_mount_prefixes or {}).items()
        if defining_file == rel_path
    }

    def collect_static_prefixes(n: Node) -> None:
        # include_router(...) prefixes are deliberately NOT collected here even
        # when the call happens to live in this same file - map_api_endpoints's
        # cross-file pre-pass (_collect_fastapi_include_prefixes) already scans
        # every .py file, this one included, and hands the result in via
        # external_router_mount_prefixes. Collecting it again here double-counted
        # the prefix for any router mounted in the same file it's defined in
        # (e.g. router = APIRouter(...); ...; app.include_router(router, prefix=...)
        # all in one module) - confirmed via a real map_api_endpoints() run:
        # "/internal" was applied twice, producing .../internal/internal/....
        if n.type == "call":
            function = n.child_by_field_name("function")
            args = n.child_by_field_name("arguments")
            if function is not None and args is not None:
                function_text = source[function.start_byte:function.end_byte].decode()
                if function.type == "identifier" and function_text == "APIRouter":
                    parent = n.parent
                    if parent is not None and parent.type == "assignment":
                        left = parent.child_by_field_name("left")
                        if left is not None and left.type == "identifier":
                            prefix_arg = next(
                                (arg for arg in args.named_children if arg.type == "keyword_argument"
                                 and source[arg.child_by_field_name("name").start_byte:arg.child_by_field_name("name").end_byte].decode() == "prefix"),
                                None,
                            )
                            prefix = literal(prefix_arg.child_by_field_name("value")) if prefix_arg else None
                            if prefix is not None:
                                router_prefixes[source[left.start_byte:left.end_byte].decode()] = prefix
        for child in n.children:
            collect_static_prefixes(child)

    collect_static_prefixes(root)

    def walk(n: Node) -> None:
        if n.type == "decorated_definition":
            definition = n.child_by_field_name("definition")
            handler = "unknown"
            if definition is not None and definition.type == "function_definition":
                name_node = definition.child_by_field_name("name")
                if name_node is not None:
                    handler = source[name_node.start_byte : name_node.end_byte].decode()

            for decorator in (c for c in n.children if c.type == "decorator"):
                call = next((c for c in decorator.named_children if c.type == "call"), None)
                if call is None:
                    continue
                func = call.child_by_field_name("function")
                if func is None or func.type != "attribute":
                    continue
                attribute_node = func.child_by_field_name("attribute")
                if attribute_node is None:
                    continue
                attribute_name = source[
                    attribute_node.start_byte : attribute_node.end_byte
                ].decode()

                args = call.child_by_field_name("arguments")
                if args is None:
                    continue
                path_node = next((a for a in args.named_children if a.type == "string"), None)
                if path_node is None:
                    continue
                path = _string_literal_text(path_node, source)
                router_object = func.child_by_field_name("object")
                router_name = (
                    source[router_object.start_byte:router_object.end_byte].decode()
                    if router_object is not None and router_object.type == "identifier"
                    else None
                )
                # A router mounted at more than one prefix (include_router(router,
                # prefix="/api") in one place, include_router(router, prefix="/admin")
                # in another) is reachable at each mount separately - every route on it
                # really exists at both "/api/..." and "/admin/...". Chaining the mount
                # prefixes onto one path instead produced a single, wrong compound path
                # ("/api/admin/...") and silently dropped the other mount's endpoint
                # entirely. Each mount prefix now composes with the router's own
                # constructor prefix (if any) into its own separate path.
                constructor_prefix = router_prefixes.get(router_name) if router_name is not None else None
                mount_prefixes = (
                    router_mount_prefixes.get(router_name, []) if router_name is not None else []
                )

                def compose(mount_prefix: str | None, _path: str = path) -> str:
                    composed = _path
                    if constructor_prefix:
                        composed = join_prefix(constructor_prefix, composed)
                    if mount_prefix:
                        composed = join_prefix(mount_prefix, composed)
                    return composed

                paths = [compose(mp) for mp in mount_prefixes] if mount_prefixes else [compose(None)]
                line = decorator.start_point[0] + 1

                if attribute_name == "route":
                    methods = ["GET"]
                    for arg in args.named_children:
                        if arg.type != "keyword_argument":
                            continue
                        kw_name = arg.child_by_field_name("name")
                        if kw_name is None:
                            continue
                        if source[kw_name.start_byte : kw_name.end_byte].decode() != "methods":
                            continue
                        value = arg.child_by_field_name("value")
                        if value is not None and value.type == "list":
                            methods = [
                                _string_literal_text(item, source).upper()
                                for item in value.named_children
                                if item.type == "string"
                            ]
                    for method in methods:
                        for entry_path in paths:
                            entries.append(
                                {
                                    "method": method,
                                    "path": entry_path,
                                    "framework": "flask",
                                    "file": rel_path,
                                    "line": line,
                                    "handler": handler,
                                    "unresolved": False,
                                    "note": None,
                                }
                            )
                elif attribute_name in _ROUTE_VERB_METHODS:
                    for entry_path in paths:
                        entries.append(
                            {
                                "method": attribute_name.upper(),
                                "path": entry_path,
                                "framework": "flask_or_fastapi",
                                "file": rel_path,
                                "line": line,
                                "handler": handler,
                                "unresolved": False,
                                "note": None,
                            }
                        )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _extract_django_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "assignment":
            left = n.child_by_field_name("left")
            right = n.child_by_field_name("right")
            is_urlpatterns = (
                left is not None
                and left.type == "identifier"
                and source[left.start_byte : left.end_byte].decode() == "urlpatterns"
            )
            if is_urlpatterns and right is not None and right.type == "list":
                for item in right.named_children:
                    entry = _django_call_to_entry(item, source, rel_path)
                    if entry is not None:
                        entries.append(entry)
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _django_call_to_entry(call: Node, source: bytes, rel_path: str) -> dict | None:
    if call.type != "call":
        return None
    func = call.child_by_field_name("function")
    if func is None or func.type != "identifier":
        return None
    func_name = source[func.start_byte : func.end_byte].decode()
    if func_name not in _DJANGO_ROUTE_FUNCS and func_name != "include":
        return None

    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    positional = [a for a in args.named_children if a.type != "keyword_argument"]
    if not positional or positional[0].type != "string":
        return None
    path = _string_literal_text(positional[0], source)
    line = call.start_point[0] + 1

    if func_name == "include":
        return {
            "method": None,
            "path": path,
            "framework": "django",
            "file": rel_path,
            "line": line,
            "handler": "include(...)",
            "unresolved": True,
            "note": None,
        }

    handler = "unknown"
    if len(positional) >= 2:
        view = positional[1]
        handler = source[view.start_byte : view.end_byte].decode()

    return {
        "method": "ANY",
        "path": path,
        "framework": "django",
        "file": rel_path,
        "line": line,
        "handler": handler,
        "unresolved": False,
        "note": None,
    }


def _js_string_literal_text(node: Node, source: bytes) -> str:
    raw = source[node.start_byte : node.end_byte].decode()
    return raw.strip("'\"")


def _express_handler_label(node: Node | None, source: bytes) -> str:
    if node is None:
        return "unknown"
    if node.type == "identifier":
        return source[node.start_byte : node.end_byte].decode()
    return "<inline handler>"


def _looks_like_express_path(path: str) -> bool:
    # `.get(...)`/`.post(...)`/`.use(...)` are extremely common method names
    # on plain objects, Maps, and third-party libraries that have nothing to
    # do with Express (e.g. a Motion/animation library's internal state
    # getters: e.get("stroke-dasharray"), e.get("transformOrigin")). Without
    # this, any such call with a string literal first argument gets
    # misidentified as a registered route. A real Express path always
    # starts with "/" (or is the "*" wildcard) - this cheaply rejects the
    # overwhelming majority of false positives with no risk to real routes.
    return path == "*" or path.startswith("/")


def _extract_express_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func is not None and func.type == "member_expression":
                property_node = func.child_by_field_name("property")
                args = n.child_by_field_name("arguments")
                if property_node is not None and args is not None:
                    method_name = source[
                        property_node.start_byte : property_node.end_byte
                    ].decode()
                    named = args.named_children
                    if named and named[0].type == "string":
                        path = _js_string_literal_text(named[0], source)
                        if _looks_like_express_path(path):
                            line = n.start_point[0] + 1
                            handler_node = named[1] if len(named) > 1 else None

                            if method_name in _EXPRESS_ROUTE_METHODS:
                                entries.append(
                                    {
                                        "method": (
                                            "ANY" if method_name == "all" else method_name.upper()
                                        ),
                                        "path": path,
                                        "framework": "express",
                                        "file": rel_path,
                                        "line": line,
                                        "handler": _express_handler_label(handler_node, source),
                                        "unresolved": False,
                                        "note": None,
                                    }
                                )
                            elif method_name == "use":
                                entries.append(
                                    {
                                        "method": None,
                                        "path": path,
                                        "framework": "express",
                                        "file": rel_path,
                                        "line": line,
                                        "handler": "app.use(...)",
                                        "unresolved": True,
                                        "note": None,
                                    }
                                )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _go_string_literal_text(node: Node, source: bytes) -> str:
    content = next(
        (c for c in node.children if c.type == "interpreted_string_literal_content"), None
    )
    if content is None:
        return ""
    return source[content.start_byte : content.end_byte].decode()


def _split_go_pattern(raw_path: str) -> tuple[str, str]:
    parts = raw_path.split(" ", 1)
    if len(parts) == 2 and parts[0] in _HTTP_METHODS:
        return parts[0], parts[1]
    return "ANY", raw_path


def _go_is_wrapped_by_methods_chain(call_node: Node, source: bytes) -> bool:
    parent = call_node.parent
    if parent is None or parent.type != "selector_expression":
        return False
    grandparent = parent.parent
    if grandparent is None or grandparent.type != "call_expression":
        return False
    outer_field = parent.child_by_field_name("field")
    return (
        outer_field is not None
        and source[outer_field.start_byte : outer_field.end_byte].decode() == "Methods"
    )


def _go_handler_name(args_named: list[Node], source: bytes) -> str:
    if len(args_named) > 1 and args_named[1].type == "identifier":
        return source[args_named[1].start_byte : args_named[1].end_byte].decode()
    return "unknown"


def _extract_go_net_http_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func is not None and func.type == "selector_expression":
                field = func.child_by_field_name("field")
                operand = func.child_by_field_name("operand")
                if field is not None and operand is not None:
                    field_name = source[field.start_byte : field.end_byte].decode()

                    if field_name == "Methods" and operand.type == "call_expression":
                        inner = operand
                        inner_func = inner.child_by_field_name("function")
                        if inner_func is not None and inner_func.type == "selector_expression":
                            inner_field = inner_func.child_by_field_name("field")
                            inner_operand = inner_func.child_by_field_name("operand")
                            inner_field_name = (
                                source[inner_field.start_byte : inner_field.end_byte].decode()
                                if inner_field is not None
                                else ""
                            )
                            if inner_field_name in _GO_HANDLE_FIELDS:
                                inner_args = inner.child_by_field_name("arguments")
                                outer_args = n.child_by_field_name("arguments")
                                if (
                                    inner_operand is not None
                                    and inner_args is not None
                                    and outer_args is not None
                                ):
                                    inner_named = inner_args.named_children
                                    if (
                                        inner_named
                                        and inner_named[0].type == "interpreted_string_literal"
                                    ):
                                        raw_path = _go_string_literal_text(inner_named[0], source)
                                        _, path = _split_go_pattern(raw_path)
                                        handler = _go_handler_name(inner_named, source)
                                        operand_text = source[
                                            inner_operand.start_byte : inner_operand.end_byte
                                        ].decode()
                                        framework = (
                                            "go_net_http"
                                            if operand_text == "http"
                                            else "gorilla_mux"
                                        )
                                        methods = [
                                            _go_string_literal_text(a, source).upper()
                                            for a in outer_args.named_children
                                            if a.type == "interpreted_string_literal"
                                        ]
                                        line = inner.start_point[0] + 1
                                        for method in methods:
                                            entries.append(
                                                {
                                                    "method": method,
                                                    "path": path,
                                                    "framework": framework,
                                                    "file": rel_path,
                                                    "line": line,
                                                    "handler": handler,
                                                    "unresolved": False,
                                                    "note": None,
                                                }
                                            )

                    elif field_name == "PathPrefix":
                        args = n.child_by_field_name("arguments")
                        if args is not None:
                            named = args.named_children
                            if named and named[0].type == "interpreted_string_literal":
                                path = _go_string_literal_text(named[0], source)
                                entries.append(
                                    {
                                        "method": None,
                                        "path": path,
                                        "framework": "gorilla_mux",
                                        "file": rel_path,
                                        "line": n.start_point[0] + 1,
                                        "handler": "Subrouter()",
                                        "unresolved": True,
                                        "note": None,
                                    }
                                )

                    elif field_name in _GO_HANDLE_FIELDS:
                        if not _go_is_wrapped_by_methods_chain(n, source):
                            args = n.child_by_field_name("arguments")
                            if args is not None:
                                named = args.named_children
                                if named and named[0].type == "interpreted_string_literal":
                                    raw_path = _go_string_literal_text(named[0], source)
                                    method, path = _split_go_pattern(raw_path)
                                    handler = _go_handler_name(named, source)
                                    operand_text = source[
                                        operand.start_byte : operand.end_byte
                                    ].decode()
                                    framework = (
                                        "go_net_http" if operand_text == "http" else "gorilla_mux"
                                    )
                                    entries.append(
                                        {
                                            "method": method,
                                            "path": path,
                                            "framework": framework,
                                            "file": rel_path,
                                            "line": n.start_point[0] + 1,
                                            "handler": handler,
                                            "unresolved": False,
                                            "note": None,
                                        }
                                    )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _extract_gin_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func is not None and func.type == "selector_expression":
                field = func.child_by_field_name("field")
                if field is not None:
                    field_name = source[field.start_byte : field.end_byte].decode()
                    if field_name in _GIN_VERB_METHODS or field_name == "Any":
                        args = n.child_by_field_name("arguments")
                        if args is not None:
                            named = args.named_children
                            if named and named[0].type == "interpreted_string_literal":
                                path = _go_string_literal_text(named[0], source)
                                handler = _go_handler_name(named, source)
                                method = "ANY" if field_name == "Any" else field_name
                                entries.append(
                                    {
                                        "method": method,
                                        "path": path,
                                        "framework": "gin",
                                        "file": rel_path,
                                        "line": n.start_point[0] + 1,
                                        "handler": handler,
                                        "unresolved": False,
                                        "note": None,
                                    }
                                )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _rust_string_literal_text(node: Node, source: bytes) -> str:
    content = next((c for c in node.children if c.type == "string_content"), None)
    if content is None:
        return ""
    return source[content.start_byte : content.end_byte].decode()


def _collect_axum_combinators(node: Node, source: bytes) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []

    def walk(n: Node) -> None:
        if n.type != "call_expression":
            return
        func = n.child_by_field_name("function")
        if func is None:
            return
        if func.type == "identifier":
            name = source[func.start_byte : func.end_byte].decode()
            if name in _RUST_COMBINATOR_METHODS or name == "any":
                args = n.child_by_field_name("arguments")
                handler = "unknown"
                if args is not None:
                    named = args.named_children
                    if named and named[0].type == "identifier":
                        handler = source[named[0].start_byte : named[0].end_byte].decode()
                method = "ANY" if name == "any" else name.upper()
                results.append((method, handler))
        elif func.type == "field_expression":
            value = func.child_by_field_name("value")
            if value is not None:
                walk(value)
            field = func.child_by_field_name("field")
            if field is not None:
                field_name = source[field.start_byte : field.end_byte].decode()
                if field_name in _RUST_COMBINATOR_METHODS or field_name == "any":
                    args = n.child_by_field_name("arguments")
                    handler = "unknown"
                    if args is not None:
                        named = args.named_children
                        if named and named[0].type == "identifier":
                            handler = source[named[0].start_byte : named[0].end_byte].decode()
                    method = "ANY" if field_name == "any" else field_name.upper()
                    results.append((method, handler))

    walk(node)
    return results


def _extract_axum_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func is not None and func.type == "field_expression":
                field = func.child_by_field_name("field")
                if field is not None:
                    field_name = source[field.start_byte : field.end_byte].decode()
                    if field_name == "route":
                        args = n.child_by_field_name("arguments")
                        if args is not None:
                            named = args.named_children
                            if len(named) >= 2 and named[0].type == "string_literal":
                                path = _rust_string_literal_text(named[0], source)
                                line = n.start_point[0] + 1
                                for method, handler in _collect_axum_combinators(
                                    named[1], source
                                ):
                                    entries.append(
                                        {
                                            "method": method,
                                            "path": path,
                                            "framework": "axum",
                                            "file": rel_path,
                                            "line": line,
                                            "handler": handler,
                                            "unresolved": False,
                                            "note": None,
                                        }
                                    )
                    elif field_name == "nest":
                        args = n.child_by_field_name("arguments")
                        if args is not None:
                            named = args.named_children
                            if named and named[0].type == "string_literal":
                                path = _rust_string_literal_text(named[0], source)
                                entries.append(
                                    {
                                        "method": None,
                                        "path": path,
                                        "framework": "axum",
                                        "file": rel_path,
                                        "line": n.start_point[0] + 1,
                                        "handler": "nest(...)",
                                        "unresolved": True,
                                        "note": None,
                                    }
                                )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _java_string_literal_text(node: Node, source: bytes) -> str:
    content = next((c for c in node.children if c.type == "string_fragment"), None)
    if content is None:
        return ""
    return source[content.start_byte : content.end_byte].decode()


def _spring_path_from_args(args_list: Node | None, source: bytes) -> str | None:
    if args_list is None:
        return None
    for arg in args_list.named_children:
        if arg.type == "string_literal":
            return _java_string_literal_text(arg, source)
        if arg.type == "element_value_pair":
            key = arg.child_by_field_name("key")
            if key is not None and source[key.start_byte : key.end_byte].decode() == "value":
                value = arg.child_by_field_name("value")
                if value is not None and value.type == "string_literal":
                    return _java_string_literal_text(value, source)
    return None


def _spring_request_mapping_method(args_list: Node | None, source: bytes) -> str:
    if args_list is None:
        return "ANY"
    for arg in args_list.named_children:
        if arg.type == "element_value_pair":
            key = arg.child_by_field_name("key")
            if key is not None and source[key.start_byte : key.end_byte].decode() == "method":
                value = arg.child_by_field_name("value")
                if value is not None and value.type == "field_access":
                    field_node = value.child_by_field_name("field")
                    if field_node is not None:
                        return source[field_node.start_byte : field_node.end_byte].decode()
    return "ANY"


def _spring_class_prefix_note(method_node: Node, source: bytes) -> str | None:
    node = method_node.parent
    while node is not None and node.type != "class_declaration":
        node = node.parent
    if node is None:
        return None
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return None
    for ann in (c for c in modifiers.children if c.type == "annotation"):
        name_node = ann.child_by_field_name("name")
        if (
            name_node is not None
            and source[name_node.start_byte : name_node.end_byte].decode() == "RequestMapping"
        ):
            return "class-level @RequestMapping prefix present, not composed into this path"
    return None


def _extract_spring_boot_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "method_declaration":
            name_node = n.child_by_field_name("name")
            handler = "unknown"
            if name_node is not None:
                handler = source[name_node.start_byte : name_node.end_byte].decode()

            modifiers = next((c for c in n.children if c.type == "modifiers"), None)
            if modifiers is not None:
                for ann in (c for c in modifiers.children if c.type == "annotation"):
                    ann_name_node = ann.child_by_field_name("name")
                    if ann_name_node is None:
                        continue
                    ann_name = source[ann_name_node.start_byte : ann_name_node.end_byte].decode()
                    args_list = ann.child_by_field_name("arguments")

                    method: str | None = None
                    if ann_name in _SPRING_VERB_ANNOTATIONS:
                        method = _SPRING_VERB_ANNOTATIONS[ann_name]
                    elif ann_name == "RequestMapping":
                        method = _spring_request_mapping_method(args_list, source)

                    if method is not None:
                        path = _spring_path_from_args(args_list, source)
                        if path is not None:
                            entries.append(
                                {
                                    "method": method,
                                    "path": path,
                                    "framework": "spring_boot",
                                    "file": rel_path,
                                    "line": ann.start_point[0] + 1,
                                    "handler": handler,
                                    "unresolved": False,
                                    "note": _spring_class_prefix_note(n, source),
                                }
                            )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _ruby_string_content(node: Node, source: bytes) -> str:
    content = next((c for c in node.children if c.type == "string_content"), None)
    if content is None:
        return ""
    return source[content.start_byte : content.end_byte].decode()


def _rails_path_and_to(
    args: Node | None, source: bytes, is_root: bool
) -> tuple[str | None, str | None]:
    if args is None:
        return None, None
    path = None if not is_root else "/"
    to_value = None
    for arg in args.named_children:
        if arg.type == "string" and path is None:
            path = _ruby_string_content(arg, source)
        elif arg.type == "pair":
            key = arg.child_by_field_name("key")
            if key is not None and source[key.start_byte : key.end_byte].decode() == "to":
                value = arg.child_by_field_name("value")
                if value is not None and value.type == "string":
                    to_value = _ruby_string_content(value, source)
    return path, to_value


def _extract_rails_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "call":
            method_node = n.child_by_field_name("method")
            args = n.child_by_field_name("arguments")
            if method_node is not None and method_node.type == "identifier":
                method_name = source[method_node.start_byte : method_node.end_byte].decode()
                if method_name in _RAILS_ROUTE_METHODS or method_name == "root":
                    path, to_value = _rails_path_and_to(
                        args, source, is_root=(method_name == "root")
                    )
                    if to_value is not None and path is not None:
                        entries.append(
                            {
                                "method": "GET" if method_name == "root" else method_name.upper(),
                                "path": path,
                                "framework": "rails",
                                "file": rel_path,
                                "line": n.start_point[0] + 1,
                                "handler": to_value,
                                "unresolved": False,
                                "note": None,
                            }
                        )
                elif method_name == "resources" and args is not None:
                    named = args.named_children
                    if named and named[0].type == "simple_symbol":
                        resource_name = source[
                            named[0].start_byte : named[0].end_byte
                        ].decode().lstrip(":")
                        entries.append(
                            {
                                "method": None,
                                "path": resource_name,
                                "framework": "rails",
                                "file": rel_path,
                                "line": n.start_point[0] + 1,
                                "handler": "resources(...)",
                                "unresolved": True,
                                "note": None,
                            }
                        )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _php_string_content(node: Node, source: bytes) -> str:
    content = next((c for c in node.children if c.type == "string_content"), None)
    if content is None:
        return ""
    return source[content.start_byte : content.end_byte].decode()


def _php_argument_value(arg_wrapper: Node) -> Node | None:
    return arg_wrapper.children[0] if arg_wrapper.type == "argument" and arg_wrapper.children else None


def _laravel_handler_label(node: Node | None, source: bytes) -> str:
    if node is None:
        return "unknown"
    if node.type == "array_creation_expression":
        elements = [c for c in node.named_children if c.type == "array_element_initializer"]
        if elements:
            last_value = elements[-1].children[0] if elements[-1].children else None
            if last_value is not None and last_value.type == "string":
                return _php_string_content(last_value, source)
    if node.type == "anonymous_function":
        return "<inline handler>"
    return "unknown"


def _laravel_group_note(call_node: Node, source: bytes) -> str | None:
    node = call_node.parent
    while node is not None:
        if node.type == "scoped_call_expression":
            scope = node.child_by_field_name("scope")
            name = node.child_by_field_name("name")
            if (
                scope is not None
                and source[scope.start_byte : scope.end_byte].decode() == "Route"
                and name is not None
                and source[name.start_byte : name.end_byte].decode() == "group"
            ):
                return "declared inside a Route::group() prefix, not composed into this path"
        node = node.parent
    return None


def _extract_laravel_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "scoped_call_expression":
            scope = n.child_by_field_name("scope")
            name = n.child_by_field_name("name")
            if scope is not None and name is not None:
                scope_text = source[scope.start_byte : scope.end_byte].decode()
                method_name = source[name.start_byte : name.end_byte].decode()
                if scope_text == "Route":
                    args = n.child_by_field_name("arguments")
                    arg_values = (
                        [_php_argument_value(a) for a in args.named_children]
                        if args is not None
                        else []
                    )
                    line = n.start_point[0] + 1
                    note = _laravel_group_note(n, source)

                    if (
                        method_name in _LARAVEL_ROUTE_METHODS
                        and arg_values
                        and arg_values[0] is not None
                        and arg_values[0].type == "string"
                    ):
                        path = _php_string_content(arg_values[0], source)
                        handler = _laravel_handler_label(
                            arg_values[1] if len(arg_values) > 1 else None, source
                        )
                        method = "ANY" if method_name == "any" else method_name.upper()
                        entries.append(
                            {
                                "method": method,
                                "path": path,
                                "framework": "laravel",
                                "file": rel_path,
                                "line": line,
                                "handler": handler,
                                "unresolved": False,
                                "note": note,
                            }
                        )
                    elif (
                        method_name == "match"
                        and len(arg_values) >= 2
                        and arg_values[0] is not None
                        and arg_values[0].type == "array_creation_expression"
                        and arg_values[1] is not None
                        and arg_values[1].type == "string"
                    ):
                        methods = [
                            _php_string_content(el.children[0], source).upper()
                            for el in arg_values[0].named_children
                            if el.type == "array_element_initializer"
                            and el.children
                            and el.children[0].type == "string"
                        ]
                        path = _php_string_content(arg_values[1], source)
                        handler = _laravel_handler_label(
                            arg_values[2] if len(arg_values) > 2 else None, source
                        )
                        for method in methods:
                            entries.append(
                                {
                                    "method": method,
                                    "path": path,
                                    "framework": "laravel",
                                    "file": rel_path,
                                    "line": line,
                                    "handler": handler,
                                    "unresolved": False,
                                    "note": note,
                                }
                            )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _csharp_string_literal_text(node: Node, source: bytes) -> str:
    content = next((c for c in node.children if c.type == "string_literal_content"), None)
    if content is None:
        return ""
    return source[content.start_byte : content.end_byte].decode()


def _aspnet_attribute_path(attr_node: Node, source: bytes) -> str | None:
    args_list = next(
        (c for c in attr_node.children if c.type == "attribute_argument_list"), None
    )
    if args_list is None:
        return None
    for arg in args_list.named_children:
        if (
            arg.type == "attribute_argument"
            and arg.children
            and arg.children[0].type == "string_literal"
        ):
            return _csharp_string_literal_text(arg.children[0], source)
    return None


def _aspnet_attributes(node: Node) -> list[Node]:
    # A class/method can carry several stacked attributes on separate lines
    # (e.g. [ApiController]\n[Route(...)], or [Authorize]\n[HttpGet(...)]) -
    # each line is its own sibling attribute_list under the same declaration,
    # not one shared list. Checking only the first one silently misses every
    # attribute after the first line - confirmed against a real stacked
    # [ApiController]/[Route] class and a real [Authorize]/[HttpGet] method,
    # both extremely common real-world patterns.
    return [
        attr
        for attr_list in node.children
        if attr_list.type == "attribute_list"
        for attr in attr_list.children
        if attr.type == "attribute"
    ]


def _aspnet_class_prefix_note(method_node: Node, source: bytes) -> str | None:
    node = method_node.parent
    while node is not None and node.type != "class_declaration":
        node = node.parent
    if node is None:
        return None
    for attr in _aspnet_attributes(node):
        name_node = attr.child_by_field_name("name")
        if (
            name_node is not None
            and source[name_node.start_byte : name_node.end_byte].decode() == "Route"
        ):
            return "class-level [Route] template present, not composed into this path"
    return None


def _extract_aspnet_attribute_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "method_declaration":
            name_node = n.child_by_field_name("name")
            handler = "unknown"
            if name_node is not None:
                handler = source[name_node.start_byte : name_node.end_byte].decode()

            for attr in _aspnet_attributes(n):
                attr_name_node = attr.child_by_field_name("name")
                if attr_name_node is None:
                    continue
                attr_name = source[attr_name_node.start_byte : attr_name_node.end_byte].decode()
                if attr_name in _ASPNET_ATTRIBUTE_METHODS:
                    path = _aspnet_attribute_path(attr, source)
                    if path is not None:
                        entries.append(
                            {
                                "method": _ASPNET_ATTRIBUTE_METHODS[attr_name],
                                "path": path,
                                "framework": "aspnet_attribute",
                                "file": rel_path,
                                "line": attr.start_point[0] + 1,
                                "handler": handler,
                                "unresolved": False,
                                "note": _aspnet_class_prefix_note(n, source),
                            }
                        )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def _aspnet_minimal_handler_label(arg_wrapper: Node | None, source: bytes) -> str:
    if arg_wrapper is None or not arg_wrapper.children:
        return "unknown"
    value = arg_wrapper.children[0]
    if value.type == "identifier":
        return source[value.start_byte : value.end_byte].decode()
    if value.type == "lambda_expression":
        return "<inline handler>"
    return "unknown"


def _extract_aspnet_minimal_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "invocation_expression":
            func = n.child_by_field_name("function")
            if func is not None and func.type == "member_access_expression":
                name_node = func.child_by_field_name("name")
                args = n.child_by_field_name("arguments")
                if name_node is not None and args is not None:
                    method_name = source[name_node.start_byte : name_node.end_byte].decode()
                    named = args.named_children

                    if (
                        method_name in _ASPNET_MINIMAL_METHODS
                        and named
                        and named[0].type == "argument"
                        and named[0].children
                        and named[0].children[0].type == "string_literal"
                    ):
                        path = _csharp_string_literal_text(named[0].children[0], source)
                        handler = _aspnet_minimal_handler_label(
                            named[1] if len(named) > 1 else None, source
                        )
                        entries.append(
                            {
                                "method": _ASPNET_MINIMAL_METHODS[method_name],
                                "path": path,
                                "framework": "aspnet_minimal",
                                "file": rel_path,
                                "line": n.start_point[0] + 1,
                                "handler": handler,
                                "unresolved": False,
                                "note": None,
                            }
                        )
                    elif (
                        method_name == "MapGroup"
                        and named
                        and named[0].type == "argument"
                        and named[0].children
                        and named[0].children[0].type == "string_literal"
                    ):
                        path = _csharp_string_literal_text(named[0].children[0], source)
                        entries.append(
                            {
                                "method": None,
                                "path": path,
                                "framework": "aspnet_minimal",
                                "file": rel_path,
                                "line": n.start_point[0] + 1,
                                "handler": "MapGroup(...)",
                                "unresolved": True,
                                "note": None,
                            }
                        )
        for child in n.children:
            walk(child)

    walk(root)
    return entries


def map_api_endpoints(
    repo_path: Path,
    *,
    unchanged_endpoints: dict[str, list[dict]] | None = None,
    ignored_paths: list[str] | None = None,
) -> dict:
    """unchanged_endpoints: path -> the list of endpoint dicts previously
    found in that file (possibly empty), for files known not to have
    changed since that data was computed - skips tree-sitter parsing for
    those paths entirely, reusing the cached list as-is. Defaults to
    None: fully backward compatible, every file parsed fresh."""
    endpoints: list[dict] = []

    parsers: dict[str, Parser] = {}
    for name, lang in (
        ("py", PY_LANGUAGE),
        ("js", JS_LANGUAGE),
        ("ts", TS_LANGUAGE),
        ("tsx", TSX_LANGUAGE),
        ("go", GO_LANGUAGE),
        ("rs", RUST_LANGUAGE),
        ("java", JAVA_LANGUAGE),
        ("rb", RUBY_LANGUAGE),
        ("php", PHP_LANGUAGE),
        ("cs", CSHARP_LANGUAGE),
    ):
        parser = Parser()
        parser.language = lang
        parsers[name] = parser

    python_source_roots = _python_source_roots(repo_path)
    cross_file_router_mounts: dict[tuple[str, str], list[str]] = {}
    for path in _iter_source_files(repo_path, ignored_paths):
        if path.suffix != ".py":
            continue
        source = path.read_bytes()
        tree = parsers["py"].parse(source)
        collected = _collect_fastapi_include_prefixes(
            tree.root_node, source, repo_path, path, python_source_roots
        )
        for key, prefixes in collected.items():
            cross_file_router_mounts.setdefault(key, []).extend(prefixes)

    for path in _iter_source_files(repo_path, ignored_paths):
        rel_path = _rel(repo_path, path)

        if unchanged_endpoints is not None and rel_path in unchanged_endpoints:
            endpoints.extend(unchanged_endpoints[rel_path])
            continue

        suffix = path.suffix

        if suffix == ".py":
            source = path.read_bytes()
            tree = parsers["py"].parse(source)
            endpoints.extend(
                _extract_flask_fastapi_routes(
                    tree.root_node, source, rel_path, cross_file_router_mounts
                )
            )
            if path.name == "urls.py":
                endpoints.extend(_extract_django_routes(tree.root_node, source, rel_path))
        elif suffix in (".js", ".jsx"):
            source = path.read_bytes()
            tree = parsers["js"].parse(source)
            endpoints.extend(_extract_express_routes(tree.root_node, source, rel_path))
        elif suffix == ".ts":
            source = path.read_bytes()
            tree = parsers["ts"].parse(source)
            endpoints.extend(_extract_express_routes(tree.root_node, source, rel_path))
        elif suffix == ".tsx":
            source = path.read_bytes()
            tree = parsers["tsx"].parse(source)
            endpoints.extend(_extract_express_routes(tree.root_node, source, rel_path))
        elif suffix == ".go":
            source = path.read_bytes()
            tree = parsers["go"].parse(source)
            endpoints.extend(_extract_go_net_http_routes(tree.root_node, source, rel_path))
            endpoints.extend(_extract_gin_routes(tree.root_node, source, rel_path))
        elif suffix == ".rs":
            source = path.read_bytes()
            tree = parsers["rs"].parse(source)
            endpoints.extend(_extract_axum_routes(tree.root_node, source, rel_path))
        elif suffix == ".java":
            source = path.read_bytes()
            tree = parsers["java"].parse(source)
            endpoints.extend(_extract_spring_boot_routes(tree.root_node, source, rel_path))
        elif suffix == ".rb" and path.name == "routes.rb":
            source = path.read_bytes()
            tree = parsers["rb"].parse(source)
            endpoints.extend(_extract_rails_routes(tree.root_node, source, rel_path))
        elif suffix == ".php" and "routes" in Path(rel_path).parts:
            source = path.read_bytes()
            tree = parsers["php"].parse(source)
            endpoints.extend(_extract_laravel_routes(tree.root_node, source, rel_path))
        elif suffix == ".cs":
            source = path.read_bytes()
            tree = parsers["cs"].parse(source)
            endpoints.extend(_extract_aspnet_attribute_routes(tree.root_node, source, rel_path))
            endpoints.extend(_extract_aspnet_minimal_routes(tree.root_node, source, rel_path))

    return {"checked": True, "endpoints": endpoints}
