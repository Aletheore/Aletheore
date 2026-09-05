import json
from pathlib import Path

import yaml

from aletheore.scanner.detect import (
    detect_ai_usage,
    detect_build_tools,
    detect_database,
    detect_environment_variables,
    detect_frameworks,
    detect_infrastructure,
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


def test_nested_git_discovery_prunes_ignored_dependency_trees(tmp_path):
    from aletheore.scanner.detect import _nested_git_roots

    repo = tmp_path / "repo"
    (repo / "node_modules" / "nested" / ".git").mkdir(parents=True)
    (repo / "node_modules" / "nested" / "package.py").write_text("x = 1\n")
    (repo / "app.py").write_text("x = 1\n")
    _nested_git_roots.cache_clear()

    assert _nested_git_roots(repo) == set()
    assert [entry["file_count"] for entry in detect_languages(repo) if entry["name"] == "python"] == [1]


def test_nested_git_discovery_still_finds_a_directory_style_clone_outside_ignored_dirs(tmp_path):
    """IGNORED_DIRS contains ".git" itself (so a found root's own .git
    internals aren't walked) - pruning dirnames against IGNORED_DIRS before
    checking whether ".git" is present would silently disable directory-
    style nested-clone detection everywhere, not just inside ignored dirs."""
    from aletheore.scanner.detect import _nested_git_roots

    repo = tmp_path / "repo"
    (repo / "vendor" / "some-lib" / ".git").mkdir(parents=True)
    (repo / "vendor" / "some-lib" / "lib.py").write_text("x = 1\n")
    (repo / "app.py").write_text("x = 1\n")
    _nested_git_roots.cache_clear()

    assert _nested_git_roots(repo) == {repo / "vendor" / "some-lib"}


def test_detect_languages_does_not_follow_symlinked_directories(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (outside / "leaked.py").write_text("x = 1\n")
    (repo / "linked").symlink_to(outside, target_is_directory=True)
    (repo / "main.py").write_text("x = 1\n")

    python_entries = [entry for entry in detect_languages(repo) if entry["name"] == "python"]
    assert python_entries[0]["file_count"] == 1


def test_detect_languages_returns_a_sorted_list(tmp_path):
    # Built from _iter_source_files' raw filesystem-walk order, which is
    # filesystem-dependent (confirmed elsewhere in this file: the same repo
    # produces differently-ordered results on APFS vs ext4) - must sort its
    # own output by name rather than pass through walk order, the same fix
    # already applied to the other detectors below.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "z.go").write_text("package main\n")
    (repo / "a.py").write_text("x = 1\n")
    (repo / "m.rb").write_text("x = 1\n")

    languages = detect_languages(repo)

    names = [entry["name"] for entry in languages]
    assert names == sorted(names)
    assert names == ["go", "python", "ruby"]


def test_detect_languages_covers_every_module_graph_language(tmp_path):
    # detect_languages() used to keep its own, separately-maintained extension
    # mapping that fell out of sync with graph.py's - it silently reported zero
    # files for Rust, Java, Ruby, PHP, C, C++, and C# despite all of them being
    # fully supported by the module graph (found on a real scan: a C/C++-heavy
    # repo reported zero C or C++ in its language summary). It now reuses
    # graph.py's mapping directly, so anything the module graph supports must
    # show up here too.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.rs").write_text("fn main() {}\n")
    (repo / "Main.java").write_text("class Main {}\n")
    (repo / "app.rb").write_text("puts 1\n")
    (repo / "index.php").write_text("<?php echo 1; ?>\n")
    (repo / "main.c").write_text("int main() { return 0; }\n")
    (repo / "util.hpp").write_text("int add(int a, int b);\n")
    (repo / "Program.cs").write_text("class Program {}\n")

    languages = detect_languages(repo)
    by_name = {entry["name"]: entry for entry in languages}

    assert by_name["rust"]["file_count"] == 1
    assert by_name["java"]["file_count"] == 1
    assert by_name["ruby"]["file_count"] == 1
    assert by_name["php"]["file_count"] == 1
    assert by_name["c"]["file_count"] == 1
    assert by_name["cpp"]["file_count"] == 1
    assert by_name["csharp"]["file_count"] == 1


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


def test_match_dependency_markers_matches_pip_and_npm():
    from aletheore.scanner.detect import _match_dependency_markers

    pip_lines = [("sqlalchemy", "sqlalchemy==2.0.0", "requirements.txt")]
    npm_deps = {"Prisma": "^5.0.0"}
    matches = _match_dependency_markers(
        {"sqlalchemy": "sqlalchemy"}, {"prisma": "prisma"}, pip_lines, npm_deps
    )
    names = {m["name"] for m in matches}
    assert names == {"sqlalchemy", "prisma"}


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


def test_detect_languages_ignores_nested_git_worktree(tmp_path):
    # A linked git worktree (`git worktree add`) is a directory containing its own
    # `.git` file (not a directory - that's what distinguishes it from a submodule
    # checked out the classic way, but both are "a separate git working tree" for
    # this purpose) pointing back at the main repo's `.git/worktrees/<name>`. It can
    # be named anything - unlike cache dirs, there's no fixed name to add to
    # IGNORED_DIRS. Found on a real scan: a repo with a worktree at
    # `.claude/worktrees/<name>/` double-counted every file in it.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    worktree = repo / "some-custom-worktree-name"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/some-custom-worktree-name\n")
    (worktree / "main.py").write_text("x = 1\ny = 2\n")

    languages = detect_languages(repo)
    by_name = {entry["name"]: entry for entry in languages}
    assert by_name["python"]["file_count"] == 1


def test_detect_languages_does_not_follow_a_symlinked_file_outside_the_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.py").write_text("x = 1\n")
    (repo / "linked.py").symlink_to(tmp_path / "outside.py")
    (repo / "main.py").write_text("x = 1\n")

    languages = detect_languages(repo)
    by_name = {entry["name"]: entry for entry in languages}
    assert by_name["python"]["file_count"] == 1


def test_detect_languages_does_not_descend_into_a_symlinked_directory_outside_the_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "module.py").write_text("x = 1\n")
    (repo / "linked_dir").symlink_to(tmp_path / "outside")
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


def test_detect_frameworks_reads_pyproject_pep621_dependencies(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi>=0.110.0,<0.136.3"]\n'
    )
    frameworks = detect_frameworks(repo)
    names = {f["name"] for f in frameworks}
    assert "fastapi" in names
    entry = next(f for f in frameworks if f["name"] == "fastapi")
    assert entry["evidence"] == "pyproject.toml:fastapi>=0.110.0,<0.136.3"


def test_detect_ai_usage_reads_pyproject_poetry_dependencies(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\n'
        'python = "^3.11"\n'
        'openai = {version = "^1.30.0", extras = ["embeddings"]}\n'
    )
    result = detect_ai_usage(repo)
    names = {p["name"] for p in result["providers"]}
    assert "openai" in names
    entry = next(p for p in result["providers"] if p["name"] == "openai")
    assert entry["evidence"] == "pyproject.toml:openai ^1.30.0"
    assert not any(p["name"] == "python" for p in result["providers"])


def test_detect_frameworks_still_reads_requirements_txt_with_correct_source(tmp_path):
    repo = make_repo(tmp_path)
    frameworks = detect_frameworks(repo)
    entry = next(f for f in frameworks if f["name"] == "fastapi")
    assert entry["evidence"] == "requirements.txt:fastapi==0.110.0"


def test_detect_database_finds_orm_in_requirements_txt(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("sqlalchemy==2.0.0\n")

    result = detect_database(repo)

    names = {p["name"] for p in result["orm_frameworks"]}
    assert "sqlalchemy" in names


def test_detect_database_finds_orm_in_package_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"dependencies": {"prisma": "^5.0.0"}}))

    result = detect_database(repo)

    names = {p["name"] for p in result["orm_frameworks"]}
    assert "prisma" in names


def test_detect_database_finds_generic_migrations_directory(tmp_path):
    repo = tmp_path / "repo"
    migrations = repo / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "001_initial.sql").write_text("CREATE TABLE x (id INT);\n")
    (migrations / "002_add_column.sql").write_text("ALTER TABLE x ADD y INT;\n")
    (migrations / "README.md").write_text("not a migration\n")

    result = detect_database(repo)

    assert result["migration_directories"] == [{"path": "migrations", "file_count": 2}]


def test_detect_database_finds_flyway_style_singular_migration_directory(tmp_path):
    """Found via real-repo stress testing (killbill, a real Java/Flyway
    billing platform): "migration" (singular) is Flyway's own documented
    default convention (src/main/resources/db/migration,
    V<version>__<description>.sql) - without it, migration_directories
    came back empty for a real Flyway project, so schema_map.extract_schema
    was never even invoked with the right path."""
    repo = tmp_path / "repo"
    migration = repo / "src" / "main" / "resources" / "db" / "migration"
    migration.mkdir(parents=True)
    (migration / "V1__initial.sql").write_text("CREATE TABLE x (id INT);\n")
    (migration / "V2__add_column.sql").write_text("ALTER TABLE x ADD y INT;\n")

    result = detect_database(repo)

    assert result["migration_directories"] == [
        {"path": "src/main/resources/db/migration", "file_count": 2}
    ]


