"""Second-order flows: one handler stores request data in the database, another reads it back into a sink."""
from pathlib import Path

from aletheore.flow.discover import discover_repo

HEAD = "import os\nfrom flask import Flask, request\nfrom django.http import HttpResponse\napp = Flask(__name__)\nclass User:\n    pass\n"


def _found(tmp_path: Path, body: str):
    (tmp_path / "a.py").write_text(HEAD + body)
    return sorted((f["line"], f["rule_id"].split(".")[-1]) for f in discover_repo(tmp_path))


W = "@app.route('/w')\ndef w():\n    User.objects.create(name=request.args['n'])\n    return 'ok'\n"


def test_stored_value_read_back_into_a_command(tmp_path):
    body = W + "@app.route('/r')\ndef r():\n    u = User.objects.first()\n    os.system('echo ' + u.name)\n"
    assert (14, "command-injection") in _found(tmp_path, body)


def test_stored_value_read_back_into_html(tmp_path):
    body = W + "@app.route('/r')\ndef r():\n    u = User.objects.get(id=1)\n    return HttpResponse('<b>' + u.name + '</b>')\n"
    assert any(r == "xss" for _, r in _found(tmp_path, body))


def test_nothing_stored_means_nothing_read_back(tmp_path):
    body = "@app.route('/r')\ndef r():\n    u = User.objects.first()\n    os.system('echo ' + u.name)\n"
    assert _found(tmp_path, body) == []


def test_constant_write_is_not_tainted_storage(tmp_path):
    body = "@app.route('/w')\ndef w():\n    User.objects.create(name='x')\n@app.route('/r')\ndef r():\n    u = User.objects.first()\n    os.system('echo ' + u.name)\n"
    assert _found(tmp_path, body) == []


def test_other_model_is_not_read_back(tmp_path):
    body = W + "class Post:\n    pass\n@app.route('/r')\ndef r():\n    p = Post.objects.first()\n    os.system('echo ' + p.title)\n"
    assert _found(tmp_path, body) == []


def test_model_instance_save_and_raw_sql(tmp_path):
    body = ("@app.route('/w')\ndef w():\n    u = User(name=request.args['n'])\n    u.save()\n"
            "@app.route('/r')\ndef r():\n    u = User.objects.first()\n    os.system('echo ' + u.name)\n")
    assert any(r == "command-injection" for _, r in _found(tmp_path, body))
    sql = ("import sqlite3\nc = sqlite3.connect('x').cursor()\n@app.route('/w')\ndef w():\n    c.execute('INSERT INTO t VALUES (?)', (request.args['n'],))\n"
           "@app.route('/r')\ndef r():\n    row = c.fetchone()\n    os.system('echo ' + row[0])\n")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "a.py").write_text(HEAD + sql)
    assert any(r == "command-injection" for _, r in sorted((f["line"], f["rule_id"].split(".")[-1]) for f in discover_repo(tmp_path / "b")))


def test_stored_finding_says_where_it_came_from(tmp_path):
    body = W + "@app.route('/r')\ndef r():\n    u = User.objects.first()\n    os.system('echo ' + u.name)\n"
    (tmp_path / "a.py").write_text(HEAD + body)
    msgs = [f["message"] for f in discover_repo(tmp_path) if f["line"] == 14]
    assert msgs and "stored earlier" in msgs[0]


JS_HEAD = "const express=require('express');const {exec}=require('child_process');const User=require('./m');const app=express();\n"
JS_W = "app.post('/w',async (req,res)=>{ await User.create({name:req.body.n}); res.send('ok'); });\n"


def _js(tmp_path: Path, body: str):
    (tmp_path / "a.js").write_text(JS_HEAD + body)
    return sorted((f["line"], f["rule_id"].split(".")[-1]) for f in discover_repo(tmp_path))


def test_js_stored_value_read_back_into_a_command(tmp_path):
    body = JS_W + "app.get('/r',async (req,res)=>{ const u = await User.findOne({}); exec('echo '+u.name); });\n"
    assert (3, "command-injection") in _js(tmp_path, body)


def test_js_stored_value_read_back_into_html(tmp_path):
    body = JS_W + "app.get('/r',async (req,res)=>{ const u = await User.findById(1); res.send('<b>'+u.name+'</b>'); });\n"
    assert any(r == "xss" for _, r in _js(tmp_path, body))


def test_js_nothing_stored_or_constant_write_stays_quiet(tmp_path):
    read = "app.get('/r',async (req,res)=>{ const u = await User.findOne({}); exec('echo '+u.name); });\n"
    assert _js(tmp_path, read) == []
    (tmp_path / "c").mkdir()
    const = "app.post('/w',async (req,res)=>{ await User.create({name:'x'}); });\n" + read
    (tmp_path / "c" / "a.js").write_text(JS_HEAD + const)
    assert discover_repo(tmp_path / "c") == []
