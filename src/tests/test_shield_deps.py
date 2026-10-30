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


def _no_pins(*args, **kwargs):
    raise AssertionError("manifest pins were read although no manifest changed")


def _no_whole_repo_walk(*args, **kwargs):
    raise AssertionError("the whole repository was walked for manifests")


@pytest.fixture
def no_repo_walk(monkeypatch):
    """Shield never discovers manifests by walking the repository: not through collect_pins (which walks every
    manifest folder with recursive globs) and not through os.walk."""
    import aletheore.vulnerabilities as vulnerabilities

    monkeypatch.setattr(vulnerabilities, "collect_pins", _no_whole_repo_walk)
    monkeypatch.setattr(_deps.os, "walk", _no_whole_repo_walk)


@pytest.fixture
def git_calls(monkeypatch):
    """Every git process the deps check starts, as the argument lists."""
    from aletheore.shield import changes, gitutil

    calls = []
    real = gitutil.run_git

    def counting(repo, *args, **kwargs):
        calls.append(args)
        return real(repo, *args, **kwargs)

    for module in (gitutil, changes, _deps):
        monkeypatch.setattr(module, "run_git", counting)
    return calls


@pytest.fixture
def parsed(monkeypatch):
    """The manifest folders parsed for pins, by the files each held."""
    seen = []
    real = _deps.pins_in_directory

    def spy(directory):
        seen.append(tuple(sorted(p.name for p in directory.iterdir())))
        return real(directory)

    monkeypatch.setattr(_deps, "pins_in_directory", spy)
    return seen


def test_no_manifest_change_never_reads_pins(tmp_path, monkeypatch, no_repo_walk, git_calls):
    repo = _init(tmp_path / "r", {
        "pyproject.toml": "[project]\nname = 'app'\ndependencies = ['requests>=2']\n",
        "app/main.py": "import os\n",
    })
    monkeypatch.setattr(_deps, "pins_in_directory", _no_pins)
    _write(repo, "app/main.py", "import os\nimport requests\nimport fastjsonx\n")
    refs, not_checked = added_dependencies(repo)
    # requests is declared in [project].dependencies (read without pins); fastjsonx is new
    assert refs == [DepRef("PyPI", "fastjsonx", "import", "app/main.py")]
    assert not_checked == []
    assert not any(call[0] == "ls-tree" for call in git_calls)


def test_manifest_change_parses_only_the_changed_folder_once_the_base_is_cached(tmp_path, no_repo_walk, parsed):
    files = {f"svc{i}/requirements.txt": f"lib{i}==1.0\n" for i in range(6)}
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n", **files})
    _write(repo, "svc3/requirements.txt", "lib3==1.0\nfastjsonx==1.0\n")
    refs, _ = added_dependencies(repo)
    assert refs == [DepRef("PyPI", "fastjsonx", "manifest", "svc3/requirements.txt")]
    assert len(parsed) == 7 + 1  # first run: every base folder, then the changed folder as it is now
    parsed.clear()
    _write(repo, "svc3/requirements.txt", "lib3==1.0\nfastjsonx==1.0\nzzqother==2.0\n")
    refs, _ = added_dependencies(repo)
    assert {r.name for r in refs} == {"fastjsonx", "zzqother"}
    assert parsed == [("requirements.txt",)]  # base folders all came from the cache


def test_git_calls_do_not_grow_with_manifests_or_changed_sources(tmp_path, git_calls):
    def run(count):
        files = {f"svc{i}/requirements.txt": f"lib{i}==1.0\n" for i in range(count)}
        files.update({f"pkg/m{i}.py": "x = 1\n" for i in range(count)})
        repo = _init(tmp_path / f"r{count}", files)
        for i in range(count):
            _write(repo, f"svc{i}/requirements.txt", f"lib{i}==1.0\nzzqnew{i}==1.0\n")
            _write(repo, f"pkg/m{i}.py", f"import zzqimp{i}\n")
        git_calls.clear()
        refs, _ = added_dependencies(repo)
        assert len(refs) == 2 * count
        return [call[0] for call in git_calls]

    small, large = run(2), run(25)
    assert small == large
    assert large.count("cat-file") == 2  # base manifests in one batch, base sources in one batch
    assert "show" not in large


def test_more_than_300_manifests_never_turn_a_known_dependency_into_a_new_one(tmp_path):
    files = {f"pkgs/p{i:03d}/requirements.txt": f"lib{i}==1.0\n" for i in range(320)}
    files["zz/requirements.txt"] = "sharedlib==1.0\n"  # sorts after the old 300-manifest cut-off
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n", **files})
    _write(repo, "requirements.txt", "requests==2.0\nsharedlib==1.0\nfastjsonx==1.0\n")
    refs, not_checked = added_dependencies(repo)
    assert refs == [DepRef("PyPI", "fastjsonx", "manifest", "requirements.txt")]
    assert not_checked == []


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_time_budget_reports_manifests_not_compared_and_the_next_run_continues(tmp_path, monkeypatch, parsed):
    from aletheore.shield.gitutil import Budget

    files = {f"svc{i}/requirements.txt": f"lib{i}==1.0\n" for i in range(5)}
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n", **files})
    _write(repo, "requirements.txt", "requests==2.0\nlib4==1.0\nfastjsonx==1.0\n")
    clock = _Clock()
    real = _deps.pins_in_directory

    def slow(directory):
        clock.t += 1.0
        return real(directory)

    monkeypatch.setattr(_deps, "pins_in_directory", slow)
    refs, not_checked = added_dependencies(repo, budget=Budget(2.5, clock))
    assert refs == []  # an incomplete base never yields a "new" manifest dependency
    assert NotChecked("", _deps.TIME_BUDGET_MANIFESTS) in not_checked
    first = clock.t
    refs, not_checked = added_dependencies(repo)
    assert refs == [DepRef("PyPI", "fastjsonx", "manifest", "requirements.txt")]
    assert clock.t - first == (6 - first) + 1  # the rest of the base folders, then the changed one


