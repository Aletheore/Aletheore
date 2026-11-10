"""Regressions for Codex pass-3 defects D-30 to D-32 and the D-4 visibility follow-up."""
from pathlib import Path

from aletheore.flow.discover import discover_repo
from aletheore.static_analysis import _drop_duplicate_flow_findings


def _write(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return tmp_path


def test_d30_safe_mapper_sharing_a_method_name_is_quiet(tmp_path):
    _write(tmp_path, {
        "VMapper.java": 'import org.apache.ibatis.annotations.*;\ninterface VMapper {\n  @Select("select * from u where n = \'${name}\'")\n  Object find(@Param("name") String name);\n}\n',
        "SMapper.java": 'import org.apache.ibatis.annotations.*;\ninterface SMapper {\n  @Select("select * from u where n = #{name}")\n  Object find(@Param("name") String name);\n}\n',
        "Handler.java": ('import javax.servlet.http.HttpServletRequest;\nclass Handler {\n  VMapper vMapper;\n  SMapper sMapper;\n'
                         '  Object a(HttpServletRequest r) { return vMapper.find(r.getParameter("n")); }\n'
                         '  Object b(HttpServletRequest r) { return sMapper.find(r.getParameter("n")); }\n}\n'),
    })
    assert sorted(f["line"] for f in discover_repo(tmp_path) if f["rule_id"].endswith("sql-injection")) == [5]


ZIP = """from pathlib import Path
from zipfile import ZipFile
def extract(archive, destination):
    root = Path(destination).resolve()
    with ZipFile(archive) as z:
        for entry in z.infolist():
            target = (root / entry.filename).resolve()
%s            with target.open("wb") as output:
                output.write(z.read(entry))
"""


def _zip(tmp_path, guard):
    _write(tmp_path, {"x.py": ZIP % guard})
    return [f for f in discover_repo(tmp_path) if f["rule_id"].endswith("path-traversal")]


def test_d31_resolved_path_without_a_check_is_reported(tmp_path):
    assert _zip(tmp_path, "")


def test_d31_prefix_check_without_separator_is_reported(tmp_path):
    assert _zip(tmp_path, '            if not str(target).startswith(str(root)):\n                raise ValueError("x")\n')


def test_d31_is_relative_to_is_a_containment_check(tmp_path):
    assert not _zip(tmp_path, '            if not target.is_relative_to(root):\n                raise ValueError("x")\n')


def test_d32_format_error_in_a_constructor_costs_one_class(tmp_path):
    _write(tmp_path, {"app.py": (
        "import os\nfrom flask import Flask, request\napp = Flask(__name__)\n\n"
        "class Widget:\n    def __init__(self):\n        self.label = '{:.2f}'.format(None)\n    def show(self):\n        return self.label\n\n"
        "@app.route('/x')\ndef x():\n    os.system(request.args.get('c'))\n    return 'ok'\n")})
    assert any(f["rule_id"] == "flow.python.command-injection" for f in discover_repo(tmp_path))


def test_d4_kept_duplicate_says_flow_also_found_it():
    bandit = {"tool": "bandit", "path": "a.py", "line": 4, "cwe": ["CWE-78"]}
    flow = {"tool": "aletheore-flow", "path": "a.py", "line": 4, "cwe": ["CWE-78"]}
    kept = _drop_duplicate_flow_findings([bandit, flow])
    assert kept == [bandit] and bandit["also_found_by"] == ["aletheore-flow"]


def test_d33_commonpath_on_str_paths_is_a_containment_check(tmp_path):
    guard = '            if os.path.commonpath([str(root), str(target)]) != str(root):\n                raise ValueError("x")\n'
    _write(tmp_path, {"x.py": "import os\n" + ZIP % guard})
    assert not [f for f in discover_repo(tmp_path) if f["rule_id"].endswith("path-traversal")]


def test_d33_commonprefix_is_character_based_and_still_reported(tmp_path):
    guard = '            if os.path.commonprefix([str(root), str(target)]) != str(root):\n                raise ValueError("x")\n'
    _write(tmp_path, {"x.py": "import os\n" + ZIP % guard})
    assert [f for f in discover_repo(tmp_path) if f["rule_id"].endswith("path-traversal")]


def test_d34_mybatis_dollar_in_an_included_sql_fragment(tmp_path):
    _write(tmp_path, {
        "UserMapper.java": "interface UserMapper {\n  Object search(String sort);\n}\n",
        "UserMapper.xml": ('<mapper namespace="UserMapper">\n  <sql id="orderFragment"> ORDER BY ${sort} </sql>\n'
                           '  <select id="search">SELECT * FROM users <include refid="orderFragment"/></select>\n</mapper>\n'),
        "Handler.java": ('import javax.servlet.http.HttpServletRequest;\nclass Handler {\n  UserMapper userMapper;\n'
                         '  Object a(HttpServletRequest r) { return userMapper.search(r.getParameter("s")); }\n}\n'),
    })
    assert [f["line"] for f in discover_repo(tmp_path) if f["rule_id"].endswith("sql-injection")] == [4]
