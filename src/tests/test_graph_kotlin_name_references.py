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


def _modules(tmp_path: Path, files: dict[str, str]):
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return {m["path"]: m for m in modules}


MP = "lib/src/"


def test_a_reference_to_an_expect_type_links_the_actual_files_and_expect_links_its_actuals(tmp_path):
    mods = _modules(tmp_path, {
        MP + "commonMain/kotlin/p/Buffer.kt": "package p\nexpect class Buffer\n",
        MP + "jvmMain/kotlin/p/Buffer.kt": "package p\nactual class Buffer\n",
        MP + "commonMain/kotlin/p/Use.kt": "package p\nfun f(b: Buffer) = b\n",
    })
    assert set(mods[MP + "commonMain/kotlin/p/Use.kt"]["imports"]) == {MP + "jvmMain/kotlin/p/Buffer.kt"}
    assert set(mods[MP + "commonMain/kotlin/p/Buffer.kt"]["imports"]) == {MP + "jvmMain/kotlin/p/Buffer.kt"}
    assert MP + "commonMain/kotlin/p/Buffer.kt" not in mods[MP + "jvmMain/kotlin/p/Buffer.kt"]["imports"]


def test_a_reference_to_an_expect_type_with_no_actual_stays_on_the_expect_file(tmp_path):
    mods = _modules(tmp_path, {
        MP + "commonMain/kotlin/p/Buffer.kt": "package p\nexpect class Buffer\n",
        MP + "commonMain/kotlin/p/Use.kt": "package p\nfun f(b: Buffer) = b\n",
    })
    assert set(mods[MP + "commonMain/kotlin/p/Use.kt"]["imports"]) == {MP + "commonMain/kotlin/p/Buffer.kt"}


def test_an_imported_top_level_function_is_an_exact_edge(tmp_path):
    mods = _modules(tmp_path, {
        MP + "main/kotlin/a/Util.kt": "package a\nfun checkOffset(x: Int) {}\n",
        MP + "main/kotlin/b/User.kt": "package b\nimport a.checkOffset\nfun g() { checkOffset(1) }\n",
    })
    user = mods[MP + "main/kotlin/b/User.kt"]
    assert MP + "main/kotlin/a/Util.kt" in user["imports"]
    assert user.get("import_confidence", {}).get(MP + "main/kotlin/a/Util.kt") is None


def test_an_unimported_same_package_top_level_function_is_marked_inferred(tmp_path):
    mods = _modules(tmp_path, {
        MP + "main/kotlin/a/Util.kt": "package a\nfun checkOffset(x: Int) {}\n",
        MP + "main/kotlin/a/User.kt": "package a\nfun g() { checkOffset(1) }\n",
    })
    user = mods[MP + "main/kotlin/a/User.kt"]
    assert MP + "main/kotlin/a/Util.kt" in user["imports"]
    assert user["import_confidence"][MP + "main/kotlin/a/Util.kt"] == "inferred"


def test_a_local_with_the_name_of_a_top_level_function_is_not_an_edge(tmp_path):
    mods = _modules(tmp_path, {
        MP + "main/kotlin/a/Util.kt": "package a\nfun checkOffset(x: Int) {}\n",
        MP + "main/kotlin/a/User.kt": "package a\nfun g(checkOffset: Int) = checkOffset\n",
    })
    assert MP + "main/kotlin/a/Util.kt" not in mods[MP + "main/kotlin/a/User.kt"]["imports"]


def test_a_class_declared_below_several_annotation_lines_is_still_a_top_level_type(tmp_path):
    mods = _modules(tmp_path, {
        MP + "main/kotlin/p/Annotations.kt": (
            "package p\n\n@MustBeDocumented\n@Retention(AnnotationRetention.BINARY)\n"
            "@RequiresOptIn(level = RequiresOptIn.Level.ERROR)\npublic annotation class InternalApi\n"),
        MP + "main/kotlin/p/Use.kt": "package p\n\n@OptIn(InternalApi::class)\nfun f() {}\n",
    })
    assert MP + "main/kotlin/p/Annotations.kt" in mods[MP + "main/kotlin/p/Use.kt"]["imports"]


def test_a_type_the_grammar_reads_as_an_expression_is_still_found_by_its_declaration_line(tmp_path):
    # several annotation lines with arguments before `public annotation class` make tree-sitter-kotlin report an
    # infix expression instead of a class declaration
    mods = _modules(tmp_path, {
        MP + "main/kotlin/p/Annotations.kt": (
            "package p\n\n@MustBeDocumented\n@Retention(AnnotationRetention.BINARY)\n"
            "@RequiresOptIn(\n    level = RequiresOptIn.Level.ERROR,\n    message = \"internal\" +\n        \"api\"\n)\n"
            "public annotation class InternalApi\n\n@MustBeDocumented\n@RequiresOptIn(level = RequiresOptIn.Level.WARNING)\n"
            "public annotation class DelicateApi\n"),
        MP + "main/kotlin/p/Use.kt": "package p\n\n@OptIn(InternalApi::class, DelicateApi::class)\nfun f() {}\n",
    })
    assert MP + "main/kotlin/p/Annotations.kt" in mods[MP + "main/kotlin/p/Use.kt"]["imports"]



def test_a_qualified_name_inside_a_string_literal_is_not_a_reference(tmp_path):
    mods = _modules(tmp_path, {
        MP + "main/kotlin/a/Thing.kt": "package a\nclass Thing\n",
        MP + "main/kotlin/b/Use.kt": 'package b\nval message = "see a.Thing for details"\nval raw = """a.Thing"""\n',
    })
    assert MP + "main/kotlin/a/Thing.kt" not in mods[MP + "main/kotlin/b/Use.kt"]["imports"]
