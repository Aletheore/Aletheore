"""A field, static property, global or class variable written from a request in one handler and read by another carries the request value
to the sink; a constant, a different field, a sanitized value or a local of the same name stays quiet."""
import pytest

from aletheore.flow.discover import discover_repo

JAVA = ("import org.springframework.web.bind.annotation.*;\n@RestController public class App {{\n  {mod} String last = \"\";\n  String other = \"\";\n"
        "  @GetMapping(\"/set\") public String set(@RequestParam String q) {{ {write} }}\n"
        "  @GetMapping(\"/run\") public String run() throws Exception {{ Runtime.getRuntime().exec({read}); return \"ok\"; }}\n}}\n")
CS = ("using Microsoft.AspNetCore.Mvc;using System.Diagnostics;\npublic class C : Controller {{\n  {mod} string last = \"\";\n  string other = \"\";\n"
      "  [HttpGet(\"/set\")] public string Set(string q) {{ {write} return \"ok\"; }}\n"
      "  [HttpGet(\"/run\")] public string Run() {{ Process.Start(\"sh\", \"-c \" + {read}); return \"ok\"; }}\n}}\n")
KT = ("import org.springframework.web.bind.annotation.*\n\n@RestController\nclass A {{\n    companion object {{ var last: String = \"\"; var other: String = \"\" }}\n"
      "    @GetMapping(\"/set\")\n    fun set(@RequestParam q: String): String {{ {write}; return \"ok\" }}\n"
      "    @GetMapping(\"/run\")\n    fun run(): String {{ Runtime.getRuntime().exec({read}); return \"ok\" }}\n}}\n")
PHP_STATIC = "<?php\nclass S {{ public static $last = ''; public static $other = ''; }}\nfunction set() {{ {write} }}\nfunction run() {{ system({read}); }}\n"
PHP_GLOBAL = "<?php\nfunction set() {{ global $last, $other; {write} }}\nfunction run() {{ global $last, $other; system({read}); }}\n"
RB_GLOBAL = "require 'sinatra'\nget '/set' do\n  {write}\n  'ok'\nend\nget '/run' do\n  system({read})\n  'ok'\nend\n"
RB_CLASSVAR = "class Ctl < ApplicationController\n  def set\n    {write}\n  end\n  def run\n    system({read})\n  end\nend\n"

# (file, template, request value, constant value, field written, field read, name that is not the same field, write statement builder)
CASES = [
    ("App.java", JAVA, "q", '"x"', {"mod": "static"}, lambda f, v: f"{f} = {v}; return \"ok\";", "last", "other"),
    ("App.java", JAVA, "q", '"x"', {"mod": ""}, lambda f, v: f"this.{f} = {v}; return \"ok\";", "this.last", "this.other"),
    ("C.cs", CS, "q", '"x"', {"mod": "static"}, lambda f, v: f"{f} = {v};", "last", "other"),
    ("A.kt", KT, "q", '"x"', {}, lambda f, v: f"{f} = {v}", "last", "other"),
    ("a.php", PHP_STATIC, "$_GET['q']", "'x'", {}, lambda f, v: f"S::${f} = {v};", "S::$last", "S::$other"),
    ("a.php", PHP_GLOBAL, "$_GET['q']", "'x'", {}, lambda f, v: f"${f} = {v};", "$last", "$other"),
    ("app.rb", RB_GLOBAL, "params[:q]", "'x'", {}, lambda f, v: f"${f} = {v}", "$last", "$other"),
    ("app.rb", RB_CLASSVAR, "params[:q]", "'x'", {}, lambda f, v: f"@@{f} = {v}", "@@last", "@@other"),
]
IDS = ["java-static", "java-this", "cs-static", "kotlin-companion", "php-static", "php-global", "ruby-global", "ruby-classvar"]


def build(case, tmp_path, value, write_field="last", read=None):
    name, tpl, tainted, const, extra, w, read_last, _ = case
    val = {"req": tainted, "const": const}[value]
    (tmp_path / name).write_text(tpl.format(write=w(write_field, val), read=read or read_last, **extra))


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_request_value_reaches_the_sink(tmp_path, case):
    build(case, tmp_path, "req")
    assert len(discover_repo(tmp_path)) == 1


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_constant_value_stays_quiet(tmp_path, case):
    build(case, tmp_path, "const")
    assert discover_repo(tmp_path) == []


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_different_field_stays_quiet(tmp_path, case):
    build(case, tmp_path, "req", write_field=case[7].replace("this.", "").replace("S::$", "").replace("$", "").replace("@@", ""), read=case[6])
    assert discover_repo(tmp_path) == []


def test_a_local_of_the_same_name_is_not_the_field(tmp_path):
    (tmp_path / "App.java").write_text(JAVA.format(mod="static", write="String last = q; return \"ok\";", read="last"))
    assert discover_repo(tmp_path) == []


def test_an_encoded_value_stays_quiet(tmp_path):
    (tmp_path / "a.php").write_text(PHP_STATIC.format(write="S::$last = escapeshellarg($_GET['q']);", read="S::$last"))
    assert discover_repo(tmp_path) == []


