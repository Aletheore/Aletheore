"""Graph-validated relationships, bounded workflows and task routing."""
from __future__ import annotations

from dataclasses import asdict

from .claims import fingerprint, is_test
from .index import Index


def relationships(index: Index) -> list[dict]:
    result = []
    for edge in index.edges:
        data = {"from": edge.caller, "to": edge.target, "label": edge.kind,
                "consumer": edge.consumer, "revision": index.source.revision,
                "evidence": index.source.excerpt(edge.path, edge.line, edge.end)}
        data["id"] = fingerprint(data)
        result.append(data)
    return result


def validate_relationship(index: Index, relationship: dict, allowed: dict | None = None) -> None:
    allowed = allowed if allowed is not None else {r["id"]: r for r in relationships(index)}
    if allowed.get(relationship.get("id")) != relationship:
        raise ValueError("Relationship is stale or is not supported by the indexed source graph")


def workflows(index: Index, limit: int = 5, targets: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    if not 0 <= limit <= 5:
        raise ValueError("Workflow count must be between zero and five")
    edges = relationships(index)
    by_id = {r["id"]: r for r in edges}
    calls = {}
    for edge in edges:
        if edge["label"] == "call":
            calls.setdefault(edge["from"], []).append(edge)
    candidates = []
    for edge in edges:
        if edge["label"] == "callback_argument":
            # The call and callback argument must be observed at the same site.
            partner = next((r for r in calls.get(edge["from"], [])
                            if r["to"] == edge["consumer"] and r["evidence"]["path"] == edge["evidence"]["path"]
                            and r["evidence"]["start_line"] == edge["evidence"]["start_line"]), None)
            if partner:
                candidates.append((100, f"Callback contract: {edge['to']} → {edge['consumer']}", [edge, partner]))
        elif edge["label"] == "dynamic_attribute":
            candidates.append((90, f"Dynamic consumer: {edge['from']} → {edge['to']}", [edge]))
        elif edge["label"] == "call" and not is_test(edge["evidence"]["path"]):
            for next_edge in calls.get(edge["to"], []):
                if next_edge["to"] == edge["from"]:
                    continue
                if len({e["evidence"]["path"] for e in [edge, next_edge]}) > 1:
                    candidates.append((60, f"Call path: {edge['from']} → {next_edge['to']}", [edge, next_edge]))
    candidates.sort(key=lambda c: (-c[0], c[1], tuple(r["id"] for r in c[2])))
    result, signatures, covered = [], set(), set()
    for priority, title, steps in candidates:
        if len(result) >= limit:
            break
        signature = tuple((r["from"], r["to"], r["label"]) for r in steps)
        nodes = {n for r in steps for n in [r["from"], r["to"], r["consumer"]] if n}
        if targets is not None and not (nodes & targets):
            continue
        # Prefer different contracts to many test sites for the same dependency.
        if signature in signatures or nodes <= covered:
            continue
        for step in steps:
            validate_relationship(index, step, by_id)
        definitions = []
        destinations = {r['consumer'] for r in steps if r['consumer']}
        destinations.add(steps[-1]['to'])
        for key in sorted(destinations):
            symbol = index.symbols.get(key)
            if symbol:
                end = symbol.end if symbol.end - symbol.start <= 100 else symbol.start + 24
                evidence = index.source.excerpt(symbol.path, symbol.start, end)
                evidence.update(subject=key, partial=end < symbol.end)
                definitions.append(evidence)
        data = {"title": title, "revision": index.source.revision,
                "relationship_ids": [s["id"] for s in steps],
                "files": sorted({n.split("::", 1)[0] for n in nodes}),
                "steps": steps, "priority": priority, "definitions": definitions,
                "interpretation": "Observed dependency path, not a proof of runtime ordering or a defect."}
        data["id"] = fingerprint(data)
        result.append(data)
        signatures.add(signature)
        covered.update(nodes)
    return edges, result


def validate_workflow(index: Index, workflow: dict) -> None:
    if workflow.get("revision") != index.source.revision:
        raise ValueError("Stale workflow revision")
    expected_id = fingerprint({k: v for k, v in workflow.items() if k != "id"})
    if expected_id != workflow.get("id"):
        raise ValueError("Workflow content changed without regeneration")
    allowed = {r["id"]: r for r in relationships(index)}
    if workflow.get("relationship_ids") != [s["id"] for s in workflow.get("steps", [])] or not workflow.get("steps"):
        raise ValueError("Workflow steps are missing or mismatched")
    for step in workflow["steps"]:
        validate_relationship(index, step, allowed)
    nodes = {n for step in workflow['steps'] for n in [step['from'], step['to'], step['consumer']] if n}
    for evidence in workflow.get('definitions', []):
        symbol = index.symbols.get(evidence.get('subject'))
        if not symbol or symbol.key not in nodes:
            raise ValueError('Workflow definition is unrelated to the path')
        expected_end = symbol.end if symbol.end - symbol.start <= 100 else symbol.start + 24
        actual = index.source.excerpt(symbol.path, symbol.start, expected_end)
        if any(evidence.get(k) != value for k, value in actual.items()) or evidence.get('partial') != (expected_end < symbol.end):
            raise ValueError('Workflow definition does not match source')
    for left, right in zip(workflow["steps"], workflow["steps"][1:]):
        connected = left["to"] == right["from"] or (left["label"] == "callback_argument" and
                    left["from"] == right["from"] and left["consumer"] == right["to"])
        if not connected:
            raise ValueError("Disconnected workflow steps")


def render_workflow(workflow: dict) -> str:
    lines = ["# " + workflow["title"], "", "Revision: `" + workflow["revision"] + "`.", "", workflow["interpretation"], ""]
    for number, step in enumerate(workflow["steps"], 1):
        e = step["evidence"]
        lines += [f"## {number}. {step['label']}", "", f"`{step['from']}` → `{step['to']}`", ""]
        if step["consumer"]:
            lines += ["Consumer: `" + step["consumer"] + "`.", ""]
        lines += [f"Evidence: `{e['path']}:{e['start_line']}-{e['end_line']}`; blob `{e['blob']}`.",
                  "```python", e["source"], "```", ""]
    for evidence in workflow.get('definitions', []):
        lines += ['## Destination implementation: ' + evidence['subject'], '',
                  f"Evidence: `{evidence['path']}:{evidence['start_line']}-{evidence['end_line']}`."]
        if evidence['partial']:
            lines += ['Partial excerpt; inspect the remaining implementation.']
        lines += ['```python', evidence['source'], '```', '']
    return "\n".join(lines)


def routing(index: Index, paths: list[str], flows: list[dict]) -> dict:
    rows = {}
    for path in paths:
        tests = sorted({e.path for e in index.edges if e.target.startswith(path + "::") and is_test(e.path)})
        neighbors = sorted({e.path for e in index.edges if e.target.startswith(path + "::") and e.path != path})
        rows[path] = {"document": "files/" + path + ".md", "tests": tests,
                      "consumer_files": neighbors,
                      "workflows": ["workflows/" + f["id"] + ".md" for f in flows if path in f["files"]]}
    return {"schema_version": 2, "revision": index.source.revision, "if_you_change": rows}


def render_routes(data: dict) -> str:
    lines = ["# Change routing", "", "Revision: `" + data["revision"] + "`.", "", "If you change a file, start with its contracts, then its observed consumers and tests.", ""]
    for path, row in data["if_you_change"].items():
        lines += ["## " + path, "", f"- [Change safety]({row['document']})"]
        lines += [f"- [Related workflow]({p})" for p in row["workflows"]]
        lines += ["- Test source: `" + p + "`." for p in row["tests"]]
        if not row["tests"]:
            lines.append("- No resolved test dependency; coverage is unknown.")
        lines.append("")
    return "\n".join(lines)
