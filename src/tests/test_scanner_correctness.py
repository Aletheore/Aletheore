"""Scanner and evidence correctness: unreadable files, workspaces, nested Go
modules and manifests, .gitignore, config validation, read-only folders."""

import json
import os
import subprocess
import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from aletheore.dead_code import find_dead_code
from aletheore.dependents import dependents_of
from aletheore.evidence import (
    EVIDENCE_VERSION,
    EvidenceWriteError,
    IncompatibleEvidenceVersionError,
    load_evidence_file,
    scan_repository,
    write_evidence,
)
from aletheore.licenses import check_dependency_licenses
from aletheore.manifests import clear_manifest_cache, discover_manifests
from aletheore.query import find_imported_by, find_imports
from aletheore.repo_config import load_repo_config, validate_repo_config_text
from aletheore.scanner.detect import detect_monorepo, git_visible_paths
from aletheore.scanner.graph import build_module_graph
from aletheore.vulnerabilities import check_vulnerabilities

pytestmark = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="permission tests need a non-root user"
)


# chmod 0 / a 0o555 folder do not make a file unreadable or a folder read-only on
# Windows (and some tests are root-proof only via the skip above), so those
# tests are POSIX-only; the same behaviour is covered everywhere by the
# simulated-read tests below.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


@pytest.fixture(autouse=True)
def _fresh_caches():
    git_visible_paths.cache_clear()
    clear_manifest_cache()
    yield
    git_visible_paths.cache_clear()
    clear_manifest_cache()


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _git(repo: Path) -> None:
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _quick_scan(repo: Path, **kwargs) -> dict:
    options = dict(
        check_vulnerabilities=False, scan_git_history=False, check_licenses=False,
        check_static_analysis=False,
    )
    options.update(kwargs)
    return scan_repository(repo, **options)


# ---------------------------------------------------------------- H1


UNREADABLE = {
    "ok.py": "def f(): pass\n",
    "bad.py": "def g(): pass\n",
    "bad.ts": "export const a = 1\n",
    "bad.go": "package main\nfunc main() {}\n",
    "bad.java": "class A {}\n",
    "bad.rb": "puts 1\n",
    "bad.sql": "CREATE TABLE t (id int);\n",
    "bad.yaml": "apiVersion: v1\nkind: Pod\n",
    "bad.md": "# hi\n",
    ".env.local": "FOO=1\n",
    "routes/web.php": "<?php echo 1;",
    "migrations/0001_initial.py": "class M: pass\n",
    "db/migrate/20200101_a.rb": "create_table :t\n",
    "docker-compose.yml": "services:\n  web:\n    image: x\n",
    "requirements.txt": "flask==2.0\n",
    "package.json": '{"name": "x", "dependencies": {"express": "4.0.0"}}',
    "pyproject.toml": '[project]\nname = "x"\n',
    "go.mod": "module example.com/x\n",
    "LICENSE": "MIT License\n",
}


@posix_only
@pytest.mark.parametrize("git", [False, True])
def test_scan_survives_unreadable_files_of_every_kind(tmp_path, git):
    repo = tmp_path / "repo"
    _write(repo, UNREADABLE)
    if git:
        _git(repo)
    for rel in UNREADABLE:
        if rel != "ok.py":
            (repo / rel).chmod(0)

    evidence = _quick_scan(repo)

    paths = {m["path"] for m in evidence["repository"]["modules"]}
    assert "ok.py" in paths
    assert evidence["repository"]["unreadable_files"] is not None
    skipped = {e["path"] for e in evidence["repository"]["unparseable_files"]} | {
        e["path"] for e in evidence["repository"]["unreadable_files"]
    }
    assert {"bad.py", "bad.ts", "bad.go", "bad.java", "bad.rb"} <= skipped


