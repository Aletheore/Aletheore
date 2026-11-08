"""JavaScript refine mode: a Semgrep finding is dropped only when the sink value is provably safe."""

from aletheore.flow.discover_js import validate_javascript_finding


def verdict(tmp_path, src, needle, cwes, name="a.js"):
    (tmp_path / name).write_text(src)
    for i, l in enumerate(src.splitlines(), 1):
        if needle in l:
            return validate_javascript_finding(tmp_path, name, (i, l.index(needle) + 1), (i, len(l) + 1), cwes)
    raise AssertionError("needle not found")


def test_constant_query_is_dropped(tmp_path):
    src = 'const T = "users";\nfunction f(db) {\n  db.query("select * from " + T);\n}\n'
    assert verdict(tmp_path, src, "db.query", {89}).droppable


def test_request_value_is_kept(tmp_path):
    src = 'app.get("/x", (req, res) => {\n  db.query("select " + req.query.n);\n});\n'
    assert not verdict(tmp_path, src, "db.query", {89}).droppable


def test_nested_callback_is_judged_from_the_handler(tmp_path):
    src = 'app.get("/x", (req, res) => {\n  const n = req.query.n;\n  fs.readFile("a", (err, d) => {\n    db.query("select " + n);\n  });\n});\n'
    assert not verdict(tmp_path, src, "db.query", {89}).droppable


def test_parseint_is_dropped(tmp_path):
    src = 'app.get("/x", (req, res) => {\n  const n = parseInt(req.query.n, 10);\n  db.query("select " + n);\n});\n'
    assert verdict(tmp_path, src, "db.query", {89}).droppable


def test_unknown_argument_is_kept(tmp_path):
    src = 'function f(db, q) {\n  db.query(q);\n}\n'
    assert not verdict(tmp_path, src, "db.query", {89}).droppable


def test_wide_range_is_not_judged_by_a_harmless_call(tmp_path):
    src = 'app.get("/x", (req, res) => {\n  const n = String(req.query.n);\n  const q = "select " + n;\n  db.query(q);\n});\n'
    (tmp_path / "a.js").write_text(src)
    assert not validate_javascript_finding(tmp_path, "a.js", (2, 3), (4, 14), {89}).droppable


def test_typescript_file(tmp_path):
    src = 'app.get("/x", (req: Request, res: Response) => {\n  db.query("select " + req.query.n);\n});\n'
    assert not verdict(tmp_path, src, "db.query", {89}, name="a.ts").droppable
