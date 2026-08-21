from pathlib import Path
from unittest.mock import patch

from aletheore.scanner.graph import build_module_graph
from conftest import symbol_names


def make_java_repo(tmp_path: Path) -> Path:
    # Mirrors a real Maven-layout project verified with `javac` before this fixture
    # was written - Main.java at com.example, importing across com.example.handlers
    # (a direct class import), com.example.store (direct import), and a wildcard
    # import of com.example.logging.
    repo = tmp_path / "repo"
    base = repo / "src" / "main" / "java" / "com" / "example"
    (base / "handlers").mkdir(parents=True)
    (base / "store").mkdir(parents=True)
    (base / "logging").mkdir(parents=True)

    (base / "logging" / "Logger.java").write_text(
        "package com.example.logging;\n\n"
        "public class Logger {\n"
        "    private String prefix;\n\n"
        "    public Logger(String prefix) {\n"
        "        this.prefix = prefix;\n"
        "    }\n\n"
        "    public void info(String msg) {\n"
        '        System.out.println(this.prefix + ": " + msg);\n'
        "    }\n"
        "}\n"
    )
    (base / "store" / "User.java").write_text(
        "package com.example.store;\n\n"
        "public class User {\n"
        "    public int id;\n"
        "    public String name;\n"
        "}\n"
    )
    (base / "store" / "Store.java").write_text(
        "package com.example.store;\n\n"
        "import java.util.HashMap;\n"
        "import java.util.Map;\n\n"
        "public class Store {\n"
        "    private Map<Integer, User> users = new HashMap<>();\n\n"
        "    public User get(int id) {\n"
        "        return users.get(id);\n"
        "    }\n"
        "}\n"
    )
    (base / "handlers" / "Handler.java").write_text(
        "package com.example.handlers;\n\n"
        "import com.example.store.Store;\n"
        "import com.example.store.User;\n"
        "import com.example.logging.Logger;\n\n"
        "public class Handler {\n"
        "    private Store store;\n"
        "    private Logger logger;\n\n"
        "    public Handler(Store store, Logger logger) {\n"
        "        this.store = store;\n"
        "        this.logger = logger;\n"
        "    }\n\n"
        "    public void getUser(int id) {\n"
        '        this.logger.info("fetching user");\n'
        "        User u = this.store.get(id);\n"
        "    }\n"
        "}\n"
    )
    (base / "Main.java").write_text(
        "package com.example;\n\n"
        "import com.example.handlers.Handler;\n"
        "import com.example.store.Store;\n"
        "import com.example.logging.*;\n\n"
        "public class Main {\n"
        "    public static void main(String[] args) {\n"
        "        Store store = new Store();\n"
        '        Logger logger = new Logger("server");\n'
        "        Handler handler = new Handler(store, logger);\n"
        "        handler.getUser(1);\n"
        "    }\n"
        "}\n"
    )
    return repo


def test_build_module_graph_extracts_java_symbols(tmp_path):
    repo = make_java_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    handler = by_path["src/main/java/com/example/handlers/Handler.java"]
    assert handler["language"] == "java"
    assert "Handler" in symbol_names(handler["symbols"]["classes"])
    assert "getUser" in symbol_names(handler["symbols"]["functions"])

    get_user_fn = next(f for f in handler["symbols"]["functions"] if f["name"] == "getUser")
    assert get_user_fn["params"] == "(int id)"
    handler_cls = next(c for c in handler["symbols"]["classes"] if c["name"] == "Handler")
    assert handler_cls["params"] is None

    assert unparseable == []


def test_build_module_graph_java_source_root_inferred_from_package(tmp_path):
    repo = make_java_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert (
        "src/main/java/com/example/Main.java",
        "src/main/java/com/example/handlers/Handler.java",
    ) in edges
    assert (
        "src/main/java/com/example/Main.java",
        "src/main/java/com/example/store/Store.java",
    ) in edges


def test_build_module_graph_java_direct_import_resolves(tmp_path):
    repo = make_java_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert (
        "src/main/java/com/example/handlers/Handler.java",
        "src/main/java/com/example/store/Store.java",
    ) in edges
    assert (
        "src/main/java/com/example/handlers/Handler.java",
        "src/main/java/com/example/store/User.java",
    ) in edges