def test_detect_database_finds_nested_django_style_migrations(tmp_path):
    repo = tmp_path / "repo"
    migrations = repo / "app" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "0001_initial.py").write_text("class Migration:\n    pass\n")

    result = detect_database(repo)

    assert result["migration_directories"] == [{"path": "app/migrations", "file_count": 1}]


def test_detect_database_finds_alembic_versions(tmp_path):
    repo = tmp_path / "repo"
    versions = repo / "alembic" / "versions"
    versions.mkdir(parents=True)
    (versions / "abc123_initial.py").write_text(
        "revision = 'abc123'\ndown_revision = None\n\ndef upgrade():\n    pass\n"
    )
    (versions / "def456_add_index.py").write_text(
        "revision = 'def456'\ndown_revision = 'abc123'\n\ndef upgrade():\n    pass\n"
    )

    result = detect_database(repo)

    assert {"path": "alembic/versions", "file_count": 2} in result["migration_directories"]


def test_detect_database_finds_renamed_alembic_versions_directory(tmp_path):
    """Found via real-repo stress testing (Apache Superset, a large,
    well-known real repo): Alembic's own generator names the migrations
    directory "alembic" by default, but real projects commonly rename it
    - Superset uses migrations/versions, not alembic/versions. "versions"
    is genuinely Alembic's fixed subdirectory name regardless of what its
    parent is called, so any directory literally named "versions" is now
    a candidate, content-verified (a real down_revision assignment) so an
    unrelated "versions" directory doesn't false-positive."""
    repo = tmp_path / "repo"
    versions = repo / "superset" / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "abc123_initial.py").write_text(
        "revision = 'abc123'\ndown_revision = None\n\ndef upgrade():\n    pass\n"
    )

    result = detect_database(repo)

    assert {"path": "superset/migrations/versions", "file_count": 1} in result["migration_directories"]


def test_detect_database_ignores_unrelated_versions_directory(tmp_path):
    repo = tmp_path / "repo"
    versions = repo / "api" / "versions"
    versions.mkdir(parents=True)
    (versions / "v1.py").write_text("VERSION = '1.0'\n")

    result = detect_database(repo)

    assert not any(d["path"] == "api/versions" for d in result["migration_directories"])


def test_detect_database_finds_rails_style_migrate_dir(tmp_path):
    repo = tmp_path / "repo"
    migrate = repo / "db" / "migrate"
    migrate.mkdir(parents=True)
    (migrate / "20260101000000_create_users.rb").write_text("class CreateUsers; end\n")

    result = detect_database(repo)

    assert {"path": "db/migrate", "file_count": 1} in result["migration_directories"]


def test_detect_database_ignores_migrations_dir_inside_node_modules(tmp_path):
    repo = tmp_path / "repo"
    vendored = repo / "node_modules" / "some-orm" / "migrations"
    vendored.mkdir(parents=True)
    (vendored / "001.js").write_text("module.exports = {};\n")

    result = detect_database(repo)

    assert result["migration_directories"] == []


def test_detect_database_finds_prisma_schema_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "prisma").mkdir()
    (repo / "prisma" / "schema.prisma").write_text("datasource db {}\n")

    result = detect_database(repo)

    assert result["schema_files"] == ["prisma/schema.prisma"]


