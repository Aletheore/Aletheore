import os
from unittest.mock import MagicMock

import pytest

from app_server.audit_signing import (
    LLM_SUGGESTION_HEADING,
    contains_non_evidence_backed_section,
)
from app_server.db import get_installation, set_llm_suggestions_enabled, upsert_installation
from scan_worker.db import get_installation as get_installation_row
from test_admin import _logged_in_client

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)

EVIDENCE_BACKED_REPORT = "# Audit\n\n- finding, see `app.py:12`\n"


# --- The setting -------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestions_are_on_by_default(pool):
    # This adds a switch; it must not silently withdraw a section paying
    # installations already receive.
    await upsert_installation(pool, 900, "acme")

    assert (await get_installation(pool, 900))["llm_suggestions_enabled"] is True


@pytest.mark.asyncio
async def test_the_setting_round_trips(pool):
    await upsert_installation(pool, 901, "acme")

    await set_llm_suggestions_enabled(pool, 901, False)
    assert (await get_installation(pool, 901))["llm_suggestions_enabled"] is False

    await set_llm_suggestions_enabled(pool, 901, True)
    assert (await get_installation(pool, 901))["llm_suggestions_enabled"] is True


@pytest.mark.asyncio
async def test_the_worker_reads_the_same_setting(pool):
    # app_server writes it with asyncpg, the worker reads it with psycopg -
    # two different drivers against one column, so this pins that they agree.
    # The worker reads it off the installation row it already fetches rather
    # than issuing a second query.
    await upsert_installation(pool, 902, "acme")
    await set_llm_suggestions_enabled(pool, 902, False)

    row = get_installation_row(TEST_DATABASE_URL, 902)
    assert row["llm_suggestions_enabled"] is False


def test_the_worker_sees_nothing_for_an_unknown_installation():
    # jobs.py treats a missing row as "enabled", matching the column default -
    # a lookup miss must not quietly change what a report contains.
    assert get_installation_row(TEST_DATABASE_URL, 999_999) is None


# --- The audit honours it ----------------------------------------------------


def _run_audit(monkeypatch, tmp_path, *, include: bool):
    import scan_worker.managed_audit as managed_audit

    report_path = tmp_path / "report.md"
    report_path.write_text(EVIDENCE_BACKED_REPORT)

    monkeypatch.setattr(managed_audit, "run_reasoning_phase", lambda *a, **k: str(report_path))
    monkeypatch.setattr(managed_audit, "_citation_verification_section", lambda *a, **k: "")
    monkeypatch.setattr(
        managed_audit, "writing_adapter_for_plan", lambda *a, **k: MagicMock()
    )
    calls = []

    def fake_section(report_text, plan, on_usage=None):
        calls.append(report_text)
        return f"\n\n---\n\n{LLM_SUGGESTION_HEADING}\n\n**Overall rating: 7/10**\n"

    monkeypatch.setattr(managed_audit, "_llm_based_suggestion_section", fake_section)

    text = managed_audit.run_managed_audit(
        tmp_path, plan="air", include_llm_suggestions=include
    )
    return text, calls


def test_opting_out_produces_a_fully_evidence_backed_report(monkeypatch, tmp_path):
    text, _ = _run_audit(monkeypatch, tmp_path, include=False)

    assert LLM_SUGGESTION_HEADING not in text
    assert contains_non_evidence_backed_section(text) is False
    assert "finding, see" in text  # the real audit survives


def test_opting_out_does_not_spend_on_the_model_call(monkeypatch, tmp_path):
    # Generating-then-discarding would bill the customer's monthly LLM cap for
    # a section they turned off.
    _text, calls = _run_audit(monkeypatch, tmp_path, include=False)

    assert calls == []


def test_leaving_it_on_still_appends_the_section(monkeypatch, tmp_path):
    text, calls = _run_audit(monkeypatch, tmp_path, include=True)

    assert LLM_SUGGESTION_HEADING in text
    assert len(calls) == 1


def test_the_heading_constant_matches_what_the_writer_emits(monkeypatch, tmp_path):
    """The verifier detects the section by this exact string. If the writer's
    heading and the shared constant ever drift, the certificate would silently
    start reporting opinion-bearing reports as fully evidence-backed.
    """
    import scan_worker.managed_audit as managed_audit

    monkeypatch.setattr(
        managed_audit, "writing_adapter_for_plan", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(
        managed_audit.json,
        "loads",
        lambda _raw: {"rating": 7, "rating_justification": "ok", "suggestions": ["do a thing"]},
    )

    section = managed_audit._llm_based_suggestion_section("report", "air")

    assert contains_non_evidence_backed_section(section)


# --- The certificate discloses it -------------------------------------------


def test_detector_is_false_for_a_purely_evidence_backed_report():
    assert contains_non_evidence_backed_section(EVIDENCE_BACKED_REPORT) is False


@pytest.mark.asyncio
async def test_verify_reports_a_clean_audit_as_fully_evidence_backed(pool, monkeypatch):
    body = await _verify_body(pool, monkeypatch, EVIDENCE_BACKED_REPORT, token="tok-clean")

    assert body["fully_evidence_backed"] is True
    assert body["non_evidence_backed_sections"] == []


@pytest.mark.asyncio
async def test_verify_discloses_the_opinion_section_when_present(pool, monkeypatch):
    # The point of the whole change: a signature proves Aletheore wrote this
    # text, not that every claim in it is cited. The certificate must not let
    # a reader conflate the two.
    report = EVIDENCE_BACKED_REPORT + f"\n\n---\n\n{LLM_SUGGESTION_HEADING}\n\n**Overall rating: 7/10**\n"
    body = await _verify_body(pool, monkeypatch, report, token="tok-opinion")

    assert body["verified"] is True
    assert body["fully_evidence_backed"] is False
    assert body["non_evidence_backed_sections"] == ["LLM Based Suggestion (Not Evidence Backed)"]


async def _verify_body(pool, monkeypatch, report_text: str, token: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from app_server.audit_signing import content_hash, sign_report
    from app_server.main import app

    private_key = "11" * 32
    monkeypatch.setenv("AUDIT_SIGNING_PRIVATE_KEY", private_key)
    app.state.db_pool = pool
    await upsert_installation(pool, 910, "acme")
    await pool.execute(
        """
        INSERT INTO audit_reports
            (installation_id, repo_full_name, report_text, content_hash, signature, verification_token)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        910,
        "acme/api",
        report_text,
        content_hash(report_text),
        sign_report(report_text, private_key),
        token,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/v1/audit/{token}/verify")
    assert response.status_code == 200, response.text
    return response.json()


# --- The admin route ---------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_route_toggles_the_setting(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/llm-suggestions", json={"enabled": False}
        )

    assert response.status_code == 200, response.text
    assert (await get_installation(pool, 100))["llm_suggestions_enabled"] is False


@pytest.mark.asyncio
async def test_admin_route_rejects_a_non_administrator(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    await upsert_installation(pool, 101, "globex")
    async with client:
        response = await client.put(
            "/admin/globex/web/llm-suggestions", json={"enabled": False}
        )

    assert response.status_code in (403, 404)
    assert (await get_installation(pool, 101))["llm_suggestions_enabled"] is True
