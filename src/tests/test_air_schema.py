import json
import subprocess
from pathlib import Path

import pytest

from aletheore.air_schema import (
    AIR_JSON_SCHEMA,
    schema_fingerprint,
    validate_evidence,
)
from aletheore.evidence import (
    EVIDENCE_VERSION,
    MalformedEvidenceError,
    load_evidence_file,
    scan_repository,
)
from tests.air_fixtures import minimal_air_evidence

# Bump both of these together, never one alone. If a schema change lands
# without a matching EVIDENCE_VERSION bump, every already-written air.json
# stays "compatible" by version check while actually having a different
# shape - which is exactly the silent drift the version field exists to
# prevent. See docs/AIR-SCHEMA.md for the migration rules.
EXPECTED_SCHEMA_FINGERPRINT = "ae1f3063d2364b26"
EXPECTED_EVIDENCE_VERSION = "0.3.0"


def test_schema_changes_require_an_evidence_version_bump():
    assert schema_fingerprint() == EXPECTED_SCHEMA_FINGERPRINT, (
        "The AIR schema changed. If this was deliberate: bump EVIDENCE_VERSION's "
        "MINOR in aletheore/evidence.py, update EXPECTED_EVIDENCE_VERSION and "
        "EXPECTED_SCHEMA_FINGERPRINT here, and record the change in "
        "docs/AIR-SCHEMA.md. Do not update the fingerprint alone."
    )
    assert EVIDENCE_VERSION == EXPECTED_EVIDENCE_VERSION


def test_schema_is_serializable_as_published_json_schema():
    # It is handed to external consumers verbatim, so it has to survive a
    # round trip through plain JSON.
    assert json.loads(json.dumps(AIR_JSON_SCHEMA)) == AIR_JSON_SCHEMA
    assert AIR_JSON_SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def _valid_evidence(**overrides) -> dict:
    """A valid document with `overrides` replacing whole sections, so each
    test can state the one malformed thing it is about.
    """
    return {**minimal_air_evidence(), **overrides}


def test_the_minimal_instance_helper_is_itself_valid():
    # Every test below rests on this, so it gets asserted rather than assumed.
    assert validate_evidence(_valid_evidence(), deep=True) == []


@pytest.fixture(scope="module")
def scanned_repo(tmp_path_factory):
    """A real scan of a real git repo - the schema is a claim about what
    scan_repository actually emits, so checking it against a hand-built dict
    would only prove the fixture matches the schema.
    """
    repo = tmp_path_factory.mktemp("air_schema_repo")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "core.py").write_text("import os\n\n\ndef run():\n    return os.getcwd()\n")
    (repo / "pkg" / "app.py").write_text("from pkg.core import run\n\n\ndef main():\n    return run()\n")
    (repo / "README.md").write_text("# fixture\n")

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    return repo, scan_repository(
        repo,
        check_vulnerabilities=False,
        scan_git_history=False,
        check_licenses=False,
    )


def test_scan_output_conforms_to_the_schema(scanned_repo):
    _repo, evidence = scanned_repo
    assert validate_evidence(evidence, deep=True) == []


def test_schema_covers_every_path_consumers_index(scanned_repo):
    """Guards the other direction: a schema that omitted a key consumers
    depend on would pass every conformance check while protecting nothing.

    These are the two-level paths found across the CLI, MCP server, and
    dashboard modules.
    """
    _repo, evidence = scanned_repo
    consumer_paths = [
        ("repository", "languages"),
        ("repository", "modules"),
        ("repository", "dependency_graph"),
        ("repository", "api_endpoints"),
        ("repository", "dead_code"),
        ("repository", "database"),
        ("repository", "monorepo"),
        ("repository", "infrastructure"),
        ("repository", "environment_variables"),
        ("git", "branches"),
        ("git", "commit_cadence"),
        ("git", "ownership"),
        ("git", "total_commits"),
        ("security", "secrets"),
        ("security", "dependency_vulnerabilities"),
        ("security", "dependency_licenses"),
        ("architecture", "clusters"),
        ("architecture", "layer_violations"),
    ]
    for section, key in consumer_paths:
        section_schema = AIR_JSON_SCHEMA["properties"][section]
        assert key in section_schema["properties"], (
            f"consumers read evidence['{section}']['{key}'] but the schema does not declare it"
        )
        assert key in evidence[section], f"scan output is missing {section}.{key}"


def test_validate_reports_a_missing_section_by_path():
    evidence = _valid_evidence()
    del evidence["repository"]
    del evidence["security"]

    problems = validate_evidence(evidence)

    assert any("evidence.repository: required key missing" in p for p in problems)
    assert any("evidence.security: required key missing" in p for p in problems)


def test_validate_reports_a_wrong_container_type():
    problems = validate_evidence(_valid_evidence(repository=[]))

    assert any(p.startswith("evidence.repository: expected object, got list") for p in problems)


def test_validate_rejects_a_boolean_where_a_count_belongs():
    # bool subclasses int in Python, so a naive isinstance check would let a
    # counter that started returning True through.
    problems = validate_evidence(_valid_evidence(git={"available": True, "total_commits": True}))

    assert any("evidence.git.total_commits" in p for p in problems)


def test_shallow_validation_ignores_array_items_but_deep_does_not():
    evidence = _valid_evidence()
    evidence["repository"]["languages"] = [{"name": "python"}]

    assert validate_evidence(evidence, deep=False) == []
    deep_problems = validate_evidence(evidence, deep=True)
    assert any("evidence.repository.languages[0].loc" in p for p in deep_problems)


def test_git_unavailable_repo_is_valid_with_only_the_available_flag():
    # A repo with no commits yields {"available": False} and nothing else.
    assert validate_evidence(_valid_evidence(git={"available": False})) == []


def test_load_evidence_file_rejects_malformed_evidence(tmp_path):
    path = tmp_path / "air.json"
    evidence = _valid_evidence(aletheore_version=EVIDENCE_VERSION, repository="not-an-object")
    path.write_text(json.dumps(evidence))

    with pytest.raises(MalformedEvidenceError) as excinfo:
        load_evidence_file(path)

    assert "does not match the AIR schema" in str(excinfo.value)
    assert "evidence.repository" in str(excinfo.value)


def test_load_evidence_file_accepts_a_real_scan(scanned_repo, tmp_path):
    _repo, evidence = scanned_repo
    path = tmp_path / "air.json"
    path.write_text(json.dumps(evidence))

    assert load_evidence_file(path)["aletheore_version"] == EVIDENCE_VERSION


def test_published_schema_file_matches_the_module(scanned_repo):
    """The checked-in schema file is what external consumers fetch. If it
    drifts from AIR_JSON_SCHEMA it is worse than not shipping one.
    """
    published = Path(__file__).resolve().parents[2] / "schemas" / "air.schema.json"
    assert published.exists(), f"{published} is missing - regenerate it from AIR_JSON_SCHEMA"
    assert json.loads(published.read_text()) == AIR_JSON_SCHEMA
