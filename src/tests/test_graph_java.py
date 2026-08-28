from pathlib import Path
from unittest.mock import patch

from aletheore.scanner.graph import build_module_graph
from conftest import symbol_names


def _write_synthetic_java_repo(repo: Path, file_count: int) -> None:
    # Real class bodies, not one-liners - tree-sitter tree size scales with
    # source complexity, so trivial fixtures under-report the memory this
    # test is trying to measure (verified: one-line classes never surfaced
    # a measurable gap between the cached and uncached pre-pass; classes
    # this size reliably do).
    for i in range(file_count):
        fields = "\n".join(f"    private int field{j} = {j};" for j in range(40))
        methods = "\n".join(
            f"""
    public int method{j}(int a, int b) {{
        int total = 0;
        for (int k = 0; k < 10; k++) {{
            if (k % 2 == 0) {{
                total += a * k + field{j % 40};
            }} else {{
                total -= b - k;
            }}
        }}
        return total;
    }}"""
            for j in range(60)
        )
        (repo / f"C{i}.java").write_text(
            f"package com.example.pkg{i};\n\nimport java.util.List;\nimport java.util.Map;\n\n"
            f"public class C{i} {{\n{fields}\n{methods}\n}}\n"
        )


def _measure_boundary_rss_for_java_repo(file_count: int, out_queue) -> None:
    # Runs inside its own subprocess so ru_maxrss - a whole-process
    # high-water-mark that can't be reset mid-process - reflects only this
    # one scan, not whatever the pytest process had already touched before
    # this test ran.
    import resource
    import sys
    import tempfile
    from pathlib import Path as _Path

    from aletheore.scanner import graph as _graph_module

    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    boundary: dict[str, int] = {}
    original_rel = _graph_module._rel

    def tracking_rel(repo_path, path):
        # _rel(repo_path, path) is the first thing the main loop does for
        # each file, so its first-ever call lands right at the boundary
        # between the pre-pass finishing (every .java file already parsed)
        # and the main loop starting to consume anything - the instant a
        # whole-repo cache would be at its fullest.
        if "value" not in boundary:
            boundary["value"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return original_rel(repo_path, path)

    _graph_module._rel = tracking_rel

    with tempfile.TemporaryDirectory() as tmp:
        repo = _Path(tmp)
        _write_synthetic_java_repo(repo, file_count)
        _graph_module.build_module_graph(repo)

    # ru_maxrss is bytes on macOS, KB on Linux.
    scale = 1 if sys.platform == "darwin" else 1024
    out_queue.put((boundary["value"] - baseline) * scale)


def _boundary_rss_delta_for_java_repo(file_count: int) -> int:
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    out_queue: multiprocessing.Queue = ctx.Queue()
    process = ctx.Process(target=_measure_boundary_rss_for_java_repo, args=(file_count, out_queue))
    process.start()
    result = out_queue.get()
    process.join()
    return result


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


def test_build_module_graph_java_ambiguous_import_is_flagged_inferred(tmp_path):
    # Two independent source trees (no shared src/main/java prefix, so each
    # contributes its own distinct root) each declare com.example.Shared -
    # a real multi-root tiebreak, resolved deterministically (moduleA sorts
    # before moduleB) but flagged, not silently presented as certain.
    repo = tmp_path / "repo"
    module_a = repo / "moduleA" / "com" / "example"
    module_b = repo / "moduleB" / "com" / "example"
    module_a.mkdir(parents=True)
    module_b.mkdir(parents=True)
    (module_a / "Shared.java").write_text(
        "package com.example;\n\npublic class Shared {\n    public int x;\n}\n"
    )
    (module_b / "Shared.java").write_text(
        "package com.example;\n\npublic class Shared {\n    public int x;\n}\n"
    )
    (module_a / "User.java").write_text(
        "package com.example;\n\nimport com.example.Shared;\n\n"
        "public class User {\n    private Shared s;\n}\n"
    )

    modules, _dependency_graph, _unparseable = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}

    user = by_path["moduleA/com/example/User.java"]
    assert user["imports"] == ["moduleA/com/example/Shared.java"]
    assert user["import_confidence"] == {"moduleA/com/example/Shared.java": "inferred"}


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


def test_build_module_graph_java_static_wildcard_import_resolves_to_the_class(tmp_path):
    # audit finding 29: "import static com.example.util.Constants.*;" used
    # to be checked against is_wildcard before is_static, treating every
    # segment - including the trailing class name "Constants" - as a
    # package-directory component and looking for a directory
    # .../util/Constants/ that doesn't exist (the real file is
    # Constants.java). A static wildcard's dotted text is already just the
    # class path (no member/"*" segment appended, unlike a static
    # non-wildcard import), so it needs the same class-file lookup the
    # test right above this one already exercises for a named static
    # member - never the plain-wildcard package-directory lookup.
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
        "import static com.example.util.Constants.*;\n\n"
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


