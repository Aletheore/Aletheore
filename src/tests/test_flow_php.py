"""PHP discover and validate modes: superglobals are followed to sinks across functions and included
files, guards are understood in both directions, and the near misses seen on DVWA are not reported."""

import pytest

from aletheore.flow.discover_php import discover_php, validate_php_finding


def run(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return discover_php(tmp_path)


def kinds(found):
    return sorted({f["rule_id"].split(".")[-1] for f in found})


def page(body):
    return "<?php\n" + body + "\n"


TAINTED = {
    "mysqli query": ("$id = $_GET['id'];\n$q = \"SELECT * FROM t WHERE id = '$id'\";\nmysqli_query($conn, $q);", "sql-injection"),
    "concatenated query": ("mysql_query('SELECT ' . $_POST['x']);", "sql-injection"),
    "pdo query": ("$pdo->query('SELECT ' . $_GET['x']);", "sql-injection"),
    "shell_exec": ("shell_exec('ping ' . $_REQUEST['ip']);", "command-injection"),
    "system": ("system($_GET['cmd']);", "command-injection"),
    "backticks": ("$o = `ls {$_GET['d']}`;", "command-injection"),
    "echo": ("echo '<b>' . $_GET['name'] . '</b>';", "xss"),
    "echo interpolation": ("$n = $_GET['n'];\necho \"Hello $n\";", "xss"),
    "print": ("print $_GET['n'];", "xss"),
    "die": ("die($_GET['msg']);", "xss"),
    "include": ("include($_GET['page']);", "file-inclusion"),
    "unlink": ("unlink($_GET['f']);", "path-traversal"),
    "file read": ("echo file_get_contents($_GET['f']);", "path-traversal"),
    "upload destination": ("move_uploaded_file($_FILES['f']['tmp_name'], 'up/' . $_FILES['f']['name']);", "path-traversal"),
    "eval": ("eval($_POST['code']);", "code-injection"),
    "unserialize": ("unserialize($_COOKIE['d']);", "insecure-deserialization"),
    "redirect": ("header('Location: ' . $_GET['u']);", "open-redirect"),
    "ssrf": ("$ch = curl_init($_GET['u']);", "ssrf"),
    "session": ("$_SESSION['user'] = $_POST['user'];", "trust-boundary"),
    "through an array": ("$a = array('k' => $_GET['v']);\nsystem($a['k']);", "command-injection"),
    "branch": ("$n = 'x';\nif ($_GET['m']) { $n = $_GET['a']; }\nsystem($n);", "command-injection"),
    "foreach": ("foreach ($_GET as $k => $v) { system($v); }", "command-injection"),
}


@pytest.mark.parametrize("name", sorted(TAINTED))
def test_tainted_flows_are_reported(tmp_path, name):
    body, expected = TAINTED[name]
    assert expected in kinds(run(tmp_path, {"a.php": page(body)})), name


SAFE = {
    "constant": "mysqli_query($conn, 'SELECT 1');",
    "intval": "mysqli_query($conn, 'SELECT ' . intval($_GET['id']));",
    "int cast": "mysqli_query($conn, 'SELECT ' . (int)$_GET['id']);",
    "escaped for the sink": "mysqli_query($conn, \"SELECT '\" . mysqli_real_escape_string($conn, $_GET['n']) . \"'\");",
    "html escaped": "echo '<b>' . htmlspecialchars($_GET['name']) . '</b>';",
    "shell escaped": "shell_exec('ping ' . escapeshellarg($_GET['ip']));",
    "basename": "unlink('up/' . basename($_GET['f']));",
    "hash": "unlink('/tmp/' . md5(uniqid() . $_GET['f']));",
    "overwritten": "$n = $_GET['n'];\n$n = 'fixed';\nsystem($n);",
    "files temp name": "$t = $_FILES['f']['tmp_name'];\nunlink($t);",
    "prepared statement": "$s = $pdo->prepare('SELECT * FROM t WHERE a = ?');\n$s->execute(array($_GET['a']));",
    "request in an unrelated variable": "$x = $_GET['x'];\nsystem('ls');",
}


@pytest.mark.parametrize("name", sorted(SAFE))
def test_safe_flows_are_not_reported(tmp_path, name):
    assert run(tmp_path, {"a.php": page(SAFE[name])}) == [], name


def test_the_wrong_escape_for_the_sink_class_still_reports(tmp_path):
    # An HTML escape does not make a value safe in a shell command.
    found = run(tmp_path, {"a.php": page("shell_exec('ping ' . htmlspecialchars($_GET['ip']));")})
    assert kinds(found) == ["command-injection"]


GUARDED_SAFE = {
    "is_numeric on an element": "$o = explode('.', $_GET['ip']);\nif (is_numeric($o[0]) && is_numeric($o[1])) {\n  system('ping ' . $o[0] . '.' . $o[1]);\n}",
    "in_array of constants": "$n = $_GET['n'];\nif (in_array($n, array('a', 'b'))) { system($n); }",
    "equality with a constant": "$n = $_GET['n'];\nif ($n === 'admin') { system($n); }",
    "ctype test": "$n = $_GET['n'];\nif (ctype_alnum($n)) { system($n); }",
    "early exit": "$n = $_GET['n'];\nif (!is_numeric($n)) { die('bad'); }\nsystem('x ' . $n);",
    "case folded equality with a value built before the guard": (
        "$e = $_GET['e'];\n$t = md5(uniqid()) . '.' . $e;\n"
        "if (strtolower($e) == 'jpg' || strtolower($e) == 'png') {\n  unlink($t);\n}"),
    "safe regex": "$n = $_GET['n'];\nif (preg_match('/^[a-z0-9_]+$/', $n)) { system($n); }",
}
GUARDED_TAINTED = {
    "the other branch": "$n = $_GET['n'];\nif (is_numeric($n)) { echo 'ok'; } else { system($n); }",
    "after the if with no exit": "$n = $_GET['n'];\nif (is_numeric($n)) { echo 'ok'; }\nsystem($n);",
    "an unknown collection": "$n = $_GET['n'];\nif (in_array($n, load_allowed())) { system($n); }",
    "a disjunction with an unrelated test": "$n = $_GET['n'];\nif (is_numeric($n) || $flag) { system($n); }",
    "the unvalidated parent of a validated value": (
        "$name = $_GET['f'];\n$ext = substr($name, 1);\n"
        "if (strtolower($ext) == 'jpg') {\n  unlink($name);\n}"),
    "a permissive regex": "$n = $_GET['n'];\nif (preg_match('/^.+$/', $n)) { system($n); }",
    "an early exit that does not exit": "$n = $_GET['n'];\nif (!is_numeric($n)) { echo 'bad'; }\nsystem($n);",
}


@pytest.mark.parametrize("name", sorted(GUARDED_SAFE))
def test_a_value_that_passed_a_guard_is_safe(tmp_path, name):
    assert run(tmp_path, {"a.php": page(GUARDED_SAFE[name])}) == [], name


@pytest.mark.parametrize("name", sorted(GUARDED_TAINTED))
def test_a_value_that_did_not_pass_a_guard_is_still_reported(tmp_path, name):
    assert run(tmp_path, {"a.php": page(GUARDED_TAINTED[name])}) != [], name


def test_a_function_in_another_file_is_followed(tmp_path):
    found = run(tmp_path, {
        "a.php": page("require 'lib.php';\nrunit($_GET['c']);"),
        "lib.php": page("function runit($c) { system($c); }"),
    })
    assert kinds(found) == ["command-injection"] and found[0]["path"] == "lib.php"


def test_a_class_method_in_another_file_is_followed(tmp_path):
    found = run(tmp_path, {
        "a.php": page("$s = new Svc();\n$s->run($_GET['c']);"),
        "svc.php": page("class Svc { public function run($c) { return shell_exec($c); } }"),
    })
    assert kinds(found) == ["command-injection"] and found[0]["path"] == "svc.php"


def test_include_with_a_choice_of_files_links_the_flow(tmp_path):
    # DVWA's shape: a per-level file sets $file and the page includes it.
    found = run(tmp_path, {
        "fi/index.php": page("$level = $_COOKIE['level'];\nswitch ($level) {\n  case 'low': $f = 'low.php'; break;\n  default: $f = 'high.php';\n}\n"
                             "require_once 'fi/source/' . $f;\nif (isset($file)) { include($file); }"),
        "fi/source/low.php": page("$file = $_GET['page'];"),
        "fi/source/high.php": page("$file = 'include.php';"),
    })
    assert "file-inclusion" in kinds(found)


def test_validate_mode_suppresses_a_provably_safe_finding_and_keeps_a_tainted_one(tmp_path):
    src = ("<?php\n$a = $_GET['a'];\nif (!is_numeric($a)) { die('x'); }\nshell_exec('ping ' . $a);\n"
           "$b = $_GET['b'];\nshell_exec('ping ' . $b);\n")
    (tmp_path / "a.php").write_text(src)
    lines = src.splitlines()

    def verdict(line_no):
        text = lines[line_no - 1]
        col = text.index("shell_exec") + 1
        return validate_php_finding(tmp_path, "a.php", (line_no, col), (line_no, len(text)), {78}).kind

    assert verdict(4) in ("safe", "unreachable")
    assert verdict(6) == "tainted"


def test_unwritten_property_of_a_modelled_class_is_not_request_data(tmp_path):
    src = ("<?php\nclass R {\n  private $path = 'php://input';\n  public $type = '';\n"
           "  function __construct(array $config = []) {\n    $config = ['type' => $_SERVER['CONTENT_TYPE'], 'query' => $_GET];\n    $this->init($config);\n  }\n"
           "  function init(array $p = []) {\n    foreach ($p as $name => $value) { $this->{$name} = $value; }\n    $this->read();\n  }\n"
           "  function read() { return file_get_contents($this->path); }\n}\n")
    (tmp_path / "R.php").write_text(src)
    from aletheore.flow.discover_php import discover_php
    assert discover_php(tmp_path) == []


def test_dynamic_property_write_with_request_key_still_flows(tmp_path):
    src = ("<?php\nclass R {\n  private $path = 'x';\n  function go() {\n    $k = $_GET['k'];\n    $this->{$k} = $_GET['v'];\n    return file_get_contents($this->path);\n  }\n}\n")
    (tmp_path / "R.php").write_text(src)
    from aletheore.flow.discover_php import discover_php
    assert any("path-traversal" in f["rule_id"] for f in discover_php(tmp_path))


def test_framework_request_collection_is_a_source(tmp_path):
    src = ("<?php\nclass E {\n  function jsonp($data, $param = 'jsonp') {\n    $cb = $this->request()->query[$param];\n"
           "    echo $cb . '(' . json_encode($data) . ');';\n  }\n}\n")
    (tmp_path / "E.php").write_text(src)
    from aletheore.flow.discover_php import discover_php
    assert any("xss" in f["rule_id"] for f in discover_php(tmp_path))


def test_framework_response_write_is_an_xss_sink(tmp_path):
    src = "<?php\nfunction h($app) {\n  $cb = $_GET['cb'];\n  $app->response()->write($cb . '(1);');\n}\n"
    (tmp_path / "a.php").write_text(src)
    from aletheore.flow.discover_php import discover_php
    assert any("xss" in f["rule_id"] for f in discover_php(tmp_path))
    ok = "<?php\nfunction h($app) {\n  $app->response()->write(htmlspecialchars($_GET['cb']));\n}\n"
    (tmp_path / "a.php").write_text(ok)
    assert discover_php(tmp_path) == []


def test_compound_early_exit_guard_proves_the_value_safe(tmp_path):
    src = ("<?php\nfunction h($req) {\n  $cb = (string) $_GET['cb'];\n"
           "  if ($cb !== '' && !preg_match('/^[A-Za-z_$][\\w$.]{0,127}$/', $cb)) {\n    throw new Exception('bad');\n  }\n"
           "  $req->response()->write($cb . '(1);');\n}\n")
    (tmp_path / "a.php").write_text(src)
    from aletheore.flow.discover_php import discover_php
    assert discover_php(tmp_path) == []


def test_dot_in_a_validating_class_is_not_enough_for_paths(tmp_path):
    src = ("<?php\nfunction h() {\n  $f = $_GET['f'];\n  if (!preg_match('/^[\\w.]+$/', $f)) { die('x'); }\n  return file_get_contents('/d/' . $f);\n}\n")
    (tmp_path / "a.php").write_text(src)
    from aletheore.flow.discover_php import discover_php
    assert any("path-traversal" in f["rule_id"] for f in discover_php(tmp_path))
