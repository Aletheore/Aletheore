"""Python refine mode: a Semgrep finding is dropped only when the sink value is provably safe."""

from aletheore.flow.discover_py import validate_python_finding


def verdict(tmp_path, src, needle, cwes):
    (tmp_path / "a.py").write_text(src)
    for i, l in enumerate(src.splitlines(), 1):
        if needle in l:
            return validate_python_finding(tmp_path, "a.py", (i, l.index(needle) + 1), (i, len(l) + 1), cwes)
    raise AssertionError("needle not found")


def test_constant_query_is_dropped(tmp_path):
    src = 'import sqlite3\nTABLE = "users"\n\ndef f(cur):\n    cur.execute("select * from " + TABLE)\n'
    assert verdict(tmp_path, src, "cur.execute", {89}).droppable


def test_request_value_is_kept(tmp_path):
    src = 'from flask import request\n\ndef f(cur):\n    cur.execute("select * from t where n=" + request.args["n"])\n'
    assert not verdict(tmp_path, src, "cur.execute", {89}).droppable


def test_route_param_is_kept(tmp_path):
    src = '@app.route("/x/<n>")\ndef f(n):\n    cur.execute("select " + n)\n'
    assert not verdict(tmp_path, src, "cur.execute", {89}).droppable


def test_int_cast_is_dropped(tmp_path):
    src = 'from flask import request\n\ndef f(cur):\n    n = int(request.args["n"])\n    cur.execute("select " + str(n))\n'
    assert verdict(tmp_path, src, "cur.execute", {89}).droppable


def test_unknown_argument_is_kept(tmp_path):
    src = 'def f(cur, q):\n    cur.execute(q)\n'
    assert not verdict(tmp_path, src, "cur.execute", {89}).droppable


def test_dead_branch_is_dropped(tmp_path):
    src = 'from flask import request\n\ndef f(cur):\n    if False:\n        cur.execute("x" + request.args["n"])\n'
    assert verdict(tmp_path, src, "cur.execute", {89}).droppable


def test_wide_taint_range_is_not_judged_by_a_harmless_call(tmp_path):
    # Semgrep taint findings span from the source line to the sink line. The first call in that range
    # (request.form.get) is not a sink; the finding must be judged by the real sink inside it.
    src = ('from flask import request\n\n@app.route("/l")\ndef lab():\n'
           '    u = request.form.get("u", "")\n    q = f"select * from t where n=\'{u}\'"\n    r = c.execute(q).fetchone()\n    return str(r)\n')
    (tmp_path / "a.py").write_text(src)
    from aletheore.flow.discover_py import validate_python_finding
    assert not validate_python_finding(tmp_path, "a.py", (5, 5), (8, 21), {89}).droppable


def test_wide_range_with_no_sink_is_kept(tmp_path):
    src = 'def f(x):\n    a = len(x)\n    b = str(a)\n    return b\n'
    (tmp_path / "a.py").write_text(src)
    from aletheore.flow.discover_py import validate_python_finding
    assert not validate_python_finding(tmp_path, "a.py", (2, 5), (4, 13), {89}).droppable
