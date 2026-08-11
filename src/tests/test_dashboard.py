import asyncio
import json
import tempfile
from pathlib import Path

from starlette.testclient import TestClient

from aletheore.dashboard import (
    _watch_evidence_mtime,
    build_app,
    build_evidence_summary,
    build_graph_summary,
    build_history_summary,
)
from aletheore.evidence import EVIDENCE_VERSION
from tests.air_fixtures import minimal_air_evidence


def make_evidence(scanned_at: str, module_count: int = 2, secrets_count: int = 0) -> dict:
    return {
        "aletheore_version": EVIDENCE_VERSION,
        "scanned_at": scanned_at,
        "repository": {
            "languages": [{"name": "python", "file_count": module_count}],
            "modules": [{"path": f"m{i}.py"} for i in range(module_count)],
            "monorepo": {"detected": False, "workspaces": []},
            "dependency_graph": {"nodes": [], "edges": []},
            "dead_code": {
                "unreachable_modules": [{"path": "old/unused.py", "reason": "no other module imports this file"}],
                "unused_dependencies": [{"ecosystem": "pip", "package": "unused-pkg"}],
            },
        },
        "git": {
            "total_commits": 10,
            "commit_cadence": {
                "weekly_counts": [1, 2, 3],
                "trend": "steady",
                "most_recent_week_partial": False,
            },
            "ownership": [{"path": "m0.py", "top_author": "alice"}],
            "branches": [{"name": "main", "ahead_of_main": 0}],
        },
        "security": {
            "secrets": {
                "findings": [
                    {
                        "path": f"s{i}.py",
                        "pattern": "aws_access_key_id",
                        "match_preview": "AKIA...MNOP",
                        "likely_placeholder": i % 2 == 0,
                    }
                    for i in range(secrets_count)
                ],
                "history_findings": [],
            },
            "dependency_vulnerabilities": {"checked": True, "reason": None, "findings": []},
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["m0.py"], "internal_edges": 0}],
            "layer_violations": {"convention_detected": True, "layers": [], "violations": []},
        },
    }


def test_build_evidence_summary_shape():
    evidence = make_evidence("2026-07-15T12:00:00+00:00", module_count=3, secrets_count=2)

    summary = build_evidence_summary(evidence)

    assert summary["scanned_at"] == "2026-07-15T12:00:00+00:00"
    assert summary["repo_overview"]["module_count"] == 3
    assert summary["repo_overview"]["languages"] == [{"name": "python", "file_count": 3}]
    assert summary["git_activity"]["total_commits"] == 10
    assert summary["git_activity"]["branches"] == [{"name": "main", "ahead_of_main": 0}]
    assert summary["security"]["secrets"]["total_findings"] == 2
    assert summary["security"]["secrets"]["real_findings"] == 1
    assert summary["architecture"]["cluster_count"] == 1
    assert summary["architecture"]["convention_detected"] is True
    assert summary["dead_code"]["unreachable_modules"] == [
        {"path": "old/unused.py", "reason": "no other module imports this file"}
    ]
    assert summary["dead_code"]["unused_dependencies"] == [{"ecosystem": "pip", "package": "unused-pkg"}]


def test_build_history_summary_reads_all_snapshots(tmp_path):
    repo = tmp_path / "repo"
    history_dir = repo / ".aletheore" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "2026-07-15T10-00-00.json").write_text(
        json.dumps(make_evidence("2026-07-15T10:00:00+00:00", module_count=2, secrets_count=0))
    )
    (history_dir / "2026-07-15T11-00-00.json").write_text(
        json.dumps(make_evidence("2026-07-15T11:00:00+00:00", module_count=3, secrets_count=1))
    )

    result = build_history_summary(repo)

    assert len(result) == 2
    assert result[0] == {
        "scanned_at": "2026-07-15T10:00:00+00:00",
        "module_count": 2,
        "secrets_findings": 0,
        "vulnerability_findings": 0,
    }
    assert result[1]["module_count"] == 3
    assert result[1]["secrets_findings"] == 1


