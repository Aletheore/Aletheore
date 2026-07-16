# Veridion API Endpoint Mapping + Live Health Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add static API endpoint mapping (Flask/FastAPI/Django/Express) as a new evidence
block, wired through query/MCP/diff, then a GET-only live health-check command layered on top.

**Architecture:** Phase 1 is a new `veridion/endpoints.py` module that walks the same
tree-sitter parse trees the module graph already builds, using per-framework pattern-matchers
on decorators (Flask/FastAPI) and calls (Django/Express), wired into `evidence.py`/`cli.py`/
`query.py`/`mcp_server.py`/`history.py` exactly the way the `licenses` feature was. Phase 2 is
a new `veridion/healthcheck.py` module that reads the endpoint map back out of evidence and
sends real GET requests to a user-supplied base URL, exposed as its own CLI command and MCP
tool — deliberately never folded into `scan`/`audit`/`diff`.

**Tech Stack:** Python 3.11+, tree-sitter (already-installed grammars: `tree-sitter-python`,
`tree-sitter-javascript`, `tree-sitter-typescript`), stdlib `urllib.request` + `certifi` for
live HTTP calls (matches the existing pattern in `vulnerabilities.py`/`licenses.py` — no new
dependency).

## Global Constraints

- Package/CLI name stays `veridion` throughout this plan (confirmed explicitly by the project
  owner — a separate, unresolved naming question is being tracked outside this plan and does
  not block this work).
- No new entries in `prototype/pyproject.toml`'s `dependencies` — everything needed (tree-sitter
  grammars, `certifi`, stdlib `urllib`/`ssl`/`re`/`time`/`datetime`) is already there.
- Every new evidence-producing function returns a plain `dict`/`list` (JSON-serializable) — no
  custom classes — matching every existing evidence block.
- `map_api_endpoints(repo_path: Path) -> dict` and `run_healthcheck(endpoints: list[dict],
  base_url: str, timeout: float = 5.0) -> dict` are the two public entry points later tasks
  depend on; their signatures are fixed by this header and must not change once Task 4 (for the
  first) and Task 8 (for the second) land.
- Endpoint entries are always a dict with exactly these keys: `method` (`str | None` — a real
  HTTP verb in upper-case, `"ANY"` for Django/Express's method-agnostic registration, or `None`
  for an unresolved routing-indirection entry), `path` (`str`, literal as written in source,
  never normalized across frameworks), `framework` (`"flask" | "flask_or_fastapi" | "django" |
  "express"`), `file` (repo-relative `str`), `line` (`int`, 1-indexed), `handler` (`str`), and
  `unresolved` (`bool`).
- Iteration order must stay deterministic (reuse `_iter_source_files`, which already sorts) —
  this is what keeps `evidence.json` reproducible for the same repo content, a property every
  other block already holds.

---

## Phase 1: Static Endpoint Mapping

### Task 1: Flask + FastAPI decorator route extraction

**Files:**
- Create: `prototype/veridion/endpoints.py`
- Test: `prototype/tests/test_endpoints.py`

**Interfaces:**
- Produces: `_extract_flask_fastapi_routes(root: Node, source: bytes, rel_path: str) -> list[dict]`
  and the shared helper `_string_literal_text(node: Node, source: bytes) -> str`, both used by
  Task 4's orchestration and reused by Task 2/3's extractors.

The real tree-sitter-python shape for `@app.route("/users/<int:id>", methods=["GET", "POST"])`
followed by `def get_user(id): ...` (verified live, not assumed):

```
decorated_definition
  decorator
    @
    call
      function: attribute (object: identifier "app", attribute: identifier "route")
      arguments: argument_list
        string "/users/<int:id>"
        keyword_argument (name: identifier "methods", value: list [string "GET", string "POST"])
  definition: function_definition (name: identifier "get_user", ...)
```

`decorated_definition.child_by_field_name("definition")` gives the function directly.
`call.child_by_field_name("function")` / `.child_by_field_name("arguments")` and
`attribute.child_by_field_name("object")` / `.child_by_field_name("attribute")` are all real,
verified field names.

`@app.route(...)` is Flask-specific (labeled `"flask"`). `@app.get/post/put/delete/patch(...)`
is syntactically identical between Flask 2.x+ and FastAPI — rather than guess wrong
confidently, these are labeled `"flask_or_fastapi"`, an honest label for a real ambiguity.

- [ ] **Step 1: Write the failing test**

```python
# prototype/tests/test_endpoints.py
from tree_sitter import Parser

from veridion.scanner.graph import PY_LANGUAGE
from veridion.endpoints import _extract_flask_fastapi_routes


def parse_python(source: str):
    parser = Parser()
    parser.language = PY_LANGUAGE
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
        }
    ]


def test_extract_flask_fastapi_ignores_non_route_decorators():
    root, source = parse_python(
        "@staticmethod\ndef helper():\n    pass\n"
    )

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert entries == []


def test_extract_flask_fastapi_handles_multiple_decorators_on_one_function():
    root, source = parse_python(
        "@app.get(\"/a\")\n@some_other_decorator\ndef handler():\n    pass\n"
    )

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert len(entries) == 1
    assert entries[0]["path"] == "/a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd prototype && python -m pytest tests/test_endpoints.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'veridion.endpoints'`

- [ ] **Step 3: Write the implementation**

```python
# prototype/veridion/endpoints.py
from pathlib import Path

from tree_sitter import Node, Parser

_ROUTE_VERB_METHODS = {"get", "post", "put", "delete", "patch"}


def _string_literal_text(node: Node, source: bytes) -> str:
    raw = source[node.start_byte : node.end_byte].decode()
    return raw.strip("'\"")


def _extract_flask_fastapi_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "decorated_definition":
            definition = n.child_by_field_name("definition")
            handler = "unknown"
            if definition is not None and definition.type == "function_definition":
                name_node = definition.child_by_field_name("name")
                if name_node is not None:
                    handler = source[name_node.start_byte : name_node.end_byte].decode()

            for decorator in (c for c in n.children if c.type == "decorator"):
                call = next(
                    (c for c in decorator.named_children if c.type == "call"), None
                )
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
                path_node = next(
                    (a for a in args.named_children if a.type == "string"), None
                )
                if path_node is None:
                    continue
                path = _string_literal_text(path_node, source)
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
                                _string_literal_text(item, source)
                                for item in value.named_children
                                if item.type == "string"
                            ]
                    for method in methods:
                        entries.append(
                            {
                                "method": method.upper(),
                                "path": path,
                                "framework": "flask",
                                "file": rel_path,
                                "line": line,
                                "handler": handler,
                                "unresolved": False,
                            }
                        )
                elif attribute_name in _ROUTE_VERB_METHODS:
                    entries.append(
                        {
                            "method": attribute_name.upper(),
                            "path": path,
                            "framework": "flask_or_fastapi",
                            "file": rel_path,
                            "line": line,
                            "handler": handler,
                            "unresolved": False,
                        }
                    )
        for child in n.children:
            walk(child)

    walk(root)
    return entries
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd prototype && python -m pytest tests/test_endpoints.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/endpoints.py prototype/tests/test_endpoints.py
git commit -m "feat: extract Flask/FastAPI routes from decorator syntax"
```