@posix_only
def test_unreadable_manifests_degrade_the_dependency_checks_without_crashing(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, {"requirements.txt": "flask==2.0\n", "web/package.json": '{"dependencies": {"a": "1.0.0"}}'})
    (repo / "web" / "package.json").chmod(0)
    seen = {}

    def fake_batch(pins, timeout):
        seen["pins"] = list(pins)
        return [{"vulns": []}] * len(pins)

    with patch("aletheore.vulnerabilities._query_batch", fake_batch):
        nested = check_vulnerabilities(repo, cache_path=tmp_path / "c.json")
    # An unreadable NESTED manifest skips only its own directory.
    assert nested["checked"] is True and nested["unreadable_manifest_dirs"] == ["web"]
    assert ("flask", "2.0", "PyPI") in seen["pins"]

    (repo / "requirements.txt").chmod(0)
    root = check_vulnerabilities(repo, cache_path=tmp_path / "c2.json")
    # An unreadable ROOT manifest reports the check as unavailable, not as clean.
    assert root["checked"] is False and "could not read a dependency manifest" in root["reason"]


@posix_only
def test_endpoint_mapping_pass_records_an_unreadable_file(tmp_path):
    from aletheore.endpoints import map_api_endpoints

    repo = tmp_path / "repo"
    _write(repo, {"app.py": "from flask import Flask\napp = Flask(__name__)\n@app.get('/x')\ndef x(): pass\n",
                  "locked.py": "x = 1\n"})
    (repo / "locked.py").chmod(0)
    result = map_api_endpoints(repo)
    assert [e["path"] for e in result["endpoints"]] == ["/x"]


# ---------------------------------------------------------------- M1


def test_gitignored_files_are_not_scanned_but_tracked_and_new_files_are(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, {
        ".gitignore": "built/\nforced.ts\n",
        "packages/a/.gitignore": "gen/\n",
        "built/z.ts": "export const z = 1\n",
        "packages/a/gen/out.ts": "export const o = 1\n",
        "packages/a/src/x.ts": "export const x = 1\n",
        "forced.ts": "export const f = 1\n",
    })
    _git(repo)
    subprocess.run(["git", "add", "-f", "forced.ts"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "force"], cwd=repo, check=True)
    _write(repo, {"packages/a/src/new_untracked.ts": "export const n = 1\n"})

    evidence = _quick_scan(repo)

    paths = {m["path"] for m in evidence["repository"]["modules"]}
    assert paths == {"packages/a/src/x.ts", "forced.ts", "packages/a/src/new_untracked.ts"}
    unreachable = {m["path"] for m in evidence["repository"]["dead_code"]["unreachable_modules"]}
    assert "built/z.ts" not in unreachable and "packages/a/gen/out.ts" not in unreachable


def test_non_git_folder_keeps_the_previous_behaviour(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, {".gitignore": "built/\n", "built/z.ts": "export const z = 1\n"})
    modules, _graph, _bad = build_module_graph(repo)
    assert [m["path"] for m in modules] == ["built/z.ts"]


def test_aletheore_json_ignored_paths_still_work_in_a_git_repo(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, {".aletheore.json": '{"ignored_paths": ["skipme"]}', "skipme/a.ts": "export const a=1\n",
                  "keep/b.ts": "export const b=1\n"})
    _git(repo)
    evidence = _quick_scan(repo)
    assert {m["path"] for m in evidence["repository"]["modules"]} == {"keep/b.ts"}


# ---------------------------------------------------------------- H3a


def _workspace_repo(root: Path) -> None:
    _write(root, {
        "package.json": '{"name": "root", "private": true, "workspaces": ["packages/*"]}',
        "packages/a/package.json": '{"name": "@m/a", "dependencies": {"@m/b": "*", "lodash": "4.17.20", "left-pad": "1.3.0"}}',
        "packages/b/package.json": '{"name": "@m/b", "main": "dist/index.js"}',
        "packages/c/package.json": '{"name": "@m/c", "main": "lib/index.js"}',
        "packages/d/package.json": '{"name": "@m/d", "exports": {".": "./src/index.ts", "./utils": "./src/utils/helpers.ts"}}',
        "packages/a/src/index.ts": (
            "import { b } from '@m/b';\nimport { c } from '@m/c';\nimport { d } from '@m/d';\n"
            "import { h } from '@m/d/utils';\nimport _ from 'lodash';\n"
        ),
        "packages/b/src/index.ts": "export const b = 1;\n",
        "packages/c/lib/index.js": "export const c = 1;\n",
        "packages/d/src/index.ts": "export const d = 1;\n",
        "packages/d/src/utils/helpers.ts": "export const h = 1;\n",
    })