def test_build_history_summary_skips_corrupt_snapshots(tmp_path):
    repo = tmp_path / "repo"
    history_dir = repo / ".aletheore" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "2026-07-15T10-00-00.json").write_text("{not valid json")
    (history_dir / "2026-07-15T11-00-00.json").write_text(
        json.dumps(make_evidence("2026-07-15T11:00:00+00:00"))
    )

    result = build_history_summary(repo)

    assert len(result) == 1
    assert result[0]["scanned_at"] == "2026-07-15T11:00:00+00:00"


def test_build_history_summary_empty_when_no_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert build_history_summary(repo) == []


def test_build_graph_summary_annotates_nodes_with_cluster_id():
    evidence = {
        "repository": {
            "dependency_graph": {
                "nodes": ["a.py", "b.py", "c.py"],
                "edges": [["a.py", "b.py"], ["b.py", "c.py"]],
            }
        },
        "architecture": {
            "clusters": [
                {"id": 0, "modules": ["a.py", "b.py"], "internal_edges": 1},
                {"id": 1, "modules": ["c.py"], "internal_edges": 0},
            ]
        },
    }

    result = build_graph_summary(evidence)

    assert result["nodes"] == [
        {"id": "a.py", "cluster": 0},
        {"id": "b.py", "cluster": 0},
        {"id": "c.py", "cluster": 1},
    ]
    assert result["edges"] == [
        {"source": "a.py", "target": "b.py"},
        {"source": "b.py", "target": "c.py"},
    ]
    assert result["clusters"] == evidence["architecture"]["clusters"]


def test_build_graph_summary_handles_unclustered_node():
    evidence = {
        "repository": {"dependency_graph": {"nodes": ["orphan.py"], "edges": []}},
        "architecture": {"clusters": []},
    }

    result = build_graph_summary(evidence)

    assert result["nodes"] == [{"id": "orphan.py", "cluster": None}]


def _deep_merge(base: dict, overrides: dict) -> dict:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def make_repo_with_evidence(tmp_path: Path) -> Path:
    # Merged onto minimal_air_evidence() (schema-valid, every collection
    # empty) rather than written as-is - the dashboard API now routes
    # through read_evidence -> load_evidence_file, which validates full AIR
    # schema shape. make_evidence()'s hand-rolled dict, used directly (in
    # memory, never through this file-writing path) by many other tests
    # here, omits several required keys nothing used to check for.
    repo = tmp_path / "repo"
    aletheore_dir = repo / ".aletheore"
    aletheore_dir.mkdir(parents=True)
    evidence = _deep_merge(minimal_air_evidence(), make_evidence("2026-07-15T12:00:00+00:00"))
    (aletheore_dir / "air.json").write_text(json.dumps(evidence))
    return repo


