from pathlib import Path

from veridion.scanner.graph import build_module_graph


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
    assert "login" in auth["symbols"]["functions"]
    assert "AuthError" in auth["symbols"]["classes"]

    config = by_path["app/config.py"]
    assert "app/auth.py" in config["imported_by"]

    assert unparseable == []


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
    assert "add" in by_path["utils.ts"]["symbols"]["functions"]
    assert unparseable == []
