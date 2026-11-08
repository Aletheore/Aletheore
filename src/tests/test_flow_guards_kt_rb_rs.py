"""Condition guards in Kotlin, Ruby and Rust: an allowlist, a safe anchored regex, a path-token check or a numeric check before a
sink proves the value harmless on the path that continues; a guard that proves nothing (a regex allowing quotes) does not."""

import pytest

from aletheore.flow.discover import discover_repo


def kinds(tmp_path, name, src):
    (tmp_path / name).write_text(src)
    return sorted({f["rule_id"].split(".")[-1] for f in discover_repo(tmp_path)})


def kt(body, extra=""):
    return ("@RestController\nclass C(val jdbc: JdbcTemplate) {\n" + extra + "\n    @GetMapping(\"/x\")\n    fun act(@RequestParam name: String): String {\n" + body + "\n        return \"ok\"\n    }\n}\n")


KT_SAFE = {
    "regex allowlist": 'if (!name.matches(Regex("[A-Za-z]+"))) { return "bad" }\n        jdbc.queryForList("select * from t where n=\'" + name + "\'")',
    "regex object": 'if (!Regex("^[A-Z]{2}-[0-9]{1,4}$").matches(name)) { return "bad" }\n        jdbc.queryForList("select " + name)',
    "equality": 'if (name != "a" && name != "b") { return "bad" }\n        jdbc.queryForList("select " + name)',
    "list membership": 'if (name !in listOf("a", "b")) { return "bad" }\n        jdbc.queryForList("select " + name)',
    "numeric": 'if (!name.all { it.isDigit() }) { return "bad" }\n        jdbc.queryForList("select " + name)',
}


@pytest.mark.parametrize("case", sorted(KT_SAFE))
def test_kotlin_guard_proves_value_safe(tmp_path, case):
    assert kinds(tmp_path, "C.kt", kt(KT_SAFE[case])) == []


def test_kotlin_regex_with_quote_is_not_a_guard(tmp_path):
    body = 'if (!name.matches(Regex("[A-Za-z\' ]+"))) { return "bad" }\n        jdbc.queryForList("select \'" + name + "\'")'
    assert "sql-injection" in kinds(tmp_path, "C.kt", kt(body))


def test_kotlin_path_token_guard(tmp_path):
    body = 'if (name.contains("..") || name.contains("/") || name.contains("\\\\")) { return "bad" }\n        java.io.File("/srv/" + name).readText()'
    assert kinds(tmp_path, "C.kt", kt(body)) == []
    assert "path-traversal" in kinds(tmp_path / "x", "C.kt", kt('java.io.File("/srv/" + name).readText()')) if (tmp_path / "x").mkdir() is None else False


RB = "class HomeController < ApplicationController\n  def act\n    n = params[:n]\n%s\n  end\nend\n"


def rb(body):
    return RB % body


RB_SAFE = {
    "regex guard clause": "    return unless n =~ /\\A[a-z]+\\z/\n    User.find_by_sql(\"select * from u where n = '\" + n + \"'\")",
    "match?": "    return unless n.match?(/\\A[A-Z]{2}-[0-9]+\\z/)\n    system(\"echo \" + n)",
    "include": "    return unless %w[a b].include?(n)\n    system(\"echo \" + n)",
    "equality": "    unless n == 'a' || n == 'b'\n      return\n    end\n    system(\"echo \" + n)",
    "path tokens": "    return if n.include?('..') || n.include?('/') || n.include?('\\\\')\n    File.read('/srv/' + n)",
}


@pytest.mark.parametrize("case", sorted(RB_SAFE))
def test_ruby_guard_proves_value_safe(tmp_path, case):
    assert kinds(tmp_path, "app.rb", rb(RB_SAFE[case])) == []


def test_ruby_unguarded_and_quote_regex_still_flow(tmp_path):
    assert "command-injection" in kinds(tmp_path, "app.rb", rb("    system(\"echo \" + n)"))
    d = tmp_path / "q"
    d.mkdir()
    assert "command-injection" in kinds(d, "app.rb", rb("    return unless n =~ /\\A[a-z' ;]+\\z/\n    system(\"echo \" + n)"))


def rs(body):
    return ("use actix_web::*;\n#[get(\"/x\")]\nasync fn h(q: web::Query<P>) -> impl Responder {\n    let n = q.name.clone();\n" + body + "\n    HttpResponse::Ok().finish()\n}\n")


RS_SAFE = {
    "regex": 'let re = regex::Regex::new(r"^[a-z]+$").unwrap();\n    if !re.is_match(&n) { return HttpResponse::BadRequest().finish(); }\n    Command::new("sh").arg("-c").arg(format!("echo {}", n)).output();',
    "equality": 'if n != "a" && n != "b" { return HttpResponse::BadRequest().finish(); }\n    Command::new("sh").arg("-c").arg(format!("echo {}", n)).output();',
    "contains list": 'if !["a", "b"].contains(&n.as_str()) { return HttpResponse::BadRequest().finish(); }\n    Command::new("sh").arg("-c").arg(format!("echo {}", n)).output();',
    "chars all": 'if !n.chars().all(|c| c.is_alphanumeric()) { return HttpResponse::BadRequest().finish(); }\n    Command::new("sh").arg("-c").arg(format!("echo {}", n)).output();',
    "parse ok": 'if !n.parse::<u32>().is_ok() { return HttpResponse::BadRequest().finish(); }\n    Command::new("sh").arg("-c").arg(format!("echo {}", n)).output();',
}


@pytest.mark.parametrize("case", sorted(RS_SAFE))
def test_rust_guard_proves_value_safe(tmp_path, case):
    assert kinds(tmp_path, "main.rs", rs(RS_SAFE[case])) == []


def test_rust_unguarded_still_flows(tmp_path):
    assert "command-injection" in kinds(tmp_path, "main.rs", rs('Command::new("sh").arg("-c").arg(format!("echo {}", n)).output();'))


def test_ruby_lambda_global_halt_and_nested_dig(tmp_path):
    src = ('require "sinatra"\n$g = nil\nget("/a") do\n  h = ->(c) { `echo #{c}` }\n  h.call(params[:c])\nend\n'
           'get("/b") do\n  $g = params[:p]\n  File.read($g)\nend\n'
           'get("/c") do\n  cfg = { "p" => { "l" => params[:f] } }\n  File.read(cfg.dig("p", "l"))\nend\n')
    assert {"command-injection", "path-traversal"} <= set(kinds(tmp_path, "app.rb", src))
    d = tmp_path / "h"
    d.mkdir()
    safe = 'require "sinatra"\nget("/e") do\n  w = params[:w]\n  halt 400 unless w =~ /\\A[a-z]+\\z/\n  `echo #{w}`\nend\n'
    assert kinds(d, "app.rb", safe) == []


def test_same_site_path_prefix_is_not_an_open_redirect(tmp_path):
    src = 'class H < ApplicationController\n  def a\n    redirect_to("/session/#{params[:page]}")\n  end\nend\n'
    assert kinds(tmp_path, "h.rb", src) == []
    d = tmp_path / "o"
    d.mkdir()
    assert "open-redirect" in kinds(d, "h.rb", 'class H < ApplicationController\n  def a\n    redirect_to(params[:u])\n  end\nend\n')