def test_detect_database_returns_empty_when_nothing_present(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    result = detect_database(repo)

    assert result == {"orm_frameworks": [], "migration_directories": [], "schema_files": []}


def test_detect_docker_compose_services_finds_real_services(tmp_path):
    from aletheore.scanner.detect import _detect_docker_compose_services

    repo = tmp_path / "repo"
    repo.mkdir()
    compose = {
        "services": {
            "app-server": {"build": "."},
            "postgres": {"image": "postgres:16"},
        },
        "volumes": {"data": None},
    }
    (repo / "docker-compose.yml").write_text(yaml.dump(compose))

    result = _detect_docker_compose_services(repo)

    assert result == [{"file": "docker-compose.yml", "services": ["app-server", "postgres"]}]


def test_detect_docker_compose_services_finds_a_compose_file_in_a_subdirectory(tmp_path):
    from aletheore.scanner.detect import _detect_docker_compose_services

    repo = tmp_path / "repo"
    service_dir = repo / "backend-service"
    service_dir.mkdir(parents=True)
    compose = {"services": {"web": {"image": "nginx"}}}
    (service_dir / "docker-compose.yml").write_text(yaml.dump(compose))

    result = _detect_docker_compose_services(repo)

    assert result == [{"file": "backend-service/docker-compose.yml", "services": ["web"]}]


def test_detect_docker_compose_services_returns_empty_when_no_compose_file(tmp_path):
    from aletheore.scanner.detect import _detect_docker_compose_services

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _detect_docker_compose_services(repo) == []


def test_detect_docker_compose_services_skips_malformed_yaml(tmp_path):
    from aletheore.scanner.detect import _detect_docker_compose_services

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text("services:\n  app: [unterminated\n")

    assert _detect_docker_compose_services(repo) == []


def test_detect_docker_compose_services_ignores_node_modules(tmp_path):
    from aletheore.scanner.detect import _detect_docker_compose_services

    repo = tmp_path / "repo"
    vendored = repo / "node_modules" / "some-pkg"
    vendored.mkdir(parents=True)
    (vendored / "docker-compose.yml").write_text(yaml.dump({"services": {"x": {}}}))

    assert _detect_docker_compose_services(repo) == []


def test_detect_kubernetes_manifests_finds_a_real_deployment(tmp_path):
    from aletheore.scanner.detect import _detect_kubernetes_manifests

    repo = tmp_path / "repo"
    k8s = repo / "k8s"
    k8s.mkdir(parents=True)
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web"},
    }
    (k8s / "deployment.yaml").write_text(yaml.dump(manifest))

    result = _detect_kubernetes_manifests(repo)

    assert result == ["k8s/deployment.yaml"]


def test_detect_kubernetes_manifests_ignores_non_k8s_yaml(tmp_path):
    from aletheore.scanner.detect import _detect_kubernetes_manifests

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.yaml").write_text(yaml.dump({"some_setting": True}))

    assert _detect_kubernetes_manifests(repo) == []


def test_detect_kubernetes_manifests_ignores_node_modules(tmp_path):
    from aletheore.scanner.detect import _detect_kubernetes_manifests

    repo = tmp_path / "repo"
    vendored = repo / "node_modules" / "some-pkg"
    vendored.mkdir(parents=True)
    manifest = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "x"}}
    (vendored / "service.yaml").write_text(yaml.dump(manifest))

    assert _detect_kubernetes_manifests(repo) == []


def test_detect_terraform_files_finds_tf_files(tmp_path):
    from aletheore.scanner.detect import _detect_terraform_files

    repo = tmp_path / "repo"
    terraform = repo / "terraform"
    terraform.mkdir(parents=True)
    (terraform / "main.tf").write_text('resource "aws_instance" "web" {}\n')

    result = _detect_terraform_files(repo)

    assert result == ["terraform/main.tf"]


def test_detect_helm_charts_finds_chart_yaml(tmp_path):
    from aletheore.scanner.detect import _detect_helm_charts

    repo = tmp_path / "repo"
    chart_dir = repo / "charts" / "myapp"
    chart_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text("apiVersion: v2\nname: myapp\nversion: 0.1.0\n")

    result = _detect_helm_charts(repo)

    assert result == ["charts/myapp/Chart.yaml"]


