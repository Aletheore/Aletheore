import json
from pathlib import Path

IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".veridion",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".cache",
    "dist", "build", "out", "release", ".next", "coverage", "htmlcov",
}

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
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


def _iter_pip_package_lines(repo_path: Path) -> list[tuple[str, str]]:
    requirements = repo_path / "requirements.txt"
    if not requirements.exists():
        return []
    results = []
    for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        package_name = line.split("==")[0].split(">=")[0].split("<=")[0].strip().lower()
        results.append((package_name, line))
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

    for package_name, line in _iter_pip_package_lines(repo_path):
        if package_name in FRAMEWORK_MARKERS_PY:
            frameworks.append(
                {"name": FRAMEWORK_MARKERS_PY[package_name], "evidence": f"requirements.txt:{line}"}
            )

    for name, version in _npm_dependencies(repo_path).items():
        key = name.lower()
        if key in FRAMEWORK_MARKERS_JS:
            frameworks.append(
                {"name": FRAMEWORK_MARKERS_JS[key], "evidence": f"package.json:{name}@{version}"}
            )

    return frameworks


def _match_ai_markers(
    pip_markers: dict[str, str],
    js_markers: dict[str, str],
    pip_lines: list[tuple[str, str]],
    npm_deps: dict[str, str],
) -> list[dict]:
    matches: list[dict] = []
    for package_name, line in pip_lines:
        if package_name in pip_markers:
            matches.append({"name": pip_markers[package_name], "evidence": f"requirements.txt:{line}"})
    for name, version in npm_deps.items():
        key = name.lower()
        if key in js_markers:
            matches.append({"name": js_markers[key], "evidence": f"package.json:{name}@{version}"})
    return matches


def detect_ai_usage(repo_path: Path) -> dict:
    pip_lines = _iter_pip_package_lines(repo_path)
    npm_deps = _npm_dependencies(repo_path)

    return {
        "providers": _match_ai_markers(
            AI_PROVIDER_MARKERS_PY, AI_PROVIDER_MARKERS_JS, pip_lines, npm_deps
        ),
        "orchestration": _match_ai_markers(
            AI_ORCHESTRATION_MARKERS_PY, AI_ORCHESTRATION_MARKERS_JS, pip_lines, npm_deps
        ),
        "vector_stores": _match_ai_markers(AI_VECTOR_STORE_MARKERS_PY, {}, pip_lines, npm_deps),
        "local_inference": _match_ai_markers(
            AI_LOCAL_INFERENCE_MARKERS_PY, {}, pip_lines, npm_deps
        ),
        "mcp": _match_ai_markers(AI_MCP_MARKERS_PY, AI_MCP_MARKERS_JS, pip_lines, npm_deps),
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