---

### Task 2: Django `urlpatterns` route extraction

**Files:**
- Modify: `prototype/veridion/endpoints.py`
- Test: `prototype/tests/test_endpoints.py`

**Interfaces:**
- Consumes: `_string_literal_text` from Task 1.
- Produces: `_extract_django_routes(root: Node, source: bytes, rel_path: str) -> list[dict]`.

Verified real AST for `urlpatterns = [path('users/<int:id>/', views.get_user, name="x"),
re_path(r'^items/$', views.list_items), include("myapp.urls")]`: an `assignment` node with
`child_by_field_name("left")` == `identifier "urlpatterns"` and `child_by_field_name("right")`
== a `list` node whose named children are `call` nodes (`function` field is a plain
`identifier` — `path`/`re_path`/`include` — not an `attribute`, unlike Flask/FastAPI).

- [ ] **Step 1: Write the failing test**

```python
# append to prototype/tests/test_endpoints.py
from veridion.endpoints import _extract_django_routes


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
        }
    ]


def test_extract_django_re_path_call():
    root, source = parse_python(
        "urlpatterns = [re_path(r'^items/$', views.list_items)]\n"
    )

    entries = _extract_django_routes(root, source, "app/urls.py")

    assert len(entries) == 1
    assert entries[0]["path"] == "^items/$"
    assert entries[0]["handler"] == "views.list_items"


def test_extract_django_include_is_recorded_as_unresolved():
    root, source = parse_python(
        'urlpatterns = [include("myapp.urls")]\n'
    )

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
        }
    ]


def test_extract_django_ignores_non_urlpatterns_assignments():
    root, source = parse_python("app_name = 'myapp'\n")

    entries = _extract_django_routes(root, source, "app/urls.py")

    assert entries == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd prototype && python -m pytest tests/test_endpoints.py -k django -v`
Expected: FAIL with `ImportError: cannot import name '_extract_django_routes'`

- [ ] **Step 3: Write the implementation**

```python
# append to prototype/veridion/endpoints.py

_DJANGO_ROUTE_FUNCS = {"path", "re_path"}


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
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd prototype && python -m pytest tests/test_endpoints.py -k django -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/endpoints.py prototype/tests/test_endpoints.py
git commit -m "feat: extract Django urlpatterns routes, record include() as unresolved"
```

---

### Task 3: Express route extraction (JS/TS)

**Files:**
- Modify: `prototype/veridion/endpoints.py`
- Test: `prototype/tests/test_endpoints.py`

**Interfaces:**
- Produces: `_extract_express_routes(root: Node, source: bytes, rel_path: str) -> list[dict]`.

Verified real tree-sitter-javascript AST for `app.get("/users", handler)`: a `call_expression`
whose `function` field is a `member_expression` (fields `object` → `identifier "app"`,
`property` → `property_identifier "get"`) and whose `arguments` field's first named child is a
`string` node.

- [ ] **Step 1: Write the failing test**

```python
# append to prototype/tests/test_endpoints.py
from veridion.scanner.graph import JS_LANGUAGE
from veridion.endpoints import _extract_express_routes


def parse_js(source: str):
    parser = Parser()
    parser.language = JS_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


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
        }
    ]


def test_extract_express_ignores_unrelated_method_calls():
    root, source = parse_js('res.send("ok");\napp.listen(3000);\n')

    entries = _extract_express_routes(root, source, "server.js")

    assert entries == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd prototype && python -m pytest tests/test_endpoints.py -k express -v`
Expected: FAIL with `ImportError: cannot import name '_extract_express_routes'`

- [ ] **Step 3: Write the implementation**

```python
# append to prototype/veridion/endpoints.py

_EXPRESS_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "all"}


def _js_string_literal_text(node: Node, source: bytes) -> str:
    raw = source[node.start_byte : node.end_byte].decode()
    return raw.strip("'\"")


def _express_handler_label(node: Node | None, source: bytes) -> str:
    if node is None:
        return "unknown"
    if node.type == "identifier":
        return source[node.start_byte : node.end_byte].decode()
    return "<inline handler>"


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
                        line = n.start_point[0] + 1
                        handler_node = named[1] if len(named) > 1 else None

                        if method_name in _EXPRESS_ROUTE_METHODS:
                            entries.append(
                                {
                                    "method": "ANY" if method_name == "all" else method_name.upper(),
                                    "path": path,
                                    "framework": "express",
                                    "file": rel_path,
                                    "line": line,
                                    "handler": _express_handler_label(handler_node, source),
                                    "unresolved": False,
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
                                }
                            )
        for child in n.children:
            walk(child)

    walk(root)
    return entries
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd prototype && python -m pytest tests/test_endpoints.py -k express -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/endpoints.py prototype/tests/test_endpoints.py
git commit -m "feat: extract Express routes, record mounted routers as unresolved"
```

---

### Task 4: `map_api_endpoints` orchestration, verified against real running apps

**Files:**
- Modify: `prototype/veridion/endpoints.py`
- Test: `prototype/tests/test_endpoints.py`

**Interfaces:**
- Consumes: `_extract_flask_fastapi_routes`, `_extract_django_routes`,
  `_extract_express_routes` (Tasks 1-3); `_iter_source_files`, `_rel`, `PY_LANGUAGE`,
  `JS_LANGUAGE`, `TS_LANGUAGE`, `TSX_LANGUAGE` from `veridion.scanner.graph`.
