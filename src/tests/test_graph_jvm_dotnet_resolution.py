"""Java source-root choice and same-package edges; C# project, namespace and extension-method resolution."""
from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _imports(repo: Path) -> dict[str, set[str]]:
    modules, _graph, _unparseable = build_module_graph(repo)
    return {m["path"]: set(m["imports"]) for m in modules}


# ---------------- Java ----------------

def test_java_import_prefers_source_root_nearest_the_importer(tmp_path):
    _write(tmp_path, "lib/full/src/main/java/org/x/Util.java", "package org.x;\npublic class Util {}\n")
    _write(tmp_path, "lib/me/src/org/x/Util.java", "package org.x;\npublic class Util {}\n")
    _write(tmp_path, "lib/full/src/main/java/org/y/Client.java",
           "package org.y;\nimport org.x.Util;\npublic class Client { Util u; }\n")
    _write(tmp_path, "lib/me/src/org/y/Client.java",
           "package org.y;\nimport org.x.Util;\npublic class Client { Util u; }\n")
    imports = _imports(tmp_path)
    assert imports["lib/full/src/main/java/org/y/Client.java"] == {"lib/full/src/main/java/org/x/Util.java"}
    assert imports["lib/me/src/org/y/Client.java"] == {"lib/me/src/org/x/Util.java"}


def test_java_wildcard_import_prefers_nearest_source_root(tmp_path):
    _write(tmp_path, "a/src/p/Thing.java", "package p;\npublic class Thing {}\n")
    _write(tmp_path, "b/deep/er/src/p/Thing.java", "package p;\npublic class Thing {}\n")
    _write(tmp_path, "b/deep/er/src/q/Use.java", "package q;\nimport p.*;\npublic class Use { Thing t; }\n")
    assert _imports(tmp_path)["b/deep/er/src/q/Use.java"] == {"b/deep/er/src/p/Thing.java"}


def test_java_same_package_reference_creates_edge_without_import(tmp_path):
    _write(tmp_path, "src/a/Base.java", "package a;\npublic class Base {}\n")
    _write(tmp_path, "src/a/Child.java", "package a;\npublic class Child extends Base {}\n")
    _write(tmp_path, "src/a/Other.java", "package a;\npublic class Other { int x; }\n")
    imports = _imports(tmp_path)
    assert imports["src/a/Child.java"] == {"src/a/Base.java"}
    assert imports["src/a/Other.java"] == set()


def test_java_same_package_ignores_comments_strings_nested_shadowing_and_type_params(tmp_path):
    _write(tmp_path, "src/a/Base.java", "package a;\npublic class Base {}\n")
    _write(tmp_path, "src/a/Node.java", "package a;\npublic class Node {}\n")
    _write(tmp_path, "src/a/T.java", "package a;\npublic class T {}\n")
    _write(tmp_path, "src/a/Quiet.java",
           'package a;\n// Base is only mentioned here\n/* Node too */\n'
           'public class Quiet<T> { String s = "Base Node"; static class Base {} Base b; T t; }\n')
    assert _imports(tmp_path)["src/a/Quiet.java"] == set()


def test_java_single_type_import_shadows_same_package_type(tmp_path):
    _write(tmp_path, "src/a/List.java", "package a;\npublic class List {}\n")
    _write(tmp_path, "src/b/List.java", "package b;\npublic class List {}\n")
    _write(tmp_path, "src/a/Use.java", "package a;\nimport b.List;\npublic class Use { List l; }\n")
    assert _imports(tmp_path)["src/a/Use.java"] == {"src/b/List.java"}


def test_java_same_package_only_matches_the_same_package(tmp_path):
    _write(tmp_path, "src/a/Base.java", "package a;\npublic class Base {}\n")
    _write(tmp_path, "src/b/Use.java", "package b;\npublic class Use { Base b; }\n")
    assert _imports(tmp_path)["src/b/Use.java"] == set()


# ---------------- C# ----------------

def _csproj(repo: Path, rel: str, refs: tuple[str, ...] = ()) -> None:
    items = "".join(f'<ProjectReference Include="{r}" />' for r in refs)
    _write(repo, rel, f'<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>{items}</ItemGroup></Project>')


