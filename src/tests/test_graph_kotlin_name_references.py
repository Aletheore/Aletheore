"""Kotlin files depend on each other by naming types: same package needs no import, a wildcard import links only
the files whose types are used, and a class need not live in a file of the same name."""

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


SRC = "src/main/kotlin/"


def test_same_package_type_use_needs_no_import(tmp_path):
    got = _imports(tmp_path, {
        SRC + "a/Things.kt": "package a\nclass Thing\nobject Holder\n",
        SRC + "a/User.kt": "package a\nfun f(t: Thing) = Holder\n",
    }, SRC + "a/User.kt")
    assert got == {SRC + "a/Things.kt"}


def test_explicit_import_of_a_class_declared_in_a_differently_named_file(tmp_path):
    got = _imports(tmp_path, {
        SRC + "core/Misc.kt": "package core\nclass Thing\n",
        SRC + "app/User.kt": "package app\nimport core.Thing\nfun f(t: Thing) = t\n",
    }, SRC + "app/User.kt")
    assert SRC + "core/Misc.kt" in got


def test_wildcard_import_links_only_the_files_whose_names_are_used(tmp_path):
    got = _imports(tmp_path, {
        SRC + "core/A.kt": "package core\nclass Used\n",
        SRC + "core/B.kt": "package core\nclass Unused\n",
        SRC + "app/User.kt": "package app\nimport core.*\nfun f(u: Used) = u\n",
    }, SRC + "app/User.kt")
    assert got == {SRC + "core/A.kt"}


def test_a_name_in_another_package_without_an_import_is_not_linked(tmp_path):
    got = _imports(tmp_path, {
        SRC + "core/A.kt": "package core\nclass Thing\n",
        SRC + "app/User.kt": "package app\nfun f(t: Thing) = t\n",
    }, SRC + "app/User.kt")
    assert got == set()


def test_aliased_import(tmp_path):
    got = _imports(tmp_path, {
        SRC + "core/A.kt": "package core\nclass Thing\n",
        SRC + "app/User.kt": "package app\nimport core.Thing as T\nfun f(t: T) = t\n",
    }, SRC + "app/User.kt")
    assert SRC + "core/A.kt" in got


def test_a_type_declared_in_two_files_of_a_package_is_not_guessed(tmp_path):
    got = _imports(tmp_path, {
        SRC + "a/One.kt": "package a\nclass Dup\n",
        SRC + "a/Two.kt": "package a\nclass Dup\n",
        SRC + "a/User.kt": "package a\nfun f(d: Dup) = d\n",
    }, SRC + "a/User.kt")
    assert got == set()


def test_comments_strings_and_own_declarations_are_not_references(tmp_path):
    got = _imports(tmp_path, {
        SRC + "a/A.kt": "package a\nclass Thing\n",
        SRC + "a/User.kt": 'package a\n// Thing\nval s = "Thing"\nclass Thing2\n',
    }, SRC + "a/User.kt")
    assert got == set()


def test_a_nested_class_import_links_the_file_of_its_outer_class(tmp_path):
    got = _imports(tmp_path, {
        SRC + "core/Outer.kt": "package core\nclass Outer { class Inner }\n",
        SRC + "app/User.kt": "package app\nimport core.Outer.Inner\nfun f(i: Inner) = i\n",
    }, SRC + "app/User.kt")
    assert SRC + "core/Outer.kt" in got


def test_interfaces_enums_typealiases_and_objects_are_indexed(tmp_path):
    got = _imports(tmp_path, {
        SRC + "a/Decls.kt": "package a\ninterface I\nenum class E { X }\ntypealias TA = Int\nobject O\n",
        SRC + "a/User.kt": "package a\nclass C : I\nfun f(e: E, t: TA) = O\n",
    }, SRC + "a/User.kt")
    assert got == {SRC + "a/Decls.kt"}


def test_a_fully_qualified_name_needs_no_import(tmp_path):
    got = _imports(tmp_path, {
        SRC + "core/A.kt": "package core.deep\nclass Thing\n",
        SRC + "app/User.kt": "package app\nfun f(t: core.deep.Thing) = t\nval x = core.deep.Thing()\n",
    }, SRC + "app/User.kt")
    assert SRC + "core/A.kt" in got


def test_a_qualified_name_in_a_comment_is_not_a_reference(tmp_path):
    got = _imports(tmp_path, {
        SRC + "core/A.kt": "package core.deep\nclass Thing\n",
        SRC + "app/User.kt": "package app\n/** See [core.deep.Thing] */\n// core.deep.Thing\nfun f() = 1\n",
    }, SRC + "app/User.kt")
    assert got == set()
