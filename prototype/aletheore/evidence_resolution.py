import fnmatch
import subprocess
from pathlib import Path
from typing import Any

CONFIDENCE_ORDER = {"unavailable": 0, "weak": 1, "inferred": 2, "exact": 3}
CANONICAL_FIELDS = (
    "kind",
    "file",
    "line",
    "end_line",
    "symbol",
    "owner",
    "commit",
    "dependency",
    "risk",
    "confidence",
    "evidence_path",
)


def empty_resolution(kind: str = "unknown") -> dict:
    return {
        "kind": kind,
        "file": None,
        "line": None,
        "end_line": None,
        "symbol": None,
        "owner": None,
        "owner_status": "unavailable",
        "commit": None,
        "commit_status": "unavailable",
        "dependency": None,
        "dependency_status": "unavailable",
        "risk": [],
        "risk_status": "unavailable",
        "confidence": "unavailable",
        "evidence_path": None,
        "evidence_status": "unavailable",
    }


def normalize_resolution(
    *,
    kind: str = "unknown",
    file: str | None = None,
    line: int | None = None,
    end_line: int | None = None,
    symbol: str | None = None,
    owner: str | list[str] | None = None,
    commit: dict | None = None,
    dependency: str | list[str] | None = None,
    risk: list[dict] | None = None,
    confidence: str = "unavailable",
    evidence_path: str | None = None,
) -> dict:
    result = empty_resolution(kind)
    result.update(
        {
            "file": file,
            "line": line,
            "end_line": end_line,
            "symbol": symbol,
            "owner": owner,
            "owner_status": "available" if owner else "unavailable",
            "commit": commit,
            "commit_status": "available" if commit else "unavailable",
            "dependency": dependency,
            "dependency_status": "available" if dependency else "unavailable",
            "risk": risk or [],
            "risk_status": "available" if risk else "unavailable",
            "confidence": confidence if confidence in CONFIDENCE_ORDER else "unavailable",
            "evidence_path": evidence_path,
            "evidence_status": "available" if evidence_path else "unavailable",
        }
    )
    return result


def merge_resolution(base: dict, *attachments: dict) -> dict:
    result = normalize_resolution(**{field: base.get(field) for field in CANONICAL_FIELDS})
    for attachment in attachments:
        for key, value in attachment.items():
            if key == "kind":
                continue
            if key == "confidence":
                if CONFIDENCE_ORDER.get(value, 0) > CONFIDENCE_ORDER.get(result[key], 0):
                    result[key] = value
                continue
            if key == "risk":
                if value:
                    existing = result.get("risk") or []
                    result["risk"] = existing + [item for item in value if item not in existing]
                    result["risk_status"] = "available"
                continue
            if value not in (None, [], {}, "unavailable"):
                result[key] = value
                status_key = f"{key}_status"
                if status_key in result:
                    result[status_key] = "available"
    return result


def _normal_method(method: str | None) -> str:
    return (method or "").upper()


def _normal_path(path: str | None) -> str:
    if not path:
        return ""
    return path if path.startswith("/") else f"/{path}"


def resolve_endpoint(evidence: dict, method: str, path: str) -> dict:
    endpoints = evidence.get("repository", {}).get("api_endpoints", {}).get("endpoints", [])
    wanted_method = _normal_method(method)
    wanted_path = _normal_path(path)
    for index, endpoint in enumerate(endpoints):
        endpoint_method = _normal_method(endpoint.get("method"))
        method_matches = endpoint_method in {wanted_method, "ANY"} or wanted_method == endpoint_method
        if method_matches and _normal_path(endpoint.get("path")) == wanted_path:
            return normalize_resolution(
                kind="endpoint",
                file=endpoint.get("file"),
                line=endpoint.get("line"),
                symbol=endpoint.get("handler"),
                confidence="exact",
                evidence_path=f"repository.api_endpoints.endpoints[{index}]",
            )
    result = empty_resolution("endpoint")
    result["method"] = wanted_method
    result["path"] = wanted_path
    return result


def _codeowners_path(repo_path: Path) -> Path | None:
    for rel in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        path = repo_path / rel
        if path.exists():
            return path
    return None


def _parse_codeowners_line(line: str) -> tuple[str, list[str]] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split()
    if len(parts) < 2:
        return None
    return parts[0], parts[1:]


def _codeowners_matches(pattern: str, file_path: str) -> bool:
    normalized = pattern.lstrip("/")
    if normalized.endswith("/"):
        return file_path.startswith(normalized)
    if "/" not in normalized:
        return fnmatch.fnmatch(Path(file_path).name, normalized)
    return fnmatch.fnmatch(file_path, normalized)


def resolve_owner(repo_path: Path, file_path: str) -> dict:
    codeowners = _codeowners_path(repo_path)
    if codeowners is None:
        return empty_resolution("owner")

    owners: list[str] | None = None
    for raw_line in codeowners.read_text(encoding="utf-8", errors="ignore").splitlines():
        parsed = _parse_codeowners_line(raw_line)
        if parsed is None:
            continue
        pattern, candidate_owners = parsed
        if _codeowners_matches(pattern, file_path):
            owners = candidate_owners

    if not owners:
        return empty_resolution("owner")
    return normalize_resolution(kind="owner", owner=owners, confidence="inferred")


