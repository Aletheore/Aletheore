from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, files):
    for name, code in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code)
    return sorted((e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"])


def test_koa_router_prefix_option_and_prefix_call(tmp_path):
    code = """const Router = require('@koa/router')
const users = new Router({ prefix: '/api/users' })
users.get('/', list)
users.get('/:id', one)
const items = new Router()
items.prefix('/items')
items.post('/', add)
const plain = new Router()
plain.get('/health', h)
"""
    assert _routes(tmp_path, {"app.js": code}) == [
        ("GET", "/api/users"), ("GET", "/api/users/:id"), ("GET", "/health"), ("POST", "/items"),
    ]


def test_hono_base_path_and_same_file_route_mounts(tmp_path):
    code = """import { Hono } from 'hono'
const app = new Hono()
const api = new Hono().basePath('/v1')
api.get('/ping', (c) => c.text('pong'))
const admin = new Hono()
admin.post('/ban', (c) => c.text('x'))
app.route('/api', api)
app.route('/admin', admin)
app.get('/health', (c) => c.text('ok'))
"""
    assert _routes(tmp_path, {"index.ts": code}) == [("GET", "/api/v1/ping"), ("GET", "/health"), ("POST", "/admin/ban")]


def test_hono_sub_app_imported_from_another_file(tmp_path):
    files = {
        "src/index.ts": "import { Hono } from 'hono'\nimport users from './users'\nimport { posts } from './posts'\nconst app = new Hono()\napp.route('/users', users)\napp.route('/posts', posts)\nexport default app\n",
        "src/users.ts": "import { Hono } from 'hono'\nconst users = new Hono()\nusers.get('/:id', (c) => c.text('u'))\nexport default users\n",
        "src/posts.ts": "import { Hono } from 'hono'\nexport const posts = new Hono()\nposts.delete('/:id', (c) => c.text('p'))\n",
    }
    assert _routes(tmp_path, files) == [("DELETE", "/posts/:id"), ("GET", "/users/:id")]


def test_hono_sub_app_mounted_twice_is_listed_at_both(tmp_path):
    code = "import { Hono } from 'hono'\nconst app = new Hono()\nconst v = new Hono()\nv.get('/x', h)\napp.route('/a', v)\napp.route('/b', v)\n"
    assert _routes(tmp_path, {"i.ts": code}) == [("GET", "/a/x"), ("GET", "/b/x")]


def test_plain_express_files_are_unchanged(tmp_path):
    code = "const router = require('express').Router()\nrouter.get('/x', h)\n"
    assert _routes(tmp_path, {"r.js": code}) == [("GET", "/x")]
