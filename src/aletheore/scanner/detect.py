import json
import os
import tomllib
from functools import lru_cache
from pathlib import Path

import yaml

from aletheore.repo_config import is_ignored

IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".aletheore",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".cache",
    "dist", "build", "out", "release", ".next", "coverage", "htmlcov",
    # .NET's intermediate build directory - confirmed by a real `dotnet build`:
    # it fills this with auto-generated .cs files (assembly attributes, etc.)
    # that would otherwise get scanned as real source. Not adding "bin" (.NET's
    # other build-output dir) alongside it - unlike "obj", "bin" is also a
    # legitimate source directory in other ecosystems (e.g. a Ruby gem's own
    # executable scripts), so excluding it globally risks hiding real source
    # more than the noise it would remove here.
    "obj",
    # Claude Code's own config/scratch directory - can hold a full git
    # worktree checkout under .claude/worktrees/<name>/ (a real duplicate of
    # this repo's own source tree, at a possibly-older commit). Confirmed via
    # a real self-scan of this repo: an active worktree there duplicated
    # every module path under github-app/ and src/, which corrupted import
    # resolution enough that real, actively-imported files (webhook
    # handlers, API routers) were misreported as dead code. Always
    # gitignored by convention, never project source.
    ".claude",
}

FRAMEWORK_MARKERS_PY = {
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
    "uvicorn": "uvicorn",
}

FRAMEWORK_MARKERS_JS = {
    "react": "react",
    "vue": "vue",
    "express": "express",
    "next": "next",
}

AI_PROVIDER_MARKERS_PY = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google-generativeai": "google-generativeai",
    "google-genai": "google-genai",
    "cohere": "cohere",
    "mistralai": "mistralai",
}

AI_PROVIDER_MARKERS_JS = {
    "openai": "openai",
    "@anthropic-ai/sdk": "@anthropic-ai/sdk",
    "@google/generative-ai": "@google/generative-ai",
}

AI_ORCHESTRATION_MARKERS_PY = {
    "langchain": "langchain",
    "llama-index": "llama-index",
    "llama_index": "llama-index",
    "crewai": "crewai",
    "autogen": "autogen",
}

AI_ORCHESTRATION_MARKERS_JS = {
    "langchain": "langchain",
}

AI_VECTOR_STORE_MARKERS_PY = {
    "pinecone-client": "pinecone",
    "pinecone": "pinecone",
    "chromadb": "chromadb",
    "weaviate-client": "weaviate",
    "qdrant-client": "qdrant",
    "faiss-cpu": "faiss",
}

AI_LOCAL_INFERENCE_MARKERS_PY = {
    "transformers": "transformers",
    "ollama": "ollama",
    "llama-cpp-python": "llama-cpp-python",
    "vllm": "vllm",
}

AI_MCP_MARKERS_PY = {
    "mcp": "mcp",
}

AI_MCP_MARKERS_JS = {
    "@modelcontextprotocol/sdk": "@modelcontextprotocol/sdk",
}

DB_ORM_MARKERS_PY = {
    "sqlalchemy": "sqlalchemy",
    "django": "django-orm",
    "peewee": "peewee",
    "tortoise-orm": "tortoise-orm",
    "mongoengine": "mongoengine",
}

DB_ORM_MARKERS_JS = {
    "prisma": "prisma",
    "@prisma/client": "prisma",
    "typeorm": "typeorm",
    "sequelize": "sequelize",
    "mongoose": "mongoose",
    "knex": "knex",
}

# "migration" (singular) is Flyway's own documented default convention
# (`src/main/resources/db/migration`, `V<version>__<description>.sql`) -
# confirmed on a real repo (killbill, a Java billing platform): without
# it, a Flyway-based project's migration directory was never detected at
# all, so detect_database's migration_directories came back empty and
# schema_map.extract_schema was never even invoked with the right path -
# not a parsing gap, the feature silently produced nothing for the whole
# project.
MIGRATION_DIR_NAME_MARKERS = ("migrations", "migration")

SCHEMA_FILE_MARKERS = (
    "prisma/schema.prisma",
    "db/schema.rb",
    "db/structure.sql",
)

COMPOSE_FILE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")

K8S_KIND_MARKERS = {
    "Deployment",
    "Service",
    "Ingress",
    "ConfigMap",
    "Secret",
    "StatefulSet",
    "DaemonSet",
    "Job",
    "CronJob",
    "Namespace",
    "PersistentVolumeClaim",
}

