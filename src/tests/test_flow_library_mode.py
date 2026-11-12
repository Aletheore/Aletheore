"""Library mode: in a package that others import (a package.json and no web framework among its dependencies), the parameters of
exported functions are caller-controlled, so a sink they reach is reported. A web application is analysed through its routes alone."""
import json
from pathlib import Path

from aletheore.flow.discover import discover_repo


def _scan(tmp_path: Path, src: str, deps: dict | None = None, name: str = "index.js", extra: dict | None = None) -> list[tuple[str, int]]:
    (tmp_path / "package.json").write_text(json.dumps({"name": "pkg", "main": name, "dependencies": deps or {}, **(extra or {})}))
    (tmp_path / name).write_text(src)
    return sorted((f["rule_id"].split(".")[-1], f["line"]) for f in discover_repo(tmp_path))


EXEC = "const { exec } = require('child_process');\n"


def test_module_exports_function(tmp_path):
    assert _scan(tmp_path, EXEC + "module.exports = function (a) { exec('echo ' + a); };\n") == [("command-injection", 2)]


def test_exports_property_and_object(tmp_path):
    assert _scan(tmp_path, EXEC + "exports.run = function (a) { exec('echo ' + a); };\n") == [("command-injection", 2)]
    src = EXEC + "function run(a) { exec('echo ' + a); }\nmodule.exports = { run };\n"
    assert _scan(tmp_path, src) == [("command-injection", 2)]


def test_esm_exports(tmp_path):
    assert _scan(tmp_path, "import { exec } from 'child_process';\nexport function run(a) { exec('echo ' + a); }\n") == [("command-injection", 2)]
    assert _scan(tmp_path, "import fs from 'fs';\nexport default function (p) { return fs.readFileSync(p); }\n") == [("path-traversal", 2)]


def test_a_function_that_is_not_exported_is_not_an_entry(tmp_path):
    assert _scan(tmp_path, EXEC + "function run(a) { exec('echo ' + a); }\nmodule.exports = {};\n") == []


def test_constant_command_and_quoted_argument_are_quiet(tmp_path):
    assert _scan(tmp_path, EXEC + "module.exports = function (a) { exec('echo hi'); };\n") == []
    src = EXEC + "const { quote } = require('shell-quote');\nmodule.exports = function (a) { exec('echo ' + quote([a])); };\n"
    assert _scan(tmp_path, src) == []


def test_a_web_application_is_not_a_library(tmp_path):
    assert _scan(tmp_path, EXEC + "module.exports = function (a) { exec('echo ' + a); };\n", deps={"express": "4"}) == []


def test_without_a_package_json_nothing_changes(tmp_path):
    (tmp_path / "index.js").write_text(EXEC + "module.exports = function (a) { exec('echo ' + a); };\n")
    assert discover_repo(tmp_path) == []


def test_a_private_package_is_an_application(tmp_path):
    assert _scan(tmp_path, EXEC + "module.exports = function (a) { exec('echo ' + a); };\n", extra={"private": True}) == []


def test_a_package_that_exposes_nothing_is_not_a_library(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "pkg"}))
    (tmp_path / "index.js").write_text(EXEC + "module.exports = function (a) { exec('echo ' + a); };\n")
    assert discover_repo(tmp_path) == []


def test_javascript_inside_a_ruby_or_go_application_is_not_a_library(tmp_path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
    assert _scan(tmp_path, EXEC + "module.exports = function (a) { exec('echo ' + a); };\n") == []


def test_library_findings_are_minor_severity(tmp_path):
    _scan(tmp_path, EXEC + "module.exports = function (a) { exec('echo ' + a); };\n")
    assert {f["severity"] for f in discover_repo(tmp_path)} == {"minor"}
