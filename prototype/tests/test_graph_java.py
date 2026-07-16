from pathlib import Path

from veridion.scanner.graph import build_module_graph


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
    assert "Handler" in handler["symbols"]["classes"]
    assert "getUser" in handler["symbols"]["functions"]

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
    assert "Main" in modules[0]["symbols"]["classes"]
    assert unparseable == []