YAML_EXTENSIONS = (".yaml", ".yml")

ENV_FILE_MARKERS = (".env.example", ".env.sample", ".env.template", "env.example")

BUILD_TOOL_MARKERS = {
    "Dockerfile": "docker",
    "docker-compose.yml": "docker-compose",
    "Makefile": "make",
    "webpack.config.js": "webpack",
    "vite.config.ts": "vite",
    "vite.config.js": "vite",
}

POLICY_DOC_MARKERS = {
    "LICENSE": "license",
    "LICENSE.md": "license",
    "README.md": "readme",
    "SECURITY.md": "security_policy",
    "PRIVACY.md": "privacy_policy",
    "PRIVACY_POLICY.md": "privacy_policy",
    "CODE_OF_CONDUCT.md": "code_of_conduct",
    "CONTRIBUTING.md": "contributing_guide",
    "TERMS.md": "terms_of_service",
    "TERMS_OF_SERVICE.md": "terms_of_service",
    "GOVERNANCE.md": "governance_policy",
    "docs/security": "security_policy",
    "docs/privacy": "privacy_policy",
    "docs/compliance": "compliance_docs",
    "docs/governance": "governance_policy",
}


def _iter_pip_package_lines(repo_path: Path) -> list[tuple[str, str, str]]:
    results = []

    requirements = repo_path / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            package_name = line.split("==")[0].split(">=")[0].split("<=")[0].strip().lower()
            results.append((package_name, line, "requirements.txt"))

    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="ignore"))
        except tomllib.TOMLDecodeError:
            data = {}

        for dep in data.get("project", {}).get("dependencies", []):
            package_name = (
                dep.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0]
                .split("[")[0].split(";")[0].strip().lower()
            )
            results.append((package_name, dep, "pyproject.toml"))

        poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
        for name, spec in poetry_deps.items():
            if name.lower() == "python":
                continue
            version = spec.get("version", "") if isinstance(spec, dict) else spec
            results.append((name.lower(), f"{name} {version}".strip(), "pyproject.toml"))

    return results


def _npm_dependencies(repo_path: Path) -> dict[str, str]:
    package_json = repo_path / "package.json"
    if not package_json.exists():
        return {}
    try:
        data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return {}
    return {**data.get("dependencies", {}), **data.get("devDependencies", {})}


@lru_cache(maxsize=128)
def _nested_git_roots(repo_path: Path) -> set[Path]:
    """Directories other than repo_path itself that contain their own `.git`
    entry (file or directory) - a linked worktree (`git worktree add`) or a
    submodule checked out the classic way, and therefore a separate git
    working tree, not this repo's own source. Unlike cache/build dirs, these
    have no fixed name to add to IGNORED_DIRS - a real scan found one at
    `.claude/worktrees/<name>/`, doubling every file inside it.

    os.walk(followlinks=False) rather than Path.rglob(".git") - a symlinked
    directory shouldn't be descended into just to look for a nested repo.
    """
    repo_path = repo_path.resolve()
    roots = set()
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        current_dir = Path(dirpath)
        # Check for .git before pruning - IGNORED_DIRS contains ".git" itself
        # (so descending into a found repo's own .git internals is skipped),
        # and pruning first would remove ".git" from dirnames before this
        # check ever sees it, silently disabling directory-style nested-clone
        # detection entirely regardless of where it sits.
        has_nested_git = ".git" in dirnames or ".git" in filenames
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        if current_dir != repo_path and has_nested_git:
            roots.add(current_dir)
            dirnames[:] = []
    return roots


def _iter_pruned_tree(repo_path: Path):
    """Single os.walk yielding (path, is_dir) for every file and directory
    under repo_path, pruning IGNORED_DIRS before descending.

    Replaces six independent repo_path.rglob() calls that each traversed the
    full tree then discarded IGNORED_DIRS results after the fact. Same pruning
    pattern as _iter_source_files: dirnames[:] = [d for d in dirnames if d
    not in IGNORED_DIRS], followlinks=False (a symlinked directory shouldn't
    be descended into).
    """
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        current_dir = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            yield current_dir / filename, False
        for dirname in dirnames:
            yield current_dir / dirname, True


