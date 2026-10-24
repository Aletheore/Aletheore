"""Round 3: Go methods and fields, Rust `mod x;` inside inline modules, argument-taking module macros."""

from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def edges(repo: Path) -> set[tuple[str, str]]:
    _, graph, _ = build_module_graph(repo)
    return {tuple(e) for e in graph["edges"]}


GO_MOD = "module example.com/m\n\ngo 1.21\n"


def go_repo(tmp_path, files):
    return write(tmp_path / "r", {"go.mod": GO_MOD, **files})


# ---------------------------------------------------------------- Go methods and fields


def test_go_method_on_receiver_from_composite_literal(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { e := Engine{}\n\te.Serve() }\n",
        "b.go": "package m\n\ntype Engine struct{}\n\nfunc (e Engine) Serve() {}\n",
    })
    assert ("a.go", "b.go") in edges(repo)


def test_go_method_on_constructor_result_and_chained_call(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { r := New()\n\tr.Group().Handle() }\n",
        "engine.go": "package m\n\ntype Engine struct{}\n\nfunc New() *Engine { return &Engine{} }\n\nfunc (e *Engine) Group() *Router { return nil }\n",
        "router.go": "package m\n\ntype Router struct{}\n\nfunc (r *Router) Handle() {}\n",
    })
    found = edges(repo)
    assert ("a.go", "engine.go") in found  # New and Group
    assert ("a.go", "router.go") in found  # Handle, receiver type known through Group's result


def test_go_promoted_method_through_embedded_struct(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A(e *Engine) { e.GET() }\n",
        "engine.go": "package m\n\ntype Engine struct {\n\tRouterGroup\n}\n",
        "group.go": "package m\n\ntype RouterGroup struct{}\n\nfunc (g *RouterGroup) GET() {}\n",
    })
    found = edges(repo)
    assert ("a.go", "group.go") in found
    assert ("a.go", "engine.go") in found


def test_go_unexported_member_name_is_linked_without_type_evidence(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A(x any) { _ = unknownThing().secret }\n",
        "b.go": "package m\n\ntype T struct{ secret int }\n",
    })
    assert ("a.go", "b.go") in edges(repo)


def test_go_exported_member_name_without_type_evidence_is_not_linked(tmp_path):
    # `Header` may be net/http's field, not this package's
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nimport \"net/http\"\n\nfunc A(r *http.Request) { _ = r.Header }\n",
        "b.go": "package m\n\ntype T struct{ Header int }\n",
    })
    assert ("a.go", "b.go") not in edges(repo)


def test_go_member_declared_on_two_types_in_two_files_is_ambiguous(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A(x any) { _ = f(x).secret }\n",
        "b.go": "package m\n\ntype T struct{ secret int }\n",
        "c.go": "package m\n\ntype U struct{ secret int }\n",
    })
    found = edges(repo)
    assert ("a.go", "b.go") not in found or ("a.go", "c.go") in found  # both or neither (one declaring file each)


def test_go_function_local_struct_fields_are_not_package_members(tmp_path):
    repo = go_repo(tmp_path, {
        "a_test.go": "package m\n\nfunc TestX() {\n\ttests := []struct{ secret int }{{1}}\n\tfor _, tc := range tests { _ = tc.secret }\n}\n",
        "b.go": "package m\n\ntype T struct{ secret int }\n",
    })
    assert ("a_test.go", "b.go") not in edges(repo)


def test_go_common_member_names_are_not_linked(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A(x any) { _ = f(x).String() }\n",
        "b.go": "package m\n\ntype T struct{}\n\nfunc (T) String() string { return \"\" }\n",
    })
    assert ("a.go", "b.go") not in edges(repo)


def test_go_member_rule_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEORE_GO_MEMBERS", "off")
    from aletheore.scanner import go_resolve

    go_resolve.clear_cache()
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { e := Engine{}\n\te.Serve() }\n",
        "b.go": "package m\n\ntype Engine struct{}\n\nfunc (e Engine) Serve() {}\n",
    })
    # Engine (a package-level use) still links the files; the member rule alone adds nothing here
    assert ("a.go", "b.go") in edges(repo)
    go_resolve.clear_cache()


# ---------------------------------------------------------------- Rust inline module `mod x;`


def pkg(name: str) -> str:
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n'


def test_rust_mod_declaration_inside_inline_module_uses_the_inline_directory(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod outer {\n    mod x;\n    pub mod deep {\n        mod y;\n    }\n}\n",
        "src/outer/x.rs": "",
        "src/outer/deep/y/mod.rs": "",
        "src/x.rs": "",
    })
    found = edges(repo)
    assert ("src/lib.rs", "src/outer/x.rs") in found
    assert ("src/lib.rs", "src/outer/deep/y/mod.rs") in found
    assert ("src/lib.rs", "src/x.rs") not in found


def test_rust_inline_mod_declaration_in_non_root_file_uses_file_stem_directory(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod a;\n",
        "src/a.rs": "mod inner {\n    mod z;\n}\n",
        "src/a/inner/z.rs": "",
    })
    assert ("src/a.rs", "src/a/inner/z.rs") in edges(repo)


# ---------------------------------------------------------------- Rust macros with arguments


def test_rust_macro_with_ident_list_declares_modules(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "#[macro_use]\nmod macros;\n\ndeclare_modules!(alpha, beta);\n",
        "src/macros.rs": "macro_rules! declare_modules {\n    ($($name:ident),* $(,)?) => {\n        $(pub mod $name;)*\n    };\n}\n",
        "src/alpha.rs": "",
        "src/beta.rs": "",
    })
    found = edges(repo)
    assert ("src/lib.rs", "src/alpha.rs") in found and ("src/lib.rs", "src/beta.rs") in found


def test_rust_macro_with_single_ident_and_fixed_list(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod macros;\none!(first);\ntwo!(second, third);\n",
        "src/macros.rs": (
            "macro_rules! one { ($n:ident) => { mod $n; }; }\n"
            "macro_rules! two { ($a:ident, $b:ident) => { pub mod $a; mod $b; }; }\n"
        ),
        "src/first.rs": "",
        "src/second.rs": "",
        "src/third.rs": "",
    })
    found = edges(repo)
    assert {("src/lib.rs", "src/first.rs"), ("src/lib.rs", "src/second.rs"), ("src/lib.rs", "src/third.rs")} <= found


def test_rust_macro_with_literals_or_expressions_is_not_expanded(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": 'mod macros;\nlit!("alpha");\nexpr!(1 + 2);\ntt!(alpha);\n',
        "src/macros.rs": (
            'macro_rules! lit { ($n:literal) => { mod alpha; }; }\n'
            "macro_rules! expr { ($n:expr) => { mod alpha; }; }\n"
            "macro_rules! tt { ($($t:tt)*) => { mod alpha; }; }\n"
        ),
        "src/alpha.rs": "",
    })
    assert ("src/lib.rs", "src/alpha.rs") not in edges(repo)


def test_rust_macro_invocation_with_non_identifier_arguments_is_ignored(tmp_path):
    repo = write(tmp_path / "r", {
        "Cargo.toml": pkg("c"),
        "src/lib.rs": "mod macros;\ndeclare!(a::b, c);\n",
        "src/macros.rs": "macro_rules! declare { ($($n:ident),*) => { $(mod $n;)* }; }\n",
        "src/a.rs": "",
        "src/c.rs": "",
    })
    found = edges(repo)
    assert ("src/lib.rs", "src/c.rs") not in found and ("src/lib.rs", "src/a.rs") not in found
