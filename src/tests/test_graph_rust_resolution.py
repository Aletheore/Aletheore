"""Rust import resolution beyond plain `use crate::module` (synthetic crates)."""

from pathlib import Path

from aletheore.scanner import rust_resolve
from aletheore.scanner.graph import build_module_graph


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def edges_of(repo: Path) -> set[tuple[str, str]]:
    _, graph, _ = build_module_graph(repo)
    return {tuple(edge) for edge in graph["edges"]}


def pkg(name: str, deps: str = "") -> str:
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n{deps}'


def test_workspace_crate_name_resolves_to_sibling_crate(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": '[workspace]\nmembers = ["core_lib", "app"]\n',
        "core_lib/Cargo.toml": pkg("core-lib"),
        "core_lib/src/lib.rs": "pub mod de;\n",
        "core_lib/src/de.rs": "pub struct D;\n",
        "app/Cargo.toml": pkg("app", 'core-lib = { path = "../core_lib" }\n'),
        "app/src/lib.rs": "use core_lib::de::D;\nuse core_lib::Root;\nuse std::fmt;\n",
    })
    edges = edges_of(repo)
    assert ("app/src/lib.rs", "core_lib/src/de.rs") in edges
    assert ("app/src/lib.rs", "core_lib/src/lib.rs") in edges
    assert not any(target.startswith("std") for _, target in edges)


def test_crate_not_in_dependencies_is_not_linked(tmp_path):
    repo = write(tmp_path / "r", {
        "core_lib/Cargo.toml": pkg("core_lib"),
        "core_lib/src/lib.rs": "pub struct X;\n",
        "app/Cargo.toml": pkg("app"),
        "app/src/lib.rs": "use core_lib::X;\n",
    })
    assert ("app/src/lib.rs", "core_lib/src/lib.rs") not in edges_of(repo)


def test_use_crate_item_defined_in_crate_root_links_to_root(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod a;\nstruct private;\n",
        "src/a.rs": "use crate::private;\n",
    })
    assert ("src/a.rs", "src/lib.rs") in edges_of(repo)


def test_use_super_item_links_to_parent_module_file(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod a;\n",
        "src/a/mod.rs": "mod b;\npub struct Shared;\n",
        "src/a/b.rs": "use super::Shared;\nuse super::*;\n",
    })
    assert ("src/a/b.rs", "src/a/mod.rs") in edges_of(repo)


def test_super_inside_inline_test_module_does_not_climb_out_of_the_file(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod a;\npub struct Top;\n",
        "src/a.rs": "pub fn f() {}\n#[cfg(test)]\nmod tests {\n    use super::*;\n}\n",
    })
    assert ("src/a.rs", "src/lib.rs") not in edges_of(repo)


def test_path_attribute_and_cfg_attr_path_resolve(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": '#[path = "other/impl_a.rs"]\nmod a;\n#[cfg_attr(feature = "x", path = "elsewhere/b.rs")]\nmod b;\n',
        "src/other/impl_a.rs": "pub fn a() {}\n",
        "src/b.rs": "pub fn b() {}\n",
        "src/elsewhere/b.rs": "pub fn b() {}\n",
    })
    edges = edges_of(repo)
    assert ("src/lib.rs", "src/other/impl_a.rs") in edges
    assert ("src/lib.rs", "src/b.rs") in edges
    assert ("src/lib.rs", "src/elsewhere/b.rs") in edges


def test_path_attribute_cannot_escape_the_repo(tmp_path):
    outside = tmp_path / "outside.rs"
    outside.write_text("pub fn x() {}\n", encoding="utf-8")
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": '#[path = "../../../outside.rs"]\nmod evil;\n',
    })
    assert edges_of(repo) == set()


def test_modules_declared_inside_zero_arg_macro_are_linked(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "#[macro_use]\nmod root_macro;\n\nroot_macro!();\n",
        "src/root_macro.rs": "macro_rules! root_macro {\n    () => {\n        pub mod de;\n        pub mod ser;\n    };\n}\n",
        "src/de.rs": "pub struct D;\n",
        "src/ser.rs": "pub struct S;\n",
    })
    edges = edges_of(repo)
    assert ("src/lib.rs", "src/de.rs") in edges
    assert ("src/lib.rs", "src/ser.rs") in edges
    # the macro file itself does not declare them, rustc expands them into lib.rs
    assert ("src/root_macro.rs", "src/de.rs") not in edges


def test_macro_with_arguments_is_not_expanded(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod m;\nm::thing!(x);\n",
        "src/m.rs": "macro_rules! thing {\n    ($x:ident) => { mod de; };\n}\n",
        "src/de.rs": "",
    })
    assert ("src/lib.rs", "src/de.rs") not in edges_of(repo)