def _iter_source_files(repo_path: Path, ignored_paths: list[str] | None = None):
    # os.walk(followlinks=False) rather than Path.rglob("*") - a symlinked
    # directory would otherwise have its contents walked and reported on as
    # if they were part of this repo. followlinks only stops descent into
    # symlinked *directories* - a symlinked file sitting directly in a real
    # directory still needs its own is_symlink() check below.
    nested_git_roots = _nested_git_roots(repo_path)
    patterns = ignored_paths or []
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        current_dir = Path(dirpath)
        rel_dir = current_dir.relative_to(repo_path).as_posix()
        dirnames[:] = [
            d
            for d in dirnames
            if d not in IGNORED_DIRS
            and not is_ignored(f"{rel_dir}/{d}" if rel_dir != "." else d, patterns)
        ]
        if any(root in current_dir.parents or root == current_dir for root in nested_git_roots):
            dirnames[:] = []
            continue
        for filename in filenames:
            path = current_dir / filename
            if path.is_symlink() or not path.is_file():
                continue
            rel_path = path.relative_to(repo_path).as_posix()
            if is_ignored(rel_path, patterns):
                continue
            yield path


def detect_languages(repo_path: Path, ignored_paths: list[str] | None = None) -> list[dict]:
    # Local import: graph.py already imports IGNORED_DIRS from this module, so a
    # module-level import here would be circular. LANGUAGE_BY_EXTENSION is the
    # single source of truth for "which extensions we support" - this used to be
    # a second, separately-maintained mapping here that fell out of sync (missing
    # Rust/Java/Ruby/PHP/C/C++/C# entirely - confirmed on a real scan where a
    # C/C++-heavy repo reported zero C or C++ in its language summary despite
    # both being fully parsed into the module graph).
    from aletheore.scanner.graph import LANGUAGE_BY_EXTENSION

    counts: dict[str, dict] = {}
    for path in _iter_source_files(repo_path, ignored_paths):
        entry_spec = LANGUAGE_BY_EXTENSION.get(path.suffix)
        if entry_spec is None:
            continue
        language = entry_spec[0]
        entry = counts.setdefault(language, {"name": language, "file_count": 0, "loc": 0})
        entry["file_count"] += 1
        try:
            entry["loc"] += sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    # counts preserves _iter_source_files' filesystem-walk order, which is
    # filesystem-dependent (APFS vs ext4 give different orders for the same
    # repo) - sorted here for the same reason the other detectors below are.
    return sorted(counts.values(), key=lambda entry: entry["name"])


def detect_frameworks(repo_path: Path) -> list[dict]:
    frameworks: list[dict] = []

    for package_name, line, source in _iter_pip_package_lines(repo_path):
        if package_name in FRAMEWORK_MARKERS_PY:
            frameworks.append(
                {"name": FRAMEWORK_MARKERS_PY[package_name], "evidence": f"{source}:{line}"}
            )

    for name, version in _npm_dependencies(repo_path).items():
        key = name.lower()
        if key in FRAMEWORK_MARKERS_JS:
            frameworks.append(
                {"name": FRAMEWORK_MARKERS_JS[key], "evidence": f"package.json:{name}@{version}"}
            )

    return frameworks


def _match_dependency_markers(
    pip_markers: dict[str, str],
    js_markers: dict[str, str],
    pip_lines: list[tuple[str, str, str]],
    npm_deps: dict[str, str],
) -> list[dict]:
    matches: list[dict] = []
    for package_name, line, source in pip_lines:
        if package_name in pip_markers:
            matches.append({"name": pip_markers[package_name], "evidence": f"{source}:{line}"})
    for name, version in npm_deps.items():
        key = name.lower()
        if key in js_markers:
            matches.append({"name": js_markers[key], "evidence": f"package.json:{name}@{version}"})
    return matches


def detect_ai_usage(repo_path: Path) -> dict:
    pip_lines = _iter_pip_package_lines(repo_path)
    npm_deps = _npm_dependencies(repo_path)

    return {
        "providers": _match_dependency_markers(
            AI_PROVIDER_MARKERS_PY, AI_PROVIDER_MARKERS_JS, pip_lines, npm_deps
        ),
        "orchestration": _match_dependency_markers(
            AI_ORCHESTRATION_MARKERS_PY, AI_ORCHESTRATION_MARKERS_JS, pip_lines, npm_deps
        ),
        "vector_stores": _match_dependency_markers(
            AI_VECTOR_STORE_MARKERS_PY, {}, pip_lines, npm_deps
        ),
        "local_inference": _match_dependency_markers(
            AI_LOCAL_INFERENCE_MARKERS_PY, {}, pip_lines, npm_deps
        ),
        "mcp": _match_dependency_markers(
            AI_MCP_MARKERS_PY, AI_MCP_MARKERS_JS, pip_lines, npm_deps
        ),
    }


