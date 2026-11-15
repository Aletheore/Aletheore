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


def test_rust_mod_declared_inside_an_item_wrapping_macro_invocation_links_its_file(tmp_path):
    repo = write(tmp_path / "mac", {
        "Cargo.toml": pkg("app"),
        "src/lib.rs": "mod sys;\n",
        "src/sys/mod.rs": "mod selector;\ncfg_net! {\n    pub(crate) mod tcp;\n    pub(crate) mod udp;\n}\n",
        "src/sys/selector.rs": "pub struct S;\n",
        "src/sys/tcp.rs": "pub struct T;\n",
        "src/sys/udp.rs": "pub struct U;\n",
    })
    edges = edges_of(repo)
    assert ("src/sys/mod.rs", "src/sys/tcp.rs") in edges
    assert ("src/sys/mod.rs", "src/sys/udp.rs") in edges
    assert ("src/sys/mod.rs", "src/sys/selector.rs") in edges


def test_rust_mod_placeholder_in_a_macro_rules_body_is_not_a_module(tmp_path):
    repo = write(tmp_path / "mac2", {
        "Cargo.toml": pkg("app"),
        "src/lib.rs": "macro_rules! decl { ($n:ident) => { mod $n; }; }\ndecl!(real);\n",
        "src/real.rs": "pub struct R;\n",
        "src/n.rs": "pub struct N;\n",
    })
    assert ("src/lib.rs", "src/n.rs") not in edges_of(repo)


def test_rust_mod_text_inside_a_macro_invocation_comment_or_string_is_not_a_module(tmp_path):
    repo = write(tmp_path / "mac3", {
        "Cargo.toml": pkg("app"),
        "src/lib.rs": 'cfg_net! {\n    // mod ghost;\n    pub(crate) mod real;\n    const NOTE: &str = "mod phantom;";\n}\n',
        "src/real.rs": "pub struct R;\n", "src/ghost.rs": "pub struct G;\n", "src/phantom.rs": "pub struct P;\n",
    })
    edges = edges_of(repo)
    assert ("src/lib.rs", "src/real.rs") in edges
    assert ("src/lib.rs", "src/ghost.rs") not in edges and ("src/lib.rs", "src/phantom.rs") not in edges


def _custom_root_repo(tmp_path, manifest: str, root_rel: str, extra: dict[str, str] | None = None):
    repo = tmp_path / "custom"
    files = {
        "Cargo.toml": manifest,
        root_rel: "mod flags;\nmod util;\nfn main() {}\n",
        f"{root_rel.rsplit('/', 1)[0]}/util.rs": "pub fn helper() {}\n",
        f"{root_rel.rsplit('/', 1)[0]}/flags/mod.rs": "pub mod defs;\nuse crate::util::helper;\n",
        f"{root_rel.rsplit('/', 1)[0]}/flags/defs.rs": "use crate::util::helper;\nuse super::other;\n",
        f"{root_rel.rsplit('/', 1)[0]}/flags/other.rs": "pub fn x() {}\n",
    }
    files.update(extra or {})
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return {m["path"]: set(m["imports"]) for m in modules}


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


def test_rust_bin_root_declared_in_the_manifest_outside_src(tmp_path):
    manifest = '[package]\nname = "app"\nversion = "0.1.0"\n[[bin]]\nname = "app"\npath = "crates/core/main.rs"\n'
    imp = _custom_root_repo(tmp_path, manifest, "crates/core/main.rs")
    assert {"crates/core/flags/mod.rs", "crates/core/util.rs"} <= imp["crates/core/main.rs"]
    assert "crates/core/util.rs" in imp["crates/core/flags/mod.rs"]
    assert "crates/core/util.rs" in imp["crates/core/flags/defs.rs"]
    assert "crates/core/flags/other.rs" in imp["crates/core/flags/defs.rs"]


def test_rust_lib_root_declared_in_the_manifest_outside_src(tmp_path):
    manifest = '[package]\nname = "app"\nversion = "0.1.0"\n[lib]\npath = "lib/lib.rs"\n'
    imp = _custom_root_repo(tmp_path, manifest, "lib/lib.rs")
    assert "lib/util.rs" in imp["lib/flags/defs.rs"]


def test_rust_default_src_layout_is_unchanged_when_the_manifest_has_no_paths(tmp_path):
    manifest = '[package]\nname = "app"\nversion = "0.1.0"\n'
    imp = _custom_root_repo(tmp_path, manifest, "src/main.rs")
    assert "src/util.rs" in imp["src/flags/defs.rs"]


