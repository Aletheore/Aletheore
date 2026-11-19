"""Which extension method a call binds to follows the static type of its receiver (what the compiler's overload resolution does)."""

from pathlib import Path

import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser

from aletheore.scanner.csharp_receivers import ReceiverTypes, definitely_not, normalize_type
from aletheore.scanner.graph import build_module_graph


def _imports(tmp_path: Path, files: dict[str, str], path: str) -> set[str]:
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return set(next(m for m in modules if m["path"] == path)["imports"])


def _calls(source: str, kinds: dict[str, str] | None = None, ext_returns: dict[str, str] | None = None) -> dict:
    data = source.encode()
    tree = Parser(Language(tscsharp.language())).parse(data)
    return ReceiverTypes(tree.root_node, data, kinds or {}, ext_returns).calls


def test_normalize_type_strips_generics_namespaces_nullability_and_aliases():
    assert normalize_type("System.Collections.Generic.List<string>?") == "List"
    assert normalize_type("Int32") == "int"
    assert normalize_type("int[]") == "int[]"


def test_value_types_that_are_clearly_different_are_rejected_but_numeric_widening_is_accepted():
    kinds: dict[str, str] = {}
    assert definitely_not("string", "DateTime", set(), kinds, lambda n: set())
    assert definitely_not("DateTime", "DateOnly", set(), kinds, lambda n: set())
    assert not definitely_not("int", "long", set(), kinds, lambda n: set())
    assert definitely_not("double", "int", set(), kinds, lambda n: set())


def test_anything_not_clearly_incompatible_is_accepted():
    kinds = {"Dog": "class", "Animal": "class", "Cat": "class", "IPet": "interface"}
    ancestors = {"Dog": {"Animal", "IPet"}}.get
    assert not definitely_not("Dog", "Animal", set(), kinds, lambda n: ancestors(n, set()))
    assert not definitely_not("Dog", "IPet", set(), kinds, lambda n: ancestors(n, set()))
    assert definitely_not("Dog", "Cat", set(), kinds, lambda n: ancestors(n, set()))
    assert not definitely_not("Dog", "T", {"T"}, kinds, lambda n: ancestors(n, set()))
    assert not definitely_not("string", "IEnumerable", set(), kinds, lambda n: set())  # a BCL interface: cannot tell
    assert not definitely_not("Mystery", "string", set(), kinds, lambda n: set())  # an unknown receiver type is never rejected


def test_receiver_types_of_literals_declarations_and_new():
    calls = _calls(
        'class C { string field; void M(int n, Foo f) { var s = "x"; var d = new Bar(); long big = 5; '
        's.Go(); n.Go(); f.Go(); d.Go(); big.Go(); 3.14.Go(); true.Go(); field.Go(); mystery.Go(); } }'
    )
    assert calls["Go"] == ["string", "int", "Foo", "Bar", "long", "double", "bool", "string", None]


def test_the_same_variable_name_in_two_methods_is_two_variables():
    calls = _calls("class C { void A(string x) { x.Go(); } void B(int x) { x.Go(); } }")
    assert calls["Go"] == ["string", "int"]


def test_a_lambda_parameter_is_not_the_outer_variable():
    calls = _calls("class C { void A(string x) { items.Select(x => x.Go()); } }")
    assert calls["Go"] == [None]


def test_arithmetic_comparison_and_element_access_types():
    calls = _calls('class C { void M(int a, string s) { (a + 1).Go(); (a > 1).Go(); (s + a).Go(); s[0].Go(); (a * 2.5).Go(); } }')
    assert calls["Go"] == ["int", "bool", "string", "char", "double"]


def test_enum_member_and_static_bcl_member_types():
    calls = _calls("class C { void M() { Color.Red.Go(); int.MaxValue.Go(); DateTime.Now.Go(); } }", {"Color": "enum"})
    assert calls["Go"] == ["Color", "int", "DateTime"]


def test_chained_extension_calls_use_the_shared_return_type():
    calls = _calls('class C { void M(string s) { s.Humanize().Go(); s.ToString().Go(); s.Other().Go(); } }', ext_returns={"Humanize": "string"})
    assert calls["Go"] == ["string", "string", None]


HUMANIZE_STRING = "namespace App;\npublic static class StringExt { public static string Humanize(this string input) => input; }\n"
HUMANIZE_SPAN = "namespace App;\npublic static class SpanExt { public static string Humanize(this System.TimeSpan input) => \"\"; }\n"
HUMANIZE_ENUM_USER = "namespace App;\npublic static class DateExt { public static string Humanize(this System.DateTime input) => \"\"; }\n"


def test_a_call_links_only_the_extension_overload_its_receiver_type_selects(tmp_path):
    files = {
        "StringExt.cs": HUMANIZE_STRING, "SpanExt.cs": HUMANIZE_SPAN, "DateExt.cs": HUMANIZE_ENUM_USER,
        "T.cs": 'namespace App;\npublic class T { string Run() => "abc".Humanize(); }\n',
    }
    got = _imports(tmp_path, files, "T.cs")
    assert "StringExt.cs" in got
    assert "SpanExt.cs" not in got and "DateExt.cs" not in got


def test_an_overload_on_exactly_the_receiver_type_beats_a_more_general_one(tmp_path):
    general = "namespace App;\npublic static class SeqExt { public static string Humanize<T>(this System.Collections.Generic.IEnumerable<T> items) => \"\"; }\n"
    files = {
        "StringExt.cs": HUMANIZE_STRING, "SeqExt.cs": general,
        "T.cs": 'namespace App;\npublic class T { string Run() => "abc".Humanize(); }\n',
    }
    got = _imports(tmp_path, files, "T.cs")
    assert "StringExt.cs" in got and "SeqExt.cs" not in got


def test_an_unknown_receiver_keeps_every_candidate_extension(tmp_path):
    files = {
        "StringExt.cs": HUMANIZE_STRING, "SpanExt.cs": HUMANIZE_SPAN,
        "T.cs": "namespace App;\npublic class T { string Run(object o) => Make().Humanize(); object Make() => null; }\n",
    }
    got = _imports(tmp_path, files, "T.cs")
    assert "StringExt.cs" in got and "SpanExt.cs" in got
