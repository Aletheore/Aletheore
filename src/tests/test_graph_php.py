from pathlib import Path

from aletheore.scanner.graph import build_module_graph
from conftest import symbol_names


def make_php_repo(tmp_path: Path) -> Path:
    # Mirrors a real project verified by actually RUNNING it with `php main.php`
    # before this fixture was written - a hand-rolled PSR-4 autoloader matching the
    # same composer.json mapping this resolver reads, Handler.php reaching Store.php
    # and Logger.php via `use`, main.php reaching all three plus a require_once'd
    # lib/util.php via the __DIR__ . '/...' idiom.
    repo = tmp_path / "repo"
    (repo / "src" / "Handlers").mkdir(parents=True)
    (repo / "src" / "Store").mkdir(parents=True)
    (repo / "src" / "Logging").mkdir(parents=True)
    (repo / "lib").mkdir(parents=True)

    (repo / "composer.json").write_text(
        '{"name": "example/webservice", "autoload": {"psr-4": {"App\\\\": "src/"}}}'
    )

    (repo / "src" / "Logging" / "Logger.php").write_text(
        "<?php\n\n"
        "namespace App\\Logging;\n\n"
        "class Logger\n"
        "{\n"
        "    public function info(string $msg): void\n"
        "    {\n"
        '        echo $msg . "\\n";\n'
        "    }\n"
        "}\n"
    )
    (repo / "src" / "Store" / "Store.php").write_text(
        "<?php\n\n"
        "namespace App\\Store;\n\n"
        "class Store\n"
        "{\n"
        "    public function get(int $id): ?string\n"
        "    {\n"
        "        return null;\n"
        "    }\n"
        "}\n"
    )
    (repo / "src" / "Handlers" / "Handler.php").write_text(
        "<?php\n\n"
        "namespace App\\Handlers;\n\n"
        "use App\\Store\\Store;\n"
        "use App\\Logging\\Logger;\n\n"
        "class Handler\n"
        "{\n"
        "    public function __construct(Store $store, Logger $logger) {}\n\n"
        "    public function getUser(int $id): void {}\n"
        "}\n"
    )
    (repo / "lib" / "util.php").write_text(
        "<?php\n\nfunction utilHelper(): string\n{\n    return 'helper';\n}\n"
    )
    (repo / "main.php").write_text(
        "<?php\n\n"
        "require_once __DIR__ . '/lib/util.php';\n\n"
        "use App\\Handlers\\Handler;\n"
        "use App\\Store\\Store;\n"
        "use App\\Logging\\Logger;\n"
    )
    return repo


def test_build_module_graph_extracts_php_symbols(tmp_path):
    repo = make_php_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    handler = by_path["src/Handlers/Handler.php"]
    assert handler["language"] == "php"
    assert "Handler" in symbol_names(handler["symbols"]["classes"])
    assert "getUser" in symbol_names(handler["symbols"]["functions"])

    assert unparseable == []


def test_build_module_graph_php_psr4_use_resolves(tmp_path):
    repo = make_php_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/Handlers/Handler.php", "src/Store/Store.php") in edges
    assert ("src/Handlers/Handler.php", "src/Logging/Logger.php") in edges
    assert ("main.php", "src/Handlers/Handler.php") in edges


def test_build_module_graph_php_dir_concat_require_resolves(tmp_path):
    repo = make_php_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("main.php", "lib/util.php") in edges


def test_build_module_graph_php_leaf_files_have_no_outgoing_edges(tmp_path):
    repo = make_php_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)

    sources_with_edges = {edge[0] for edge in dependency_graph["edges"]}
    assert "src/Store/Store.php" not in sources_with_edges
    assert "src/Logging/Logger.php" not in sources_with_edges
    assert "lib/util.php" not in sources_with_edges


def test_build_module_graph_php_use_does_not_resolve_without_composer_json(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Foo.php").write_text("<?php\nnamespace App;\nclass Foo {}\n")
    (repo / "main.php").write_text("<?php\nuse App\\Foo;\n")

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_php_relative_require_resolves(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helper.php").write_text("<?php\nfunction h() {}\n")
    (repo / "main.php").write_text("<?php\nrequire './helper.php';\n")

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("main.php", "helper.php") in edges


def test_build_module_graph_php_use_of_unmapped_namespace_does_not_resolve(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "composer.json").write_text(
        '{"autoload": {"psr-4": {"App\\\\": "src/"}}}'
    )
    (repo / "main.php").write_text("<?php\nuse Vendor\\SomeLib\\Thing;\n")

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []
