import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from aletheore.evidence import (
    EVIDENCE_VERSION,
    IncompatibleEvidenceVersionError,
    is_evidence_version_compatible,
    load_evidence,
    load_evidence_file,
    scan_repository,
    write_evidence,
)


def run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def hello():\n    return 1\n")
    (repo / "requirements.txt").write_text("fastapi==0.110.0\n")
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")
    run(repo, "add", ".")
    run(repo, "commit", "-m", "init")
    return repo


def test_is_evidence_version_compatible_accepts_the_current_version():
    assert is_evidence_version_compatible(EVIDENCE_VERSION) is True


def test_is_evidence_version_compatible_accepts_a_patch_difference():
    major, minor, _patch = EVIDENCE_VERSION.split(".")
    assert is_evidence_version_compatible(f"{major}.{minor}.99") is True


def test_is_evidence_version_compatible_rejects_a_minor_difference():
    major, minor, _patch = EVIDENCE_VERSION.split(".")
    assert is_evidence_version_compatible(f"{major}.{int(minor) + 1}.0") is False


def test_is_evidence_version_compatible_rejects_missing_or_malformed_versions():
    assert is_evidence_version_compatible(None) is False
    assert is_evidence_version_compatible("") is False
    assert is_evidence_version_compatible("not-a-version") is False
    assert is_evidence_version_compatible(1) is False


def test_load_evidence_file_returns_a_compatible_evidence_dict(tmp_path):
    evidence_path = tmp_path / "air.json"
    evidence_path.write_text(json.dumps({"aletheore_version": EVIDENCE_VERSION, "repository": {}}))

    assert load_evidence_file(evidence_path)["repository"] == {}


def test_load_evidence_file_rejects_an_incompatible_version(tmp_path):
    evidence_path = tmp_path / "air.json"
    evidence_path.write_text(json.dumps({"aletheore_version": "9.9.9", "repository": {}}))

    try:
        load_evidence_file(evidence_path)
        assert False, "expected IncompatibleEvidenceVersionError"
    except IncompatibleEvidenceVersionError as exc:
        assert "9.9.9" in str(exc)
        assert "re-run" in str(exc)


def test_load_evidence_file_rejects_a_missing_version(tmp_path):
    evidence_path = tmp_path / "air.json"
    evidence_path.write_text(json.dumps({"repository": {}}))

    try:
        load_evidence_file(evidence_path)
        assert False, "expected IncompatibleEvidenceVersionError"
    except IncompatibleEvidenceVersionError:
        pass


def test_load_evidence_raises_file_not_found_when_repo_never_scanned(tmp_path):
    try:
        load_evidence(tmp_path)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as exc:
        assert "aletheore scan" in str(exc)


def test_load_evidence_reads_the_repos_own_air_json(tmp_path):
    (tmp_path / ".aletheore").mkdir()
    (tmp_path / ".aletheore" / "air.json").write_text(
        json.dumps({"aletheore_version": EVIDENCE_VERSION, "repository": {"modules": []}})
    )

    assert load_evidence(tmp_path)["repository"]["modules"] == []


def test_scan_repository_produces_full_schema(tmp_path):
    repo = make_repo(tmp_path)
    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert evidence["aletheore_version"] == "0.1.0"
    assert "scanned_at" in evidence
    assert evidence["repo_path"] == str(repo)

    assert any(entry["name"] == "python" for entry in evidence["repository"]["languages"])
    assert any(entry["name"] == "fastapi" for entry in evidence["repository"]["frameworks"])
    assert evidence["repository"]["modules"][0]["path"] == "main.py"

    assert evidence["git"]["available"] is True
    assert evidence["git"]["total_commits"] == 1


def test_scan_repository_handles_no_git_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    evidence = scan_repository(repo, check_vulnerabilities=False, check_licenses=False)
    assert evidence["git"] == {"available": False}
    assert "dead_code" in evidence["repository"]
    assert "hotspots" not in evidence["git"]


def test_scan_repository_includes_dead_code_and_hotspots(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    (repo / "main.py").write_text("def run():\n    pass\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)

    evidence = scan_repository(
        repo,
        check_vulnerabilities=False,
        scan_git_history=False,
        check_licenses=False,
        map_endpoints=False,
    )

    assert "dead_code" in evidence["repository"]
    assert "unreachable_modules" in evidence["repository"]["dead_code"]
    assert "hotspots" in evidence["git"]
    assert evidence["git"]["hotspots"][0]["path"] == "main.py"


def test_scan_repository_honors_git_history_depth_cap_env_var(tmp_path, monkeypatch):
    # Proves the hosted scan-worker's ALETHEORE_GIT_HISTORY_DEPTH_CAP env
    # var (set before invoking `aletheore scan` as a subprocess, see
    # scan_worker/jobs.py's _run_scan) actually reaches analyze_git - this
    # is what keeps a cold sync of an oversized repo from OOMing before any
    # persistence-layer code even runs. Unset by default for a developer
    # scanning their own repo directly.
    repo = make_repo(tmp_path)
    for i in range(4):
        (repo / "main.py").write_text(f"def hello():\n    return {i}\n")
        run(repo, "add", "-A")
        run(repo, "commit", "-q", "-m", f"change {i}")

    monkeypatch.setenv("ALETHEORE_GIT_HISTORY_DEPTH_CAP", "2")
    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert evidence["git"]["total_commits"] == 5
    assert evidence["git"]["history_depth_limited"] is True


def test_scan_repository_honors_secrets_history_depth_cap_env_var(tmp_path, monkeypatch):
    # Separate env var from the git-graph cap above - `git log -p` (full
    # diffs, used for secrets-in-history) is far more expensive per commit
    # than the graph engine's --name-only walk, so it's tunable
    # independently. Unset by default for a developer scanning locally.
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ALETHEORE_SECRETS_HISTORY_DEPTH_CAP", "7")
    with (
        patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check,
        patch("aletheore.evidence.find_secrets_in_history") as mock_history,
    ):
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        mock_history.return_value = {"history_scanned_commits": 0, "history_findings": []}
        scan_repository(repo, check_licenses=False)


def test_scan_repository_reuses_unchanged_scan_cache_env_var(tmp_path, monkeypatch):
    # Proves the hosted scan-worker's ALETHEORE_UNCHANGED_SCAN_CACHE env var
    # (a JSON file path, set before invoking `aletheore scan` as a
    # subprocess against a persistent per-repo checkout - see
    # scan_worker/jobs.py) actually reaches build_module_graph/
    # map_api_endpoints, so files known unchanged since the last scan are
    # never re-parsed. Unset by default for a developer scanning locally.
    repo = make_repo(tmp_path)
    (repo / "unchanged.py").write_text("def cached():\n    pass\n")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "add unchanged.py")

    # Deliberately WRONG relative to unchanged.py's real content ("def
    # cached(): pass") - proves the cached dict is used verbatim rather
    # than the file being re-parsed (a real parse would never produce
    # this name).
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps({
        "modules": {
            "unchanged.py": {
                "path": "unchanged.py",
                "language": "python",
                "imports": [],
                "imported_by": [],
                "symbols": {
                    "functions": [{"name": "definitely_not_a_real_parse", "start_line": 1, "end_line": 2}],
                    "classes": [],
                },
            }
        },
        "endpoints": {"unchanged.py": []},
    }))
    monkeypatch.setenv("ALETHEORE_UNCHANGED_SCAN_CACHE", str(cache_path))

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    by_path = {m["path"]: m for m in evidence["repository"]["modules"]}
    assert "definitely_not_a_real_parse" in [f["name"] for f in by_path["unchanged.py"]["symbols"]["functions"]]


def test_scan_repository_ignores_missing_unchanged_scan_cache_file(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ALETHEORE_UNCHANGED_SCAN_CACHE", str(tmp_path / "does-not-exist.json"))

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert evidence["repository"]["modules"]


def test_scan_repository_writes_a_local_scan_cache(tmp_path):
    # A plain local `aletheore scan` (no hosted-worker env var) had no
    # incremental path at all - every scan re-parsed every file from
    # scratch, even on an unchanged repo. This proves the CLI's own
    # self-contained cache actually gets written after a scan.
    repo = make_repo(tmp_path)
    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        scan_repository(repo, check_licenses=False)

    cache_path = repo / ".aletheore" / "scan-cache.json"
    assert cache_path.exists()
    cache = json.loads(cache_path.read_text())
    assert "main.py" in cache["hashes"]
    assert cache["modules"]["main.py"]["path"] == "main.py"


def test_scan_repository_reuses_local_scan_cache_for_an_unchanged_file(tmp_path):
    # Same "deliberately wrong cached data" proof as the hosted env-var
    # test above, but for the CLI's own automatic local cache: a second
    # scan of an unchanged file must reuse the cached module dict verbatim
    # rather than re-parsing, which a real parse could never produce.
    repo = make_repo(tmp_path)
    (repo / "unchanged.py").write_text("def cached():\n    pass\n")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "add unchanged.py")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        scan_repository(repo, check_licenses=False)

    cache_path = repo / ".aletheore" / "scan-cache.json"
    cache = json.loads(cache_path.read_text())
    cache["modules"]["unchanged.py"]["symbols"]["functions"] = [
        {"name": "definitely_not_a_real_parse", "start_line": 1, "end_line": 2}
    ]
    cache_path.write_text(json.dumps(cache))

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    by_path = {m["path"]: m for m in evidence["repository"]["modules"]}
    assert "definitely_not_a_real_parse" in [
        f["name"] for f in by_path["unchanged.py"]["symbols"]["functions"]
    ]


def test_scan_repository_reparses_a_file_that_changed_since_the_local_cache(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "changing.py").write_text("def before():\n    pass\n")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "add changing.py")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        scan_repository(repo, check_licenses=False)

    (repo / "changing.py").write_text("def after():\n    pass\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    by_path = {m["path"]: m for m in evidence["repository"]["modules"]}
    assert "after" in [f["name"] for f in by_path["changing.py"]["symbols"]["functions"]]
    assert "before" not in [f["name"] for f in by_path["changing.py"]["symbols"]["functions"]]


def test_scan_repository_ignores_local_cache_when_hosted_cache_env_var_is_set(tmp_path, monkeypatch):
    # The hosted worker's own cache must always take priority - a plain
    # local cache left over on the same machine (e.g. a developer testing
    # both paths) must never interfere with it.
    repo = make_repo(tmp_path)
    (repo / "unchanged.py").write_text("def cached():\n    pass\n")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "add unchanged.py")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        scan_repository(repo, check_licenses=False)

    local_cache_path = repo / ".aletheore" / "scan-cache.json"
    local_cache = json.loads(local_cache_path.read_text())
    local_cache["modules"]["unchanged.py"]["symbols"]["functions"] = [
        {"name": "should_never_be_used", "start_line": 1, "end_line": 2}
    ]
    local_cache_path.write_text(json.dumps(local_cache))

    hosted_cache_path = tmp_path / "hosted-cache.json"
    hosted_cache_path.write_text(json.dumps({"modules": {}, "endpoints": {}}))
    monkeypatch.setenv("ALETHEORE_UNCHANGED_SCAN_CACHE", str(hosted_cache_path))

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    by_path = {m["path"]: m for m in evidence["repository"]["modules"]}
    assert "should_never_be_used" not in [
        f["name"] for f in by_path["unchanged.py"]["symbols"]["functions"]
    ]
    assert "cached" in [f["name"] for f in by_path["unchanged.py"]["symbols"]["functions"]]


def test_write_evidence_creates_aletheore_dir(tmp_path):
    repo = make_repo(tmp_path)
    evidence = scan_repository(repo, check_vulnerabilities=False, check_licenses=False)
    written_path = write_evidence(evidence, repo)

    assert written_path == repo / ".aletheore" / "air.json"
    assert written_path.exists()
    loaded = json.loads(written_path.read_text())
    assert loaded["aletheore_version"] == "0.1.0"


def test_write_evidence_also_writes_a_toon_copy(tmp_path):
    import toon

    repo = make_repo(tmp_path)
    evidence = scan_repository(repo, check_vulnerabilities=False, check_licenses=False)
    write_evidence(evidence, repo)

    toon_path = repo / ".aletheore" / "air.toon"
    assert toon_path.exists()
    assert toon.decode(toon_path.read_text()) == evidence


def test_scan_repository_includes_security_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert "security" in evidence
    assert "secrets" in evidence["security"]
    assert evidence["security"]["secrets"]["scanned_files"] >= 1
    assert evidence["security"]["dependency_vulnerabilities"]["checked"] is True
    mock_check.assert_called_once()


def test_scan_repository_skips_vulnerability_check_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        evidence = scan_repository(repo, check_vulnerabilities=False, check_licenses=False)

    mock_check.assert_not_called()
    assert evidence["security"]["dependency_vulnerabilities"] == {
        "checked": False,
        "reason": "skipped (--no-check-vulnerabilities)",
        "findings": [],
    }


