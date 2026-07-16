from pathlib import Path

from veridion.scanner.graph import build_module_graph


def make_cpp_repo(tmp_path: Path) -> Path:
    # Mirrors a real project verified by actually compiling AND running it with
    # `clang++ -Iinclude ... && ./a.out` before this fixture was written -
    # handler.h reaching store.h/logger.h via same-directory quoted includes,
    # each .cpp reaching its own header via a "../include/x.h" relative include,
    # and out-of-class method definitions (Logger::info, Handler::getUser).
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "include").mkdir(parents=True)

    (repo / "include" / "logger.h").write_text(
        "#ifndef LOGGER_H\n#define LOGGER_H\n\n"
        "#include <string>\n\n"
        "class Logger {\n"
        "public:\n"
        "    Logger(const std::string& prefix);\n"
        "    void info(const std::string& msg);\n"
        "private:\n"
        "    std::string prefix_;\n"
        "};\n\n#endif\n"
    )
    (repo / "src" / "logger.cpp").write_text(
        '#include "../include/logger.h"\n'
        "#include <iostream>\n\n"
        "Logger::Logger(const std::string& prefix) : prefix_(prefix) {}\n\n"
        "void Logger::info(const std::string& msg) {\n"
        "    std::cout << prefix_ << msg << std::endl;\n"
        "}\n"
    )
    (repo / "include" / "store.h").write_text(
        "#ifndef STORE_H\n#define STORE_H\n\n"
        "class Store {\npublic:\n    int get(int id);\n};\n\n#endif\n"
    )
    (repo / "src" / "store.cpp").write_text(
        '#include "../include/store.h"\n\n'
        "int Store::get(int id) {\n    return id;\n}\n"
    )
    (repo / "include" / "handler.h").write_text(
        "#ifndef HANDLER_H\n#define HANDLER_H\n\n"
        '#include "store.h"\n'
        '#include "logger.h"\n\n'
        "class Handler {\n"
        "public:\n"
        "    Handler(Store* store, Logger* logger);\n"
        "    void getUser(int id);\n"
        "private:\n"
        "    Store* store_;\n"
        "    Logger* logger_;\n"
        "};\n\n#endif\n"
    )
    (repo / "src" / "handler.cpp").write_text(
        '#include "../include/handler.h"\n\n'
        "Handler::Handler(Store* store, Logger* logger) : store_(store), logger_(logger) {}\n\n"
        "void Handler::getUser(int id) {\n"
        '    logger_->info("fetching user");\n'
        "    store_->get(id);\n"
        "}\n"
    )
    (repo / "src" / "main.cpp").write_text(
        '#include "../include/handler.h"\n'
        '#include "../include/store.h"\n'
        '#include "../include/logger.h"\n\n'
        "int main() {\n"
        "    Store store;\n"
        '    Logger logger("server");\n'
        "    Handler handler(&store, &logger);\n"
        "    handler.getUser(1);\n"
        "    return 0;\n"
        "}\n"
    )
    return repo


def test_build_module_graph_extracts_cpp_symbols(tmp_path):
    repo = make_cpp_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    handler_h = by_path["include/handler.h"]
    assert handler_h["language"] == "cpp"
    assert "Handler" in handler_h["symbols"]["classes"]

    assert unparseable == []


def test_build_module_graph_cpp_out_of_class_methods_are_extracted(tmp_path):
    repo = make_cpp_repo(tmp_path)
    modules, _, _ = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    handler_cpp = by_path["src/handler.cpp"]
    assert "Handler" in handler_cpp["symbols"]["functions"]
    assert "getUser" in handler_cpp["symbols"]["functions"]

    logger_cpp = by_path["src/logger.cpp"]
    assert "info" in logger_cpp["symbols"]["functions"]


def test_build_module_graph_cpp_same_directory_include_resolves(tmp_path):
    repo = make_cpp_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("include/handler.h", "include/store.h") in edges
    assert ("include/handler.h", "include/logger.h") in edges


def test_build_module_graph_cpp_relative_traversal_include_resolves(tmp_path):
    repo = make_cpp_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/main.cpp", "include/handler.h") in edges
    assert ("src/main.cpp", "include/store.h") in edges
    assert ("src/main.cpp", "include/logger.h") in edges
    assert ("src/store.cpp", "include/store.h") in edges


def test_build_module_graph_cpp_angle_bracket_include_never_resolves(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.cpp").write_text("#include <iostream>\n\nint main() { return 0; }\n")

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_cpp_forward_declaration_is_not_counted_as_a_defined_type(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.cpp").write_text(
        "struct Foo;\n\n"
        "struct Foo* getFoo();\n\n"
        "struct Bar {\n    int x;\n};\n"
    )

    modules, _, _ = build_module_graph(repo)

    assert modules[0]["symbols"]["classes"] == ["Bar"]


def test_build_module_graph_c_file_uses_c_grammar(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "util.h").write_text("int add(int a, int b);\n")
    (repo / "util.c").write_text(
        '#include "util.h"\n\nint add(int a, int b) {\n    return a + b;\n}\n'
    )

    modules, dependency_graph, unparseable = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}

    assert by_path["util.c"]["language"] == "c"
    assert "add" in by_path["util.c"]["symbols"]["functions"]
    assert ("util.c", "util.h") in {tuple(e) for e in dependency_graph["edges"]}
    assert unparseable == []
