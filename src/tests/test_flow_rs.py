"""Rust discover and validate modes: web-framework extractor data is followed to sinks across functions and types,
constants and bound parameters are recognised as safe, and anything the engine cannot model is kept."""

import pytest

from aletheore.flow.discover_rs import discover_rs, validate_rs_finding


def run(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return discover_rs(tmp_path)


def kinds(found):
    return sorted({f["rule_id"].split(".")[-1] for f in found})


def handler(body, extra="", params="q: web::Query<P>"):
    return ("use actix_web::*;\n" + extra + "\n#[get(\"/x\")]\nasync fn h(" + params + ") -> impl Responder {\n    let n = q.name.clone();\n" + body + "\n    HttpResponse::Ok().finish()\n}\n")


TAINTED = {
    "format query": (handler('conn.execute(&format!("select * from t where n=\'{}\'", n), []);'), "sql-injection"),
    "inline format arg": (handler('conn.execute(&format!("select {n}"), []);'), "sql-injection"),
    "push_str": (handler('let mut s = String::from("select ");\ns.push_str(&n);\nconn.execute(&s, []);'), "sql-injection"),
    "concat": (handler('let s = "select ".to_string() + &n;\nconn.execute(&s, []);'), "sql-injection"),
    "sqlx": (handler('sqlx::query(&format!("select {}", n)).fetch_all(&pool);'), "sql-injection"),
    "command": (handler('Command::new(&n).output();'), "command-injection"),
    "shell -c": (handler('Command::new("sh").arg("-c").arg(&n).output();'), "command-injection"),
    "file open": (handler('File::open(&n);'), "path-traversal"),
    "fs read": (handler('std::fs::read_to_string(format!("/d/{}", n));'), "path-traversal"),
    "named file": (handler('NamedFile::open(&n);'), "path-traversal"),
    "redirect": (handler('Redirect::to(&n);'), "open-redirect"),
    "ssrf": (handler('reqwest::get(&n);'), "ssrf"),
    "html body": (handler('HttpResponse::Ok().content_type("text/html").body(format!("<b>{}</b>", n));'), "xss"),
    "through helper": (handler('run(&n);', "fn run(x: &str) { Command::new(x).output(); }"), "command-injection"),
    "match": (handler('let s = match n.as_str() { "a" => "x".to_string(), other => other.to_string() };\nconn.execute(&s, []);'), "sql-injection"),
    "if let": (handler('if let Some(v) = Some(n.clone()) { conn.execute(&v, []); }'), "sql-injection"),
    "vec": (handler('let v = vec![n.clone()];\nconn.execute(&v[0], []);'), "sql-injection"),
    "closure": (handler('vec![n.clone()].iter().for_each(|x| { conn.execute(x, []); });'), "sql-injection"),
    "request": (handler('conn.execute(req.query_string(), []);', params="q: web::Query<P>, req: HttpRequest"), "sql-injection"),
    "method on type": (handler('S::go(&n);', "struct S;\nimpl S { fn go(x: &str) { Command::new(x).output(); } }"), "command-injection"),
}


@pytest.mark.parametrize("case", sorted(TAINTED))
def test_tainted_flows_are_reported(tmp_path, case):
    src, kind = TAINTED[case]
    assert kind in kinds(run(tmp_path, {"src/main.rs": src}))


SAFE = {
    "const": handler('conn.execute(&format!("select * from {}", T), []);', 'const T: &str = "users";'),
    "bound": handler('conn.execute("select * from t where n = ?1", [&n]);'),
    "parse": handler('let id: i64 = n.parse().unwrap();\nconn.execute(&format!("select {}", id), []);'),
    "int param": handler('conn.execute(&format!("select {}", id), []);', params="id: web::Path<u32>"),
    "constant command": handler('Command::new("ls").arg(&n).output();'),
    "constant file": handler('File::open("/etc/app.conf");'),
    "dead branch": handler('if false { Command::new(&n).output(); }'),
    "constant folded": handler('let x = "a";\nconn.execute(&format!("select {}", x), []);'),
    "constant redirect": handler('Redirect::to("/home");'),
    "plain text body": handler('HttpResponse::Ok().body(format!("hi {}", n));'),
    "not a db receiver": handler('cache.execute(&n);'),
}


@pytest.mark.parametrize("case", sorted(SAFE))
def test_safe_flows_are_quiet(tmp_path, case):
    assert run(tmp_path, {"src/main.rs": SAFE[case]}) == []


def test_cross_file(tmp_path):
    files = {
        "src/run.rs": "pub fn go(c: &str) {\n    Command::new(c).output();\n}\n",
        "src/main.rs": "#[get(\"/a\")]\nasync fn a(q: web::Query<P>) {\n    go(\"ls\");\n    go(&q.c);\n}\n",
    }
    assert "command-injection" in kinds(run(tmp_path, files))


def test_validate_drops_constant_and_keeps_tainted(tmp_path):
    safe = handler('conn.execute(&format!("select * from {}", T), []);', 'const T: &str = "users";')
    bad = handler('conn.execute(&format!("select * from {}", n), []);')
    (tmp_path / "s.rs").write_text(safe)
    (tmp_path / "b.rs").write_text(bad)

    def span(src):
        for i, l in enumerate(src.splitlines(), 1):
            if "conn.execute" in l:
                return (i, l.index("conn") + 1), (i, len(l) + 1)
    assert validate_rs_finding(tmp_path, "s.rs", *span(safe), {89}).droppable
    assert not validate_rs_finding(tmp_path, "b.rs", *span(bad), {89}).droppable


def test_opaque_container_not_trusted(tmp_path):
    src = handler('let mut v = vec!["a".to_string()];\nmystery(&mut v);\nconn.execute(&v[0], []);')
    (tmp_path / "a.rs").write_text(src)
    ln, line = next((i, x) for i, x in enumerate(src.splitlines(), 1) if "conn.execute" in x)
    assert not validate_rs_finding(tmp_path, "a.rs", (ln, line.index("conn") + 1), (ln, len(line) + 1), {89}).droppable
