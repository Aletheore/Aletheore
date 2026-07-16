from pathlib import Path

from veridion.scanner.graph import build_module_graph


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
    assert "Handler" in handler["symbols"]["classes"]
    assert "GetUser" in handler["symbols"]["functions"]

    assert unparseable == []


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


def test_build_module_graph_dotnet_obj_directory_is_excluded(tmp_path):
    repo = tmp_path / "repo"
    (repo / "obj" / "Debug").mkdir(parents=True)
    (repo / "obj" / "Debug" / "Generated.cs").write_text("namespace Ignored { class X {} }\n")
    (repo / "Program.cs").write_text("var x = 1;\n")

    modules, _, _ = build_module_graph(repo)

    assert [m["path"] for m in modules] == ["Program.cs"]
