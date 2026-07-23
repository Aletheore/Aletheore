"""AIRview generation: naming, writing, and evidence-grounding on top of
the deterministic briefs/diagrams in aletheore.wiki_mapping/wiki_diagrams.

Naming always uses Flash - cheap, fast, low-stakes (just picking a
readable label for a cluster the scanner already found). Writing uses
the pricing tier's model (see scan_worker/model_tiers.py) for the
one-time initial build, and Flash/DeepSeek for every incremental update
after that regardless of tier - frequent, on every push, so it stays
cheap even for higher tiers. Both use this module's same functions -
which adapter to pass in is the caller's decision (see jobs.py).

Every model response is validated against the deterministic brief it was
given before being trusted: a file, function, or line number the model
returns that isn't actually in the brief is dropped, never stored. This
module never touches the database - it takes evidence and adapters in,
returns plain dict records out.
"""

import json

from aletheore.citation_verifier import verify_citations
from aletheore.wiki_diagrams import build_overview_diagram, build_subsystem_diagram
from aletheore.wiki_mapping import build_cluster_briefs

FLASH_MODEL = "deepseek-v4-flash"
UPDATE_MODEL = "deepseek-v4-pro"

NAMING_SYSTEM_PROMPT = """You name subsystems of a codebase for a generated wiki. You are given a
JSON array of clusters, each with a cluster_id and a list of file paths. Respond with ONLY a JSON
object mapping each cluster_id (as a string) to a short, human-readable subsystem name (2-4 words,
title case, e.g. "Authentication", "Payment Webhooks", "Health Monitoring"). No other text, no
markdown fences."""

SUBSYSTEM_WRITING_SYSTEM_PROMPT = """You write one page of a codebase wiki for a single subsystem.
You are given the subsystem's name and a JSON brief listing its files and each file's key
functions/classes with line numbers. Respond with ONLY a JSON object with this shape:
{"description": "2-4 sentence overview of what this subsystem does and why it exists",
 "files": [{"path": "<exact path from the brief>", "role": "1-2 sentence description of this
 file's responsibility", "key_symbols": [{"name": "<exact name from the brief>", "line": <exact
 start_line from the brief>, "explanation": "one sentence on what it does"}]}]}
Only describe files and symbols that appear in the brief - never invent a file, function, or line
number that isn't there, and never cite a file that isn't in this subsystem's file list. If a file
has no key symbols, return an empty key_symbols list for it. No markdown fences."""

OVERVIEW_WRITING_SYSTEM_PROMPT = """You write the landing page of a codebase wiki. You are given a
JSON array of subsystems, each with a name and description already written. Respond with ONLY a
JSON object: {"description": "3-5 sentence overview of the whole system - what it does, and how
the subsystems listed relate to each other"}. Do not invent subsystems or relationships beyond
what's given. No markdown fences."""


