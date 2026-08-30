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


def test_extract_spring_class_level_prefix_produces_a_note():
    root, source = parse_java(
        '@RequestMapping("/api/users")\n'
        "public class UserController {\n"
        '    @GetMapping("/{id}")\n'
        "    public User getUser(Long id) { return null; }\n"
        "}\n"
    )

    entries = _extract_spring_boot_routes(root, source, "UserController.java")

    assert entries[0]["path"] == "/{id}"
    assert entries[0]["note"] == (
        "class-level @RequestMapping prefix present, not composed into this path"
    )


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


def test_extract_rails_ignores_unrelated_calls():
    root, source = parse_ruby('puts "hello"\n')

    entries = _extract_rails_routes(root, source, "config/routes.rb")

    assert entries == []


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


def test_extract_laravel_route_inside_group_gets_a_note():
    root, source = parse_php(
        "<?php\n"
        "Route::group(['prefix' => 'admin'], function () {\n"
        "    Route::get('/dashboard', [AdminController::class, 'index']);\n"
        "});\n"
    )

    entries = _extract_laravel_routes(root, source, "routes/web.php")

    assert len(entries) == 1
    assert entries[0]["path"] == "/dashboard"
    assert entries[0]["note"] == (
        "declared inside a Route::group() prefix, not composed into this path"
    )


def test_extract_laravel_inline_closure_handler():
    root, source = parse_php("<?php\nRoute::get('/ping', function () { return 'ok'; });\n")

    entries = _extract_laravel_routes(root, source, "routes/web.php")

    assert entries[0]["handler"] == "<inline handler>"


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


def test_extract_aspnet_class_level_route_template_produces_a_note():
    root, source = parse_csharp(
        '[Route("api/[controller]")]\n'
        "public class UsersController {\n"
        '    [HttpGet("{id}")]\n'
        "    public User GetUser(int id) { return null; }\n"
        "}\n"
    )

    entries = _extract_aspnet_attribute_routes(root, source, "UsersController.cs")

    assert entries[0]["note"] == (
        "class-level [Route] template present, not composed into this path"
    )


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

    assert entries[0]["note"] == (
        "class-level [Route] template present, not composed into this path"
    )


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


def test_map_api_endpoints_extracts_kotlin_ktor_routes(tmp_path):
    (tmp_path / "Routes.kt").write_text(
        'fun r() { routing { get("/kt") { respond() } } }\n'
    )

    result = map_api_endpoints(tmp_path)

    paths = {e["path"] for e in result["endpoints"]}
    assert "/kt" in paths
