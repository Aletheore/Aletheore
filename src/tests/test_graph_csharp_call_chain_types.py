"""The type of a method-call or property-access receiver follows the repository's declared members, so an extension
call chained off one binds to the overload for that type instead of keeping every candidate."""

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


MONEY = "namespace App;\npublic class Money {\n    public static Money Of(int x) => new Money();\n    public Money Doubled() => this;\n}\n"
SHOW_MONEY = "namespace App;\npublic static class MoneyExt { public static string Show(this Money m) => \"\"; }\n"
SHOW_SPAN = "namespace App;\npublic static class SpanExt { public static string Show(this System.TimeSpan t) => \"\"; }\n"
SHOW_DATE = "namespace App;\npublic static class DateExt { public static string Show(this System.DateTime t) => \"\"; }\n"


def _check(got: set[str], wanted: str, unwanted: tuple[str, ...]) -> None:
    assert wanted in got
    for other in unwanted:
        assert other not in got, other


def test_a_static_method_call_gives_its_declared_return_type(tmp_path):
    files = {
        "Money.cs": MONEY, "MoneyExt.cs": SHOW_MONEY, "SpanExt.cs": SHOW_SPAN,
        "T.cs": "namespace App;\npublic class T { string Run() => Money.Of(5).Show(); }\n",
    }
    _check(_imports(tmp_path, files, "T.cs"), "MoneyExt.cs", ("SpanExt.cs",))


def test_an_instance_method_call_gives_its_declared_return_type(tmp_path):
    files = {
        "Money.cs": MONEY, "MoneyExt.cs": SHOW_MONEY, "SpanExt.cs": SHOW_SPAN,
        "T.cs": "namespace App;\npublic class T { string Run(Money m) => m.Doubled().Show(); }\n",
    }
    _check(_imports(tmp_path, files, "T.cs"), "MoneyExt.cs", ("SpanExt.cs",))


def test_a_property_access_gives_the_property_type(tmp_path):
    files = {
        "Money.cs": MONEY, "MoneyExt.cs": SHOW_MONEY, "SpanExt.cs": SHOW_SPAN,
        "Holder.cs": "namespace App;\npublic class Holder { public Money Value { get; } }\n",
        "T.cs": "namespace App;\npublic class T { string Run(Holder h) => h.Value.Show(); }\n",
    }
    _check(_imports(tmp_path, files, "T.cs"), "MoneyExt.cs", ("SpanExt.cs",))


def test_an_inherited_property_gives_its_declared_type(tmp_path):
    files = {
        "Money.cs": MONEY, "MoneyExt.cs": SHOW_MONEY, "SpanExt.cs": SHOW_SPAN,
        "Base.cs": "namespace App;\npublic class Base { public Money Value { get; } }\n",
        "Derived.cs": "namespace App;\npublic class Derived : Base { }\n",
        "T.cs": "namespace App;\npublic class T { string Run(Derived d) => d.Value.Show(); }\n",
    }
    _check(_imports(tmp_path, files, "T.cs"), "MoneyExt.cs", ("SpanExt.cs",))


def test_well_known_dotnet_methods_keep_their_type(tmp_path):
    files = {
        "DateExt.cs": SHOW_DATE, "SpanExt.cs": SHOW_SPAN,
        "T.cs": "using System;\nnamespace App;\npublic class T { string Run() => DateTime.UtcNow.AddDays(1).Show(); }\n",
    }
    _check(_imports(tmp_path, files, "T.cs"), "DateExt.cs", ("SpanExt.cs",))


def test_overloads_with_different_return_types_keep_every_candidate(tmp_path):
    ambiguous = (
        "namespace App;\npublic class Maker {\n    public Money Make(int x) => null;\n"
        "    public System.TimeSpan Make(string s) => default;\n}\n"
    )
    files = {
        "Money.cs": MONEY, "Maker.cs": ambiguous, "MoneyExt.cs": SHOW_MONEY, "SpanExt.cs": SHOW_SPAN,
        "T.cs": "namespace App;\npublic class T { string Run(Maker k, object o) => k.Make(o).Show(); }\n",
    }
    got = _imports(tmp_path, files, "T.cs")
    assert "MoneyExt.cs" in got and "SpanExt.cs" in got  # the return type depends on the overload: undecidable here


def test_a_local_variable_named_like_a_type_is_not_a_static_call(tmp_path):
    files = {
        "Money.cs": MONEY, "MoneyExt.cs": SHOW_MONEY, "SpanExt.cs": SHOW_SPAN,
        "T.cs": "namespace App;\npublic class T { string Run(System.TimeSpan Money) => Money.Negate().Show(); }\n",
    }
    got = _imports(tmp_path, files, "T.cs")
    assert "SpanExt.cs" in got
    assert "MoneyExt.cs" not in got


def test_a_name_that_is_only_declared_in_a_file_is_not_a_use_of_the_same_named_extension(tmp_path):
    truncate_ext = "namespace App;\npublic static class TruncateExt { public static string Truncate(this string s, int n) => s; }\n"
    files = {
        "TruncateExt.cs": truncate_ext,
        "ITruncator.cs": "namespace App;\npublic interface ITruncator { string Truncate(string value, int length); }\n",
    }
    assert "TruncateExt.cs" not in _imports(tmp_path, files, "ITruncator.cs")


def test_a_call_of_a_same_named_extension_still_links(tmp_path):
    truncate_ext = "namespace App;\npublic static class TruncateExt { public static string Truncate(this string s, int n) => s; }\n"
    files = {
        "TruncateExt.cs": truncate_ext,
        "Truncator.cs": "namespace App;\npublic class Truncator { string Truncate(string v, int n) => v; string Run() => \"abc\".Truncate(2); }\n",
    }
    assert "TruncateExt.cs" in _imports(tmp_path, files, "Truncator.cs")


TRANSFORM_EXT = "namespace App;\npublic static class To { public static string Transform(this string s, int n) => s; }\n"


def test_a_call_without_a_receiver_is_not_an_extension_call(tmp_path):
    files = {
        "To.cs": TRANSFORM_EXT,
        "Upper.cs": "namespace App;\npublic class Upper { string Transform(string s) => s; string Run(string s) => Transform(s); }\n",
    }
    assert "To.cs" not in _imports(tmp_path, files, "Upper.cs")


def test_a_call_without_a_receiver_still_links_when_the_file_uses_using_static(tmp_path):
    files = {
        "To.cs": TRANSFORM_EXT,
        "Upper.cs": "using static App.To;\nnamespace App;\npublic class Upper { string Run(string s) => Transform(s); }\n",
    }
    assert "To.cs" in _imports(tmp_path, files, "Upper.cs")
