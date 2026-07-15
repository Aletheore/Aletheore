import json
from pathlib import Path

from veridion.scanner.detect import (
    detect_ai_usage,
    detect_build_tools,
    detect_frameworks,
    detect_languages,
    detect_monorepo,
    detect_policy_docs,
)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "frontend").mkdir()
    (repo / "app" / "main.py").write_text("import os\n\ndef hello():\n    return 1\n")
    (repo / "app" / "other.py").write_text("x = 1\ny = 2\n")
    (repo / "frontend" / "index.js").write_text("console.log('hi')\n")
    (repo / "requirements.txt").write_text("fastapi==0.110.0\nuvicorn==0.29.0\n")
    (repo / "package.json").write_text(
        json.dumps({"name": "frontend", "dependencies": {"react": "^18.2.0"}})
    )
    return repo


def test_detect_languages_counts_files_and_loc(tmp_path):
    repo = make_repo(tmp_path)
    languages = detect_languages(repo)
    by_name = {entry["name"]: entry for entry in languages}
    assert by_name["python"]["file_count"] == 2
    assert by_name["python"]["loc"] == 6
    assert by_name["javascript"]["file_count"] == 1


def test_detect_frameworks_reads_requirements_txt(tmp_path):
    repo = make_repo(tmp_path)
    frameworks = detect_frameworks(repo)
    names = {f["name"] for f in frameworks}
    assert "fastapi" in names
    fastapi_entry = next(f for f in frameworks if f["name"] == "fastapi")
    assert fastapi_entry["evidence"] == "requirements.txt:fastapi==0.110.0"


def test_detect_frameworks_reads_package_json(tmp_path):
    repo = make_repo(tmp_path)
    frameworks = detect_frameworks(repo)
    names = {f["name"] for f in frameworks}
    assert "react" in names


def test_detect_build_tools_finds_dockerfile(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "Dockerfile").write_text("FROM python:3.11\n")
    tools = detect_build_tools(repo)
    names = {t["name"] for t in tools}
    assert "docker" in names


def test_detect_monorepo_detects_npm_workspaces(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "package.json").write_text(
        json.dumps({"name": "root", "workspaces": ["packages/*"]})
    )
    result = detect_monorepo(repo)
    assert result["detected"] is True
    assert result["workspaces"] == ["packages/*"]


def test_detect_monorepo_false_when_absent(tmp_path):
    repo = make_repo(tmp_path)
    result = detect_monorepo(repo)
    assert result["detected"] is False
    assert result["workspaces"] == []


def test_detect_languages_ignores_cache_dirs(tmp_path):
    repo = tmp_path / "repo"
    cache = repo / ".mypy_cache" / "3.12"
    cache.mkdir(parents=True)
    for i in range(50):
        (cache / f"mod{i}.json").write_text("{}")
    (repo / "main.py").write_text("x = 1\n")
    languages = detect_languages(repo)
    by_name = {entry["name"]: entry for entry in languages}
    assert by_name["python"]["file_count"] == 1


def test_detect_ai_usage_finds_a_provider_in_requirements_txt(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("openai==1.30.0\nrequests==2.31.0\n")

    result = detect_ai_usage(repo)

    names = {p["name"] for p in result["providers"]}
    assert "openai" in names
    entry = next(p for p in result["providers"] if p["name"] == "openai")
    assert entry["evidence"] == "requirements.txt:openai==1.30.0"


def test_detect_ai_usage_finds_orchestration_and_vector_store(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("langchain==0.2.0\nchromadb==0.5.0\n")

    result = detect_ai_usage(repo)

    assert {p["name"] for p in result["orchestration"]} == {"langchain"}
    assert {p["name"] for p in result["vector_stores"]} == {"chromadb"}


def test_detect_ai_usage_finds_local_inference_and_mcp(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("transformers==4.40.0\nmcp==1.0.0\n")

    result = detect_ai_usage(repo)

    assert {p["name"] for p in result["local_inference"]} == {"transformers"}
    assert {p["name"] for p in result["mcp"]} == {"mcp"}


def test_detect_ai_usage_reads_package_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@anthropic-ai/sdk": "^0.20.0",
                    "@modelcontextprotocol/sdk": "^1.0.0",
                }
            }
        )
    )

    result = detect_ai_usage(repo)

    assert {p["name"] for p in result["providers"]} == {"@anthropic-ai/sdk"}
    assert {p["name"] for p in result["mcp"]} == {"@modelcontextprotocol/sdk"}


def test_detect_ai_usage_empty_lists_when_nothing_matches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("requests==2.31.0\n")

    result = detect_ai_usage(repo)

    assert result == {
        "providers": [],
        "orchestration": [],
        "vector_stores": [],
        "local_inference": [],
        "mcp": [],
    }


def test_detect_policy_docs_finds_multiple_file_markers(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text("MIT")
    (repo / "SECURITY.md").write_text("# Security Policy\n")
    (repo / "README.md").write_text("# My Project\n")

    result = detect_policy_docs(repo)

    names = {d["name"] for d in result}
    assert names == {"license", "security_policy", "readme"}
    license_entry = next(d for d in result if d["name"] == "license")
    assert license_entry["evidence"] == "LICENSE"


def test_detect_policy_docs_detects_directory_markers(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs" / "security").mkdir(parents=True)

    result = detect_policy_docs(repo)

    assert any(
        d["name"] == "security_policy" and d["evidence"] == "docs/security" for d in result
    )


def test_detect_policy_docs_empty_when_nothing_present(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    result = detect_policy_docs(repo)

    assert result == []
