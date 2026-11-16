"""Generic arity and call position decide which same-named type a use names (what the compiler's binder does)."""

from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def _imports(tmp_path: Path, files: dict[str, str], path: str) -> set[str]:
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return set(next(m for m in modules if m["path"] == path)["imports"])


PLAIN = "namespace App;\npublic static class Mock { public static int Get() => 1; }\n"
GENERIC = "namespace App;\npublic class Mock<T> { }\n"
TWO = "namespace App;\npublic class Mock<T, U> { }\n"


def test_a_generic_use_names_the_generic_type_not_the_plain_one(tmp_path):
    files = {"Mock.cs": PLAIN, "MockOfT.cs": GENERIC, "T.cs": "namespace App;\npublic class T { Mock<int> m; }\n"}
    got = _imports(tmp_path, files, "T.cs")
    assert "MockOfT.cs" in got
    assert "Mock.cs" not in got


def test_a_plain_use_names_the_plain_type_not_the_generic_one(tmp_path):
    files = {"Mock.cs": PLAIN, "MockOfT.cs": GENERIC, "T.cs": "namespace App;\npublic class T { int Run() => Mock.Get(); }\n"}
    got = _imports(tmp_path, files, "T.cs")
    assert "Mock.cs" in got
    assert "MockOfT.cs" not in got


def test_generic_arity_is_matched_exactly(tmp_path):
    files = {"One.cs": GENERIC, "Two.cs": TWO, "T.cs": "namespace App;\npublic class T { Mock<int, string> m; }\n"}
    got = _imports(tmp_path, files, "T.cs")
    assert "Two.cs" in got
    assert "One.cs" not in got


def test_an_unbound_generic_typeof_names_the_generic_type(tmp_path):
    files = {"Two.cs": TWO, "T.cs": "namespace App;\npublic class T { object o = typeof(Mock<,>); }\n"}
    assert "Two.cs" in _imports(tmp_path, files, "T.cs")


def test_a_generic_type_that_is_the_only_one_with_that_name_links_through_its_base_list(tmp_path):
    files = {"Mock.cs": GENERIC, "T.cs": "namespace App;\npublic class T : Mock<int> { }\n"}
    assert "Mock.cs" in _imports(tmp_path, files, "T.cs")


def test_the_called_name_of_a_method_call_is_not_a_type(tmp_path):
    files = {
        "Setup.cs": "namespace App;\npublic class Setup { }\n",
        "T.cs": "namespace App;\npublic class T { void Run(object x) { x.Setup(1); } }\n",
    }
    assert "Setup.cs" not in _imports(tmp_path, files, "T.cs")


def test_a_name_that_is_also_used_as_a_type_still_links(tmp_path):
    files = {
        "Setup.cs": "namespace App;\npublic class Setup { }\n",
        "T.cs": "namespace App;\npublic class T { Setup s; void Run(object x) { x.Setup(1); } }\n",
    }
    assert "Setup.cs" in _imports(tmp_path, files, "T.cs")
