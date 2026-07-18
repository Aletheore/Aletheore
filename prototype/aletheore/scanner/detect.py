import json
import tomllib
from pathlib import Path

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
}

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
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

MIGRATION_DIR_NAME_MARKERS = ("migrations",)

SCHEMA_FILE_MARKERS = (
    "prisma/schema.prisma",
    "db/schema.rb",
    "db/structure.sql",
)

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


def _iter_source_files(repo_path: Path):
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        yield path


def detect_languages(repo_path: Path) -> list[dict]:
    counts: dict[str, dict] = {}
    for path in _iter_source_files(repo_path):
        language = EXTENSION_TO_LANGUAGE.get(path.suffix)
        if language is None:
            continue
        entry = counts.setdefault(language, {"name": language, "file_count": 0, "loc": 0})
        entry["file_count"] += 1
        try:
            entry["loc"] += sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return list(counts.values())


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


def _detect_migration_directories(repo_path: Path) -> list[dict]:
    results: list[dict] = []
    for name in MIGRATION_DIR_NAME_MARKERS:
        for candidate in repo_path.rglob(name):
            if not candidate.is_dir():
                continue
            rel_parts = candidate.relative_to(repo_path).parts
            if any(part in IGNORED_DIRS for part in rel_parts):
                continue
            file_count = sum(
                1
                for f in candidate.iterdir()
                if f.is_file() and f.suffix in (".py", ".sql", ".js", ".ts", ".rb")
            )
            results.append(
                {"path": candidate.relative_to(repo_path).as_posix(), "file_count": file_count}
            )

    alembic_versions = repo_path / "alembic" / "versions"
    if alembic_versions.is_dir():
        file_count = sum(1 for f in alembic_versions.iterdir() if f.is_file() and f.suffix == ".py")
        results.append({"path": "alembic/versions", "file_count": file_count})

    rails_migrate = repo_path / "db" / "migrate"
    if rails_migrate.is_dir():
        file_count = sum(1 for f in rails_migrate.iterdir() if f.is_file() and f.suffix == ".rb")
        results.append({"path": "db/migrate", "file_count": file_count})

    return results


def _detect_schema_files(repo_path: Path) -> list[str]:
    return [marker for marker in SCHEMA_FILE_MARKERS if (repo_path / marker).exists()]


def detect_database(repo_path: Path) -> dict:
    pip_lines = _iter_pip_package_lines(repo_path)
    npm_deps = _npm_dependencies(repo_path)
    return {
        "orm_frameworks": _match_dependency_markers(
            DB_ORM_MARKERS_PY, DB_ORM_MARKERS_JS, pip_lines, npm_deps
        ),
        "migration_directories": _detect_migration_directories(repo_path),
        "schema_files": _detect_schema_files(repo_path),
    }