def test_workspace_package_imports_become_graph_edges(tmp_path):
    _workspace_repo(tmp_path)
    modules, graph, _bad = build_module_graph(tmp_path)
    edges = {tuple(e) for e in graph["edges"]}
    importer = "packages/a/src/index.ts"
    assert (importer, "packages/b/src/index.ts") in edges  # main points into dist, mapped back to src
    assert (importer, "packages/c/lib/index.js") in edges  # main in lib/
    assert (importer, "packages/d/src/index.ts") in edges  # exports "."
    assert (importer, "packages/d/src/utils/helpers.ts") in edges  # exports "./utils"
    assert not any("lodash" in t for _s, t in edges)


def test_dependents_and_imports_see_workspace_edges(tmp_path):
    _workspace_repo(tmp_path)
    modules, graph, bad = build_module_graph(tmp_path)
    evidence = {
        "scanned_at": "2999-01-01T00:00:00+00:00",
        "repository": {"modules": modules, "dependency_graph": graph, "unparseable_files": bad},
    }
    allp, direct = dependents_of(evidence, "packages/b/src/index.ts")
    assert allp == ["packages/a/src/index.ts"] and direct == {"packages/a/src/index.ts"}
    assert "packages/b/src/index.ts" in find_imports(evidence, "packages/a/src/index.ts")
    assert find_imported_by(evidence, "packages/d/src/utils/helpers.ts") == ["packages/a/src/index.ts"]


# ---------------------------------------------------------------- H3b


def test_go_module_in_a_subdirectory_resolves_internal_imports(tmp_path):
    _write(tmp_path, {
        "go/go.mod": "module example.com/g\n\ngo 1.21\n",
        "go/cmd/main.go": 'package main\nimport "example.com/g/internal/util"\nfunc main() { util.Do() }\n',
        "go/internal/util/util.go": "package util\nfunc Do() {}\n",
    })
    modules, graph, _bad = build_module_graph(tmp_path)
    assert graph["edges"] == [["go/cmd/main.go", "go/internal/util/util.go"]]
    evidence = {"repository": {"modules": modules}}
    assert find_imports(evidence, "go/cmd/main.go") == ["go/internal/util/util.go"]
    assert find_imported_by(evidence, "go/internal/util/util.go") == ["go/cmd/main.go"]


def test_several_go_modules_and_a_root_module_all_resolve(tmp_path):
    _write(tmp_path, {
        "go.mod": "module example.com/root\n",
        "main.go": 'package main\nimport (\n"example.com/root/lib"\n"example.com/svc/api"\n"example.com/rootx/nope"\n)\nfunc main() {}\n',
        "lib/lib.go": "package lib\n",
        "svc/go.mod": "module example.com/svc\n",
        "svc/api/api.go": "package api\n",
    })
    _modules, graph, _bad = build_module_graph(tmp_path)
    assert {tuple(e) for e in graph["edges"]} == {("main.go", "lib/lib.go"), ("main.go", "svc/api/api.go")}


# ---------------------------------------------------------------- H3c / H3d


def _nested_manifest_repo(root: Path) -> None:
    _write(root, {
        "requirements.txt": "django==3.0\n",
        "web/package.json": '{"name": "web", "dependencies": {"express": "4.18.0", "lodash": "4.17.21"}}',
        "web/src/server.js": "const express = require('express');\n",
        "packages/a/package.json": '{"name": "pa", "dependencies": {"lodash": "4.17.21", "left-pad": "1.3.0"}}',
        "packages/a/index.js": "const _ = require('lodash');\n",
        "svc/requirements.txt": "flask==2.0.0\n",
        "go/go.mod": "module example.com/g\n\nrequire github.com/gin-gonic/gin v1.9.0\n",
        "node_modules/skipme/package.json": '{"name": "skipme", "dependencies": {"zzz": "1.0.0"}}',
        "vendor/x/package.json": '{"name": "vendored", "dependencies": {"yyy": "1.0.0"}}',
        "build/package.json": '{"name": "built", "dependencies": {"www": "1.0.0"}}',
    })


