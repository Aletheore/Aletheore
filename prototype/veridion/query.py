from collections.abc import Callable
from typing import Any


class ModuleNotFoundInEvidenceError(Exception):
    def __init__(self, file_path: str):
        super().__init__(f"'{file_path}' is not present in evidence.repository.modules")
        self.file_path = file_path


class BranchNotFoundInEvidenceError(Exception):
    def __init__(self, branch_name: str):
        super().__init__(f"'{branch_name}' is not present in evidence.git.branches")
        self.branch_name = branch_name


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


def find_cluster(evidence: dict, target: str | None) -> dict:
    for cluster in evidence["architecture"]["clusters"]:
        if target in cluster["modules"]:
            return cluster
    raise ModuleNotFoundInEvidenceError(target)


def find_layer_violations(evidence: dict, target: str | None) -> dict:
    return evidence["architecture"]["layer_violations"]


QUERY_FUNCTIONS: dict[str, tuple[Callable[[dict, str | None], Any], bool]] = {
    "imports": (find_imports, True),
    "imported-by": (find_imported_by, True),
    "symbols": (find_symbols, True),
    "branch": (find_branch, True),
    "ownership": (find_ownership, False),
    "secrets": (find_secrets_for_file, True),
    "vulnerabilities": (find_vulnerabilities, False),
    "cluster": (find_cluster, True),
    "layer-violations": (find_layer_violations, False),
}
