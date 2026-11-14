"""Swift files of one module depend on each other by naming types, with no `import` (same module)."""

from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def _scan(tmp_path: Path, files: dict[str, str]):
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return {m["path"]: m for m in modules}


def _imports(mods, path):
    return set(mods[path]["imports"])


THING = "public struct Thing { public init() {} }\n"


def test_use_of_a_type_declared_in_another_file_of_the_same_target(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/Thing.swift": THING,
        "Sources/App/User.swift": "func run() { _ = Thing() }\n",
    })
    assert _imports(mods, "Sources/App/User.swift") == {"Sources/App/Thing.swift"}
    assert _imports(mods, "Sources/App/Thing.swift") == set()


def test_type_annotation_conformance_extension_and_typealias_are_references(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/Proto.swift": "protocol Proto {}\n",
        "Sources/App/Thing.swift": THING,
        "Sources/App/A.swift": "struct A: Proto {}\n",
        "Sources/App/B.swift": "extension Thing: Equatable {}\n",
        "Sources/App/C.swift": "typealias Alias = Thing\n",
        "Sources/App/D.swift": "struct D { let t: Thing; var xs: [Thing] = [] }\n",
    })
    assert _imports(mods, "Sources/App/A.swift") == {"Sources/App/Proto.swift"}
    assert _imports(mods, "Sources/App/B.swift") == {"Sources/App/Thing.swift"}
    assert _imports(mods, "Sources/App/C.swift") == {"Sources/App/Thing.swift"}
    assert _imports(mods, "Sources/App/D.swift") == {"Sources/App/Thing.swift"}


def test_a_name_declared_in_two_files_of_the_target_is_not_guessed(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/Thing1.swift": THING,
        "Sources/App/Thing2.swift": "extension Other {}\nstruct Thing {}\n",
        "Sources/App/User.swift": "func run() { _ = Thing() }\n",
    })
    assert _imports(mods, "Sources/App/User.swift") == set()


def test_comments_strings_and_lowercase_names_are_not_references(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/Thing.swift": THING,
        "Sources/App/User.swift": '// Thing\nfunc run() { let thing = "Thing"; _ = thing }\n',
    })
    assert _imports(mods, "Sources/App/User.swift") == set()


def test_a_type_the_file_declares_itself_is_not_an_edge_to_another_file(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/Core/Thing.swift": THING,
        "Sources/App/Own.swift": "struct Thing {}\nfunc f() { _ = Thing() }\n",
    })
    assert _imports(mods, "Sources/App/Own.swift") == set()


def test_unknown_and_standard_library_types_add_nothing(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/Thing.swift": THING,
        "Sources/App/User.swift": "func f(_ s: String, _ a: [Int]) -> Optional<Date> { nil }\n",
    })
    assert _imports(mods, "Sources/App/User.swift") == set()


def test_another_target_is_reached_only_through_an_import(tmp_path):
    files = {
        "Sources/Core/Thing.swift": THING,
        "Sources/Core/Other.swift": "struct Other {}\n",
        "Sources/App/Importing.swift": "import Core\nfunc f() { _ = Thing() }\n",
        "Sources/App/Plain.swift": "func g() { _ = Thing() }\n",
    }
    mods = _scan(tmp_path, files)
    assert "Sources/Core/Thing.swift" in _imports(mods, "Sources/App/Importing.swift")
    assert _imports(mods, "Sources/App/Plain.swift") == set()


def test_same_target_declaration_wins_over_an_imported_target_with_the_same_name(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/Core/Thing.swift": THING,
        "Sources/App/Thing.swift": "struct Thing {}\n",
        "Sources/App/User.swift": "import Core\nfunc f() { _ = Thing() }\n",
    })
    got = _imports(mods, "Sources/App/User.swift")
    assert "Sources/App/Thing.swift" in got


def test_editing_a_swift_file_changes_the_resolution_signature_and_drops_a_stale_edge(tmp_path):
    repo = tmp_path / "r"
    files = {"Sources/App/Thing.swift": THING, "Sources/App/User.swift": "func run() { _ = Thing() }\n"}
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    first: dict[str, str] = {}
    modules, _, _ = build_module_graph(repo, signatures_out=first)
    assert "Sources/App/Thing.swift" in next(m for m in modules if m["path"].endswith("User.swift"))["imports"]
    cached = {m["path"]: m for m in modules}
    # Thing is renamed: User.swift is unchanged, so only the signature can tell its cached edge is now wrong
    (repo / "Sources/App/Thing.swift").write_text("public struct Renamed { public init() {} }\n")
    second: dict[str, str] = {}
    modules, _, _ = build_module_graph(
        repo, unchanged_modules={"Sources/App/User.swift": cached["Sources/App/User.swift"]},
        prior_signatures=first, signatures_out=second,
    )
    assert first["swift"] != second["swift"]
    assert next(m for m in modules if m["path"].endswith("User.swift"))["imports"] == []
