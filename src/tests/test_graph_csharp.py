from pathlib import Path
from unittest.mock import patch

from aletheore.scanner.graph import build_module_graph
from conftest import symbol_names


def make_csharp_repo(tmp_path: Path) -> Path:
    # Mirrors a real project verified by actually compiling AND running it with
    # `dotnet run` before this fixture was written - a <RootNamespace>App</RootNamespace>
    # csproj (the default in every "dotnet new" template) with NO "App" folder on
    # disk at all, Handler.cs (namespace App.Handlers) reaching Store/Store.cs
    # (namespace App.Store, class UserStore - deliberately NOT matching the
    # filename, since C# doesn't enforce that the way Java does) and
    # Logging/Logger.cs via `using`, Program.cs reaching all three.
    repo = tmp_path / "repo"
    (repo / "Handlers").mkdir(parents=True)
    (repo / "Store").mkdir(parents=True)
    (repo / "Logging").mkdir(parents=True)

    (repo / "Logging" / "Logger.cs").write_text(
        "namespace App.Logging\n"
        "{\n"
        "    public class Logger\n"
        "    {\n"
        "        public void Info(string msg)\n"
        "        {\n"
        "            System.Console.WriteLine(msg);\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
    (repo / "Store" / "Store.cs").write_text(
        "namespace App.Store\n"
        "{\n"
        "    public class UserStore\n"
        "    {\n"
        "        public string? Get(int id)\n"
        "        {\n"
        "            return null;\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
    (repo / "Handlers" / "Handler.cs").write_text(
        "using App.Store;\n"
        "using App.Logging;\n\n"
        "namespace App.Handlers\n"
        "{\n"
        "    public class Handler\n"
        "    {\n"
        "        private UserStore _store;\n"
        "        private Logger _logger;\n\n"
        "        public Handler(UserStore store, Logger logger)\n"
        "        {\n"
        "            _store = store;\n"
        "            _logger = logger;\n"
        "        }\n\n"
        "        public void GetUser(int id)\n"
        "        {\n"
        '            _logger.Info("fetching user");\n'
        "            _store.Get(id);\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
    (repo / "Program.cs").write_text(
        "using App.Handlers;\n"
        "using App.Store;\n"
        "using App.Logging;\n\n"
        "var store = new UserStore();\n"
        'var logger = new Logger("server");\n'
        "var handler = new Handler(store, logger);\n"
        "handler.GetUser(1);\n"
    )
    return repo


def test_build_module_graph_extracts_csharp_symbols(tmp_path):
    repo = make_csharp_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    handler = by_path["Handlers/Handler.cs"]
    assert handler["language"] == "csharp"
    assert "Handler" in symbol_names(handler["symbols"]["classes"])
    assert "GetUser" in symbol_names(handler["symbols"]["functions"])

    get_user_fn = next(f for f in handler["symbols"]["functions"] if f["name"] == "GetUser")
    assert get_user_fn["params"] == "(int id)"
    handler_cls = next(c for c in handler["symbols"]["classes"] if c["name"] == "Handler")
    assert handler_cls["params"] is None

    assert unparseable == []


def test_build_module_graph_extracts_csharp_properties_and_fields(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "A.cs").write_text(
        "public class A {\n"
        "  public string Name { get; set; }\n"
        "  private int _count;\n"
        "  public static readonly int Limit = 10;\n"
        "}\n"
    )
    modules, _, _ = build_module_graph(repo)
    symbols = modules[0]["symbols"]
    properties = {s["name"]: s for s in symbols["properties"]}
    fields = {s["name"]: s for s in symbols["fields"]}
    assert properties["Name"]["return_type"] == "string"
    assert fields["_count"]["return_type"] == "int"
    assert fields["Limit"]["return_type"] == "int"


def test_build_module_graph_csharp_using_resolves_despite_implicit_root_namespace(tmp_path):
    # The real bug this test exists to pin down: RootNamespace="App" prepends an
    # implicit prefix with no "App" folder anywhere on disk. Requiring the whole
    # namespace to mirror the directory (which is exactly right for Java, which
    # has no such feature) silently resolved nothing at all here until fixed.
    repo = make_csharp_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("Handlers/Handler.cs", "Store/Store.cs") in edges
    assert ("Handlers/Handler.cs", "Logging/Logger.cs") in edges
    assert ("Program.cs", "Handlers/Handler.cs") in edges
    assert ("Program.cs", "Store/Store.cs") in edges
    assert ("Program.cs", "Logging/Logger.cs") in edges


def test_build_module_graph_csharp_using_resolves_by_namespace_not_by_class_name(tmp_path):
    # The other real bug: "using App.Store;" only imports a namespace, not the
    # specific "UserStore" class - a Java-style "resolve straight to a same-named
    # file" approach can never work here since the file is Store.cs but the class
    # is UserStore. This asserts the actual resolved target is the file that
    # exists in that namespace's directory, regardless of what's declared inside.
    repo = make_csharp_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("Handlers/Handler.cs", "Store/Store.cs") in edges


def test_build_module_graph_csharp_unmapped_namespace_does_not_resolve(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Program.cs").write_text("using Some.External.Library;\n")

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_csharp_flat_project_does_not_cross_match_sibling_namespace(tmp_path):
    # Regression test: the flat-project fallback in _csharp_prefix_and_root_for
    # (triggered when no directory mirrors any suffix of the namespace, so the
    # whole namespace becomes the implicit prefix) used to return that prefix
    # WITHOUT a trailing dot, breaking the "." boundary every other return path
    # in that function enforces. All three files below sit flat at repo root
    # with distinct namespaces and no mirroring folders, so each falls into the
    # fallback. Before the fix, "App.Data" (bare) trivially self-matched via
    # plain startswith() with an empty remainder, which resolved to *every*
    # .cs file in the shared root directory - including DataAccess.cs itself
    # and the unrelated Other.cs.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Data.cs").write_text("namespace App.Data\n{\n    public class Repository {}\n}\n")
    (repo / "Other.cs").write_text("namespace App.Other\n{\n    public class Unrelated {}\n}\n")
    (repo / "DataAccess.cs").write_text(
        "using App.Data;\n\nnamespace App.DataAccess\n{\n    public class Layer {}\n}\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("DataAccess.cs", "DataAccess.cs") not in edges
    assert ("DataAccess.cs", "Other.cs") not in edges


def test_build_module_graph_dotnet_obj_directory_is_excluded(tmp_path):
    repo = tmp_path / "repo"
    (repo / "obj" / "Debug").mkdir(parents=True)
    (repo / "obj" / "Debug" / "Generated.cs").write_text("namespace Ignored { class X {} }\n")
    (repo / "Program.cs").write_text("var x = 1;\n")

    modules, _, _ = build_module_graph(repo)

    assert [m["path"] for m in modules] == ["Program.cs"]


def test_build_module_graph_csharp_using_escaping_repo_root_does_not_crash(tmp_path):
    # Before this fix, this crashed with an unhandled ValueError from
    # path.relative_to(). A file directly at the repo root whose single-segment
    # namespace matches the repo directory's own name (here "App") makes
    # _csharp_prefix_and_root_for infer a resolution root one level ABOVE
    # repo_path - a real coincidence for any project namespaced after its own
    # folder. A "using" statement resolving relative to that escaped root can
    # then fan out to real files genuinely outside repo_path.
    repo = tmp_path / "App"
    repo.mkdir()
    (repo / "Foo.cs").write_text("namespace App;\n\nclass Foo {}\n")
    (tmp_path / "Other").mkdir()
    (tmp_path / "Other" / "Outside.cs").write_text("class Outside {}\n")
    (repo / "Main.cs").write_text("using Other;\n\nclass Main {}\n")

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_reads_each_csharp_file_only_once(tmp_path):
    # Before this fix, the namespace/root-inference pre-pass and the main
    # extraction loop each independently read_bytes() and re-parsed every
    # .cs file from scratch - a real, avoidable 2x tree-sitter parse cost
    # per file.
    repo = make_csharp_repo(tmp_path)

    real_read_bytes = Path.read_bytes
    read_counts: dict[str, int] = {}

    def counting_read_bytes(self):
        if self.suffix == ".cs":
            read_counts[str(self)] = read_counts.get(str(self), 0) + 1
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", counting_read_bytes):
        build_module_graph(repo)

    assert read_counts
    assert all(count == 1 for count in read_counts.values())


def test_csharp_extracts_summary_from_xmldoc(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "A.cs").write_text(
        "public class A {\n"
        "  /// <summary>\n  /// Adds two numbers.\n  /// </summary>\n"
        "  public int Add(int a, int b) {\n    return a + b;\n  }\n"
        "}\n"
    )
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers."
    assert func["return_type"] == "int"


def test_csharp_falls_back_to_raw_text_for_non_xml_triple_slash_comment(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "A.cs").write_text(
        "public class A {\n"
        "  /// Adds two numbers.\n"
        "  public int Add(int a, int b) {\n    return a + b;\n  }\n"
        "}\n"
    )
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers."


def test_csharp_method_with_no_doc_comment_gets_none(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "A.cs").write_text("public class A {\n  public void F() {}\n}\n")
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] is None
    assert func["return_type"] == "void"


# No C# equivalent of the JS/Java/Rust/PHP/C++ "named thing nested only in
# an anonymous closure" test: confirmed empirically that _extract_csharp
# only ever tracks method_declaration/constructor_declaration (functions)
# and class/interface/struct/record/enum_declaration (types) as symbols,
# and C# doesn't support declaring any of those inside a lambda body at
# all (unlike Java's local classes) - `void Inner() {}` inside a lambda is
# a local_function_statement, a node type this scanner never extracts as
# a symbol in the first place, nested or not. lambda_expression and
# anonymous_method_expression stay in the shared node-type set anyway
# (real, reachable fix for Java/C++, which do allow this), they're just
# inert for C# - there's no valid C# code that would ever need them here.
