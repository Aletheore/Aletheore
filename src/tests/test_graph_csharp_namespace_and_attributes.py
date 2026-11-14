"""A namespace the parser loses to `#if` blocks is recovered from the text, and `[Foo]` binds to `FooAttribute`."""

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


# `#if` branches that start inside a declaration make tree-sitter report an error and drop the namespace node.
SPLIT_BY_IF = """
#if NET20
using System.Collections;
#else
using System.Collections.Generic;
#endif
#if DNXCORE50
public class Attr : System.Attribute { }
#endif
namespace App.Tests
{
    public abstract class FixtureBase { }
#if NET20
    public class Extra
    {
#else
    public class Extra2
    {
#endif
        public void M() { }
    }
}
"""


def test_a_type_in_a_file_whose_namespace_node_was_lost_to_if_blocks_is_still_linked(tmp_path):
    files = {
        "Base.cs": SPLIT_BY_IF,
        "Tests/Sub/Reader.cs": "namespace App.Tests.Sub\n{\n    public class Reader : FixtureBase { }\n}\n",
    }
    assert "Base.cs" in _imports(tmp_path, files, "Tests/Sub/Reader.cs")


def test_an_attribute_written_without_its_suffix_links_the_attribute_class(tmp_path):
    files = {
        "JsonPropertyAttribute.cs": "namespace App;\npublic class JsonPropertyAttribute : System.Attribute { }\n",
        "Model.cs": "namespace App;\npublic class Model { [JsonProperty(\"x\")] public int X; }\n",
    }
    assert "JsonPropertyAttribute.cs" in _imports(tmp_path, files, "Model.cs")


def test_a_qualified_attribute_name_links_the_attribute_class(tmp_path):
    files = {
        "KeepAttribute.cs": "namespace Lib;\npublic class KeepAttribute : System.Attribute { }\n",
        "Model.cs": "namespace Lib;\npublic class Model { [Lib.Keep] public int X; }\n",
    }
    assert "KeepAttribute.cs" in _imports(tmp_path, files, "Model.cs")


def test_an_extension_method_in_a_static_partial_class_links_its_file(tmp_path):
    files = {
        "Ext.cs": "namespace App;\npublic static partial class StringExtensions\n{\n    public static int Shout(this string s, int times) => times;\n}\n",
        "User.cs": "namespace App;\npublic class User { int F(string s) => s.Shout(2); }\n",
    }
    assert "Ext.cs" in _imports(tmp_path, files, "User.cs")


def test_a_part_of_a_partial_type_links_the_sibling_that_declares_a_nested_type_it_names(tmp_path):
    files = {
        "Db.cs": "namespace App;\npublic partial class Db { private class CacheInfo { } }\n",
        "Db.Async.cs": "namespace App;\npublic partial class Db { object M() { return new CacheInfo(); } }\n",
        "Other.cs": "namespace App;\npublic class Other { }\n",
    }
    assert "Db.cs" in _imports(tmp_path, files, "Db.Async.cs")
    assert "Db.Async.cs" not in _imports(tmp_path, files, "Db.cs")  # Db.cs names nothing the other part declares
    assert "Db.cs" not in _imports(tmp_path, files, "Other.cs")


def test_a_part_that_calls_a_member_declared_in_a_sibling_part_links_it_as_inferred(tmp_path):
    files = {
        "Db.cs": "namespace App;\npublic partial class Db { public int ComputeTotal() => 1; }\n",
        "Db.Async.cs": "namespace App;\npublic partial class Db { public int Run() => ComputeTotal(); }\n",
    }
    repo = tmp_path / "r"
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    modules, _, _ = build_module_graph(repo)
    part = next(m for m in modules if m["path"] == "Db.Async.cs")
    assert "Db.cs" in part["imports"]
    assert part["import_confidence"]["Db.cs"] == "inferred"


def test_two_unrelated_classes_with_the_same_name_in_different_namespaces_are_not_partial_parts(tmp_path):
    files = {
        "A.cs": "namespace One;\npublic partial class Db { public int ComputeTotal() => 1; }\n",
        "B.cs": "namespace Two;\npublic partial class Db { public int Run() => ComputeTotal(); }\n",
    }
    assert "A.cs" not in _imports(tmp_path, files, "B.cs")


def test_a_part_that_names_its_own_partial_type_in_its_body_depends_on_every_part(tmp_path):
    files = {
        "Db.cs": "namespace App;\npublic partial class Db { }\n",
        "Db.Async.cs": "namespace App;\npublic partial class Db { static void F() { Db.Helper(); } }\n",
        "Db.Other.cs": "namespace App;\npublic partial class Db { }\n",
    }
    got = _imports(tmp_path, files, "Db.Async.cs")
    assert {"Db.cs", "Db.Other.cs"} <= got