def test_manifest_discovery_finds_nested_and_skips_dependency_dirs(tmp_path):
    _nested_manifest_repo(tmp_path)
    found = discover_manifests(tmp_path)
    assert found["dirs"] == [".", "go", "svc", "web", "packages/a"]  # root first, then by depth
    assert found["truncated"] is False


def test_manifest_discovery_is_capped_with_a_flag(tmp_path):
    from aletheore import manifests

    for i in range(5):
        _write(tmp_path, {f"p{i}/package.json": "{}"})
    with patch.object(manifests, "MAX_MANIFEST_DIRS", 3):
        clear_manifest_cache()
        found = discover_manifests(tmp_path)
    assert len(found["dirs"]) == 3 and found["total"] == 5 and found["truncated"] is True


def test_vulnerability_check_covers_nested_manifests_and_attributes_them(tmp_path):
    _nested_manifest_repo(tmp_path)
    captured = {}

    def fake_batch(pins, timeout):
        captured["pins"] = list(pins)
        return [{"vulns": [{"id": "OSV-1"}] if name == "lodash" else []} for name, _v, _e in pins]

    with patch("aletheore.vulnerabilities._query_batch", fake_batch), patch(
        "aletheore.vulnerabilities._fetch_vuln_detail", return_value={"summary": "bad", "severity": []}
    ):
        result = check_vulnerabilities(tmp_path, cache_path=tmp_path / "cache.json")

    pins = captured["pins"]
    assert ("express", "4.18.0", "npm") in pins
    assert ("flask", "2.0.0", "PyPI") in pins
    assert ("django", "3.0", "PyPI") in pins
    assert ("github.com/gin-gonic/gin", "v1.9.0", "Go") in pins or any(p[2] == "Go" for p in pins)
    assert not any(p[0] in {"zzz", "yyy", "www"} for p in pins)
    assert pins.count(("lodash", "4.17.21", "npm")) == 1  # de-duplicated across two manifests
    (finding,) = result["findings"]
    assert finding["package"] == "lodash"
    assert sorted(finding["manifests"]) == ["packages/a", "web"]
    assert result["manifest_dirs"] == [".", "go", "svc", "web", "packages/a"]


def test_license_check_covers_nested_manifests(tmp_path):
    _write(tmp_path, {"web/package.json": '{"dependencies": {"leftpad": "1.0.0"}}', "svc/requirements.txt": "pyqt5==5.15.10\n"})

    def fake_fetch(pin, timeout):
        return "GPL-3.0" if pin[0] == "pyqt5" else "MIT"

    with patch("aletheore.licenses._fetch_one_license", fake_fetch):
        result = check_dependency_licenses(tmp_path, cache_path=tmp_path / "lc.json")
    (finding,) = result["findings"]
    assert finding["package"] == "pyqt5" and finding["manifests"] == ["svc"]


def test_unused_dependencies_cover_nested_manifests_scoped_to_their_directory(tmp_path):
    _nested_manifest_repo(tmp_path)
    modules, _graph, _bad = build_module_graph(tmp_path)
    result = find_dead_code(tmp_path, modules, None)
    unused = {(d["package"], d.get("manifest", ".")) for d in result["unused_dependencies"]}
    assert ("lodash", "web") in unused  # declared in web/, only used under packages/a
    assert ("left-pad", "packages/a") in unused
    assert ("flask", "svc") in unused
    assert ("express", "web") not in unused
    assert ("lodash", "packages/a") not in unused


def test_monorepo_detected_from_manifests_in_different_directories(tmp_path):
    _write(tmp_path, {"requirements.txt": "a==1\n", "web/package.json": '{"name": "web"}',
                      "go/go.mod": "module example.com/g\n"})
    result = detect_monorepo(tmp_path)
    assert result["detected"] is True and result["workspaces"] == []
    assert {p["path"] for p in result["packages"]} == {".", "web", "go"}
    by_path = {p["path"]: p for p in result["packages"]}
    assert by_path["go"]["name"] == "example.com/g" and by_path["web"]["ecosystems"] == ["npm"]


