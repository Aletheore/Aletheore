"""Cargo target files are not implicitly modules of sibling targets."""

import pytest

from aletheore.scanner.graph import build_module_graph
from aletheore.scanner.rust_resolve import target_crate_alias


def _repo(tmp_path, target, source):
    files = {
        "Cargo.toml": '[package]\nname = "bytes"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/lib.rs": "pub struct Buf;\n",
        f"{target}/runner.rs": source,
        f"{target}/bytes.rs": "pub struct Buf;\n",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("target", ["benches", "examples", "tests"])
def test_target_imports_library_not_undeclared_sibling(tmp_path, target):
    repo = _repo(tmp_path, target, "use bytes::Buf;\nfn main() {}\n")
    modules, graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in graph["edges"]}
    assert (f"{target}/runner.rs", "src/lib.rs") in edges
    assert (f"{target}/runner.rs", f"{target}/bytes.rs") not in edges
    importer = next(m for m in modules if m["path"] == f"{target}/runner.rs")
    assert importer["import_confidence"]["src/lib.rs"] == "inferred"


def test_explicit_local_module_still_shadows_the_crate(tmp_path):
    repo = _repo(tmp_path, "tests", "mod bytes;\nuse bytes::Buf;\n")
    _, graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in graph["edges"]}
    assert ("tests/runner.rs", "tests/bytes.rs") in edges
    assert ("tests/runner.rs", "src/lib.rs") not in edges


def test_reexport_from_inferred_crate_is_also_inferred(tmp_path):
    repo = _repo(tmp_path, "benches", "use bytes::Buf;\n")
    (repo / "src/lib.rs").write_text("mod inner;\npub use inner::Buf;\n", encoding="utf-8")
    (repo / "src/inner.rs").write_text("pub struct Buf;\n", encoding="utf-8")
    modules, graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in graph["edges"]}
    assert ("benches/runner.rs", "src/inner.rs") in edges
    importer = next(m for m in modules if m["path"] == "benches/runner.rs")
    assert importer["import_confidence"]["src/inner.rs"] == "inferred"


def test_alias_is_not_replaced_by_crate_guess(tmp_path):
    repo = _repo(tmp_path, "examples", "use crate::other as bytes;\nuse bytes::Buf;\n")
    assert target_crate_alias(repo, repo / "examples/runner.rs", "bytes::Buf") is None
