import re
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.graph import (
    CSHARP_LANGUAGE,
    GO_LANGUAGE,
    JAVA_LANGUAGE,
    JS_LANGUAGE,
    KOTLIN_LANGUAGE,
    PHP_LANGUAGE,
    PY_LANGUAGE,
    RUBY_LANGUAGE,
    RUST_LANGUAGE,
    SWIFT_LANGUAGE,
    TS_LANGUAGE,
    TSX_LANGUAGE,
    _iter_source_files,
    _python_source_roots,
    _rel,
    _resolve_python_from_import,
)

from aletheore.safe_io import read_bytes_or_none

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
_VAPOR_VERB_METHODS = {"get", "post", "put", "delete", "patch"}
_RAILS_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
_LARAVEL_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "any"}
# Laravel's direct equivalent of Rails' `resources` - generates all 7
# RESTful actions for a controller in one call. Unlike Rails, the
# controller is always an explicit second argument (::class reference or,
# in older code, a bare string), never inferred from the resource name by
# naming convention - confirmed empirically: this call form was completely
# unextracted before, a real gap of exactly the same shape and likely
# equal or greater real-world prevalence as the Rails `resources` gap
# #664 fixed, since Route::resource()/apiResource() is Laravel's own
# standard CRUD-controller idiom.
_LARAVEL_RESOURCE_METHODS = {"resource", "apiResource"}
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


def _resolve_from_import_binding(
    root: Node, source: bytes, name: str, repo_path: Path, path: Path, source_roots: list[Path]
) -> str | None:
    """Finds which file `name` is bound to by a `from <module> import name
    [as alias]` statement in this same file - None if no such import binds
    it (the caller decides what that means: a same-file definition for a
    bare router name, or an unresolvable reference for a module alias -
    see _resolve_router_definition_file and the attribute-access branch of
    _collect_fastapi_include_prefixes)."""
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
            target, _ambiguous = _resolve_python_from_import(
                repo_path, module_name, imported_name, path, source_roots
            )
            if target is not None:
                return target
    return None


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
    target = _resolve_from_import_binding(root, source, name, repo_path, path, source_roots)
    return target if target is not None else _rel(repo_path, path)


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
        if not positional:
            continue
        router_arg = positional[0]
        if router_arg.type == "identifier":
            router = source[router_arg.start_byte:router_arg.end_byte].decode()
            defining_file: str | None = _resolve_router_definition_file(
                root, source, router, repo_path, path, source_roots
            )
        elif router_arg.type == "attribute":
            # include_router(users.router, prefix="/users") - the exact
            # namespacing pattern this whole feature exists to disambiguate
            # (a bare "router" collides across files) done properly, and
            # previously invisible to this scan entirely: positional[0].type
            # was required to be a plain identifier, so this branch never
            # matched and the call was silently skipped - the mount prefix
            # never applied, producing a real, reachable endpoint's path
            # with its mount prefix missing. Confirmed directly: a router
            # mounted this way reported "/list" instead of "/users/list".
            #
            # No same-file fallback here (unlike the identifier case above):
            # a locally-defined router is always referenced by its own bare
            # name, never via module.attribute, so if the module alias
            # can't be resolved to a real import, this mount is genuinely
            # unknown rather than "must be this file" - skip only this
            # mount, same discipline as the non-literal-prefix case below.
            object_node = router_arg.child_by_field_name("object")
            attribute_node = router_arg.child_by_field_name("attribute")
            if object_node is None or object_node.type != "identifier" or attribute_node is None:
                continue
            module_alias = source[object_node.start_byte:object_node.end_byte].decode()
            router = source[attribute_node.start_byte:attribute_node.end_byte].decode()
            defining_file = _resolve_from_import_binding(
                root, source, module_alias, repo_path, path, source_roots
            )
            if defining_file is None:
                continue
        else:
            continue
        prefix_arg = next(
            (arg for arg in args.named_children if arg.type == "keyword_argument"
             and source[arg.child_by_field_name("name").start_byte:arg.child_by_field_name("name").end_byte].decode() == "prefix"),
            None,
        )
        if prefix_arg is None:
            # No prefix= at all - an ordinary FastAPI pattern for mounting a
            # router unprefixed alongside also mounting it elsewhere under a
            # versioned/admin prefix. Recorded as an explicit empty-string
            # mount rather than skipped: skipping it left this router's
            # mounts list empty for this call, and if the same router also
            # had one explicitly-prefixed mount elsewhere, that non-empty
            # list silently suppressed the fan-out branch that would have
            # emitted the implicit mount's own unprefixed path - a real,
            # reachable endpoint dropped from the map entirely. compose()
            # below already treats an empty-string mount_prefix as "nothing
            # to join", so recording it here is enough to fix the fan-out.
            prefix_text = ""
        else:
            value = prefix_arg.child_by_field_name("value")
            if value is None or value.type != "string":
                # prefix= given but not a literal string (a variable,
                # f-string, ...) - can't resolve it statically, so skip only
                # this mount rather than guessing at its value.
                continue
            prefix_text = _string_literal_text(value, source)
        prefixes.setdefault((defining_file, router), []).append(prefix_text)
    return prefixes


def _walk_tree(root: Node):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


_PY_ROUTE_VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _py_call_name(call: Node, source: bytes) -> tuple[str, str]:
    """(last name, full dotted text) of a call's callee: `web.get` -> (`get`, `web.get`)."""
    func = call.child_by_field_name("function")
    text = source[func.start_byte : func.end_byte].decode() if func is not None else ""
    return text.split(".")[-1], text


def _py_arguments(call: Node, source: bytes) -> tuple[list[Node], dict[str, Node]]:
    args = call.child_by_field_name("arguments")
    positional: list[Node] = []
    keyword: dict[str, Node] = {}
    for child in (args.named_children if args is not None else []):
        if child.type == "keyword_argument":
            key, value = child.child_by_field_name("name"), child.child_by_field_name("value")
            if key is not None and value is not None:
                keyword[source[key.start_byte : key.end_byte].decode()] = value
        else:
            positional.append(child)
    return positional, keyword


def _py_literal(node: Node | None, source: bytes) -> str | None:
    if node is None or node.type != "string" or any(c.type == "interpolation" for c in node.children):
        return None
    return _string_literal_text(node, source)


def _extract_starlette_aiohttp_routes(root: Node, source: bytes, rel_path: str, framework: str) -> list[dict]:
    """Starlette: `Route("/x", endpoint, methods=[..])` and `WebSocketRoute`, with `Mount("/p", routes=[..])` prefixes,
    plus `app.add_route("/x", h, methods=[..])`. aiohttp: `web.get("/x", h)` (and post, put, ...), `web.route("GET", "/x", h)`,
    `app.router.add_get("/x", h)` and `add_route("GET", "/x", h)`."""
    entries: list[dict] = []

    def emit(method: str, path: str, handler: str, node: Node, prefix: str = "") -> None:
        full = (prefix.rstrip("/") + "/" + path.lstrip("/")).rstrip("/") if prefix else path
        entries.append(
            {
                "method": method,
                "path": full or "/",
                "framework": framework,
                "file": rel_path,
                "line": node.start_point[0] + 1,
                "handler": handler,
                "unresolved": False,
                "note": None,
            }
        )

    def mount_prefix(node: Node) -> str:
        parts: list[str] = []
        ancestor = node.parent
        while ancestor is not None:
            if ancestor.type == "call" and _py_call_name(ancestor, source)[0].lower() == "mount":
                positional = _py_arguments(ancestor, source)[0]
                literal = _py_literal(positional[0], source) if positional else None
                if literal:
                    parts.append(literal)
            ancestor = ancestor.parent
        return "".join("/" + p.strip("/") for p in reversed(parts) if p.strip("/"))

    def handler_of(node: Node | None) -> str:
        return source[node.start_byte : node.end_byte].decode()[:60] if node is not None else "unknown"

    for call in _walk_tree(root):
        if call.type != "call":
            continue
        name, dotted = _py_call_name(call, source)
        positional, keyword = _py_arguments(call, source)
        if framework == "starlette":
            if name in ("Route", "WebSocketRoute") or (name == "add_route" and "." in dotted) or name == "add_websocket_route":
                path = _py_literal(positional[0] if positional else keyword.get("path"), source)
                if path is None or not path.startswith("/"):
                    continue  # aiohttp's add_route("GET", "/x", h) has the verb first
                endpoint = positional[1] if len(positional) > 1 else keyword.get("endpoint") or keyword.get("route")
                methods_node = keyword.get("methods")
                methods = (
                    [m.upper() for m in re.findall(r"""["']([A-Za-z]+)["']""", source[methods_node.start_byte : methods_node.end_byte].decode())]
                    if methods_node is not None
                    else ["WEBSOCKET"] if "WebSocket" in name or "websocket" in name else ["GET"]
                )
                prefix = mount_prefix(call)
                for method in methods:
                    emit(method, path, handler_of(endpoint), call, prefix)
        else:
            bare_ok = dotted == name and b"from aiohttp.web import" in source  # `from aiohttp.web import get`
            if name in _PY_ROUTE_VERBS and (dotted == f"web.{name}" or bare_ok) or (name.startswith("add_") and name[4:] in _PY_ROUTE_VERBS):
                verb = name if name in _PY_ROUTE_VERBS else name[4:]
                path = _py_literal(positional[0] if positional else keyword.get("path"), source)
                handler = positional[1] if len(positional) > 1 else keyword.get("handler")
                if path is not None:
                    emit(verb.upper(), path, handler_of(handler), call)
            elif name in ("route", "add_route") and ("web" in dotted or "router" in dotted) and (len(positional) >= 2 or keyword.get("path")):
                verb_node = positional[0] if positional else keyword.get("method")
                path_node = positional[1] if len(positional) > 1 else keyword.get("path")
                handler = positional[2] if len(positional) > 2 else keyword.get("handler")
                verb, path = _py_literal(verb_node, source), _py_literal(path_node, source)
                if verb is not None and path is not None:
                    emit(verb.upper() if verb != "*" else "ANY", path, handler_of(handler), call)
    return entries


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

    def collect_static_prefixes(root: Node) -> None:
        # Iterative, not recursive - see graph.py's walk() for why. Prunes
        # rather than descending unconditionally (skips function/class
        # bodies below, per the module-scope note further down), so it
        # can't reuse the plain _walk_tree() generator the unconditional
        # walks in this file were converted to.
        stack = [root]
        while stack:
            n = stack.pop()
            collect_static_prefixes_body(n)
            if n.type not in ("function_definition", "class_definition"):
                stack.extend(reversed(n.children))

    def collect_static_prefixes_body(n: Node) -> None:
        # include_router(...) prefixes are deliberately NOT collected here even
        # when the call happens to live in this same file - map_api_endpoints's
        # cross-file pre-pass (_collect_fastapi_include_prefixes) already scans
        # every .py file, this one included, and hands the result in via
        # external_router_mount_prefixes. Collecting it again here double-counted
        # the prefix for any router mounted in the same file it's defined in
        # (e.g. router = APIRouter(...); ...; app.include_router(router, prefix=...)
        # all in one module) - confirmed via a real map_api_endpoints() run:
        # "/internal" was applied twice, producing .../internal/internal/....
        #
        # Restricted to module scope: router_prefixes is a flat dict keyed
        # only by variable name text, with no notion of which scope an
        # identifier belongs to. Descending into function/class bodies let
        # an unrelated local `router = APIRouter(...)` (the idiomatic
        # FastAPI factory-function shape reusing the "router" name)
        # silently overwrite the real module-level router's prefix -
        # confirmed directly: a `make_test_router()` helper building its
        # own local router flipped every `@router.get(...)` in the module
        # onto the local router's prefix instead. The router a decorator
        # actually binds to is always resolved from module scope (a
        # decorator can only reference a name already in scope at that
        # point), so nothing below module level is ever a real match here.
        # (function_definition/class_definition bodies are pruned by the
        # caller below, before it ever reaches this function.)
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

    collect_static_prefixes(root)

    for n in _walk_tree(root):
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
    return entries


