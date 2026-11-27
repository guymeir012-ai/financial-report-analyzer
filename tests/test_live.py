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
        "roe": 192880 / 228984,
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


def test_pages_render_live_nvda(client):
    from backend.app import create_app

    http = create_app(client).test_client()
    confirm = http.get("/confirm?ticker=NVDA")
    assert confirm.status_code == 200 and "NVIDIA" in confirm.get_data(as_text=True)
    resp = http.get("/summary/NVDA")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and html.count("<tr data-key=") == 9
    assert "תקופה נוכחית" in html and "https://www.sec.gov/" in html
    assert not any(word in html for word in ("קנייה", "מכירה", "המלצ", "Buy", "Sell"))
    assert http.get("/confirm?ticker=NVDX").status_code == 404


# ---- valuation: SEC facts against Finnhub and Tiingo (needs FINNHUB_API_KEY and TIINGO_API_KEY) ----

@pytest.fixture(scope="module")
def providers():
    from backend.config import get_finnhub_key, get_tiingo_key
    from backend.prices import FinnhubClient, TiingoClient

    if not (get_finnhub_key() and get_tiingo_key()):
        pytest.skip("FINNHUB_API_KEY / TIINGO_API_KEY are not configured")
    return FinnhubClient(get_finnhub_key()), TiingoClient(get_tiingo_key())


@pytest.fixture(scope="module")
def nvda_valuation(client, providers):
    from backend.ratios import load_company
    from backend.valuation import build_valuation

    loaded = load_company("NVDA", client)
    return loaded, build_valuation(loaded, *providers)


def test_eps_ttm_matches_finnhub_within_3_percent(nvda_valuation):
    _, val = nvda_valuation
    ours, theirs = val["current"]["eps"]["value"], val["current"]["cross_check"]["eps_ttm"]
    assert ours == pytest.approx(theirs, rel=0.03)
    pe_ours, pe_theirs = val["current"]["pe"]["value"], val["current"]["cross_check"]["pe_ttm"]
    assert pe_ours == pytest.approx(pe_theirs, rel=0.05)


@pytest.mark.parametrize("ticker", ["NVDA", "JPM", "RIVN"])
def test_annual_eps_matches_reported_eps_within_1_percent(client, ticker):
    from backend.filings import FilingFacts
    from backend.ratios import load_company
    from backend.valuation import eps_annual

    loaded = load_company(ticker, client)
    tenk = next(x for x in loaded["periodic"] if x.form == "10-K")
    ff = FilingFacts(loaded["facts_json"], tenk)
    ours, reported = eps_annual(ff), ff.reported_eps()
    assert ours["status"] == "ok" and reported is not None
    assert ours["value"] == pytest.approx(reported.value, rel=0.01)


def test_history_and_peers_live(nvda_valuation):
    _, val = nvda_valuation
    history = val["history"]
    assert history["status"] == "ok" and history["summary"]["count"] >= 12
    point = next(p for p in history["points"] if p["report_date"] == "2026-07-26")
    assert point["price"]["provider"] == "Tiingo" and point["price"]["as_of"] == "2026-07-24"
    assert point["price"]["value"] == pytest.approx(206.84)
    peers = val["peers"]
    assert peers["status"] == "ok" and peers["count"] >= 3 and len(peers["leaders"]) == 3
    assert "NVDA" not in [e["ticker"] for e in peers["peers"]]
    text = " ".join(val["explanation_he"])
    assert text.endswith("זולה לא אומר ראויה להשקעה.")
    assert not any(word in text for word in ("קנייה", "מכירה", "המלצ", "Buy", "Sell"))


def test_pages_render_live_with_valuation(client, providers):
    from backend.app import create_app

    http = create_app(client, finnhub=providers[0], tiingo=providers[1]).test_client()
    html = http.get("/summary/NVDA").get_data(as_text=True)
    assert 'data-status="ok"' in html and "שעון ישראל" in html and "בדיקה צולבת" in html
    assert "history-chart" in html and "data-peer=" in html
    bank = http.get("/summary/JPM").get_data(as_text=True)
    assert 'data-status="ok"' in bank and "לא רלוונטי לבנקים" in bank
    assert not any(word in html + bank for word in ("קנייה", "מכירה", "המלצ", "Buy", "Sell"))
    assert http.get("/api/valuation/NVDA").get_json()["current"]["pe"]["status"] == "ok"


def test_niv_method_clf_q4_2021(client):
    from backend.filings import find_company_filings
    from backend.niv_method import compare, filing_periods
    _, cik, _, periodic, _ = find_company_filings(client, "CLF")
    filing = next(x for x in periodic if x.form == "10-K" and x.report_date == "2021-12-31")
    periods = filing_periods(client.company_facts(cik), filing)
    quarter, annual = compare(*periods["quarter"]), compare(*periods["annual"])
    assert periods["quarter"][0].revenue == pytest.approx(2_256_000_000, abs=500_000)
    assert (round(quarter["revenue_growth_pct"]), round(annual["revenue_growth_pct"])) == (137, 282)
    assert (round(quarter["efficiency_change_points"]), round(annual["efficiency_change_points"])) == (16, 22)
    assert (annual["stage"]["from_stage"], annual["stage"]["to_stage"]) == (4, 6)
    assert (quarter["stage"]["from_stage"], quarter["stage"]["to_stage"]) == (6, 7)


@pytest.mark.parametrize("ticker", ["NVDA", "AAPL", "JPM"])
def test_niv_method_on_latest_filing_of_real_companies(client, ticker):
    """Shifted fiscal years, a 10-K or a 10-Q, and a bank: revenue and net profit are always found."""
    from backend.filings import filings_with_facts, find_company_filings
    from backend.niv_method import filing_periods, report_analysis
    _, cik, _, periodic, _ = find_company_filings(client, ticker)
    facts = client.company_facts(cik)
    filing = filings_with_facts(facts, periodic)[0]
    result = report_analysis(filing_periods(facts, filing))
    assert result["comparisons"]
    for c in result["comparisons"].values():
        assert all(v is not None for v in c["values"]["revenue"])
        assert all(v is not None for v in c["values"]["net_income"])
        assert c["stage"]["to_stage"] is not None


def test_niv_method_nvda_q1_fy2024(client):
    """Lesson 14's report: every figure Niv computed in class, from EDGAR itself."""
    from backend.filings import find_company_filings
    from backend.niv_method import filing_periods, guidance_growth_pct, report_analysis
    _, cik, _, periodic, _ = find_company_filings(client, "NVDA")
    filing = next(x for x in periodic if x.form == "10-Q" and x.report_date == "2023-04-30")
    c = report_analysis(filing_periods(client.company_facts(cik), filing))["comparisons"]["quarter"]
    assert c["values"]["revenue"] == (8_288_000_000, 7_192_000_000)
    assert round(c["revenue_growth_pct"], 1) == -13.2
    assert c["previous_quarter"]["revenue"] == 6_051_000_000
    assert round(c["previous_quarter"]["revenue_growth_pct"], 1) == 18.9
    assert (round(c["efficiency_prior"] * 100), round(c["efficiency_current"] * 100)) == (77, 70)
    assert c["values"]["net_income"] == (1_618_000_000, 2_043_000_000)
    assert round(guidance_growth_pct(11_000_000_000, c["values"]["revenue"][1])) == 53
