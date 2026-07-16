from pathlib import Path

from veridion.scanner.graph import build_module_graph


def make_rust_repo(tmp_path: Path) -> Path:
    # Mirrors a real crate (webservice) verified with `cargo build` before this
    # fixture was written - main.rs at the crate root declaring three submodules,
    # handlers/mod.rs reaching across (crate::) and up (super::) to the other two.
    repo = tmp_path / "repo"
    (repo / "src" / "handlers").mkdir(parents=True)
    (repo / "src" / "store").mkdir(parents=True)
    (repo / "Cargo.toml").write_text(
        '[package]\nname = "webservice"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    (repo / "src" / "main.rs").write_text(
        "mod handlers;\n"
        "mod logging;\n"
        "mod store;\n\n"
        "use handlers::Handler;\n"
        "use logging::Logger;\n"
        "use store::Store;\n\n"
        "fn main() {\n"
        '    let logger = Logger::new("server");\n'
        "    let store = Store::new();\n"
        "    let handler = Handler::new(store, logger);\n"
        "    handler.get_user(1);\n"
        "}\n"
    )
    (repo / "src" / "logging.rs").write_text(
        "pub struct Logger {\n"
        "    pub prefix: String,\n"
        "}\n\n"
        "impl Logger {\n"
        "    pub fn new(prefix: &str) -> Self {\n"
        "        Logger { prefix: prefix.to_string() }\n"
        "    }\n\n"
        "    pub fn info(&self, msg: &str) {\n"
        '        println!("{}: {}", self.prefix, msg);\n'
        "    }\n"
        "}\n"
    )
    (repo / "src" / "store" / "mod.rs").write_text(
        "pub struct User {\n"
        "    pub id: u32,\n"
        "    pub name: String,\n"
        "}\n\n"
        "pub struct Store {\n"
        "    users: Vec<User>,\n"
        "}\n\n"
        "impl Store {\n"
        "    pub fn new() -> Self {\n"
        "        Store { users: Vec::new() }\n"
        "    }\n\n"
        "    pub fn get(&self, id: u32) -> Option<&User> {\n"
        "        self.users.iter().find(|u| u.id == id)\n"
        "    }\n"
        "}\n"
    )
    (repo / "src" / "handlers" / "mod.rs").write_text(
        "use crate::store::Store;\n"
        "use super::logging::Logger;\n\n"
        "pub struct Handler {\n"
        "    store: Store,\n"
        "    logger: Logger,\n"
        "}\n\n"
        "impl Handler {\n"
        "    pub fn new(store: Store, logger: Logger) -> Self {\n"
        "        Handler { store, logger }\n"
        "    }\n\n"
        "    pub fn get_user(&self, id: u32) {\n"
        '        self.logger.info("fetching user");\n'
        "        self.store.get(id);\n"
        "    }\n"
        "}\n"
    )
    return repo


def test_build_module_graph_extracts_rust_symbols(tmp_path):
    repo = make_rust_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    handlers = by_path["src/handlers/mod.rs"]
    assert handlers["language"] == "rust"
    assert "Handler" in handlers["symbols"]["classes"]
    assert "get_user" in handlers["symbols"]["functions"]

    store = by_path["src/store/mod.rs"]
    assert "User" in store["symbols"]["classes"]
    assert "Store" in store["symbols"]["classes"]

    assert unparseable == []


def test_build_module_graph_rust_implicit_crate_relative_use_resolves(tmp_path):
    repo = make_rust_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/main.rs", "src/handlers/mod.rs") in edges
    assert ("src/main.rs", "src/logging.rs") in edges
    assert ("src/main.rs", "src/store/mod.rs") in edges


def test_build_module_graph_rust_crate_prefix_resolves(tmp_path):
    repo = make_rust_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/handlers/mod.rs", "src/store/mod.rs") in edges


def test_build_module_graph_rust_super_prefix_climbs_to_crate_root(tmp_path):
    repo = make_rust_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/handlers/mod.rs", "src/logging.rs") in edges


def test_build_module_graph_rust_leaf_files_have_no_outgoing_edges(tmp_path):
    repo = make_rust_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)

    sources_with_edges = {edge[0] for edge in dependency_graph["edges"]}
    assert "src/logging.rs" not in sources_with_edges
    assert "src/store/mod.rs" not in sources_with_edges


def test_build_module_graph_rust_std_import_does_not_resolve(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (repo / "src" / "main.rs").write_text(
        "use std::collections::HashMap;\n\nfn main() {\n    let _m: HashMap<i32, i32> = HashMap::new();\n}\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []


def test_build_module_graph_rust_grouped_use_resolves_both_names(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (repo / "src" / "foo.rs").write_text(
        "pub struct Bar;\npub struct Baz;\n"
    )
    (repo / "src" / "main.rs").write_text(
        "mod foo;\n\nuse crate::foo::{Bar, Baz};\n\nfn main() {}\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/main.rs", "src/foo.rs") in edges
    assert len([e for e in dependency_graph["edges"] if e[0] == "src/main.rs"]) == 2


def test_build_module_graph_rust_wildcard_use_resolves(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (repo / "src" / "foo.rs").write_text("pub struct Bar;\n")
    (repo / "src" / "main.rs").write_text(
        "mod foo;\n\nuse crate::foo::*;\n\nfn main() {}\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/main.rs", "src/foo.rs") in edges


def test_build_module_graph_rust_aliased_use_resolves(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (repo / "src" / "foo.rs").write_text("pub struct Bar;\n")
    (repo / "src" / "main.rs").write_text(
        "mod foo;\n\nuse crate::foo::Bar as MyBar;\n\nfn main() {}\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/main.rs", "src/foo.rs") in edges


def test_build_module_graph_rust_imports_do_not_resolve_without_a_crate_root(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "foo.rs").write_text("pub struct Bar;\n")
    (repo / "src" / "notmain.rs").write_text("use crate::foo::Bar;\n")

    _, dependency_graph, _ = build_module_graph(repo)

    assert dependency_graph["edges"] == []
