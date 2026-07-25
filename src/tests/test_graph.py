from pathlib import Path

from aletheore.scanner.graph import build_module_graph
from conftest import symbol_names


def make_python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    app = repo / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "config.py").write_text("SETTING = 1\n\ndef load():\n    return SETTING\n")
    (app / "auth.py").write_text(
        "from app import config\n\n\ndef login():\n    return config.load()\n\n\nclass AuthError(Exception):\n    pass\n"
    )
    (app / "routes.py").write_text("from app.auth import login\n\ndef handle():\n    return login()\n")
    return repo


def test_build_module_graph_extracts_python_imports_and_symbols(tmp_path):
    repo = make_python_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    assert "app/auth.py" in by_path
    auth = by_path["app/auth.py"]
    assert "app/config.py" in auth["imports"]
    assert "login" in symbol_names(auth["symbols"]["functions"])
    assert "AuthError" in symbol_names(auth["symbols"]["classes"])

    config = by_path["app/config.py"]
    assert "app/auth.py" in config["imported_by"]

    assert unparseable == []


def test_build_module_graph_records_symbol_line_bounds(tmp_path):
    repo = make_python_repo(tmp_path)
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    auth = by_path["app/auth.py"]

    login_fn = next(f for f in auth["symbols"]["functions"] if f["name"] == "login")
    assert login_fn["start_line"] == 4
    assert login_fn["end_line"] == 5

    auth_error_cls = next(c for c in auth["symbols"]["classes"] if c["name"] == "AuthError")
    assert auth_error_cls["start_line"] == 8
    assert auth_error_cls["end_line"] == 9


def test_build_module_graph_dependency_edges(tmp_path):
    repo = make_python_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}
    assert ("app/auth.py", "app/config.py") in edges
    assert ("app/routes.py", "app/auth.py") in edges


def test_build_module_graph_records_unparseable_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helper.swift").write_text("func hi() {}\n")
    modules, _, unparseable = build_module_graph(repo)
    assert modules == []
    assert unparseable == [{"path": "helper.swift", "reason": "no grammar registered for .swift"}]


def test_build_module_graph_extracts_javascript_imports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "utils.js").write_text("export function add(a, b) { return a + b; }\n")
    (repo / "index.js").write_text(
        "import { add } from './utils';\n\nfunction main() { return add(1, 2); }\n"
    )
    modules, dependency_graph, unparseable = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    assert "index.js" in by_path
    assert "utils.js" in by_path["index.js"]["imports"]
    assert unparseable == []


def test_build_module_graph_skips_non_source_files_silently(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    (repo / "data.json").write_text("{}")
    (repo / "logo.png").write_bytes(b"\x89PNG")
    (repo / "notes.md").write_text("# hi")
    modules, _, unparseable = build_module_graph(repo)
    assert unparseable == []
    assert {m["path"] for m in modules} == {"main.py"}


def test_build_module_graph_ignores_cache_and_build_dirs(tmp_path):
    repo = tmp_path / "repo"
    cache = repo / ".mypy_cache" / "3.12"
    cache.mkdir(parents=True)
    (cache / "module.data.json").write_text("{}")
    (repo / "dist").mkdir()
    (repo / "dist" / "bundle.js").write_text("console.log(1)")
    (repo / "main.py").write_text("x = 1\n")
    modules, _, unparseable = build_module_graph(repo)
    assert unparseable == []
    assert {m["path"] for m in modules} == {"main.py"}


def test_build_module_graph_extracts_typescript_imports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "utils.ts").write_text(
        "export function add(a: number, b: number): number { return a + b; }\n"
    )
    (repo / "index.ts").write_text(
        "import { add } from './utils';\n\nfunction main(): number { return add(1, 2); }\n"
    )
    modules, dependency_graph, unparseable = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    assert "index.ts" in by_path
    assert "utils.ts" in by_path["index.ts"]["imports"]
    assert "add" in symbol_names(by_path["utils.ts"]["symbols"]["functions"])
    assert unparseable == []


def test_build_module_graph_resolves_relative_imports(tmp_path):
    repo = tmp_path / "repo"
    app = repo / "app"
    routers = app / "routers"
    services = app / "services"
    routers.mkdir(parents=True)
    services.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (routers / "__init__.py").write_text("")
    (services / "__init__.py").write_text("")
    (app / "shared.py").write_text("def toplevel():\n    pass\n")
    (services / "sessions.py").write_text(
        "def collect_session_screenshots():\n    pass\n"
    )
    (routers / "helpers.py").write_text("def helper():\n    pass\n")
    (routers / "admin.py").write_text(
        "from ..services.sessions import collect_session_screenshots\n"
        "from . import helpers\n"
        "from .. import shared\n"
    )

    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    admin_imports = by_path["app/routers/admin.py"]["imports"]

    assert "app/services/sessions.py" in admin_imports
    assert "app/routers/helpers.py" in admin_imports
    assert "app/shared.py" in admin_imports


def test_build_module_graph_relative_sibling_import_does_not_become_parent_package(tmp_path):
    repo = tmp_path / "repo"
    app = repo / "app"
    routers = app / "routers"
    routers.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (routers / "__init__.py").write_text("")
    (routers / "helpers.py").write_text("def helper():\n    pass\n")
    (routers / "admin.py").write_text("from . import helpers\n")

    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    admin_imports = by_path["app/routers/admin.py"]["imports"]

    # a naive fix could turn "from . import helpers" (current package) into an
    # accidental "from .. import helpers" (parent package) if it inserts an extra
    # separator dot on top of the dot already present in "." - this must resolve
    # to the sibling module, not to app/__init__.py (the parent package)
    assert "app/routers/helpers.py" in admin_imports


def test_build_module_graph_resolves_absolute_imports_in_a_monorepo(tmp_path):
    # A monorepo can hold multiple independent Python projects, each with its own
    # top-level package one directory below the scanned root (repo/service_a/pkg_a/,
    # repo/service_b/pkg_b/) rather than directly inside it (repo/app/). Absolute
    # imports inside each project must resolve against that project's own root, not
    # the scanned repo root itself.
    repo = tmp_path / "repo"

    service_a = repo / "service_a"
    pkg_a = service_a / "pkg_a"
    pkg_a.mkdir(parents=True)
    (pkg_a / "__init__.py").write_text("")
    (pkg_a / "config.py").write_text("SETTING = 1\n")
    (pkg_a / "main.py").write_text("from pkg_a.config import SETTING\n")

    service_b = repo / "service_b"
    pkg_b = service_b / "pkg_b"
    pkg_b.mkdir(parents=True)
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "utils.py").write_text("def helper():\n    pass\n")
    (pkg_b / "app.py").write_text("import pkg_b.utils\n")

    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    assert "service_a/pkg_a/config.py" in by_path["service_a/pkg_a/main.py"]["imports"]
    assert "service_b/pkg_b/utils.py" in by_path["service_b/pkg_b/app.py"]["imports"]
    assert dependency_graph["edges"] != []