- Produces: `map_api_endpoints(repo_path: Path) -> dict` returning
  `{"checked": True, "endpoints": [...]}`. This is the function `evidence.py` calls in Task 5.

Django route files are conventionally named `urls.py`; only files with that exact name are
checked for `urlpatterns` (avoids false positives from an unrelated variable named
`urlpatterns` elsewhere, and matches how the rest of Veridion's detection is convention-based,
e.g. `detect_policy_docs` matching on filename).

- [ ] **Step 1: Write the failing test (fixture-based, no live app yet)**

```python
# append to prototype/tests/test_endpoints.py
from veridion.endpoints import map_api_endpoints


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd prototype && python -m pytest tests/test_endpoints.py -k map_api_endpoints -v`
Expected: FAIL with `ImportError: cannot import name 'map_api_endpoints'`

- [ ] **Step 3: Write the implementation**

```python
# top of prototype/veridion/endpoints.py, add these imports
from pathlib import Path

from veridion.scanner.graph import (
    JS_LANGUAGE,
    PY_LANGUAGE,
    TS_LANGUAGE,
    TSX_LANGUAGE,
    _iter_source_files,
    _rel,
)

# append at the end of prototype/veridion/endpoints.py

def map_api_endpoints(repo_path: Path) -> dict:
    endpoints: list[dict] = []

    py_parser = Parser()
    py_parser.language = PY_LANGUAGE
    js_parser = Parser()
    js_parser.language = JS_LANGUAGE
    ts_parser = Parser()
    ts_parser.language = TS_LANGUAGE
    tsx_parser = Parser()
    tsx_parser.language = TSX_LANGUAGE

    for path in _iter_source_files(repo_path):
        rel_path = _rel(repo_path, path)
        suffix = path.suffix

        if suffix == ".py":
            source = path.read_bytes()
            tree = py_parser.parse(source)
            endpoints.extend(
                _extract_flask_fastapi_routes(tree.root_node, source, rel_path)
            )
            if path.name == "urls.py":
                endpoints.extend(_extract_django_routes(tree.root_node, source, rel_path))
        elif suffix in (".js", ".jsx"):
            source = path.read_bytes()
            tree = js_parser.parse(source)
            endpoints.extend(_extract_express_routes(tree.root_node, source, rel_path))
        elif suffix == ".ts":
            source = path.read_bytes()
            tree = ts_parser.parse(source)
            endpoints.extend(_extract_express_routes(tree.root_node, source, rel_path))
        elif suffix == ".tsx":
            source = path.read_bytes()
            tree = tsx_parser.parse(source)
            endpoints.extend(_extract_express_routes(tree.root_node, source, rel_path))

    return {"checked": True, "endpoints": endpoints}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd prototype && python -m pytest tests/test_endpoints.py -v`
Expected: all tests in the file pass (17 so far)

- [ ] **Step 5: Verify against real running Flask, FastAPI, Django, and Express apps**

This is not a pytest step — it's the same live-verification discipline every prior language
addition in this project used (real `cargo build`/`javac`/`dotnet run`, not just fixtures).
Run each of these in a scratch directory, actually install the real framework, and confirm both
that the app runs for real and that `map_api_endpoints` matches its real routes exactly:

```bash
mkdir -p /tmp/veridion-endpoint-check/flaskapp && cd /tmp/veridion-endpoint-check/flaskapp
pip install flask
cat > app.py <<'EOF'
from flask import Flask
app = Flask(__name__)

@app.route("/users", methods=["GET", "POST"])
def users():
    return "ok"

@app.get("/health")
def health():
    return "ok"
EOF
python -c "
import subprocess, time, urllib.request
p = subprocess.Popen(['flask', '--app', 'app', 'run', '--port', '5001'])
time.sleep(2)
print(urllib.request.urlopen('http://127.0.0.1:5001/health').status)
p.terminate()
"
python -c "
from pathlib import Path
from veridion.endpoints import map_api_endpoints
result = map_api_endpoints(Path('.'))
print(result)
assert {'GET', 'POST'} <= {e['method'] for e in result['endpoints']}
"
```

Expected: the Flask dev server actually starts and returns HTTP 200 for `/health`, and
`map_api_endpoints` reports both `/users` (GET+POST) and `/health` (GET) entries.

Repeat the same pattern for a minimal `fastapi`+`uvicorn` app (`@app.get`/`@app.post` on an
`APIRouter` or `FastAPI()` instance), a minimal `django` project (`django-admin startproject`,
add a real `urls.py` with `path()` entries, `python manage.py runserver`), and a minimal
`express` app (`npm install express`, `app.get(...)`, `node server.js &`) — for each, actually
start the real server, confirm it responds, then run `map_api_endpoints` and confirm the
extracted entries match that framework's real routes.

- [ ] **Step 6: Commit**

```bash
git add prototype/veridion/endpoints.py prototype/tests/test_endpoints.py
git commit -m "feat: map_api_endpoints orchestrates all four framework extractors"
```

---

### Task 5: Wire endpoint mapping into `evidence.py` and `cli.py`

**Files:**
- Modify: `prototype/veridion/evidence.py`
- Modify: `prototype/veridion/cli.py`
- Test: `prototype/tests/test_evidence.py`, `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `map_api_endpoints` from Task 4.
- Produces: `scan_repository(..., map_endpoints: bool = True)` now sets
  `evidence["repository"]["api_endpoints"]`. `_scan`/`_audit` in `cli.py` gain a `map_endpoints`
  parameter threaded the same way `check_licenses` already is (see `evidence.py:23-28` and
  `cli.py:41-68` for the exact existing pattern being extended).

- [ ] **Step 1: Write the failing tests**

```python
# append to prototype/tests/test_evidence.py

def test_scan_repository_includes_api_endpoints_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text('@app.route("/users")\ndef list_users():\n    pass\n')

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert evidence["repository"]["api_endpoints"]["checked"] is True
    paths = {e["path"] for e in evidence["repository"]["api_endpoints"]["endpoints"]}
    assert "/users" in paths


def test_scan_repository_skips_endpoint_mapping_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text('@app.route("/users")\ndef list_users():\n    pass\n')

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False, map_endpoints=False)

    assert evidence["repository"]["api_endpoints"] == {
        "checked": False,
        "reason": "skipped (--no-map-endpoints)",
        "endpoints": [],
    }
