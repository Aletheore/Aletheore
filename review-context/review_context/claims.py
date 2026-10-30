"""Deterministic, source-backed change-safety claims; no inferred invariants."""
from __future__ import annotations

import ast
import hashlib
import json
from collections import defaultdict

from .index import Index

SECTIONS = {
    "invariants": "Invariants and enforced checks",
    "failure": "Failure behavior",
    "lifecycle": "State and resource lifecycle",
    "configuration": "Configuration reads",
    "tests": "Tests and dynamic consumers",
    "callers": "Callers and return-value contracts",
}


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:20]


def is_test(path: str) -> bool:
    return any(p in ("test", "tests") for p in path.split("/")) or path.rsplit("/", 1)[-1].startswith("test_")


def validate_claim(index: Index, claim: dict) -> None:
    """Validate identity and source provenance, not arbitrary prose semantics."""
    if claim.get("category") not in SECTIONS or not claim.get("change_impact"):
        raise ValueError("Claim lacks a material change-safety category or impact")
    if not claim.get("evidence"):
        raise ValueError("Unsupported claim: no source evidence")
    payload = {k: v for k, v in claim.items() if k != "id"}
    if fingerprint(payload) != claim.get("id"):
        raise ValueError("Claim content changed without regeneration")
    for evidence in claim["evidence"]:
        if evidence.get("revision") != index.source.revision:
            raise ValueError("Claim revision is stale")
        start, end = evidence["start_line"], evidence["end_line"]
        length = len(index.source.read(evidence["path"]).splitlines())
        if not 1 <= start <= end <= length:
            raise ValueError("Claim evidence bounds are invalid")
        actual = index.source.excerpt(evidence["path"], start, end)
        if any(evidence.get(k) != value for k, value in actual.items()):
            raise ValueError("Claim evidence no longer matches the source")