def _extract_django_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    for n in _walk_tree(root):
        # "urlpatterns += [...]" (splitting the list across an initial
        # assignment plus one or more extensions - an ordinary, documented
        # Django organizing pattern) parses to augmented_assignment, a
        # distinct node type from plain assignment - the unconditional
        # recursion below already walks into it, but nothing matched it
        # for extraction, so every route declared this way was silently
        # missing from the endpoint inventory with no indication anything
        # was skipped (audit finding 27). Both node types use the same
        # left/right field names in this grammar, confirmed directly.
        if n.type in ("assignment", "augmented_assignment"):
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


_NEXT_APP_ROUTE = re.compile(r"^((?:[^/]+/)*?(?:src/)?)app/((?:[^/]+/)*)route\.(?:ts|js|tsx|jsx)$")
_NEXT_PAGES_API = re.compile(r"^((?:[^/]+/)*?(?:src/)?)pages/(api(?:/[^/]+)*)\.(?:ts|js|tsx|jsx)$")
_NEXT_HTTP_EXPORT = re.compile(
    r"export\s+(?:async\s+)?(?:function\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b|const\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b)"
)
_NEXT_EXPORT_LIST = re.compile(r"export\s*\{([^}]*)\}")


def _next_segment(segment: str) -> str | None:
    """One folder / file name of a Next.js route -> its URL segment; None when it adds nothing (route groups, slots)."""
    if (segment.startswith("(") and segment.endswith(")")) or segment.startswith("@"):
        return None
    optional = re.fullmatch(r"\[\[\.\.\.(\w+)\]\]", segment)
    if optional:
        return "{..." + optional.group(1) + "?}"
    catch_all = re.fullmatch(r"\[\.\.\.(\w+)\]", segment)
    if catch_all:
        return "{..." + catch_all.group(1) + "}"
    dynamic = re.fullmatch(r"\[(\w+)\]", segment)
    return "{" + dynamic.group(1) + "}" if dynamic else segment


