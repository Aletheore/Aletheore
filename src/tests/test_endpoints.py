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
    _extract_laravel_routes,
    _extract_rails_routes,
    _extract_spring_boot_routes,
    map_api_endpoints,
)
from aletheore.scanner.graph import (
    CSHARP_LANGUAGE,
    GO_LANGUAGE,
    JAVA_LANGUAGE,
    JS_LANGUAGE,
    PHP_LANGUAGE,
    PY_LANGUAGE,
    RUBY_LANGUAGE,
    RUST_LANGUAGE,
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
