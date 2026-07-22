"""End-to-end verification of the Live Wiki pipeline against this repo's
own real evidence file (.aletheore/air.json), with only the paid LLM API
calls mocked. Everything else - cluster brief extraction, mermaid diagram
generation, citation/symbol grounding validation, Postgres storage, and
the async dashboard read path - runs for real.

This is deliberately separate from test_live_wiki.py and test_jobs.py,
which exercise the same modules against small synthetic evidence and
mock generate_subsystems/_store_wiki_generation at a higher level. Here
the goal is the opposite: real evidence, real orchestration functions,
fake adapters only at the network boundary.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app_server.db import get_wiki_overview, get_wiki_subsystem, list_wiki_subsystems
from scan_worker import live_wiki
from scan_worker.db import (
    delete_wiki_subsystems_not_in,
    list_wiki_subsystems as sync_list_wiki_subsystems,
    upsert_wiki_overview,
    upsert_wiki_subsystem,
)
from scan_worker.jobs import _store_wiki_generation

# tests/conftest.py sets this as the DATABASE_URL default before any test
# runs, so it's always the real test Postgres these sync psycopg calls
# need to talk to - matching what get_settings().database_url would
# resolve to in production.
TEST_DATABASE_URL = os.environ["DATABASE_URL"]

AIR_JSON_PATH = Path(__file__).resolve().parents[2] / ".aletheore" / "air.json"

# Chosen because it's a single real file with real functions and no
# dependency on other clusters - small enough to assert on exactly,
# while still being genuine scanner output, not a fixture.
REAL_SUBSYSTEM_FILE = "scripts/audit/vdp001_acceptance_audit.py"
REAL_CLUSTER_ID = 14


@pytest.fixture(scope="module")
def real_evidence() -> dict:
    if not AIR_JSON_PATH.exists():
        pytest.skip(f"no real evidence file at {AIR_JSON_PATH} - run `aletheore scan` first")
    evidence = json.loads(AIR_JSON_PATH.read_text())
    clusters = {c["id"]: c for c in evidence["architecture"]["clusters"]}
    cluster = clusters.get(REAL_CLUSTER_ID)
    if cluster is None or cluster["modules"] != [REAL_SUBSYSTEM_FILE]:
        pytest.skip("air.json has drifted from the fixed cluster this test targets - rescan needed")
    return evidence


def test_build_cluster_briefs_reflects_real_evidence(real_evidence):
    from aletheore.wiki_mapping import build_cluster_briefs

    briefs = build_cluster_briefs(real_evidence)
    assert len(briefs) == len(real_evidence["architecture"]["clusters"])

    brief = next(b for b in briefs if b["cluster_id"] == REAL_CLUSTER_ID)
    assert [f["path"] for f in brief["files"]] == [REAL_SUBSYSTEM_FILE]
    symbol_names = {s["name"] for s in brief["files"][0]["key_symbols"]}
    assert symbol_names == {
        "split_front_matter",
        "req_group",
        "body_between",
        "classify_requirement",
        "main",
        "valid_fixture",
    }


def test_build_overview_diagram_reflects_real_clusters(real_evidence):
    from aletheore.wiki_diagrams import build_overview_diagram

    diagram = build_overview_diagram(real_evidence)
    assert diagram.startswith("flowchart TD")
    assert f'C{REAL_CLUSTER_ID}["Cluster {REAL_CLUSTER_ID}"]' in diagram


def _grounded_naming_adapter() -> MagicMock:
    adapter = MagicMock()

    def respond(system_prompt, user_prompt, cwd="."):
        payload = json.loads(user_prompt)
        return json.dumps({str(c["cluster_id"]): f"Cluster {c['cluster_id']} Subsystem" for c in payload})

    adapter.simple_completion.side_effect = respond
    return adapter


def _grounded_writing_adapter() -> MagicMock:
    """A fake 'model' that only ever echoes back file paths, symbol names,
    and line numbers it was actually handed in the brief - the honest-actor
    case. Proves the pipeline accepts and stores well-grounded output.
    """
    adapter = MagicMock()

    def respond(system_prompt, user_prompt, cwd="."):
        payload = json.loads(user_prompt)
        if "brief" in payload:  # subsystem writing prompt
            brief = payload["brief"]
            first_file = brief["files"][0]
            first_symbol = first_file["key_symbols"][0] if first_file["key_symbols"] else None
            description = f"This subsystem centers on {first_file['path']}."
            if first_symbol:
                description += f" See {first_file['path']}:{first_symbol['start_line']} for its entry point."
            return json.dumps(
                {
                    "description": description,
                    "files": [
                        {
                            "path": f["path"],
                            "role": f"Implements part of {payload['name']}.",
                            "key_symbols": [
                                {"name": s["name"], "line": s["start_line"], "explanation": f"Does {s['name']}."}
                                for s in f["key_symbols"]
                            ],
                        }
                        for f in brief["files"]
                    ],
                }
            )
        else:  # overview writing prompt - payload is a list of {name, description}
            names = ", ".join(item["name"] for item in payload)
            return json.dumps({"description": f"This system is made up of: {names}."})

    adapter.simple_completion.side_effect = respond
    return adapter


def _fabricated_file_writing_adapter() -> MagicMock:
    """A fake 'model' that invents a file/line that isn't in the brief, but
    otherwise writes a clean description. Proves _sanitize_written_files
    strips the fabricated file/symbol out of the structured fields rather
    than trusting it, while the record itself (grounded in its remaining
    real fields) is still kept.
    """
    adapter = MagicMock()
    adapter.simple_completion.return_value = json.dumps(
        {
            "description": "This subsystem does important things.",
            "files": [
                {
                    "path": "totally/made/up/file.py",
                    "role": "A file that does not exist.",
                    "key_symbols": [{"name": "fake_function", "line": 9999, "explanation": "Fabricated."}],
                }
            ],
        }
    )
    return adapter


def _fabricated_citation_writing_adapter() -> MagicMock:
    """A fake 'model' whose free-text description cites a file:line that
    doesn't exist in evidence at all. Proves build_subsystem_record drops
    the whole record rather than storing an ungrounded claim, per the same
    citation-verification rule the audit report generator uses.
    """
    adapter = MagicMock()
    adapter.simple_completion.return_value = json.dumps(
        {
            "description": "This logic was introduced in totally/made/up/file.py:9999 and is critical.",
            "files": [],
        }
    )
    return adapter


def test_generate_subsystems_grounds_output_in_real_evidence(real_evidence):
    naming_adapter = _grounded_naming_adapter()
    writing_adapter = _grounded_writing_adapter()

    records = live_wiki.generate_subsystems(
        real_evidence, naming_adapter, writing_adapter, cluster_ids={REAL_CLUSTER_ID}
    )

    assert len(records) == 1
    record = records[0]
    assert record["subsystem_id"] == str(REAL_CLUSTER_ID)
    assert record["name"] == f"Cluster {REAL_CLUSTER_ID} Subsystem"
    assert REAL_SUBSYSTEM_FILE in record["description"]
    assert len(record["files"]) == 1
    assert record["files"][0]["path"] == REAL_SUBSYSTEM_FILE
    stored_symbol_names = {s["name"] for s in record["files"][0]["key_symbols"]}
    assert stored_symbol_names == {
        "split_front_matter",
        "req_group",
        "body_between",
        "classify_requirement",
        "main",
        "valid_fixture",
    }
    assert record["diagram_mermaid"].startswith("flowchart TD")


def test_generate_subsystems_strips_fabricated_files_but_keeps_record(real_evidence):
    naming_adapter = _grounded_naming_adapter()
    writing_adapter = _fabricated_file_writing_adapter()

    records = live_wiki.generate_subsystems(
        real_evidence, naming_adapter, writing_adapter, cluster_ids={REAL_CLUSTER_ID}
    )

    assert len(records) == 1
    # The fabricated file isn't in this cluster's brief, so it's silently
    # dropped rather than stored - never "made up a file", never crashed.
    assert records[0]["files"] == []


def test_generate_subsystems_rejects_fabricated_citation(real_evidence):
    naming_adapter = _grounded_naming_adapter()
    writing_adapter = _fabricated_citation_writing_adapter()

    records = live_wiki.generate_subsystems(
        real_evidence, naming_adapter, writing_adapter, cluster_ids={REAL_CLUSTER_ID}
    )

    # The description cites a file:line that isn't in evidence at all, so
    # verify_citations fails and the whole record is dropped rather than
    # stored with an invented claim.
    assert records == []


@pytest.mark.asyncio
async def test_full_pipeline_stores_and_reads_back_through_dashboard_api(pool, real_evidence):
    installation_id = 9001
    repo_full_name = "octocat/aletheore-e2e"
    from app_server.db import upsert_installation

    await upsert_installation(pool, installation_id, "octocat")

    naming_adapter = _grounded_naming_adapter()
    writing_adapter = _grounded_writing_adapter()
    records = live_wiki.generate_subsystems(
        real_evidence, naming_adapter, writing_adapter, cluster_ids={REAL_CLUSTER_ID}
    )
    assert len(records) == 1

    # Exercises the real production storage orchestration function - not a
    # re-implementation of it - against a real Postgres.
    _store_wiki_generation(
        TEST_DATABASE_URL, installation_id, repo_full_name, real_evidence, records, writing_adapter, "deadbeef"
    )

    # Read back through the exact async functions the dashboard route
    # (Task 8) uses, proving the write path (sync psycopg) and read path
    # (async asyncpg) agree on what's in the database.
    overview = await get_wiki_overview(pool, installation_id, repo_full_name)
    assert overview is not None
    assert "Cluster 14 Subsystem" in overview["description"]
    assert overview["diagram_mermaid"].startswith("flowchart TD")
    assert overview["source_commit"] == "deadbeef"

    subsystems = await list_wiki_subsystems(pool, installation_id, repo_full_name)
    assert len(subsystems) == 1
    assert subsystems[0]["subsystem_id"] == str(REAL_CLUSTER_ID)
    assert subsystems[0]["files"] == records[0]["files"]

    detail = await get_wiki_subsystem(pool, installation_id, repo_full_name, str(REAL_CLUSTER_ID))
    assert detail is not None
    assert detail["name"] == "Cluster 14 Subsystem"
    assert detail["description"] == records[0]["description"]


@pytest.mark.asyncio
async def test_prune_removes_subsystems_whose_cluster_no_longer_exists(pool, real_evidence):
    installation_id = 9002
    repo_full_name = "octocat/aletheore-e2e-prune"
    from app_server.db import upsert_installation

    await upsert_installation(pool, installation_id, "octocat")

    # Simulate an old subsystem page for a cluster id that doesn't exist
    # in this evidence at all (e.g. it was merged/deleted since last scan).
    upsert_wiki_subsystem(
        TEST_DATABASE_URL,
        installation_id,
        repo_full_name,
        "999999",
        "Stale Cluster",
        "This cluster no longer exists.",
        [],
        "flowchart TD",
        None,
    )
    upsert_wiki_overview(TEST_DATABASE_URL, installation_id, repo_full_name, "stale", "flowchart TD")

    current_cluster_ids = [str(c["id"]) for c in real_evidence["architecture"]["clusters"]]
    delete_wiki_subsystems_not_in(TEST_DATABASE_URL, installation_id, repo_full_name, current_cluster_ids)

    remaining = sync_list_wiki_subsystems(TEST_DATABASE_URL, installation_id, repo_full_name)
    assert remaining == []