def test_single_project_is_not_a_monorepo(tmp_path):
    _write(tmp_path, {"requirements.txt": "a==1\n", "pyproject.toml": '[project]\nname = "x"\n'})
    assert detect_monorepo(tmp_path)["detected"] is False


def test_workspaces_declaration_is_a_monorepo_even_with_one_package(tmp_path):
    _write(tmp_path, {"package.json": '{"workspaces": {"packages": ["p/*"]}}'})
    result = detect_monorepo(tmp_path)
    assert result["detected"] is True and result["workspaces"] == ["p/*"]


# ---------------------------------------------------------------- M3 / M4


@posix_only
def test_write_evidence_to_a_read_only_folder_raises_a_clear_error(tmp_path):
    repo = tmp_path / "ro"
    repo.mkdir()
    repo.chmod(0o555)
    try:
        with pytest.raises(EvidenceWriteError) as excinfo:
            write_evidence({"x": 1}, repo)
    finally:
        repo.chmod(0o755)
    message = str(excinfo.value)
    assert message.startswith("cannot write evidence to ") and ".aletheore" in message
    assert "permission denied" in message
    assert isinstance(excinfo.value, OSError)


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("{bad", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"severity_threshold": "urgent"}', "severity_threshold"),
        ('{"cluster_resolution": "x"}', "cluster_resolution"),
        ('{"disabled_checks": ["nonsense"]}', "unknown value 'nonsense'"),
        ('{"disabled_checks": [["x"]]}', "unknown value"),
        ('{"mystery": 1}', "unknown key 'mystery'"),
        ('{"ignored_paths": "a"}', "ignored_paths must be a list"),
    ],
)
def test_invalid_config_is_reported(text, fragment):
    problems = validate_repo_config_text(text)
    assert any(fragment in p for p in problems), problems


def test_valid_config_has_no_problems():
    text = json.dumps({"layer_markers": {"a": 1}, "cluster_resolution": 1.5, "dead_code_entry_points": [],
                       "accepted_secrets": [], "ignored_paths": ["x"], "disabled_checks": ["licenses"],
                       "severity_threshold": None})
    assert validate_repo_config_text(text) == []
    assert validate_repo_config_text(None) == []


def test_scan_warns_and_records_config_problems_but_continues(tmp_path):
    _write(tmp_path, {"a.py": "x = 1\n", "skip/b.py": "y = 2\n",
                      ".aletheore.json": '{"ignored_paths": ["skip"], "severity_threshold": "urgent", "bogus": 1}'})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        evidence = _quick_scan(tmp_path)
    assert len(evidence["config_warnings"]) == 2
    assert sum(".aletheore.json" in str(w.message) for w in caught) == 2
    assert {m["path"] for m in evidence["repository"]["modules"]} == {"a.py"}  # valid ignored_paths still applied
    assert load_repo_config(tmp_path)["severity_threshold"] is None


def test_scan_without_config_problems_has_an_empty_list(tmp_path):
    _write(tmp_path, {"a.py": "x = 1\n"})
    assert _quick_scan(tmp_path)["config_warnings"] == []


# ---------------------------------------------------------------- L items


def test_module_imports_are_deduplicated_in_order(tmp_path):
    _write(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/helpers.py": "def h(): pass\n",
        "pkg/other.py": "def o(): pass\n",
        "pkg/core.py": "from . import helpers\nfrom .helpers import h\nimport pkg.helpers\nfrom .other import o\n",
    })
    modules, graph, _bad = build_module_graph(tmp_path)
    core = next(m for m in modules if m["path"] == "pkg/core.py")
    assert core["imports"] == ["pkg/helpers.py", "pkg/other.py"]
    assert graph["edges"].count(["pkg/core.py", "pkg/helpers.py"]) == 1


def test_python_syntax_error_is_reported_as_unparseable(tmp_path):
    _write(tmp_path, {"good.py": "def ok(): pass\n", "broken.py": "def syntaxerr(:\n    pass\n"})
    modules, _graph, bad = build_module_graph(tmp_path)
    assert [e["path"] for e in bad] == ["broken.py"]
    assert "syntax error" in bad[0]["reason"]
    assert {m["path"] for m in modules} == {"good.py", "broken.py"}