def test_detect_terraform_files_returns_a_sorted_list(tmp_path):
    # rglob traversal order is filesystem-dependent (confirmed: the same
    # repo produced differently-ordered results on APFS vs ext4) - the
    # detector must sort its own output rather than pass through whatever
    # order the OS happens to return.
    from aletheore.scanner.detect import _detect_terraform_files

    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("zulu", "delta", "mike", "alpha"):
        (repo / f"{name}.tf").write_text('resource "x" "y" {}\n')

    result = _detect_terraform_files(repo)

    assert result == sorted(result)
    assert result == ["alpha.tf", "delta.tf", "mike.tf", "zulu.tf"]


def test_detect_helm_charts_returns_a_sorted_list(tmp_path):
    from aletheore.scanner.detect import _detect_helm_charts

    repo = tmp_path / "repo"
    for name in ("zulu", "alpha", "mike"):
        chart_dir = repo / "charts" / name
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2\nname: x\nversion: 0.1.0\n")

    result = _detect_helm_charts(repo)

    assert result == sorted(result)
    assert result == [
        "charts/alpha/Chart.yaml",
        "charts/mike/Chart.yaml",
        "charts/zulu/Chart.yaml",
    ]


def test_detect_declared_env_vars_orders_by_source_but_preserves_line_order_within_it(tmp_path):
    # ENV_FILE_MARKERS are exact filenames (".env.example", etc.), not
    # globs - each source here lives in its own subdirectory so two
    # differently-named markers can still be told apart by path.
    from aletheore.scanner.detect import _detect_declared_env_vars

    repo = tmp_path / "repo"
    (repo / "zulu").mkdir(parents=True)
    (repo / "zulu" / ".env.example").write_text("SECOND=1\nFIRST=2\n")
    (repo / "alpha").mkdir(parents=True)
    (repo / "alpha" / ".env.sample").write_text("FOURTH=3\nTHIRD=4\n")

    result = _detect_declared_env_vars(repo)

    assert result == [
        {"name": "FOURTH", "source": "alpha/.env.sample"},
        {"name": "THIRD", "source": "alpha/.env.sample"},
        {"name": "SECOND", "source": "zulu/.env.example"},
        {"name": "FIRST", "source": "zulu/.env.example"},
    ]


def test_detect_infrastructure_categories_return_empty_when_nothing_present(tmp_path):
    from aletheore.scanner.detect import (
        _detect_helm_charts,
        _detect_kubernetes_manifests,
        _detect_terraform_files,
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    assert _detect_kubernetes_manifests(repo) == []
    assert _detect_terraform_files(repo) == []
    assert _detect_helm_charts(repo) == []


def test_detect_declared_env_vars_reads_names_only_never_values(tmp_path):
    from aletheore.scanner.detect import _detect_declared_env_vars

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env.example").write_text(
        "DATABASE_URL=postgresql://user:supersecretpassword@host/db\n"
        "# a comment\n"
        "\n"
        "API_KEY=\n"
    )

    result = _detect_declared_env_vars(repo)

    assert result == [
        {"name": "DATABASE_URL", "source": ".env.example"},
        {"name": "API_KEY", "source": ".env.example"},
    ]
    assert "supersecretpassword" not in str(result)


def test_detect_declared_env_vars_reads_multiple_marker_filenames(tmp_path):
    from aletheore.scanner.detect import _detect_declared_env_vars

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env.sample").write_text("FOO=bar\n")

    result = _detect_declared_env_vars(repo)

    assert result == [{"name": "FOO", "source": ".env.sample"}]


def test_detect_declared_env_vars_returns_empty_when_no_env_files(tmp_path):
    from aletheore.scanner.detect import _detect_declared_env_vars

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    assert _detect_declared_env_vars(repo) == []


