from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, code, name="app.js"):
    (tmp_path / name).write_text(code)
    return sorted((e["framework"], e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"])


def test_route_object_with_a_method_and_url(tmp_path):
    code = "const fastify = require('fastify')()\nfastify.route({ method: 'POST', url: '/items', handler: create })\n"
    assert _routes(tmp_path, code) == [("fastify", "POST", "/items")]


def test_method_array_and_path_key(tmp_path):
    code = "app.route({\n  method: ['GET', 'HEAD'],\n  path: '/status',\n  handler: async () => 'ok',\n})\n"
    assert _routes(tmp_path, code) == [("fastify", "GET", "/status"), ("fastify", "HEAD", "/status")]


def test_typescript_file_and_shorthand_verbs_are_not_doubled(tmp_path):
    code = "fastify.get('/a', h)\nfastify.route({ method: 'DELETE', url: '/a/:id', handler: remove })\n"
    assert _routes(tmp_path, code, "app.ts") == [("express", "GET", "/a"), ("fastify", "DELETE", "/a/:id")]


def test_route_object_without_a_literal_url_is_ignored(tmp_path):
    code = "fastify.route({ method: 'GET', url: PATH, handler: h })\nrouter.route('/x').get(h)\n"
    assert all(framework != "fastify" for framework, _m, _p in _routes(tmp_path, code))


def test_hapi_route_objects_and_arrays_are_labelled_hapi(tmp_path):
    code = """const Hapi = require('@hapi/hapi')
server.route({ method: 'GET', path: '/users', handler: list })
server.route([
  { method: 'POST', path: '/items', handler: add },
  { method: ['PUT', 'PATCH'], path: '/items/{id}', handler: update },
])
"""
    assert _routes(tmp_path, code) == [
        ("hapi", "GET", "/users"), ("hapi", "PATCH", "/items/{id}"), ("hapi", "POST", "/items"), ("hapi", "PUT", "/items/{id}"),
    ]
