"""Regressions found by fuzzing the graph builder (see test_fuzz_graph_builder.py)."""

from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def _modules(tmp_path: Path, files: dict[str, str]) -> dict[str, dict]:
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return {m["path"]: m for m in modules}


def test_a_javascript_import_with_a_nul_byte_does_not_abort_the_scan(tmp_path):
    mods = _modules(tmp_path, {
        "src/a.js": "const x = require('.\x00/b');\nconst y = require('./b');\n",
        "src/b.js": "exports.v = 1;\n",
    })
    assert mods["src/a.js"]["imports"] == ["src/b.js"]  # the real import still resolves
    assert "src/b.js" in mods


def test_a_typescript_import_with_a_nul_byte_does_not_abort_the_scan(tmp_path):
    mods = _modules(tmp_path, {"src/a.ts": "import { x } from '.\x00/b';\n", "src/b.ts": "export const x = 1;\n"})
    assert mods["src/a.ts"]["imports"] == []


def test_a_java_import_names_the_class_in_its_exact_case(tmp_path):
    # On a case-insensitive filesystem `app/a.java` "exists" because `app/A.java` does; the import must not link it
    mods = _modules(tmp_path, {
        "src/main/java/app/A.java": "package app;\n\nimport app.b;\n\npublic class A { }\n",
        "src/main/java/app/B.java": "package app;\n\npublic class B { }\n",
    })
    assert mods["src/main/java/app/A.java"]["imports"] == []


def test_a_java_import_with_the_right_case_still_links(tmp_path):
    mods = _modules(tmp_path, {
        "src/main/java/app/A.java": "package app;\n\nimport app.util.B;\n\npublic class A { }\n",
        "src/main/java/app/util/B.java": "package app.util;\n\npublic class B { }\n",
    })
    assert mods["src/main/java/app/A.java"]["imports"] == ["src/main/java/app/util/B.java"]


def test_a_java_package_import_names_the_directory_in_its_exact_case(tmp_path):
    mods = _modules(tmp_path, {
        "src/main/java/app/A.java": "package app;\n\nimport app.UTIL.*;\n\npublic class A { }\n",
        "src/main/java/app/util/B.java": "package app.util;\n\npublic class B { }\n",
    })
    assert mods["src/main/java/app/A.java"]["imports"] == []
