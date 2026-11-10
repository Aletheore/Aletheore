"""Regressions for Codex pass-2 defects D-16 to D-25, with the soundness cases that must keep reporting."""
from pathlib import Path

from aletheore.flow.discover import discover_repo

KT_HEAD = """import javax.sql.DataSource
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
@RestController
class C(private val db: DataSource) {
    @GetMapping("/x")
    fun search(@RequestParam name: String): String {
"""
KT_TAIL = """        return "ok"
    }
}
"""
SQL = 'db.connection.createStatement().executeQuery("SELECT * FROM u WHERE n = \'$X\'")'


def _scan(tmp_path: Path, rel: str, src: str):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src)
    return [(f["rule_id"].split(".")[-1], f["line"]) for f in discover_repo(tmp_path)]


def _kt(tmp_path, body):
    return _scan(tmp_path, "C.kt", KT_HEAD + body + KT_TAIL)


# D-16 Kotlin lambdas
def test_kt_invoked_lambda(tmp_path):
    assert _kt(tmp_path, "        val run = { " + SQL.replace("$X", "$name") + " }\n        run()\n")


def test_kt_lambda_invoke_method(tmp_path):
    assert _kt(tmp_path, "        val run = { " + SQL.replace("$X", "$name") + " }\n        run.invoke()\n")


def test_kt_lambda_never_invoked_constant_is_quiet(tmp_path):
    assert _kt(tmp_path, '        val q = "a"\n        val run = { ' + SQL.replace("$X", "$q") + " }\n        run()\n") == []


# D-17 Kotlin brace-less loop bodies
def test_kt_for_without_braces(tmp_path):
    assert _kt(tmp_path, '        var x = "safe"\n        for (i in 0..0) x = name\n        ' + SQL.replace("$X", "$x") + "\n")


def test_kt_while_without_braces(tmp_path):
    assert _kt(tmp_path, '        var x = "safe"\n        var i = 0\n        while (i++ < 1) x = name\n        ' + SQL.replace("$X", "$x") + "\n")


RS = """use actix_web::{web, HttpResponse};
use std::collections::HashMap;
use std::process::Command;
async fn h(query: web::Query<HashMap<String, String>>) -> HttpResponse {
%s
    HttpResponse::Ok().finish()
}
%s
"""


# D-22 Rust closures
def test_rs_invoked_closure(tmp_path):
    body = '    let x = query.get("cmd").unwrap();\n    let run = || { Command::new("sh").arg("-c").arg(x).output().unwrap(); };\n    run();'
    assert _scan(tmp_path, "src/main.rs", RS % (body, ""))


# D-23 Rust &mut out-parameters
def test_rs_mut_ref_assignment(tmp_path):
    body = '    let mut x = "date".to_string();\n    store(&mut x, query.get("cmd").unwrap());\n    Command::new("sh").arg("-c").arg(x).output().unwrap();'
    assert _scan(tmp_path, "src/main.rs", RS % (body, "fn store(out: &mut String, x: &str) { *out = x.to_string(); }"))


def test_rs_mut_ref_constant_write_is_quiet(tmp_path):
    body = '    let mut x = query.get("cmd").unwrap().to_string();\n    store(&mut x);\n    Command::new("sh").arg("-c").arg(x).output().unwrap();'
    assert _scan(tmp_path, "src/main.rs", RS % (body, 'fn store(out: &mut String) { *out = "date".to_string(); }')) == []


# D-21 Ruby Shellwords
def test_rb_shellwords_escape_is_quiet(tmp_path):
    src = 'require "shellwords"\nclass A < ActionController::Base\n  def show\n    x = params[:cmd]\n    system("echo " + Shellwords.escape(x))\n  end\nend\n'
    assert _scan(tmp_path, "app/controllers/a_controller.rb", src) == []


def test_rb_unescaped_still_reported(tmp_path):
    src = 'class A < ActionController::Base\n  def show\n    x = params[:cmd]\n    system("echo " + x)\n  end\nend\n'
    assert _scan(tmp_path, "app/controllers/a_controller.rb", src)


# D-25 Next.js App Router
NEXT = """import { createReadStream } from 'fs';
import { resolve, sep, join } from 'path';
export const GET = async (request: Request, context: { params: Promise<{ path?: string[] }> }) => {
  const { path } = await context.params;
%s
  const s = createReadStream(filePath);
  return new Response('ok');
};
"""


def test_next_route_in_frontend_package_traversal(tmp_path):
    body = "  const filePath = process.env.UP + '/' + (path ?? []).join('/');"
    assert ("path-traversal", 6) in _scan(tmp_path, "apps/frontend/src/app/api/u/[[...path]]/route.ts", NEXT % body)


def test_next_route_resolve_and_containment_is_quiet(tmp_path):
    body = ("  const base = resolve(process.env.UP!);\n  const filePath = resolve(base, (path ?? []).join('/'));\n"
            "  if (filePath !== base && !filePath.startsWith(base + sep)) { return new Response('no'); }")
    assert _scan(tmp_path, "apps/frontend/src/app/api/u/[[...path]]/route.ts", (NEXT % body).replace("createReadStream(filePath);\n", "createReadStream(filePath);\n", 1)) == []


def test_next_route_prefix_check_without_canonicalising_still_reported(tmp_path):
    body = ("  const base = resolve(process.env.UP!);\n  const filePath = join(base, (path ?? []).join('/'));\n"
            "  if (!filePath.startsWith(base + sep)) { return new Response('no'); }")
    assert _scan(tmp_path, "app/api/u/[[...path]]/route.ts", NEXT % body)


def test_next_route_prefix_from_request_still_reported(tmp_path):
    body = ("  const base = resolve(new URL(request.url).searchParams.get('root') || '/');\n"
            "  const filePath = resolve(base, (path ?? []).join('/'));\n"
            "  if (!filePath.startsWith(base + sep)) { return new Response('no'); }")
    assert _scan(tmp_path, "app/api/u/[[...path]]/route.ts", NEXT % body)


def test_frontend_express_file_still_skipped(tmp_path):
    src = "const fs = require('fs');\nconst app = require('express')();\napp.get('/f', (req, res) => { fs.readFileSync(req.query.p); });\n"
    assert _scan(tmp_path, "frontend/src/server.js", src) == []
