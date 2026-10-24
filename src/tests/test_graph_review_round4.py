"""Review follow-ups: cache fingerprints that cover config files and Go siblings, Rust include!/glob edge cases,
Java wildcard shadowing, Go paths given relative to the repo."""
import copy
from pathlib import Path

from aletheore.scanner import go_resolve, rust_resolve
from aletheore.scanner.graph import build_module_graph


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _edges(modules) -> dict[str, list[str]]:
    return {m["path"]: sorted(m["imports"]) for m in modules}


def _first_scan(repo: Path):
    sigs: dict[str, str] = {}
    modules, _g, _u = build_module_graph(repo, signatures_out=sigs)
    return modules, sigs


def _incremental(repo: Path, previous_modules, previous_sigs, changed: set[str]):
    cache = {m["path"]: copy.deepcopy(m) for m in previous_modules if m["path"] not in changed}
    go_resolve.clear_cache()
    modules, _g, _u = build_module_graph(repo, unchanged_modules=cache, prior_signatures=previous_sigs, signatures_out={})
    return modules


def test_go_cached_file_goes_stale_when_a_sibling_moves_a_declaration(tmp_path):
    _write(tmp_path, "p/a.go", "package p\n\nfunc Run() { Helper() }\n")
    _write(tmp_path, "p/b.go", "package p\n\nfunc Helper() {}\n")
    first, sigs = _first_scan(tmp_path)
    assert _edges(first)["p/a.go"] == ["p/b.go"]
    _write(tmp_path, "p/b.go", "package p\n\nfunc Other() {}\n")
    _write(tmp_path, "p/c.go", "package p\n\nfunc Helper() {}\n")
    go_resolve.clear_cache()
    clean, _ = _first_scan(tmp_path)
    assert _edges(clean)["p/a.go"] == ["p/c.go"]
    assert _edges(_incremental(tmp_path, first, sigs, {"p/b.go", "p/c.go"})) == _edges(clean)


def test_go_body_only_sibling_edit_keeps_cached_modules(tmp_path):
    _write(tmp_path, "p/a.go", "package p\n\nfunc Run() { Helper() }\n")
    _write(tmp_path, "p/b.go", "package p\n\nfunc Helper() {}\n")
    first, sigs = _first_scan(tmp_path)
    _write(tmp_path, "p/b.go", "package p\n\nfunc Helper() { _ = 1 }\n")
    go_resolve.clear_cache()
    cache = {m["path"]: m for m in first if m["path"] != "p/b.go"}
    modules, _g, _u = build_module_graph(tmp_path, unchanged_modules=cache, prior_signatures=sigs)
    assert any(m is cache["p/a.go"] for m in modules)


def test_ts_cached_module_goes_stale_when_tsconfig_gains_paths(tmp_path):
    _write(tmp_path, "src/a.ts", 'import { x } from "@/util";\nexport const y = x;\n')
    _write(tmp_path, "src/util.ts", "export const x = 1;\n")
    first, sigs = _first_scan(tmp_path)
    assert _edges(first)["src/a.ts"] == []
    _write(tmp_path, "tsconfig.json", '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}')
    clean, _ = _first_scan(tmp_path)
    assert _edges(clean)["src/a.ts"] == ["src/util.ts"]
    assert _edges(_incremental(tmp_path, first, sigs, set())) == _edges(clean)


def test_go_same_package_targets_with_a_relative_repo_path(tmp_path, monkeypatch):
    _write(tmp_path, "p/a.go", "package p\n\nfunc Run() { Helper() }\n")
    _write(tmp_path, "p/b.go", "package p\n\nfunc Helper() {}\n")
    monkeypatch.chdir(tmp_path)
    go_resolve.clear_cache()
    assert go_resolve.same_package_targets(Path("."), tmp_path / "p" / "a.go") == ["p/b.go"]


def test_rust_include_in_a_block_comment_is_not_an_import(tmp_path):
    _write(tmp_path, "Cargo.toml", '[package]\nname = "c"\nversion = "0.1.0"\n')
    _write(tmp_path, "src/gen.rs", "pub fn g() {}\n")
    live = 'include!("gen.rs");\n'
    assert rust_resolve.extra_imports(tmp_path, tmp_path / "src/lib.rs", live) == ["@path:gen.rs"]
    assert rust_resolve.extra_imports(tmp_path, tmp_path / "src/lib.rs", f"/* {live} */\n// {live}") == []


def test_rust_bare_glob_keeps_nothing_without_a_prefix():
    assert rust_resolve.flatten_use("*", keep_glob=True) == []
    assert rust_resolve.flatten_use("a::*", keep_glob=True) == ["a::*"]
    assert rust_resolve.flatten_use("a::{*, b}", keep_glob=True) == ["a::*", "a::b"]


def test_java_own_declaration_shadows_a_wildcard_import(tmp_path):
    _write(tmp_path, "src/a/Foo.java", "package a;\npublic class Foo {}\n")
    _write(tmp_path, "src/b/Use.java", "package b;\nimport a.*;\npublic class Use { Foo f; }\nclass Foo {}\n")
    _write(tmp_path, "src/d/Plain.java", "package d;\nimport a.*;\npublic class Plain { Foo f; }\n")
    edges = _edges(build_module_graph(tmp_path)[0])
    assert edges["src/b/Use.java"] == []
    assert edges["src/d/Plain.java"] == ["src/a/Foo.java"]
