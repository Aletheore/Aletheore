"""Values that are provably harmless by construction (a parsed UUID, an enum constant, a file name, an allowed redirect host) stay quiet; the neighbouring unsafe forms report."""
import pytest

from aletheore.flow.discover import discover_repo

JH = "import org.springframework.web.bind.annotation.*;\nimport java.util.*;\nimport java.nio.file.*;\nimport java.sql.*;\n"


def _java(tmp_path, body, extra=""):
    (tmp_path / "App.java").write_text(JH + "@RestController public class App {\n" + extra + "  Connection c;\n  @GetMapping(\"/r\") public String r(@RequestParam String q) throws Exception {\n" + body + "\n return \"ok\"; }\n}\n")
    return len(discover_repo(tmp_path))


def test_java_uuid_and_enum_are_harmless(tmp_path):
    assert _java(tmp_path, "UUID u=UUID.fromString(q); c.createStatement().executeQuery(\"select * from t where id = '\"+u+\"'\");") == 0


def test_java_enum_value_of_is_harmless(tmp_path):
    assert _java(tmp_path, "Col k=Col.valueOf(q); c.createStatement().executeQuery(\"select * from t order by \"+k.name());", "  enum Col { NAME, AGE }\n") == 0


def test_java_plain_string_still_reports(tmp_path):
    assert _java(tmp_path, "c.createStatement().executeQuery(\"select * from t where id = '\"+q+\"'\");") == 1


def test_java_file_name_component_is_safe_for_paths_only(tmp_path):
    assert _java(tmp_path, "new String(Files.readAllBytes(Paths.get(\"/srv/files\", Paths.get(q).getFileName().toString())));") == 0
    assert _java(tmp_path, "c.createStatement().executeQuery(\"select * from t where n = '\"+Paths.get(q).getFileName()+\"'\");") == 1


PY = "import os, sqlite3, uuid\nfrom flask import Flask, request, abort, redirect\nfrom urllib.parse import urlparse\napp = Flask(__name__)\ndb = sqlite3.connect('x')\n@app.route('/r')\ndef r():\n{body}    return 'ok'\n"


def _py(tmp_path, body):
    (tmp_path / "app.py").write_text(PY.format(body=body))
    return len(discover_repo(tmp_path))


def test_python_uuid_is_harmless(tmp_path):
    assert _py(tmp_path, "    u = uuid.UUID(request.args['q'])\n    db.execute('select * from t where id = \\'' + str(u) + '\\'')\n") == 0


def test_allowed_host_check_settles_a_redirect(tmp_path):
    assert _py(tmp_path, "    u = request.args['next']\n    if urlparse(u).netloc not in ('example.com',):\n        abort(400)\n    return redirect(u)\n") == 0


def test_host_suffix_check_does_not_settle_a_redirect(tmp_path):
    assert _py(tmp_path, "    u = request.args['next']\n    if urlparse(u).netloc.endswith('example.com'):\n        return redirect(u)\n") >= 1
