"""Kotlin discover and validate modes: Spring, servlet and Ktor request data is followed to sinks across functions
and classes, constants and parameter binding are recognised as safe, and anything the engine cannot model is kept."""

import pytest

from aletheore.flow.discover_kt import discover_kt, validate_kt_finding


def run(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return discover_kt(tmp_path)


def kinds(found):
    return sorted({f["rule_id"].split(".")[-1] for f in found})


def ctrl(body, extra="", params="@RequestParam name: String, req: HttpServletRequest"):
    return ("@RestController\nclass C(val jdbc: JdbcTemplate) {\n" + extra + "\n    @GetMapping(\"/x\")\n    fun act(" + params + "): String {\n" + body + "\n        return \"ok\"\n    }\n}\n")


TAINTED = {
    "concat query": (ctrl('jdbc.queryForList("select * from t where n=\'" + name + "\'")'), "sql-injection"),
    "template query": (ctrl('jdbc.queryForList("select * from t where n=\'$name\'")'), "sql-injection"),
    "brace template": (ctrl('jdbc.queryForList("select * from t where n=\'${req.getParameter("n")}\'")'), "sql-injection"),
    "statement": (ctrl('val s = conn.createStatement()\ns.executeQuery("select " + name)'), "sql-injection"),
    "via local": (ctrl('val q = "select " + name\njdbc.execute(q)'), "sql-injection"),
    "runtime exec": (ctrl('Runtime.getRuntime().exec("ls " + name)'), "command-injection"),
    "process builder": (ctrl('ProcessBuilder("sh", "-c", name).start()'), "command-injection"),
    "file": (ctrl('File("/data/" + name).readText()'), "path-traversal"),
    "paths": (ctrl('Files.readAllBytes(Paths.get(name))'), "path-traversal"),
    "redirect": (ctrl('resp.sendRedirect(name)', params="@RequestParam name: String, resp: HttpServletResponse"), "open-redirect"),
    "ssrf": (ctrl('URL(name).openStream()'), "ssrf"),
    "rest template": (ctrl('restTemplate.getForObject(name, String::class.java)'), "ssrf"),
    "writer": (ctrl('resp.writer.print("<b>" + name + "</b>")', params="@RequestParam name: String, resp: HttpServletResponse"), "xss"),
    "ois": (ctrl('ObjectInputStream(req.inputStream).readObject()'), "insecure-deserialization"),
    "class forname": (ctrl('Class.forName(name)'), "code-injection"),
    "through helper": (ctrl('run(name)', "    fun run(x: String) { Runtime.getRuntime().exec(x) }"), "command-injection"),
    "field": (ctrl('v = name\ngo()', "    var v: String = \"\"\n    fun go() { Runtime.getRuntime().exec(v) }"), "command-injection"),
    "request param": (ctrl('jdbc.execute("select " + req.getParameter("a"))'), "sql-injection"),
    "list": (ctrl('val l = mutableListOf<String>()\nl.add(name)\njdbc.execute("select " + l[0])'), "sql-injection"),
    "stringbuilder": (ctrl('val sb = StringBuilder()\nsb.append("select ")\nsb.append(name)\njdbc.execute(sb.toString())'), "sql-injection"),
    "branch": (ctrl('var q = "select 1"\nif (name.length > 3) q = "select " + name\njdbc.execute(q)'), "sql-injection"),
    "for each": (ctrl('listOf(name).forEach { jdbc.execute("select " + it) }'), "sql-injection"),
    "session": (ctrl('session.setAttribute("u", name)', params="@RequestParam name: String, session: HttpSession"), "trust-boundary"),
    "unannotated string param": (ctrl('jdbc.execute("select " + id)', params="id: String"), "sql-injection"),
    "when": (ctrl('val q = when (name) { "a" -> "x" else -> name }\njdbc.execute("select " + q)'), "sql-injection"),
}


@pytest.mark.parametrize("case", sorted(TAINTED))
def test_tainted_flows_are_reported(tmp_path, case):
    src, kind = TAINTED[case]
    assert kind in kinds(run(tmp_path, {"C.kt": src}))


SAFE = {
    "const": ctrl('jdbc.execute("select * from " + T)', "    companion object {\n        const val T = \"users\"\n    }"),
    "int param": ctrl('jdbc.execute("select " + id)', params="@RequestParam id: Int"),
    "toInt": ctrl('jdbc.execute("select " + name.toInt())'),
    "html escaped": ctrl('resp.writer.print(StringEscapeUtils.escapeHtml4(name))', params="@RequestParam name: String, resp: HttpServletResponse"),
    "constant redirect": ctrl('resp.sendRedirect("/home")', params="@RequestParam name: String, resp: HttpServletResponse"),
    "dead branch": ctrl('if (false) { jdbc.execute("select " + name) }'),
    "constant folded": ctrl('val x = "a"\njdbc.execute("select " + x)'),
    "private helper const": ctrl('jdbc.execute(table())', "    fun table(): String = \"select 1\""),
    "constant file": ctrl('File("/etc/app.conf").readText()'),
    "ktor respondText is plain text": ctrl('call.respondText("hi " + name)'),
    "not a db receiver": ctrl('cache.update(name)'),
}


@pytest.mark.parametrize("case", sorted(SAFE))
def test_safe_flows_are_quiet(tmp_path, case):
    assert run(tmp_path, {"C.kt": SAFE[case]}) == []


def test_cross_class(tmp_path):
    files = {
        "Runner.kt": 'object Runner {\n    fun go(c: String) {\n        Runtime.getRuntime().exec(c)\n    }\n}\n',
        "C.kt": '@RestController\nclass C {\n    @GetMapping("/a")\n    fun a(@RequestParam c: String) {\n        Runner.go("ls")\n        Runner.go(c)\n    }\n}\n',
    }
    found = run(tmp_path, files)
    assert [f["path"] for f in found] == ["Runner.kt"]


def test_validate_drops_constant_and_keeps_tainted(tmp_path):
    safe = ctrl('jdbc.execute("select * from " + T)', "    companion object {\n        const val T = \"users\"\n    }")
    bad = ctrl('jdbc.execute("select * from " + name)')
    (tmp_path / "s.kt").write_text(safe)
    (tmp_path / "b.kt").write_text(bad)

    def span(src):
        for i, l in enumerate(src.splitlines(), 1):
            if "jdbc.execute" in l:
                return (i, l.index("jdbc") + 1), (i, len(l) + 1)
    assert validate_kt_finding(tmp_path, "s.kt", *span(safe), {89}).droppable
    assert not validate_kt_finding(tmp_path, "b.kt", *span(bad), {89}).droppable


def test_opaque_container_not_trusted(tmp_path):
    src = ctrl('val l = mutableListOf("a")\nmystery(l)\njdbc.execute("select " + l[0])')
    (tmp_path / "a.kt").write_text(src)
    ln, line = next((i, x) for i, x in enumerate(src.splitlines(), 1) if "jdbc.execute" in x)
    assert not validate_kt_finding(tmp_path, "a.kt", (ln, line.index("jdbc") + 1), (ln, len(line) + 1), {89}).droppable


def test_ktor_route_lambda(tmp_path):
    src = 'fun Application.module() {\n    routing {\n        get("/x") {\n            val n = call.parameters["n"]\n            Runtime.getRuntime().exec("ls " + n)\n        }\n    }\n}\n'
    assert "command-injection" in kinds(run(tmp_path, {"A.kt": src}))
    ok = 'fun Application.module() {\n    routing {\n        get("/x") {\n            Runtime.getRuntime().exec("ls")\n        }\n    }\n}\n'
    assert run(tmp_path / "ok", {"A.kt": ok}) == []
