"""A type nested in another type or in an extension of it is named as `Outer.Name`, or as `Name` inside Outer."""

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


LOGGER = "public struct Logger {\n    public var label = \"\"\n    public init() {}\n}\n"
NESTED_IN_EXTENSION = "extension Logger {\n    public struct Provider {\n        public init() {}\n    }\n}\n"


def test_a_qualified_use_names_the_file_that_declares_the_nested_type(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/Logger.swift": LOGGER,
        "Sources/App/Provider.swift": NESTED_IN_EXTENSION,
        "Sources/App/Use.swift": "func make() -> Logger.Provider { Logger.Provider() }\n",
    })
    got = _imports(mods, "Sources/App/Use.swift")
    assert "Sources/App/Provider.swift" in got
    assert "Sources/App/Logger.swift" in got


def test_a_nested_type_used_by_its_plain_name_inside_the_outer_type(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/Provider.swift": NESTED_IN_EXTENSION,
        "Sources/App/Logger.swift": "public struct Logger {\n    var provider: Provider? = nil\n    public init() {}\n}\n",
    })
    assert "Sources/App/Provider.swift" in _imports(mods, "Sources/App/Logger.swift")


def test_a_plain_use_outside_the_outer_type_does_not_name_the_nested_type(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/Provider.swift": NESTED_IN_EXTENSION,
        "Sources/App/Other.swift": "struct Other { var provider: Provider? = nil }\n",
    })
    assert "Sources/App/Provider.swift" not in _imports(mods, "Sources/App/Other.swift")


def test_a_nested_name_declared_under_two_outer_types_resolves_by_the_one_written(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/A.swift": "struct A { struct Options {} }\n",
        "Sources/App/B.swift": "struct B { struct Options {} }\n",
        "Sources/App/Use.swift": "func f(_ o: A.Options) {}\n",
    })
    got = _imports(mods, "Sources/App/Use.swift")
    assert "Sources/App/A.swift" in got
    assert "Sources/App/B.swift" not in got


def test_an_extension_of_a_nested_type_names_the_file_that_declares_it(tmp_path):
    mods = _scan(tmp_path, {
        "Sources/App/Logger.swift": LOGGER,
        "Sources/App/Provider.swift": NESTED_IN_EXTENSION,
        "Sources/App/Ext.swift": "extension Logger.Provider {\n    func describe() -> String { \"\" }\n}\n",
    })
    assert "Sources/App/Provider.swift" in _imports(mods, "Sources/App/Ext.swift")
