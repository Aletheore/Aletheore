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

    get_user_fn = next(f for f in handler["symbols"]["functions"] if f["name"] == "getUser")
    assert get_user_fn["params"] == "(int $id)"
    handler_cls = next(c for c in handler["symbols"]["classes"] if c["name"] == "Handler")
    assert handler_cls["params"] is None

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


def test_build_module_graph_php_include_escaping_repo_root_does_not_crash(tmp_path):
    # Before this fix, a require/include resolving above the repo root (a real
    # file on disk, just outside repo_path) crashed the whole scan with an
    # unhandled ValueError from path.relative_to().
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.php").write_text("<?php\nfunction outside() {}\n")
    (repo / "main.php").write_text("<?php\nrequire '../outside.php';\n")

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_php_use_of_unmapped_namespace_does_not_resolve(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "composer.json").write_text(
        '{"autoload": {"psr-4": {"App\\\\": "src/"}}}'
    )
    (repo / "main.php").write_text("<?php\nuse Vendor\\SomeLib\\Thing;\n")

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_php_extracts_phpdoc_and_return_type(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.php").write_text(
        "<?php\n"
        "/**\n * Adds two numbers.\n */\n"
        "function add(int $a, int $b): int {\n  return $a + $b;\n}\n"
    )
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers."
    assert func["return_type"] == "int"


def test_php_class_and_method_phpdoc_is_extracted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.php").write_text(
        "<?php\n"
        "/**\n * A widget.\n */\n"
        "class Widget {\n"
        "  /**\n   * Renders it.\n   */\n"
        "  public function render() {}\n"
        "}\n"
    )
    modules, _, _ = build_module_graph(repo)
    cls = modules[0]["symbols"]["classes"][0]
    method = modules[0]["symbols"]["functions"][0]
    assert cls["docstring"] == "A widget."
    assert method["docstring"] == "Renders it."


def test_php_function_with_no_docblock_gets_none(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.php").write_text("<?php\nfunction f() {}\n")
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] is None
    assert func["return_type"] is None


def test_php_function_nested_only_in_an_anonymous_function_is_not_public(tmp_path):
    # A function whose only enclosing container is an anonymous function
    # expression (no named function ancestor between it and that closure)
    # had no matching ancestor before anonymous_function was added to the
    # shared node-type set.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.php").write_text(
        "<?php\n$f = function() {\n  function inner() {}\n};\n\nfunction top_level() {}\n"
    )
    modules, _, _ = build_module_graph(repo)
    by_name = {f["name"]: f for f in modules[0]["symbols"]["functions"]}
    assert by_name["inner"]["is_public"] is False
    assert by_name["top_level"]["is_public"] is True