def test_csharp_type_reference_respects_project_references(tmp_path):
    _csproj(tmp_path, "Lib/Lib.csproj")
    _csproj(tmp_path, "App/App.csproj", ("..\\Lib\\Lib.csproj",))
    _csproj(tmp_path, "Other/Other.csproj")
    _write(tmp_path, "Lib/Widget.cs", "namespace Shared;\npublic class Widget {}\n")
    _write(tmp_path, "App/Use.cs", "using Shared;\nnamespace App;\npublic class Use { Widget w; }\n")
    _write(tmp_path, "Other/Stray.cs", "using Shared;\nnamespace Other;\npublic class Stray { Widget w; }\n")
    imports = _imports(tmp_path)
    assert imports["App/Use.cs"] == {"Lib/Widget.cs"}
    assert imports["Other/Stray.cs"] == set()  # Other does not reference Lib


def test_csharp_duplicate_type_names_resolved_by_visible_namespace(tmp_path):
    _csproj(tmp_path, "P/P.csproj")
    _write(tmp_path, "P/A/Source.cs", "namespace P.A;\npublic class Source {}\n")
    _write(tmp_path, "P/B/Source.cs", "namespace P.B;\npublic class Source {}\n")
    _write(tmp_path, "P/A/UseA.cs", "namespace P.A;\npublic class UseA { Source s; }\n")
    _write(tmp_path, "P/B/UseB.cs", "namespace P.B;\npublic class UseB { Source s; }\n")
    _write(tmp_path, "P/C/UseC.cs", "using P.B;\nnamespace P.C;\npublic class UseC { Source s; }\n")
    _write(tmp_path, "P/D/UseD.cs", "namespace P.D;\npublic class UseD { Source s; }\n")  # sees neither
    imports = _imports(tmp_path)
    assert imports["P/A/UseA.cs"] == {"P/A/Source.cs"}
    assert imports["P/B/UseB.cs"] == {"P/B/Source.cs"}
    assert imports["P/C/UseC.cs"] == {"P/B/Source.cs"}
    assert imports["P/D/UseD.cs"] == set()


def test_csharp_partial_type_links_every_part(tmp_path):
    _write(tmp_path, "Big.Part1.cs", "namespace N;\npublic partial class BigThing {}\n")
    _write(tmp_path, "Big.Part2.cs", "namespace N;\npublic partial class BigThing {}\n")
    _write(tmp_path, "Use.cs", "namespace N;\npublic class Use { BigThing b; }\n")
    assert _imports(tmp_path)["Use.cs"] == {"Big.Part1.cs", "Big.Part2.cs"}


def test_csharp_nested_types_are_not_owners(tmp_path):
    _write(tmp_path, "Holder.cs", "namespace N;\npublic class Holder { public class Source {} }\n")
    _write(tmp_path, "Use.cs", "namespace N;\npublic class Use { Source s; }\n")
    assert _imports(tmp_path)["Use.cs"] == set()


def test_csharp_using_alone_creates_no_directory_fan_out(tmp_path):
    _csproj(tmp_path, "P/P.csproj")
    for name in ("One", "Two", "Three"):
        _write(tmp_path, f"P/Lib/{name}Thing.cs", f"namespace P.Lib;\npublic class {name}Thing {{}}\n")
    _write(tmp_path, "P/Use.cs", "using P.Lib;\nnamespace P;\npublic class Use { OneThing t; }\n")
    assert _imports(tmp_path)["P/Use.cs"] == {"P/Lib/OneThing.cs"}


def test_csharp_extension_method_call_links_declaring_file_only_when_namespace_visible(tmp_path):
    _csproj(tmp_path, "P/P.csproj")
    _write(tmp_path, "P/Ext/StringExt.cs",
           "namespace P.Ext;\npublic static class StringExt { public static int CountVowels(this string s) => 0; }\n")
    _write(tmp_path, "P/Caller.cs", "using P.Ext;\nnamespace P;\npublic class Caller { int n = \"x\".CountVowels(); }\n")
    _write(tmp_path, "P/NoCall.cs", "using P.Ext;\nnamespace P;\npublic class NoCall { int n = 1; }\n")
    _write(tmp_path, "P/NoUsing.cs", "namespace P;\npublic class NoUsing { int n = \"x\".CountVowels(); }\n")
    imports = _imports(tmp_path)
    assert imports["P/Caller.cs"] == {"P/Ext/StringExt.cs"}
    assert imports["P/NoCall.cs"] == set()
    assert imports["P/NoUsing.cs"] == set()


def test_csharp_names_in_comments_and_using_lines_are_not_references(tmp_path):
    _write(tmp_path, "Extensions.cs", "namespace N;\npublic class Extensions {}\n")
    _write(tmp_path, "Use.cs",
           "using Microsoft.Extensions.Logging;\nnamespace N;\n// Extensions in a comment\npublic class Use { string s = \"Extensions\"; }\n")
    assert _imports(tmp_path)["Use.cs"] == set()
