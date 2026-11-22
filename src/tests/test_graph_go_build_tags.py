"""Go build constraints: a file is built always, never, or only on some platforms (default tags, any OS)."""

import pytest

from aletheore.scanner import go_resolve
from aletheore.scanner.graph import build_module_graph

GO_MOD = "module example.com/m\n\ngo 1.21\n"


def go_repo(tmp_path, files):
    root = tmp_path / "r"
    for rel, text in {"go.mod": GO_MOD, **files}.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


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


@pytest.mark.parametrize(
    "source, target, exclusive",
    [
        ("x_windows.go", "y_linux.go", True),
        ("x_windows_amd64_test.go", "y_linux_arm64.go", True),
        ("x_linux.go", "y_android.go", False),
        ("x_darwin.go", "y_ios.go", False),
        ("x_solaris.go", "y_illumos.go", False),
        ("x.go", "y_windows.go", False),
        ("x_linux.go", "y_linux_amd64.go", False),
        ("x_amd64.go", "y_windows.go", False),
        ("x_linux_amd64.go", "y_linux_arm64.go", True),
        ("x_amd64.go", "y_arm64.go", True),
        ("x_linux_amd64.go", "y_android_arm64.go", True),
        ("x_linux_amd64.go", "y_android_amd64.go", False),
    ],
)
def test_mutually_exclusive_platform_names(source, target, exclusive):
    assert go_resolve.mutually_exclusive_platforms(source, target) is exclusive
    assert go_resolve.mutually_exclusive_platforms(target, source) is exclusive


def test_go_import_does_not_link_files_for_incompatible_platforms(tmp_path):
    repo = go_repo(tmp_path, {
        "main_windows.go": 'package m\nimport "example.com/m/p"\nfunc F() { p.F() }\n',
        "main.go": 'package m\nimport "example.com/m/p"\nfunc G() { p.F() }\n',
        "p/impl_windows.go": "package p\nfunc F() {}\n",
        "p/impl_linux.go": "package p\nfunc F() {}\n",
        "p/common.go": "package p\nvar X = 1\n",
    })
    modules, edges = _scan(repo)
    assert ("main_windows.go", "p/impl_linux.go") not in edges
    assert ("main_windows.go", "p/impl_windows.go") in edges
    assert ("main_windows.go", "p/common.go") in edges
    assert ("main.go", "p/impl_linux.go") in edges
    assert _conf(modules, "main_windows.go", "p/impl_windows.go") == "inferred"


def test_go_build_lines_and_arch_suffixes_also_separate_platform_files(tmp_path):
    repo = go_repo(tmp_path, {
        "main_linux_amd64.go": 'package m\nimport "example.com/m/p"\nfunc F() { p.F() }\n',
        "main_unix.go": '//go:build windows\n\npackage m\nimport "example.com/m/p"\nfunc G() { p.F() }\n',
        "p/impl_linux_amd64.go": "package p\nfunc F() {}\n",
        "p/impl_linux_arm64.go": "package p\nfunc F() {}\n",
        "p/impl_other.go": "//go:build !windows && cgo\n\npackage p\nfunc F() {}\n",
        "p/impl_win.go": "//go:build windows\n\npackage p\nfunc F() {}\n",
    })
    _modules, edges = _scan(repo)
    assert ("main_linux_amd64.go", "p/impl_linux_arm64.go") not in edges
    assert ("main_linux_amd64.go", "p/impl_linux_amd64.go") in edges
    assert ("main_linux_amd64.go", "p/impl_win.go") not in edges
    assert ("main_linux_amd64.go", "p/impl_other.go") in edges   # cgo may be on, so it can share a build with this file
    assert ("main_unix.go", "p/impl_win.go") in edges
    assert ("main_unix.go", "p/impl_linux_amd64.go") not in edges


def test_plain_files_stay_exact(tmp_path):
    repo = go_repo(tmp_path, {
        "a.go": "package m\n\nfunc A() { helper() }\n",
        "helper.go": "package m\n\nfunc helper() {}\n",
    })
    modules, _ = _scan(repo)
    assert _conf(modules, "a.go", "helper.go") == "exact"


def test_a_go_version_tag_newer_than_the_known_toolchain_is_unknown_not_false():
    assert go_resolve.build_state("a.go", "//go:build go1.999\n\npackage x\n") == "maybe"
    assert go_resolve.build_state("a.go", "//go:build go1.1\n\npackage x\n") == "always"


def test_a_block_comment_mentioning_package_before_the_constraint_does_not_hide_it():
    head = "/*\nThis package is licensed under X.\n*/\n//go:build ignore\n\npackage x\n"
    assert go_resolve.build_state("a.go", head) == "never"
