import json
import shutil
import subprocess

import pytest

from app_server import frontend

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available in this environment")


def _run_js(snippet: str):
    # The pure credit helpers are plain JavaScript defined once and shared by
    # every page that shows a balance, so they are exercised here in Node
    # exactly as the browser would run them.
    program = frontend.CREDIT_SUMMARY_JS + "\nconsole.log(JSON.stringify(" + snippet + "));"
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_the_headline_balance_includes_purchased_credit():
    summary = _run_js(
        "creditSummary({base_credit_remaining_usd: 14.2568, topup_credit_balance_usd: 5, base_credit_allotment_usd: 18})"
    )

    assert summary["total"] == pytest.approx(19.2568)
    assert summary["parts"] == ["$14.26 of $18.00 included this month", "$5.00 purchased, never expires"]


@needs_node
def test_the_included_credit_meter_ignores_purchased_credit():
    summary = _run_js(
        "creditSummary({base_credit_remaining_usd: 9, topup_credit_balance_usd: 100, base_credit_allotment_usd: 18})"
    )

    assert summary["pct"] == 50


@needs_node
def test_a_balance_with_no_purchased_credit_shows_only_the_included_part():
    summary = _run_js(
        "creditSummary({base_credit_remaining_usd: 4, topup_credit_balance_usd: 0, base_credit_allotment_usd: 5})"
    )

    assert summary["total"] == 4
    assert summary["parts"] == ["$4.00 of $5.00 included this month"]


@needs_node
def test_missing_balance_fields_read_as_zero_not_nan():
    summary = _run_js("creditSummary({})")

    assert summary["total"] == 0 and summary["pct"] == 0 and summary["parts"] == []


@needs_node
@pytest.mark.parametrize(
    "before, current, expected",
    [(0, 5, 5), (3.5, 8.5, 5), (5, 5, 0), (5, 4.99, 0), (0, 0.001, 0)],
)
def test_topup_arrival_reports_only_a_real_increase(before, current, expected):
    assert _run_js(f"topupArrival({before}, {current})") == pytest.approx(expected)


def test_the_credits_page_confirms_a_purchase_after_checkout_returns():
    html = frontend._credits_page(123)

    assert 'id="purchase-banner"' in html
    # Checkout returns here with ?purchased=1; without it the page reloaded
    # silently and the buyer could not tell whether the payment worked.
    assert "purchased=1" in html
    assert "topupArrival(" in html


def test_the_credits_page_headline_uses_the_shared_summary():
    html = frontend._credits_page(123)

    assert "creditSummary(" in html
    assert "available" in html


def test_the_air_usage_page_headline_uses_the_same_shared_summary():
    assert "creditSummary(" in frontend._usage_html()


def test_a_purchase_confirmation_survives_a_failed_balance_load_and_a_missing_banner():
    # A failed first balance fetch must not also swallow the "payment received"
    # message, and a page without the banner element must not throw.
    html = frontend._credits_page(123)

    assert "try {\n      await loadCredits();\n    } finally {\n      await confirmPurchaseIfReturning();\n    }" in html
    assert "if (!banner) return;" in html
