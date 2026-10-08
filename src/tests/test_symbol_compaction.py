"""EVIDENCE_VERSION 0.8.1: symbol entries leave out params/docstring/return_type
when null and is_pure_declaration when false. Every reader must produce exactly
the same output from the compact form as from the old explicit-null form."""

import copy
import sys
from pathlib import Path

from aletheore.docs_reference import build_module_reference
from aletheore.scanner.graph import _compact_symbol, build_module_graph
from aletheore.search_index import build_chunks
from aletheore.signature_diff import find_changed_signatures

EMPTY_DEFAULTS = {"params": None, "docstring": None, "return_type": None, "is_pure_declaration": False}


def _expand(evidence: dict) -> dict:
    """The pre-0.8.1 shape: every symbol carries every field explicitly."""
    old = copy.deepcopy(evidence)
    for module in old["repository"]["modules"]:
        for kind in ("functions", "classes", "constants"):
            for symbol in module["symbols"].get(kind, []):
                for key, default in EMPTY_DEFAULTS.items():
                    symbol.setdefault(key, default)
    return old


def _evidence(repo: Path) -> dict:
    modules, graph, bad = build_module_graph(repo)
    return {"repository": {"modules": modules, "dependency_graph": graph}}


def _repo(tmp_path: Path) -> Path:
    files = {
        "pkg/api.py": 'LIMIT = 10\n\ndef get(user_id: int) -> dict:\n    """Fetch a user."""\n    return {}\n\nclass Store:\n    pass\n',
        "pkg/plain.py": "def helper(a, b=1):\n    return a\n",
        "web/app.ts": "export const ROUTES = 3;\nexport function load(id: string): number { return 1; }\ninterface Shape { x: number }\n",
        "kernel/defs.h": "#define MAX_LEN 64\n#define FLAG 0x1\nint run(int argc);\n",
        "src/Svc.java": "public interface Svc { void go(); }\nclass Impl implements Svc { public void go() {} }\n",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_compact_symbol_only_drops_empty_values():
    entry = {"name": "x", "start_line": 1, "end_line": 1, "params": None, "docstring": "d",
             "return_type": None, "is_public": False, "is_pure_declaration": False}
    assert _compact_symbol(entry) == {"name": "x", "start_line": 1, "end_line": 1, "docstring": "d", "is_public": False}
    kept = {**entry, "params": "()", "return_type": "int", "is_pure_declaration": True}
    assert _compact_symbol(kept) == {**kept}


def test_scanned_symbols_carry_no_empty_fields(tmp_path):
    evidence = _evidence(_repo(tmp_path))
    symbols = [s for m in evidence["repository"]["modules"] for kind in ("functions", "classes", "constants")
               for s in m["symbols"].get(kind, [])]
    assert symbols
    for symbol in symbols:
        for key, default in EMPTY_DEFAULTS.items():
            assert key not in symbol or symbol[key] != default, (symbol, key)
    assert any(s.get("is_pure_declaration") for s in symbols)  # still present when True


def test_docs_reference_reads_both_shapes_the_same(tmp_path):
    evidence = _evidence(_repo(tmp_path))
    old = _expand(evidence)
    for module in evidence["repository"]["modules"]:
        assert build_module_reference(evidence, module["path"]) == build_module_reference(old, module["path"])


def test_search_chunks_read_both_shapes_the_same(tmp_path):
    repo = _repo(tmp_path)
    evidence = _evidence(repo)
    assert build_chunks(evidence, repo) == build_chunks(_expand(evidence), repo)


def test_signature_diff_reads_both_shapes_the_same(tmp_path):
    evidence = _evidence(_repo(tmp_path))
    changed = copy.deepcopy(evidence)
    for module in changed["repository"]["modules"]:
        for fn in module["symbols"].get("functions", []):
            fn["params"] = "(a, b, c)"
    assert find_changed_signatures(evidence, changed) == find_changed_signatures(_expand(evidence), changed)


def test_hosted_live_docs_reads_both_shapes_the_same(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "github-app"))
    try:
        from scan_worker.live_docs import _build_request_items, _symbols_needing_work
    except ImportError:  # github-app deps not installed in this environment
        import pytest

        pytest.skip("github-app not importable here")
    repo = _repo(tmp_path)
    evidence = _evidence(repo)
    old = _expand(evidence)
    for new_module, old_module in zip(evidence["repository"]["modules"], old["repository"]["modules"]):
        lines = (repo / new_module["path"]).read_text().splitlines()
        for polish in (True, False):
            new_syms = _symbols_needing_work(new_module, polish)
            old_syms = _symbols_needing_work(old_module, polish)
            assert [s["name"] for s in new_syms] == [s["name"] for s in old_syms]
            assert _build_request_items(new_syms, lines, polish) == _build_request_items(old_syms, lines, polish)
