from collections.abc import Callable
from pathlib import Path
from typing import Any

from aletheore.evidence_resolution import resolve_code_evidence


class ModuleNotFoundInEvidenceError(Exception):
    def __init__(self, file_path: str):
        super().__init__(f"'{file_path}' is not present in evidence.repository.modules")
        self.file_path = file_path


class BranchNotFoundInEvidenceError(Exception):
    def __init__(self, branch_name: str):
        super().__init__(f"'{branch_name}' is not present in evidence.git.branches")
        self.branch_name = branch_name


class SymbolNotFoundInEvidenceError(Exception):
    def __init__(self, module_path: str, symbol_name: str):
        super().__init__(f"'{symbol_name}' is not present in {module_path}'s symbols")
        self.module_path = module_path
        self.symbol_name = symbol_name


def _find_module(evidence: dict, file_path: str) -> dict:
    for module in evidence["repository"]["modules"]:
        if module["path"] == file_path:
            return module
    raise ModuleNotFoundInEvidenceError(file_path)


def find_imports(evidence: dict, target: str | None) -> list[str]:
    return _find_module(evidence, target)["imports"]


def find_imported_by(evidence: dict, target: str | None) -> list[str]:
    return _find_module(evidence, target)["imported_by"]


def find_symbols(evidence: dict, target: str | None) -> dict:
    return _find_module(evidence, target)["symbols"]


def find_symbol_source(
    evidence: dict, repo_path: Path, module_path: str, symbol_name: str
) -> dict:
    module = _find_module(evidence, module_path)
    symbols = module["symbols"]["functions"] + module["symbols"]["classes"]
    entry = next((symbol for symbol in symbols if symbol["name"] == symbol_name), None)
    if entry is None:
        raise SymbolNotFoundInEvidenceError(module_path, symbol_name)

    file_path = repo_path / module_path
    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    source = "\n".join(lines[entry["start_line"] - 1 : entry["end_line"]])

    return {
        "module": module_path,
        "symbol": symbol_name,
        "start_line": entry["start_line"],
        "end_line": entry["end_line"],
        "source": source,
    }


def find_branch(evidence: dict, target: str | None) -> dict:
    for branch in evidence["git"]["branches"]:
        if branch["name"] == target:
            return branch
    raise BranchNotFoundInEvidenceError(target)


def find_ownership(evidence: dict, target: str | None) -> list[dict]:
    return evidence["git"].get("ownership", [])


def find_secrets_for_file(evidence: dict, target: str | None) -> list[dict]:
    return [
        finding
        for finding in evidence["security"]["secrets"]["findings"]
        if finding["path"] == target
    ]


def find_vulnerabilities(evidence: dict, target: str | None) -> dict:
    return evidence["security"]["dependency_vulnerabilities"]


def find_licenses(evidence: dict, target: str | None) -> dict:
    return evidence["security"]["dependency_licenses"]


def find_endpoints(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["api_endpoints"]


def find_code_evidence_for_endpoint(evidence: dict, target: str | None) -> dict:
    if not target or " " not in target.strip():
        return resolve_code_evidence(evidence, kind="endpoint")
    method, path = target.strip().split(maxsplit=1)
    return resolve_code_evidence(evidence, kind="endpoint", method=method, path=path)


def find_code_evidence_for_symbol(evidence: dict, target: str | None) -> dict:
    return resolve_code_evidence(evidence, kind="symbol", symbol=target)


def find_code_evidence_for_dependency(evidence: dict, target: str | None) -> dict:
    return resolve_code_evidence(evidence, kind="dependency", dependency=target)


def find_cluster(evidence: dict, target: str | None) -> dict:
    for cluster in evidence["architecture"]["clusters"]:
        if target in cluster["modules"]:
            return cluster
    raise ModuleNotFoundInEvidenceError(target)


def find_layer_violations(evidence: dict, target: str | None) -> dict:
    return evidence["architecture"]["layer_violations"]


def find_dead_code_evidence(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["dead_code"]


def find_database(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["database"]


def find_infrastructure(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["infrastructure"]


def find_environment_variables(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["environment_variables"]


def find_hotspots(evidence: dict, target: str | None) -> list[dict]:
    return evidence["git"].get("hotspots", [])


QUERY_FUNCTIONS: dict[str, tuple[Callable[[dict, str | None], Any], bool]] = {
    "imports": (find_imports, True),
    "imported-by": (find_imported_by, True),
    "symbols": (find_symbols, True),
    "branch": (find_branch, True),
    "ownership": (find_ownership, False),
    "secrets": (find_secrets_for_file, True),
    "vulnerabilities": (find_vulnerabilities, False),
    "licenses": (find_licenses, False),
    "endpoints": (find_endpoints, False),
    "cluster": (find_cluster, True),
    "layer-violations": (find_layer_violations, False),
    "dead-code": (find_dead_code_evidence, False),
    "hotspots": (find_hotspots, False),
    "database": (find_database, False),
    "infrastructure": (find_infrastructure, False),
    "environment-variables": (find_environment_variables, False),
    "evidence-for-endpoint": (find_code_evidence_for_endpoint, True),
    "evidence-for-symbol": (find_code_evidence_for_symbol, True),
    "evidence-for-dependency": (find_code_evidence_for_dependency, True),
}