```

```python
# append to prototype/tests/test_cli.py

def test_main_scan_threads_no_map_endpoints_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "app.py").write_text('@app.route("/users")\ndef list_users():\n    pass\n')
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo), "--no-map-endpoints"])

    main()

    evidence = json.loads((repo / ".veridion" / "evidence.json").read_text())
    assert evidence["repository"]["api_endpoints"]["checked"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_evidence.py -k api_endpoints tests/test_cli.py -k map_endpoints -v`
Expected: FAIL — `KeyError: 'api_endpoints'` and `unrecognized arguments: --no-map-endpoints`

- [ ] **Step 3: Wire `evidence.py`**

In `prototype/veridion/evidence.py`, add the import alongside the existing ones (line 7):

```python
from veridion.endpoints import map_api_endpoints
```

Change the `scan_repository` signature (currently lines 23-28) to:

```python
def scan_repository(
    repo_path: Path,
    check_vulnerabilities: bool = True,
    scan_git_history: bool = True,
    check_licenses: bool = True,
    map_endpoints: bool = True,
) -> dict:
```

Add, right after the existing `check_licenses` block (currently lines 61-69):

```python
    if map_endpoints:
        api_endpoints_data = map_api_endpoints(repo_path)
    else:
        api_endpoints_data = {
            "checked": False,
            "reason": "skipped (--no-map-endpoints)",
            "endpoints": [],
        }
```

And add `"api_endpoints": api_endpoints_data,` to the `"repository"` dict in the return value
(alongside `"modules"`, `"dependency_graph"`, `"unparseable_files"` — currently lines 82-84).

- [ ] **Step 4: Wire `cli.py`**

Update `_scan` (currently lines 41-56):

```python
def _scan(
    repo_path: str,
    check_vulnerabilities: bool,
    scan_git_history: bool,
    check_licenses: bool = True,
    map_endpoints: bool = True,
) -> tuple[int, dict, Path]:
    repo = Path(repo_path).resolve()
    print(f"Scanning {repo}...")
    evidence = scan_repository(
        repo,
        check_vulnerabilities=check_vulnerabilities,
        scan_git_history=scan_git_history,
        check_licenses=check_licenses,
        map_endpoints=map_endpoints,
    )
    evidence_path = write_evidence(evidence, repo)
    print(f"Evidence written to {evidence_path}")
    snapshot_path = save_snapshot(evidence, repo)
    print(f"Snapshot saved to {snapshot_path}")
    return 0, evidence, evidence_path
```

Update `_audit` (currently lines 59-68) the same way, adding `map_endpoints: bool = True` to its
signature and threading it into the `_scan(...)` call inside it.

Add to both `audit_parser` and `scan_parser` (currently lines 239-245 and 263-269), right after
the existing `--no-check-licenses` block:

```python
    audit_parser.add_argument(
        "--no-map-endpoints",
        dest="map_endpoints",
        action="store_false",
        default=True,
        help="skip static API endpoint mapping (on by default)",
    )
```

(and the identical block on `scan_parser`).

Update the dispatch calls in `main()` (currently lines 324-336) to pass `args.map_endpoints`:
as the 6th positional argument to `_audit(...)` (which has 6 total params after this change:
`repo_path, forced_agent, check_vulnerabilities, scan_git_history, check_licenses,
map_endpoints`), and as the 5th positional argument to both call sites of `_scan(...)` — once
inside `_audit` itself, and once in the `"scan"` command branch of `main()` (which has 5 total
params after this change: `repo_path, check_vulnerabilities, scan_git_history, check_licenses,
map_endpoints`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_evidence.py tests/test_cli.py -v`
Expected: all pass. Note: `test_main_audit_invokes_audit_flow` in `test_cli.py:103-108` asserts
`mock_audit.assert_called_once_with(str(tmp_path), "claude", True, True, True)` — this now
needs a 6th `True` appended (for the new `map_endpoints` default) or it will fail on argument
count. Update that one assertion to
`mock_audit.assert_called_once_with(str(tmp_path), "claude", True, True, True, True)`.

- [ ] **Step 6: Commit**

```bash
git add prototype/veridion/evidence.py prototype/veridion/cli.py prototype/tests/test_evidence.py prototype/tests/test_cli.py
git commit -m "feat: wire API endpoint mapping into scan_repository and the CLI"
```

---

### Task 6: `veridion query endpoints` and the `veridion_endpoints` MCP tool

**Files:**
- Modify: `prototype/veridion/query.py`
- Modify: `prototype/veridion/mcp_server.py`
- Test: `prototype/tests/test_query.py`, `prototype/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `find_endpoints(evidence, target) -> dict` registered in `QUERY_FUNCTIONS["endpoints"]
  = (find_endpoints, False)`; MCP tool `veridion_endpoints` (mirrors exactly how
  `veridion_licenses` was added — see `mcp_server.py:29-40`'s `_TOOL_NAME_TO_QUERY_KIND` dict).

- [ ] **Step 1: Write the failing tests**

```python
# in prototype/tests/test_query.py, add to make_evidence()'s "repository" dict:
        "api_endpoints": {
            "checked": True,
            "endpoints": [
                {"method": "GET", "path": "/users", "framework": "flask", "file": "app.py",
                 "line": 1, "handler": "list_users", "unresolved": False}
            ],
        },

# add the import
from veridion.query import find_endpoints

# add the test
def test_find_endpoints_returns_the_whole_block_ignoring_target():
    result = find_endpoints(make_evidence(), None)
    assert result == make_evidence()["repository"]["api_endpoints"]

# update the existing registry test to include "endpoints": False in `expected`
```

```python
# in prototype/tests/test_mcp_server.py:
# 1. add "api_endpoints": {"checked": True, "endpoints": []} to the "repository" dict
#    in make_repo_with_evidence()
# 2. add "veridion_endpoints" to the `expected` set in
#    test_build_server_registers_all_11_wrapper_tools (rename to ..._12_wrapper_tools)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_query.py tests/test_mcp_server.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_endpoints'` and a missing-tool-name
assertion failure.

- [ ] **Step 3: Implement `find_endpoints` in `query.py`**

Add after `find_licenses` (currently `query.py:59-60`):

```python
def find_endpoints(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["api_endpoints"]
```

Add `"endpoints": (find_endpoints, False),` to `QUERY_FUNCTIONS` (currently `query.py:74-85`).

- [ ] **Step 4: Register the MCP tool in `mcp_server.py`**

Add `"veridion_endpoints": "endpoints",` to `_TOOL_NAME_TO_QUERY_KIND` (currently
`mcp_server.py:29-40`) — no other change needed, since `_register_query_wrapper_tools` already
iterates this dict generically.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_query.py tests/test_mcp_server.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add prototype/veridion/query.py prototype/veridion/mcp_server.py prototype/tests/test_query.py prototype/tests/test_mcp_server.py
git commit -m "feat: add 'endpoints' query kind and veridion_endpoints MCP tool"
```

---

### Task 7: Track endpoint changes in `veridion diff`

**Files:**
- Modify: `prototype/veridion/history.py`
- Test: `prototype/tests/test_history.py`

**Interfaces:**
- Consumes: `_new_and_resolved` (already defined in `history.py:41-52`).
- Produces: `compute_diff(...)`'s curated result gains an `"endpoints": {"new": [...],
  "resolved": [...]}` key, plus a caveat when endpoint-mapping was toggled between the two
  scans being compared (same pattern as the existing vulnerability/history-scan caveats at
  `history.py:59-77`).

- [ ] **Step 1: Write the failing tests**

```python
# in prototype/tests/test_history.py, add to base_evidence()'s "repository" dict:
            "api_endpoints": {
                "checked": True,
                "endpoints": [
                    {"method": "GET", "path": "/users", "framework": "flask", "file": "app.py",
                     "line": 1, "handler": "list_users", "unresolved": False}
                ],
            },

def test_compute_diff_detects_a_new_endpoint():
    old = base_evidence()
    new = base_evidence()
    new["repository"]["api_endpoints"]["endpoints"].append(
        {"method": "POST", "path": "/users", "framework": "flask", "file": "app.py",
         "line": 5, "handler": "create_user", "unresolved": False}
    )

    diff = compute_diff(old, new)

    assert len(diff["endpoints"]["new"]) == 1
    assert diff["endpoints"]["new"][0]["path"] == "/users"
    assert diff["endpoints"]["new"][0]["method"] == "POST"
    assert diff["endpoints"]["resolved"] == []


def test_compute_diff_detects_a_resolved_endpoint():
    old = base_evidence()
    new = base_evidence()
    new["repository"]["api_endpoints"]["endpoints"] = []

    diff = compute_diff(old, new)

    assert len(diff["endpoints"]["resolved"]) == 1
    assert diff["endpoints"]["new"] == []


def test_compute_diff_caveat_fires_when_endpoint_mapping_toggled():
    old = base_evidence()
    old["repository"]["api_endpoints"]["checked"] = False
    old["repository"]["api_endpoints"]["endpoints"] = []
    new = base_evidence()

    diff = compute_diff(old, new)

    assert "caveats" in diff
    assert any("endpoint" in c for c in diff["caveats"])
```

Also update `test_compute_diff_reports_no_new_or_resolved_when_identical` to assert
`diff["endpoints"] == {"new": [], "resolved": []}` alongside the existing assertions.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_history.py -k endpoint -v`
Expected: FAIL with `KeyError: 'endpoints'`

- [ ] **Step 3: Implement in `history.py`**

Add, inside `_compute_curated_diff` (currently `history.py:55-119`), right after the existing
license/vulnerability-checked-state caveat block and before the `if caveats:` line:

```python
    old_endpoints_checked = old["repository"]["api_endpoints"]["checked"]
    new_endpoints_checked = new["repository"]["api_endpoints"]["checked"]
    if old_endpoints_checked != new_endpoints_checked:
        caveats.append(
            "API endpoint mapping state changed between scans "
            f"(was checked={old_endpoints_checked}, now checked={new_endpoints_checked}) - "
            "new/resolved endpoint findings below may reflect mapping being toggled on/off, "
            "not necessarily real changes"
        )
```

And add, alongside the existing `result["vulnerabilities"] = ...` /
`result["layer_violations"] = ...` lines:

```python
    new_endpoints, resolved_endpoints = _new_and_resolved(
        old["repository"]["api_endpoints"]["endpoints"],
        new["repository"]["api_endpoints"]["endpoints"],
        ("method", "path"),
    )
    result["endpoints"] = {"new": new_endpoints, "resolved": resolved_endpoints}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_history.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/history.py prototype/tests/test_history.py
git commit -m "feat: track new/resolved API endpoints in veridion diff"
```

---

## Phase 2: Live Health Check

### Task 8: `run_healthcheck` core (GET-only, path-param substitution)

**Files:**
- Create: `prototype/veridion/healthcheck.py`
- Test: `prototype/tests/test_healthcheck.py`

**Interfaces:**
- Produces: `run_healthcheck(endpoints: list[dict], base_url: str, timeout: float = 5.0) ->
  dict`, returning `{"base_url": str, "checked_at": str, "results": [...]}`. This is what
  Task 9's CLI command and MCP tool call.

- [ ] **Step 1: Write the failing tests**

```python
# prototype/tests/test_healthcheck.py
import urllib.error
from unittest.mock import MagicMock, patch

from veridion.healthcheck import run_healthcheck


def _mock_response(status: int):
    mock = MagicMock()
    mock.status = status
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_run_healthcheck_reports_reachable_get_endpoint():
    endpoints = [
        {"method": "GET", "path": "/health", "framework": "flask", "file": "app.py",
         "line": 1, "handler": "health", "unresolved": False}
    ]

    with patch("veridion.healthcheck.urllib.request.urlopen", return_value=_mock_response(200)):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["base_url"] == "http://localhost:5000"
    assert len(result["results"]) == 1
    entry = result["results"][0]
    assert entry["status_code"] == 200
    assert entry["reachable"] is True
    assert entry["note"] is None


def test_run_healthcheck_substitutes_path_params_and_notes_it():
    endpoints = [
        {"method": "GET", "path": "/users/<int:id>", "framework": "flask", "file": "app.py",
         "line": 1, "handler": "get_user", "unresolved": False}
    ]

    with patch(
        "veridion.healthcheck.urllib.request.urlopen", return_value=_mock_response(404)
    ) as mock_urlopen:
        result = run_healthcheck(endpoints, "http://localhost:5000")

    called_url = mock_urlopen.call_args[0][0].full_url
    assert called_url == "http://localhost:5000/users/1"
    assert result["results"][0]["note"] == "path contains parameters, tested with placeholder value(s)"


def test_run_healthcheck_never_sends_non_get_methods():
    endpoints = [
        {"method": "POST", "path": "/users", "framework": "flask", "file": "app.py",
         "line": 1, "handler": "create_user", "unresolved": False}
    ]

    with patch("veridion.healthcheck.urllib.request.urlopen") as mock_urlopen:
        result = run_healthcheck(endpoints, "http://localhost:5000")

    mock_urlopen.assert_not_called()
    assert result["results"][0]["skipped"] is True
    assert result["results"][0]["reason"] == "only GET is health-checked"


def test_run_healthcheck_treats_any_method_as_get_checkable():
    endpoints = [
        {"method": "ANY", "path": "/items", "framework": "django", "file": "urls.py",
         "line": 1, "handler": "views.items", "unresolved": False}
    ]

    with patch("veridion.healthcheck.urllib.request.urlopen", return_value=_mock_response(200)):
        result = run_healthcheck(endpoints, "http://localhost:8000")

    assert result["results"][0]["skipped"] is False if "skipped" in result["results"][0] else True
    assert result["results"][0]["reachable"] is True


def test_run_healthcheck_skips_unresolved_indirection_entries():
    endpoints = [
        {"method": None, "path": "myapp.urls", "framework": "django", "file": "urls.py",
         "line": 1, "handler": "include(...)", "unresolved": True}
    ]

    with patch("veridion.healthcheck.urllib.request.urlopen") as mock_urlopen:
        result = run_healthcheck(endpoints, "http://localhost:8000")

    mock_urlopen.assert_not_called()
    assert result["results"][0]["skipped"] is True
    assert "unresolved" in result["results"][0]["reason"]


def test_run_healthcheck_reports_http_error_status_as_reachable():
    endpoints = [
        {"method": "GET", "path": "/missing", "framework": "flask", "file": "app.py",
         "line": 1, "handler": "x", "unresolved": False}
    ]

    with patch(
        "veridion.healthcheck.urllib.request.urlopen",
        side_effect=urllib.error.HTTPError("url", 404, "not found", {}, None),
    ):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["status_code"] == 404
    assert result["results"][0]["reachable"] is True


def test_run_healthcheck_reports_unreachable_on_connection_error():
    endpoints = [
        {"method": "GET", "path": "/x", "framework": "flask", "file": "app.py",
         "line": 1, "handler": "x", "unresolved": False}
    ]

    with patch(
        "veridion.healthcheck.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = run_healthcheck(endpoints, "http://localhost:9999")

    assert result["results"][0]["reachable"] is False
    assert result["results"][0]["status_code"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_healthcheck.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'veridion.healthcheck'`

- [ ] **Step 3: Write the implementation**

```python
# prototype/veridion/healthcheck.py
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import certifi

# Same reasoning as vulnerabilities.py/licenses.py: certifi's CA bundle explicitly.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

_PATH_PARAM_PATTERNS = (
    re.compile(r"<[^>]+>"),                   # Flask: <int:id> or <id>
    re.compile(r"\{[^}]+\}"),                 # FastAPI: {id}
    re.compile(r":[A-Za-z_][A-Za-z0-9_]*"),   # Express: :id
)


def _substitute_path_params(path: str) -> tuple[str, bool]:
    substituted = path
    had_params = False
    for pattern in _PATH_PARAM_PATTERNS:
        if pattern.search(substituted):
            had_params = True
            substituted = pattern.sub("1", substituted)
    return substituted, had_params


def run_healthcheck(endpoints: list[dict], base_url: str, timeout: float = 5.0) -> dict:
    results: list[dict] = []

    for endpoint in endpoints:
        if endpoint.get("unresolved"):
            results.append(
                {
                    "method": endpoint.get("method"),
                    "path": endpoint["path"],
                    "skipped": True,
                    "reason": "unresolved routing indirection (include/mount), not a concrete endpoint",
                }
            )
            continue

        method = endpoint.get("method")
        if method not in ("GET", "ANY"):
            results.append(
                {
                    "method": method,
                    "path": endpoint["path"],
                    "skipped": True,
                    "reason": "only GET is health-checked",
                }
            )
            continue

        resolved_path, had_params = _substitute_path_params(endpoint["path"])
        url = base_url.rstrip("/") + "/" + resolved_path.lstrip("/")

        entry = {
            "method": "GET",
            "path": endpoint["path"],
            "note": (
                "path contains parameters, tested with placeholder value(s)"
                if had_params
                else None
            ),
        }

        start = time.monotonic()
        try:
            request = urllib.request.Request(url)
            with urllib.request.urlopen(
                request, timeout=timeout, context=_SSL_CONTEXT
            ) as response:
                entry["status_code"] = response.status
                entry["reachable"] = True
        except urllib.error.HTTPError as exc:
            entry["status_code"] = exc.code
            entry["reachable"] = True
        except (urllib.error.URLError, TimeoutError, OSError):
            entry["status_code"] = None
            entry["reachable"] = False
        entry["latency_ms"] = round((time.monotonic() - start) * 1000, 1)

        results.append(entry)

    return {
        "base_url": base_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_healthcheck.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/healthcheck.py prototype/tests/test_healthcheck.py
git commit -m "feat: run_healthcheck sends GET-only live requests to mapped endpoints"
```

---

### Task 9: `veridion healthcheck` CLI command and `veridion_healthcheck` MCP tool

**Files:**
- Modify: `prototype/veridion/cli.py`
- Modify: `prototype/veridion/mcp_server.py`
- Test: `prototype/tests/test_cli.py`, `prototype/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `run_healthcheck` (Task 8).
- Produces: a minimal `save_healthcheck(result: dict, repo_path: Path) -> Path` stub, written
  as part of this task's Step 3 (no rotation yet — it just writes one file). Task 10
  immediately following this one replaces this stub's body with a rotation-aware version
  (same name, same call sites, an added `keep: int = 20` parameter with a default so nothing
  calling it needs to change) — persistence is split into its own task only because rotation
  has its own tests worth isolating, not because this task's tests depend on Task 10 existing
  first.

- [ ] **Step 1: Write the failing tests**

```python
# append to prototype/tests/test_cli.py

def test_main_healthcheck_reports_results(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "app.py").write_text('@app.route("/health")\ndef health():\n    pass\n')
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])
    main()
    capsys.readouterr()

    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    monkeypatch.setattr(
        sys,
        "argv",
        ["veridion", "healthcheck", str(repo), "--base-url", "http://localhost:5000"],
    )
    with patch("veridion.healthcheck.urllib.request.urlopen", return_value=response):
        exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "/health" in captured.out
    assert "200" in captured.out


