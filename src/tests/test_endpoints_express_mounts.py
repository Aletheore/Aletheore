"""Express router mounts across files, route() chains, factories and ES/CommonJS exports (end to end, tmp repos)."""

from pathlib import Path

from aletheore.endpoints import map_api_endpoints


def _entries(tmp_path: Path, files: dict[str, str]) -> list[dict]:
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return map_api_endpoints(repo)["endpoints"]


def _routes(tmp_path, files):
    return {(e["method"], e["path"]) for e in _entries(tmp_path, files) if e["method"]}


def test_router_mounted_with_a_prefix_across_files_commonjs(tmp_path):
    got = _routes(tmp_path, {
        "app.js": "const express = require('express');\nconst users = require('./routes/users');\nconst app = express();\napp.use('/users', users);\n",
        "routes/users.js": "const express = require('express');\nconst router = express.Router();\nrouter.get('/:id', h);\nrouter.post('/', h);\nmodule.exports = router;\n",
    })
    assert {("GET", "/users/:id"), ("POST", "/users/")} <= got or {("GET", "/users/:id"), ("POST", "/users")} <= got
    assert ("GET", "/:id") not in got


def test_router_mounted_with_es_modules(tmp_path):
    got = _routes(tmp_path, {
        "app.js": "import express from 'express';\nimport users from './routes/users.js';\nconst app = express();\napp.use('/users', users);\n",
        "routes/users.js": "import { Router } from 'express';\nconst router = Router();\nrouter.get('/me', h);\nexport default router;\n",
    })
    assert ("GET", "/users/me") in got


def test_named_export_and_destructured_require(tmp_path):
    got = _routes(tmp_path, {
        "app.js": "const { adminRouter } = require('./admin');\nconst app = require('express')();\napp.use('/admin', adminRouter);\n",
        "admin.js": "const express = require('express');\nconst adminRouter = express.Router();\nadminRouter.get('/stats', h);\nmodule.exports = { adminRouter };\n",
    })
    assert ("GET", "/admin/stats") in got


def test_aliased_named_import(tmp_path):
    got = _routes(tmp_path, {
        "app.ts": "import express from 'express';\nimport { router as api } from './api';\nconst app = express();\napp.use('/api', api);\n",
        "api.ts": "import { Router } from 'express';\nexport const router = Router();\nrouter.get('/ping', h);\n",
    })
    assert ("GET", "/api/ping") in got


def test_nested_routers_compose_prefixes(tmp_path):
    got = _routes(tmp_path, {
        "app.js": "const express = require('express');\nconst { admin } = require('./admin');\nconst app = express();\napp.use('/admin', admin);\n",
        "admin.js": "const express = require('express');\nconst admin = express.Router();\nconst reports = express.Router();\nreports.get('/daily', h);\nadmin.use('/reports', reports);\nmodule.exports = { admin };\n",
    })
    assert ("GET", "/admin/reports/daily") in got


def test_router_built_by_a_factory_function(tmp_path):
    got = _routes(tmp_path, {
        "app.js": "const express = require('express');\nconst makeShop = require('./shop');\nconst app = express();\napp.use('/shop', makeShop());\n",
        "shop.js": "const express = require('express');\nmodule.exports = function makeShop() {\n  const router = express.Router();\n  router.get('/products', h);\n  return router;\n};\n",
    })
    assert ("GET", "/shop/products") in got


def test_router_in_the_same_file(tmp_path):
    got = _routes(tmp_path, {"app.js": "const express = require('express');\nconst app = express();\nconst v1 = express.Router();\nv1.get('/status', h);\napp.use('/api/v1', v1);\n"})
    assert ("GET", "/api/v1/status") in got


def test_router_mounted_at_two_prefixes_is_listed_at_both(tmp_path):
    got = _routes(tmp_path, {"app.js": "const express = require('express');\nconst app = express();\nconst r = express.Router();\nr.get('/x', h);\napp.use('/a', r);\napp.use('/b', r);\n"})
    assert {("GET", "/a/x"), ("GET", "/b/x")} <= got


def test_mount_without_a_prefix_adds_nothing(tmp_path):
    got = _routes(tmp_path, {"app.js": "const express = require('express');\nconst app = express();\nconst r = express.Router();\nr.get('/x', h);\napp.use(r);\n"})
    assert ("GET", "/x") in got


