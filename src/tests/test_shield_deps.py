import json
import subprocess

from aletheore.shield.deps import DepRef, NotChecked, added_dependencies


import pytest

from aletheore.shield import deps as _deps


@pytest.fixture(autouse=True)
def _nothing_installed(monkeypatch):
    monkeypatch.setattr(_deps, "_installed_import_names", lambda: set())


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init(path, files=None, commit=True):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    for rel, text in (files or {}).items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if commit:
        (path / ".keep").write_text("k\n")
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", "init")
    return path


def _write(repo, rel, text):
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_new_manifest_entry_is_reported(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n",
                                  "web/package.json": json.dumps({"name": "web", "dependencies": {"left-pad": "1.0.0"}})})
    _write(repo, "requirements.txt", "requests==2.0\nfastjsonx==1.0\n")
    _write(repo, "web/package.json", json.dumps({"name": "web", "dependencies": {"left-pad": "1.0.0", "tinyutilx": "^2.1.0"}}))
    refs, not_checked = added_dependencies(repo)
    assert DepRef("PyPI", "fastjsonx", "manifest", "requirements.txt") in refs
    assert DepRef("npm", "tinyutilx", "manifest", "web/package.json") in refs
    assert len(refs) == 2 and not_checked == []


def test_dependency_already_in_base_is_not_new(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "fastjsonx==1.0\n"})
    _write(repo, "requirements.txt", "fastjsonx==2.0\n")
    refs, not_checked = added_dependencies(repo)
    assert refs == [] and not_checked == []


def test_workspace_and_local_packages_are_skipped(tmp_path):
    repo = _init(tmp_path / "r", {
        "package.json": json.dumps({"name": "root", "workspaces": ["packages/*"]}),
        "packages/core/package.json": json.dumps({"name": "@acme/core", "version": "1.0.0"}),
        "pyproject.toml": "[tool.poetry]\nname='app'\n[tool.poetry.dependencies]\npython='^3.11'\n",
    })
    _write(repo, "package.json", json.dumps({"name": "root", "workspaces": ["packages/*"],
                                             "dependencies": {"@acme/core": "1.0.0", "other-ws": "workspace:*"}}))
    _write(repo, "pyproject.toml",
           "[tool.poetry]\nname='app'\n[tool.poetry.dependencies]\npython='^3.11'\nsiblib={path='../siblib'}\n"
           "uvlib='1.0'\n[tool.uv.sources]\nuvlib={path='../uvlib'}\n")
    refs, not_checked = added_dependencies(repo)
    assert refs == []


def test_import_only_addition_in_mapped_name_resolves(tmp_path):
    repo = _init(tmp_path / "r", {"app.py": "x = 1\n"})
    _write(repo, "app.py", "import yaml\nfrom PIL import Image\nx = 1\n")
    refs, _ = added_dependencies(repo)
    assert DepRef("PyPI", "pyyaml", "import", "app.py") in refs
    assert DepRef("PyPI", "pillow", "import", "app.py") in refs


def test_import_already_in_base_file_is_not_new(tmp_path):
    repo = _init(tmp_path / "r", {"app.py": "import yaml\n"})
    _write(repo, "app.py", "import yaml\nprint(1)\n")
    assert added_dependencies(repo)[0] == []


def test_unmapped_import_is_reported_as_import_source_when_not_stdlib_or_local(tmp_path):
    repo = _init(tmp_path / "r", {"app.py": "x = 1\n"})
    _write(repo, "app.py", "import zzqfakepkg\nx = 1\n")
    _write(repo, "ui.ts", "import { a } from 'zzq-fake-js';\nconst b = require('@zzq/scoped/deep');\nimport('lodash/fp');\n")
    refs, _ = added_dependencies(repo)
    assert DepRef("PyPI", "zzqfakepkg", "import", "app.py") in refs
    assert DepRef("npm", "zzq-fake-js", "import", "ui.ts") in refs
    assert DepRef("npm", "@zzq/scoped", "import", "ui.ts") in refs
    assert DepRef("npm", "lodash", "import", "ui.ts") in refs


def test_unmapped_import_declared_in_manifest_is_skipped(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "zzqdeclared==1.0\n", "app.py": "x = 1\n"})
    _write(repo, "app.py", "import zzqdeclared\n")
    assert added_dependencies(repo)[0] == []