def test_rust_manifest_path_outside_the_repo_is_ignored(tmp_path):
    manifest = '[package]\nname = "app"\nversion = "0.1.0"\n[[bin]]\nname = "app"\npath = "../../etc/main.rs"\n'
    imp = _custom_root_repo(tmp_path, manifest, "src/main.rs")
    assert "src/util.rs" in imp["src/flags/defs.rs"]


def test_rust_use_group_keeps_nested_paths_next_to_plain_names(tmp_path):
    # `use crate::flags::{defs::FLAGS, Other}`: the nested path `defs::FLAGS` is an entry of the group like `Other` is.
    repo = tmp_path / "grp"
    files = {
        "Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\n',
        "src/main.rs": "mod flags;\nmod user;\nfn main() {}\n",
        "src/flags/mod.rs": "pub mod defs;\npub mod doc;\npub struct Other;\n",
        "src/flags/defs.rs": "pub const FLAGS: u8 = 1;\n",
        "src/flags/doc.rs": "pub fn version() {}\n",
        "src/user.rs": "use crate::flags::{defs::FLAGS, doc::version, Other};\n",
        "src/multi.rs": "use crate::flags::{\n    defs::FLAGS,\n    doc::version,\n};\n",
    }
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    imp = {m["path"]: set(m["imports"]) for m in modules}
    assert {"src/flags/defs.rs", "src/flags/doc.rs", "src/flags/mod.rs"} <= imp["src/user.rs"]
    assert {"src/flags/defs.rs", "src/flags/doc.rs"} <= imp["src/multi.rs"]


def test_rust_top_level_use_list_keeps_nested_paths(tmp_path):
    from aletheore.scanner.graph import RUST_LANGUAGE, _extract_rust
    from tree_sitter import Parser

    src = b"use {std::fmt::Write, crate::a::b, c};\n"
    paths = _extract_rust(Parser(RUST_LANGUAGE).parse(src).root_node, src)[0]
    assert set(paths) == {"std::fmt::Write", "crate::a::b", "c"}


def test_rust_top_level_use_list_keeps_grouped_and_aliased_entries():
    from aletheore.scanner.graph import RUST_LANGUAGE, _extract_rust
    from tree_sitter import Parser

    src = b"use {\n    grep_matcher::{ByteSet, LineTerminator},\n    std::io as sio,\n    regex::*,\n};\n"
    paths = _extract_rust(Parser(RUST_LANGUAGE).parse(src).root_node, src)[0]
    assert {"grep_matcher::ByteSet", "grep_matcher::LineTerminator", "std::io", "regex"} <= set(paths)


def test_rust_integration_test_modules_resolve_crate_paths_to_their_test_root(tmp_path):
    repo = write(tmp_path / "it", {
        "Cargo.toml": pkg("app"),
        "src/lib.rs": "pub fn run() {}\n",
        "src/util.rs": "pub fn lib_util() {}\n",
        "tests/tests.rs": "mod util;\nmod binary;\n",
        "tests/util.rs": "pub fn helper() {}\n",
        "tests/binary.rs": "use crate::util::helper;\n",
    })
    imports = edges_of(repo)
    assert ("tests/binary.rs", "tests/util.rs") in imports
    assert ("tests/binary.rs", "src/util.rs") not in imports


def test_rust_test_file_without_a_declaring_root_does_not_guess_a_sibling(tmp_path):
    # Nothing in tests/ says `mod util;`, so `crate::util` is not resolved to tests/util.rs by name alone.
    repo = write(tmp_path / "it2", {
        "Cargo.toml": pkg("app"),
        "src/lib.rs": "pub fn run() {}\n",
        "tests/util.rs": "pub fn helper() {}\n",
        "tests/binary.rs": "use crate::util::helper;\n",
    })
    assert ("tests/binary.rs", "tests/util.rs") not in edges_of(repo)


def test_rust_use_crate_item_follows_the_root_pub_use_to_its_defining_file(tmp_path):
    repo = write(tmp_path / "re", {
        "Cargo.toml": pkg("app"),
        "src/lib.rs": "mod glob;\nmod serde_impl;\npub use crate::glob::{Glob, GlobSet};\n",
        "src/glob.rs": "pub struct Glob;\npub struct GlobSet;\n",
        "src/serde_impl.rs": "use crate::{Glob, GlobSet};\n",
    })
    imports = edges_of(repo)
    assert ("src/serde_impl.rs", "src/glob.rs") in imports
    assert ("src/serde_impl.rs", "src/lib.rs") in imports