def test_root_serves_html_page(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'id="app"' in response.text


def test_api_evidence_returns_summary(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["scanned_at"] == "2026-07-15T12:00:00+00:00"
    assert "repo_overview" in body


def test_api_history_returns_list(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/history")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_api_graph_returns_shape(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/graph")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"nodes", "edges", "clusters"}


def test_api_mcp_tools_reflects_the_configured_consent_posture(tmp_path):
    # The dashboard lists what an agent would actually be offered, so it
    # shows the default posture: everything except the evidence-upload tool,
    # which is withheld until ALETHEORE_MCP_ALLOW opts into `external`.
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/mcp-tools")

    assert response.status_code == 200
    tools = response.json()
    assert len(tools) == 27
    names = {t["name"] for t in tools}
    assert "aletheore_managed_audit" not in names
    assert "aletheore_scan" in names
    assert "aletheore_search" in names
    assert "aletheore_index" in names
    assert "aletheore_search_codebase" in names
    assert "aletheore_endpoints" in names
    assert "aletheore_healthcheck" in names
    assert "aletheore_symbol_source" in names
    assert "aletheore_dead_code" in names
    assert "aletheore_hotspots" in names
    assert "aletheore_database" in names
    assert "aletheore_infrastructure" in names
    assert "aletheore_environment_variables" in names


def test_logo_route_serves_the_bundled_png(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/logo.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


# --- SSE stream: shutdown, cancellation, malformed evidence, browser icons ---


def test_watch_evidence_mtime_ends_promptly_when_shutdown_is_signalled():
    """The stream never ends on its own, so uvicorn sat at "Waiting for
    connections to close" until the user pressed Ctrl-C a second time -
    reproduced live, one SIGINT never exited. sse_starlette's own exit
    listener does not fire (ContextVar bug in 3.0.3), so this event is ours."""

    async def scenario(repo: Path) -> bool:
        shutdown = asyncio.Event()
        agen = _watch_evidence_mtime(repo, shutdown)
        task = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.1)
        assert not task.done()

        shutdown.set()
        # Well inside the 1.5s poll interval: the stream must react to the
        # event, not merely notice it on the next scheduled wake-up.
        try:
            await asyncio.wait_for(task, timeout=0.5)
            ended = False
        except StopAsyncIteration:
            ended = True
        await agen.aclose()
        return ended

    with tempfile.TemporaryDirectory() as tmp:
        assert asyncio.run(scenario(Path(tmp))) is True


def test_watch_evidence_mtime_swallows_cancellation_instead_of_raising():
    """A forced shutdown cancels this generator mid-flight; an un-caught
    CancelledError propagates through sse_starlette into Starlette's ASGI
    error handler, which is what printed two full tracebacks on Ctrl-C.

    StopAsyncIteration is the assertion that matters - it means the generator
    caught the cancellation and *returned* rather than re-raising."""

    async def scenario(repo: Path) -> BaseException:
        agen = _watch_evidence_mtime(repo)
        task = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
            raised: BaseException = RuntimeError("generator yielded instead of stopping")
        except BaseException as exc:  # noqa: BLE001 - the exception type IS the assertion
            raised = exc
        await agen.aclose()
        return raised

    with tempfile.TemporaryDirectory() as tmp:
        assert isinstance(asyncio.run(scenario(Path(tmp))), StopAsyncIteration)


def test_watch_evidence_mtime_skips_malformed_evidence_instead_of_dying(tmp_path):
    """This was the one evidence reader checking neither schema version nor
    shape before indexing evidence["scanned_at"]. It fires on mtime, so it can
    observe a scan mid-write - and a truncated file killed the stream, leaving
    the dashboard silently not auto-refreshing for the rest of the session."""
    aletheore_dir = tmp_path / ".aletheore"
    aletheore_dir.mkdir()
    evidence_path = aletheore_dir / "air.json"
    evidence_path.write_text("placeholder")

    good = minimal_air_evidence()
    good["scanned_at"] = "2026-08-11T00:00:00Z"

    async def scenario() -> str:
        agen = _watch_evidence_mtime(tmp_path)
        task = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.1)

        evidence_path.write_text('{"aletheore_version": "0.2.0", "trunc')
        await asyncio.sleep(2.0)
        assert not task.done(), "a truncated air.json ended the stream"

        # A valid write still gets through afterwards, so the stream is
        # skipping bad ticks rather than having gone permanently deaf.
        evidence_path.write_text(json.dumps(good))
        event = await asyncio.wait_for(task, timeout=6)
        await agen.aclose()
        return json.loads(event["data"])["scanned_at"]

    assert asyncio.run(scenario()) == "2026-08-11T00:00:00Z"


def test_build_app_exposes_the_shutdown_event_the_cli_signals():
    """cli._dashboard reaches for app.state.shutdown_event in its uvicorn
    handle_exit override, so renaming it would break Ctrl-C silently."""
    with tempfile.TemporaryDirectory() as tmp:
        assert isinstance(build_app(Path(tmp)).state.shutdown_event, asyncio.Event)


def test_browser_icon_requests_are_served_rather_than_404(tmp_path):
    """A page load requests all four unprompted; each 404'd, filling the log
    with errors for a dashboard that had served the page fine."""
    client = TestClient(build_app(tmp_path))

    for path in (
        "/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/logo.png",
    ):
        assert client.get(path).status_code == 200, path
