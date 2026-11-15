from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, code, framework):
    (tmp_path / "app.py").write_text(code)
    return sorted((e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == framework)


def test_starlette_routes_methods_and_mount_prefixes(tmp_path):
    code = """from starlette.routing import Route, Mount, WebSocketRoute
routes = [
    Route("/users", users, methods=["GET", "POST"]),
    Route("/health", health),
    Mount("/api", routes=[Route("/items", items, methods=["DELETE"]), Mount("/v2", routes=[Route("/x", x)])]),
    WebSocketRoute("/ws", ws),
]
"""
    assert _routes(tmp_path, code, "starlette") == [
        ("DELETE", "/api/items"), ("GET", "/api/v2/x"), ("GET", "/health"), ("GET", "/users"), ("POST", "/users"),
        ("WEBSOCKET", "/ws"),
    ]


def test_starlette_add_route(tmp_path):
    code = "from starlette.applications import Starlette\napp = Starlette()\napp.add_route('/ping', ping, methods=['POST'])\n"
    assert _routes(tmp_path, code, "starlette") == [("POST", "/ping")]


def test_aiohttp_route_helpers_and_router_methods(tmp_path):
    code = """from aiohttp import web
app = web.Application()
app.add_routes([web.get('/users', users), web.post('/items', items), web.route('PUT', '/thing', thing)])
app.router.add_get('/health', health)
app.router.add_route('DELETE', '/items/{id}', remove)
"""
    assert _routes(tmp_path, code, "aiohttp") == [
        ("DELETE", "/items/{id}"), ("GET", "/health"), ("GET", "/users"), ("POST", "/items"), ("PUT", "/thing"),
    ]


def test_file_without_either_framework_is_ignored(tmp_path):
    code = "routes = [Route('/x', x)]\nweb = object()\nweb.get('/y', y)\n"
    assert _routes(tmp_path, code, "starlette") == [] and _routes(tmp_path, code, "aiohttp") == []


def test_file_with_both_frameworks_is_read_for_both(tmp_path):
    code = """from starlette.routing import Route
from aiohttp import web
routes = [Route('/s', s)]
app = web.Application()
app.router.add_route('GET', '/a', a)
"""
    assert _routes(tmp_path, code, "starlette") == [("GET", "/s")]
    assert _routes(tmp_path, code, "aiohttp") == [("GET", "/a")]


def test_starlette_app_mount_prefixes_its_routes(tmp_path):
    code = "from starlette.applications import Starlette\nfrom starlette.routing import Route\napp = Starlette()\napp.mount('/api', routes=[Route('/x', x)])\n"
    assert _routes(tmp_path, code, "starlette") == [("GET", "/api/x")]


def test_aiohttp_keyword_arguments_and_unrelated_get_calls(tmp_path):
    code = """from aiohttp import web
import db
app = web.Application()
app.router.add_get(path='/kw', handler=h)
rows = db.get('users', cb)
"""
    assert _routes(tmp_path, code, "aiohttp") == [("GET", "/kw")]
