"""Python discover mode: flows are followed across functions and modules, and the near misses that
produced false positives on real projects (framework response builders, whitelist filters, str.replace
versus Path.replace, send_from_directory) are not reported."""

import pytest

from aletheore.flow.discover_py import discover_python


def run(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return discover_python(tmp_path)


def kinds(found):
    return sorted({f["rule_id"].split(".")[-1] for f in found})


ROUTE = "from flask import Flask, request\napp = Flask(__name__)\n@app.route('/x')\ndef view():\n"


def test_sql_injection_through_a_function_in_another_module(tmp_path):
    found = run(tmp_path, {
        "views.py": ROUTE + "    import dao\n    name = request.form.get('n')\n    return dao.find(name)\n",
        "dao.py": "def find(name):\n    c = conn.cursor()\n    c.execute(\"SELECT * FROM t WHERE n = '{}'\".format(name))\n",
    })
    assert kinds(found) == ["sql-injection"] and found[0]["path"] == "dao.py"


def test_a_module_in_the_same_directory_wins_over_a_copy_elsewhere(tmp_path):
    # vulpy ships bad/ and good/ copies of every module; imports must resolve to the sibling.
    bad = {"bad/mod.py": ROUTE + "    import lib\n    lib.q(request.args.get('a'))\n",
           "bad/lib.py": "def q(a):\n    cur.execute('select ' + a)\n",
           "good/mod.py": ROUTE + "    import lib\n    lib.q(request.args.get('a'))\n",
           "good/lib.py": "def q(a):\n    cur.execute('select ?', (a,))\n"}
    found = run(tmp_path, bad)
    assert [f["path"] for f in found] == ["bad/lib.py"]


@pytest.mark.parametrize("name, body", {
    "constant query": "    c.execute('select 1')\n",
    "parameterized": "    c.execute('select * from t where a = ?', (request.args.get('a'),))\n",
    "number first": "    c.execute('select ' + str(int(request.args.get('a'))))\n",
    "whitelist join": "    n = ''.join(ch for ch in request.args.get('a') if ch.isalnum() or ch in '-_')\n    c.execute('select ' + n)\n",
    "re.sub whitelist": "    import re\n    n = re.sub(r'[^a-zA-Z0-9_-]', '', request.args.get('a'))\n    c.execute('select ' + n)\n",
    "overwritten": "    n = request.args.get('a')\n    n = 'fixed'\n    c.execute('select ' + n)\n",
    "dead branch": "    n = request.args.get('a') if 1 > 2 else 'safe'\n    c.execute('select ' + n)\n",
    "dict value read of a safe key": "    d = {'a': 'safe', 'b': request.args.get('b')}\n    c.execute('select ' + d['a'])\n",
}.items())
def test_safe_flows_are_not_reported(tmp_path, name, body):
    assert run(tmp_path, {"v.py": ROUTE + body}) == [], name


TAINTED_CASES = {
    "f-string": ("    n = request.args.get('a')\n    c.execute(f\"select {n}\")\n", "sql-injection"),
    "percent format": ("    c.execute('select %s' % request.args['a'])\n", "sql-injection"),
    "list element": ("    parts = ['a']\n    parts.append(request.args.get('b'))\n    c.execute(' '.join(parts))\n", "sql-injection"),
    "dict value": ("    d = {'k': request.args.get('b')}\n    c.execute('select ' + d['k'])\n", "sql-injection"),
    "tainted branch": ("    n = 'x'\n    if request.args.get('m'):\n        n = request.args.get('a')\n    c.execute('select ' + n)\n", "sql-injection"),
    "tuple unpack": ("    a, b = 'x', request.args.get('a')\n    c.execute('select ' + b)\n", "sql-injection"),
    "os.system": ("    import os\n    os.system('ls ' + request.args.get('d'))\n", "command-injection"),
    "subprocess shell": ("    import subprocess\n    subprocess.run('ls ' + request.args.get('d'), shell=True)\n", "command-injection"),
    "open": ("    open(request.args.get('f'))\n", "path-traversal"),
    "eval": ("    eval(request.args.get('e'))\n", "code-injection"),
    "pickle": ("    import pickle\n    pickle.loads(request.data)\n", "insecure-deserialization"),
    "ssrf": ("    import requests\n    requests.get(request.args.get('u'))\n", "ssrf"),
    "redirect": ("    from flask import redirect\n    return redirect(request.args.get('next'))\n", "open-redirect"),
}


@pytest.mark.parametrize("name, body, expected", [(k, v[0], v[1]) for k, v in TAINTED_CASES.items()])
def test_tainted_flows_are_reported(tmp_path, name, body, expected):
    found = run(tmp_path, {"v.py": ROUTE + body})
    assert expected in kinds(found), (name, found)


def test_route_parameters_are_request_data(tmp_path):
    src = "from flask import Flask\napp = Flask(__name__)\n@app.route('/u/<name>')\ndef u(name):\n    cur.execute('select ' + name)\n"
    assert kinds(run(tmp_path, {"v.py": src})) == ["sql-injection"]


def test_a_plain_function_parameter_is_not_assumed_tainted(tmp_path):
    assert run(tmp_path, {"v.py": "def f(name):\n    cur.execute('select ' + name)\n"}) == []


def test_a_tainted_string_returned_from_a_route_is_xss_but_a_response_object_is_not(tmp_path):
    xss = ROUTE + "    return '<b>' + request.args.get('n') + '</b>'\n"
    ok = ROUTE + "    from flask import jsonify, render_template\n    n = request.args.get('n')\n    return jsonify(n) if n else render_template('x.html', n=n)\n"
    assert kinds(run(tmp_path / "a", {"v.py": xss})) == ["xss"]
    assert run(tmp_path / "b", {"v.py": ok}) == []


def test_framework_response_builders_and_safe_file_serving_are_not_sinks(tmp_path):
    body = ("    from flask import make_response, render_template, redirect, send_from_directory\n"
            "    n = request.args.get('n')\n"
            "    r = make_response(render_template('a.html', n=n))\n"
            "    r2 = make_response(redirect('/home'))\n"
            "    return send_from_directory('static', n)\n")
    assert run(tmp_path, {"v.py": ROUTE + body}) == []


def test_string_replace_is_not_mistaken_for_path_replace_but_a_real_path_is_a_sink(tmp_path):
    assert run(tmp_path / "a", {"v.py": ROUTE + "    n = request.args.get('n').replace('<', '')\n    return 'ok'\n"}) == []
    real = ROUTE + "    from pathlib import Path\n    Path(request.args.get('f')).read_text()\n"
    assert kinds(run(tmp_path / "b", {"v.py": real})) == ["path-traversal"]
    joined = ROUTE + "    from pathlib import Path\n    (Path('/srv') / request.args.get('f')).unlink()\n"
    assert kinds(run(tmp_path / "c", {"v.py": joined})) == ["path-traversal"]


def test_session_write_needs_a_tainted_string(tmp_path):
    body = "    from flask import session\n    session['u'] = request.form.get('u')\n"
    assert kinds(run(tmp_path / "a", {"v.py": ROUTE + body})) == ["trust-boundary"]
    safe = "    from flask import session\n    session['u'] = helper(request.form.get('u'))\n"
    assert run(tmp_path / "b", {"v.py": ROUTE + safe}) == []


def test_import_from_resolves_across_files_and_class_methods_are_followed(tmp_path):
    found = run(tmp_path, {
        "app.py": ROUTE + "    from svc import Service\n    Service().run(request.args.get('c'))\n",
        "svc.py": "import os\nclass Service:\n    def run(self, c):\n        os.system(c)\n",
    })
    assert kinds(found) == ["command-injection"] and found[0]["path"] == "svc.py"


GUARDED_SAFE = {
    "membership in a constant collection": "    n = request.args.get('n')\n    if n in ('a', 'b'):\n        cur.execute('select ' + n)\n",
    "membership in a constant dict": "    n = request.args.get('n')\n    if n in USERS:\n        cur.execute('select ' + n)\n",
    "equality with a constant": "    n = request.args.get('n')\n    if n == 'admin':\n        cur.execute('select ' + n)\n",
    "character-class test": "    n = request.args.get('n')\n    if n.isalnum():\n        cur.execute('select ' + n)\n",
    "early exit on a failed test": "    n = request.args.get('n')\n    if not n.isalnum():\n        return 'bad'\n    cur.execute('select ' + n)\n",
    "early exit on not in": "    n = request.args.get('n')\n    if n not in ('a', 'b'):\n        raise ValueError(n)\n    cur.execute('select ' + n)\n",
    "conjunction": "    n = request.args.get('n')\n    if n and n.isdigit():\n        cur.execute('select ' + n)\n",
}
GUARDED_TAINTED = {
    "other branch is not guarded": "    n = request.args.get('n')\n    if n.isalnum():\n        pass\n    else:\n        cur.execute('select ' + n)\n",
    "after the if, without an early exit": "    n = request.args.get('n')\n    if n.isalnum():\n        pass\n    cur.execute('select ' + n)\n",
    "membership in an unknown collection": "    n = request.args.get('n')\n    if n in lookup():\n        cur.execute('select ' + n)\n",
    "disjunction guards only what both sides guard": "    n = request.args.get('n')\n    if n.isalnum() or flag:\n        cur.execute('select ' + n)\n",
    "inequality is no guard in the true branch": "    n = request.args.get('n')\n    if n != 'x':\n        cur.execute('select ' + n)\n",
    "early exit that does not exit": "    n = request.args.get('n')\n    if not n.isalnum():\n        log(n)\n    cur.execute('select ' + n)\n",
}


@pytest.mark.parametrize("name", sorted(GUARDED_SAFE))
def test_a_value_that_passed_a_guard_is_safe(tmp_path, name):
    src = "USERS = {'a': 1}\n" + ROUTE + GUARDED_SAFE[name]
    assert run(tmp_path, {"v.py": src}) == [], name


@pytest.mark.parametrize("name", sorted(GUARDED_TAINTED))
def test_a_value_that_did_not_pass_a_guard_is_still_reported(tmp_path, name):
    src = ROUTE + GUARDED_TAINTED[name]
    assert kinds(run(tmp_path, {"v.py": src})) == ["sql-injection"], name