def resolve_recent_commit(repo_path: Path, file_path: str, line: int | None = None) -> dict:
    del line
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%H%x1f%an%x1f%aI%x1f%s", "--", file_path],
            cwd=repo_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return empty_resolution("commit")
    if proc.returncode != 0 or not proc.stdout.strip():
        return empty_resolution("commit")

    parts = proc.stdout.strip().split("\x1f", 3)
    if len(parts) != 4:
        return empty_resolution("commit")
    sha, author, date, subject = parts
    return normalize_resolution(
        kind="commit",
        commit={"sha": sha, "author": author, "date": date, "subject": subject},
        confidence="weak",
    )


def attach_dependency_evidence(evidence: dict, resolution: dict) -> dict:
    if resolution.get("kind") == "dependency" and resolution.get("dependency"):
        return resolution
    file_path = resolution.get("file")
    if not file_path:
        return resolution
    modules = evidence.get("repository", {}).get("modules", [])
    module = next((entry for entry in modules if entry.get("path") == file_path), None)
    imports = module.get("imports", []) if module else []
    if not imports:
        return resolution
    return merge_resolution(
        resolution,
        normalize_resolution(kind="dependency", dependency=list(imports), confidence="exact"),
    )


def _risk(category: str, severity: str, summary: str, evidence_path: str) -> dict:
    return {
        "category": category,
        "severity": severity,
        "summary": summary,
        "evidence_path": evidence_path,
    }


def attach_risk_evidence(evidence: dict, resolution: dict, max_risks: int = 5) -> dict:
    file_path = resolution.get("file")
    dependencies = set(resolution.get("dependency") or [])
    risks: list[dict[str, Any]] = []
    if file_path:
        for index, finding in enumerate(
            evidence.get("security", {}).get("secrets", {}).get("findings", [])
        ):
            if finding.get("path") == file_path:
                risks.append(
                    _risk(
                        "secret",
                        "high",
                        f"{finding.get('pattern', 'secret')} at {file_path}:{finding.get('line')}",
                        f"security.secrets.findings[{index}]",
                    )
                )

        for index, violation in enumerate(
            evidence.get("architecture", {})
            .get("layer_violations", {})
            .get("violations", [])
        ):
            if violation.get("from") == file_path or violation.get("to") == file_path:
                risks.append(
                    _risk(
                        "architecture",
                        "medium",
                        violation.get("reason") or "architecture layer violation",
                        f"architecture.layer_violations.violations[{index}]",
                    )
                )

    for index, finding in enumerate(
        evidence.get("security", {}).get("dependency_vulnerabilities", {}).get("findings", [])
    ):
        package = finding.get("package")
        if not dependencies or package in dependencies:
            risks.append(
                _risk(
                    "vulnerability",
                    str(finding.get("severity", "unknown")).lower(),
                    f"{package} {finding.get('advisory_id', 'vulnerability')}",
                    f"security.dependency_vulnerabilities.findings[{index}]",
                )
            )

    for index, finding in enumerate(
        evidence.get("security", {}).get("dependency_licenses", {}).get("findings", [])
    ):
        package = finding.get("package")
        if package in dependencies:
            risks.append(
                _risk(
                    "license",
                    str(finding.get("severity", "unknown")).lower(),
                    f"{package} license {finding.get('license', 'unknown')}",
                    f"security.dependency_licenses.findings[{index}]",
                )
            )

    if not risks:
        return resolution
    return merge_resolution(
        resolution,
        normalize_resolution(kind="risk", risk=risks[:max_risks], confidence="inferred"),
    )


def resolve_code_evidence(
    evidence: dict,
    repo_path: Path | None = None,
    *,
    kind: str,
    method: str | None = None,
    path: str | None = None,
    symbol: str | None = None,
    dependency: str | None = None,
) -> dict:
    if kind == "endpoint" and method is not None and path is not None:
        resolution = resolve_endpoint(evidence, method, path)
    elif kind == "symbol" and symbol is not None:
        resolution = _resolve_symbol(evidence, symbol)
    elif kind == "dependency" and dependency is not None:
        resolution = _resolve_dependency(evidence, dependency)
    else:
        resolution = empty_resolution(kind)

    if repo_path is not None and resolution.get("file"):
        resolution = merge_resolution(resolution, resolve_owner(repo_path, resolution["file"]))
        resolution = merge_resolution(
            resolution,
            resolve_recent_commit(repo_path, resolution["file"], resolution.get("line")),
        )
    resolution = attach_dependency_evidence(evidence, resolution)
    resolution = attach_risk_evidence(evidence, resolution)
    return resolution


def _resolve_symbol(evidence: dict, symbol: str) -> dict:
    modules = evidence.get("repository", {}).get("modules", [])
    for module_index, module in enumerate(modules):
        symbols = module.get("symbols", {})
        for group in ("functions", "classes"):
            for symbol_index, entry in enumerate(symbols.get(group, [])):
                if entry.get("name") == symbol:
                    return normalize_resolution(
                        kind="symbol",
                        file=module.get("path"),
                        line=entry.get("start_line"),
                        end_line=entry.get("end_line"),
                        symbol=symbol,
                        confidence="exact",
                        evidence_path=(
                            f"repository.modules[{module_index}].symbols.{group}[{symbol_index}]"
                        ),
                    )
    result = empty_resolution("symbol")
    result["symbol"] = symbol
    return result


def _resolve_dependency(evidence: dict, dependency: str) -> dict:
    matches = []
    for module in evidence.get("repository", {}).get("modules", []):
        if dependency in module.get("imports", []):
            matches.append(module.get("path"))
    if not matches:
        result = empty_resolution("dependency")
        result["dependency"] = dependency
        return result
    return normalize_resolution(
        kind="dependency",
        file=matches[0],
        dependency=dependency,
        confidence="exact",
        evidence_path="repository.modules",
    )