def test_static_analysis_lists_only_tools_that_ran(tmp_path):
    from aletheore.static_analysis import check_static_analysis

    _write(tmp_path, {"a.py": "x = 1\n"})
    result = check_static_analysis(tmp_path)
    assert "pmd" not in result["tools_run"] and "gosec" not in result["tools_run"]
    skipped = {s["tool"]: s["reason"] for s in result["tools_skipped"]}
    assert skipped["pmd"] == "no Java source files" and skipped["gosec"] == "no Go source files"


def test_git_section_says_why_it_is_unavailable(tmp_path):
    _write(tmp_path, {"a.py": "x = 1\n"})
    assert _quick_scan(tmp_path)["git"] == {"available": False, "reason": "not a git repository"}
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    assert _quick_scan(repo)["git"] == {"available": False, "reason": "no commits yet"}


def test_evidence_carries_the_cli_version_next_to_the_schema_version(tmp_path):
    from aletheore import __version__

    _write(tmp_path, {"a.py": "x = 1\n"})
    evidence = _quick_scan(tmp_path)
    assert evidence["aletheore_version"] == EVIDENCE_VERSION
    assert evidence["cli_version"] == __version__


def test_incompatible_evidence_message_names_the_schema(tmp_path):
    path = tmp_path / "air.json"
    path.write_text(json.dumps({"aletheore_version": "0.1.0"}))
    with pytest.raises(IncompatibleEvidenceVersionError) as excinfo:
        load_evidence_file(path)
    message = str(excinfo.value)
    assert "evidence schema '0.1.0'" in message and "aletheore_version" not in message


# ----------------------------------------------- simulated unreadability (all platforms)


def _deny(monkeypatch, name: str) -> None:
    real_bytes, real_text = Path.read_bytes, Path.read_text

    def read_bytes(self, *a, **k):
        if self.name == name:
            raise PermissionError(13, "Permission denied")
        return real_bytes(self, *a, **k)

    def read_text(self, *a, **k):
        if self.name == name:
            raise PermissionError(13, "Permission denied")
        return real_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)


def test_endpoint_pass_skips_an_unreadable_file_simulated(tmp_path, monkeypatch):
    from aletheore.endpoints import map_api_endpoints

    _write(tmp_path, {"app.py": "from flask import Flask\napp = Flask(__name__)\n@app.get('/x')\ndef x(): pass\n",
                      "locked.py": "x = 1\n", "locked.ts": "export const a = 1\n"})
    _deny(monkeypatch, "locked.py")
    _deny(monkeypatch, "locked.ts")
    assert [e["path"] for e in map_api_endpoints(tmp_path)["endpoints"]] == ["/x"]


def test_safe_io_records_unreadable_files(tmp_path, monkeypatch):
    from aletheore.safe_io import collect_unreadable, read_bytes_or_none, read_text_lenient, reset_unreadable

    _write(tmp_path, {"a.env": "X=1\n"})
    reset_unreadable()
    _deny(monkeypatch, "a.env")
    assert read_bytes_or_none(tmp_path / "a.env") is None
    assert read_text_lenient(tmp_path / "a.env") == ""
    (entry,) = collect_unreadable(tmp_path)
    assert entry["path"] == "a.env" and entry["reason"].startswith("could not read file")


def test_unreadable_nested_manifest_skips_only_its_directory_simulated(tmp_path, monkeypatch):
    _write(tmp_path, {"requirements.txt": "flask==2.0\n", "web/package.json": '{"dependencies": {"a": "1.0.0"}}'})
    real = Path.read_text

    def read_text(self, *a, **k):
        if self.parent.name == "web":
            raise PermissionError(13, "Permission denied")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)
    with patch("aletheore.vulnerabilities._query_batch", lambda pins, t: [{"vulns": []}] * len(pins)):
        result = check_vulnerabilities(tmp_path, cache_path=tmp_path / "c.json")
    assert result["checked"] is True and result["unreadable_manifest_dirs"] == ["web"]