def test_a_corrupt_pins_cache_is_ignored_and_replaced(tmp_path, parsed):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n", "a/requirements.txt": "x==1\n"})
    cache = repo / ".git" / "aletheore-shield" / _deps.PINS_CACHE_NAME
    cache.parent.mkdir()
    cache.write_text('{"v": 1, "units": {"k": [[1, 2]]}', encoding="utf-8")
    _write(repo, "requirements.txt", "requests==2.0\nfastjsonx==1.0\n")
    assert added_dependencies(repo)[0] == [DepRef("PyPI", "fastjsonx", "manifest", "requirements.txt")]
    assert json.loads(cache.read_text(encoding="utf-8"))["v"] == 1
    parsed.clear()
    assert added_dependencies(repo)[0] == [DepRef("PyPI", "fastjsonx", "manifest", "requirements.txt")]
    assert parsed == [("requirements.txt",)]


def test_pins_cache_stays_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(_deps, "PINS_CACHE_MAX_UNITS", 3)
    files = {f"svc{i}/requirements.txt": f"lib{i}==1.0\n" for i in range(6)}
    repo = _init(tmp_path / "r", files)
    _write(repo, "svc0/requirements.txt", "lib0==1.0\nfastjsonx==1.0\n")
    added_dependencies(repo)
    data = json.loads((repo / ".git" / "aletheore-shield" / _deps.PINS_CACHE_NAME).read_text(encoding="utf-8"))
    assert len(data["units"]) == 3


def _cache_file(repo):
    return repo / ".git" / "aletheore-shield" / _deps.PINS_CACHE_NAME


def test_pins_parsed_by_another_aletheore_version_are_never_served(tmp_path, parsed):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n", "a/requirements.txt": "x==1\n"})
    _write(repo, "a/requirements.txt", "x==1\ny==1\n")
    added_dependencies(repo)  # fills the cache
    data = json.loads(_cache_file(repo).read_text(encoding="utf-8"))
    # Pins as a different parser version might have read them: every base folder "declares" fastjsonx.
    data["units"] = {key: [["fastjsonx", "1.0", "PyPI"]] for key in data["units"]}
    data["parser"] = "0.0.1-other"
    _cache_file(repo).write_text(json.dumps(data), encoding="utf-8")
    _write(repo, "requirements.txt", "requests==2.0\nfastjsonx==1.0\n")
    parsed.clear()
    refs, _ = added_dependencies(repo)
    assert DepRef("PyPI", "fastjsonx", "manifest", "requirements.txt") in refs
    assert len(parsed) == 2 + 2  # both base folders parsed again, then both changed folders


def test_dependency_config_change_alone_parses_no_pins_but_still_marks_the_index_private(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r", {"package.json": json.dumps({"name": "p"}), "a.js": "x\n"})
    monkeypatch.setattr(_deps, "pins_in_directory", _no_pins)
    _write(repo, ".npmrc", "@corp:registry=https://npm.corp.example/\n")
    _write(repo, "a.js", "import t from '@corp/thing';\n")
    refs, not_checked = added_dependencies(repo)
    assert refs == [] and len(not_checked) == 1 and "private" in not_checked[0].reason


def test_a_manifest_in_ignored_paths_is_not_compared(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r", {".aletheore.json": json.dumps({"ignored_paths": ["examples"]}),
                                  "examples/demo/requirements.txt": "a==1\n"})
    monkeypatch.setattr(_deps, "pins_in_directory", _no_pins)
    _write(repo, "examples/demo/requirements.txt", "a==1\nzzqdemo==1\n")
    assert added_dependencies(repo) == ([], [])


def test_moved_between_manifests_is_not_new(tmp_path):
    repo = _init(tmp_path / "r", {"a/requirements.txt": "movedlib==1.0\n", "b/requirements.txt": "other==1\n"})
    _write(repo, "a/requirements.txt", "")
    _write(repo, "b/requirements.txt", "other==1\nmovedlib==1.0\n")
    assert added_dependencies(repo) == ([], [])


def test_deleted_source_and_manifest_files_are_handled(tmp_path):
    repo = _init(tmp_path / "r", {"a/requirements.txt": "x==1\n", "m.py": "import x\n", "keep.py": "y = 1\n"})
    (repo / "a" / "requirements.txt").unlink()
    (repo / "m.py").unlink()
    assert added_dependencies(repo) == ([], [])


def test_installed_names_are_listed_only_when_an_import_needs_them(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r", {"a.py": "x = 1\n"})
    monkeypatch.setattr(_deps, "_installed_import_names", lambda: (_ for _ in ()).throw(AssertionError("listed")))
    _write(repo, "a.py", "x = 2\n")
    assert added_dependencies(repo) == ([], [])
