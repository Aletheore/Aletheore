"""Equidistant Java source sets, inline qualified Java types, short-named C# extension methods."""
from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _imports(repo: Path) -> dict[str, set[str]]:
    return {m["path"]: set(m["imports"]) for m in build_module_graph(repo)[0]}


def _two_source_sets(tmp_path: Path) -> None:
    # "full" is the primary set (3 files), "trim" a smaller copy declaring the same packages; both can satisfy Use.
    for name in ("Alpha", "Beta", "Gamma"):
        _write(tmp_path, f"lib/full/src/org/x/{name}.java", f"package org.x;\npublic class {name} {{}}\n")
    for name in ("Alpha", "Beta"):
        _write(tmp_path, f"lib/trim/src/org/x/{name}.java", f"package org.x;\npublic class {name} {{}}\n")


def test_java_equidistant_source_sets_prefer_the_one_covering_more_imports(tmp_path):
    _two_source_sets(tmp_path)  # trim lacks Gamma
    _write(tmp_path, "app/Use.java",
           "package app;\nimport org.x.Alpha;\nimport org.x.Gamma;\npublic class Use { Alpha a; Gamma g; }\n")
    assert _imports(tmp_path)["app/Use.java"] == {"lib/full/src/org/x/Alpha.java", "lib/full/src/org/x/Gamma.java"}


def test_java_equidistant_equal_coverage_prefers_the_larger_source_set(tmp_path):
    _two_source_sets(tmp_path)
    _write(tmp_path, "app/Use.java", "package app;\nimport org.x.Alpha;\npublic class Use { Alpha a; }\n")
    assert _imports(tmp_path)["app/Use.java"] == {"lib/full/src/org/x/Alpha.java"}


def test_java_importer_inside_a_source_set_still_uses_its_own_set(tmp_path):
    _two_source_sets(tmp_path)
    _write(tmp_path, "lib/trim/src/org/y/Use.java", "package org.y;\nimport org.x.Alpha;\npublic class Use { Alpha a; }\n")
    assert _imports(tmp_path)["lib/trim/src/org/y/Use.java"] == {"lib/trim/src/org/x/Alpha.java"}


def test_java_inline_fully_qualified_type_creates_an_edge(tmp_path):
    _write(tmp_path, "src/org/x/Err.java", "package org.x;\npublic class Err extends Exception {}\n")
    _write(tmp_path, "src/org/y/Use.java", "package org.y;\npublic class Use { void f() throws org.x.Err {} }\n")
    _write(tmp_path, "src/org/y/Quiet.java", "package org.y;\n// org.x.Err in a comment\npublic class Quiet { String s = \"org.x.Err\"; }\n")
    imports = _imports(tmp_path)
    assert imports["src/org/y/Use.java"] == {"src/org/x/Err.java"}
    assert imports["src/org/y/Quiet.java"] == set()


def _ext_repo(tmp_path: Path) -> None:
    _write(tmp_path, "P/P.csproj", '<Project Sdk="Microsoft.NET.Sdk"/>')
    _write(tmp_path, "P/Ext.cs",
           "namespace P;\npublic static class Ext {\n"
           "  public static bool Has<TAttr>(this System.Reflection.MemberInfo m) => true;\n"
           "  public static void Add(this System.Collections.Generic.List<int> l, string s) {}\n}\n")


def test_csharp_short_generic_extension_links_only_with_explicit_type_arguments(tmp_path):
    _ext_repo(tmp_path)
    _write(tmp_path, "P/Caller.cs", "namespace P;\npublic class Caller { bool b(System.Reflection.MemberInfo m) => m.Has<Attribute>(); }\n")
    _write(tmp_path, "P/Plain.cs", "namespace P;\npublic class Plain { void f(System.Collections.Generic.List<int> l) { l.Add(1); } }\n")
    imports = _imports(tmp_path)
    assert imports["P/Caller.cs"] == {"P/Ext.cs"}
    assert imports["P/Plain.cs"] == set()  # a bare .Add( is indistinguishable from List.Add


def test_csharp_short_extension_declared_in_two_files_is_not_linked(tmp_path):
    _write(tmp_path, "P/P.csproj", '<Project Sdk="Microsoft.NET.Sdk"/>')
    for n in ("A", "B"):
        _write(tmp_path, f"P/Ext{n}.cs", f"namespace P;\npublic static class Ext{n} {{ public static bool Has<T>(this string s) => true; }}\n")
    _write(tmp_path, "P/Caller.cs", "namespace P;\npublic class Caller { bool b(string s) => s.Has<int>(); }\n")
    assert _imports(tmp_path)["P/Caller.cs"] == set()
