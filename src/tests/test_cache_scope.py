import pytest

from aletheore.scanner.cache_scope import (
    ALL_LANGUAGES,
    LANGUAGE_BY_SUFFIX,
    languages_to_reextract,
    parse_name_status,
)


def test_the_extension_table_matches_the_scanner_exactly():
    from aletheore.scanner.graph import LANGUAGE_BY_EXTENSION

    assert LANGUAGE_BY_SUFFIX == {suffix: name for suffix, (name, _grammar) in LANGUAGE_BY_EXTENSION.items()}


def test_parse_name_status_reads_every_status_and_splits_renames():
    text = "M\tsrc/a.py\nA\tsrc/b.py\nD\tsrc/c.py\nR087\told/d.cs\tnew/d.cs\nT\tsrc/e.py\n\nbroken line\n"
    assert parse_name_status(text) == [
        ("M", "src/a.py"), ("A", "src/b.py"), ("D", "src/c.py"),
        ("D", "old/d.cs"), ("A", "new/d.cs"), ("T", "src/e.py"),
    ]


@pytest.mark.parametrize(
    "changes,expected",
    [
        # editing a Python or JS/TS file's body cannot change what other files resolve to
        ([("M", "pkg/a.py")], set()),
        ([("M", "web/app.ts")], set()),
        # ...but adding, deleting or retyping one can (imports depend on which files exist)
        ([("A", "pkg/new.py")], {"python"}),
        ([("D", "pkg/old.py")], {"python"}),
        ([("T", "pkg/x.py")], {"python"}),
        ([("A", "web/new.js")], {"javascript", "typescript"}),
        # languages that resolve against declarations are stale on any change to a file of that language
        ([("M", "src/Foo.cs")], {"csharp"}),
        ([("M", "src/Foo.java")], {"java", "kotlin"}),
        ([("M", "lib/a.php")], {"php"}),
        ([("M", "lib/a.rb")], {"ruby"}),
        ([("M", "src/a.h")], {"c", "cpp"}),
        ([("M", "src/lib.rs")], {"rust"}),
        ([("M", "pkg/a.go")], {"go"}),
        # build and package files
        ([("M", "go.mod")], {"go"}),
        ([("M", "Cargo.toml")], {"rust"}),
        ([("M", "web/package.json")], {"javascript", "typescript"}),
        ([("M", "web/tsconfig.base.json")], {"javascript", "typescript"}),
        ([("M", "src/App/App.csproj")], {"csharp"}),
        ([("M", "composer.json")], {"php"}),
        ([("M", "pyproject.toml")], {"python"}),
        # files that are not source or build files change nothing
        ([("M", "README.md"), ("M", "docs/x.txt"), ("A", "data/y.csv")], set()),
        # two languages at once
        ([("M", "a/Foo.cs"), ("A", "b/new.py")], {"csharp", "python"}),
    ],
)
def test_languages_to_reextract(changes, expected):
    assert languages_to_reextract(changes) == frozenset(expected)


@pytest.mark.parametrize("name", [".gitignore", ".aletheore.json", ".gitmodules"])
def test_a_file_that_decides_what_the_scan_sees_invalidates_everything(name):
    assert languages_to_reextract([("M", name)]) == ALL_LANGUAGES
    assert languages_to_reextract([("M", f"sub/dir/{name}")]) == ALL_LANGUAGES
