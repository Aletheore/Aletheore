from pathlib import Path

from veridion.scanner.graph import build_module_graph


def make_go_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "internal" / "service").mkdir(parents=True)
    (repo / "go.mod").write_text("module github.com/example/myapp\n\ngo 1.22\n")
    (repo / "main.go").write_text(
        'package main\n\n'
        'import (\n'
        '\t"fmt"\n'
        '\t"github.com/example/myapp/internal/service"\n'
        ')\n\n'
        'func main() {\n'
        '\tfmt.Println(service.Hello())\n'
        '}\n'
    )
    (repo / "internal" / "service" / "service.go").write_text(
        'package service\n\n'
        'func Hello() string {\n'
        '\treturn "hi"\n'
        '}\n\n'
        'type Greeter struct {\n'
        '\tName string\n'
        '}\n'
    )
    (repo / "internal" / "service" / "helper.go").write_text(
        'package service\n\n'
        'func helper() {}\n'
    )
    (repo / "internal" / "service" / "service_test.go").write_text(
        'package service\n\n'
        'import "testing"\n\n'
        'func TestHello(t *testing.T) {}\n'
    )
    return repo


def test_build_module_graph_extracts_go_imports_and_symbols(tmp_path):
    repo = make_go_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    main = by_path["main.go"]
    assert main["language"] == "go"
    assert "internal/service/service.go" in main["imports"]
    assert "internal/service/helper.go" in main["imports"]

    service = by_path["internal/service/service.go"]
    assert "Hello" in service["symbols"]["functions"]
    assert "Greeter" in service["symbols"]["classes"]

    assert unparseable == []


def test_build_module_graph_go_package_import_fans_out_to_every_file_in_the_package(tmp_path):
    repo = make_go_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("main.go", "internal/service/service.go") in edges
    assert ("main.go", "internal/service/helper.go") in edges


def test_build_module_graph_go_package_import_excludes_test_files_from_fan_out(tmp_path):
    repo = make_go_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("main.go", "internal/service/service_test.go") not in edges


def test_build_module_graph_go_stdlib_import_does_not_resolve(tmp_path):
    repo = make_go_repo(tmp_path)
    modules, dependency_graph, _ = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    main = by_path["main.go"]
    assert "fmt" not in main["imports"]
    assert not any(edge[1] == "fmt" for edge in dependency_graph["edges"])


def test_build_module_graph_go_aliased_import_still_resolves(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkgb").mkdir(parents=True)
    (repo / "go.mod").write_text("module example.com/aliastest\n")
    (repo / "main.go").write_text(
        'package main\n\n'
        'import (\n'
        '\tb "example.com/aliastest/pkgb"\n'
        ')\n\n'
        'func main() {\n'
        '\tb.Run()\n'
        '}\n'
    )
    (repo / "pkgb" / "b.go").write_text('package pkgb\n\nfunc Run() {}\n')

    modules, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("main.go", "pkgb/b.go") in edges


def test_build_module_graph_go_imports_do_not_resolve_without_a_go_mod(tmp_path):
    repo = tmp_path / "repo"
    (repo / "internal" / "service").mkdir(parents=True)
    (repo / "main.go").write_text(
        'package main\n\n'
        'import "github.com/example/myapp/internal/service"\n\n'
        'func main() {\n'
        '\tservice.Hello()\n'
        '}\n'
    )
    (repo / "internal" / "service" / "service.go").write_text(
        'package service\n\nfunc Hello() string { return "hi" }\n'
    )

    modules, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_go_single_import_without_parens_resolves(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkgb").mkdir(parents=True)
    (repo / "go.mod").write_text("module example.com/single\n")
    (repo / "main.go").write_text(
        'package main\n\n'
        'import "example.com/single/pkgb"\n\n'
        'func main() {\n'
        '\tpkgb.Run()\n'
        '}\n'
    )
    (repo / "pkgb" / "b.go").write_text('package pkgb\n\nfunc Run() {}\n')

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("main.go", "pkgb/b.go") in edges


def test_build_module_graph_go_method_declarations_are_not_lost(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "server.go").write_text(
        'package main\n\n'
        'type Server struct {\n'
        '\tName string\n'
        '}\n\n'
        'func (s *Server) Greet() string {\n'
        '\treturn s.Name\n'
        '}\n'
    )

    modules, _, _ = build_module_graph(repo)
    server = modules[0]

    assert "Server" in server["symbols"]["classes"]
    assert "Greet" in server["symbols"]["functions"]
