"""Go build constraints: a file is built always, never, or only on some platforms (default tags, any OS)."""

import pytest

from aletheore.scanner import go_resolve
from aletheore.scanner.graph import build_module_graph

from test_graph_go_rust_round2 import go_repo


@pytest.mark.parametrize(
    "head, name, expected",
    [
        ("package m\n", "a.go", "always"),
        ("//go:build !nomsgpack\n\npackage m\n", "a.go", "always"),
        ("//go:build nomsgpack\n\npackage m\n", "a.go", "never"),
        ("//go:build ignore\n\npackage main\n", "gen.go", "never"),
        ("//go:build appengine\n\npackage m\n", "a.go", "never"),
        ("//go:build go1.18\n\npackage m\n", "a.go", "always"),
        ("//go:build linux\n\npackage m\n", "a.go", "maybe"),
        ("//go:build !windows\n\npackage m\n", "a.go", "maybe"),
        ("//go:build cgo\n\npackage m\n", "a.go", "maybe"),
        ("//go:build unix\n\npackage m\n", "a.go", "maybe"),
        ("package m\n", "a_linux.go", "maybe"),
        ("package m\n", "a_windows_amd64.go", "maybe"),
        ("package m\n", "a_linux_test.go", "maybe"),
        # custom tags are false by default; an OS tag alone decides nothing
        ("//go:build jsoniter && linux\n\npackage m\n", "a.go", "never"),
        ("//go:build jsoniter || linux\n\npackage m\n", "a.go", "maybe"),
        ("//go:build !jsoniter && !go_json && !(sonic && avx && (linux || windows) && amd64)\n\npackage m\n", "a.go", "always"),
        ("//go:build sonic && avx && (linux || windows) && amd64\n\npackage m\n", "a.go", "never"),
        # legacy form: lines are ANDed, space-separated terms ORed, commas ANDed
        ("// +build !nomsgpack\n\npackage m\n", "a.go", "always"),
        ("// +build nomsgpack\n\npackage m\n", "a.go", "never"),
        ("// +build linux darwin\n\npackage m\n", "a.go", "maybe"),
        ("// +build linux,amd64\n\npackage m\n", "a.go", "maybe"),
        # //go:build wins over // +build when both are present
        ("//go:build !nomsgpack\n// +build nomsgpack\n\npackage m\n", "a.go", "always"),
        # a malformed expression is not guessed at: it stays unknown
        ("//go:build (linux &&\n\npackage m\n", "a.go", "maybe"),
        # a constraint after the package clause is an ordinary comment
        ("package m\n\n//go:build nomsgpack\nvar x = 1\n", "a.go", "always"),
    ],
)
def test_build_state(head, name, expected):
    assert go_resolve.build_state(name, head) == expected


def _scan(repo):
    modules, graph, _ = build_module_graph(repo)
    return {m["path"]: m for m in modules}, {tuple(e) for e in graph["edges"]}


def _conf(modules, src, dst):
    return (modules[src].get("import_confidence") or {}).get(dst, "exact")


def test_default_built_conditional_file_keeps_exact_edges(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { v() }\n",
        "v.go": "//go:build !nomsgpack\n\npackage m\n\nfunc v() {}\n",
    })
    modules, edges = _scan(repo)
    assert ("a.go", "v.go") in edges
    assert _conf(modules, "a.go", "v.go") == "exact"


def test_edges_from_and_to_a_never_built_file_are_inferred(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { Alt() }\n",
        "alt.go": "//go:build nomsgpack\n\npackage m\n\nfunc Alt() { helper() }\n",
        "helper.go": "package m\n\nfunc helper() {}\n",
    })
    modules, edges = _scan(repo)
    assert ("a.go", "alt.go") in edges and ("alt.go", "helper.go") in edges
    assert _conf(modules, "a.go", "alt.go") == "inferred"      # into the alternative
    assert _conf(modules, "alt.go", "helper.go") == "inferred"  # out of it: exists only under the tag


def test_edges_out_of_a_platform_file_are_inferred(tmp_path):
    repo = go_repo(tmp_path, {
        "x_linux.go": "package m\n\nfunc X() { helper() }\n",
        "helper.go": "package m\n\nfunc helper() {}\n",
    })
    modules, _ = _scan(repo)
    assert _conf(modules, "x_linux.go", "helper.go") == "inferred"


def test_plain_files_stay_exact(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { helper() }\n",
        "helper.go": "package m\n\nfunc helper() {}\n",
    })
    modules, _ = _scan(repo)
    assert _conf(modules, "a.go", "helper.go") == "exact"