def change_safety(index: Index, path: str) -> dict:
    claims = []
    seen = set()

    def add(category, subject, text, impact, evidence_path, start, end, extra_evidence=()):
        key = (category, subject, text)
        if key in seen:
            return
        seen.add(key)
        evidence = index.source.excerpt(evidence_path, start, end)
        claim = {
            "category": category, "subject": subject, "text": text,
            "change_impact": impact, "basis": "observed_source_syntax",
            "evidence": [evidence, *extra_evidence],
        }
        claim["id"] = fingerprint(claim)
        validate_claim(index, claim)
        claims.append(claim)

    for node in ast.walk(index.trees[path]):
        if not hasattr(node, "lineno"):
            continue
        subject = index.scope(path, node)
        if isinstance(node, ast.Assert):
            add("invariants", subject, f"Asserts `{ast.unparse(node.test)}`.",
                "Check callers before changing this enforced condition; Python optimization may disable assertions.",
                path, node.lineno, node.end_lineno)
        elif isinstance(node, ast.ExceptHandler):
            exception = ast.unparse(node.type) if node.type else "all exceptions"
            # Observe syntax only: a nested raise does not prove every branch rethrows.
            action_nodes = [n for child in node.body for n in ast.walk(child)
                            if isinstance(n, (ast.Return, ast.Raise, ast.Pass))]
            actions = sorted({type(n).__name__.lower() for n in action_nodes})
            # Cite one occurrence of each action, even when outside the header excerpt.
            cited = {}
            for action in action_nodes:
                cited.setdefault(type(action).__name__, action)
            extra = [index.source.excerpt(path, n.lineno, n.end_lineno) for n in cited.values()]
            suffix = f" Handler contains {', '.join(actions)} statements." if actions else ""
            add("failure", subject, f"Handles `{exception}`.{suffix}",
                "Inspect which results or failures escape this handler and how callers interpret them.",
                path, node.lineno, min(node.end_lineno, node.lineno + 15), extra)
        elif isinstance(node, ast.Raise):
            target = ast.unparse(node.exc) if node.exc else "the active exception"
            add("failure", subject, f"Raises `{target}`.",
                "Check failure compatibility with callers and recovery paths.", path, node.lineno, node.end_lineno)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            resources = ", ".join(ast.unparse(item.context_expr) for item in node.items)
            add("lifecycle", subject, f"Uses a context manager: `{resources}`.",
                "Preserve acquisition, cleanup, and exception boundaries; the context manager implementation determines their semantics.",
                path, node.lineno, max(item.context_expr.end_lineno for item in node.items))
        elif isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in ("os.getenv", "os.environ.get") and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                add("configuration", subject, f"Reads environment key `{node.args[0].value}` through `{name}`.",
                    "Check absent-value behavior and callers before changing this configuration contract.", path, node.lineno, node.end_lineno)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in ("close", "commit", "rollback", "put_many", "flush", "acquire", "release"):
                add("lifecycle", subject, f"Calls `{name}`.",
                    "Inspect the called implementation for storage, cleanup or ownership effects; the method name alone does not prove those effects.",
                    path, node.lineno, node.end_lineno)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and ast.unparse(node.value) == "os.environ":
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                add("configuration", subject, f"Reads environment key `{node.slice.value}` by subscription.",
                    "Check missing-key handling before changing this configuration read.", path, node.lineno, node.end_lineno)

    targets = {s.key for s in index.symbols.values() if s.path == path}
    targets.update(path + "::" + name for name in index.imports[path])
    for target in sorted(targets):
        for edge in index.incoming.get(target, []):
            if edge.caller == target:
                continue
            category = "tests" if is_test(edge.path) else "callers"
            if edge.kind == "callback_argument":
                text = f"`{edge.caller}` passes `{target}` as a callback to `{edge.consumer}`."
                impact = "Inspect the consumer's handling of return values, partial work, storage and repeated runs."
            elif edge.kind == "dynamic_attribute":
                text = f"`{edge.caller}` references module attribute `{target}` through a dotted string."
                impact = "Check these consumers before removing or renaming the module attribute, including imported attributes."
            else:
                text = f"`{edge.caller}` calls `{target}`."
                impact = "Check this caller's input, output and failure assumptions before changing the target."
            add(category, target, text, impact, edge.path, edge.line, edge.end)

    claims.sort(key=lambda c: (list(SECTIONS).index(c["category"]), c["subject"], c["evidence"][0]["start_line"], c["id"]))
    grouped = defaultdict(list)
    for claim in claims:
        grouped[claim["category"]].append(claim["id"])
    return {
        "schema_version": 2, "revision": index.source.revision, "path": path,
        "claims": claims,
        "sections": {key: {"title": title, "claim_ids": grouped[key],
                           "gap": "No supported claim extracted; this is not a guarantee of absence." if not grouped[key] else None}
                     for key, title in SECTIONS.items()},
        "materiality_policy": "Keep observed behavior or dependencies that can change a safe change plan; omit symbol inventories and unsupported descriptions.",
        "limitations": ["Citation validation proves provenance, not every semantic inference.",
                        "Static Python rules are incomplete; missing claims are explicit coverage gaps."],
    }


def render_safety(data: dict, claim_limit: int | None = 12) -> str:
    claims = {c["id"]: c for c in data["claims"]}
    lines = ["## Change safety", ""]
    for section in data["sections"].values():
        lines += ["### " + section["title"], ""]
        ids = section["claim_ids"] if claim_limit is None else section["claim_ids"][:claim_limit]
        for key in ids:
            claim = claims[key]
            evidence = claim["evidence"][0]
            citations = ', '.join(f"`{e['path']}:{e['start_line']}-{e['end_line']}`" for e in claim['evidence'])
            lines.append(f"- [{key}] {claim['text']} {claim['change_impact']} "
                         f"Evidence: {citations}.")
        if section["gap"]:
            lines.append(section["gap"])
        if len(ids) < len(section["claim_ids"]):
            lines.append(f"{len(section['claim_ids']) - len(ids)} additional claims are in the structured contract document.")
        lines.append("")
    return "\n".join(lines)