def test_main_healthcheck_without_evidence_errors_clearly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["veridion", "healthcheck", str(tmp_path), "--base-url", "http://localhost:5000"],
    )
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "veridion scan" in captured.out
```

```python
# append to prototype/tests/test_mcp_server.py

@pytest.mark.asyncio
async def test_veridion_healthcheck_tool_returns_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".veridion" / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["repository"]["api_endpoints"] = {
        "checked": True,
        "endpoints": [
            {"method": "GET", "path": "/health", "framework": "flask", "file": "app.py",
             "line": 1, "handler": "health", "unresolved": False}
        ],
    }
    evidence_path.write_text(json.dumps(evidence))
    server = build_server(repo)

    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch("veridion.healthcheck.urllib.request.urlopen", return_value=response):
        result = await server.call_tool(
            "veridion_healthcheck", {"base_url": "http://localhost:5000"}
        )

    body = tool_result_body(result)["result"]
    assert body["results"][0]["status_code"] == 200
```

Add `from unittest.mock import MagicMock, patch` to `test_mcp_server.py`'s imports if not
already present (it isn't currently — check the top of the file before adding).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_cli.py -k healthcheck tests/test_mcp_server.py -k healthcheck -v`
Expected: FAIL — `unrecognized arguments: healthcheck` and `unknown tool: veridion_healthcheck`

