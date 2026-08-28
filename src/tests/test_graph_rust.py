from pathlib import Path

from aletheore.scanner.graph import build_module_graph
from conftest import symbol_names


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
    assert "Handler" in symbol_names(handlers["symbols"]["classes"])
    assert "get_user" in symbol_names(handlers["symbols"]["functions"])

    get_user_fn = next(f for f in handlers["symbols"]["functions"] if f["name"] == "get_user")
    assert get_user_fn["params"] == "(&self, id: u32)"
    handler_cls = next(c for c in handlers["symbols"]["classes"] if c["name"] == "Handler")
    assert handler_cls["params"] is None

    store = by_path["src/store/mod.rs"]
    assert "User" in symbol_names(store["symbols"]["classes"])
    assert "Store" in symbol_names(store["symbols"]["classes"])

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


def test_build_module_graph_rust_pub_use_resolves(tmp_path):
    # audit finding 21: "pub use" is the standard idiom for constructing a
    # crate's public re-export surface, not a corner case - a
    # use_declaration with a leading visibility_modifier ("pub"/
    # "pub(crate)") child used to have that modifier mistaken for the path
    # itself (the first non-"use"/";" child), producing an import of the
    # literal string "pub" - which then failed to resolve and silently
    # dropped the real dependency.
    #
    # The target has to be reachable ONLY via the "pub use" itself, not
    # also via a "mod" declaration - main.rs "mod foo;"-ing foo.rs directly
    # would produce the same edge regardless of whether "pub use" resolved
    # correctly, silently passing even against the pre-fix code. Nesting
    # the real target one level deeper (foo::bar, reached only through
    # "pub use crate::foo::bar::Something;", never through "mod foo;"
    # alone) is what isolates the fix.
    repo = tmp_path / "repo"
    (repo / "src" / "foo").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (repo / "src" / "foo.rs").write_text("mod bar;\n")
    (repo / "src" / "foo" / "bar.rs").write_text("pub struct Something;\n")
    (repo / "src" / "main.rs").write_text(
        "mod foo;\n\npub use crate::foo::bar::Something;\n\nfn main() {}\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/main.rs", "src/foo/bar.rs") in edges


def test_build_module_graph_rust_nested_grouped_use_resolves_every_level(tmp_path):
    # audit finding 33: "use std::{fmt, io::{self, Write}};" - a group item
    # can itself be a nested group, not just a bare identifier/self leaf.
    # The old code only accepted identifier/self children, so a nested
    # scoped_use_list matched neither type and was dropped along with
    # everything inside it - here, foo::{Bar, baz::{Qux}} must resolve
    # Bar (one level deep) AND Qux (two levels deep), not just Bar.
    repo = tmp_path / "repo"
    (repo / "src" / "foo").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (repo / "src" / "foo.rs").write_text("mod baz;\npub struct Bar;\n")
    (repo / "src" / "foo" / "baz.rs").write_text("pub struct Qux;\n")
    (repo / "src" / "main.rs").write_text(
        "mod foo;\n\nuse crate::foo::{Bar, baz::{Qux}};\n\nfn main() {}\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/main.rs", "src/foo.rs") in edges
    assert ("src/main.rs", "src/foo/baz.rs") in edges


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
    # Three, not two: one edge per name in the grouped use, plus one for the
    # `mod foo;` declaration. `mod` is a genuine file dependency - it is how the
    # module tree is declared - and counting only `use` left crates whose lib.rs
    # is all `mod` statements with no edges at all.
    assert len([e for e in dependency_graph["edges"] if e[0] == "src/main.rs"]) == 3


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


def test_rust_extracts_doc_comment_and_return_type(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.rs").write_text(
        "/// Adds two numbers.\npub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"
    )
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    func = by_path["src/main.rs"]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers."
    assert func["return_type"] == "i32"


def test_rust_extracts_multiline_doc_comment(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.rs").write_text(
        "/// Adds two numbers.\n/// Returns their sum.\npub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"
    )
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    func = by_path["src/main.rs"]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers.\nReturns their sum."


def test_rust_function_with_blank_line_before_comment_gets_no_docstring(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.rs").write_text(
        "/// Unrelated.\n\npub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"
    )
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    func = by_path["src/main.rs"]["symbols"]["functions"][0]
    assert func["docstring"] is None


def test_rust_struct_doc_comment_is_extracted(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.rs").write_text(
        "/// A widget.\npub struct Widget {\n    pub name: String,\n}\n"
    )
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    cls = by_path["src/main.rs"]["symbols"]["classes"][0]
    assert cls["docstring"] == "A widget."


def test_rust_pub_and_private_functions_are_classified_correctly(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.rs").write_text(
        "pub fn public_fn() {}\n\nfn private_fn() {}\n"
    )
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    by_name = {f["name"]: f for f in by_path["src/main.rs"]["symbols"]["functions"]}
    assert by_name["public_fn"]["is_public"] is True
    assert by_name["private_fn"]["is_public"] is False


def test_rust_fn_nested_only_in_a_closure_is_not_public(tmp_path):
    # A named fn whose only enclosing container is an anonymous closure
    # (no named fn anywhere further up, e.g. a top-level `static` holding
    # a closure) had no matching ancestor before closure_expression was
    # added to the shared node-type set, so it was still marked public.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.rs").write_text(
        "static CLOSURE: fn() = || {\n    fn inner() {}\n};\n\npub fn top_level() {}\n"
    )
    modules, _, _ = build_module_graph(repo)
    by_name = {f["name"]: f for f in modules[0]["symbols"]["functions"]}
    assert by_name["inner"]["is_public"] is False
    assert by_name["top_level"]["is_public"] is True


def test_build_module_graph_rust_workspace_member_resolves_crate_paths(tmp_path):
    """Cargo workspaces put each crate in its own subdirectory with its own
    Cargo.toml and src/, and `crate::` there means that crate, not the repo.
    Resolving against a single repo-root src/ meant a workspace resolved nothing:
    serde-rs/serde scanned as 208 modules with 0 edges and 208 one-file clusters."""
    repo = tmp_path / "repo"
    (repo / "member" / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[workspace]\nmembers = ["member"]\n')
    (repo / "member" / "Cargo.toml").write_text('[package]\nname = "m"\nversion = "0.1.0"\n')
    (repo / "member" / "src" / "helper.rs").write_text("pub struct Widget;\n")
    (repo / "member" / "src" / "lib.rs").write_text(
        "mod helper;\n\nuse crate::helper::Widget;\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(e) for e in dependency_graph["edges"]}
    assert ("member/src/lib.rs", "member/src/helper.rs") in edges


def test_build_module_graph_rust_bare_mod_declaration_is_an_edge(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (repo / "src" / "thing.rs").write_text("pub const V: i32 = 1;\n")
    (repo / "src" / "lib.rs").write_text("mod thing;\n")

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(e) for e in dependency_graph["edges"]}
    assert ("src/lib.rs", "src/thing.rs") in edges


def test_build_module_graph_rust_inline_mod_body_is_not_an_edge(tmp_path):
    """`mod foo { ... }` defines the module inline - there is no separate file
    to depend on, so it must not manufacture an edge."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    (repo / "src" / "lib.rs").write_text("mod foo { pub fn f() {} }\n")

    _, dependency_graph, _ = build_module_graph(repo)
    assert dependency_graph["edges"] == []