def test_route_chain_on_app_and_router(tmp_path):
    got = _routes(tmp_path, {"app.js": (
        "const express = require('express');\nconst app = express();\nconst r = express.Router();\n"
        "app.route('/book').get(h).post(h);\nr.route('/:id/posts').get(h).put(h).delete(h);\napp.use('/u', r);\n"
    )})
    assert {("GET", "/book"), ("POST", "/book"), ("GET", "/u/:id/posts"), ("PUT", "/u/:id/posts"), ("DELETE", "/u/:id/posts")} <= got


def test_a_resolved_mount_is_not_reported_as_an_endpoint(tmp_path):
    entries = _entries(tmp_path, {"app.js": "const express = require('express');\nconst app = express();\nconst r = express.Router();\nr.get('/x', h);\napp.use('/a', r);\n"})
    assert not [e for e in entries if e["method"] is None]


def test_a_mount_that_cannot_be_resolved_stays_reported_as_unresolved(tmp_path):
    entries = _entries(tmp_path, {"app.js": "const express = require('express');\nconst app = express();\nconst ext = require('some-package');\napp.use('/ext', ext);\n"})
    assert [e for e in entries if e["method"] is None and e["path"] == "/ext" and e["unresolved"]]


def test_a_non_literal_mount_prefix_is_flagged_unresolved(tmp_path):
    entries = _entries(tmp_path, {
        "app.js": "const express = require('express');\nconst r = require('./r');\nconst app = express();\napp.use(process.env.BASE, r);\n",
        "r.js": "const express = require('express');\nconst r = express.Router();\nr.get('/x', h);\nmodule.exports = r;\n",
    })
    found = [e for e in entries if e["path"].endswith("/x")]
    assert found and all(e["unresolved"] for e in found)


def test_all_stays_any(tmp_path):
    got = _routes(tmp_path, {"app.js": "const express = require('express');\nconst app = express();\napp.all('/any', h);\n"})
    assert ("ANY", "/any") in got


def test_index_file_and_directory_require(tmp_path):
    got = _routes(tmp_path, {
        "app.js": "const express = require('express');\nconst app = express();\napp.use('/v', require('./v'));\n",
        "v/index.js": "const express = require('express');\nconst r = express.Router();\nr.get('/a', h);\nmodule.exports = r;\n",
    })
    assert ("GET", "/v/a") in got


def test_fluent_router_chain_and_anonymous_default_export(tmp_path):
    # the RealWorld example layout: routers built by chaining .use(...) and an unnamed default export
    got = _routes(tmp_path, {
        "src/main.ts": "import express from 'express';\nimport routes from './routes';\nconst app = express();\napp.use(routes);\n",
        "src/routes.ts": (
            "import { Router } from 'express';\nimport tags from './tag.controller';\nimport articles from './article.controller';\n"
            "const api = Router()\n  .use(tags)\n  .use(articles);\n\nexport default Router().use('/api', api);\n"
        ),
        "src/tag.controller.ts": "import { Router } from 'express';\nconst router = Router();\nrouter.get('/tags', h);\nexport default router;\n",
        "src/article.controller.ts": "import { Router } from 'express';\nconst router = Router();\nrouter.get('/articles', h);\nrouter.post('/articles', h);\nexport default router;\n",
    })
    assert {("GET", "/api/tags"), ("GET", "/api/articles"), ("POST", "/api/articles")} <= got
    assert ("GET", "/tags") not in got


def test_router_variable_initialised_with_a_chain(tmp_path):
    got = _routes(tmp_path, {"app.js": "const express = require('express');\nconst app = express();\nconst a = express.Router();\na.get('/x', h);\nconst b = express.Router().use('/in', a);\napp.use('/out', b);\n"})
    assert ("GET", "/out/in/x") in got


def test_module_exports_of_a_chained_router(tmp_path):
    got = _routes(tmp_path, {
        "app.js": "const express = require('express');\nconst app = express();\napp.use('/p', require('./r'));\n",
        "r.js": "const express = require('express');\nconst leaf = express.Router();\nleaf.get('/z', h);\nmodule.exports = express.Router().use('/leaf', leaf);\n",
    })
    assert ("GET", "/p/leaf/z") in got
