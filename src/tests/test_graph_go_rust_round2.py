"""Go same-package coupling and the remaining Rust resolution cases (synthetic repos)."""

from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def scan(repo: Path):
    modules, graph, _ = build_module_graph(repo)
    return modules, {tuple(e) for e in graph["edges"]}


GO_MOD = "module example.com/m\n\ngo 1.21\n"


def go_repo(tmp_path, files):
    return write(tmp_path / "r", {"go.mod": GO_MOD, **files})


# ---------------------------------------------------------------- Go same-package


def test_go_function_use_links_declaring_file(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() int { return helper() }\n",
        "b.go": "package m\n\nfunc helper() int { return 1 }\n",
    })
    assert ("a.go", "b.go") in scan(repo)[1]
    assert ("b.go", "a.go") not in scan(repo)[1]


def test_go_type_and_const_uses_link(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nvar x Thing = Limit\n",
        "b.go": "package m\n\ntype Thing struct{}\n\nconst Limit = 3\n",
    })
    assert ("a.go", "b.go") in scan(repo)[1]


def test_go_selector_field_is_not_a_use(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A(s S) int { return s.Count }\n\ntype S struct{ Count int }\n",
        "b.go": "package m\n\nfunc Count() int { return 1 }\n",
    })
    assert ("a.go", "b.go") not in scan(repo)[1]


def test_go_struct_literal_key_is_not_a_use(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\ntype S struct{ Name string }\n\nvar v = S{Name: \"x\"}\n",
        "b.go": "package m\n\nfunc Name() string { return \"\" }\n",
    })
    assert ("a.go", "b.go") not in scan(repo)[1]


def test_go_local_variable_shadows_package_name(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() int {\n\thelper := 2\n\treturn helper\n}\n",
        "b.go": "package m\n\nfunc helper() int { return 1 }\n",
    })
    assert ("a.go", "b.go") not in scan(repo)[1]


def test_go_parameter_shadows_package_name(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A(helper int) int { return helper }\n",
        "b.go": "package m\n\nfunc helper() int { return 1 }\n",
    })
    assert ("a.go", "b.go") not in scan(repo)[1]


def test_go_name_declared_in_two_plain_files_is_ambiguous(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { shared() }\n",
        "b.go": "package m\n\nfunc shared() {}\n",
        "c.go": "package m\n\nfunc shared() {}\n",
    })
    edges = scan(repo)[1]
    assert ("a.go", "b.go") not in edges and ("a.go", "c.go") not in edges


def test_go_build_tag_variants_prefer_the_unconditional_file(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { validate() }\n",
        "plain.go": "package m\n\nfunc validate() {}\n",
        "alt.go": "//go:build nomsgpack\n\npackage m\n\nfunc validate() {}\n",
    })
    modules, edges = scan(repo)
    assert ("a.go", "plain.go") in edges
    assert ("a.go", "alt.go") not in edges


def test_go_all_conditional_variants_are_linked_and_marked_inferred(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { impl() }\n",
        "x_linux.go": "package m\n\nfunc impl() {}\n",
        "x_windows.go": "package m\n\nfunc impl() {}\n",
    })
    modules, edges = scan(repo)
    assert ("a.go", "x_linux.go") in edges and ("a.go", "x_windows.go") in edges
    a = next(m for m in modules if m["path"] == "a.go")
    assert a["import_confidence"]["x_linux.go"] == "inferred"


def test_go_non_test_file_does_not_depend_on_test_file_declarations(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { onlyInTests() }\n",
        "a_test.go": "package m\n\nfunc onlyInTests() {}\n",
        "b_test.go": "package m\n\nfunc B() { onlyInTests() }\n",
    })
    edges = scan(repo)[1]
    assert ("a.go", "a_test.go") not in edges
    assert ("b_test.go", "a_test.go") in edges


def test_go_package_imports_still_resolve(tmp_path):
    repo = go_repo(tmp_path, {
        "main.go": "package m\n\nimport \"example.com/m/sub\"\n\nvar _ = sub.F\n",
        "sub/s.go": "package sub\n\nfunc F() {}\n",
    })
    assert ("main.go", "sub/s.go") in scan(repo)[1]


# ---------------------------------------------------------------- Rust


def pkg(name: str) -> str:
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n'


def test_rust_pub_use_reexport_reaches_defining_file(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod internals;\nmod user;\n",
        "src/internals/mod.rs": "mod ctxt;\npub use self::ctxt::Ctxt;\n",
        "src/internals/ctxt.rs": "pub struct Ctxt;\n",
        "src/user.rs": "use crate::internals::Ctxt;\n",
    })
    edges = scan(repo)[1]
    assert ("src/user.rs", "src/internals/mod.rs") in edges
    assert ("src/user.rs", "src/internals/ctxt.rs") in edges


def test_rust_glob_reexport_reaches_defining_file(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod shapes;\nmod user;\n",
        "src/shapes/mod.rs": "mod circle;\npub use circle::*;\n",
        "src/shapes/circle.rs": "pub struct Circle;\n",
        "src/user.rs": "use crate::shapes::Circle;\n",
    })
    assert ("src/user.rs", "src/shapes/circle.rs") in scan(repo)[1]