def test_build_module_graph_reparses_each_java_file_in_the_main_loop(tmp_path):
    # Documents the deliberate trade-off behind audit finding 15: the
    # source-root pre-pass already has to read and parse every .java file
    # to extract its package declaration, but nothing from that parse is
    # cached for the main loop to reuse anymore - each file is read and
    # parsed a second time there. That's real, avoidable CPU cost, traded
    # away on purpose because the alternative (a dict caching every file's
    # (source, Tree) between the two passes) pins every tree in the repo
    # in memory at once - real risk on a hosted worker capped at 1GB
    # (scan-worker/scan-worker-2's mem_limit in docker-compose.yml). If a
    # future change reintroduces that cache, this test's count drops back
    # to 1 and should be revisited alongside the memory trade-off, not
    # just updated to match.
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
    assert all(count == 2 for count in read_counts.values())


def test_build_module_graph_never_holds_every_java_files_tree_at_once(tmp_path, monkeypatch):
    # Real regression this guards: java_pre_parsed used to hold every
    # .java file's (source, Tree) simultaneously once the pre-pass
    # finished - tree-sitter trees run ~37x their source size, so on a
    # large Java repo this is real memory (measured independently at
    # 82MB RSS on a 512-file C# repo, the same shape), and it was pinned
    # until the main loop got around to consuming each entry. The fix
    # doesn't cache at all: each file's (source, Tree) falls out of scope
    # at the end of its own pre-pass iteration, before the next file is
    # even read.
    #
    # Inspects build_module_graph's own frame locals directly rather than
    # inferring retention from sys.getrefcount(): getrefcount on a single
    # captured tree turned out to fluctuate for reasons unrelated to
    # caching (other locals briefly holding it during unrelated
    # processing), which made it unable to reliably tell the cached and
    # uncached implementations apart. Scanning every local collection in
    # the frame for how many Tree objects it holds at once has no such
    # ambiguity - a name-agnostic version of "did anything just accumulate
    # one entry per file".
    repo = tmp_path / "repo"
    repo.mkdir()
    file_count = 12
    for i in range(file_count):
        (repo / f"C{i}.java").write_text(f"package com.example;\nclass C{i} {{}}\n")

    import sys

    import tree_sitter

    from aletheore.scanner import graph as graph_module

    peak_trees_in_one_local: dict[str, int] = {"value": 0}
    original_rel = graph_module._rel

    def tracking_rel(repo_path, path):
        frame = sys._getframe(1)
        if frame.f_code.co_name == "build_module_graph":
            for value in frame.f_locals.values():
                if isinstance(value, dict):
                    n = sum(
                        1
                        for v in value.values()
                        if isinstance(v, tuple) and any(isinstance(x, tree_sitter.Tree) for x in v)
                    )
                elif isinstance(value, (list, set)):
                    n = sum(1 for v in value if isinstance(v, tree_sitter.Tree))
                else:
                    n = 0
                peak_trees_in_one_local["value"] = max(peak_trees_in_one_local["value"], n)
        return original_rel(repo_path, path)

    monkeypatch.setattr(graph_module, "_rel", tracking_rel)

    graph_module.build_module_graph(repo)

    # A reintroduced whole-pre-pass cache would show up here as a single
    # local holding all file_count trees at once (verified: this exact
    # test found java_pre_parsed holding 12/12 when run against the
    # dict-caching implementation this fix replaces).
    assert peak_trees_in_one_local["value"] < file_count


def test_build_module_graph_java_prepass_boundary_rss_does_not_scale_with_repo_size(tmp_path):
    # Secondary, corroborating check for the same invariant the frame-
    # inspection test above proves directly: peak process memory at the
    # boundary between the pre-pass finishing and the main loop starting
    # should stay roughly flat as file count grows, not scale with it.
    # Real production constraint this guards: scan-worker/scan-worker-2 are
    # both capped at mem_limit: 1g in docker-compose.yml.
    #
    # Calibrated against real measurements on this exact fixture shape, in
    # an isolated subprocess so ru_maxrss - a whole-process high-water-mark
    # that can't be reset mid-process - reflects only this one scan:
    # dict-caching (pre-fix) scaled the boundary delta close to linearly
    # with file count (~7MB at 10 files, ~406MB at 600 files); this fix
    # stays flat regardless of file count (~1-3MB at both). 60MB leaves
    # roughly 20x headroom above the fix's real measured delta while
    # sitting comfortably below the pre-fix number it needs to catch.
    boundary_delta = _boundary_rss_delta_for_java_repo(600)

    assert boundary_delta < 60 * 1024 * 1024


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