- [ ] **Step 3: Add the CLI command**

In `cli.py`, add the import: `from veridion.healthcheck import run_healthcheck,
save_healthcheck`. First, define `save_healthcheck` in `healthcheck.py` as this task's minimal
working stub (Task 10 replaces its body with a rotation-aware version, same signature plus a
defaulted `keep` parameter):

```python
# add to prototype/veridion/healthcheck.py for this task
import json
from pathlib import Path


def _healthchecks_dir(repo_path: Path) -> Path:
    return repo_path / ".veridion" / "healthchecks"


def save_healthcheck(result: dict, repo_path: Path) -> Path:
    healthchecks_dir = _healthchecks_dir(repo_path)
    healthchecks_dir.mkdir(parents=True, exist_ok=True)
    safe_name = result["checked_at"].replace(":", "-")
    path = healthchecks_dir / f"{safe_name}.json"
    path.write_text(json.dumps(result, indent=2))
    return path
```

Add to `cli.py`, after `_diff` and before `_mcp`:

```python
def _healthcheck(repo_path: str, base_url: str) -> int:
    repo = Path(repo_path).resolve()
    evidence_path = repo / ".veridion" / "evidence.json"
    if not evidence_path.exists():
        print(f"error: no evidence found at {evidence_path}")
        print(f"Run 'veridion scan {repo}' first.")
        return 1

    evidence = json.loads(evidence_path.read_text())
    endpoints = evidence["repository"].get("api_endpoints", {}).get("endpoints", [])
    result = run_healthcheck(endpoints, base_url)
    save_healthcheck(result, repo)

    for entry in result["results"]:
        method = entry.get("method") or "?"
        if entry.get("skipped"):
            print(f"{method:6} {entry['path']:40} SKIPPED ({entry['reason']})")
        else:
            status = entry["status_code"] if entry["reachable"] else "UNREACHABLE"
            note = f" ({entry['note']})" if entry.get("note") else ""
            print(f"{method:6} {entry['path']:40} {status} {entry['latency_ms']}ms{note}")

    return 0
```