# ---- objects and containers held by a shared field (D-45, D-46) and local aliases of them --------------------------------------------------

KT_OBJ = ("import javax.sql.DataSource\nimport org.springframework.web.bind.annotation.*\nclass Slot {{ var value: String = \"1\" }}\n@RestController\n"
          "class Controller(private val db: DataSource) {{\n val first = Slot(); val second = Slot()\n"
          " @GetMapping(\"/write\") fun write(@RequestParam id: String): String {{ {write}; return \"ok\" }}\n"
          " @GetMapping(\"/read\") fun read(): String {{ db.connection.createStatement().executeQuery(\"SELECT * FROM users WHERE id=\" + {read}); return \"ok\" }}\n}}\n")
CS_OBJ = ("using System.Web;\nusing System.Data.SqlClient;\nclass Slot {{ public string value = \"1\"; }}\nclass Controller {{\n readonly Slot first = new Slot(); readonly Slot second = new Slot();\n"
          " void Write(HttpRequest request) {{ {write}; }}\n void Read(SqlConnection db) {{ new SqlCommand(\"SELECT * FROM users WHERE id=\" + {read}, db).ExecuteReader(); }}\n}}\n")
JAVA_OBJ = ("import org.springframework.web.bind.annotation.*;\nimport java.sql.*;\n@RestController public class App {{\n  static class Slot {{ String value = \"1\"; }}\n"
            "  Slot first = new Slot(); Slot second = new Slot(); Connection db;\n"
            "  @GetMapping(\"/w\") public String w(@RequestParam String id) throws Exception {{ {write}; return \"ok\"; }}\n"
            "  @GetMapping(\"/r\") public String r() throws Exception {{ db.createStatement().executeQuery(\"SELECT * FROM users WHERE id=\" + {read}); return \"ok\"; }}\n}}\n")
OBJ = [
    ("Main.kt", KT_OBJ, "first.value = id", "val alias = first; alias.value = id", "first.value", "second.value"),
    ("Main.cs", CS_OBJ, "first.value = request.QueryString[\"id\"]", "var alias = first; alias.value = request.QueryString[\"id\"]", "first.value", "second.value"),
    ("App.java", JAVA_OBJ, "first.value = id", "Slot alias = first; alias.value = id", "first.value", "second.value"),
]
OBJ_IDS = ["kotlin", "csharp", "java"]


@pytest.mark.parametrize("case", OBJ, ids=OBJ_IDS)
@pytest.mark.parametrize("via", ["direct", "alias"])
def test_a_field_of_a_shared_object_carries_the_request_value(tmp_path, case, via):
    name, tpl, direct, alias, read, _ = case
    (tmp_path / name).write_text(tpl.format(write=direct if via == "direct" else alias, read=read))
    assert len(discover_repo(tmp_path)) == 1


@pytest.mark.parametrize("case", OBJ, ids=OBJ_IDS)
def test_another_shared_object_stays_quiet(tmp_path, case):
    name, tpl, direct, alias, _, other = case
    (tmp_path / name).write_text(tpl.format(write=direct, read=other))
    assert discover_repo(tmp_path) == []


@pytest.mark.parametrize("case", OBJ, ids=OBJ_IDS)
def test_a_constant_in_a_shared_object_stays_quiet(tmp_path, case):
    name, tpl, direct, alias, read, _ = case
    const = {"Main.kt": "first.value = \"x\"", "Main.cs": "first.value = \"x\"", "App.java": "first.value = \"x\""}[name]
    (tmp_path / name).write_text(tpl.format(write=const, read=read))
    assert discover_repo(tmp_path) == []


RB_HASH = "class ItemsController < ActionController::Base\n  @@first = {{\"value\" => \"date\"}}\n  @@second = {{\"value\" => \"date\"}}\n  def write\n    {write}\n  end\n  def run\n    system({read})\n  end\nend\n"


@pytest.mark.parametrize("write", ['@@first["value"] = params[:cmd]', 'x = @@first; x["value"] = params[:cmd]'], ids=["direct", "alias"])
def test_a_class_variable_hash_carries_the_request_value(tmp_path, write):
    (tmp_path / "app.rb").write_text(RB_HASH.format(write=write, read='@@first["value"]'))
    assert len(discover_repo(tmp_path)) == 1


def test_another_class_variable_hash_stays_quiet(tmp_path):
    (tmp_path / "app.rb").write_text(RB_HASH.format(write='@@first["value"] = params[:cmd]', read='@@second["value"]'))
    assert discover_repo(tmp_path) == []


def test_the_class_body_value_is_still_used_when_nothing_tainted_was_stored(tmp_path):
    (tmp_path / "app.rb").write_text(RB_HASH.format(write='@@first["value"] = "fine"', read='@@first["value"]'))
    assert discover_repo(tmp_path) == []


def test_a_global_hash_in_one_action_reaches_a_sink_in_the_same_action(tmp_path):
    (tmp_path / "app.rb").write_text("require 'sinatra'\nget '/r' do\n  $h = {}\n  $h['x'] = params[:c]\n  system($h['x'])\nend\n")
    assert len(discover_repo(tmp_path)) == 1