def detect_build_tools(repo_path: Path) -> list[dict]:
    tools = []
    for filename, tool_name in BUILD_TOOL_MARKERS.items():
        marker = repo_path / filename
        if marker.exists():
            tools.append({"name": tool_name, "evidence": filename})
    return tools


def detect_policy_docs(repo_path: Path) -> list[dict]:
    docs = []
    for marker, category in POLICY_DOC_MARKERS.items():
        candidate = repo_path / marker
        if candidate.exists():
            docs.append({"name": category, "evidence": marker})
    return docs


def detect_monorepo(repo_path: Path) -> dict:
    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            data = {}
        workspaces = data.get("workspaces")
        if workspaces:
            return {"detected": True, "workspaces": list(workspaces)}

    for marker in ("pnpm-workspace.yaml", "lerna.json", "nx.json"):
        if (repo_path / marker).exists():
            return {"detected": True, "workspaces": []}

    return {"detected": False, "workspaces": []}


def _looks_like_alembic_versions_file(path: Path) -> bool:
    """`down_revision = ` is distinctive enough to real Alembic migration
    files (Alembic's own generator always writes it, even when None) that
    it safely tells a real "versions" directory apart from an unrelated
    one - same content-verification role Django migrations already get
    via orm_migrations.looks_like_django_migration."""
    try:
        return "down_revision" in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _detect_migration_directories(repo_path: Path, pruned_tree=None) -> list[dict]:
    results: list[dict] = []
    # Materialized to a list (not left as a generator) even when the
    # caller passes nothing - this function now walks it twice (once for
    # the plain name markers, once below for "versions"), and a generator
    # would be silently exhausted after the first pass.
    pruned_tree = list(pruned_tree) if pruned_tree is not None else list(_iter_pruned_tree(repo_path))
    for path, is_dir in pruned_tree:
        if not is_dir:
            continue
        if path.name not in MIGRATION_DIR_NAME_MARKERS:
            continue
        file_count = sum(
            1
            for f in path.iterdir()
            if f.is_file() and f.suffix in (".py", ".sql", ".js", ".ts", ".rb")
        )
        results.append(
            {"path": path.relative_to(repo_path).as_posix(), "file_count": file_count}
        )

    # Alembic's own generator names the migrations directory "alembic" by
    # default, but real projects very commonly rename it - Apache Superset
    # (a large, well-known real repo) uses migrations/versions, not
    # alembic/versions; hardcoding the default alone missed it entirely,
    # confirmed directly. "versions" is genuinely Alembic's fixed
    # subdirectory name regardless of what its parent is called, so any
    # directory literally named "versions" is a candidate - content-
    # verified (at least one .py file has a real `down_revision =`
    # assignment, distinctive enough to Alembic that an unrelated
    # "versions" directory for something else won't false-positive) before
    # being reported, since "versions" alone is a more generic name than
    # "migrations"/"migration" and isn't paired with content-sniffing
    # anywhere else in this function.
    for path, is_dir in pruned_tree:
        if not is_dir or path.name != "versions":
            continue
        py_files = [f for f in path.iterdir() if f.is_file() and f.suffix == ".py"]
        if not any(_looks_like_alembic_versions_file(f) for f in py_files):
            continue
        results.append(
            {"path": path.relative_to(repo_path).as_posix(), "file_count": len(py_files)}
        )

    rails_migrate = repo_path / "db" / "migrate"
    if rails_migrate.is_dir():
        file_count = sum(1 for f in rails_migrate.iterdir() if f.is_file() and f.suffix == ".rb")
        results.append({"path": "db/migrate", "file_count": file_count})

    # rglob traversal order is filesystem-dependent (APFS vs ext4 can order
    # the same directory's entries differently) - sorted here so the same
    # repo produces the same evidence bytes regardless of what it's scanned
    # on.
    results.sort(key=lambda entry: entry["path"])
    return results


def _detect_schema_files(repo_path: Path) -> list[str]:
    return [marker for marker in SCHEMA_FILE_MARKERS if (repo_path / marker).exists()]


