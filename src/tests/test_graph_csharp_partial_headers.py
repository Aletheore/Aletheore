"""Partial types whose header holds a preprocessor directive, and nested partial types split over files."""

from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def _imports(tmp_path: Path, files: dict[str, str]) -> dict[str, set[str]]:
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return {m["path"]: set(m["imports"]) for m in modules}


def test_directive_between_a_class_name_and_its_base_list_keeps_the_type_name(tmp_path):
    got = _imports(tmp_path, {
        "Reader.cs": "namespace App;\npublic abstract partial class Reader { public int Value; }\n",
        "Reader.Async.cs": (
            "#if HAVE_ASYNC\nnamespace App\n{\n    public abstract partial class Reader\n#if HAVE_ASYNC_DISPOSABLE\n"
            "        : System.IAsyncDisposable\n#endif\n    {\n        public int Other;\n    }\n}\n#endif\n"
        ),
        "User.cs": "namespace App;\npublic class User { void Run(Reader r) { } }\n",
    })
    assert {"Reader.cs", "Reader.Async.cs"} <= got["User.cs"]


def test_nested_partial_type_declared_in_two_files_links_its_parts(tmp_path):
    got = _imports(tmp_path, {
        "Mapper.cs": (
            "namespace App;\npublic static partial class Mapper\n{\n"
            "    private sealed partial class Row { public int A; }\n"
            "    private sealed class Meta { object Make() { return new Row(); } }\n}\n"
        ),
        "Mapper.Row.cs": "namespace App;\npublic static partial class Mapper\n{\n    private sealed partial class Row { public int B; }\n}\n",
    })
    assert "Mapper.Row.cs" in got["Mapper.cs"]


def test_nested_partial_parts_of_different_outer_types_are_not_linked(tmp_path):
    got = _imports(tmp_path, {
        "A.cs": "namespace App;\npublic partial class A\n{\n    partial class Row { }\n    object Make() { return new Row(); }\n}\n",
        "B.cs": "namespace App;\npublic partial class B\n{\n    partial class Row { }\n}\n",
    })
    assert "B.cs" not in got["A.cs"]


def test_header_directive_whose_endif_is_past_the_brace_is_left_alone(tmp_path):
    # `#if` in the header, `#endif` after the opening brace: blanking only the `#if` would unbalance the file
    from aletheore.scanner.graph import _csharp_blank_header_directives

    source = b"namespace A { public partial class R\n#if X\n  : IFoo\n{\n  int a;\n#endif\n}\n}\n"
    assert _csharp_blank_header_directives(source) == source
