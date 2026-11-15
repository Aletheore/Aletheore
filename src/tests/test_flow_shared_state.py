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