def _detect_docker_compose_services(repo_path: Path, pruned_tree=None) -> list[dict]:
    # Compose files commonly live under one app inside a larger repository.
    results: list[dict] = []
    if pruned_tree is None:
        pruned_tree = _iter_pruned_tree(repo_path)
    for path, is_dir in pruned_tree:
        if is_dir:
            continue
        if path.name not in COMPOSE_FILE_NAMES:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="ignore"))
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        services = list(data.get("services", {}).keys())
        if services:
            results.append(
                {"file": path.relative_to(repo_path).as_posix(), "services": services}
            )
    # See _detect_migration_directories for why this is sorted.
    results.sort(key=lambda entry: entry["file"])
    return results


def _detect_kubernetes_manifests(repo_path: Path, pruned_tree=None) -> list[str]:
    results: list[str] = []
    if pruned_tree is None:
        pruned_tree = _iter_pruned_tree(repo_path)
    for path, is_dir in pruned_tree:
        if is_dir:
            continue
        if path.suffix not in YAML_EXTENSIONS:
            continue
        try:
            docs = list(
                yaml.safe_load_all(path.read_text(encoding="utf-8", errors="ignore"))
            )
        except yaml.YAMLError:
            continue
        for doc in docs:
            if (
                isinstance(doc, dict)
                and doc.get("kind") in K8S_KIND_MARKERS
                and "apiVersion" in doc
            ):
                results.append(path.relative_to(repo_path).as_posix())
                break
    # See _detect_migration_directories for why this is sorted.
    results.sort()
    return results


def _detect_terraform_files(repo_path: Path, pruned_tree=None) -> list[str]:
    results: list[str] = []
    if pruned_tree is None:
        pruned_tree = _iter_pruned_tree(repo_path)
    for path, is_dir in pruned_tree:
        if is_dir:
            continue
        if path.suffix != ".tf":
            continue
        results.append(path.relative_to(repo_path).as_posix())
    # See _detect_migration_directories for why this is sorted.
    results.sort()
    return results


def _detect_helm_charts(repo_path: Path, pruned_tree=None) -> list[str]:
    results: list[str] = []
    if pruned_tree is None:
        pruned_tree = _iter_pruned_tree(repo_path)
    for path, is_dir in pruned_tree:
        if is_dir:
            continue
        if path.name != "Chart.yaml":
            continue
        results.append(path.relative_to(repo_path).as_posix())
    # See _detect_migration_directories for why this is sorted.
    results.sort()
    return results


def _detect_declared_env_vars(repo_path: Path, pruned_tree=None) -> list[dict]:
    results: list[dict] = []
    if pruned_tree is None:
        pruned_tree = _iter_pruned_tree(repo_path)
    for path, is_dir in pruned_tree:
        if is_dir:
            continue
        if path.name not in ENV_FILE_MARKERS:
            continue
        source = path.relative_to(repo_path).as_posix()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            name = stripped.split("=", 1)[0].strip()
            if name and all(c.isalnum() or c == "_" for c in name):
                results.append({"name": name, "source": source})
    # Stable sort by source only (not name too) - which file gets visited
    # first is what's filesystem-dependent and needs pinning down; each
    # file's own variables should stay in their original declaration order.
    # See _detect_migration_directories for the general rationale.
    results.sort(key=lambda entry: entry["source"])
    return results


def detect_database(repo_path: Path) -> dict:
    pip_lines = _iter_pip_package_lines(repo_path)
    npm_deps = _npm_dependencies(repo_path)
    pruned_tree = list(_iter_pruned_tree(repo_path))
    return {
        "orm_frameworks": _match_dependency_markers(
            DB_ORM_MARKERS_PY, DB_ORM_MARKERS_JS, pip_lines, npm_deps
        ),
        "migration_directories": _detect_migration_directories(repo_path, pruned_tree),
        "schema_files": _detect_schema_files(repo_path),
    }


def detect_infrastructure(repo_path: Path) -> dict:
    pruned_tree = list(_iter_pruned_tree(repo_path))
    return {
        "docker_compose_services": _detect_docker_compose_services(repo_path, pruned_tree),
        "kubernetes_manifests": _detect_kubernetes_manifests(repo_path, pruned_tree),
        "terraform_files": _detect_terraform_files(repo_path, pruned_tree),
        "helm_charts": _detect_helm_charts(repo_path, pruned_tree),
    }


def detect_environment_variables(repo_path: Path) -> dict:
    pruned_tree = list(_iter_pruned_tree(repo_path))
    return {"declared": _detect_declared_env_vars(repo_path, pruned_tree)}