def test_scan_repository_includes_architecture_block(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "a.py").write_text("from app import b\n")
    (repo / "app" / "b.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert "architecture" in evidence
    assert "clusters" in evidence["architecture"]
    assert "cross_cluster_edges" in evidence["architecture"]
    assert "layer_violations" in evidence["architecture"]
    assert evidence["architecture"]["layer_violations"]["convention_detected"] is False


def test_scan_repository_includes_ai_usage_in_repository_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("openai==1.30.0\n")
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert "ai_usage" in evidence["repository"]
    names = {p["name"] for p in evidence["repository"]["ai_usage"]["providers"]}
    assert "openai" in names


def test_scan_repository_includes_database_in_repository_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("sqlalchemy==2.0.0\n")
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert "database" in evidence["repository"]
    names = {p["name"] for p in evidence["repository"]["database"]["orm_frameworks"]}
    assert "sqlalchemy" in names


def test_scan_repository_includes_infrastructure_and_environment_variables(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx\n")
    (repo / ".env.example").write_text("FOO=bar\n")
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert evidence["repository"]["infrastructure"]["docker_compose_services"] == [
        {"file": "docker-compose.yml", "services": ["web"]}
    ]
    assert evidence["repository"]["environment_variables"]["declared"] == [
        {"name": "FOO", "source": ".env.example"}
    ]


def test_scan_repository_includes_policy_docs_in_repository_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text("MIT")
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    names = {d["name"] for d in evidence["repository"]["policy_docs"]}
    assert "license" in names


def test_scan_repository_includes_history_findings_in_secrets_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    secrets = evidence["security"]["secrets"]
    assert "history_scanned_commits" in secrets
    assert "history_findings" in secrets


def test_scan_repository_skips_history_scan_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        with patch("aletheore.evidence.find_secrets_in_history") as mock_history:
            evidence = scan_repository(repo, scan_git_history=False, check_licenses=False)

    mock_history.assert_not_called()
    secrets = evidence["security"]["secrets"]
    assert secrets["history_scanned_commits"] == 0
    assert secrets["history_findings"] == []


def test_scan_repository_applies_aletheore_json_config(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app" / "biz").mkdir(parents=True)
    (repo / "app" / "routers").mkdir(parents=True)
    (repo / "app" / "biz" / "order.py").write_text("x = 1\n")
    (repo / "app" / "routers" / "orders.py").write_text("from app.biz import order\n")
    (repo / ".aletheore.json").write_text('{"layer_markers": {"biz": 1}}')

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, scan_git_history=False, check_licenses=False)

    assert evidence["architecture"]["config_applied"] == {
        "layer_markers": {"biz": 1},
        "cluster_resolution": 1.0,
    }
    assert evidence["architecture"]["layer_violations"]["convention_detected"] is True


def test_scan_repository_config_applied_is_none_without_aletheore_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, scan_git_history=False, check_licenses=False)

    assert evidence["architecture"]["config_applied"] is None


def test_scan_repository_applies_dead_code_entry_points_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "worker.py").write_text("def run():\n    pass\n")
    (repo / ".aletheore.json").write_text('{"dead_code_entry_points": ["worker.py"]}')

    evidence = scan_repository(repo, scan_git_history=False, check_licenses=False)

    assert "worker.py" in evidence["repository"]["dead_code"]["entry_points_detected"]
    assert evidence["repository"]["dead_code"]["unreachable_modules"] == []


def test_scan_repository_applies_a_secrets_baseline_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        first_scan = scan_repository(repo, scan_git_history=False, check_licenses=False)

    finding = first_scan["security"]["secrets"]["findings"][0]
    assert finding["accepted"] is False

    (repo / ".aletheore.json").write_text(
        json.dumps(
            {
                "accepted_secrets": [
                    {
                        "path": finding["path"],
                        "pattern": finding["pattern"],
                        "match_preview": finding["match_preview"],
                    }
                ]
            }
        )
    )

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        second_scan = scan_repository(repo, scan_git_history=False, check_licenses=False)

    assert second_scan["security"]["secrets"]["findings"][0]["accepted"] is True


def test_scan_repository_includes_dependency_licenses_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        with patch("aletheore.evidence.check_dependency_licenses") as mock_licenses:
            mock_licenses.return_value = {
                "checked": True,
                "reason": None,
                "repo_license": {"category": "permissive", "detected_from": "LICENSE text match"},
                "findings": [],
            }
            evidence = scan_repository(repo)

    mock_licenses.assert_called_once()
    assert evidence["security"]["dependency_licenses"]["checked"] is True
    assert evidence["security"]["dependency_licenses"]["repo_license"]["category"] == "permissive"


def test_scan_repository_skips_license_check_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        with patch("aletheore.evidence.check_dependency_licenses") as mock_licenses:
            evidence = scan_repository(repo, check_licenses=False)

    mock_licenses.assert_not_called()
    assert evidence["security"]["dependency_licenses"] == {
        "checked": False,
        "reason": "skipped (--no-check-licenses)",
        "repo_license": {"category": "unknown", "detected_from": None},
        "findings": [],
    }


def test_scan_repository_includes_api_endpoints_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text('@app.route("/users")\ndef list_users():\n    pass\n')

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert evidence["repository"]["api_endpoints"]["checked"] is True
    paths = {e["path"] for e in evidence["repository"]["api_endpoints"]["endpoints"]}
    assert "/users" in paths


def test_scan_repository_skips_endpoint_mapping_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text('@app.route("/users")\ndef list_users():\n    pass\n')

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False, map_endpoints=False)

    assert evidence["repository"]["api_endpoints"] == {
        "checked": False,
        "reason": "skipped (--no-map-endpoints)",
        "endpoints": [],
    }


def test_scan_repository_reports_progress_through_major_phases(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    messages = []
    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        scan_repository(repo, check_licenses=False, progress=messages.append)

    assert any("module dependency graph" in m for m in messages)
    assert any("git history" in m for m in messages)
    assert messages[-1] == "Done"


def test_scan_repository_progress_is_optional(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("aletheore.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert evidence["repository"]["languages"]
