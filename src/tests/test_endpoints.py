from tree_sitter import Parser

from aletheore.endpoints import (
    _extract_aspnet_attribute_routes,
    _extract_aspnet_minimal_routes,
    _extract_axum_routes,
    _extract_django_routes,
    _extract_express_routes,
    _extract_flask_fastapi_routes,
    _extract_gin_routes,
    _extract_go_net_http_routes,
    _extract_ktor_routes,
    _extract_laravel_routes,
    _extract_rails_routes,
    _extract_spring_boot_routes,
    _extract_vapor_routes,
    map_api_endpoints,
)
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
)


def parse_python(source: str):
    parser = Parser()
    parser.language = PY_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def parse_js(source: str):
    parser = Parser()
    parser.language = JS_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def parse_swift(source: str):
    parser = Parser()
    parser.language = SWIFT_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def parse_go(source: str):
    parser = Parser()
    parser.language = GO_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def parse_rust(source: str):
    parser = Parser()
    parser.language = RUST_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def parse_java(source: str):
    parser = Parser()
    parser.language = JAVA_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def parse_kotlin(source: str):
    parser = Parser()
    parser.language = KOTLIN_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def parse_ruby(source: str):
    parser = Parser()
    parser.language = RUBY_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def parse_php(source: str):
    parser = Parser()
    parser.language = PHP_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def parse_csharp(source: str):
    parser = Parser()
    parser.language = CSHARP_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def test_extract_flask_route_decorator_with_methods():
    root, source = parse_python(
        '@app.route("/users/<int:id>", methods=["GET", "POST"])\n'
        "def get_user(id):\n"
        "    pass\n"
    )

    entries = _extract_flask_fastapi_routes(root, source, "app/routes.py")

    assert len(entries) == 2
    methods = {e["method"] for e in entries}
    assert methods == {"GET", "POST"}
    for entry in entries:
        assert entry["path"] == "/users/<int:id>"
        assert entry["framework"] == "flask"
        assert entry["file"] == "app/routes.py"
        assert entry["handler"] == "get_user"
        assert entry["unresolved"] is False


def test_extract_flask_route_defaults_to_get_when_no_methods_kwarg():
    root, source = parse_python('@app.route("/ping")\ndef ping():\n    pass\n')

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert len(entries) == 1
    assert entries[0]["method"] == "GET"


