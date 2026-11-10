"""An object field written in both branches of if/else holds either value afterwards: the `else` branch must not
overwrite what the `if` branch wrote (found on mutillidae's CustomErrorHandler)."""
from pathlib import Path

from aletheore.flow.discover import discover_repo

PHP = """<?php
class Enc { public function encodeForHTML($s) { return htmlspecialchars($s); } }
class Handler {
    private $encodeOutput = false;
    private $mDiag = "";
    public function __construct($level) { $this->mEncoder = new Enc(); if ($level == "5") { $this->encodeOutput = true; } }
    private function setProps($d) {
        if (!$this->encodeOutput) {
            $this->mDiag = $d;
        } else {
            $this->mDiag = $this->mEncoder->encodeForHTML($d);
        }
    }
    public function format($d) { $this->setProps($d); return "<td>" . $this->mDiag . "</td>"; }
}
$h = new Handler($_GET["lvl"]);
echo $h->format("page " . $_GET["page"]);
"""
PY = """from flask import Flask, request
from markupsafe import escape
app = Flask(__name__)

class H:
    def __init__(self, enc):
        self.enc = enc
        self.m = ""
    def set(self, d):
        if not self.enc:
            self.m = d
        else:
            self.m = escape(d)

@app.route("/x")
def x():
    h = H(request.args.get("lvl") == "5")
    h.set(request.args.get("q"))
    return "<p>" + h.m + "</p>"
"""


def test_php_else_branch_field_write_joins(tmp_path: Path):
    (tmp_path / "a.php").write_text(PHP)
    assert any(f["rule_id"] == "flow.php.xss" and f["line"] == 17 for f in discover_repo(tmp_path))


def test_python_else_branch_attribute_write_joins(tmp_path: Path):
    (tmp_path / "app.py").write_text(PY)
    assert any(f["rule_id"] == "flow.python.xss" for f in discover_repo(tmp_path))


def test_both_branches_encoded_is_quiet(tmp_path: Path):
    (tmp_path / "app.py").write_text(PY.replace("            self.m = d\n", "            self.m = escape(d)\n"))
    assert not any(f["rule_id"] == "flow.python.xss" for f in discover_repo(tmp_path))
