"""Library mode for every language: a package others import has no request, so its public functions are the entry points and
their string parameters are whatever the caller passes. Findings are `minor`. Applications (web dependencies, executables),
private functions and test directories stay quiet."""
from pathlib import Path

import pytest

from aletheore.flow.discover import discover_repo


def _run(tmp_path: Path, files: dict[str, str]):
    for k, v in files.items():
        (tmp_path / k).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / k).write_text(v)
    return [(f["path"], f["line"], f["severity"]) for f in discover_repo(tmp_path) if f["rule_id"].endswith("command-injection")]


PY = "import os\ndef run(cmd):\n    os.system('echo ' + cmd)\n"
GO = 'package lib\n\nimport "os/exec"\n\nfunc Run(cmd string) {\n\texec.Command("sh", "-c", cmd).Run()\n}\n'
JAVA = "package p;\npublic class A {\n  public void run(String cmd) throws Exception {\n    Runtime.getRuntime().exec(new String[]{\"sh\",\"-c\",cmd});\n  }\n}\n"
KT = "package p\nclass A {\n  fun run(cmd: String) {\n    Runtime.getRuntime().exec(arrayOf(\"sh\", \"-c\", cmd))\n  }\n}\n"
RS = "pub fn run(cmd: &str) {\n    std::process::Command::new(\"sh\").arg(\"-c\").arg(cmd).output().unwrap();\n}\n"
RB = "class A\n  def run(cmd)\n    system('echo ' + cmd)\n  end\nend\n"
PHP = "<?php\nclass A {\n  public function run($cmd) { system('echo ' . $cmd); }\n}\n"
CS = "using System.Diagnostics;\npublic class A {\n  public void Run(string cmd) {\n    Process.Start(\"sh\", \"-c \" + cmd);\n  }\n}\n"

# name: (manifest files, source path, source, expected line, web manifest that turns it into an application)
CASES = {
    "python": ({"setup.py": "from setuptools import setup\nsetup(name='lib')\n"}, "lib/a.py", PY, 3, {"requirements.txt": "flask\n"}),
    "go": ({"go.mod": "module example.com/lib\n\ngo 1.21\n"}, "a.go", GO, 6, {"go.mod": "module x\n\nrequire github.com/gin-gonic/gin v1.9.0\n"}),
    "java": ({"pom.xml": "<project><packaging>jar</packaging></project>"}, "src/main/java/p/A.java", JAVA, 4, {"pom.xml": "<project>spring-boot-starter-web</project>"}),
    "kotlin": ({"build.gradle.kts": "plugins { kotlin(\"jvm\") }\n"}, "src/main/kotlin/A.kt", KT, 4, {"build.gradle.kts": "implementation(\"io.ktor:ktor-server\")"}),
    "rust": ({"Cargo.toml": "[package]\nname=\"x\"\n[lib]\n"}, "src/lib.rs", RS, 2, {"Cargo.toml": "[dependencies]\nactix-web=\"4\"\n[lib]\n"}),
    "ruby": ({"x.gemspec": "Gem::Specification.new { |s| s.name='x' }\n"}, "lib/a.rb", RB, 3, {"x.gemspec": "Gem::Specification.new { |s| s.add_dependency 'rails' }"}),
    "php": ({"composer.json": '{"name":"a/b","type":"library","autoload":{"psr-4":{"A\\\\":"src/"}}}'}, "src/A.php", PHP, 3,
            {"composer.json": '{"name":"a/b","require":{"laravel/framework":"*"},"autoload":{"psr-4":{"A\\\\":"src/"}}}'}),
    "csharp": ({"x.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"></Project>"}, "A.cs", CS, 4, {"x.csproj": "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>"}),
}


@pytest.mark.parametrize("lang", list(CASES))
def test_public_function_of_a_library_is_reported_as_minor(tmp_path, lang):
    manifest, path, src, line, _ = CASES[lang]
    assert _run(tmp_path, {**manifest, path: src}) == [(path, line, "minor")]


@pytest.mark.parametrize("lang", list(CASES))
def test_web_application_is_not_a_library(tmp_path, lang):
    manifest, path, src, _, web = CASES[lang]
    assert _run(tmp_path, {**manifest, **web, path: src}) == []


@pytest.mark.parametrize("lang", ["python", "go", "java", "kotlin", "rust", "ruby", "php", "csharp"])
def test_no_manifest_means_no_library_mode(tmp_path, lang):
    _, path, src, _, _ = CASES[lang]
    assert _run(tmp_path, {path: src}) == []


@pytest.mark.parametrize("lang", list(CASES))
def test_test_directories_are_not_entry_points(tmp_path, lang):
    manifest, path, src, _, _ = CASES[lang]
    assert _run(tmp_path, {**manifest, "tests/" + path: src}) == []


def test_private_members_are_not_entry_points(tmp_path):
    m = CASES
    assert _run(tmp_path / "py", {**m["python"][0], "lib/a.py": PY.replace("def run", "def _run")}) == []
    assert _run(tmp_path / "go", {**m["go"][0], "a.go": GO.replace("Run", "run")}) == []
    assert _run(tmp_path / "java", {**m["java"][0], m["java"][1]: JAVA.replace("public void", "private void")}) == []
    assert _run(tmp_path / "kt", {**m["kotlin"][0], m["kotlin"][1]: KT.replace("fun run", "private fun run")}) == []
    assert _run(tmp_path / "rs", {**m["rust"][0], "src/lib.rs": RS.replace("pub fn", "fn")}) == []
    assert _run(tmp_path / "php", {**m["php"][0], "src/A.php": PHP.replace("public function", "private function")}) == []
    assert _run(tmp_path / "cs", {**m["csharp"][0], "A.cs": CS.replace("public void", "private void")}) == []


def test_safe_library_function_stays_quiet(tmp_path):
    safe = "import os, shlex\ndef run(cmd):\n    os.system('echo ' + shlex.quote(cmd))\n"
    assert _run(tmp_path, {**CASES["python"][0], "lib/a.py": safe}) == []
    assert _run(tmp_path / "go", {**CASES["go"][0], "a.go": GO.replace('exec.Command("sh", "-c", cmd)', 'exec.Command("echo", cmd)')}) == []