def test_extract_fastapi_verb_decorator_labeled_ambiguous():
    root, source = parse_python(
        '@router.get("/items/{item_id}")\ndef read_item(item_id):\n    pass\n'
    )

    entries = _extract_flask_fastapi_routes(root, source, "app/api.py")

    assert entries == [
        {
            "method": "GET",
            "path": "/items/{item_id}",
            "framework": "flask_or_fastapi",
            "file": "app/api.py",
            "line": 1,
            "handler": "read_item",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_fastapi_composes_router_and_include_prefixes():
    # include_router(...) prefixes are supplied via external_router_mount_prefixes
    # here, not written inline - _extract_flask_fastapi_routes no longer collects
    # them from its own source, since map_api_endpoints's cross-file pre-pass is
    # now the single source of truth for that (see test below for why: collecting
    # it both ways double-counted the prefix whenever include_router happened to
    # be in the same file as the router it mounts).
    root, source = parse_python(
        'router = APIRouter(prefix="/api/v1/users")\n'
        '@router.get("/{user_id}")\n'
        'def get_user(user_id: int):\n    pass\n'
    )

    entries = _extract_flask_fastapi_routes(
        root, source, "app/api.py", {("app/api.py", "router"): ["/internal"]}
    )

    assert entries[0]["method"] == "GET"
    assert entries[0]["path"] == "/internal/api/v1/users/{user_id}"
    assert entries[0]["unresolved"] is False


def test_extract_fastapi_module_level_prefix_not_shadowed_by_a_same_named_local():
    # Real bug (Claude_Audit.md finding 19): collect_static_prefixes walked
    # the entire file's AST with no scope tracking, keyed only by variable
    # name text - a local `router = APIRouter(...)` inside an unrelated
    # function (a common FastAPI factory-function shape reusing the
    # idiomatic "router" name) silently overwrote the module-level
    # router's real prefix. Reproduced exactly as documented: the
    # module-level router's real "/api" prefix must survive a later,
    # unrelated local "router" in a factory function.
    root, source = parse_python(
        'router = APIRouter(prefix="/api")\n'
        '\n'
        '@router.get("/x")\n'
        'def handler():\n'
        '    pass\n'
        '\n'
        'def make_test_router():\n'
        '    router = APIRouter(prefix="/testing")\n'
        '    return router\n'
    )

    entries = _extract_flask_fastapi_routes(root, source, "app/api.py")

    assert entries[0]["path"] == "/api/x"
    assert entries[0]["method"] == "GET"
    assert entries[0]["unresolved"] is False


def test_map_api_endpoints_composes_fastapi_prefix_from_another_file(tmp_path):
    (tmp_path / "users.py").write_text(
        'router = APIRouter(prefix="/users")\n'
        '@router.get("/{user_id}")\n'
        'def get_user(user_id: int):\n    pass\n'
    )
    (tmp_path / "main.py").write_text(
        "from users import router\n"
        'app.include_router(router, prefix="/api/v1")\n'
    )

    result = map_api_endpoints(tmp_path)

    route = next(endpoint for endpoint in result["endpoints"] if endpoint["file"] == "users.py")
    assert route["path"] == "/api/v1/users/{user_id}"


def test_map_api_endpoints_reparses_a_router_file_when_only_the_mounting_file_changed(tmp_path):
    # docs/audits/Claude_Audit.md finding 20, confirmed live before the fix:
    # cross_file_router_mounts is recomputed fresh every call (the pre-pass
    # loop above has no unchanged_endpoints check), but the per-file
    # cache-reuse skip below used to trust users.py's own hash/diff as the
    # whole story - so bumping main.py's include_router prefix while
    # users.py stayed byte-identical left the cached, now-stale /api/v1
    # path in place. users.py must be excluded from cache-reuse because its
    # composed path depends on a DIFFERENT file's include_router call, not
    # because its own content changed.
    (tmp_path / "users.py").write_text(
        'router = APIRouter()\n'
        '@router.get("/list")\n'
        'def list_users():\n    pass\n'
    )
    (tmp_path / "main.py").write_text(
        "from users import router\n"
        'app.include_router(router, prefix="/api/v1")\n'
    )

    first = map_api_endpoints(tmp_path)
    cached_users_endpoint = next(e for e in first["endpoints"] if e["file"] == "users.py")
    assert cached_users_endpoint["path"] == "/api/v1/list"

    # Only main.py changes (prefix bumped to /api/v2) - users.py is passed
    # as unchanged, exactly as evidence.py/scan_worker.jobs would build it
    # from a real hash/diff comparison.
    (tmp_path / "main.py").write_text(
        "from users import router\n"
        'app.include_router(router, prefix="/api/v2")\n'
    )

    second = map_api_endpoints(tmp_path, unchanged_endpoints={"users.py": [cached_users_endpoint]})
    users_endpoint = next(e for e in second["endpoints"] if e["file"] == "users.py")
    assert users_endpoint["path"] == "/api/v2/list"


def test_map_api_endpoints_does_not_double_count_a_same_file_include_router_prefix(tmp_path):
    (tmp_path / "api.py").write_text(
        'router = APIRouter(prefix="/api/v1/users")\n'
        '@router.get("/{user_id}")\n'
        'def get_user(user_id: int):\n    pass\n'
        'app.include_router(router, prefix="/internal")\n'
    )

    result = map_api_endpoints(tmp_path)

    route = next(endpoint for endpoint in result["endpoints"] if endpoint["file"] == "api.py")
    assert route["path"] == "/internal/api/v1/users/{user_id}"


def test_map_api_endpoints_fans_out_a_router_mounted_at_multiple_prefixes(tmp_path):
    (tmp_path / "users.py").write_text(
        'router = APIRouter()\n'
        '@router.get("/{user_id}")\n'
        'def get_user(user_id: int):\n    pass\n'
    )
    (tmp_path / "main.py").write_text(
        "from users import router\n"
        'app.include_router(router, prefix="/api")\n'
        'app.include_router(router, prefix="/admin")\n'
    )

    result = map_api_endpoints(tmp_path)

    routes = [e for e in result["endpoints"] if e["file"] == "users.py"]
    paths = {route["path"] for route in routes}
    assert paths == {"/api/{user_id}", "/admin/{user_id}"}


def test_map_api_endpoints_keeps_an_implicit_mount_alongside_a_prefixed_one(tmp_path):
    # Batch 5 finding 4: _collect_fastapi_include_prefixes only recorded a
    # mount when include_router(...) carried an explicit prefix= kwarg - a
    # prefix-less app.include_router(router) call (an ordinary FastAPI
    # pattern for mounting a router unprefixed alongside also mounting it
    # under a versioned/admin prefix) contributed nothing to the mounts
    # list, so when the *same* router also had one explicitly-prefixed
    # mount, the truthy mounts list from that other call suppressed the
    # fan-out branch that would have emitted the unprefixed path - silently
    # dropping a real, reachable endpoint from the map.
    (tmp_path / "users.py").write_text(
        'router = APIRouter()\n'
        '@router.get("/{user_id}")\n'
        'def get_user(user_id: int):\n    pass\n'
    )
    (tmp_path / "main.py").write_text(
        "from users import router\n"
        "app.include_router(router)\n"
        'app.include_router(router, prefix="/admin")\n'
    )

    result = map_api_endpoints(tmp_path)

    routes = [e for e in result["endpoints"] if e["file"] == "users.py"]
    paths = {route["path"] for route in routes}
    assert paths == {"/{user_id}", "/admin/{user_id}"}


def test_map_api_endpoints_does_not_cross_contaminate_same_named_routers_in_different_files(tmp_path):
    # Regression test: "router" is the idiomatic FastAPI variable name, so
    # two different files' routers, each imported into a different mounting
    # file under that same conventional bare name, used to be
    # indistinguishable to cross_file_router_mounts (keyed by bare
    # identifier text only) - every file's routes got every OTHER router's
    # mount prefixes too, in addition to its own. Modeled on the idiomatic
    # `from app.routers.users import router` (no alias) pattern - two
    # separate mounting files here, matching a real modular app that splits
    # router registration by domain.
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "routers").mkdir()
    (tmp_path / "app" / "routers" / "__init__.py").write_text("")
    (tmp_path / "app" / "routers" / "users.py").write_text(
        'router = APIRouter()\n'
        '@router.get("/list")\n'
        'def list_users():\n    pass\n'
    )
    (tmp_path / "app" / "routers" / "items.py").write_text(
        'router = APIRouter()\n'
        '@router.get("/list")\n'
        'def list_items():\n    pass\n'
    )
    (tmp_path / "app" / "main.py").write_text(
        "from app.routers.users import router\n"
        'app.include_router(router, prefix="/users")\n'
    )
    (tmp_path / "app" / "admin_setup.py").write_text(
        "from app.routers.items import router\n"
        'sub_app.include_router(router, prefix="/items")\n'
    )

    result = map_api_endpoints(tmp_path)

    users_paths = {e["path"] for e in result["endpoints"] if e["file"] == "app/routers/users.py"}
    items_paths = {e["path"] for e in result["endpoints"] if e["file"] == "app/routers/items.py"}
    assert users_paths == {"/users/list"}
    assert items_paths == {"/items/list"}


def test_map_api_endpoints_applies_mount_prefix_for_attribute_style_include_router(tmp_path):
    # Regression: include_router(users.router, prefix="/users") - routers
    # namespaced by module attribute access instead of a bare imported
    # name, the idiomatic way to avoid exactly the bare-"router"-name
    # collision the test above guards against - was invisible to this scan
    # entirely. positional[0] was required to be a plain identifier, so an
    # attribute node (`users.router`) never matched and the whole
    # include_router call was silently skipped: the mount prefix never
    # applied, and a real, reachable endpoint reported its path without it
    # ("/list" instead of "/users/list"). Confirmed directly before fixing.
    (tmp_path / "routers").mkdir()
    (tmp_path / "routers" / "__init__.py").write_text("")
    (tmp_path / "routers" / "users.py").write_text(
        'router = APIRouter()\n'
        '@router.get("/list")\n'
        'def list_users():\n    pass\n'
    )
    (tmp_path / "main.py").write_text(
        "from routers import users\n"
        'app.include_router(users.router, prefix="/users")\n'
    )

    result = map_api_endpoints(tmp_path)

    paths = {e["path"] for e in result["endpoints"] if e["file"] == "routers/users.py"}
    assert paths == {"/users/list"}


def test_map_api_endpoints_skips_attribute_style_include_router_when_module_unresolved(tmp_path):
    # The module-alias analog of the non-literal-prefix case: if the object
    # in `module.router` can't be traced back to a real import, this mount
    # is genuinely unknown - skip only this mount rather than guessing, and
    # never fall back to "this file", unlike the bare-identifier case where
    # a same-file local definition is a real, common possibility.
    (tmp_path / "users.py").write_text(
        'router = APIRouter()\n'
        '@router.get("/list")\n'
        'def list_users():\n    pass\n'
    )
    (tmp_path / "main.py").write_text(
        'app.include_router(some_dynamically_built_module.router, prefix="/users")\n'
    )

    result = map_api_endpoints(tmp_path)

    paths = {e["path"] for e in result["endpoints"] if e["file"] == "users.py"}
    assert paths == {"/list"}


def test_extract_flask_fastapi_ignores_non_route_decorators():
    root, source = parse_python("@staticmethod\ndef helper():\n    pass\n")

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert entries == []


def test_extract_flask_fastapi_handles_multiple_decorators_on_one_function():
    root, source = parse_python(
        '@app.get("/a")\n@some_other_decorator\ndef handler():\n    pass\n'
    )

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert len(entries) == 1
    assert entries[0]["path"] == "/a"


def test_extract_flask_fastapi_deeply_nested_handler_body_does_not_crash():
    # graph.py's own AST walk used to be recursive and blew past Python's
    # default recursion limit on real, deeply-nested source (confirmed on the
    # Linux kernel) - fixed in that file, but endpoints.py's extractors kept
    # their own, separate recursive walk() closures over the same trees and
    # were never converted, so the identical crash was still reachable via
    # endpoint mapping. 3000 nesting levels comfortably exceeds the default
    # limit (1000) while staying trivially parseable.
    depth = 3000
    nested_expr = "1" + "".join(f"+({i}" for i in range(depth)) + ")" * depth
    root, source = parse_python(
        f'@app.get("/deep")\ndef handler():\n    x = {nested_expr}\n    return x\n'
    )

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert len(entries) == 1
    assert entries[0]["path"] == "/deep"


def test_extract_flask_fastapi_deeply_nested_module_level_statement_does_not_crash():
    # Same bug class as the test above, but targeting collect_static_prefixes
    # specifically: it prunes function/class bodies rather than descending
    # unconditionally, so it can't reuse the shared _walk_tree() generator and
    # needed its own, separately-converted iterative stack. Nesting has to
    # stay at module level (outside any function) for that pruning to matter -
    # depth kept lower than the 3000 used elsewhere since real module-level
    # nesting (if/try chains) is inherently shallower than expression nesting,
    # and this still comfortably exceeds the recursion limit.
    depth = 1500
    # Build real nested if-blocks (each level indented one more) rather than
    # a single flat block, so the AST is actually depth-many levels deep.
    lines = []
    for i in range(depth):
        lines.append("    " * i + f"if True:  # {i}")
    lines.append("    " * depth + "pass")
    nested_module_body = "\n".join(lines)
    source_text = (
        f'router = APIRouter(prefix="/api")\n'
        f"{nested_module_body}\n"
        '@router.get("/deep")\n'
        "def handler():\n    pass\n"
    )
    root, source = parse_python(source_text)

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert len(entries) == 1
    assert entries[0]["path"] == "/api/deep"


def test_extract_django_path_call():
    root, source = parse_python(
        "urlpatterns = [\n"
        "    path('users/<int:id>/', views.get_user, name='get_user'),\n"
        "]\n"
    )

    entries = _extract_django_routes(root, source, "app/urls.py")

    assert entries == [
        {
            "method": "ANY",
            "path": "users/<int:id>/",
            "framework": "django",
            "file": "app/urls.py",
            "line": 2,
            "handler": "views.get_user",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_django_augmented_assignment_urlpatterns_is_not_skipped():
    # audit finding 27: "urlpatterns += [...]" - splitting the list across
    # an initial assignment plus one or more extensions is an ordinary,
    # documented Django organizing pattern. It parses to
    # augmented_assignment, a distinct node type from plain assignment -
    # only the initial "urlpatterns = [...]" used to be matched, so every
    # route declared via "+=" was silently missing from the endpoint
    # inventory with no indication anything was skipped.
    root, source = parse_python(
        "urlpatterns = [\n"
        "    path('home/', views.home),\n"
        "]\n"
        "urlpatterns += [\n"
        "    path('api/', views.api),\n"
        "]\n"
    )

    entries = _extract_django_routes(root, source, "app/urls.py")

    paths = {entry["path"] for entry in entries}
    assert paths == {"home/", "api/"}


def test_extract_django_re_path_call():
    root, source = parse_python("urlpatterns = [re_path(r'^items/$', views.list_items)]\n")

    entries = _extract_django_routes(root, source, "app/urls.py")

    assert len(entries) == 1
    assert entries[0]["path"] == "^items/$"
    assert entries[0]["handler"] == "views.list_items"


def test_extract_django_include_is_recorded_as_unresolved():
    root, source = parse_python('urlpatterns = [include("myapp.urls")]\n')

    entries = _extract_django_routes(root, source, "project/urls.py")

    assert entries == [
        {
            "method": None,
            "path": "myapp.urls",
            "framework": "django",
            "file": "project/urls.py",
            "line": 1,
            "handler": "include(...)",
            "unresolved": True,
            "note": None,
        }
    ]


def test_extract_django_ignores_non_urlpatterns_assignments():
    root, source = parse_python("app_name = 'myapp'\n")

    entries = _extract_django_routes(root, source, "app/urls.py")

    assert entries == []


def test_extract_express_get_route_with_named_handler():
    root, source = parse_js('app.get("/users", listUsers);\n')

    entries = _extract_express_routes(root, source, "server.js")

    assert entries == [
        {
            "method": "GET",
            "path": "/users",
            "framework": "express",
            "file": "server.js",
            "line": 1,
            "handler": "listUsers",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_express_route_with_inline_arrow_handler():
    root, source = parse_js('app.post("/users", (req, res) => { res.send("ok"); });\n')

    entries = _extract_express_routes(root, source, "server.js")

    assert len(entries) == 1
    assert entries[0]["method"] == "POST"
    assert entries[0]["handler"] == "<inline handler>"


def test_extract_express_router_all_maps_to_any():
    root, source = parse_js("router.all('/health', handler);\n")

    entries = _extract_express_routes(root, source, "routes.js")

    assert entries[0]["method"] == "ANY"


def test_extract_express_mounted_router_is_recorded_as_unresolved():
    root, source = parse_js("app.use('/api', apiRouter);\n")

    entries = _extract_express_routes(root, source, "server.js")

    assert entries == [
        {
            "method": None,
            "path": "/api",
            "framework": "express",
            "file": "server.js",
            "line": 1,
            "handler": "app.use(...)",
            "unresolved": True,
            "note": None,
        }
    ]


def test_extract_express_ignores_unrelated_method_calls():
    root, source = parse_js('res.send("ok");\napp.listen(3000);\n')

    entries = _extract_express_routes(root, source, "server.js")

    assert entries == []


def test_extract_express_ignores_non_path_get_calls():
    # Regression test: a bare .get("key")/.set("key") on a Map-like object
    # (e.g. an animation library's internal state, or any generic getter)
    # must not be misidentified as an Express route just because the method
    # name matches and the first argument is a string literal. Real
    # production false positive: a vendored Motion library's
    # e.get("stroke-dasharray") / e.get("transformOrigin") state getters.
    root, source = parse_js(
        'e.get("stroke-dasharray");\n'
        'e.get("transformOrigin");\n'
        'e.get("transform");\n'
    )

    entries = _extract_express_routes(root, source, "vendor/motion.js")

    assert entries == []


def test_extract_express_accepts_wildcard_path():
    root, source = parse_js('app.get("*", catchAll);\n')

    entries = _extract_express_routes(root, source, "server.js")

    assert entries[0]["path"] == "*"


def test_map_api_endpoints_combines_all_frameworks(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "routes.py").write_text(
        '@app.route("/users")\ndef list_users():\n    pass\n'
    )
    (tmp_path / "app" / "urls.py").write_text(
        "urlpatterns = [path('items/', views.list_items)]\n"
    )
    (tmp_path / "server.js").write_text('app.get("/health", healthCheck);\n')

    result = map_api_endpoints(tmp_path)

    assert result["checked"] is True
    paths = {e["path"] for e in result["endpoints"]}
    assert paths == {"/users", "items/", "/health"}


def test_map_api_endpoints_only_treats_urls_py_as_django_routes(tmp_path):
    (tmp_path / "not_urls.py").write_text(
        "urlpatterns = [path('items/', views.list_items)]\n"
    )

    result = map_api_endpoints(tmp_path)

    assert result["endpoints"] == []


def test_map_api_endpoints_reuses_unchanged_endpoints_instead_of_reparsing(tmp_path, monkeypatch):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "routes.py").write_text(
        '@app.route("/users")\ndef list_users():\n    pass\n'
    )
    (tmp_path / "server.js").write_text('app.get("/health", healthCheck);\n')

    from aletheore import endpoints as endpoints_module

    def _failing_flask_extractor(*a, **k):
        raise AssertionError("app/routes.py should not be re-parsed - it's in unchanged_endpoints")

    monkeypatch.setattr(endpoints_module, "_extract_flask_fastapi_routes", _failing_flask_extractor)

    cached = [{"method": "GET", "path": "/users", "file": "app/routes.py", "line": 1, "handler": "list_users"}]
    result = map_api_endpoints(tmp_path, unchanged_endpoints={"app/routes.py": cached})

    assert result["checked"] is True
    paths = {e["path"] for e in result["endpoints"]}
    assert paths == {"/users", "/health"}


def test_map_api_endpoints_without_unchanged_endpoints_is_unchanged(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "routes.py").write_text(
        '@app.route("/users")\ndef list_users():\n    pass\n'
    )

    with_none = map_api_endpoints(tmp_path, unchanged_endpoints=None)
    without_param = map_api_endpoints(tmp_path)

    assert with_none == without_param


def test_map_api_endpoints_empty_repo_returns_checked_true_empty_list(tmp_path):
    (tmp_path / "README.md").write_text("hello\n")

    result = map_api_endpoints(tmp_path)

    assert result == {"checked": True, "endpoints": []}


def test_extract_go_stdlib_handlefunc():
    root, source = parse_go(
        'package main\nfunc main() {\n\thttp.HandleFunc("/health", healthHandler)\n}\n'
    )

    entries = _extract_go_net_http_routes(root, source, "main.go")

    assert entries == [
        {
            "method": "ANY",
            "path": "/health",
            "framework": "go_net_http",
            "file": "main.go",
            "line": 3,
            "handler": "healthHandler",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_go_stdlib_handlefunc_go122_combined_pattern():
    root, source = parse_go(
        'package main\nfunc main() {\n\thttp.HandleFunc("GET /users/{id}", getUser)\n}\n'
    )

    entries = _extract_go_net_http_routes(root, source, "main.go")

    assert entries[0]["method"] == "GET"
    assert entries[0]["path"] == "/users/{id}"


def test_extract_gorilla_mux_handlefunc_with_chained_methods():
    root, source = parse_go(
        'package main\nfunc main() {\n\tr.HandleFunc("/items", updateItem).Methods("GET", "POST")\n}\n'
    )

    entries = _extract_go_net_http_routes(root, source, "main.go")

    assert len(entries) == 2
    methods = {e["method"] for e in entries}
    assert methods == {"GET", "POST"}
    for e in entries:
        assert e["framework"] == "gorilla_mux"
        assert e["path"] == "/items"
        assert e["handler"] == "updateItem"


def test_extract_gorilla_mux_subrouter_is_unresolved():
    root, source = parse_go(
        'package main\nfunc main() {\n\tapi := r.PathPrefix("/api").Subrouter()\n\t_ = api\n}\n'
    )

    entries = _extract_go_net_http_routes(root, source, "main.go")

    assert entries == [
        {
            "method": None,
            "path": "/api",
            "framework": "gorilla_mux",
            "file": "main.go",
            "line": 3,
            "handler": "Subrouter()",
            "unresolved": True,
            "note": None,
        }
    ]


def test_extract_gin_get_route():
    root, source = parse_go('router.GET("/ping", pingHandler)\n')

    entries = _extract_gin_routes(root, source, "main.go")

    assert entries == [
        {
            "method": "GET",
            "path": "/ping",
            "framework": "gin",
            "file": "main.go",
            "line": 1,
            "handler": "pingHandler",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_gin_any_route_maps_to_any_method():
    root, source = parse_go('router.Any("/health", anyHandler)\n')

    entries = _extract_gin_routes(root, source, "main.go")

    assert entries[0]["method"] == "ANY"


def test_extract_gin_ignores_unrelated_selector_calls():
    root, source = parse_go("router.Use(loggerMiddleware)\n")

    entries = _extract_gin_routes(root, source, "main.go")

    assert entries == []


def test_extract_gin_composes_a_route_group_prefix():
    # Real bug found via audit: router.Group("/prefix") was completely
    # untracked - a route registered on the group's returned variable
    # silently emitted its bare, unprefixed path instead of the real one,
    # the same failure class already fixed 4 times for FastAPI (router-
    # mount-prefix loss/contamination) - Gin route groups are the
    # standard, near-universal way real Gin APIs are organized.
    root, source = parse_go(
        'func main() {\n'
        '\trouter := gin.Default()\n'
        '\tv1 := router.Group("/api/v1")\n'
        '\tv1.GET("/users", getUsers)\n'
        '\tv1.POST("/users", createUser)\n'
        '}\n'
    )

    entries = _extract_gin_routes(root, source, "main.go")

    paths = [(e["method"], e["path"]) for e in entries]
    assert paths == [("GET", "/api/v1/users"), ("POST", "/api/v1/users")]


def test_extract_gin_composes_a_nested_route_group_prefix():
    root, source = parse_go(
        'func main() {\n'
        '\trouter := gin.Default()\n'
        '\tv1 := router.Group("/api/v1")\n'
        '\tadmin := v1.Group("/admin")\n'
        '\tadmin.DELETE("/users/:id", deleteUser)\n'
        '}\n'
    )

    entries = _extract_gin_routes(root, source, "main.go")

    assert entries == [
        {
            "method": "DELETE",
            "path": "/api/v1/admin/users/:id",
            "framework": "gin",
            "file": "main.go",
            "line": 5,
            "handler": "deleteUser",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_gin_ungrouped_route_on_the_base_router_is_unaffected():
    # The group-prefix fix must be purely additive: a route on a variable
    # that was never the result of a .Group() call resolves to "" (its
    # prior, unprefixed behavior), not a crash or a spurious prefix.
    root, source = parse_go(
        'func main() {\n'
        '\trouter := gin.Default()\n'
        '\trouter.GET("/health", healthCheck)\n'
        '}\n'
    )

    entries = _extract_gin_routes(root, source, "main.go")

    assert entries[0]["path"] == "/health"


def test_extract_gin_group_var_reused_in_another_function_does_not_bleed_across():
    # Flash Review finding on PR #597: the original binding table was keyed
    # only by identifier text, file-wide - a later, unrelated
    # `v1 := other.Group("/admin")` in a second function overwrote the map
    # entry for the first function's own "v1", so routes registered under
    # the FIRST v1 (never actually grouped under /admin) were reported with
    # the SECOND function's /admin prefix. Each function reusing the
    # idiomatic "v1" group-variable name is real, common Gin code, not a
    # contrived shape.
    root, source = parse_go(
        'func registerPublicRoutes(router *gin.Engine) {\n'
        '\tv1 := router.Group("/api/v1")\n'
        '\tv1.GET("/users", getUsers)\n'
        '}\n'
        '\n'
        'func registerAdminRoutes(router *gin.Engine) {\n'
        '\tv1 := router.Group("/admin")\n'
        '\tv1.DELETE("/users/:id", deleteUser)\n'
        '}\n'
    )

    entries = _extract_gin_routes(root, source, "main.go")

    paths = [(e["method"], e["path"]) for e in entries]
    assert paths == [("GET", "/api/v1/users"), ("DELETE", "/admin/users/:id")]


def test_extract_axum_single_route():
    root, source = parse_rust(
        'fn main() { let app = Router::new().route("/health", get(health_handler)); }\n'
    )

    entries = _extract_axum_routes(root, source, "main.rs")

    assert entries == [
        {
            "method": "GET",
            "path": "/health",
            "framework": "axum",
            "file": "main.rs",
            "line": 1,
            "handler": "health_handler",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_axum_chained_combinators_on_one_path():
    root, source = parse_rust(
        'fn main() { let app = Router::new().route("/users", get(list_users).post(create_user)); }\n'
    )

    entries = _extract_axum_routes(root, source, "main.rs")

    assert len(entries) == 2
    by_method = {e["method"]: e["handler"] for e in entries}
    assert by_method == {"GET": "list_users", "POST": "create_user"}
    assert all(e["path"] == "/users" for e in entries)


def test_extract_axum_any_combinator():
    root, source = parse_rust(
        'fn main() { let app = Router::new().route("/ping", any(ping_handler)); }\n'
    )

    entries = _extract_axum_routes(root, source, "main.rs")

    assert entries[0]["method"] == "ANY"


def test_extract_axum_nest_is_unresolved():
    root, source = parse_rust(
        'fn main() { let app = Router::new().nest("/api", api_router); }\n'
    )

    entries = _extract_axum_routes(root, source, "main.rs")

    assert entries == [
        {
            "method": None,
            "path": "/api",
            "framework": "axum",
            "file": "main.rs",
            "line": 1,
            "handler": "nest(...)",
            "unresolved": True,
            "note": None,
        }
    ]


def test_extract_vapor_route_with_trailing_closure():
    root, source = parse_swift(
        'app.get("hello") { req async throws -> String in\n'
        '    return "Hello, world!"\n'
        "}\n"
    )

    entries = _extract_vapor_routes(root, source, "routes.swift")

    assert entries == [
        {
            "method": "GET",
            "path": "/hello",
            "framework": "vapor",
            "file": "routes.swift",
            "line": 1,
            "handler": "<inline handler>",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_vapor_route_with_use_labeled_handler_and_multi_segment_path():
    root, source = parse_swift('app.get("users", ":id", use: getUserHandler)\n')

    entries = _extract_vapor_routes(root, source, "routes.swift")

    assert entries == [
        {
            "method": "GET",
            "path": "/users/:id",
            "framework": "vapor",
            "file": "routes.swift",
            "line": 1,
            "handler": "getUserHandler",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_vapor_route_with_no_additional_path_segment():
    # Real bug found via audit: a route registered with no additional path
    # segment at all - the idiomatic REST "index"/"create" action living
    # at a group's own base path (GET /users via `users.get(use: index)`,
    # contrasted with GET /users/:id via `users.get(":id", use: show)`) -
    # produced zero entries, not just an imprecise path. An empty
    # path_segments list is a valid state (the route's own root, relative
    # to whatever prefix applies), not "no route here".
    root, source = parse_swift('app.get(use: index)\n')

    entries = _extract_vapor_routes(root, source, "routes.swift")

    assert entries == [
        {
            "method": "GET",
            "path": "/",
            "framework": "vapor",
            "file": "routes.swift",
            "line": 1,
            "handler": "index",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_vapor_route_on_grouped_sub_router():
    # A route group ("api.get(...)" where api = app.grouped("api")) is
    # caught the same way Express's mounted sub-routers are: by matching
    # the verb/shape, not by tracking what `api` was actually assigned from.
    root, source = parse_swift(
        'let api = app.grouped("api")\n'
        'api.get("health") { req in "ok" }\n'
    )

    entries = _extract_vapor_routes(root, source, "routes.swift")

    assert entries == [
        {
            "method": "GET",
            "path": "/health",
            "framework": "vapor",
            "file": "routes.swift",
            "line": 2,
            "handler": "<inline handler>",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_vapor_ignores_unrelated_get_calls_without_closure_or_handler():
    # someDict.get("key") - a real, extremely common shape with no trailing
    # closure and no use: label, so it must not be misidentified as a route.
    root, source = parse_swift('let value = someDict.get("key")\n')

    entries = _extract_vapor_routes(root, source, "utils.swift")

    assert entries == []


def test_extract_spring_get_mapping():
    root, source = parse_java(
        "public class UserController {\n"
        '    @GetMapping("/{id}")\n'
        "    public User getUser(Long id) { return null; }\n"
        "}\n"
    )

    entries = _extract_spring_boot_routes(root, source, "UserController.java")

    assert entries == [
        {
            "method": "GET",
            "path": "/{id}",
            "framework": "spring_boot",
            "file": "UserController.java",
            "line": 2,
            "handler": "getUser",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_spring_request_mapping_with_explicit_method():
    root, source = parse_java(
        "public class UserController {\n"
        '    @RequestMapping(value = "/list", method = RequestMethod.GET)\n'
        "    public List<User> listUsers() { return null; }\n"
        "}\n"
    )

    entries = _extract_spring_boot_routes(root, source, "UserController.java")

    assert entries[0]["method"] == "GET"
    assert entries[0]["path"] == "/list"


def test_extract_spring_request_mapping_without_method_is_any():
    root, source = parse_java(
        "public class UserController {\n"
        '    @RequestMapping("/all")\n'
        "    public List<User> allUsers() { return null; }\n"
        "}\n"
    )

    entries = _extract_spring_boot_routes(root, source, "UserController.java")

    assert entries[0]["method"] == "ANY"


def test_extract_spring_class_level_prefix_is_composed_with_the_method_path():
    root, source = parse_java(
        '@RequestMapping("/api/users")\n'
        "public class UserController {\n"
        '    @GetMapping("/{id}")\n'
        "    public User getUser(Long id) { return null; }\n"
        "}\n"
    )

    entries = _extract_spring_boot_routes(root, source, "UserController.java")

    assert entries[0]["path"] == "/api/users/{id}"
    assert entries[0]["note"] is None


def test_extract_spring_bare_mapping_annotation_is_the_class_route():
    root, source = parse_java(
        '@RestController\n@RequestMapping(path = "/articles")\n'
        "public class ArticlesApi {\n"
        "    @PostMapping\n    public void create() { }\n"
        '    @GetMapping(path = "feed")\n    public void feed() { }\n'
        '    @GetMapping(value = "{slug}")\n    public void one() { }\n'
        "}\n"
    )

    got = {(e["method"], e["path"]) for e in _extract_spring_boot_routes(root, source, "A.java")}

    assert got == {("POST", "/articles"), ("GET", "/articles/feed"), ("GET", "/articles/{slug}")}


def test_extract_spring_request_mapping_method_arrays_and_path_arrays_expand():
    root, source = parse_java(
        "public class C {\n"
        '    @RequestMapping(value = {"/a", "/b"}, method = {RequestMethod.GET, RequestMethod.POST})\n'
        "    public void both() { }\n"
        "}\n"
    )

    got = {(e["method"], e["path"]) for e in _extract_spring_boot_routes(root, source, "C.java")}

    assert got == {("GET", "/a"), ("POST", "/a"), ("GET", "/b"), ("POST", "/b")}


def test_extract_spring_constant_paths_are_not_guessed():
    root, source = parse_java(
        '@RequestMapping(Paths.BASE)\n'
        "public class C {\n"
        '    @GetMapping("/x")\n    public void a() { }\n'
        "    @GetMapping(Paths.ONE)\n    public void b() { }\n"
        "}\n"
    )

    entries = _extract_spring_boot_routes(root, source, "C.java")

    assert [e["path"] for e in entries] == ["/x"]
    assert "not a string literal" in entries[0]["note"]


def test_extract_rails_get_route():
    root, source = parse_ruby('get "users", to: "users#index"\n')

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries == [
        {
            "method": "GET",
            "path": "users",
            "framework": "rails",
            "file": "config/routes.rb",
            "line": 1,
            "handler": "users#index",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_rails_root_route():
    root, source = parse_ruby('root to: "home#index"\n')

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries == [
        {
            "method": "GET",
            "path": "/",
            "framework": "rails",
            "file": "config/routes.rb",
            "line": 1,
            "handler": "home#index",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_rails_resources_is_unresolved():
    root, source = parse_ruby("resources :items\n")

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries == [
        {
            "method": None,
            "path": "items",
            "framework": "rails",
            "file": "config/routes.rb",
            "line": 1,
            "handler": "resources(...)",
            "unresolved": True,
            "note": None,
        }
    ]


def test_extract_rails_resources_controller_override():
    # Real, live bug confirmed at Discourse's own config/routes.rb:356 -
    # "resources :keys, controller: 'api'" routes the "keys" resource to
    # ApiController, not KeysController. Without checking for this
    # override, the resource name alone silently named the wrong
    # controller for dead-code resolution purposes.
    root, source = parse_ruby(
        'resources :keys, controller: "api", only: %i[index show]\n'
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "api"


def test_extract_rails_ignores_unrelated_calls():
    root, source = parse_ruby('puts "hello"\n')

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries == []


def test_extract_rails_resources_inside_namespace_gets_module_prefix():
    # Real gap found via a real Discourse scan: a `resources :badges`
    # nested in `namespace :admin do ... end` previously recorded resource
    # name "badges" - identical to an unrelated top-level `resources
    # :badges` elsewhere in the same routes.rb, which made dead_code.py's
    # resolver correctly refuse to guess between the two real controllers
    # and leave both flagged dead code.
    root, source = parse_ruby(
        "namespace :admin do\n  resources :badges\nend\n"
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries == [
        {
            "method": None,
            "path": "admin/badges",
            "framework": "rails",
            "file": "config/routes.rb",
            "line": 2,
            "handler": "resources(...)",
            "unresolved": True,
            "note": None,
        }
    ]


def test_extract_rails_to_route_inside_namespace_gets_module_prefix():
    root, source = parse_ruby(
        'namespace :admin do\n  get "users", to: "users#index"\nend\n'
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["handler"] == "admin/users#index"


def test_extract_rails_nested_namespace_and_scope_module_compose_in_order():
    # Arbitrary nesting depth (namespace inside scope inside namespace, ...)
    # falls out of the upward-walk design for free - no separate stack
    # needed, just each enclosing do_block's owning call checked in turn.
    root, source = parse_ruby(
        'namespace :admin do\n'
        '  scope module: "extra" do\n'
        '    resources :widgets\n'
        "  end\n"
        "end\n"
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "admin/extra/widgets"


def test_extract_rails_bare_scope_does_not_add_a_module_prefix():
    # A bare `scope "/logs" do ... end` (or `scope path: "..." do`) changes
    # only the URL, never the controller module - confirmed on Discourse's
    # own routes.rb, where this form outnumbers `namespace` 20 to 3 and
    # none of those 20 renamespace their contents' controllers. Must NOT
    # be treated the same as `namespace`/`scope module:`.
    root, source = parse_ruby(
        'scope "/logs" do\n  resources :items\nend\n'
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "items"


def test_extract_rails_scope_module_symbol_value_gets_module_prefix():
    # Flash Review finding on #666: `scope module: :admin do` (a symbol
    # value) is equally valid Rails syntax alongside `module: "admin"` (a
    # string) - only the string form was handled, so this variant silently
    # produced no module prefix at all.
    root, source = parse_ruby(
        "scope module: :admin do\n  resources :badges\nend\n"
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "admin/badges"


def test_extract_rails_namespace_composes_the_url_prefix_too():
    # Real bug found via audit: _rails_enclosing_module_prefix correctly
    # composed the *module* half of `namespace :x do` (into `handler`) but
    # nothing composed the matching *URL* half into `path` - real Rails
    # routes `namespace :admin do get "users", to: "users#index" end` to
    # GET /admin/users, but this extractor reported path "users" with
    # unresolved: False, a confidently-wrong, not-honestly-unresolved value.
    root, source = parse_ruby(
        'namespace :admin do\n  get "users", to: "users#index"\nend\n'
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "admin/users"
    assert entries[0]["handler"] == "admin/users#index"
    assert entries[0]["unresolved"] is False


def test_extract_rails_bare_scope_composes_the_url_prefix_into_path():
    # The sibling of test_extract_rails_bare_scope_does_not_add_a_module_
    # prefix: a bare `scope "/logs" do` must NOT add a module prefix, but
    # it MUST add the URL prefix - real Rails routes this to GET
    # /logs/recent, not /recent.
    root, source = parse_ruby(
        'scope "/logs" do\n  get "recent", to: "logs#recent"\nend\n'
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "logs/recent"
    assert entries[0]["handler"] == "logs#recent"


def test_extract_rails_scope_path_keyword_composes_the_url_prefix():
    root, source = parse_ruby(
        'scope path: "/legacy" do\n  get "recent", to: "legacy#recent"\nend\n'
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "legacy/recent"


def test_extract_rails_namespace_path_override_wins_over_the_symbol_for_the_url():
    # `namespace :api, path: "v2" do` - real, documented Rails syntax: the
    # module/controller stays Api::, but the URL uses "v2" instead of "api".
    root, source = parse_ruby(
        'namespace :api, path: "v2" do\n  get "users", to: "users#index"\nend\n'
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "v2/users"
    assert entries[0]["handler"] == "api/users#index"


def test_extract_rails_scope_module_only_does_not_add_a_url_prefix():
    # The mirror image of the module-only test: `scope module: "x" do`
    # (no path: override) affects only the controller module, never the
    # URL - must not regress now that URL-prefix composition exists.
    root, source = parse_ruby(
        'scope module: "admin" do\n  get "users", to: "users#index"\nend\n'
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "users"
    assert entries[0]["handler"] == "admin/users#index"


def test_extract_rails_nested_namespace_and_scope_compose_the_url_prefix_in_order():
    root, source = parse_ruby(
        'namespace :admin do\n'
        '  scope "/legacy" do\n'
        '    get "users", to: "users#index"\n'
        "  end\n"
        "end\n"
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "admin/legacy/users"
    assert entries[0]["handler"] == "admin/users#index"


def test_extract_rails_root_route_inside_namespace_composes_the_url_prefix():
    root, source = parse_ruby(
        'namespace :admin do\n  root to: "dashboard#index"\nend\n'
    )

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries[0]["path"] == "admin"
    assert entries[0]["handler"] == "admin/dashboard#index"


def test_extract_rails_hash_rocket_route():
    # Real gap found via a real Discourse scan: config/routes.rb uses this
    # "path" => "controller#action" form 819 times vs only 15 uses of the
    # `to:` keyword form this extractor already handled - every one of
    # those 819 was previously invisible here entirely, since a hash-
    # rocket route is a single `pair` argument whose key is itself the
    # path (a plain string), not a separate standalone string argument
    # the way `get "users", to: "users#index"` splits path and handler
    # into two arguments.
    root, source = parse_ruby('get "/404-body" => "exceptions#not_found_body"\n')

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries == [
        {
            "method": "GET",
            "path": "/404-body",
            "framework": "rails",
            "file": "config/routes.rb",
            "line": 1,
            "handler": "exceptions#not_found_body",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_laravel_get_route():
    root, source = parse_php(
        "<?php\nRoute::get('/users', [UserController::class, 'index']);\n"
    )

    entries = _extract_laravel_routes(root, source, "routes/web.php")

    assert entries == [
        {
            "method": "GET",
            "path": "/users",
            "framework": "laravel",
            "file": "routes/web.php",
            "line": 2,
            "handler": "index",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_laravel_match_route_multiple_methods():
    root, source = parse_php(
        "<?php\nRoute::match(['get', 'post'], '/search', [SearchController::class, 'handle']);\n"
    )

    entries = _extract_laravel_routes(root, source, "routes/web.php")

    assert {e["method"] for e in entries} == {"GET", "POST"}
    assert all(e["path"] == "/search" for e in entries)


def test_extract_laravel_route_group_prefix_is_composed():
    root, source = parse_php(
        "<?php\n"
        "Route::group(['prefix' => 'admin'], function () {\n"
        "    Route::get('/dashboard', [AdminController::class, 'index']);\n"
        "});\n"
    )

    entries = _extract_laravel_routes(root, source, "routes/web.php")

    assert len(entries) == 1
    assert entries[0]["path"] == "/admin/dashboard"
    assert entries[0]["note"] is None


def test_extract_laravel_chained_prefix_group_and_nested_groups_compose_outermost_first():
    root, source = parse_php(
        "<?php\n"
        "Route::prefix('v1')->middleware('auth')->group(function () {\n"
        "    Route::group(['prefix' => 'admin'], function () {\n"
        "        Route::get('stats', 'StatsController@show');\n"
        "    });\n"
        "});\n"
    )

    assert [e["path"] for e in _extract_laravel_routes(root, source, "routes/web.php")] == ["/v1/admin/stats"]


def test_extract_laravel_non_literal_group_prefix_is_noted_not_guessed():
    root, source = parse_php(
        "<?php\nRoute::prefix($version)->group(function () {\n    Route::get('ping', 'P@ping');\n});\n"
    )

    entry = _extract_laravel_routes(root, source, "routes/web.php")[0]

    assert entry["path"] == "/ping"
    assert "not a string literal" in entry["note"]


def test_extract_laravel_routes_in_api_php_get_the_default_api_prefix():
    root, source = parse_php("<?php\nRoute::get('users/login', 'AuthController@login');\n")

    entry = _extract_laravel_routes(root, source, "routes/api.php")[0]

    assert entry["path"] == "/api/users/login"
    assert "default /api prefix" in entry["note"]


def test_extract_laravel_relative_uri_gets_a_leading_slash():
    root, source = parse_php("<?php\nRoute::get('users', 'U@index');\n")

    assert _extract_laravel_routes(root, source, "routes/web.php")[0]["path"] == "/users"


def test_extract_laravel_inline_closure_handler():
    root, source = parse_php("<?php\nRoute::get('/ping', function () { return 'ok'; });\n")

    entries = _extract_laravel_routes(root, source, "routes/web.php")

    assert entries[0]["handler"] == "<inline handler>"


def test_extract_laravel_legacy_string_handler():
    # The pre-::class Laravel syntax, still valid today - unlike the array
    # form (which requires a `use` import of the ::class reference this
    # extractor already captures fine), a bare "'Controller@method'"
    # string has no accompanying import anywhere in the file. Previously
    # fell through every branch in _laravel_handler_label and silently
    # came back "unknown".
    root, source = parse_php(
        "<?php\nRoute::get('/users', 'UserController@index');\n"
    )

    entries = _extract_laravel_routes(root, source, "routes/web.php")

    assert entries[0]["handler"] == "UserController@index"


def test_extract_laravel_route_resource_expands_to_its_standard_routes():
    root, source = parse_php("<?php\nRoute::resource('users', UserController::class);\n")

    entries = _extract_laravel_routes(root, source, "routes/web.php")

    got = {(e["method"], e["path"], e["handler"]) for e in entries}
    assert got == {
        ("GET", "/users", "UserController@index"), ("GET", "/users/create", "UserController@create"),
        ("POST", "/users", "UserController@store"), ("GET", "/users/{user}", "UserController@show"),
        ("GET", "/users/{user}/edit", "UserController@edit"), ("PUT", "/users/{user}", "UserController@update"),
        ("PATCH", "/users/{user}", "UserController@update"), ("DELETE", "/users/{user}", "UserController@destroy"),
    }
    assert all(not e["unresolved"] for e in entries)


def test_extract_laravel_api_resource_has_no_create_or_edit():
    root, source = parse_php("<?php\nRoute::apiResource('posts', PostController::class);\n")

    paths = {(e["method"], e["path"]) for e in _extract_laravel_routes(root, source, "routes/web.php")}

    assert ("GET", "/posts/create") not in paths and ("GET", "/posts/{post}/edit") not in paths
    assert ("GET", "/posts") in paths and ("DELETE", "/posts/{post}") in paths


def test_extract_laravel_resource_only_and_except_as_options_or_chained():
    root, source = parse_php(
        "<?php\n"
        "Route::resource('a', AC::class, ['only' => ['index', 'show']]);\n"
        "Route::resource('b', BC::class, ['except' => ['create', 'edit']]);\n"
        "Route::apiResource('c', CC::class)->only(['index']);\n"
        "Route::apiResource('d', DC::class)->except(['destroy']);\n"
    )

    entries = _extract_laravel_routes(root, source, "routes/web.php")
    by = lambda h: {e["handler"].split("@")[1] for e in entries if e["handler"].startswith(h)}

    assert by("AC") == {"index", "show"}
    assert by("BC") == {"index", "store", "show", "update", "destroy"}
    assert by("CC") == {"index"}
    assert by("DC") == {"index", "store", "show", "update"}


def test_extract_laravel_nested_resource_names_its_parameter_after_the_last_segment():
    root, source = parse_php(
        "<?php\nRoute::resource('articles/{article}/comments', CommentController::class, ['only' => ['destroy']]);\n"
    )

    assert [e["path"] for e in _extract_laravel_routes(root, source, "routes/web.php")] == [
        "/articles/{article}/comments/{comment}"
    ]


def test_extract_laravel_resource_with_a_non_literal_only_list_stays_unresolved():
    root, source = parse_php("<?php\nRoute::resource('x', XC::class, ['only' => $actions]);\n")

    entry = _extract_laravel_routes(root, source, "routes/web.php")[0]

    assert entry["unresolved"] is True and entry["method"] is None and entry["handler"] == "XC"


def test_extract_laravel_resource_legacy_string_and_namespaced_controllers_keep_their_names():
    root, source = parse_php(
        "<?php\nRoute::resource('items', 'ItemController', ['only' => ['index']]);\n"
        "Route::resource('admin/users', Admin\\UserController::class, ['only' => ['index']]);\n"
    )

    handlers = {e["handler"] for e in _extract_laravel_routes(root, source, "routes/web.php")}

    assert handlers == {"ItemController@index", "Admin\\UserController@index"}


def test_extract_aspnet_httpget_attribute():
    root, source = parse_csharp(
        "public class UsersController {\n"
        '    [HttpGet("{id}")]\n'
        "    public User GetUser(int id) { return null; }\n"
        "}\n"
    )

    entries = _extract_aspnet_attribute_routes(root, source, "UsersController.cs")

    assert entries == [
        {
            "method": "GET",
            "path": "{id}",
            "framework": "aspnet_attribute",
            "file": "UsersController.cs",
            "line": 2,
            "handler": "GetUser",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_aspnet_class_level_route_template_is_composed_with_the_method_template():
    root, source = parse_csharp(
        '[Route("api/[controller]")]\n'
        "public class UsersController {\n"
        '    [HttpGet("{id}")]\n'
        "    public User GetUser(int id) { return null; }\n"
        "}\n"
    )

    entries = _extract_aspnet_attribute_routes(root, source, "UsersController.cs")

    assert entries[0]["path"] == "/api/Users/{id}"
    assert entries[0]["note"] is None


def test_extract_aspnet_bare_http_attribute_is_the_class_route():
    root, source = parse_csharp(
        '[Route("api/articles")]\n'
        "public class ArticlesController {\n"
        "    [HttpGet]\n"
        "    public void List() { }\n"
        "    [HttpPost]\n"
        "    public void Create() { }\n"
        "}\n"
    )

    got = {(e["method"], e["path"]) for e in _extract_aspnet_attribute_routes(root, source, "A.cs")}

    assert got == {("GET", "/api/articles"), ("POST", "/api/articles")}


def test_extract_aspnet_absolute_method_template_replaces_the_class_template():
    root, source = parse_csharp(
        '[Route("api/users")]\n'
        "public class UsersController {\n"
        '    [HttpGet("~/health")]\n'
        "    public void Health() { }\n"
        '    [HttpGet("/ping")]\n'
        "    public void Ping() { }\n"
        "}\n"
    )

    got = {e["path"] for e in _extract_aspnet_attribute_routes(root, source, "U.cs")}

    assert got == {"/health", "/ping"}


def test_extract_aspnet_action_token_and_method_level_route():
    root, source = parse_csharp(
        '[Route("api/[controller]/[action]")]\n'
        "public class ToolsController {\n"
        "    [HttpPost]\n"
        "    public void Run() { }\n"
        '    [Route("legacy")]\n'
        "    public void Old() { }\n"
        "}\n"
    )

    got = {(e["method"], e["path"]) for e in _extract_aspnet_attribute_routes(root, source, "T.cs")}

    assert ("POST", "/api/Tools/Run") in got
    assert ("ANY", "/api/Tools/[action]/legacy".replace("[action]", "Old")) in got


def test_extract_aspnet_non_literal_class_route_is_noted_not_guessed():
    root, source = parse_csharp(
        "[Route(Constants.Base)]\n"
        "public class UsersController {\n"
        '    [HttpGet("{id}")]\n'
        "    public void Get() { }\n"
        "}\n"
    )

    entry = _extract_aspnet_attribute_routes(root, source, "U.cs")[0]

    assert entry["path"] == "{id}"
    assert "not a string literal" in entry["note"]


def test_extract_aspnet_ignores_non_http_attributes():
    root, source = parse_csharp(
        "public class UsersController {\n"
        "    [Authorize]\n"
        "    public User GetUser(int id) { return null; }\n"
        "}\n"
    )

    entries = _extract_aspnet_attribute_routes(root, source, "UsersController.cs")

    assert entries == []


def test_extract_aspnet_finds_httpget_stacked_after_another_attribute():
    # Each attribute on its own line is a separate sibling attribute_list node,
    # not one shared list - a method with [Authorize] before [HttpGet(...)] on
    # separate lines must still be detected, not silently dropped.
    root, source = parse_csharp(
        "public class UsersController {\n"
        "    [Authorize]\n"
        '    [HttpGet("{id}")]\n'
        "    public User GetUser(int id) { return null; }\n"
        "}\n"
    )

    entries = _extract_aspnet_attribute_routes(root, source, "UsersController.cs")

    assert len(entries) == 1
    assert entries[0]["path"] == "{id}"
    assert entries[0]["method"] == "GET"


def test_extract_aspnet_class_level_route_found_when_stacked_after_apicontroller():
    # Same sibling-attribute_list issue at the class level: [ApiController] then
    # [Route(...)] on separate lines - the standard `dotnet new webapi` shape.
    root, source = parse_csharp(
        "[ApiController]\n"
        '[Route("api/[controller]")]\n'
        "public class UsersController : ControllerBase {\n"
        '    [HttpGet("{id}")]\n'
        "    public User GetUser(int id) { return null; }\n"
        "}\n"
    )

    entries = _extract_aspnet_attribute_routes(root, source, "UsersController.cs")

    assert entries[0]["path"] == "/api/Users/{id}"
    assert entries[0]["note"] is None


def test_extract_aspnet_minimal_mapget():
    root, source = parse_csharp('app.MapGet("/health", HealthHandler);\n')

    entries = _extract_aspnet_minimal_routes(root, source, "Program.cs")

    assert entries == [
        {
            "method": "GET",
            "path": "/health",
            "framework": "aspnet_minimal",
            "file": "Program.cs",
            "line": 1,
            "handler": "HealthHandler",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_aspnet_minimal_inline_lambda_handler():
    root, source = parse_csharp('app.MapGet("/ping", () => "ok");\n')

    entries = _extract_aspnet_minimal_routes(root, source, "Program.cs")

    assert entries[0]["handler"] == "<inline handler>"


def test_extract_aspnet_minimal_mapgroup_is_unresolved():
    root, source = parse_csharp('app.MapGroup("/api").MapGet("/items", GetItems);\n')

    entries = _extract_aspnet_minimal_routes(root, source, "Program.cs")

    assert any(
        e["unresolved"] and e["path"] == "/api" and e["framework"] == "aspnet_minimal"
        for e in entries
    )
    assert any(e["path"] == "/items" and e["method"] == "GET" for e in entries)


def test_map_api_endpoints_covers_all_new_languages(tmp_path):
    (tmp_path / "main.go").write_text(
        'package main\nfunc main() { http.HandleFunc("/health", h) }\n'
    )
    (tmp_path / "server.rs").write_text(
        'fn main() { let app = Router::new().route("/ping", get(ping)); }\n'
    )
    (tmp_path / "Controller.java").write_text(
        'public class C {\n    @GetMapping("/x")\n    public void x() {}\n}\n'
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "routes.rb").write_text('get "y", to: "y#index"\n')
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "web.php").write_text(
        "<?php\nRoute::get('/z', [Z::class, 'index']);\n"
    )
    (tmp_path / "Program.cs").write_text('app.MapGet("/w", W);\n')

    result = map_api_endpoints(tmp_path)

    paths = {e["path"] for e in result["endpoints"]}
    assert paths == {"/health", "/ping", "/x", "y", "/z", "/w"}


def test_extract_ktor_top_level_route_with_path():
    root, source = parse_kotlin(
        'fun main() { routing { get("/health") { call.respondText("ok") } } }\n'
    )

    entries = _extract_ktor_routes(root, source, "Routes.kt")

    assert entries == [
        {
            "method": "GET",
            "path": "/health",
            "framework": "ktor",
            "file": "Routes.kt",
            "line": 1,
            "handler": "<lambda>",
            "unresolved": False,
            "note": None,
        }
    ]


def test_extract_ktor_bare_verb_inherits_route_prefix():
    # get { } with no path argument at all - real, idiomatic Ktor for "the
    # base path of the enclosing route(...) block" - a shape that has no
    # equivalent in Spring's annotation vocabulary or Axum's combinator
    # chain, so nothing existing already covers this.
    root, source = parse_kotlin(
        'fun r() { routing { route("/users/{id}") { get { respond() } } } }\n'
    )

    entries = _extract_ktor_routes(root, source, "Routes.kt")

    assert len(entries) == 1
    assert entries[0]["method"] == "GET"
    assert entries[0]["path"] == "/users/{id}"


def test_extract_ktor_route_prefix_composes_with_sub_path():
    root, source = parse_kotlin(
        'fun r() { routing { route("/users") { post("/create") { respond() } } } }\n'
    )

    entries = _extract_ktor_routes(root, source, "Routes.kt")

    assert entries[0]["method"] == "POST"
    assert entries[0]["path"] == "/users/create"


def test_extract_ktor_pass_through_wrapper_does_not_become_a_path_segment():
    # authenticate { } (and install/intercept/etc.) are real, common Ktor
    # wrappers with a trailing lambda but no path meaning at all -
    # confirmed by direct AST inspection this shape is indistinguishable
    # from route(...) at the grammar level (both are call-with-lambda);
    # only the identifier name tells them apart. Getting this wrong either
    # way is a real bug: skipping authenticate{} entirely would silently
    # lose every route nested inside auth, and treating "authenticate" as
    # a literal path segment would corrupt every path under it.
    root, source = parse_kotlin(
        'fun r() { routing { authenticate { route("/admin") { delete("/purge") { respond() } } } } }\n'
    )

    entries = _extract_ktor_routes(root, source, "Routes.kt")

    assert len(entries) == 1
    assert entries[0]["method"] == "DELETE"
    assert entries[0]["path"] == "/admin/purge"
    assert "authenticate" not in entries[0]["path"]


def test_extract_ktor_ignores_calls_with_no_trailing_lambda():
    root, source = parse_kotlin('fun r() { val x = someHelper("/not/a/route") }\n')

    entries = _extract_ktor_routes(root, source, "Routes.kt")

    assert entries == []


def test_extract_ktor_deeply_nested_pass_through_wrappers_does_not_crash():
    # Same recursion-depth bug class as the other extractors in this file,
    # but this walk prunes rather than descending unconditionally (it only
    # recurses into a matched call's own lambda body, threading a prefix
    # stack), so its iterative conversion needed its own explicit
    # (node, prefix_stack) stack rather than reusing the plain _walk_tree()
    # generator the other extractors share - the one place a subtle ordering
    # or state-threading bug in that conversion would show up.
    depth = 2000
    nested = "authenticate { " * depth + 'get("/deep") { respond() } ' + "} " * depth
    root, source = parse_kotlin(f"fun r() {{ routing {{ {nested} }} }}\n")

    entries = _extract_ktor_routes(root, source, "Routes.kt")

    assert len(entries) == 1
    assert entries[0]["path"] == "/deep"
    assert entries[0]["method"] == "GET"


def test_map_api_endpoints_extracts_kotlin_ktor_routes(tmp_path):
    (tmp_path / "Routes.kt").write_text(
        'fun r() { routing { get("/kt") { respond() } } }\n'
    )

    result = map_api_endpoints(tmp_path)

    paths = {e["path"] for e in result["endpoints"]}
    assert "/kt" in paths


def _parse_ts(code: str):
    from aletheore.endpoints import TS_LANGUAGE

    parser = Parser()
    parser.language = TS_LANGUAGE
    return parser.parse(code.encode()).root_node, code.encode()


def test_extract_nestjs_controller_and_method_decorators_compose():
    from aletheore.endpoints import _extract_nestjs_routes

    root, source = _parse_ts(
        "@Controller('articles')\n"
        "export class ArticleController {\n"
        "  @Get()\n  findAll() {}\n"
        "  @Get(':slug')\n  find(@Param('slug') slug: string) {}\n"
        "  @Post()\n  create() {}\n"
        "  @Delete(':slug/comments/:id')\n  remove() {}\n"
        "}\n"
    )

    got = {(e["method"], e["path"], e["handler"]) for e in _extract_nestjs_routes(root, source, "a.controller.ts")}

    assert got == {("GET", "/articles", "findAll"), ("GET", "/articles/:slug", "find"), ("POST", "/articles", "create"),
                   ("DELETE", "/articles/:slug/comments/:id", "remove")}


def test_extract_nestjs_object_form_controller_array_paths_and_all():
    from aletheore.endpoints import _extract_nestjs_routes

    root, source = _parse_ts(
        "@Controller({ path: 'users', version: '1' })\n"
        "class UsersController {\n  @Get(['a', 'b'])\n  two() {}\n  @All('x')\n  any() {}\n}\n"
    )

    got = {(e["method"], e["path"]) for e in _extract_nestjs_routes(root, source, "u.ts")}

    assert got == {("GET", "/users/a"), ("GET", "/users/b"), ("ANY", "/users/x")}


def test_extract_nestjs_non_literal_controller_path_is_noted_and_non_controllers_ignored():
    from aletheore.endpoints import _extract_nestjs_routes

    root, source = _parse_ts(
        "@Controller(BASE)\nclass A {\n  @Get('x')\n  x() {}\n}\n"
        "class NotAController {\n  @Get('y')\n  y() {}\n}\n"
    )

    entries = _extract_nestjs_routes(root, source, "a.ts")

    assert [e["path"] for e in entries] == ["/x"] and "not a string literal" in entries[0]["note"]


def test_map_api_endpoints_composes_nestjs_set_global_prefix(tmp_path):
    from aletheore.endpoints import map_api_endpoints

    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.ts").write_text("async function bootstrap() { app.setGlobalPrefix('api'); }\n")
    (tmp_path / "src/tags.controller.ts").write_text("@Controller('tags')\nexport class TagsController {\n  @Get()\n  all() {}\n}\n")

    paths = {e["path"] for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "nestjs"}

    assert paths == {"/api/tags"}


def test_extract_nestjs_route_under_a_non_literal_controller_path_is_flagged_unresolved():
    from aletheore.endpoints import _extract_nestjs_routes

    root, source = _parse_ts("@Controller(BASE)\nclass A {\n  @Get('x')\n  x() {}\n}\n")

    assert _extract_nestjs_routes(root, source, "a.ts")[0]["unresolved"] is True


def test_nest_global_prefix_is_ignored_inside_comments(tmp_path):
    from aletheore.endpoints import map_api_endpoints

    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.ts").write_text("// do not call setGlobalPrefix('v2') here\nasync function bootstrap() {}\n")
    (tmp_path / "src/t.controller.ts").write_text("@Controller('tags')\nexport class T {\n  @Get()\n  all() {}\n}\n")
    assert {e["path"] for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "nestjs"} == {"/tags"}


def _django_repo(tmp_path, files):
    from aletheore.endpoints import map_api_endpoints

    for rel, body in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return {(e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"]}


def test_django_include_prefix_is_composed_across_files_and_url_is_read(tmp_path):
    got = _django_repo(tmp_path, {
        "proj/urls.py": "from django.conf.urls import include, url\nurlpatterns = [url(r'^api/', include('shop.urls'))]\n",
        "shop/__init__.py": "",
        "shop/urls.py": "from django.conf.urls import url\nfrom . import views\nurlpatterns = [url(r'^items/(?P<pk>\\d+)/?$', views.item)]\n",
        "shop/views.py": "def item(request, pk):\n    pass\n",
    })
    assert ("ANY", "/api/items/<pk>") in got
    assert not any(path == "api/" for _m, path in got)  # the include itself is replaced by its routes


def test_django_nested_includes_and_path_prefixes_compose(tmp_path):
    got = _django_repo(tmp_path, {
        "proj/urls.py": "from django.urls import include, path\nurlpatterns = [path('v1/', include('api.urls'))]\n",
        "api/__init__.py": "",
        "api/urls.py": "from django.urls import include, path\nurlpatterns = [path('shop/', include('api.shop.urls'))]\n",
        "api/shop/__init__.py": "",
        "api/shop/urls.py": "from django.urls import path\nfrom . import views\nurlpatterns = [path('items/<int:id>/', views.item)]\n",
        "api/shop/views.py": "def item(request, id):\n    pass\n",
    })
    assert ("ANY", "/v1/shop/items/<int:id>/") in got


def test_django_include_of_a_module_outside_the_repository_stays_unresolved(tmp_path):
    from aletheore.endpoints import map_api_endpoints

    (tmp_path / "proj").mkdir()
    (tmp_path / "proj/urls.py").write_text("from django.urls import include, path\nurlpatterns = [path('admin/', include('django.contrib.admin.urls'))]\n")
    entry = map_api_endpoints(tmp_path)["endpoints"][0]

    assert entry["unresolved"] is True and "not in the repository" in entry["note"]


def test_drf_router_registration_expands_through_include_router_urls(tmp_path):
    got = _django_repo(tmp_path, {
        "proj/urls.py": (
            "from django.conf.urls import include, url\nfrom rest_framework.routers import DefaultRouter\nfrom .views import PostViewSet, TagViewSet\n"
            "router = DefaultRouter(trailing_slash=False)\nrouter.register(r'posts', PostViewSet)\nrouter.register(r'tags', TagViewSet)\n"
            "urlpatterns = [url(r'^api/', include(router.urls))]\n"),
        "proj/views.py": (
            "from rest_framework import viewsets, mixins\n"
            "class PostViewSet(viewsets.ModelViewSet):\n    pass\n"
            "class TagViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):\n    pass\n"),
    })
    assert {("GET", "/api/posts"), ("POST", "/api/posts"), ("GET", "/api/posts/<pk>"), ("PUT", "/api/posts/<pk>"),
            ("PATCH", "/api/posts/<pk>"), ("DELETE", "/api/posts/<pk>")} <= got
    assert ("GET", "/api/tags") in got and ("POST", "/api/tags") not in got


def test_drf_router_with_a_viewset_from_outside_the_repo_is_unresolved_not_guessed(tmp_path):
    from aletheore.endpoints import map_api_endpoints

    for rel, body in {
        "urls.py": ("from rest_framework.routers import DefaultRouter\nfrom thirdparty import ThingViewSet\nfrom django.urls import include, path\n"
                    "router = DefaultRouter()\nrouter.register('things', ThingViewSet)\nurlpatterns = [path('', include(router.urls))]\n"),
    }.items():
        (tmp_path / rel).write_text(body)
    entries = map_api_endpoints(tmp_path)["endpoints"]

    assert len(entries) == 1 and entries[0]["unresolved"] is True and entries[0]["method"] == "ANY"


def test_django_as_view_methods_come_from_the_class_generics_and_function_decorators(tmp_path):
    got = _django_repo(tmp_path, {
        "urls.py": (
            "from django.urls import path\nfrom . import views\nurlpatterns = [\n"
            "    path('a/', views.AView.as_view()),\n    path('b/', views.BView.as_view()),\n"
            "    path('c/', views.func_view),\n    path('d/', views.DView.as_view({'get': 'list', 'post': 'create'})),\n]\n"),
        "views.py": (
            "from rest_framework import generics\nfrom rest_framework.decorators import api_view\n"
            "class AView(APIView):\n    def get(self, request): pass\n    def delete(self, request): pass\n"
            "class BView(generics.ListCreateAPIView):\n    pass\n"
            "@api_view(['GET', 'POST'])\ndef func_view(request): pass\n"),
    })
    assert {("GET", "a/"), ("DELETE", "a/"), ("GET", "b/"), ("POST", "b/"), ("GET", "c/"), ("POST", "c/"), ("GET", "d/"), ("POST", "d/")} <= got
    assert ("ANY", "a/") not in got


def test_django_regex_routes_become_templates():
    from aletheore.endpoints import _django_regex_to_route

    assert _django_regex_to_route(r"^articles/(?P<slug>[-\w]+)/favorite/?$") == "articles/<slug>/favorite"
    assert _django_regex_to_route(r"^tags/?$") == "tags"
    assert _django_regex_to_route(r"^files/(\d+)/$") == "files/<arg>/"
    assert _django_regex_to_route("users/<int:id>/") == "users/<int:id>/"


def test_django_regex_templates_drop_group_quantifiers_and_handle_non_capturing_groups():
    from aletheore.endpoints import _django_regex_to_route

    assert _django_regex_to_route(r"^articles/(?P<slug>[-\w]+)+/$") == "articles/<slug>/"
    assert _django_regex_to_route(r"^y/(?P<year>[0-9]{4})/$") == "y/<year>/"
    assert _django_regex_to_route(r"^items/(\d+)?/$") == "items/<arg>/"
    assert _django_regex_to_route(r"^docs(?:/(?P<page>\w+))?/$") == "docs/"
    assert _django_regex_to_route(r"^(?:v1|v2)/ping$") == "v1|v2/ping"


def test_extract_spring_request_mapping_method_given_as_a_statically_imported_constant():
    root, source = parse_java('public class C {\n    @RequestMapping(path = "/users", method = POST)\n    public void f() { }\n}\n')

    assert [(e["method"], e["path"]) for e in _extract_spring_boot_routes(root, source, "C.java")] == [("POST", "/users")]


def test_extract_spring_route_under_a_constant_class_prefix_is_flagged_unresolved():
    root, source = parse_java(
        "@RequestMapping(Paths.BASE)\npublic class C {\n    @GetMapping\n    public void a() { }\n    @GetMapping(\"/x\")\n    public void b() { }\n}\n"
    )

    entries = _extract_spring_boot_routes(root, source, "C.java")

    assert entries and all(e["unresolved"] is True for e in entries)


def test_extract_spring_request_mapping_with_an_unreadable_method_is_unresolved_not_any():
    root, source = parse_java('public class C {\n    @RequestMapping(path = "/x", method = Consts.VERB)\n    public void a() { }\n}\n')
    entry = _extract_spring_boot_routes(root, source, "C.java")[0]
    assert entry["unresolved"] is True and "RequestMethod" in entry["note"]


def test_extract_laravel_non_route_group_chain_is_not_a_route_group_and_notes_combine():
    root, source = parse_php("<?php\n$cache->prefix('x')->group(function () {\n    Route::get('ping', 'P@ping');\n});\n")
    assert _extract_laravel_routes(root, source, "routes/web.php")[0]["path"] == "/ping"
    root, source = parse_php("<?php\nRoute::prefix($v)->group(function () {\n    Route::get('ping', 'P@ping');\n});\n")
    note = _extract_laravel_routes(root, source, "routes/api.php")[0]["note"]
    assert "not a string literal" in note and "default /api prefix" in note


def test_extract_aspnet_route_under_a_non_literal_class_route_is_flagged_unresolved():
    root, source = parse_csharp(
        "[Route(Constants.Base)]\npublic class UsersController {\n    [HttpGet(\"{id}\")]\n    public void Get() { }\n}\n"
    )

    assert _extract_aspnet_attribute_routes(root, source, "U.cs")[0]["unresolved"] is True


def test_extract_flask_route_methods_given_as_a_tuple_or_a_set():
    for literal in ('("POST",)', '{"GET", "POST"}', '["POST"]'):
        root, source = parse_python(f'@bp.route("/api/users", methods={literal})\ndef users():\n    pass\n')
        methods = {e["method"] for e in _extract_flask_fastapi_routes(root, source, "views.py")}
        assert methods == ({"GET", "POST"} if "GET" in literal else {"POST"}), literal


def test_swift_route_segments_that_start_with_a_slash_do_not_double_it(tmp_path):
    # Hummingbird-style `router.get("/users")` goes through the Vapor matcher: no `//users`
    (tmp_path / "a.swift").write_text('import Hummingbird\nlet router = Router()\nrouter.get("/users") { _, _ in "x" }\nrouter.post("items", "{id}") { _, _ in "y" }\n')
    got = sorted((e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"])
    assert got == [("GET", "/users"), ("POST", "/items/{id}")]