def test_build_module_graph_java_wildcard_import_resolves(tmp_path):
    repo = make_java_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert (
        "src/main/java/com/example/Main.java",
        "src/main/java/com/example/logging/Logger.java",
    ) in edges


def test_build_module_graph_java_jdk_import_does_not_resolve(tmp_path):
    repo = make_java_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)

    sources_with_edges = {edge[0] for edge in dependency_graph["edges"]}
    assert "src/main/java/com/example/store/Store.java" not in sources_with_edges


def test_build_module_graph_java_leaf_files_have_no_outgoing_edges(tmp_path):
    repo = make_java_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)

    sources_with_edges = {edge[0] for edge in dependency_graph["edges"]}
    assert "src/main/java/com/example/logging/Logger.java" not in sources_with_edges
    assert "src/main/java/com/example/store/User.java" not in sources_with_edges


def test_build_module_graph_java_static_import_resolves_to_the_class_not_the_member(tmp_path):
    repo = tmp_path / "repo"
    base = repo / "src" / "main" / "java" / "com" / "example"
    (base / "util").mkdir(parents=True)
    (base / "util" / "Constants.java").write_text(
        "package com.example.util;\n\n"
        "public class Constants {\n"
        "    public static final int MAX_SIZE = 100;\n"
        "}\n"
    )
    (base / "Main.java").write_text(
        "package com.example;\n\n"
        "import static com.example.util.Constants.MAX_SIZE;\n\n"
        "public class Main {\n"
        "    public static void main(String[] args) {\n"
        "        int x = MAX_SIZE;\n"
        "    }\n"
        "}\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert (
        "src/main/java/com/example/Main.java",
        "src/main/java/com/example/util/Constants.java",
    ) in edges


def test_build_module_graph_java_no_package_declaration_still_scans_the_file(tmp_path):
    # A class in the unnamed/default package can't actually be imported by name at
    # all - javac rejects a bare "import Helper;" outright ("'.' expected", verified
    # directly rather than assumed). There's no cross-file import edge to test here;
    # this only confirms an unnamed-package file is still scanned and its own
    # symbols extracted without crashing the source-root inference.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Main.java").write_text(
        "public class Main {\n    public static void main(String[] a) {}\n}\n"
    )

    modules, dependency_graph, unparseable = build_module_graph(repo)

    assert modules[0]["path"] == "Main.java"
    assert "Main" in symbol_names(modules[0]["symbols"]["classes"])
    assert unparseable == []


def test_build_module_graph_java_import_escaping_repo_root_does_not_crash(tmp_path):
    # Before this fix, this crashed with an unhandled ValueError from
    # path.relative_to(). A file directly at the repo root whose package name's
    # last segment matches the repo directory's own name (here "app") makes
    # _java_source_root_for infer a source root one level ABOVE repo_path - a
    # real, if unusual, coincidence, not a hypothetical. An import that then
    # resolves relative to that escaped root can land on a real file genuinely
    # outside repo_path.
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "Foo.java").write_text("package app;\n\nclass Foo {}\n")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "Outside.java").write_text("class Outside {}\n")
    (repo / "Main.java").write_text("import other.Outside;\n\nclass Main {}\n")

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_reuses_the_java_prepass_parse_in_the_main_loop(tmp_path):
    # Real regression this guards: the source-root pre-pass already reads
    # and parses every .java file to extract its package declaration - the
    # main loop used to read and parse each one again from scratch instead
    # of reusing that (source, tree) pair, doubling tree-sitter parse cost
    # for every Java file in a scan for no benefit (the pre-parsed tree is
    # consumed by the main loop moments later in the same function call,
    # not retained for the scan's whole lifetime, so caching it costs
    # nothing extra in memory - the "reparse for bounded memory" framing
    # this test used to encode was never a real trade-off).
    repo = make_java_repo(tmp_path)

    real_read_bytes = Path.read_bytes
    read_counts: dict[str, int] = {}

    def counting_read_bytes(self):
        if self.suffix == ".java":
            read_counts[str(self)] = read_counts.get(str(self), 0) + 1
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", counting_read_bytes):
        build_module_graph(repo)

    assert read_counts
    assert all(count == 1 for count in read_counts.values())