def test_rust_test_target_in_a_subdirectory_resolves_crate_paths_to_its_main_rs(tmp_path):
    repo = write(tmp_path / "sub", {
        "Cargo.toml": pkg("app"),
        "src/lib.rs": "pub fn run() {}\n",
        "tests/builder/main.rs": "mod util;\nmod arg;\n",
        "tests/builder/util.rs": "pub fn helper() {}\n",
        "tests/builder/arg.rs": "use crate::util::helper;\n",
    })
    assert ("tests/builder/arg.rs", "tests/builder/util.rs") in edges_of(repo)


def test_rust_standalone_test_crate_is_not_mapped_to_a_siblings_modules(tmp_path):
    repo = write(tmp_path / "flat", {
        "Cargo.toml": pkg("app"),
        "src/lib.rs": "pub fn run() {}\n",
        "tests/tests.rs": "mod util;\nmod binary;\n",
        "tests/util.rs": "pub fn helper() {}\n",
        "tests/binary.rs": "use crate::util::helper;\n",
        "tests/standalone.rs": "use crate::util::helper;\n",  # its own crate, never declares `mod util;`
    })
    edges = edges_of(repo)
    assert ("tests/binary.rs", "tests/util.rs") in edges
    assert ("tests/standalone.rs", "tests/util.rs") not in edges


def test_rust_package_root_bin_path_without_a_src_directory(tmp_path):
    repo = write(tmp_path / "rootbin", {
        "Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\n[[bin]]\nname = "app"\npath = "main.rs"\n',
        "main.rs": "mod util;\nfn main() {}\n",
        "util.rs": "pub fn helper() {}\n",
        "other.rs": "use crate::util::helper;\n",
    })
    assert ("main.rs", "util.rs") in edges_of(repo)


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


def test_rust_glob_reexport_of_a_crate_follows_that_crates_own_reexport(tmp_path):
    # facade: `pub use builder_crate::*;`  builder_crate: `pub use crate::builder::Command;`  user: `use facade::Command;`
    repo = write(tmp_path / "glob", {
        "Cargo.toml": '[workspace]\nmembers = ["builder", "facade", "user"]\n',
        "builder/Cargo.toml": pkg("builder-crate"),
        "builder/src/lib.rs": "pub mod builder;\npub use crate::builder::Command;\n",
        "builder/src/builder/mod.rs": "mod command;\npub use self::command::Command;\n",
        "builder/src/builder/command.rs": "pub struct Command;\n",
        "facade/Cargo.toml": pkg("facade", 'builder-crate = { path = "../builder" }\n'),
        "facade/src/lib.rs": "pub use builder_crate::*;\n",
        "user/Cargo.toml": pkg("user", 'facade = { path = "../facade" }\n'),
        "user/src/lib.rs": "use facade::Command;\n",
    })
    assert ("user/src/lib.rs", "builder/src/builder/command.rs") in edges_of(repo)



def test_rust_inline_workspace_crate_path_follows_the_reexport_of_its_first_item(tmp_path):
    # `facade::Level::TRACE` written inline (no `use`): Level is re-exported from the core crate's metadata.rs
    repo = write(tmp_path / "inl", {
        "Cargo.toml": '[workspace]\nmembers = ["core", "facade", "app"]\n',
        "core/Cargo.toml": pkg("core-crate"),
        "core/src/lib.rs": "mod metadata;\npub use self::metadata::Level;\n",
        "core/src/metadata.rs": "pub struct Level;\n",
        "facade/Cargo.toml": pkg("facade", 'core-crate = { path = "../core" }\n'),
        "facade/src/lib.rs": "pub use core_crate::Level;\n",
        "app/Cargo.toml": pkg("app", 'facade = { path = "../facade" }\n'),
        "app/src/main.rs": "fn main() { let _ = facade::Level::TRACE; }\n",
    })
    assert ("app/src/main.rs", "core/src/metadata.rs") in edges_of(repo)


def test_reexport_chain_links_the_defining_file_not_the_modules_on_the_way(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod a;\nmod user;\n",
        "src/a/mod.rs": "mod b;\npub use b::Thing;\n",
        "src/a/b/mod.rs": "mod c;\npub use c::Thing;\n",
        "src/a/b/c.rs": "pub struct Thing;\n",
        "src/user.rs": "use crate::a::Thing;\n",
    })
    edges = edges_of(repo)
    assert ("src/user.rs", "src/a/b/c.rs") in edges  # where the item is defined
    assert ("src/user.rs", "src/a/mod.rs") in edges  # the module the path names
    assert ("src/user.rs", "src/a/b/mod.rs") not in edges  # only passed through