def _is_nextjs_project(repo_path: Path, prefix: str) -> bool:
    """`app/` and `pages/` are a Next.js router only in a Next.js project: the folder that holds them (`src/`'s parent
    when they sit under `src/`) has a `next.config.*` or a package.json that depends on `next`. A folder named `app` or
    `pages` deeper in the tree (`lib/app/`, `components/pages/`) is not that folder."""
    folder = (repo_path / (prefix[:-4] if prefix.endswith("src/") else prefix)).resolve()
    if any((folder / name).is_file() for name in ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs")):
        return True
    try:
        package_json = folder / "package.json"
        return package_json.is_file() and bool(re.search(r'"next"\s*:', package_json.read_text(encoding="utf-8", errors="ignore")))
    except OSError:
        return False


def _strip_js_comments(text: str) -> str:
    """The text with `//` and `/* */` comments blanked (same length, newlines kept)."""
    return re.sub(r"//[^\n]*|/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)


def _extract_nextjs_routes(rel_path: str, source: bytes, repo_path: Path | None = None) -> list[dict]:
    """Next.js file-system API routes. App router: `app/**/route.ts` (or under `src/`) exporting `GET`, `POST`, ... (a
    function, a const, or `export { GET }`); route groups `(group)` and slots `@slot` are not part of the URL and `[id]` /
    `[...slug]` become `{id}` / `{...slug}`. Pages router: every file under `pages/api/`, answering any method.
    A configured `basePath` is not applied."""
    app = _NEXT_APP_ROUTE.search(rel_path)
    pages = None if app else _NEXT_PAGES_API.search(rel_path)
    router = app or pages
    if router is None:
        return []  # most files: decided by the path alone, before the source is decoded
    if repo_path is not None and not _is_nextjs_project(repo_path, router.group(1)):
        return []
    text = _strip_js_comments(source.decode(errors="replace"))
    entries: list[dict] = []

    def emit(method: str, url_segments: list[str], line: int) -> None:
        parts = [seg for seg in (_next_segment(s) for s in url_segments) if seg]
        entries.append(
            {
                "method": method,
                "path": "/" + "/".join(parts),
                "framework": "nextjs",
                "file": rel_path,
                "line": line,
                "handler": method if method != "ANY" else "default",
                "unresolved": False,
                "note": None,
            }
        )

    if app:
        folders = [f for f in app.group(2).split("/") if f]
        found: dict[str, int] = {}
        for match in _NEXT_HTTP_EXPORT.finditer(text):
            found.setdefault(match.group(1) or match.group(2), text.count("\n", 0, match.start()) + 1)
        for match in _NEXT_EXPORT_LIST.finditer(text):
            for name in (item.split(" as ")[-1].strip() for item in match.group(1).split(",")):
                if name in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                    found.setdefault(name, text.count("\n", 0, match.start()) + 1)
        for method, line in sorted(found.items()):
            emit(method, folders, line)
        return entries
    default_export = re.search(r"export\s+default\b", text)
    if pages and default_export:
        segments = pages.group(2).split("/")
        if segments[-1] == "index":
            segments = segments[:-1]
        emit("ANY", segments, text.count("\n", 0, default_export.start()) + 1)
    return entries


def _js_object_pairs(obj: Node, source: bytes) -> dict[str, Node]:
    pairs: dict[str, Node] = {}
    for child in obj.named_children:
        if child.type == "pair":
            key, value = child.child_by_field_name("key"), child.child_by_field_name("value")
            if key is not None and value is not None:
                pairs[source[key.start_byte : key.end_byte].decode().strip("'\"`")] = value
        elif child.type in ("method_definition", "shorthand_property_identifier"):
            name = child.child_by_field_name("name") or child
            pairs.setdefault(source[name.start_byte : name.end_byte].decode(), child)
    return pairs


def _extract_js_route_objects(root: Node, source: bytes, rel_path: str) -> list[dict]:
    """The object form of a route: Fastify `fastify.route({ method: 'POST', url: '/items', handler })` and hapi
    `server.route({ method: 'GET', path: '/x', handler })` (also an array of them), `method: ['GET', 'HEAD']` allowed.
    The framework is named by what the file mentions (hapi, otherwise Fastify). The verb shorthands
    (`fastify.get('/x', h)`) are the Express matcher's."""
    entries: list[dict] = []
    lowered = source.lower()
    if b"fastify" not in lowered and b"hapi" not in lowered:
        return entries  # an object-form `.route({...})` needs one of the two frameworks to be in play
    # hapi is named by its package (`@hapi/hapi`, `require('hapi')`), not by the word anywhere in the file
    framework = "hapi" if re.search(rb"""['"]@?hapi(?:/hapi)?['"]""", source) and b"fastify" not in lowered else "fastify"
    for call in _walk_tree(root):
        if call.type != "call_expression":
            continue
        func = call.child_by_field_name("function")
        if func is None or func.type != "member_expression":
            continue
        prop = func.child_by_field_name("property")
        args = call.child_by_field_name("arguments")
        if prop is None or source[prop.start_byte : prop.end_byte].decode() != "route" or args is None:
            continue
        first = next((a for a in args.named_children if a.type in ("object", "array")), None)
        if first is None or (first.type == "array" and framework != "hapi"):
            continue  # an array of routes is hapi's form; Fastify's route() takes one object
        objects = [first] if first.type == "object" else [o for o in first.named_children if o.type == "object"]  # hapi: an array
        for obj in objects:
            entries.extend(_js_route_object_entries(obj, call, source, rel_path, framework))
    return entries


def _js_route_object_entries(obj: Node, call: Node, source: bytes, rel_path: str, framework: str) -> list[dict]:
    pairs = _js_object_pairs(obj, source)
    url_node = pairs.get("url") or pairs.get("path")
    method_node = pairs.get("method")
    if url_node is None or method_node is None or url_node.type != "string":
        return []
    path = _js_string_literal_text(url_node, source)
    if not _looks_like_express_path(path):
        return []
    if method_node.type == "string":
        methods = [_js_string_literal_text(method_node, source)]
    elif method_node.type == "array":
        methods = [_js_string_literal_text(m, source) for m in method_node.named_children if m.type == "string"]
    else:
        return []
    handler = _express_handler_label(pairs.get("handler"), source)
    return [
        {
            "method": method.upper(),
            "path": path,
            "framework": framework,
            "file": rel_path,
            "line": call.start_point[0] + 1,
            "handler": handler,
            "unresolved": False,
            "note": None,
        }
        for method in methods
    ]


def _extract_express_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    for n in _walk_tree(root):
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

    for n in _walk_tree(root):
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
    return entries


# Go's `:=` short declarations are scoped to the function they're declared
# in (or visible to a nested closure referencing an outer variable), never
# to a sibling function that merely happens to reuse the same identifier -
# real Go code does this routinely (every route-registration function in a
# typical gin app names its own local group variable "v1", "admin", "api").
_GO_FUNCTION_SCOPE_TYPES = {"function_declaration", "method_declaration", "func_literal"}


def _go_scope_chain(node: Node, root: Node) -> list[int]:
    """Ids of every function-scope boundary enclosing `node`, innermost
    first, always ending with the file's own root id (for a `:=` at file
    scope, which Go doesn't actually allow but costs nothing to fall back
    to safely)."""
    chain: list[int] = []
    current = node.parent
    while current is not None:
        if current.type in _GO_FUNCTION_SCOPE_TYPES:
            chain.append(current.id)
        current = current.parent
    chain.append(root.id)
    return chain


def _resolve_group_prefix(
    bindings: dict[tuple[int, str], str], node: Node, root: Node, var_name: str
) -> str:
    for scope_id in _go_scope_chain(node, root):
        prefix = bindings.get((scope_id, var_name))
        if prefix is not None:
            return prefix
    return ""


def _gin_group_prefix_bindings(root: Node, source: bytes) -> dict[tuple[int, str], str]:
    """(enclosing-function-scope id, var name) -> composed URL prefix for
    every `x := y.Group("/prefix")` short variable declaration in this
    file, including nested groups (`v2 := v1.Group("/v2")`).

    Keyed by scope, not by name alone: a flat file-wide `{name: prefix}`
    map means a later, unrelated `v1 := other.Group("/admin")` in a
    completely different function silently overwrites the map entry for
    every earlier route that used "v1" as its own, differently-scoped
    group variable - found via Flash Review, confirmed against a real
    two-function gin router file where this produced a wrong, plausible-
    looking `/admin` prefix on routes that were never actually grouped
    under `/admin` at all.

    A single forward pass is still sufficient within each scope: Go
    requires a variable declared before use, and _walk_tree visits nodes
    in source order, so a group's own receiver binding (if it has one) is
    already recorded by the time a later declaration in the same or an
    enclosing scope composes on top of it. A receiver not found in any
    enclosing scope (e.g. the base `*gin.Engine` returned by
    gin.Default()/gin.New(), which is never itself the result of a
    .Group() call) resolves to "" - the same unprefixed behavior every
    route already had before this existed, so this is purely additive,
    never a regression for the ungrouped case.
    """
    bindings: dict[tuple[int, str], str] = {}
    for n in _walk_tree(root):
        if n.type != "short_var_declaration":
            continue
        left = n.child_by_field_name("left")
        right = n.child_by_field_name("right")
        if left is None or right is None:
            continue
        left_names = left.named_children
        right_exprs = right.named_children
        if len(left_names) != 1 or left_names[0].type != "identifier":
            continue
        if len(right_exprs) != 1 or right_exprs[0].type != "call_expression":
            continue
        call = right_exprs[0]
        func = call.child_by_field_name("function")
        if func is None or func.type != "selector_expression":
            continue
        field = func.child_by_field_name("field")
        operand = func.child_by_field_name("operand")
        if field is None or operand is None or operand.type != "identifier":
            continue
        if source[field.start_byte : field.end_byte].decode() != "Group":
            continue
        args = call.child_by_field_name("arguments")
        if args is None:
            continue
        arg_named = args.named_children
        if not arg_named or arg_named[0].type != "interpreted_string_literal":
            continue
        prefix_literal = _go_string_literal_text(arg_named[0], source)
        receiver_name = source[operand.start_byte : operand.end_byte].decode()
        base_prefix = _resolve_group_prefix(bindings, n, root, receiver_name)
        var_name = source[left_names[0].start_byte : left_names[0].end_byte].decode()
        scope_id = _go_scope_chain(n, root)[0]
        bindings[(scope_id, var_name)] = f"{base_prefix.rstrip('/')}/{prefix_literal.strip('/')}"
    return bindings


_GO_WEB_FRAMEWORK_IMPORTS = (
    (b"github.com/go-chi/chi", "chi"),
    (b"github.com/labstack/echo", "echo"),
    (b"github.com/gofiber/fiber", "fiber"),
    (b"github.com/julienschmidt/httprouter", "httprouter"),
)
_GO_FRAMEWORK_VERBS = {
    "chi": {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "Head": "HEAD",
            "Options": "OPTIONS", "Connect": "CONNECT", "Trace": "TRACE", "Handle": "ANY", "HandleFunc": "ANY"},
    "echo": {"GET": "GET", "POST": "POST", "PUT": "PUT", "DELETE": "DELETE", "PATCH": "PATCH", "HEAD": "HEAD",
             "OPTIONS": "OPTIONS", "CONNECT": "CONNECT", "TRACE": "TRACE", "Any": "ANY"},
    "fiber": {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "Head": "HEAD",
              "Options": "OPTIONS", "Connect": "CONNECT", "Trace": "TRACE", "All": "ANY"},
    "httprouter": {"GET": "GET", "POST": "POST", "PUT": "PUT", "DELETE": "DELETE", "PATCH": "PATCH", "HEAD": "HEAD",
                   "OPTIONS": "OPTIONS", "Handler": "ANY", "HandlerFunc": "ANY"},
}


def _go_web_frameworks(source: bytes) -> list[str]:
    """Every one of chi, Echo, Fiber and httprouter the file imports: their route calls look like Gin's or net/http's, so
    the imports decide which framework a `r.Get("/x", h)` / `e.GET("/x", h)` belongs to."""
    return [name for marker, name in _GO_WEB_FRAMEWORK_IMPORTS if marker in source]


def _go_chi_route_prefix(node: Node, source: bytes) -> str:
    """Prefix a call carries from the enclosing `r.Route("/api", func(r chi.Router) { ... })` closures."""
    parts: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "func_literal":
            args = current.parent
            call = args.parent if args is not None and args.type == "argument_list" else None
            func = call.child_by_field_name("function") if call is not None else None
            if func is not None and func.type == "selector_expression":
                field = func.child_by_field_name("field")
                named = args.named_children
                if (
                    field is not None
                    and source[field.start_byte : field.end_byte].decode() == "Route"
                    and named
                    and named[0].type == "interpreted_string_literal"
                    and named[-1].id == current.id
                ):
                    parts.append(_go_string_literal_text(named[0], source))
        current = current.parent
    return "".join("/" + part.strip("/") for part in reversed(parts) if part.strip("/"))


def _extract_go_framework_routes(root: Node, source: bytes, rel_path: str, framework: str) -> list[dict]:
    """Routes of chi, Echo and Fiber: verb calls with a literal path, composed with the `Group("/p")` variable they are
    called on (Echo, Fiber) or the enclosing `Route("/p", func(r chi.Router) {..})` closures (chi)."""
    verbs = _GO_FRAMEWORK_VERBS[framework]
    group_prefixes = _gin_group_prefix_bindings(root, source) if framework in ("echo", "fiber") else {}
    entries: list[dict] = []
    for n in _walk_tree(root):
        if n.type != "call_expression":
            continue
        func = n.child_by_field_name("function")
        if func is None or func.type != "selector_expression":
            continue
        field = func.child_by_field_name("field")
        args = n.child_by_field_name("arguments")
        if field is None or args is None:
            continue
        name = source[field.start_byte : field.end_byte].decode()
        package = func.child_by_field_name("operand")
        if package is not None and package.type == "identifier" and source[package.start_byte : package.end_byte] == b"http":
            continue  # `http.HandleFunc(...)` is net/http's own matcher's
        named = args.named_children
        method = verbs.get(name)
        if framework == "httprouter" and name in ("Handle", "Handler", "HandlerFunc") and len(named) >= 3 and named[0].type == "interpreted_string_literal":
            method = _go_string_literal_text(named[0], source).upper() or None
            named = named[1:]
        elif framework == "httprouter" and name in ("Handler", "HandlerFunc"):
            method = None  # `Handler(method, path, h)` needs the method first
        if framework == "chi" and name == "Method" and len(named) >= 3 and named[0].type == "interpreted_string_literal":
            method = _go_string_literal_text(named[0], source).upper() or None
            named = named[1:]
        if method is None or not named or named[0].type != "interpreted_string_literal":
            continue
        path = _go_string_literal_text(named[0], source)
        if framework == "chi":
            prefix = _go_chi_route_prefix(n, source)
        elif framework == "httprouter":
            prefix = ""
        else:
            operand = func.child_by_field_name("operand")
            receiver = source[operand.start_byte : operand.end_byte].decode() if operand is not None and operand.type == "identifier" else None
            prefix = _resolve_group_prefix(group_prefixes, n, root, receiver) if receiver else ""
        full_path = f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix else path
        entries.append(
            {
                "method": method,
                "path": full_path or "/",
                "framework": framework,
                "file": rel_path,
                "line": n.start_point[0] + 1,
                "handler": _go_handler_name(named, source),
                "unresolved": False,
                "note": None,
            }
        )
    return entries


def _extract_gin_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []
    group_prefixes = _gin_group_prefix_bindings(root, source)

    for n in _walk_tree(root):
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
                                operand = func.child_by_field_name("operand")
                                receiver_name = (
                                    source[operand.start_byte : operand.end_byte].decode()
                                    if operand is not None and operand.type == "identifier"
                                    else None
                                )
                                prefix = (
                                    _resolve_group_prefix(group_prefixes, n, root, receiver_name)
                                    if receiver_name
                                    else ""
                                )
                                full_path = f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix else path
                                entries.append(
                                    {
                                        "method": method,
                                        "path": full_path,
                                        "framework": "gin",
                                        "file": rel_path,
                                        "line": n.start_point[0] + 1,
                                        "handler": handler,
                                        "unresolved": False,
                                        "note": None,
                                    }
                                )
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


_SINATRA_VERBS = {"get", "post", "put", "delete", "patch", "head", "options", "link", "unlink"}


def _sinatra_string(node: Node | None, source: bytes) -> tuple[str | None, bool]:
    """(text, dynamic) of a Ruby string argument; (None, False) when the argument is not a string at all."""
    if node is None or node.type != "string":
        return None, False
    if any(c.type == "interpolation" for c in node.children):
        return None, True
    return "".join(source[c.start_byte : c.end_byte].decode(errors="replace") for c in node.children if c.type == "string_content"), False


def _extract_sinatra_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    """Sinatra routes: `get '/users/:id' do ... end`, `post("/x") { }` and `route :get, :post, '/x' do`, with the
    `namespace '/api' do ... end` blocks (sinatra-contrib) around them composed into the path."""
    entries: list[dict] = []
    for node in _walk_tree(root):
        if node.type != "call":
            continue
        name_node = node.child_by_field_name("method") or next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None or node.child_by_field_name("receiver") is not None:
            continue
        name = source[name_node.start_byte : name_node.end_byte].decode(errors="replace")
        args_node = node.child_by_field_name("arguments") or next((c for c in node.children if c.type == "argument_list"), None)
        if args_node is None or not any(c.type in ("do_block", "block") for c in node.children):
            continue
        args = args_node.named_children
        methods: list[str]
        if name in _SINATRA_VERBS:
            methods = [name.upper()]
        elif name == "route":
            methods = [source[a.start_byte + 1 : a.end_byte].decode(errors="replace").upper() for a in args if a.type == "simple_symbol"]
            if not methods:
                continue
        else:
            continue
        path_node = next((a for a in args if a.type == "string"), None)
        path, dynamic = _sinatra_string(path_node, source)
        if path is None and not dynamic:
            continue  # a regular expression or a constant: not a literal route
        prefix = ""
        ancestor = node.parent
        while ancestor is not None:
            if ancestor.type == "call":
                anc_name = ancestor.child_by_field_name("method") or next((c for c in ancestor.children if c.type == "identifier"), None)
                if anc_name is not None and source[anc_name.start_byte : anc_name.end_byte].decode(errors="replace") == "namespace":
                    anc_args = ancestor.child_by_field_name("arguments") or next((c for c in ancestor.children if c.type == "argument_list"), None)
                    anc_path, _ = _sinatra_string(next((a for a in (anc_args.named_children if anc_args else []) if a.type == "string"), None), source)
                    prefix = "/" + (anc_path or "").strip("/") + prefix if anc_path else prefix
            ancestor = ancestor.parent
        for method in methods:
            full = (prefix + "/" + (path or "").lstrip("/")).rstrip("/") if prefix else (path or "")
            entries.append(
                {
                    "method": method,
                    "path": full or "/",
                    "framework": "sinatra",
                    "file": rel_path,
                    "line": node.start_point[0] + 1,
                    "handler": "block",
                    "unresolved": dynamic,
                    "note": "path is built at run time" if dynamic else None,
                }
            )
    return entries


_GRAPE_NESTERS = {"namespace", "resource", "resources", "group", "segment", "route_param"}


def _grape_name(call: Node, source: bytes) -> str:
    name = call.child_by_field_name("method") or next((c for c in call.children if c.type == "identifier"), None)
    return source[name.start_byte : name.end_byte].decode(errors="replace") if name is not None else ""


def _grape_first_argument(call: Node, source: bytes) -> str | None:
    """First argument of a Grape DSL call as a path segment: `:users` / `'users'` / `"/users"` -> `users`."""
    args = call.child_by_field_name("arguments") or next((c for c in call.children if c.type == "argument_list"), None)
    first = args.named_children[0] if args is not None and args.named_children else None
    if first is None:
        return None
    if first.type == "simple_symbol":
        return source[first.start_byte + 1 : first.end_byte].decode(errors="replace")
    text, _dynamic = _sinatra_string(first, source)
    return text.strip("/") if text is not None else None


def _extract_grape_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    """Grape APIs: `get ':id' do ... end` (and post, put, delete, patch, head, options), inside the `resource :users`,
    `namespace :x`, `group`, `segment` and `route_param :id` blocks around them, with a class-level `prefix :api` and
    `version 'v1', using: :path` in front. Mounting one API class in another is not followed."""
    entries: list[dict] = []
    class_prefix: dict[int, list[str]] = {}
    for call in _walk_tree(root):
        if call.type == "call" and call.child_by_field_name("receiver") is None and _grape_name(call, source) in ("prefix", "version"):
            segment = _grape_first_argument(call, source)
            using_path = "using: :path" in source[call.start_byte : call.end_byte].decode(errors="replace") or _grape_name(call, source) == "prefix"
            klass = call.parent
            while klass is not None and klass.type != "class":
                klass = klass.parent
            if segment and using_path and klass is not None:
                class_prefix.setdefault(klass.id, []).append(segment)
    for call in _walk_tree(root):
        if call.type != "call" or call.child_by_field_name("receiver") is not None:
            continue
        name = _grape_name(call, source)
        if name not in _SINATRA_VERBS or not any(c.type in ("do_block", "block") for c in call.children):
            continue
        segments: list[str] = []
        ancestor = call.parent
        klass_id = None
        while ancestor is not None:
            if ancestor.type == "call" and _grape_name(ancestor, source) in _GRAPE_NESTERS:
                part = _grape_first_argument(ancestor, source)
                if part:
                    segments.insert(0, (":" + part) if _grape_name(ancestor, source) == "route_param" else part)
            if ancestor.type == "class" and klass_id is None:
                klass_id = ancestor.id
            ancestor = ancestor.parent
        leaf = _grape_first_argument(call, source)
        parts = [*class_prefix.get(klass_id, []), *segments, *([leaf] if leaf else [])]
        entries.append(
            {
                "method": name.upper(),
                "path": "/" + "/".join(p.strip("/") for p in parts if p.strip("/")),
                "framework": "grape",
                "file": rel_path,
                "line": call.start_point[0] + 1,
                "handler": "block",
                "unresolved": False,
                "note": None,
            }
        )
    return entries


def _extract_axum_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    for n in _walk_tree(root):
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

    for n in _walk_tree(root):
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
    return entries


_JAXRS_VERBS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
_JAXRS_APPLICATION_PATH = re.compile(r'@ApplicationPath\(\s*(?:value\s*=\s*)?"([^"]*)"')


def _jaxrs_annotations(modifiers: Node | None, source: bytes) -> list[tuple[str, str | None]]:
    """(annotation name, first string literal in its arguments) of each annotation in a Java `modifiers` node."""
    found: list[tuple[str, str | None]] = []
    for ann in (c for c in (modifiers.children if modifiers is not None else []) if c.type in ("annotation", "marker_annotation")):
        name_node = ann.child_by_field_name("name")
        if name_node is None:
            continue
        args = ann.child_by_field_name("arguments")
        literal = next((c for c in (args.children if args is not None else []) if c.type == "string_literal"), None)
        if literal is None and args is not None:  # `@Path(value = "/x")`
            for pair in args.children:
                literal = next((c for c in pair.children if c.type == "string_literal"), None)
                if literal is not None:
                    break
        text = _java_string_literal_text(literal, source) if literal is not None else None
        found.append((source[name_node.start_byte : name_node.end_byte].decode().split(".")[-1], text))
    return found


def _extract_jaxrs_routes(
    root: Node, source: bytes, rel_path: str, application_path: str = "", ambiguous_application_path: bool = False
) -> list[dict]:
    """JAX-RS resources (javax.ws.rs / jakarta.ws.rs): a class `@Path` prefix, methods annotated `@GET` ... `@PATCH` with
    an optional `@Path`. The application-wide `@ApplicationPath`, declared on another class, is prepended when the
    repository has exactly one."""
    entries: list[dict] = []
    for cls in _walk_tree(root):
        if cls.type not in ("class_declaration", "interface_declaration"):
            continue
        class_path = next((text for name, text in _jaxrs_annotations(next((c for c in cls.children if c.type == "modifiers"), None), source) if name == "Path"), None)
        body = next((c for c in cls.children if c.type == "class_body" or c.type == "interface_body"), None)
        for method in (c for c in (body.children if body is not None else []) if c.type == "method_declaration"):
            annotations = _jaxrs_annotations(next((c for c in method.children if c.type == "modifiers"), None), source)
            verbs = [name for name, _t in annotations if name in _JAXRS_VERBS]
            if not verbs:
                continue
            method_path = next((text for name, text in annotations if name == "Path"), None)
            name_node = method.child_by_field_name("name")
            handler = source[name_node.start_byte : name_node.end_byte].decode() if name_node is not None else "unknown"
            parts = [p for p in (application_path, class_path, method_path) if p and p.strip("/")]
            path = "/" + "/".join(p.strip("/") for p in parts)
            for verb in verbs:
                entries.append(
                    {
                        "method": verb,
                        "path": path,
                        "framework": "jaxrs",
                        "file": rel_path,
                        "line": method.start_point[0] + 1,
                        "handler": handler,
                        "unresolved": False,
                        "note": (
                            "prefix from @ApplicationPath found elsewhere in the repository" if application_path
                            else "the repository declares several different @ApplicationPath values: none is applied"
                            if ambiguous_application_path else None
                        ),
                    }
                )
    return entries


_KOTLIN_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_KOTLIN_SPRING_CONTROLLERS = {"RestController", "Controller"}


_SPRING_PLACEHOLDER = re.compile(r"\$\{[^}:]*:([^}]*)\}")


def _spring_placeholder_defaults(path: str) -> tuple[str, bool]:
    """`${api.base-path:/api}/users` -> (`/api/users`, True): a Spring property placeholder with a default stands for that
    default unless configuration overrides it."""
    replaced = _SPRING_PLACEHOLDER.sub(lambda m: m.group(1), path)
    return re.sub(r"/{2,}", "/", replaced), replaced != path


def _kotlin_annotations(modifiers: Node | None, source: bytes) -> list[tuple[str, str, Node]]:
    """(annotation name, argument text, node) for every annotation in a Kotlin `modifiers` node."""
    found: list[tuple[str, str, Node]] = []
    for ann in (c for c in (modifiers.children if modifiers is not None else []) if c.type == "annotation"):
        invocation = next((c for c in ann.children if c.type == "constructor_invocation"), None)
        head = invocation if invocation is not None else next((c for c in ann.children if c.type == "user_type"), None)
        if head is None:
            continue
        name_node = head if head.type == "user_type" else next((c for c in head.children if c.type in ("user_type", "identifier")), head)
        name = source[name_node.start_byte : name_node.end_byte].decode(errors="replace").split(".")[-1].split("<")[0].strip()
        args = next((c for c in head.children if c.type == "value_arguments"), None)
        found.append((name, source[args.start_byte : args.end_byte].decode() if args is not None else "", ann))
    return found


def _kotlin_split_arguments(body: str) -> list[str]:
    """Top-level comma-separated arguments of an annotation (brackets, parentheses and strings kept whole)."""
    parts: list[str] = []
    depth = 0
    quote = ""
    start = 0
    for i, char in enumerate(body):
        if quote:
            if char == quote and body[i - 1] != "\\":
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return [p.strip() for p in parts if p.strip()]


def _kotlin_spring_paths(args_text: str) -> list[str] | None:
    """The literal paths of a mapping annotation: the first positional argument, or `value =` / `path =` (a string or a list
    of them). [""] when no path is given; None when one is given but is not a string literal (a constant). Other string
    attributes (`name = "x"`, `params = "a,b"`) are not paths."""
    body = args_text.strip()[1:-1] if args_text else ""
    chosen: str | None = None
    for index, argument in enumerate(_kotlin_split_arguments(body)):
        named = re.match(r"(\w+)\s*=\s*(.*)$", argument, re.S)
        if named and named.group(1) in ("value", "path"):
            chosen = named.group(2)
        elif not named and index == 0:
            chosen = argument
    if chosen is None:
        return [""]
    literals = [text.replace("\\$", "$") for text in _KOTLIN_STRING.findall(chosen)]
    return literals or None


def _extract_kotlin_spring_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    """Spring MVC controllers written in Kotlin: `@RestController` / `@Controller` classes with a class-level
    `@RequestMapping` prefix and `@GetMapping` ... `@PatchMapping` / `@RequestMapping(method = [..])` functions."""
    entries: list[dict] = []
    for cls in _walk_tree(root):
        if cls.type != "class_declaration":
            continue
        class_annotations = _kotlin_annotations(next((c for c in cls.children if c.type == "modifiers"), None), source)
        if not any(name in _KOTLIN_SPRING_CONTROLLERS for name, _a, _n in class_annotations):
            continue
        prefixes = [""]
        prefix_is_constant = False
        for name, args, _node in class_annotations:
            if name == "RequestMapping":
                found = _kotlin_spring_paths(args)
                prefix_is_constant = found is None
                prefixes = found or [""]
        body = next((c for c in cls.children if c.type == "class_body"), None)
        for fn in (c for c in (body.children if body is not None else []) if c.type == "function_declaration"):
            ident = next((c for c in fn.children if c.type == "identifier"), None)
            handler = source[ident.start_byte : ident.end_byte].decode() if ident is not None else "unknown"
            for name, args, node in _kotlin_annotations(next((c for c in fn.children if c.type == "modifiers"), None), source):
                if name in _SPRING_VERB_ANNOTATIONS:
                    method = _SPRING_VERB_ANNOTATIONS[name]
                elif name == "RequestMapping":
                    found = re.search(r"RequestMethod\.([A-Z]+)", args)
                    method = found.group(1) if found else "ANY"
                else:
                    continue
                paths = _kotlin_spring_paths(args)
                for prefix in prefixes:
                    for path in paths if paths is not None else [None]:
                        joined = (prefix.rstrip("/") + "/" + (path or "").lstrip("/")).rstrip("/") if (prefix or path) else ""
                        joined, defaulted = _spring_placeholder_defaults(joined)
                        note = None
                        if path is None:
                            note = "path is a constant, not a string literal"
                        elif prefix_is_constant:
                            note = "the controller's @RequestMapping path is a constant, not a string literal"
                        elif "${" in joined:
                            note = "path holds a property placeholder with no default"
                        elif defaulted:
                            note = "a property placeholder was replaced by its default value"
                        entries.append(
                            {
                                "method": method,
                                "path": joined or "/",
                                "framework": "spring_boot",
                                "file": rel_path,
                                "line": node.start_point[0] + 1,
                                "handler": handler,
                                "unresolved": path is None or prefix_is_constant or "${" in joined,
                                "note": note,
                            }
                        )
    return entries


_KTOR_VERB_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}

# Ktor route-definition calls (route(...), routing { }, verb calls) are the
# one Kotlin call_expression shape tests directly against real parsed
# output (see _ktor_call_shape) - Gradle dependency calls in
# vulnerabilities.py's _parse_gradle_kts_pins independently discovered the
# same real fact about this grammar: call_expression has no "function"/
# "arguments" field names at all, purely positional children. Confirmed
# again here rather than assumed carried over.
def _ktor_call_shape(node: Node, source: bytes) -> tuple[str, str | None, Node] | None:
    """Recognizes `name { ... }` and `name("path") { ... }` - the two real
    shapes every Ktor DSL call takes (routing { }, route("/x") { },
    get { }, get("/x") { }, authenticate { }, and any other receiver-
    lambda call, since Ktor's own routing DSL is just ordinary Kotlin
    trailing-lambda syntax, not special grammar). Returns
    (identifier_name, path_or_None, annotated_lambda_node), or None if
    node isn't a call_expression shaped this way at all (e.g. a plain
    function call with no trailing lambda, or an assignment).
    """
    if node.type != "call_expression":
        return None
    children = node.children
    if len(children) != 2 or children[1].type != "annotated_lambda":
        return None
    head, lambda_node = children
    if head.type == "identifier":
        return source[head.start_byte:head.end_byte].decode(errors="ignore"), None, lambda_node
    if head.type == "call_expression":
        inner = head.children
        if len(inner) == 2 and inner[0].type == "identifier" and inner[1].type == "value_arguments":
            name = source[inner[0].start_byte:inner[0].end_byte].decode(errors="ignore")
            path = None
            named = inner[1].named_children
            if named and named[0].type == "value_argument" and named[0].named_children:
                value = named[0].named_children[0]
                if value.type == "string_literal":
                    content = next((c for c in value.children if c.type == "string_content"), None)
                    if content is not None:
                        path = source[content.start_byte:content.end_byte].decode(errors="ignore")
            return name, path, lambda_node
    return None


def _ktor_join_path(prefix_stack: list[str], leaf: str | None) -> str:
    segments = [seg.strip("/") for seg in (*prefix_stack, leaf) if seg]
    return "/" + "/".join(segments) if segments else "/"


def _extract_ktor_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    """Ktor's routing DSL has no special grammar of its own - `routing { }`,
    `route("/x") { }`, and `get("/x") { }` are ordinary Kotlin calls with a
    trailing lambda (see _ktor_call_shape), which is what makes this
    different from every other framework already handled here: Spring's
    shape is annotations (a fixed, closed vocabulary), Axum's is chained
    combinators (a flat call chain) - Ktor's is arbitrary nesting depth of
    the *same* call-with-lambda shape, where only real domain knowledge
    (which identifiers are HTTP verbs vs. the path-prefixing `route` vs.
    an unrelated pass-through wrapper like `authenticate { }`) distinguishes
    a route definition from routing-adjacent scaffolding. Confirmed
    against real, hand-verified Ktor code (parsed directly with
    tree_sitter_kotlin, not assumed from documentation) - no real Ktor
    server code was found in this project's own verification repo
    (android/architecture-samples, a client app), so this specific
    extractor's correctness rests on that direct grammar inspection plus
    its own test fixtures, not a real third-party server repo.
    """
    entries: list[dict] = []

    # Iterative, not recursive - see graph.py's walk() for why (deeply-nested
    # real-world ASTs can exceed Python's recursion limit and crash the whole
    # scan). This walk prunes rather than descending unconditionally (a
    # matched call recurses only into its own lambda body, carrying a
    # threaded prefix_stack), so it can't reuse the plain _walk_tree()
    # generator the other extractors below were converted to - the stack
    # here carries (node, prefix_stack) pairs instead of bare nodes, and
    # reversed(node.children) before pushing preserves the same left-to-right
    # visiting order the original recursive calls produced.
    stack: list[tuple[Node, list[str]]] = [(root, [])]
    while stack:
        node, prefix_stack = stack.pop()
        shape = _ktor_call_shape(node, source)
        if shape is not None:
            name, path, lambda_node = shape
            lambda_literal = next((c for c in lambda_node.children if c.type == "lambda_literal"), None)
            if name in _KTOR_VERB_METHODS:
                entries.append(
                    {
                        "method": name.upper(),
                        "path": _ktor_join_path(prefix_stack, path),
                        "framework": "ktor",
                        "file": rel_path,
                        "line": node.start_point[0] + 1,
                        "handler": "<lambda>",
                        "unresolved": False,
                        "note": None,
                    }
                )
                if lambda_literal is not None:
                    stack.append((lambda_literal, prefix_stack))
                continue
            if name == "route":
                new_stack = [*prefix_stack, path] if path else prefix_stack
                if lambda_literal is not None:
                    stack.append((lambda_literal, new_stack))
                continue
            # Any other call-with-trailing-lambda (routing, authenticate,
            # install, intercept, static, ...) is real routing-adjacent
            # scaffolding, not a route itself and not a path prefix -
            # recurse into its body with the prefix stack unchanged rather
            # than either skipping it (would miss every route nested
            # inside authenticate { }, a real, common pattern) or treating
            # its own name as a path segment (wrong - authenticate isn't
            # part of the URL).
            if lambda_literal is not None:
                stack.append((lambda_literal, prefix_stack))
            continue
        stack.extend((child, prefix_stack) for child in reversed(node.children))

    return entries


def _extract_vapor_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    """Vapor's real routing shape (confirmed empirically against
    tree-sitter-swift, not assumed): `app.get("path") { req in ... }` or
    `app.get("path", use: handler)` - a call_expression whose callee is
    `<receiver>.<verb>` and whose call_suffix carries either a trailing
    lambda_literal or a `use:`-labeled argument. Matches on the verb/shape
    regardless of the receiver's own name (same as _extract_express_routes
    does for Express) so a route group's own variable (`app.grouped("api")`
    assigned to `api`, then `api.get(...)`) is still caught without needing
    to track that assignment.
    """
    entries: list[dict] = []

    def text(n: Node) -> str:
        return source[n.start_byte:n.end_byte].decode(errors="ignore")

    for n in _walk_tree(root):
        if n.type == "call_expression":
            nav = next((c for c in n.children if c.type == "navigation_expression"), None)
            suffix_node = next((c for c in n.children if c.type == "call_suffix"), None)
            if nav is not None and suffix_node is not None:
                nav_suffix = next((c for c in nav.children if c.type == "navigation_suffix"), None)
                method_id = next(
                    (c for c in nav_suffix.children if c.type == "simple_identifier"), None
                ) if nav_suffix is not None else None
                method_name = text(method_id) if method_id is not None else None

                if method_name in _VAPOR_VERB_METHODS:
                    args = next((c for c in suffix_node.children if c.type == "value_arguments"), None)
                    trailing_closure = next(
                        (c for c in suffix_node.children if c.type == "lambda_literal"), None
                    )
                    path_segments: list[str] = []
                    handler_label = None
                    if args is not None:
                        for arg in args.children:
                            if arg.type != "value_argument":
                                continue
                            label_node = next(
                                (c for c in arg.children if c.type == "value_argument_label"), None
                            )
                            if label_node is not None:
                                label_id = next(
                                    (c for c in label_node.children if c.type == "simple_identifier"), None
                                )
                                if label_id is not None and text(label_id) == "use" and arg.children:
                                    handler_label = text(arg.children[-1])
                                continue
                            str_lit = next(
                                (c for c in arg.children if c.type == "line_string_literal"), None
                            )
                            if str_lit is not None:
                                content = next(
                                    (c for c in str_lit.children if c.type == "line_str_text"), None
                                )
                                if content is not None:
                                    path_segments.append(text(content))

                    # A real Vapor route registration always either takes a
                    # trailing closure or a `use:`-labeled handler reference -
                    # required as a false-positive guard, since ".get"/".post"
                    # are extremely common method names on unrelated types
                    # (dictionaries, key-value stores) that also happen to take
                    # a string-literal first argument. Express's own path-
                    # must-start-with-"/" guard doesn't work here: unlike
                    # Express, real Vapor path segments never carry a leading
                    # "/" in source, so that signal isn't available.
                    #
                    # Real bug found via audit: `path_segments` was ALSO
                    # required to be non-empty, which drops a route
                    # registered with no additional path segment at all -
                    # `users.get(use: index)` for the idiomatic REST
                    # "index" action living at a group's own base path
                    # (`GET /users`, contrasted with `users.get(":id", use:
                    # show)` for `GET /users/:id`), or even the simplest
                    # possible case with no grouping at all,
                    # `app.get(use: index)` (the literal application
                    # root). Confirmed directly: both produced zero
                    # entries, not just an imprecise path - the whole
                    # route vanished, contradicting this function's own
                    # "still caught" claim above. An empty path_segments
                    # list is a valid state (root path relative to
                    # whatever prefix applies), not "no route here" -
                    # "/" + "/".join([]) already correctly produces "/"
                    # on its own, so only the guard was wrong.
                    if trailing_closure is not None or handler_label is not None:
                        entries.append(
                            {
                                "method": method_name.upper(),
                                # Normalized to a leading "/" for consistency
                                # with every other framework's stored paths -
                                # Vapor's own source never writes one.
                                "path": "/" + "/".join(path_segments),
                                "framework": "vapor",
                                "file": rel_path,
                                "line": n.start_point[0] + 1,
                                "handler": handler_label or "<inline handler>",
                                "unresolved": False,
                                "note": None,
                            }
                        )
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
            value = arg.child_by_field_name("value")
            if key is None:
                continue
            if key.type == "string":
                # The hash-rocket route form ("/404-body" => "exceptions#not_found_body")
                # - a single `pair` argument whose key IS the path, not a
                # `to:`/`hash_key_symbol` keyword arg. Confirmed on a real
                # repo (Discourse's own config/routes.rb): this form
                # outnumbers `to:` 819 to 15, so every hash-rocket route
                # was previously invisible to this extractor entirely -
                # neither branch matched a bare string-keyed pair, since
                # the top-level `arg.type == "string"` check only sees a
                # standalone string argument, not one bundled inside a pair.
                if path is None:
                    path = _ruby_string_content(key, source)
                if value is not None and value.type == "string":
                    to_value = _ruby_string_content(value, source)
            elif source[key.start_byte : key.end_byte].decode() == "to":
                if value is not None and value.type == "string":
                    to_value = _ruby_string_content(value, source)
    return path, to_value


def _rails_enclosing_module_prefix(call_node: Node, source: bytes) -> list[str]:
    """Every `namespace :x do ... end` / `scope module: "x" do ... end`
    block enclosing call_node, outermost first - Rails' *module* (not URL-
    path) namespacing, which is what determines a route's real controller
    file. `namespace :x` affects both the controller module and the URL
    path; a bare `scope "/logs" do` or `scope path: "..." do` affects only
    the URL and must NOT contribute a module segment - confirmed on
    Discourse's own routes.rb, where bare/path-only `scope` outnumbers
    `namespace` 20 to 3, and none of those 20 renamespace their contents'
    controllers.

    Walks upward from call_node rather than a push/pop descent - each
    do_block's owning `call` is checked in turn, so arbitrary nesting depth
    (namespace inside scope inside namespace, ...) falls out for free
    without a separate stateful traversal."""
    segments: list[str] = []
    node = call_node.parent
    while node is not None:
        if node.type == "do_block":
            enclosing_call = node.parent
            if enclosing_call is not None and enclosing_call.type == "call":
                method_node = enclosing_call.child_by_field_name("method")
                block_args = enclosing_call.child_by_field_name("arguments")
                if method_node is not None and block_args is not None:
                    block_method = source[method_node.start_byte : method_node.end_byte].decode()
                    segment = None
                    if block_method == "namespace":
                        first = next(
                            (a for a in block_args.named_children if a.type == "simple_symbol"),
                            None,
                        )
                        if first is not None:
                            segment = source[first.start_byte : first.end_byte].decode().lstrip(":")
                    elif block_method == "scope":
                        for arg in block_args.named_children:
                            if arg.type != "pair":
                                continue
                            key = arg.child_by_field_name("key")
                            value = arg.child_by_field_name("value")
                            if not (
                                key is not None
                                and key.type == "hash_key_symbol"
                                and source[key.start_byte : key.end_byte].decode() == "module"
                                and value is not None
                            ):
                                continue
                            if value.type == "string":
                                segment = _ruby_string_content(value, source)
                            elif value.type == "simple_symbol":
                                # `scope module: :admin do` - equally valid
                                # Rails syntax alongside `module: "admin"`,
                                # confirmed by Flash Review on #666 and
                                # missed entirely before: only the string
                                # form was handled.
                                segment = (
                                    source[value.start_byte : value.end_byte].decode().lstrip(":")
                                )
                    if segment:
                        segments.append(segment)
        node = node.parent
    segments.reverse()
    return segments


def _apply_rails_module_prefix(to_value: str, module_prefix: list[str]) -> str:
    if not module_prefix or "#" not in to_value:
        return to_value
    controller_part, action = to_value.split("#", 1)
    return "/".join([*module_prefix, controller_part]) + "#" + action


def _rails_enclosing_url_prefix(call_node: Node, source: bytes) -> list[str]:
    """Every namespace/scope block enclosing call_node that affects the
    real URL path, outermost first - the sibling of
    _rails_enclosing_module_prefix, which deliberately tracks the opposite
    half (confirmed real bug: nothing composed this half into `path`,
    only the module half into `handler`, so a route nested in namespace/
    scope reported a confidently wrong, unprefixed path while claiming
    unresolved: False).

    `namespace :x do` affects both halves, using :x for the URL too unless
    overridden by its own `path:` option (`namespace :api, path: "v2" do`
    - real, documented Rails syntax: module stays Api::, URL uses "v2").
    A bare `scope "/logs" do` or `scope path: "..." do` affects only the
    URL. `scope module: "..." do` with no `path:` affects only the module
    and contributes nothing here - mirrors _rails_enclosing_module_prefix's
    own reasoning for why a bare scope must not contribute there."""
    segments: list[str] = []
    node = call_node.parent
    while node is not None:
        if node.type == "do_block":
            enclosing_call = node.parent
            if enclosing_call is not None and enclosing_call.type == "call":
                method_node = enclosing_call.child_by_field_name("method")
                block_args = enclosing_call.child_by_field_name("arguments")
                if method_node is not None and block_args is not None:
                    block_method = source[method_node.start_byte : method_node.end_byte].decode()
                    path_override = None
                    for arg in block_args.named_children:
                        if arg.type != "pair":
                            continue
                        key = arg.child_by_field_name("key")
                        value = arg.child_by_field_name("value")
                        if (
                            key is not None
                            and key.type == "hash_key_symbol"
                            and source[key.start_byte : key.end_byte].decode() == "path"
                            and value is not None
                            and value.type == "string"
                        ):
                            path_override = _ruby_string_content(value, source)
                    segment = None
                    if block_method == "namespace":
                        if path_override is not None:
                            segment = path_override
                        else:
                            first = next(
                                (a for a in block_args.named_children if a.type == "simple_symbol"),
                                None,
                            )
                            if first is not None:
                                segment = source[first.start_byte : first.end_byte].decode().lstrip(":")
                    elif block_method == "scope":
                        if path_override is not None:
                            segment = path_override
                        else:
                            first_string = next(
                                (a for a in block_args.named_children if a.type == "string"), None
                            )
                            if first_string is not None:
                                segment = _ruby_string_content(first_string, source)
                    if segment:
                        segments.append(segment)
        node = node.parent
    segments.reverse()
    return segments


def _apply_rails_url_prefix(path: str, url_prefix: list[str]) -> str:
    cleaned = [s.strip("/") for s in url_prefix if s.strip("/")]
    if not cleaned:
        return path
    tail = path.strip("/")
    return "/".join([*cleaned, tail]) if tail else "/".join(cleaned)


def _extract_rails_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    for n in _walk_tree(root):
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
                        module_prefix = _rails_enclosing_module_prefix(n, source)
                        url_prefix = _rails_enclosing_url_prefix(n, source)
                        entries.append(
                            {
                                "method": "GET" if method_name == "root" else method_name.upper(),
                                "path": _apply_rails_url_prefix(path, url_prefix),
                                "framework": "rails",
                                "file": rel_path,
                                "line": n.start_point[0] + 1,
                                "handler": _apply_rails_module_prefix(to_value, module_prefix),
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
                        # `resources :keys, controller: "api"` - a real,
                        # live line in Discourse's own config/routes.rb
                        # (config/routes.rb:356) - routes the "keys"
                        # resource to ApiController, not KeysController.
                        # Without checking for this override, the resource
                        # name alone silently names the wrong controller.
                        for arg in named:
                            if arg.type != "pair":
                                continue
                            key = arg.child_by_field_name("key")
                            value = arg.child_by_field_name("value")
                            if (
                                key is not None
                                and key.type == "hash_key_symbol"
                                and source[key.start_byte : key.end_byte].decode() == "controller"
                                and value is not None
                                and value.type == "string"
                            ):
                                resource_name = _ruby_string_content(value, source)
                                break
                        module_prefix = _rails_enclosing_module_prefix(n, source)
                        if module_prefix:
                            resource_name = "/".join([*module_prefix, resource_name])
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
    if node.type == "string":
        # The legacy "'UserController@index'" form - still valid Laravel
        # syntax alongside the newer [Controller::class, 'method'] array
        # below, and the only one of the two with no accompanying `use`
        # import anywhere in the file (the array form requires importing
        # the ::class reference), so it's the shape dead_code.py's Laravel
        # route resolver actually needs captured here.
        return _php_string_content(node, source)
    if node.type == "array_creation_expression":
        elements = [c for c in node.named_children if c.type == "array_element_initializer"]
        if elements:
            last_value = elements[-1].children[0] if elements[-1].children else None
            if last_value is not None and last_value.type == "string":
                return _php_string_content(last_value, source)
    if node.type == "anonymous_function":
        return "<inline handler>"
    return "unknown"


def _laravel_resource_controller_class(node: Node | None, source: bytes) -> str | None:
    """The qualified controller class name from a Route::resource()/
    apiResource() second argument - either `Admin\\UserController::class`
    (a class_constant_access_expression whose first child is a `name` for
    an unqualified reference or `qualified_name` for a namespaced one,
    text already backslash-joined either way) or the older bare-string
    form, `'UserController'`."""
    if node is None:
        return None
    if node.type == "class_constant_access_expression" and node.children:
        first = node.children[0]
        if first.type in ("name", "qualified_name"):
            return source[first.start_byte : first.end_byte].decode()
        return None
    if node.type == "string":
        return _php_string_content(node, source)
    return None


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


_SYMFONY_ROUTE = re.compile(
    r"#\[\s*(?:\\?Symfony\\Component\\Routing\\(?:Attribute|Annotation)\\)?Route\s*\(((?:[^()]|\([^()]*\))*)\)\s*\]"
    r"\s*(?:#\[[^\]]*\]\s*)*(?:(?:public|private|protected|final|abstract|static|readonly)\s+)*(class|function)\s+(\w+)",
    re.S,
)
_SLIM_ROUTE = re.compile(
    r"\$\w+\s*->\s*(get|post|put|delete|patch|options|any)\s*\(\s*(['\"])([^'\"]*)\2"
    r"(?:\s*,\s*(['\"][\w\\\\:]+['\"]|[\w\\\\]+::class|\[[^\]]*\]|(?:static\s+)?(?:function|fn)\b))?"
)
_SLIM_GROUP = re.compile(r"->\s*group\s*\(\s*(['\"])([^'\"]*)\1\s*,\s*(?:static\s+)?(?:function|fn)[^{]*\{")
_PHP_STRING = re.compile(r"""['"]([^'"]*)['"]""")


def _php_brace_end(text: str, open_index: int) -> int:
    """Index of the `}` closing the `{` at `open_index`; braces inside strings and comments are not counted."""
    depth = 0
    i = open_index
    while i < len(text):
        char = text[i]
        if char in "'\"":
            i += 1
            while i < len(text) and text[i] != char:
                i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i) or char == "#" and not text.startswith("#[", i):
            i = text.find("\n", i) if text.find("\n", i) >= 0 else len(text)
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = end + 1 if end >= 0 else len(text)
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def _php_split_arguments(body: str) -> list[str]:
    """Top-level comma-separated arguments of an attribute (brackets, parentheses and strings kept whole)."""
    parts: list[str] = []
    depth = 0
    quote = ""
    start = 0
    for i, char in enumerate(body):
        if quote:
            if char == quote and body[i - 1] != "\\":
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return [p.strip() for p in parts if p.strip()]


def _extract_symfony_routes(source: bytes, rel_path: str) -> list[dict]:
    """Symfony attribute routes: `#[Route('/api')]` on a class prefixes the `#[Route('/x', methods: ['GET', 'POST'])]`
    on its methods (any HTTP method when `methods` is absent). Routes configured in YAML or by docblock annotations
    are not read."""
    text = source.decode(errors="replace")
    entries: list[dict] = []
    class_prefixes: dict[str, str] = {}
    class_starts = [(m.start(), m.group(1)) for m in re.finditer(r"\bclass\s+(\w+)", text)]
    for match in _SYMFONY_ROUTE.finditer(text):
        args, kind, name = match.group(1), match.group(2), match.group(3)
        path = ""
        for index, argument in enumerate(_php_split_arguments(args)):
            named = re.match(r"(\w+)\s*:\s*(.*)$", argument, re.S)
            literal = _PHP_STRING.fullmatch((named.group(2) if named else argument).strip())
            if literal and ((named and named.group(1) == "path") or (not named and index == 0)):
                path = literal.group(1)
        if kind == "class":
            class_prefixes[name] = path
            continue
        enclosing = next((cname for start, cname in reversed(class_starts) if start < match.start()), None)
        class_prefix = class_prefixes.get(enclosing or "", "")
        methods_match = re.search(r"\bmethods\s*:\s*\[([^\]]*)\]", args)
        methods = (
            [m.upper() for m in _PHP_STRING.findall(methods_match.group(1))]
            + re.findall(r"\bMETHOD_([A-Z]+)\b", methods_match.group(1))  # Request::METHOD_GET
            if methods_match else ["ANY"]
        )
        full = (class_prefix.rstrip("/") + "/" + path.lstrip("/")).rstrip("/") if class_prefix else path
        for method in methods:
            entries.append(
                {
                    "method": method,
                    "path": full or "/",
                    "framework": "symfony",
                    "file": rel_path,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "handler": name,
                    "unresolved": False,
                    "note": None,
                }
            )
    return entries


def _extract_slim_routes(source: bytes, rel_path: str) -> list[dict]:
    """Slim routes: `$app->get('/x', $handler)` (also post, put, delete, patch, options, any), inside any number of
    `$app->group('/api', function (...) { ... })` closures whose prefixes are composed."""
    text = source.decode(errors="replace")
    groups = [(m.end() - 1, _php_brace_end(text, m.end() - 1), m.group(2)) for m in _SLIM_GROUP.finditer(text)]
    entries: list[dict] = []
    for match in _SLIM_ROUTE.finditer(text):
        prefix = "".join("/" + g[2].strip("/") for g in sorted(groups) if g[0] < match.start() < g[1] and g[2].strip("/"))
        path = match.group(3)
        if path and not path.startswith(("/", "[")):
            continue  # `$settings->get('displayErrorDetails')`: a container or config lookup, not a route
        full = (prefix + "/" + path.lstrip("/")).rstrip("/") if prefix else path
        entries.append(
            {
                "method": "ANY" if match.group(1) == "any" else match.group(1).upper(),
                "path": full or "/",
                "framework": "slim",
                "file": rel_path,
                "line": text.count("\n", 0, match.start()) + 1,
                "handler": _slim_handler_label(match.group(4)),
                "unresolved": False,
                "note": None,
            }
        )
    return entries


def _slim_handler_label(raw: str | None) -> str:
    if not raw:
        return "unknown"
    if raw.startswith(("function", "fn", "static")):
        return "closure"
    return raw.strip("'\"")


def _extract_laravel_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    for n in _walk_tree(root):
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
                    elif (
                        method_name in _LARAVEL_RESOURCE_METHODS
                        and len(arg_values) >= 2
                        and arg_values[0] is not None
                        and arg_values[0].type == "string"
                    ):
                        path = _php_string_content(arg_values[0], source)
                        controller_class = _laravel_resource_controller_class(
                            arg_values[1], source
                        )
                        if controller_class is not None:
                            entries.append(
                                {
                                    "method": None,
                                    "path": path,
                                    "framework": "laravel",
                                    "file": rel_path,
                                    "line": line,
                                    "handler": controller_class,
                                    "unresolved": True,
                                    "note": note,
                                }
                            )
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

    for n in _walk_tree(root):
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

    for n in _walk_tree(root):
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
        ("kt", KOTLIN_LANGUAGE),
        ("swift", SWIFT_LANGUAGE),
    ):
        parser = Parser()
        parser.language = lang
        parsers[name] = parser

    python_source_roots = _python_source_roots(repo_path)
    # Walked once, not once per pass below - the FastAPI router-prefix
    # prescan and the main extraction loop each used to call
    # _iter_source_files independently, two full os.walk + ignore-pattern
    # + sort passes over the same tree per scan (same fix as
    # build_module_graph's own four-call version of this).
    source_paths = list(_iter_source_files(repo_path, ignored_paths))
    cross_file_router_mounts: dict[tuple[str, str], list[str]] = {}
    for path in source_paths:
        if path.suffix != ".py":
            continue
        source = read_bytes_or_none(path)
        if source is None:
            continue
        tree = parsers["py"].parse(source)
        collected = _collect_fastapi_include_prefixes(
            tree.root_node, source, repo_path, path, python_source_roots
        )
        for key, prefixes in collected.items():
            cross_file_router_mounts.setdefault(key, []).extend(prefixes)

    # A router-defining file's composed endpoint paths depend on every
    # include_router(..., prefix=...) call that targets it, which can live
    # in a different file entirely - this loop is unconditional above (no
    # unchanged_endpoints check), so cross_file_router_mounts is always
    # fresh, but the cache-reuse skip below used to trust a defining file's
    # own hash/diff as if that were the whole story. Confirmed live: bumping
    # app.py's include_router prefix from /api/v1 to /api/v2 while
    # routers/users.py stayed byte-identical left the cached, now-stale
    # /api/v1/users path in place, since routers/users.py alone "looked"
    # unchanged. Excluding every defining file from cache-reuse trades a
    # little incremental-scan speed for those specific files for
    # correctness that doesn't depend on tracking which mounting file
    # changed - see docs/audits/Claude_Audit.md finding 20.
    cross_file_mount_defining_files = {defining_file for defining_file, _router in cross_file_router_mounts}

    # JAX-RS: `@ApplicationPath("/api")` sits on an Application class, apart from the resources it prefixes, so resource
    # files are read afresh each scan, never reused from the cache.
    jaxrs_application_paths: set[str] = set()
    for path in source_paths:
        if path.suffix != ".java":
            continue
        jaxrs_source = read_bytes_or_none(path)
        if jaxrs_source is None or b"ws.rs" not in jaxrs_source:
            continue
        cross_file_mount_defining_files.add(_rel(repo_path, path))
        uncommented = re.sub(r"/\*.*?\*/|//[^\n]*", " ", jaxrs_source.decode(errors="replace"), flags=re.S)
        jaxrs_application_paths.update(_JAXRS_APPLICATION_PATH.findall(uncommented))
    jaxrs_application_path = next(iter(jaxrs_application_paths)) if len(jaxrs_application_paths) == 1 else ""
    jaxrs_ambiguous_application_path = len(jaxrs_application_paths) > 1

    for path in source_paths:
        rel_path = _rel(repo_path, path)

        if (
            unchanged_endpoints is not None
            and rel_path in unchanged_endpoints
            and rel_path not in cross_file_mount_defining_files
        ):
            endpoints.extend(unchanged_endpoints[rel_path])
            continue

        suffix = path.suffix

        if suffix == ".py":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["py"].parse(source)
            endpoints.extend(
                _extract_flask_fastapi_routes(
                    tree.root_node, source, rel_path, cross_file_router_mounts
                )
            )
            if path.name == "urls.py":
                endpoints.extend(_extract_django_routes(tree.root_node, source, rel_path))
            if b"starlette" in source:
                endpoints.extend(_extract_starlette_aiohttp_routes(tree.root_node, source, rel_path, "starlette"))
            if b"aiohttp" in source:
                endpoints.extend(_extract_starlette_aiohttp_routes(tree.root_node, source, rel_path, "aiohttp"))
        elif suffix in (".js", ".jsx"):
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["js"].parse(source)
            endpoints.extend(_extract_express_routes(tree.root_node, source, rel_path))
            endpoints.extend(_extract_nextjs_routes(rel_path, source, repo_path))
            endpoints.extend(_extract_js_route_objects(tree.root_node, source, rel_path))
        elif suffix == ".ts":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["ts"].parse(source)
            endpoints.extend(_extract_express_routes(tree.root_node, source, rel_path))
            endpoints.extend(_extract_nextjs_routes(rel_path, source, repo_path))
            endpoints.extend(_extract_js_route_objects(tree.root_node, source, rel_path))
        elif suffix == ".tsx":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["tsx"].parse(source)
            endpoints.extend(_extract_express_routes(tree.root_node, source, rel_path))
            endpoints.extend(_extract_nextjs_routes(rel_path, source, repo_path))
            endpoints.extend(_extract_js_route_objects(tree.root_node, source, rel_path))
        elif suffix == ".go":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["go"].parse(source)
            go_frameworks = _go_web_frameworks(source)
            for go_framework in go_frameworks:
                endpoints.extend(_extract_go_framework_routes(tree.root_node, source, rel_path, go_framework))
            # net/http and Gin keep their own matchers: always when no framework above is imported, and otherwise only when
            # the file really uses them (package-level http.Handle*, or the gin import), so a chi file is not read twice
            if not go_frameworks or b"http.Handle" in source or b"http.NewServeMux" in source:
                endpoints.extend(_extract_go_net_http_routes(tree.root_node, source, rel_path))
            if not go_frameworks or b"gin-gonic/gin" in source:
                endpoints.extend(_extract_gin_routes(tree.root_node, source, rel_path))
        elif suffix == ".rs":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["rs"].parse(source)
            endpoints.extend(_extract_axum_routes(tree.root_node, source, rel_path))
        elif suffix == ".java":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["java"].parse(source)
            endpoints.extend(_extract_spring_boot_routes(tree.root_node, source, rel_path))
            if b"ws.rs" in source:
                endpoints.extend(
                    _extract_jaxrs_routes(tree.root_node, source, rel_path, jaxrs_application_path, jaxrs_ambiguous_application_path)
                )
        elif suffix == ".swift":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["swift"].parse(source)
            endpoints.extend(_extract_vapor_routes(tree.root_node, source, rel_path))
        elif suffix == ".rb" and path.name == "routes.rb":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["rb"].parse(source)
            endpoints.extend(_extract_rails_routes(tree.root_node, source, rel_path))
        elif suffix == ".rb":
            source = read_bytes_or_none(path)
            if source is None or (b"sinatra" not in source.lower() and b"Grape" not in source):
                continue
            tree = parsers["rb"].parse(source)
            if b"Grape" in source:
                endpoints.extend(_extract_grape_routes(tree.root_node, source, rel_path))
            if b"sinatra" in source.lower():
                endpoints.extend(_extract_sinatra_routes(tree.root_node, source, rel_path))
        elif suffix == ".php" and "routes" in Path(rel_path).parts:
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["php"].parse(source)
            endpoints.extend(_extract_laravel_routes(tree.root_node, source, rel_path))
        elif suffix == ".php":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            if b"Route(" in source and b"#[" in source:
                endpoints.extend(_extract_symfony_routes(source, rel_path))
            if b"Slim\\" in source or b"Slim/" in source:
                endpoints.extend(_extract_slim_routes(source, rel_path))
        elif suffix == ".cs":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["cs"].parse(source)
            endpoints.extend(_extract_aspnet_attribute_routes(tree.root_node, source, rel_path))
            endpoints.extend(_extract_aspnet_minimal_routes(tree.root_node, source, rel_path))
        elif suffix == ".kt":
            source = read_bytes_or_none(path)
            if source is None:
                continue
            tree = parsers["kt"].parse(source)
            endpoints.extend(_extract_ktor_routes(tree.root_node, source, rel_path))
            endpoints.extend(_extract_kotlin_spring_routes(tree.root_node, source, rel_path))

    return {"checked": True, "endpoints": endpoints}