Add the subparser in `main()`, after `dashboard_parser`:

```python
    healthcheck_parser = subparsers.add_parser(
        "healthcheck", help="GET-only live health check of mapped API endpoints"
    )
    healthcheck_parser.add_argument("path", nargs="?", default=".")
    healthcheck_parser.add_argument("--base-url", required=True, dest="base_url")
```

Add the dispatch in `main()`, after the `dashboard` branch:

```python
    if args.command == "healthcheck":
        return _healthcheck(args.path, args.base_url)
```

- [ ] **Step 4: Add the MCP tool**

In `mcp_server.py`, add the import: `from veridion.healthcheck import run_healthcheck,
save_healthcheck`. Add, after `_register_scan_tool`:

```python
def _register_healthcheck_tool(mcp_instance: FastMCP, repo_path: Path) -> None:
    @mcp_instance.tool(name="veridion_healthcheck")
    def veridion_healthcheck(base_url: str) -> dict:
        """GET-only live health check of this repo's mapped API endpoints against a running instance."""
        evidence = read_evidence(repo_path)
        endpoints = evidence["repository"].get("api_endpoints", {}).get("endpoints", [])
        result = run_healthcheck(endpoints, base_url)
        save_healthcheck(result, repo_path)
        return {"result": result}
```

Register it in `build_server` (currently `mcp_server.py:183-190`):

```python
    _register_healthcheck_tool(mcp_instance, repo_path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_cli.py tests/test_mcp_server.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add prototype/veridion/cli.py prototype/veridion/mcp_server.py prototype/veridion/healthcheck.py prototype/tests/test_cli.py prototype/tests/test_mcp_server.py
git commit -m "feat: add veridion healthcheck CLI command and MCP tool"
```

---

### Task 10: Health-check persistence with rotation, reusing `history.py`'s helper

**Files:**
- Modify: `prototype/veridion/history.py`
- Modify: `prototype/veridion/healthcheck.py`
- Test: `prototype/tests/test_history.py`, `prototype/tests/test_healthcheck.py`

**Interfaces:**
- Produces: a shared `_save_json_with_rotation(data: dict, directory: Path, timestamp: str,
  keep: int) -> Path` in `history.py`, used by both `save_snapshot` (refactored, same external
  behavior) and `healthcheck.py`'s `save_healthcheck` (given a `keep: int = 20` parameter,
  replacing the no-rotation stub from Task 9).

This avoids duplicating `save_snapshot`'s collision-suffix logic (currently `history.py:18-31`)
a second time for healthcheck runs.