def test_stdlib_and_relative_and_repo_local_imports_are_ignored(tmp_path):
    repo = _init(tmp_path / "r", {
        "src/mypkg/__init__.py": "",
        "src/mypkg/util.py": "",
        "tools/helper.py": "",
        "web/lib/thing.js": "",
        "web/package.json": json.dumps({"name": "web-app"}),
        "app.py": "x = 1\n",
    })
    _write(repo, "app.py", "import os, sys\nfrom . import x\nfrom .y import z\nimport mypkg\nfrom mypkg.util import q\nimport helper\n")
    _write(repo, "web/a.js", "import fs from 'node:fs';\nimport p from 'path';\nimport x from './x';\nimport w from 'web-app';\nimport t from 'lib/thing';\n")
    refs, not_checked = added_dependencies(repo)
    assert refs == [] and not_checked == []


def test_unsupported_ecosystem_yields_not_checked(tmp_path):
    repo = _init(tmp_path / "r", {"go.mod": "module example.com/app\n\ngo 1.21\n"})
    _write(repo, "go.mod", "module example.com/app\n\ngo 1.21\n\nrequire github.com/foo/bar v1.2.3\n")
    refs, not_checked = added_dependencies(repo)
    assert refs == []
    assert len(not_checked) == 1 and "Go" in not_checked[0].reason
    assert isinstance(not_checked[0], NotChecked)


def test_private_index_config_yields_not_checked(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n", "package.json": json.dumps({"name": "p"})})
    _write(repo, "requirements.txt", "requests==2.0\ninternalthing==1.0\n")
    _write(repo, "pip.conf", "[global]\nindex-url = https://pypi.corp.example/simple\n")
    _write(repo, "package.json", json.dumps({"name": "p", "dependencies": {"@corp/thing": "1.0.0"}}))
    _write(repo, ".npmrc", "@corp:registry=https://npm.corp.example/\n")
    refs, not_checked = added_dependencies(repo)
    assert refs == []
    reasons = " ".join(n.reason for n in not_checked)
    assert len(not_checked) == 2 and "private" in reasons


def test_public_npmrc_registry_is_not_private(tmp_path):
    repo = _init(tmp_path / "r", {"package.json": json.dumps({"name": "p"})})
    _write(repo, ".npmrc", "registry=https://registry.npmjs.org/\n")
    _write(repo, "package.json", json.dumps({"name": "p", "dependencies": {"zzqpub": "1.0.0"}}))
    refs, not_checked = added_dependencies(repo)
    assert refs == [DepRef("npm", "zzqpub", "manifest", "package.json")] and not_checked == []


def test_requirements_index_url_line_is_private(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "a==1\n"})
    _write(repo, "requirements.txt", "--extra-index-url https://x.example/simple\na==1\nb==2\n")
    refs, not_checked = added_dependencies(repo)
    assert refs == [] and len(not_checked) == 1


def test_repo_without_head_treats_base_as_empty(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "fastjsonx==1.0\n"}, commit=False)
    refs, _ = added_dependencies(repo)
    assert DepRef("PyPI", "fastjsonx", "manifest", "requirements.txt") in refs


def test_lockfile_transitives_are_not_reported(tmp_path):
    repo = _init(tmp_path / "r", {"package.json": json.dumps({"name": "p"})})
    lock = {"name": "p", "lockfileVersion": 3, "packages": {"": {"name": "p"},
            "node_modules/transitivex": {"version": "1.0.0"}}}
    _write(repo, "package-lock.json", json.dumps(lock))
    assert added_dependencies(repo)[0] == []


def test_names_declared_in_other_manifest_files_suppress_imports(tmp_path):
    repo = _init(tmp_path / "r", {
        "requirements-dev.txt": "devfromreq==1.0\n",
        "pyproject.toml": "[dependency-groups]\ndev=['grouplib>=1']\n[project]\nname='x'\nversion='1'\n",
        "app.py": "x = 1\n",
    })
    _write(repo, "app.py", "import devfromreq\nimport grouplib\nx = 1\n")
    assert added_dependencies(repo)[0] == []


def test_declared_dist_containing_import_name_suppresses(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "foo-bar==1.0\n", "app.py": "x = 1\n"})
    _write(repo, "app.py", "import foo\nimport zzqhallucinated\n")
    refs, _ = added_dependencies(repo)
    assert refs == [DepRef("PyPI", "zzqhallucinated", "import", "app.py")]


def test_installed_module_import_is_skipped_even_when_mapped(tmp_path, monkeypatch):
    monkeypatch.setattr(_deps, "_installed_import_names", lambda: {"yaml", "six"})
    repo = _init(tmp_path / "r", {"app.py": "x = 1\n"})
    _write(repo, "app.py", "import yaml\nimport six\n")
    assert added_dependencies(repo)[0] == []