def test_rust_private_use_is_not_a_reexport(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod shapes;\nmod user;\n",
        "src/shapes/mod.rs": "mod circle;\nuse circle::Circle;\n",
        "src/shapes/circle.rs": "pub struct Circle;\n",
        "src/user.rs": "use crate::shapes::Circle;\n",
    })
    assert ("src/user.rs", "src/shapes/circle.rs") not in scan(repo)[1]


def test_rust_item_declared_in_module_does_not_look_through(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod a;\nmod user;\n",
        "src/a.rs": "pub struct T;\npub use other::T as Other;\n",
        "src/other.rs": "pub struct T;\n",
        "src/user.rs": "use crate::a::T;\n",
    })
    assert ("src/user.rs", "src/other.rs") not in scan(repo)[1]


def test_rust_crate_level_reexported_module_name_is_not_a_file_module(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "pub use elsewhere::{ser};\nmod user;\npub use derive::Serialize;\n",
        "src/user.rs": "use crate::ser::Serialize;\n",
        "src/derive.rs": "pub struct Serialize;\n",
    })
    assert ("src/user.rs", "src/derive.rs") not in scan(repo)[1]


def test_rust_include_literal_path_links_file(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": 'include!("generated.rs");\ninclude!("data.txt");\n',
        "src/generated.rs": "pub fn g() {}\n",
        "src/data.txt": "x",
    })
    edges = scan(repo)[1]
    assert ("src/lib.rs", "src/generated.rs") in edges
    assert not any(t.endswith("data.txt") for _, t in edges)


def test_rust_path_attribute_inside_inline_module(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": 'mod outer {\n    #[path = "impl_b.rs"]\n    mod b;\n}\n',
        "src/outer/impl_b.rs": "pub fn b() {}\n",
    })
    assert ("src/lib.rs", "src/outer/impl_b.rs") in scan(repo)[1]


# ---------------------------------------------------------------- caches across scans in one process


def test_rescan_sees_new_and_deleted_module_files(tmp_path):
    repo = write(tmp_path / "r", {"Cargo.toml": pkg("c"), "src/lib.rs": "mod x;\n"})
    assert ("src/lib.rs", "src/x.rs") not in scan(repo)[1]
    write(repo, {"src/x.rs": "pub fn x() {}\n"})
    assert ("src/lib.rs", "src/x.rs") in scan(repo)[1]
    (repo / "src" / "x.rs").unlink()
    assert ("src/lib.rs", "src/x.rs") not in scan(repo)[1]


def test_rescan_sees_changed_cargo_dependencies(tmp_path):
    repo = write(tmp_path / "r", {
        "core_lib/Cargo.toml": pkg("core_lib"),
        "core_lib/src/lib.rs": "pub struct X;\n",
        "app/Cargo.toml": pkg("app"),
        "app/src/lib.rs": "use core_lib::X;\n",
    })
    assert ("app/src/lib.rs", "core_lib/src/lib.rs") not in scan(repo)[1]
    (repo / "app" / "Cargo.toml").write_text(pkg("app") + '\n[dependencies]\ncore_lib = { path = "../core_lib" }\n', encoding="utf-8")
    assert ("app/src/lib.rs", "core_lib/src/lib.rs") in scan(repo)[1]


def test_rescan_sees_new_macro_declared_module_and_go_declaration(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod m;\nm::dummy!();\nroot!();\n",
        "src/m.rs": "macro_rules! root {\n    () => {\n        mod a;\n    };\n}\n",
        "src/a.rs": "",
    })
    assert ("src/lib.rs", "src/a.rs") in scan(repo)[1]
    (repo / "src" / "m.rs").write_text("macro_rules! root {\n    () => {\n        mod b;\n    };\n}\n", encoding="utf-8")
    write(repo, {"src/b.rs": ""})
    edges = scan(repo)[1]
    assert ("src/lib.rs", "src/b.rs") in edges and ("src/lib.rs", "src/a.rs") not in edges

    go = go_repo(tmp_path / "g", {"a.go": "package m\n\nfunc A() { later() }\n"})
    assert not scan(go)[1]
    write(go, {"b.go": "package m\n\nfunc later() {}\n"})
    assert ("a.go", "b.go") in scan(go)[1]


# ---------------------------------------------------------------- nested block comments


def test_commented_out_mod_and_paths_inside_nested_block_comments_make_no_edges(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": (
            "mod real;\n"
            "/* outer /* inner */ mod ghost; let _ = crate::ghost::X; */\n"
            "pub fn f() { let _ = crate::real::R; }\n"
        ),
        "src/real.rs": "pub struct R;\n",
        "src/ghost.rs": "pub struct X;\n",
    })
    edges = scan(repo)[1]
    assert ("src/lib.rs", "src/real.rs") in edges
    assert ("src/lib.rs", "src/ghost.rs") not in edges


def test_clean_source_keeps_code_after_nested_comment():
    from aletheore.scanner.rust_resolve import clean_source

    cleaned = clean_source("a /* x /* y */ z */ b \"/* not a comment */\" c // tail\nd")
    assert "z" not in cleaned and "b" in cleaned and "c" in cleaned and "d" in cleaned and "tail" not in cleaned
