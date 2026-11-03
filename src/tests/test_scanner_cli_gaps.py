"""Known gaps closed after the scanner and CLI audit: write failures, tsconfig aliases,
Go workspaces and local replaces, schema declarations, Windows-style citations, MCP
argument descriptions and monorepo detection."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheore import cli
from aletheore.air_schema import AIR_JSON_SCHEMA, validate_evidence
from aletheore.citation_verifier import extract_citations, verify_citations
from aletheore.cli import app
from aletheore.evidence import (
    EVIDENCE_VERSION,
    EvidenceWriteError,
    check_evidence_writable,
    is_evidence_version_compatible,
    scan_repository,
)
from aletheore.manifests import clear_manifest_cache
from aletheore.scanner.detect import detect_monorepo, git_visible_paths
from aletheore.scanner.graph import _strip_jsonc, build_module_graph
from tests.air_fixtures import minimal_air_evidence

runner = CliRunner(env={"COLUMNS": "1000"})

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
not_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="permission tests need a non-root user"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")
    git_visible_paths.cache_clear()
    clear_manifest_cache()
    yield
    git_visible_paths.cache_clear()
    clear_manifest_cache()


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def _edges(repo: Path) -> set[tuple[str, str]]:
    _modules, graph, _bad = build_module_graph(repo)
    return {(a, b) for a, b in graph["edges"]}


# --------------------------------------------------------------------------- 1. write failures


def _small_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    return repo


def _deny_probe(monkeypatch):
    def fail(repo_path):
        raise EvidenceWriteError(f"cannot write evidence to {repo_path}/.aletheore: permission denied", 13)

    monkeypatch.setattr(cli, "check_evidence_writable", fail)


def _no_traceback(result) -> None:
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    assert "Traceback" not in result.output


def test_scan_reports_an_unwritable_evidence_folder_before_scanning(tmp_path, monkeypatch):
    repo = _small_repo(tmp_path)
    _deny_probe(monkeypatch)

    def scan_must_not_run(*args, **kwargs):
        raise AssertionError("the scan ran although .aletheore/ cannot be written")

    monkeypatch.setattr(cli, "scan_repository", scan_must_not_run)
    result = runner.invoke(app, ["scan", str(repo)])
    assert result.exit_code == 1
    assert "cannot write evidence to" in result.output and "permission denied" in result.output
    _no_traceback(result)


def test_scan_reports_a_write_failure_that_happens_after_the_scan(tmp_path, monkeypatch):
    repo = _small_repo(tmp_path)

    def denied(path, text):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("aletheore.evidence._atomic_write_text", denied)
    result = runner.invoke(
        app,
        ["scan", str(repo), "--no-check-vulnerabilities", "--no-check-licenses", "--no-scan-git-history",
         "--no-check-static-analysis"],
    )
    assert result.exit_code == 1
    assert "cannot write evidence to" in result.output
    _no_traceback(result)


def test_scan_reports_a_snapshot_write_failure(tmp_path, monkeypatch):
    repo = _small_repo(tmp_path)

    def denied(evidence, repo_path, keep=20):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "save_snapshot", denied)
    result = runner.invoke(
        app,
        ["scan", str(repo), "--no-check-vulnerabilities", "--no-check-licenses", "--no-scan-git-history",
         "--no-check-static-analysis"],
    )
    assert result.exit_code == 1
    assert "snapshot could not be saved" in result.output and "permission denied" in result.output
    _no_traceback(result)


@posix_only
@not_root
def test_a_read_only_repository_folder_fails_fast_with_the_same_message(tmp_path):
    repo = _small_repo(tmp_path)
    repo.chmod(0o555)
    try:
        with pytest.raises(EvidenceWriteError) as excinfo:
            check_evidence_writable(repo)
        result = runner.invoke(app, ["scan", str(repo)])
    finally:
        repo.chmod(0o755)
    assert str(excinfo.value).startswith("cannot write evidence to ")
    assert result.exit_code == 1
    assert "cannot write evidence to" in result.output
    assert "Scanning" not in result.output
    _no_traceback(result)


def test_check_evidence_writable_leaves_no_probe_file_behind(tmp_path):
    repo = _small_repo(tmp_path)
    check_evidence_writable(repo)
    assert list((repo / ".aletheore").iterdir()) == []


def test_check_evidence_writable_names_a_file_in_the_way(tmp_path):
    repo = _small_repo(tmp_path)
    (repo / ".aletheore").write_text("not a folder", encoding="utf-8")
    with pytest.raises(EvidenceWriteError) as excinfo:
        check_evidence_writable(repo)
    assert "cannot write evidence to" in str(excinfo.value)


def test_audit_stops_with_the_same_message_when_evidence_cannot_be_written(tmp_path, monkeypatch):
    repo = _small_repo(tmp_path)
    _deny_probe(monkeypatch)
    result = runner.invoke(app, ["audit", str(repo)])
    assert result.exit_code == 1
    assert "cannot write evidence to" in result.output
    _no_traceback(result)


def test_watch_stops_with_the_same_message_when_the_first_scan_cannot_be_written(tmp_path, monkeypatch):
    repo = _small_repo(tmp_path)
    _deny_probe(monkeypatch)
    result = runner.invoke(app, ["watch", str(repo)])
    assert result.exit_code == 1
    assert "cannot write evidence to" in result.output
    _no_traceback(result)


def test_the_watch_loop_reports_a_write_failure_in_one_readable_line(tmp_path, monkeypatch):
    from aletheore import watch as watch_module

    repo = _small_repo(tmp_path)

    def failing_rebuild(repo_path, report, allow_hosted=True):
        raise EvidenceWriteError("cannot write evidence to X/.aletheore: permission denied", 13)

    messages: list[str] = []
    monkeypatch.setattr(watch_module, "rebuild", failing_rebuild)
    stop = threading.Event()

    def report(message: str) -> None:
        messages.append(message)
        if message.startswith("rebuild failed"):
            stop.set()

    class OneBatch:
        def __init__(self, *args, **kwargs):
            self.sent = False

        def take_settled_batch(self, debounce_seconds):
            if self.sent:
                return None
            self.sent = True
            return {repo / "a.py"}

    monkeypatch.setattr(watch_module, "_DebouncedHandler", OneBatch)
    monkeypatch.setattr(watch_module, "_schedule_watches", lambda *a, **k: None)
    watch_module.watch(repo, report, debounce_seconds=0.0, stop=stop, poll_seconds=0.01)
    failed = [m for m in messages if m.startswith("rebuild failed")]
    assert failed == ["rebuild failed: cannot write evidence to X/.aletheore: permission denied"]


def test_init_reports_a_config_file_it_cannot_write(tmp_path, monkeypatch):
    repo = _small_repo(tmp_path)
    original = Path.write_text

    def deny_config(self, *args, **kwargs):
        if self.name == ".aletheore.json":
            raise PermissionError(13, "Permission denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", deny_config)
    result = runner.invoke(app, ["init", str(repo)])
    assert result.exit_code == 1
    assert "could not write" in result.output and "permission denied" in result.output
    _no_traceback(result)


@posix_only
@not_root
def test_init_in_a_read_only_folder_reports_it(tmp_path):
    repo = _small_repo(tmp_path)
    repo.chmod(0o555)
    try:
        result = runner.invoke(app, ["init", str(repo)])
    finally:
        repo.chmod(0o755)
    assert result.exit_code == 1
    assert "could not write" in result.output
    _no_traceback(result)


def test_mcp_install_reports_a_config_it_cannot_write(tmp_path, monkeypatch):
    repo = _small_repo(tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "_write_json_mcp_client_config", denied)
    result = runner.invoke(app, ["mcp-install", str(repo), "--target", "claude-code"])
    assert result.exit_code == 1
    assert "could not write" in result.output and "permission denied" in result.output
    _no_traceback(result)


def test_managed_audit_report_write_failure_is_reported(tmp_path, monkeypatch):
    repo = _small_repo(tmp_path)
    (repo / ".aletheore").mkdir()
    monkeypatch.setattr(cli, "_scan", lambda *a, **k: (0, {}, repo / ".aletheore" / "air.json"))
    monkeypatch.setattr(cli, "run_managed_audit_request", lambda *a, **k: "# report")
    monkeypatch.setattr(cli, "infer_repo_full_name_from_cwd_git_remote", lambda cwd=None: None)
    original = Path.write_text

    def deny_report(self, *args, **kwargs):
        if self.name == "audit-report.md":
            raise PermissionError(13, "Permission denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", deny_report)
    exit_code = cli._managed_audit(str(repo), "token", False, False)
    assert exit_code == 1


def test_the_mcp_scan_tool_returns_the_write_error_as_a_tool_error(tmp_path, monkeypatch):
    from aletheore import mcp_server

    repo = _small_repo(tmp_path)

    def denied(evidence, repo_path):
        raise EvidenceWriteError("cannot write evidence to X/.aletheore: permission denied", 13)

    monkeypatch.setattr(mcp_server, "write_evidence", denied)
    server = mcp_server.build_server(repo, allow=mcp_server._ALL_EFFECTS)
    result = asyncio.run(
        server.call_tool(
            "aletheore_scan",
            {"check_vulnerabilities": False, "scan_git_history": False, "check_licenses": False},
        )
    )
    assert result.is_error is True
    assert "cannot write evidence to" in result.content[0].text


# --------------------------------------------------------------------------- 2. tsconfig aliases


def test_strip_jsonc_handles_comments_trailing_commas_and_strings():
    text = """\ufeff{
      // line comment
      "a": "http://example.com/*not a comment*/",
      /* block
         comment */ "b": [1, 2,],
      "c": {"d": "x",},
    }"""
    assert json.loads(_strip_jsonc(text)) == {
        "a": "http://example.com/*not a comment*/", "b": [1, 2], "c": {"d": "x"},
    }


def test_tsconfig_paths_and_base_url_become_graph_edges(tmp_path):
    _write(tmp_path, {
        "tsconfig.json": """{
          // jsonc: comments and a trailing comma are allowed
          "compilerOptions": {
            "baseUrl": ".",
            "paths": {"@app/*": ["src/app/*"], "@config": ["src/config/index.ts"],},
          },
        }""",
        "src/main.ts": 'import { a } from "@app/a";\nimport cfg from "@config";\nimport { u } from "src/util";\n',
        "src/app/a.ts": "export const a = 1;\n",
        "src/config/index.ts": "export default {};\n",
        "src/util.ts": "export const u = 1;\n",
    })
    assert _edges(tmp_path) == {
        ("src/main.ts", "src/app/a.ts"),
        ("src/main.ts", "src/config/index.ts"),
        ("src/main.ts", "src/util.ts"),
    }


def test_tsconfig_paths_without_base_url_resolve_against_the_config_folder(tmp_path):
    _write(tmp_path, {
        "web/tsconfig.json": '{"compilerOptions": {"paths": {"~/*": ["./src/*"]}}}',
        "web/src/index.ts": 'import { x } from "~/lib/x";\n',
        "web/src/lib/x.ts": "export const x = 1;\n",
    })
    assert _edges(tmp_path) == {("web/src/index.ts", "web/src/lib/x.ts")}


def test_tsconfig_extends_is_followed_and_a_child_can_override_paths(tmp_path):
    _write(tmp_path, {
        "tsconfig.base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@shared/*": ["shared/*"]}}}',
        "apps/web/tsconfig.json": '{"extends": "../../tsconfig.base", "compilerOptions": {"strict": true}}',
        "apps/web/main.ts": 'import { s } from "@shared/s";\n',
        "shared/s.ts": "export const s = 1;\n",
        # Overrides the inherited paths wholesale, as TypeScript does; they resolve against the
        # inherited baseUrl (the repository root), not against the folder of this config.
        "apps/admin/tsconfig.json": (
            '{"extends": "../../tsconfig.base.json", "compilerOptions": {"paths": {"@shared/*": ["other/*"]}}}'
        ),
        "apps/admin/main.ts": 'import { s } from "@shared/s";\n',
        "other/s.ts": "export const s = 2;\n",
    })
    edges = _edges(tmp_path)
    assert ("apps/web/main.ts", "shared/s.ts") in edges
    assert ("apps/admin/main.ts", "other/s.ts") in edges
    assert ("apps/admin/main.ts", "shared/s.ts") not in edges


def test_tsconfig_extends_list_and_cycles_are_safe(tmp_path):
    _write(tmp_path, {
        "tsconfig.json": '{"extends": ["./a.json", "./b.json"]}',
        "a.json": '{"extends": "./b.json", "compilerOptions": {"baseUrl": "."}}',
        "b.json": '{"extends": "./a.json", "compilerOptions": {"paths": {"@l/*": ["lib/*"]}}}',
        "main.ts": 'import { l } from "@l/l";\n',
        "lib/l.ts": "export const l = 1;\n",
    })
    assert ("main.ts", "lib/l.ts") in _edges(tmp_path)


def test_nested_tsconfigs_apply_to_their_own_package(tmp_path):
    _write(tmp_path, {
        "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["root-src/*"]}}}',
        "root-src/r.ts": "export const r = 1;\n",
        "root.ts": 'import { r } from "@/r";\n',
        "packages/a/tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}',
        "packages/a/src/a.ts": "export const a = 1;\n",
        "packages/a/index.ts": 'import { a } from "@/a";\n',
        # No tsconfig of its own: falls back to the root one.
        "packages/b/b.ts": 'import { r } from "@/r";\n',
    })
    assert _edges(tmp_path) == {
        ("root.ts", "root-src/r.ts"),
        ("packages/a/index.ts", "packages/a/src/a.ts"),
        ("packages/b/b.ts", "root-src/r.ts"),
    }


def test_an_alias_to_a_missing_file_or_into_node_modules_adds_no_edge(tmp_path):
    _write(tmp_path, {
        "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@m/*": ["missing/*"], "dep": ["node_modules/dep/index.ts"]}}}',
        "main.ts": 'import a from "@m/a";\nimport d from "dep";\nimport r from "react";\n',
        "node_modules/dep/index.ts": "export default 1;\n",
    })
    assert _edges(tmp_path) == set()


def test_the_longest_matching_alias_prefix_wins(tmp_path):
    _write(tmp_path, {
        "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["a/*"], "@/ui/*": ["ui-kit/*"]}}}',
        "main.ts": 'import b from "@/ui/button";\n',
        "ui-kit/button.tsx": "export default 1;\n",
        "a/ui/button.ts": "export default 2;\n",
    })
    assert _edges(tmp_path) == {("main.ts", "ui-kit/button.tsx")}


def test_an_alias_wins_over_a_workspace_package_of_the_same_name(tmp_path):
    _write(tmp_path, {
        "package.json": '{"name": "root", "workspaces": ["packages/*"]}',
        "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@acme/core": ["local/core.ts"]}}}',
        "packages/core/package.json": '{"name": "@acme/core", "main": "index.ts"}',
        "packages/core/index.ts": "export const c = 1;\n",
        "local/core.ts": "export const c = 2;\n",
        "main.ts": 'import { c } from "@acme/core";\n',
    })
    assert _edges(tmp_path) == {("main.ts", "local/core.ts")}


# --------------------------------------------------------------------------- 3. Go workspaces


GO_IMPORTER = 'package main\n\nimport "example.com/lib/sub"\n\nfunc main() { sub.Run() }\n'


def test_go_work_use_directives_resolve_modules_in_directories_discovery_skips(tmp_path):
    _write(tmp_path, {
        "go.work": "go 1.21\n\nuse (\n\t./app\n\t./third_party/lib // vendored copy\n)\n",
        "app/go.mod": "module example.com/app\n",
        "app/main.go": GO_IMPORTER,
        "third_party/lib/go.mod": "module example.com/lib\n",
        "third_party/lib/sub/sub.go": "package sub\n\nfunc Run() {}\n",
    })
    assert _edges(tmp_path) == {("app/main.go", "third_party/lib/sub/sub.go")}


def test_go_work_single_line_use(tmp_path):
    _write(tmp_path, {
        "go.work": "go 1.21\nuse ./third_party/lib\n",
        "main.go": GO_IMPORTER.replace("package main", "package main"),
        "go.mod": "module example.com/root\n",
        "third_party/lib/go.mod": "module example.com/lib\n",
        "third_party/lib/sub/sub.go": "package sub\n",
    })
    assert ("main.go", "third_party/lib/sub/sub.go") in _edges(tmp_path)


def test_go_mod_replace_to_a_local_directory_resolves_in_repo(tmp_path):
    _write(tmp_path, {
        "go.mod": "module example.com/root\n\nrequire example.com/lib v1.0.0\n\nreplace example.com/lib => ./local/lib\n",
        "main.go": GO_IMPORTER,
        # The replacement directory has no go.mod of its own.
        "local/lib/sub/sub.go": "package sub\n",
    })
    assert _edges(tmp_path) == {("main.go", "local/lib/sub/sub.go")}


def test_go_mod_replace_outside_the_repository_stays_external(tmp_path):
    _write(tmp_path, {
        "go.mod": "module example.com/root\n\nrequire example.com/lib v1.0.0\nreplace example.com/lib => ../elsewhere\n",
        "main.go": GO_IMPORTER,
        "local/lib/sub/sub.go": "package sub\n",
    })
    assert _edges(tmp_path) == set()


def test_go_mod_replace_block_and_versioned_form(tmp_path):
    _write(tmp_path, {
        "services/api/go.mod": (
            "module example.com/api\n\nreplace (\n\texample.com/lib v1.2.3 => ../../libs/lib\n"
            "\texample.com/other => github.com/fork/other v1.0.0\n)\n"
        ),
        "services/api/main.go": GO_IMPORTER,
        "libs/lib/go.mod": "module example.com/lib\n",
        "libs/lib/sub/sub.go": "package sub\n",
    })
    assert ("services/api/main.go", "libs/lib/sub/sub.go") in _edges(tmp_path)


# --------------------------------------------------------------------------- 4. schema and version


NEW_OPTIONAL_PATHS = [
    ("cli_version",),
    ("config_warnings",),
    ("repository", "unreadable_files"),
    ("repository", "monorepo", "packages"),
    ("repository", "monorepo", "reason"),
    ("repository", "monorepo", "manifests_truncated"),
    ("security", "dependency_vulnerabilities", "manifest_dirs"),
    ("security", "dependency_vulnerabilities", "unreadable_manifest_dirs"),
    ("security", "dependency_licenses", "manifest_dirs"),
    ("security", "dependency_licenses", "unreadable_manifest_dirs"),
    ("git", "reason"),
]


@pytest.mark.parametrize("path", NEW_OPTIONAL_PATHS, ids=lambda p: ".".join(p))
def test_new_evidence_fields_are_declared_and_optional(path):
    node = AIR_JSON_SCHEMA
    for key in path[:-1]:
        node = node["properties"][key]
    assert path[-1] in node["properties"], f"{'.'.join(path)} is not declared"
    assert path[-1] not in node.get("required", []), f"{'.'.join(path)} must stay optional"


def test_evidence_without_the_new_fields_is_still_valid():
    evidence = minimal_air_evidence()
    for path in NEW_OPTIONAL_PATHS:
        node = evidence
        for key in path[:-1]:
            node = node.get(key, {})
        node.pop(path[-1], None)
    assert validate_evidence(evidence, deep=True) == []


def test_a_real_scan_with_the_new_fields_conforms_to_the_schema(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, {
        "a.py": "x = 1\n",
        "requirements.txt": "flask==3.0.0\n",
        "web/package.json": '{"name": "web"}',
        ".aletheore.json": '{"mystery": 1}',
    })
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    evidence = scan_repository(
        repo, check_vulnerabilities=False, scan_git_history=False, check_licenses=False,
        check_static_analysis=False,
    )
    assert validate_evidence(evidence, deep=True) == []
    assert evidence["cli_version"] and evidence["config_warnings"]
    assert "packages" in evidence["repository"]["monorepo"]
    assert evidence["aletheore_version"] == EVIDENCE_VERSION


def test_older_additive_minor_is_readable_but_not_a_different_one():
    assert is_evidence_version_compatible("0.8.1") is True
    assert is_evidence_version_compatible("0.7.0") is False


# --------------------------------------------------------------------------- 5. Windows citations


def _citation_evidence() -> dict:
    evidence = minimal_air_evidence()
    evidence["repository"]["modules"] = [
        {"path": "app/util.py", "language": "python", "imports": [], "imported_by": []},
        {"path": "src/deep/mod.py", "language": "python", "imports": [], "imported_by": []},
    ]
    return evidence


def test_backslash_citations_are_normalised_and_verified():
    text = "See `app\\util.py:5` and src\\deep\\mod.py:3-4, plus .\\app\\util.py:2."
    citations = extract_citations(text)
    assert {(c["file"], c["line"], c.get("end_line")) for c in citations} == {
        ("app/util.py", 5, None), ("src/deep/mod.py", 3, 4), ("app/util.py", 2, None),
    }
    result = verify_citations(text, _citation_evidence())
    assert result["all_verified"] is True and result["total_citations"] == 3


def test_backslash_citations_keep_the_bounds_check():
    result = verify_citations(
        "Look at app\\util.py:50.", _citation_evidence(), fetch_line_count=lambda path: 10 if path == "app/util.py" else None
    )
    assert result["all_verified"] is False
    assert result["unverified"][0]["reason"] == "file has 10 lines"


@pytest.mark.parametrize(
    "text, expected_file",
    [
        ("`..\\..\\etc\\passwd:1`", "../../etc/passwd"),
        ("C:\\Windows\\system32\\evil.dll:7", "/Windows/system32/evil.dll"),
        ("\\\\server\\share\\secrets.txt:2", "/server/share/secrets.txt"),
    ],
)
def test_backslash_citations_outside_the_repo_are_still_flagged(text, expected_file):
    result = verify_citations(text, _citation_evidence())
    assert result["all_verified"] is False
    flagged = result["unverified"][0]
    assert flagged["file"] == expected_file
    assert flagged["reason"] == "path outside the repository"


def test_prose_with_backslashes_is_not_a_citation():
    assert extract_citations("Escape with \\n and use C:\\temp or a\\b for paths.") == []


# --------------------------------------------------------------------------- 6. MCP argument descriptions


def _all_tools(tmp_path: Path, profile: str):
    from aletheore import mcp_server
    from aletheore.adapters.base import AgentAdapter

    class StubAdapter(AgentAdapter):
        name = "stub"
        requires_consent = False

        def __init__(self):
            pass

        def is_available(self):
            return True

        def invoke(self, *args, **kwargs):
            return ""

    repo = _small_repo(tmp_path)
    server = mcp_server.build_server(
        repo, answer_adapter=StubAdapter(), allow=mcp_server._ALL_EFFECTS, profile=profile
    )
    return asyncio.run(server.list_tools())


@pytest.mark.parametrize("profile", ["full", "nav"])
def test_every_mcp_tool_argument_has_a_description(tmp_path, profile):
    tools = _all_tools(tmp_path, profile)
    assert tools
    missing = []
    for tool in tools:
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema")
        for name, prop in schema.get("properties", {}).items():
            if not str(prop.get("description", "")).strip():
                missing.append(f"{tool.name}.{name}")
    assert missing == []


def test_mcp_argument_names_are_unchanged(tmp_path):
    names = {}
    for tool in _all_tools(tmp_path, "full"):
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema")
        names[tool.name] = set(schema.get("properties", {}))
    assert names["aletheore_imports"] == {"target"}
    assert names["aletheore_page"] == {"tool", "target", "section", "offset", "limit", "filter"}
    assert names["aletheore_search"] == {"pattern", "regex", "path_glob"}
    assert names["aletheore_symbol_source"] == {"module", "symbol", "max_lines"}
    assert names["aletheore_branch"] >= {"target"}


def test_target_is_described_differently_for_a_branch_and_a_file(tmp_path):
    described = {}
    for tool in _all_tools(tmp_path, "full"):
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema")
        if "target" in schema.get("properties", {}):
            described[tool.name] = schema["properties"]["target"]["description"]
    assert "branch" in described["aletheore_branch"].lower()
    assert "file path" in described["aletheore_imports"].lower()


def test_the_description_table_names_no_unknown_tool_or_argument(tmp_path):
    from aletheore.mcp_arguments import ARGUMENT_DESCRIPTIONS

    real = {}
    for tool in _all_tools(tmp_path, "full"):
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema")
        real[tool.name] = set(schema.get("properties", {}))
    for tool_name, arguments in ARGUMENT_DESCRIPTIONS.items():
        assert tool_name in real, f"{tool_name} is not a registered tool"
        assert set(arguments) <= real[tool_name], f"{tool_name} describes an argument it does not have"


# --------------------------------------------------------------------------- 7. monorepo detection


def test_a_plain_python_repo_with_docs_requirements_is_not_a_monorepo(tmp_path):
    _write(tmp_path, {
        "pyproject.toml": '[project]\nname = "lib"\n',
        "docs/requirements.txt": "sphinx\n",
        "examples/demo/requirements.txt": "requests\n",
        "tests/fixtures/app/package.json": "{}",
    })
    result = detect_monorepo(tmp_path)
    assert result["detected"] is False and result["reason"] is None
    # Still listed, just not counted.
    assert {p["path"] for p in result["packages"]} >= {".", "docs", "examples/demo"}


def test_two_real_packages_are_still_a_monorepo(tmp_path):
    _write(tmp_path, {"services/a/go.mod": "module a\n", "services/b/go.mod": "module b\n"})
    assert detect_monorepo(tmp_path)["detected"] is True


def test_a_root_manifest_plus_a_real_nested_package_is_a_monorepo(tmp_path):
    _write(tmp_path, {"package.json": '{"name": "root"}', "tools/cli/package.json": '{"name": "cli"}'})
    assert detect_monorepo(tmp_path)["detected"] is True


def test_declared_workspaces_in_an_examples_folder_still_count(tmp_path):
    _write(tmp_path, {
        "package.json": '{"name": "root", "workspaces": ["examples/*"]}',
        "examples/one/package.json": '{"name": "one"}',
    })
    result = detect_monorepo(tmp_path)
    assert result["detected"] is True and result["reason"] == "workspaces"


def test_go_mod_replace_applies_only_to_the_module_that_declares_it(tmp_path):
    _write(tmp_path, {
        "a/go.mod": "module example.com/a\n\nreplace example.com/lib => ../libs/lib\n",
        "a/main.go": GO_IMPORTER,
        "b/go.mod": "module example.com/b\n",
        "b/main.go": GO_IMPORTER,
        "libs/lib/sub/sub.go": "package sub\n",
    })
    edges = _edges(tmp_path)
    assert ("a/main.go", "libs/lib/sub/sub.go") in edges
    assert ("b/main.go", "libs/lib/sub/sub.go") not in edges


def test_extended_length_windows_citations_are_outside_the_repo():
    result = verify_citations("see \\\\?\\C:\\repo\\x.py:7", _citation_evidence())
    assert result["all_verified"] is False
    assert result["unverified"][0]["reason"] == "path outside the repository"


@posix_only
@not_root
def test_an_existing_read_only_air_json_fails_the_early_check(tmp_path):
    repo = _small_repo(tmp_path)
    (repo / ".aletheore").mkdir()
    air = repo / ".aletheore" / "air.json"
    air.write_text("{}", encoding="utf-8")
    air.chmod(0o444)
    try:
        with pytest.raises(EvidenceWriteError):
            check_evidence_writable(repo)
    finally:
        air.chmod(0o644)


def test_a_root_go_mod_replace_does_not_reach_a_nested_module(tmp_path):
    _write(tmp_path, {
        "go.mod": "module example.com/root\n\nreplace example.com/lib => ./vendored/lib\n",
        "main.go": GO_IMPORTER,
        "nested/go.mod": "module example.com/nested\n",
        "nested/main.go": GO_IMPORTER,
        "vendored/lib/sub/sub.go": "package sub\n",
    })
    edges = _edges(tmp_path)
    assert ("main.go", "vendored/lib/sub/sub.go") in edges
    assert ("nested/main.go", "vendored/lib/sub/sub.go") not in edges


def test_a_go_work_replace_wins_over_a_go_mod_replace_for_the_same_path(tmp_path):
    _write(tmp_path, {
        "go.work": "go 1.21\nuse .\nreplace example.com/lib => ./from-work\n",
        "go.mod": "module example.com/root\n\nreplace example.com/lib => ./from-mod\n",
        "main.go": GO_IMPORTER,
        "from-work/sub/sub.go": "package sub\n",
        "from-mod/sub/sub.go": "package sub\n",
    })
    assert _edges(tmp_path) == {("main.go", "from-work/sub/sub.go")}
