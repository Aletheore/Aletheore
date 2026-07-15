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

BUILD_TOOL_MARKERS = {
    "Dockerfile": "docker",
    "docker-compose.yml": "docker-compose",
    "Makefile": "make",
    "webpack.config.js": "webpack",
    "vite.config.ts": "vite",
    "vite.config.js": "vite",
}


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

    requirements = repo_path / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            package_name = line.split("==")[0].split(">=")[0].split("<=")[0].strip().lower()
            if package_name in FRAMEWORK_MARKERS_PY:
                frameworks.append(
                    {
                        "name": FRAMEWORK_MARKERS_PY[package_name],
                        "evidence": f"requirements.txt:{line}",
                    }
                )

    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            data = {}
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        for name, version in deps.items():
            key = name.lower()
            if key in FRAMEWORK_MARKERS_JS:
                frameworks.append(
                    {
                        "name": FRAMEWORK_MARKERS_JS[key],
                        "evidence": f"package.json:{name}@{version}",
                    }
                )

    return frameworks


def detect_build_tools(repo_path: Path) -> list[dict]:
    tools = []
    for filename, tool_name in BUILD_TOOL_MARKERS.items():
        marker = repo_path / filename
        if marker.exists():
            tools.append({"name": tool_name, "evidence": filename})
    return tools


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
