from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, code, framework):
    (tmp_path / "app.py").write_text(code)
    return sorted((e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == framework)


def test_bottle_bare_decorators(tmp_path):
    code = "from bottle import route, get, post\n@route('/hello')\ndef a(): pass\n@get('/users')\ndef b(): pass\n@route('/items', method='POST')\ndef c(): pass\n@route('/multi', method=['PUT', 'DELETE'])\ndef d(): pass\n"
    assert _routes(tmp_path, code, "bottle") == [
        ("DELETE", "/multi"), ("GET", "/hello"), ("GET", "/users"), ("POST", "/items"), ("PUT", "/multi"),
    ]


def test_litestar_handlers_and_controller_prefix(tmp_path):
    code = """from litestar import Controller, get, post
@get('/health')
async def health() -> str: ...
class Users(Controller):
    path = '/users'
    @get('/{id:int}')
    async def one(self) -> str: ...
    @post()
    async def create(self) -> str: ...
"""
    assert _routes(tmp_path, code, "litestar") == [("GET", "/health"), ("GET", "/users/{id:int}"), ("POST", "/users")]


def test_receiver_decorators_are_left_to_the_flask_matcher(tmp_path):
    code = "from bottle import Bottle\napp = Bottle()\n@app.get('/x')\ndef x(): pass\n"
    assert _routes(tmp_path, code, "bottle") == []


def test_tornado_handlers_with_methods_and_url_helper(tmp_path):
    code = """import tornado.web
class Users(tornado.web.RequestHandler):
    def get(self): pass
    def post(self): pass
class Items(tornado.web.RequestHandler):
    def delete(self, id): pass
app = tornado.web.Application([(r"/users", Users), url(r"/items/(\\d+)$", Items), (r"/other", Missing)])
"""
    assert _routes(tmp_path, code, "tornado") == [
        ("ANY", "/other"), ("DELETE", "/items/(\\d+)"), ("GET", "/users"), ("POST", "/users"),
    ]


def test_falcon_resources_by_their_on_methods(tmp_path):
    code = """import falcon
class Users:
    def on_get(self, req, resp): pass
    def on_post(self, req, resp): pass
app = falcon.App()
app.add_route('/users', Users())
app.add_route('/gone', Elsewhere())
"""
    assert _routes(tmp_path, code, "falcon") == [("ANY", "/gone"), ("GET", "/users"), ("POST", "/users")]


def test_files_that_do_not_mention_a_framework_are_ignored(tmp_path):
    code = "@get('/x')\ndef x(): pass\nroutes = [('/y', Y)]\n"
    for framework in ("bottle", "litestar", "tornado", "falcon"):
        assert _routes(tmp_path, code, framework) == []


def test_pyramid_views_take_the_pattern_of_their_route_name(tmp_path):
    (tmp_path / "routes.py").write_text("def includeme(config):\n    config.add_route('home', '/home')\n    config.add_route('item', '/items/{id}')\n")
    (tmp_path / "views.py").write_text("""from pyramid.view import view_config, view_defaults
@view_config(route_name='home', request_method='GET')
def home(request): pass
@view_config(route_name='item', request_method=('PUT', 'DELETE'))
def item(request): pass
@view_defaults(route_name='item')
class Items:
    @view_config(request_method='POST')
    def create(self): pass
""")
    got = sorted((e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "pyramid")
    assert got == [("DELETE", "/items/{id}"), ("GET", "/home"), ("POST", "/items/{id}"), ("PUT", "/items/{id}")]


def test_pyramid_add_view_and_an_unknown_route_name(tmp_path):
    (tmp_path / "app.py").write_text("from pyramid.config import Configurator\nconfig = Configurator()\nconfig.add_route('ping', '/ping')\nconfig.add_view(ping_view, route_name='ping')\nconfig.add_view(other, route_name='nowhere', request_method='GET')\n")
    got = sorted((e["method"], e["path"], bool(e["note"])) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "pyramid")
    assert got == [("ANY", "/ping", False), ("GET", "/", True)]


def test_pyramid_route_name_defined_in_two_apps_resolves_to_the_nearest_one(tmp_path):
    for app, pattern in (("a", "/alpha"), ("b", "/beta")):
        (tmp_path / app).mkdir()
        (tmp_path / app / "routes.py").write_text(f"def includeme(config):\n    config.add_route('home', '{pattern}')\n")
        (tmp_path / app / "views.py").write_text("from pyramid.view import view_config\n@view_config(route_name='home')\ndef home(request): pass\n")
    got = sorted((e["file"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "pyramid")
    assert got == [("a/views.py", "/alpha"), ("b/views.py", "/beta")]
