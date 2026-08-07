from pathlib import Path

from aletheore.scanner.graph import build_module_graph
from conftest import symbol_names


def make_ruby_repo(tmp_path: Path) -> Path:
    # Mirrors a real project verified by actually RUNNING it with `ruby -Ilib
    # main.rb` before this fixture was written (Ruby has no compile step, so
    # execution is the strongest verification available) - lib/handlers/handler.rb
    # reaching lib/app/{store,logger}.rb via require_relative, main.rb reaching all
    # three via lib/-relative plain require.
    repo = tmp_path / "repo"
    (repo / "lib" / "app").mkdir(parents=True)
    (repo / "lib" / "handlers").mkdir(parents=True)

    (repo / "lib" / "app" / "logger.rb").write_text(
        "module App\n"
        "  class Logger\n"
        "    def initialize(prefix)\n"
        "      @prefix = prefix\n"
        "    end\n\n"
        "    def info(msg)\n"
        '      puts "#{@prefix}: #{msg}"\n'
        "    end\n"
        "  end\n"
        "end\n"
    )
    (repo / "lib" / "app" / "store.rb").write_text(
        "module App\n"
        "  class Store\n"
        "    def initialize\n"
        "      @users = {}\n"
        "    end\n\n"
        "    def get(id)\n"
        "      @users[id]\n"
        "    end\n"
        "  end\n"
        "end\n"
    )
    (repo / "lib" / "handlers" / "handler.rb").write_text(
        'require_relative "../app/store"\n'
        'require_relative "../app/logger"\n\n'
        "module Handlers\n"
        "  class Handler\n"
        "    def initialize(store, logger)\n"
        "      @store = store\n"
        "      @logger = logger\n"
        "    end\n\n"
        "    def get_user(id)\n"
        '      @logger.info("fetching user")\n'
        "      @store.get(id)\n"
        "    end\n"
        "  end\n"
        "end\n"
    )
    (repo / "main.rb").write_text(
        'require "handlers/handler"\n'
        'require "app/store"\n'
        'require "app/logger"\n\n'
        'logger = App::Logger.new("server")\n'
        "store = App::Store.new\n"
        "handler = Handlers::Handler.new(store, logger)\n"
        "handler.get_user(1)\n"
    )
    return repo


def test_build_module_graph_extracts_ruby_symbols(tmp_path):
    repo = make_ruby_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    handler = by_path["lib/handlers/handler.rb"]
    assert handler["language"] == "ruby"
    assert "Handler" in symbol_names(handler["symbols"]["classes"])
    assert "get_user" in symbol_names(handler["symbols"]["functions"])

    get_user_fn = next(f for f in handler["symbols"]["functions"] if f["name"] == "get_user")
    assert get_user_fn["params"] == "(id)"
    handler_cls = next(c for c in handler["symbols"]["classes"] if c["name"] == "Handler")
    assert handler_cls["params"] is None

    assert unparseable == []


def test_build_module_graph_ruby_require_relative_resolves(tmp_path):
    repo = make_ruby_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("lib/handlers/handler.rb", "lib/app/store.rb") in edges
    assert ("lib/handlers/handler.rb", "lib/app/logger.rb") in edges


def test_build_module_graph_ruby_plain_require_resolves_via_lib_directory(tmp_path):
    repo = make_ruby_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("main.rb", "lib/handlers/handler.rb") in edges
    assert ("main.rb", "lib/app/store.rb") in edges
    assert ("main.rb", "lib/app/logger.rb") in edges


def test_build_module_graph_ruby_gem_require_does_not_resolve(tmp_path):
    repo = tmp_path / "repo"
    (repo / "lib").mkdir(parents=True)
    (repo / "main.rb").write_text('require "json"\n\nputs JSON.generate({})\n')

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_ruby_require_does_not_resolve_without_a_lib_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.rb").write_text("puts 1\n")
    (repo / "main.rb").write_text('require "app"\n')

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_ruby_require_relative_with_dot_prefix_resolves(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helper.rb").write_text("def helper\nend\n")
    (repo / "main.rb").write_text('require_relative "./helper"\n')

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("main.rb", "helper.rb") in edges


def test_build_module_graph_ruby_require_relative_escaping_repo_root_does_not_crash(tmp_path):
    # Before this fix, a require_relative resolving above the repo root (a real
    # file on disk, just outside repo_path) crashed the whole scan with an
    # unhandled ValueError from path.relative_to() - treated the same as any
    # other unresolved/external require instead.
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.rb").write_text("def outside\nend\n")
    (repo / "main.rb").write_text('require_relative "../outside"\n')

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_ruby_method_call_with_receiver_is_not_mistaken_for_require(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.rb").write_text(
        "class Foo\n"
        "  def require(x)\n"
        "    x\n"
        "  end\n"
        "end\n\n"
        'Foo.new.require("not_a_real_import")\n'
    )

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_ruby_extracts_top_level_doc_comment(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.rb").write_text("# Adds two numbers.\ndef add(a, b)\n  a + b\nend\n")
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers."


def test_ruby_extracts_doc_comment_for_method_nested_in_class(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.rb").write_text(
        "class Greeter\n  # Greets someone.\n  def greet(name)\n    name\n  end\nend\n"
    )
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Greets someone."


def test_ruby_class_doc_comment_is_extracted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.rb").write_text("# A greeter.\nclass Greeter\nend\n")
    modules, _, _ = build_module_graph(repo)
    cls = modules[0]["symbols"]["classes"][0]
    assert cls["docstring"] == "A greeter."


def test_ruby_method_with_blank_line_before_comment_gets_no_docstring(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.rb").write_text("# Unrelated.\n\ndef add(a, b)\n  a + b\nend\n")
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] is None