def test_build_module_graph_releases_a_java_prepass_tree_once_the_main_loop_consumes_it(tmp_path, monkeypatch):
    # Real regression this guards: java_pre_parsed holds every .java file's
    # (source, Tree) simultaneously once the pre-pass finishes - tree-sitter
    # trees run ~37x their source size, so on a large Java repo this is real
    # memory (measured independently at 82MB RSS on a 512-file C# repo, the
    # same shape). The main loop used to keep each entry alive via a plain
    # dict lookup even after consuming it, so the whole cache stayed pinned
    # until build_module_graph returned - i.e. until every other file in the
    # repo (every language, not just Java) had also been processed. Popping
    # instead of indexing releases each entry's memory as soon as the main
    # loop passes it.
    #
    # Refcount, not RSS: RSS is real but flaky/slow to assert on directly in
    # a unit test. sys.getrefcount() on the specific Tree object is a direct,
    # reliable proxy for "is java_pre_parsed still holding a reference to
    # this" - verified by hand that reverting the pop() fix changes the
    # count this asserts (4 without the fix, 3 with it).
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AFirst.java").write_text("package com.example;\nclass AFirst {}\n")
    (repo / "ZLater.js").write_text("const x = 1;\n")

    import sys

    import tree_sitter

    from aletheore.scanner import graph as graph_module

    captured: dict[str, object] = {}
    original_parse = tree_sitter.Parser.parse

    def tracking_parse(self, source, *a, **k):
        tree = original_parse(self, source, *a, **k)
        if b"AFirst" in source:
            captured["tree"] = tree
        return tree

    monkeypatch.setattr(tree_sitter.Parser, "parse", tracking_parse)

    refcounts: dict[str, int] = {}
    original_rel = graph_module._rel

    def tracking_rel(repo_path, path):
        # _rel(repo_path, path) is the first thing the main loop does for
        # each file - by the time it's called for ZLater.js, AFirst.java's
        # own main-loop iteration (including the java_pre_parsed.pop()) has
        # already completed.
        if path.name == "ZLater.js" and "tree" in captured:
            refcounts["value"] = sys.getrefcount(captured["tree"])
        return original_rel(repo_path, path)

    monkeypatch.setattr(graph_module, "_rel", tracking_rel)

    graph_module.build_module_graph(repo)

    assert "value" in refcounts
    # Baseline references at this point: captured["tree"] itself, the local
    # `tree` parameter inside sys.getrefcount()'s own call frame. Anything
    # higher means java_pre_parsed is still holding a third reference alive.
    assert refcounts["value"] <= 3


def test_java_extracts_javadoc_and_return_type(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "main" / "java").mkdir(parents=True)
    (repo / "src" / "main" / "java" / "A.java").write_text(
        "public class A {\n"
        "  /**\n   * Adds two numbers.\n   */\n"
        "  public int add(int a, int b) {\n    return a + b;\n  }\n"
        "}\n"
    )
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers."
    assert func["return_type"] == "int"


def test_java_class_javadoc_is_extracted(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "main" / "java").mkdir(parents=True)
    (repo / "src" / "main" / "java" / "Widget.java").write_text(
        "/**\n * A widget.\n */\npublic class Widget {\n}\n"
    )
    modules, _, _ = build_module_graph(repo)
    cls = modules[0]["symbols"]["classes"][0]
    assert cls["docstring"] == "A widget."


def test_java_method_with_no_javadoc_gets_none(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "main" / "java").mkdir(parents=True)
    (repo / "src" / "main" / "java" / "A.java").write_text(
        "public class A {\n  public void f() {}\n}\n"
    )
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] is None
    assert func["return_type"] == "void"


def test_java_local_class_nested_only_in_a_lambda_is_not_public(tmp_path):
    # A local class whose only enclosing container is a lambda body (no
    # named method/constructor ancestor between it and the lambda) had no
    # matching ancestor before lambda_expression was added to the shared
    # node-type set, so it was still marked public.
    repo = tmp_path / "repo"
    (repo / "src" / "main" / "java").mkdir(parents=True)
    (repo / "src" / "main" / "java" / "Outer.java").write_text(
        "public class Outer {\n"
        "  void method() {\n"
        "    Runnable r = () -> {\n"
        "      class LocalHelper {}\n"
        "    };\n"
        "  }\n"
        "}\n"
    )
    modules, _, _ = build_module_graph(repo)
    by_name = {c["name"]: c for c in modules[0]["symbols"]["classes"]}
    assert by_name["LocalHelper"]["is_public"] is False
    assert by_name["Outer"]["is_public"] is True
