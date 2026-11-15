"""PHP `global $x` and `$GLOBALS['x']` link a function to the script's top-level variables."""
import pytest

from aletheore.flow.discover import discover_repo

READ = "<?php\n$q = {v};\nfunction show() {{\n    global $q;\n    echo $q;\n}}\nshow();\n"
WRITE = "<?php\nfunction load() {{\n    global $name;\n    $name = {v};\n}}\nfunction out() {{\n    global $name;\n    echo $name;\n}}\nload();\nout();\n"
ARRAY = "<?php\nfunction out() {{\n    echo $GLOBALS['x'];\n}}\n$GLOBALS['x'] = {v};\nout();\n"
ESCAPED = "<?php\nfunction load() {\n    global $name;\n    $name = $_GET['n'];\n}\nfunction out() {\n    global $name;\n    echo htmlspecialchars($name);\n}\nload();\nout();\n"


@pytest.mark.parametrize("tpl", [READ, WRITE, ARRAY])
def test_request_value_reaches_the_sink(tmp_path, tpl):
    (tmp_path / "x.php").write_text(tpl.format(v="$_GET['q']"))
    assert len(discover_repo(tmp_path)) == 1


@pytest.mark.parametrize("tpl", [READ, WRITE, ARRAY])
def test_constant_value_stays_quiet(tmp_path, tpl):
    (tmp_path / "x.php").write_text(tpl.format(v="'safe'"))
    assert discover_repo(tmp_path) == []


def test_escaped_output_stays_quiet(tmp_path):
    (tmp_path / "x.php").write_text(ESCAPED)
    assert discover_repo(tmp_path) == []