def _parse_json_object(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def propose_cluster_names(briefs: list[dict], naming_adapter) -> dict[int, str]:
    if not briefs:
        return {}
    payload = [{"cluster_id": b["cluster_id"], "files": [f["path"] for f in b["files"]]} for b in briefs]
    raw = naming_adapter.simple_completion(NAMING_SYSTEM_PROMPT, json.dumps(payload), cwd=".")
    parsed = _parse_json_object(raw) or {}

    names: dict[int, str] = {}
    for brief in briefs:
        cid = brief["cluster_id"]
        proposed = parsed.get(str(cid))
        names[cid] = proposed if isinstance(proposed, str) and proposed.strip() else brief["fallback_name"]
    return names


def _symbol_matches_brief(symbol: dict, known_symbols: list[dict]) -> bool:
    return any(
        symbol.get("name") == known["name"] and symbol.get("line") == known["start_line"]
        for known in known_symbols
    )


def _sanitize_written_files(written_files, brief_files: list[dict]) -> list[dict]:
    if not isinstance(written_files, list):
        return []
    brief_by_path = {f["path"]: f for f in brief_files}

    sanitized = []
    for entry in written_files:
        if not isinstance(entry, dict):
            continue
        brief_file = brief_by_path.get(entry.get("path"))
        if brief_file is None:
            continue  # file not in this subsystem's brief - drop, don't trust
        role = entry.get("role")
        if not isinstance(role, str) or not role.strip():
            continue
        key_symbols = [
            {"name": s["name"], "line": s["line"], "explanation": s.get("explanation", "")}
            for s in entry.get("key_symbols", [])
            if isinstance(s, dict) and _symbol_matches_brief(s, brief_file["key_symbols"])
        ]
        sanitized.append({"path": brief_file["path"], "role": role.strip(), "key_symbols": key_symbols})
    return sanitized


def build_subsystem_record(evidence: dict, cluster: dict, brief: dict, name: str, writing_adapter) -> dict | None:
    user_prompt = json.dumps({"name": name, "brief": brief})
    raw = writing_adapter.simple_completion(SUBSYSTEM_WRITING_SYSTEM_PROMPT, user_prompt, cwd=".")
    parsed = _parse_json_object(raw)
    if parsed is None or not isinstance(parsed.get("description"), str) or not parsed["description"].strip():
        return None

    description = parsed["description"].strip()
    # The description is free text and could still smuggle a fabricated
    # file:line citation even though the structured fields below are
    # validated directly - check it the same way audit report citations
    # are checked.
    if not verify_citations(description, evidence)["all_verified"]:
        return None

    return {
        "subsystem_id": str(cluster["id"]),
        "name": name,
        "description": description,
        "files": _sanitize_written_files(parsed.get("files"), brief["files"]),
        "diagram_mermaid": build_subsystem_diagram(evidence, cluster),
    }


def affected_cluster_ids(evidence: dict, changed_files: list[str]) -> set[int]:
    """Maps a list of changed file paths to the clusters they belong to,
    for incremental updates - only these clusters need regenerating.
    """
    changed = set(changed_files)
    return {
        cluster["id"]
        for cluster in evidence.get("architecture", {}).get("clusters", [])
        if changed & set(cluster.get("modules", []))
    }


def generate_subsystems(
    evidence: dict,
    naming_adapter,
    writing_adapter,
    cluster_ids: set[int] | None = None,
) -> list[dict]:
    """Generates subsystem records. If cluster_ids is given, only those
    clusters are processed (incremental update); otherwise every cluster
    in the evidence is (full build).
    """
    briefs = build_cluster_briefs(evidence)
    if cluster_ids is not None:
        briefs = [b for b in briefs if b["cluster_id"] in cluster_ids]
    if not briefs:
        return []

    names = propose_cluster_names(briefs, naming_adapter)
    clusters_by_id = {c["id"]: c for c in evidence.get("architecture", {}).get("clusters", [])}

    records = []
    for brief in briefs:
        cid = brief["cluster_id"]
        cluster = clusters_by_id.get(cid)
        if cluster is None:
            continue
        record = build_subsystem_record(evidence, cluster, brief, names[cid], writing_adapter)
        if record is not None:
            records.append(record)
    return records


def generate_overview(evidence: dict, all_subsystem_records: list[dict], writing_adapter) -> dict:
    """all_subsystem_records must be the full current set (freshly
    generated ones merged with unchanged ones already in storage) - the
    overview narrates how every subsystem relates, not just the ones that
    changed this run.
    """
    cluster_names = {int(r["subsystem_id"]): r["name"] for r in all_subsystem_records}
    diagram = build_overview_diagram(evidence, cluster_names)

    payload = [{"name": r["name"], "description": r["description"]} for r in all_subsystem_records]
    raw = writing_adapter.simple_completion(OVERVIEW_WRITING_SYSTEM_PROMPT, json.dumps(payload), cwd=".")
    parsed = _parse_json_object(raw)
    description = parsed.get("description") if parsed else None
    if not isinstance(description, str) or not description.strip():
        description = "Overview description unavailable."
    elif not verify_citations(description, evidence)["all_verified"]:
        description = "Overview description unavailable."

    return {"description": description, "diagram_mermaid": diagram}