def test_detect_infrastructure_combines_all_categories(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text(yaml.dump({"services": {"web": {"image": "nginx"}}}))
    (repo / "main.tf").write_text('resource "aws_instance" "x" {}\n')

    result = detect_infrastructure(repo)

    assert result["docker_compose_services"] == [{"file": "docker-compose.yml", "services": ["web"]}]
    assert result["terraform_files"] == ["main.tf"]
    assert result["kubernetes_manifests"] == []
    assert result["helm_charts"] == []


def test_detect_environment_variables_wraps_declared_list(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env.example").write_text("FOO=bar\n")

    result = detect_environment_variables(repo)

    assert result == {"declared": [{"name": "FOO", "source": ".env.example"}]}


# ── _iter_pruned_tree tests ─────────────────────────────────────────────


def test_iter_pruned_tree_excludes_ignored_dirs(tmp_path):
    from aletheore.scanner.detect import _iter_pruned_tree

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    vendored = repo / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text("console.log('hi')\n")
    (repo / ".git" / "config").parent.mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "config").write_text("[core]\n")

    pruned = list(_iter_pruned_tree(repo))
    pruned_names = {path.name for path, _ in pruned}

    assert "main.py" in pruned_names
    assert "index.js" not in pruned_names
    assert "config" not in pruned_names


def test_iter_pruned_tree_never_visits_ignored_dirs(tmp_path):
    """The whole point of the fix is fewer filesystem operations.
    Verify the walk never descends into ignored dirs, not just that the
    final result excludes files inside them."""
    from aletheore.scanner.detect import _iter_pruned_tree

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    ignored = repo / "node_modules" / "some-pkg"
    ignored.mkdir(parents=True)
    (ignored / "docker-compose.yml").write_text("services:\n  web: {}\n")
    (ignored / "Chart.yaml").write_text("apiVersion: v2\n")
    (ignored / "main.tf").write_text('resource "x" "y" {}\n')
    (ignored / ".env.example").write_text("FOO=bar\n")
    (ignored / "migrations").mkdir()
    (ignored / "migrations" / "001.py").write_text("x = 1\n")

    pruned = list(_iter_pruned_tree(repo))
    pruned_names = {path.name for path, _ in pruned}

    assert "docker-compose.yml" not in pruned_names
    assert "Chart.yaml" not in pruned_names
    assert "main.tf" not in pruned_names
    assert ".env.example" not in pruned_names
    assert "migrations" not in pruned_names


def test_iter_pruned_tree_does_not_follow_symlinked_directories(tmp_path):
    from aletheore.scanner.detect import _iter_pruned_tree

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("x = 1\n")
    (repo / "linked_dir").symlink_to(outside, target_is_directory=True)
    (repo / "main.py").write_text("x = 1\n")

    pruned = list(_iter_pruned_tree(repo))
    pruned_names = {path.name for path, _ in pruned}

    assert "main.py" in pruned_names
    assert "secret.py" not in pruned_names


def test_six_detectors_identical_output_with_shared_walk(tmp_path):
    """Each of the six detectors produces byte-identical results when called
    with a shared pruned_tree list vs. creating their own internally."""
    from aletheore.scanner.detect import (
        _detect_declared_env_vars,
        _detect_docker_compose_services,
        _detect_helm_charts,
        _detect_kubernetes_manifests,
        _detect_migration_directories,
        _detect_terraform_files,
        _iter_pruned_tree,
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    # migration dirs
    (repo / "app" / "migrations").mkdir(parents=True)
    (repo / "app" / "migrations" / "001.py").write_text("x = 1\n")
    # docker compose
    compose = {"services": {"web": {"image": "nginx"}}}
    (repo / "docker-compose.yml").write_text(yaml.dump(compose))
    # kubernetes
    (repo / "k8s").mkdir()
    manifest = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web"}}
    (repo / "k8s" / "deploy.yaml").write_text(yaml.dump(manifest))
    # terraform
    (repo / "infra").mkdir()
    (repo / "infra" / "main.tf").write_text('resource "x" "y" {}\n')
    # helm
    (repo / "charts" / "myapp").mkdir(parents=True)
    (repo / "charts" / "myapp" / "Chart.yaml").write_text("apiVersion: v2\n")
    # env vars
    (repo / ".env.example").write_text("FOO=bar\n")

    shared_tree = list(_iter_pruned_tree(repo))

    assert _detect_migration_directories(repo, shared_tree) == _detect_migration_directories(repo)
    assert _detect_docker_compose_services(repo, shared_tree) == _detect_docker_compose_services(repo)
    assert _detect_kubernetes_manifests(repo, shared_tree) == _detect_kubernetes_manifests(repo)
    assert _detect_terraform_files(repo, shared_tree) == _detect_terraform_files(repo)
    assert _detect_helm_charts(repo, shared_tree) == _detect_helm_charts(repo)
    assert _detect_declared_env_vars(repo, shared_tree) == _detect_declared_env_vars(repo)