def test_inline_crate_path_without_use_links_target_module(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod a;\nmod fmt_buf;\n",
        "src/fmt_buf.rs": "pub struct Buf;\n",
        "src/a.rs": "// crate::ignored::in_comment\npub fn f() { let _ = crate::fmt_buf::Buf; let _ = \"crate::nope::x\"; }\n",
    })
    edges = edges_of(repo)
    assert ("src/a.rs", "src/fmt_buf.rs") in edges
    assert not any("nope" in target or "ignored" in target for _, target in edges)


def test_item_name_does_not_match_module_file_with_different_case(tmp_path):
    # `Ctxt` is a type re-exported by internals/mod.rs; internals/ctxt.rs is a different, lower-case module.
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod internals;\nmod user;\n",
        "src/internals/mod.rs": "mod ctxt;\npub use self::ctxt::Ctxt;\n",
        "src/internals/ctxt.rs": "pub struct Ctxt;\n",
        "src/user.rs": "use crate::internals::Ctxt;\n",
    })
    edges = edges_of(repo)
    assert ("src/user.rs", "src/internals/mod.rs") in edges
    assert all(not target.endswith("Ctxt.rs") for _, target in edges)


def test_exact_case_directory_helpers(tmp_path):
    (tmp_path / "ctxt.rs").write_text("", encoding="utf-8")
    assert rust_resolve.file_exists_exact(tmp_path, "ctxt.rs")
    assert not rust_resolve.file_exists_exact(tmp_path, "Ctxt.rs")


def test_integration_test_root_resolves_its_own_mod_files(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "",
        "tests/test_a.rs": "mod macros;\n",
        "tests/macros/mod.rs": "",
    })
    assert ("tests/test_a.rs", "tests/macros/mod.rs") in edges_of(repo)


def test_flatten_use_groups_and_aliases():
    assert rust_resolve.flatten_use("serde_core::{de, ser::{self, Serialize as S}, Deserialize}") == [
        "serde_core::de",
        "serde_core::ser",
        "serde_core::ser::Serialize",
        "serde_core::Deserialize",
    ]
    assert rust_resolve.flatten_use("crate::a::*") == ["crate::a"]


def _facade_workspace(tmp_path, reexport: str):
    """core crate defines `de`; facade crate re-exports it (`pub use core::de`), directly or inside a macro."""
    repo = tmp_path / "ws"
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["core", "facade", "user"]\n',
        "core/Cargo.toml": '[package]\nname = "core_lib"\nversion = "0.1.0"\n',
        "core/src/lib.rs": "pub mod de;\npub mod other;\n",
        "core/src/de/mod.rs": "pub trait Deserialize {}\npub mod value;\n",
        "core/src/de/value.rs": "pub struct V;\n",
        "core/src/other.rs": "pub struct O;\n",
        "facade/Cargo.toml": '[package]\nname = "facade"\nversion = "0.1.0"\n[dependencies]\ncore_lib = { path = "../core" }\n',
        "facade/src/lib.rs": reexport,
        "facade/src/inner.rs": "use crate::de::Deserialize;\npub struct I;\n",
        "user/Cargo.toml": '[package]\nname = "user"\nversion = "0.1.0"\n[dependencies]\nfacade = { path = "../facade" }\n',
        "user/src/lib.rs": "use facade::de::Deserialize;\nuse facade::de::value::V;\n",
    }
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return {m["path"]: set(m["imports"]) for m in modules}


def test_rust_crate_path_follows_a_module_reexported_from_a_sibling_crate(tmp_path):
    imp = _facade_workspace(tmp_path, "pub use core_lib::{de, other};\nmod inner;\n")
    assert "core/src/de/mod.rs" in imp["facade/src/inner.rs"]


def test_rust_crate_path_follows_a_module_reexported_inside_a_macro(tmp_path):
    macro = "macro_rules! root {\n    () => {\n        pub use core_lib::{de, other};\n        mod inner;\n    };\n}\nroot!();\n"
    imp = _facade_workspace(tmp_path, macro)
    assert "core/src/de/mod.rs" in imp["facade/src/inner.rs"]


def test_rust_other_crate_path_adds_the_reexported_module_next_to_the_facade_root(tmp_path):
    imp = _facade_workspace(tmp_path, "pub use core_lib::{de, other};\nmod inner;\n")
    # the facade root stays a dependency (the path goes through it); the module is added, not its submodules
    assert {"facade/src/lib.rs", "core/src/de/mod.rs"} <= imp["user/src/lib.rs"]
    assert "core/src/de/value.rs" not in imp["user/src/lib.rs"]


def test_rust_private_use_is_not_treated_as_a_reexport(tmp_path):
    imp = _facade_workspace(tmp_path, "use core_lib::{de, other};\nmod inner;\n")
    assert "core/src/de/mod.rs" not in imp["facade/src/inner.rs"]
