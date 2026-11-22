"""Property-based fuzzing of the dependency-graph builder.

Each language gets a small realistic repository (so the resolvers have something to link), and Hypothesis mutates the
files with syntax-significant fragments, deletions and arbitrary text. For every mutation the builder must not raise,
must give the same answer twice, and must only produce edges between files of the repository. A failure here is a
robustness bug: a repository somebody scans can contain any bytes.
"""

import os
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aletheore.scanner.graph import build_module_graph

# language -> {relative path: realistic source}. Two or three files that reference each other.
SEEDS: dict[str, dict[str, str]] = {
    "python": {
        "pkg/__init__.py": "",
        "pkg/a.py": "from pkg import b\nfrom .b import helper\nimport os\n\nclass A(b.Base):\n    def run(self):\n        return helper()\n",
        "pkg/b.py": "class Base:\n    pass\n\ndef helper():\n    return 1\n",
    },
    "typescript": {
        "src/a.ts": "import { helper, Base } from './b';\nexport class A extends Base { run() { return helper(); } }\n",
        "src/b.ts": "export class Base {}\nexport function helper() { return 1; }\n",
        "src/index.ts": "export * from './a';\nexport { helper } from './b';\n",
    },
    "javascript": {
        "src/a.js": "const { helper } = require('./b');\nmodule.exports = () => helper();\n",
        "src/b.js": "exports.helper = function () { return 1; };\n",
    },
    "go": {
        "go.mod": "module example.com/app\n\ngo 1.21\n",
        "main.go": "package main\n\nimport (\n\t\"fmt\"\n\t\"example.com/app/util\"\n)\n\nfunc main() { fmt.Println(util.Helper()) }\n",
        "util/util.go": "package util\n\nfunc Helper() int { return 1 }\n",
    },
    "java": {
        "src/main/java/app/A.java": "package app;\n\nimport app.util.B;\nimport java.util.List;\n\npublic class A extends B { List<String> xs; }\n",
        "src/main/java/app/util/B.java": "package app.util;\n\npublic class B { }\n",
    },
    "csharp": {
        "A.cs": "using App.Util;\nnamespace App { public class A : Base<int> { Helper h; void Run() { Base<int>.Make(); } } }\n",
        "Base.cs": "namespace App.Util { public class Base<T> { public static void Make() {} } public class Helper {} }\n",
    },
    "kotlin": {
        "src/main/kotlin/a/A.kt": "package a\n\nclass A(val b: B) : Base() {\n    fun go() = b.name.length\n}\n",
        "src/main/kotlin/a/B.kt": "package a\n\nclass B(val name: String)\nopen class Base\n",
    },
    "swift": {
        "Package.swift": "// swift-tools-version:5.5\nimport PackageDescription\nlet package = Package(name: \"App\", targets: [.target(name: \"App\")])\n",
        "Sources/App/A.swift": "struct A: Base { var b: Outer.Inner? }\n",
        "Sources/App/B.swift": "class Base {}\nstruct Outer { struct Inner {} }\n",
    },
    "rust": {
        "Cargo.toml": "[package]\nname = \"app\"\nversion = \"0.1.0\"\n",
        "src/lib.rs": "mod a;\nmod b;\npub use b::Thing;\n",
        "src/a.rs": "use crate::b::Thing;\npub fn f() -> Thing { Thing }\n",
        "src/b.rs": "pub struct Thing;\n",
    },
    "c": {
        "main.c": "#include \"util.h\"\n#include <stdio.h>\nint main(void) { return helper(); }\n",
        "util.h": "#ifndef UTIL_H\n#define UTIL_H\nint helper(void);\n#endif\n",
        "util.c": "#include \"util.h\"\nint helper(void) { return 1; }\n",
    },
    "cpp": {
        "main.cpp": "#include \"util.hpp\"\n#include <vector>\nint main() { return util::helper(); }\n",
        "util.hpp": "#pragma once\nnamespace util { int helper(); }\n",
    },
    "php": {
        "composer.json": "{\"autoload\": {\"psr-4\": {\"App\\\\\": \"src/\"}}}\n",
        "src/A.php": "<?php\nnamespace App;\nuse App\\Util\\B;\nclass A extends B {}\n",
        "src/Util/B.php": "<?php\nnamespace App\\Util;\nclass B {}\n",
    },
    "ruby": {
        "lib/a.rb": "require_relative 'b'\nrequire 'json'\nclass A < Base; end\n",
        "lib/b.rb": "class Base; end\n",
    },
}

# fragments that steer a parser into odd states
FRAGMENTS = [
    "{", "}", "(", ")", "[", "]", "<", ">", "\"", "'", "`", "#", "//", "/*", "*/", "\\", ";", ":", ",", ".", "::", "=>", "->",
    "\n", "\r\n", "\t", "\x00", "﻿", "é", "日本", "import ", "use ", "package ", "namespace ", "class ", "struct ",
    "extension ", "#if X\n", "#endif\n", "@", "$", "%", "&", "|", "!", "?", "...", "{{", "}}", "<<<", ">>>",
]


@st.composite
def mutated(draw, source: str) -> str:
    text = source
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        kind = draw(st.sampled_from(["insert", "delete", "replace", "truncate", "duplicate"]))
        pos = draw(st.integers(min_value=0, max_value=max(len(text), 0)))
        if kind == "insert":
            text = text[:pos] + draw(st.one_of(st.sampled_from(FRAGMENTS), st.text(max_size=12))) + text[pos:]
        elif kind == "delete":
            width = draw(st.integers(min_value=1, max_value=20))
            text = text[:pos] + text[pos + width:]
        elif kind == "replace":
            width = draw(st.integers(min_value=1, max_value=8))
            text = text[:pos] + draw(st.sampled_from(FRAGMENTS)) + text[pos + width:]
        elif kind == "truncate":
            text = text[:pos]
        else:
            text = text + text[: draw(st.integers(min_value=0, max_value=len(text)))]
    return text


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _build(repo: Path):
    modules, _meta, _extra = build_module_graph(repo)
    return sorted(
        ((m["path"], tuple(sorted(m.get("imports") or [])), tuple(sorted((m.get("import_confidence") or {}).items())))
         for m in modules)
    )


def _check(language: str, data) -> None:
    seed = SEEDS[language]
    files = {rel: data.draw(mutated(body)) for rel, body in seed.items()}
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        _write(repo, files)
        first = _build(repo)
        second = _build(repo)
    assert first == second, "the same repository gave two different graphs"
    paths = {path for path, _imports, _conf in first}
    for path, imports, _conf in first:
        assert path not in imports, f"{path} imports itself"
        for target in imports:
            assert target in paths, f"{path} imports {target}, which is not a file of the repository"


# CI runs a short, fixed-seed pass. FUZZ_EXAMPLES=2000 (any number) runs a deep, randomised campaign to hunt for bugs.
_EXAMPLES = int(os.environ.get("FUZZ_EXAMPLES", "0"))


def _make_test(language: str):
    @settings(max_examples=_EXAMPLES or 40, deadline=None, suppress_health_check=list(HealthCheck), derandomize=not _EXAMPLES)
    @given(st.data())
    def test(data):
        _check(language, data)

    test.__name__ = f"test_fuzz_{language}_graph_is_total_deterministic_and_closed"
    return test


for _language in SEEDS:
    globals()[f"test_fuzz_{_language}_graph_is_total_deterministic_and_closed"] = _make_test(_language)
del _language
