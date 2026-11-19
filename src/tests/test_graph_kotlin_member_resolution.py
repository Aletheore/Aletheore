"""A Kotlin file depends on the file declaring each member it resolves through declared types, even when the type
that holds the member is never written in the file."""

from pathlib import Path

from aletheore.scanner.graph import build_module_graph

SRC = "src/main/kotlin/"
LOC = SRC + "Localization.kt"
CTX = SRC + "Context.kt"
BASE = SRC + "Base.kt"
USER = SRC + "User.kt"


def _scan(tmp_path: Path, files: dict[str, str]) -> dict[str, dict]:
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return {m["path"]: m for m in modules}


def _imports(mods: dict, path: str) -> set[str]:
    return set(mods[path]["imports"])


LOCALIZATION = "package a\nclass Localization {\n    fun usageTitle(): String = \"\"\n}\n"
CONTEXT = "package a\nclass Context(val localization: Localization)\n"


def test_a_chain_through_typed_receivers_links_each_declaring_file(tmp_path):
    mods = _scan(tmp_path, {
        LOC: LOCALIZATION, CTX: CONTEXT,
        USER: "package a\nfun title(context: Context): String = context.localization.usageTitle()\n",
    })
    got = _imports(mods, USER)
    assert CTX in got  # the parameter's type is written
    assert LOC in got  # usageTitle is declared there, found through Context.localization


def test_an_inherited_property_used_by_its_plain_name_links_the_member_type_file(tmp_path):
    mods = _scan(tmp_path, {
        LOC: LOCALIZATION,
        BASE: "package a\nabstract class Base {\n    val localization: Localization = Localization()\n}\n",
        USER: "package a\nclass Fmt : Base() {\n    fun title(): String = localization.usageTitle()\n}\n",
    })
    got = _imports(mods, USER)
    assert BASE in got
    assert LOC in got
    assert mods[USER]["import_confidence"].get(LOC) == "inferred"


def test_a_member_declared_in_a_grandparent_file_is_linked(tmp_path):
    mods = _scan(tmp_path, {
        BASE: "package a\nabstract class Base {\n    fun log(msg: String) {}\n}\n",
        SRC + "Mid.kt": "package a\nopen class Mid : Base()\n",
        USER: "package a\nclass Leaf : Mid() {\n    fun go() { log(\"x\") }\n}\n",
    })
    assert BASE in _imports(mods, USER)


def test_a_local_of_the_same_name_hides_the_member(tmp_path):
    mods = _scan(tmp_path, {
        BASE: "package a\nabstract class Base {\n    val localization: Int = 0\n}\n",
        USER: "package a\nclass Fmt : Base() {\n    fun go(): Int { val localization = 1; return localization }\n}\n",
    })
    got = _imports(mods, USER)
    assert BASE in got  # named as the supertype
    assert LOC not in got


def test_code_inside_a_lambda_is_not_resolved_against_the_enclosing_class(tmp_path):
    mods = _scan(tmp_path, {
        LOC: LOCALIZATION,
        BASE: "package a\nabstract class Base {\n    val localization: Localization = Localization()\n}\n",
        USER: "package a\nclass Fmt : Base() {\n    fun go(x: String) = x.let { localization.usageTitle() }\n}\n",
    })
    assert LOC not in _imports(mods, USER)  # the implicit receiver of a scope function can differ


def test_a_class_name_declared_in_two_files_resolves_to_nothing(tmp_path):
    mods = _scan(tmp_path, {
        LOC: LOCALIZATION, CTX: CONTEXT,
        SRC + "Other.kt": "package b\nclass Context(val unrelated: Int)\n",
        USER: "package a\nfun title(context: Context): String = context.localization.usageTitle()\n",
    })
    assert LOC not in _imports(mods, USER)


def test_a_named_argument_is_not_a_member_use(tmp_path):
    mods = _scan(tmp_path, {
        BASE: "package a\nabstract class Base {\n    val label: String = \"\"\n}\n",
        USER: "package a\nfun make(label: String) = 1\nclass Fmt : Base() {\n    fun go() = make(label = \"x\")\n}\n",
    })
    assert BASE in _imports(mods, USER)  # via the supertype