- [ ] **Step 1: Write the failing test**

```python
# append to prototype/tests/test_healthcheck.py
from veridion.healthcheck import save_healthcheck


def test_save_healthcheck_rotates_at_21st_save_keeping_20_newest(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    for hour in range(21):
        save_healthcheck(
            {"base_url": "x", "checked_at": f"2026-07-16T{hour:02d}:00:00+00:00", "results": []},
            repo,
        )

    healthchecks_dir = repo / ".veridion" / "healthchecks"
    files = sorted(healthchecks_dir.glob("*.json"))
    assert len(files) == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd prototype && python -m pytest tests/test_healthcheck.py -k rotates -v`
Expected: FAIL — 21 files present, not 20 (no rotation yet)

- [ ] **Step 3: Extract the shared helper in `history.py`**

Replace `save_snapshot` (currently `history.py:18-31`):

```python
def _save_json_with_rotation(data: dict, directory: Path, timestamp: str, keep: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = timestamp.replace(":", "-")
    path = directory / f"{safe_name}.json"
    suffix = 1
    while path.exists():
        path = directory / f"{safe_name}-{suffix}.json"
        suffix += 1
    path.write_text(json.dumps(data, indent=2))
    _rotate(directory, keep)
    return path


def save_snapshot(evidence: dict, repo_path: Path, keep: int = 20) -> Path:
    return _save_json_with_rotation(evidence, _history_dir(repo_path), evidence["scanned_at"], keep)
```

- [ ] **Step 4: Update `healthcheck.py`'s `save_healthcheck` to use it**

Replace the Task 9 stub:

```python
from veridion.history import _save_json_with_rotation


def _healthchecks_dir(repo_path: Path) -> Path:
    return repo_path / ".veridion" / "healthchecks"


def save_healthcheck(result: dict, repo_path: Path, keep: int = 20) -> Path:
    return _save_json_with_rotation(
        result, _healthchecks_dir(repo_path), result["checked_at"], keep
    )
```

- [ ] **Step 5: Run the full test suite to verify no regression**

Run: `cd prototype && python -m pytest tests/test_history.py tests/test_healthcheck.py -v`
Expected: all pass, including every pre-existing `test_history.py` test unchanged (confirms the
refactor preserved `save_snapshot`'s exact external behavior).

- [ ] **Step 6: Commit**

```bash
git add prototype/veridion/history.py prototype/veridion/healthcheck.py prototype/tests/test_healthcheck.py
git commit -m "refactor: share snapshot-rotation logic between scan history and healthchecks"
```

---

### Task 11: Full real-app verification and README/CHANGELOG update

**Files:**
- Modify: `prototype/README.md`
- Modify: `CHANGELOG.md`

Not a TDD task — this is the same live, end-to-end verification discipline used to close out
every previous feature in this project (7 languages, secrets baseline, dependency licenses),
run manually rather than as a pytest assertion, because it depends on starting and hitting a
real live server.

- [ ] **Step 1: Real Flask end-to-end run**

```bash
mkdir -p /tmp/veridion-e2e/flaskapp && cd /tmp/veridion-e2e/flaskapp
pip install flask
cat > app.py <<'EOF'
from flask import Flask
app = Flask(__name__)

@app.get("/health")
def health():
    return "ok"

@app.route("/users", methods=["GET", "POST"])
def users():
    return "ok"
EOF
git init -q
flask --app app run --port 5002 &
sleep 2
veridion scan .
veridion query endpoints
veridion healthcheck . --base-url http://127.0.0.1:5002
kill %1
```

Expected: `veridion query endpoints` lists `/health` (GET) and `/users` (GET, POST) with
`framework: "flask_or_fastapi"` for `/health` and `"flask"` for `/users`. `veridion healthcheck`
reports `200` for both with real latency numbers, and reports `UNREACHABLE` if run again after
`kill %1` has taken effect (confirm this too, as the negative case).

- [ ] **Step 2: `veridion diff` shows a newly added endpoint**

```bash
cd /tmp/veridion-e2e/flaskapp
cat >> app.py <<'EOF'

@app.delete("/users/<int:id>")
def delete_user(id):
    return "ok"
EOF
veridion scan .
veridion query changes
```

Expected: `endpoints.new` contains the `DELETE /users/<int:id>` entry.

- [ ] **Step 3: Update documentation**

In `prototype/README.md`, update the feature list / commands section to mention:
`repository.api_endpoints` (Flask/FastAPI/Django/Express route mapping), `veridion query
endpoints` / `veridion_endpoints` MCP tool (now **15 tools, up from 14**), `--no-map-endpoints`,
and the new `veridion healthcheck --base-url <url>` command / `veridion_healthcheck` MCP tool,
explicitly noting it is GET-only and not part of the deterministic evidence model.

In `CHANGELOG.md`, add to `## Unreleased`:

```markdown
- Added static API endpoint mapping for Flask, FastAPI, Django, and Express as a new
  `repository.api_endpoints` evidence block, with a `veridion query endpoints` /
  `veridion_endpoints` MCP tool (15 tools, up from 14), a `--no-map-endpoints` flag, and
  tracking of added/removed endpoints in `veridion diff`.
- Added `veridion healthcheck --base-url <url>` and a matching `veridion_healthcheck` MCP tool:
  a GET-only live check of an app's mapped endpoints against a running instance. Deliberately
  kept outside the deterministic evidence/diff model, since it depends on live runtime state,
  not just repo content.
```

- [ ] **Step 4: Commit**

```bash
git add prototype/README.md CHANGELOG.md
git commit -m "docs: document API endpoint mapping and live health check"
```

## Success Criteria (from the spec, restated for final verification)

1. `veridion scan` against a real Flask/FastAPI repo produces `repository.api_endpoints`
   entries matching that repo's actual routes — verified in Task 4 Step 5 and Task 11 Step 1.
2. `veridion query endpoints` and `veridion_endpoints` both return that block — verified in
   Task 6.
3. `veridion diff` shows added/removed endpoints between two real scans — verified in Task 7
   and Task 11 Step 2.
4. `veridion healthcheck --base-url <url>` against a real running instance reports accurate
   status/latency, marks non-GET as skipped without ever calling them, and reports
   `reachable: False` cleanly on an unreachable target — verified in Task 8 and Task 11 Step 1.
