"""Live checks against SEC EDGAR. Run with: pytest -m live"""
import pytest

from backend.config import ConfigError, get_sec_user_agent
from backend.filings import list_periodic_filings
from backend.ratios import analyze, compute_ratios
from backend.sec_client import SecClient, resolve_ticker
from tests.conftest import LATEST_ACCN, PREVIOUS_ACCN

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    try:
        return SecClient(get_sec_user_agent())
    except ConfigError:
        pytest.skip("SEC_EDGAR_USER_AGENT is not configured")


def test_resolve_nvda(client):
    assert resolve_ticker(client, "NVDA")[0] == 1045810


def test_pinned_filing_ratios(client):
    filings = list_periodic_filings(client.submissions(1045810), 1045810)
    by_accn = {x.accession: x for x in filings}
    latest, previous = by_accn[LATEST_ACCN], by_accn[PREVIOUS_ACCN]
    ratios = {r["key"]: r for r in compute_ratios(client.company_facts(1045810), latest, previous)}
    expected = {
        "revenue_growth_yoy": 96221 / 46743 - 1,
        "revenue_growth_qoq": 96221 / 81615 - 1,
        "gross_margin": 72142 / 96221,
        "operating_margin": 63734 / 96221,
        "net_margin": 59688 / 96221,
        "roe": 59688 / 228984,
        "current_ratio": 197412 / 43019,
        "debt_to_equity": 33366 / 228984,
        "equity_ratio": 228984 / 320272,
    }
    for key, value in expected.items():
        assert ratios[key]["value"] == pytest.approx(value, rel=1e-6), key


def test_analyze_nvda_latest_is_current(client):
    result = analyze("NVDA", client)
    filing = result["latest"]["filing"]
    assert filing["form"] in ("10-Q", "10-K")
    assert filing["report_date"] >= "2026-07-26"
