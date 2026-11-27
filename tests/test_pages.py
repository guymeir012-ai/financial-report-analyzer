"""End-to-end tests of the HTML pages through Flask's test client (NVDA fixtures, no network)."""
import copy
import json
import re
from urllib.parse import quote

import pytest

from backend import sec_client
from backend.app import create_app
from backend.sec_client import SecClient
from tests.conftest import (
    NVDA_CIK, TEST_UA, TICKERS, FakeResponse, FakeSession, LATEST_ACCN,
)

XSS = "<script>alert(1)</script>"
FORBIDDEN_WORDS = ("קנייה", "מכירה", "המלצ", "Buy", "Sell")
LEAKS = ("Traceback", "File \"", "KeyError", "RuntimeError", ".py\"")
SEC_LINK = re.compile(r'<a [^>]*href="(https?://[^"]+)"')


def make_client(submissions, facts, **kwargs):
    session = FakeSession({
        sec_client.TICKERS_URL: FakeResponse(200, TICKERS),
        sec_client.SUBMISSIONS_URL.format(cik=NVDA_CIK): FakeResponse(200, submissions),
        sec_client.COMPANY_FACTS_URL.format(cik=NVDA_CIK): FakeResponse(200, facts),
    })
    return SecClient(TEST_UA, session=session, min_interval=0, **kwargs)


def page(client, path):
    return create_app(client).test_client().get(path)


@pytest.fixture
def http(nvda_client):
    return create_app(nvda_client).test_client()


def text_of(resp) -> str:
    return resp.get_data(as_text=True)


def row(html: str, key: str) -> str:
    return re.search(rf'<tr data-key="{key}">.*?</tr>', html, re.S).group(0)


def assert_clean(resp):
    html = text_of(resp)
    assert not any(w in html for w in FORBIDDEN_WORDS)
    assert not any(bad in html for bad in LEAKS)
    assert "<script" not in html


# ---------- home ----------

def test_home(http):
    resp = http.get("/")
    html = text_of(resp)
    assert resp.status_code == 200
    assert '<html dir="rtl" lang="he">' in html
    assert 'action="/confirm"' in html and 'method="get"' in html and 'name="ticker"' in html
    for ticker in ("NVDA", "AAPL", "MSFT"):
        assert f"/confirm?ticker={ticker}" in html
    assert_clean(resp)


def test_static_css_served_and_no_tailwind(http):
    resp = http.get("/static/css/app.css")
    assert resp.status_code == 200 and resp.mimetype == "text/css"
    assert "cdn.tailwindcss" not in text_of(http.get("/"))


@pytest.mark.parametrize("path", ["/confirm?ticker=", "/confirm?ticker=%20%20", "/confirm"])
def test_empty_ticker_returns_home_with_message(http, path):
    resp = http.get(path)
    assert resp.status_code == 400
    assert "יש להזין סימול" in text_of(resp) and 'name="ticker"' in text_of(resp)


@pytest.mark.parametrize("path", [
    "/confirm?ticker=" + quote(XSS), "/summary/" + quote("<script>alert(1)", safe=""), "/summary/NV%20DA", "/confirm?ticker=" + "A" * 11,
    "/confirm?ticker=../etc", "/summary/%20",
])
def test_invalid_ticker_400_and_never_reflected(http, path):
    resp = http.get(path)
    html = text_of(resp)
    assert resp.status_code == 400
    assert "סימול המניה אינו תקין" in html and 'name="ticker"' in html
    assert XSS not in html and "alert(1)" not in html and "&lt;script" not in html
    assert_clean(resp)


def test_xss_in_unknown_path_not_reflected(http):
    resp = http.get("/" + XSS)
    assert resp.status_code == 404 and "alert(1)" not in text_of(resp)


def test_unknown_ticker_404_with_suggestions(http):
    resp = http.get("/confirm?ticker=nvdx")
    html = text_of(resp)
    assert resp.status_code == 404
    assert "לא נמצאה חברה" in html and "האם התכוונת ל:" in html
    assert 'href="/confirm?ticker=NVDA"' in html and "NVIDIA CORP" in html
    assert 'value="NVDX"' in html
    assert_clean(resp)


def test_unknown_ticker_on_summary_route(http):
    resp = http.get("/summary/NVDX")
    assert resp.status_code == 404 and "האם התכוונת ל:" in text_of(resp)


# ---------- confirm ----------

def test_confirm_shows_company_and_latest_filing(http):
    resp = http.get("/confirm?ticker=nvda")
    html = text_of(resp)
    assert resp.status_code == 200
    for expected in ("NVIDIA CORP", "NVDA", "0001045810", "10-Q", "26.07.2026", "26.08.2026"):
        assert expected in html
    assert 'href="/summary/NVDA"' in html and "אישור, נתח" in html
    assert 'href="/"' in html and "לא החברה הנכונה" in html
    assert 'rel="noopener noreferrer"' in html
    assert_clean(resp)


def test_confirm_company_without_periodic_filings(fake_time):
    session = FakeSession({
        sec_client.TICKERS_URL: FakeResponse(200, TICKERS),
        sec_client.SUBMISSIONS_URL.format(cik=999999): FakeResponse(200, {"filings": {"recent": {
            "form": ["8-K"], "accessionNumber": ["x-1"], "filingDate": ["2026-01-01"],
            "reportDate": [""], "primaryDocument": ["a.htm"]}}}),
    })
    resp = page(SecClient(TEST_UA, session=session), "/confirm?ticker=EMPT")
    html = text_of(resp)
    assert resp.status_code == 404 and "לא נמצאו דוחות תקופתיים" in html and "EMPTY FILER INC" in html
    assert_clean(resp)


# ---------- summary ----------

def test_summary_values_changes_and_headers(http):
    resp = http.get("/summary/nvda")
    html = text_of(resp)
    assert resp.status_code == 200
    assert "NVIDIA CORP" in html and "תקופה נוכחית" in html and "תקופה קודמת" in html
    assert "26.07.2026" in html and "26.04.2026" in html
    assert html.count('<tr data-key=') == 9

    margin = row(html, "gross_margin")
    assert ">75.0%<" in margin and ">74.9%<" in margin and "0.0 pp" in margin
    assert "change neutral" in margin and "▲" not in margin and "▼" not in margin

    net = row(html, "net_margin")
    assert ">62.0%<" in net and "-9.4 pp" in net and "change bad" in net and "▼" in net

    assert "+1.15x" in row(html, "current_ratio") and "change good" in row(html, "current_ratio")
    # debt to equity went up: shown as +, colored as unfavorable
    de = row(html, "debt_to_equity")
    assert ">0.15x<" in de and "+0.10x" in de and "change bad" in de and "▲" in de
    assert "+0.6 pp" in row(html, "operating_margin") and "change good" in row(html, "operating_margin")
    assert "12 חודשים אחרונים" in row(html, "roe")
    assert_clean(resp)


def test_summary_previous_column_status_not_a_guess(http):
    qoq = row(text_of(http.get("/summary/NVDA")), "revenue_growth_qoq")
    assert "17.9%" in qoq and "לא ניתן לחישוב" in qoq
    assert 'change none' in qoq and "—" in qoq


def test_summary_source_panel(http):
    html = text_of(http.get("/summary/NVDA"))
    roe = row(html, "roe")
    assert "<details" in roe and "<summary" in roe
    for expected in ("NetIncomeLoss", "StockholdersEquity", "120,067", "118,010", "45,197", "228,984",
                     "רווח נקי שנתי", "הון עצמי", "0001045810-26-000075", "מיליוני דולר",
                     "27.07.2025", "26.07.2026", "הוגש"):
        assert expected in roe
    assert 'href="https://www.sec.gov/Archives/edgar/data/1045810/' in roe
    assert 'rel="noopener noreferrer"' in roe


def test_summary_links_only_to_sec(http):
    html = text_of(http.get("/summary/NVDA"))
    links = SEC_LINK.findall(html)
    external = [u for u in links if u.startswith("http")]
    assert external and all(u.startswith("https://www.sec.gov/") for u in external)


def test_summary_no_javascript_at_all(http):
    for path in ("/", "/confirm?ticker=NVDA", "/summary/NVDA"):
        html = text_of(http.get(path)).lower()
        assert "<script" not in html and "onclick" not in html and "javascript:" not in html


def test_summary_missing_value_shows_not_reported(nvda_submissions, nvda_facts):
    facts = copy.deepcopy(nvda_facts)
    for tag in ("GrossProfit", "CostOfRevenue"):
        facts["facts"]["us-gaap"].pop(tag)
    resp = page(make_client(nvda_submissions, facts), "/summary/NVDA")
    html = text_of(resp)
    gross = row(html, "gross_margin")
    assert resp.status_code == 200 and gross.count("לא דווח") >= 2
    assert "change none" in gross and "—" in gross
    assert "חסר בדוח" in gross and "רווח גולמי" in gross
    assert re.search(r"\d+\.\d%", gross) is None
    assert_clean(resp)


def test_summary_bank_marks_not_applicable(nvda_submissions, nvda_facts):
    subs = {**nvda_submissions, "sic": "6022"}
    resp = page(make_client(subs, nvda_facts), "/summary/NVDA")
    html = text_of(resp)
    for key in ("gross_margin", "operating_margin", "current_ratio", "debt_to_equity"):
        assert "לא רלוונטי לבנקים" in row(html, key) and "change none" in row(html, key)
    assert ">71.5%<" in row(html, "equity_ratio")


def test_summary_single_filing_has_no_previous(nvda_submissions, nvda_facts):
    recent = nvda_submissions["filings"]["recent"]
    keep = [i for i, accn in enumerate(recent["accessionNumber"])
            if recent["form"][i] not in ("10-Q", "10-K") or accn == LATEST_ACCN]
    subs = copy.deepcopy(nvda_submissions)
    subs["filings"]["recent"] = {k: [v[i] for i in keep] for k, v in recent.items()}
    resp = page(make_client(subs, nvda_facts), "/summary/NVDA")
    html = text_of(resp)
    assert resp.status_code == 200
    assert html.count("אין דוח קודם") >= 9
    assert "105.9%" in row(html, "revenue_growth_yoy")
    assert "אין דוח קודם להשוואה" in row(html, "revenue_growth_qoq")
    assert all("change none" in row(html, k) for k in re.findall(r'data-key="(\w+)"', html))


def test_summary_notice_when_newer_filing_not_in_xbrl(nvda_submissions, nvda_facts):
    subs = copy.deepcopy(nvda_submissions)
    recent = subs["filings"]["recent"]
    for key, value in (("form", "10-Q"), ("accessionNumber", "0001045810-26-000099"),
                       ("filingDate", "2026-12-01"), ("reportDate", "2026-10-25"),
                       ("primaryDocument", "nvda-20261025.htm")):
        recent[key].insert(0, value)
    html = text_of(page(make_client(subs, nvda_facts), "/summary/NVDA"))
    assert "קיים דוח חדש יותר" in html and "alert-notice" in html


def test_summary_escapes_company_name(nvda_submissions, nvda_facts, fake_time):
    tickers = {"0": {"cik_str": NVDA_CIK, "ticker": "NVDA", "title": XSS}}
    session = FakeSession({
        sec_client.TICKERS_URL: FakeResponse(200, tickers),
        sec_client.SUBMISSIONS_URL.format(cik=NVDA_CIK): FakeResponse(200, nvda_submissions),
        sec_client.COMPANY_FACTS_URL.format(cik=NVDA_CIK): FakeResponse(200, nvda_facts),
    })
    for path in ("/summary/NVDA", "/confirm?ticker=NVDA"):
        html = text_of(page(SecClient(TEST_UA, session=session, min_interval=0), path))
        assert XSS not in html and "&lt;script&gt;" in html


def test_summary_rejects_non_sec_links(nvda_submissions, nvda_facts):
    subs = copy.deepcopy(nvda_submissions)
    # a hostile primary document name cannot turn the filing link into an off-site link
    for i, form in enumerate(subs["filings"]["recent"]["form"]):
        if form == "10-Q":
            subs["filings"]["recent"]["primaryDocument"][i] = "x.htm\" onclick=\"alert(1)"
    html = text_of(page(make_client(subs, nvda_facts), "/summary/NVDA"))
    assert 'onclick="alert' not in html


# ---------- errors ----------

def test_sec_unavailable_502(fake_time):
    session = FakeSession({sec_client.TICKERS_URL: FakeResponse(503)})
    client = SecClient(TEST_UA, session=session, max_retries=1)
    for path in ("/confirm?ticker=NVDA", "/summary/NVDA"):
        resp = page(client, path)
        assert resp.status_code == 502 and "לא ניתן לקבל נתונים מ-SEC" in text_of(resp)
        assert "לא זמין כרגע" in text_of(resp)
        assert_clean(resp)


def test_unexpected_error_500_no_leak(nvda_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("backend.app.analyze", boom)
    resp = create_app(nvda_client).test_client().get("/summary/NVDA")
    html = text_of(resp)
    assert resp.status_code == 500 and "אירעה שגיאה בשרת" in html
    assert "secret internal detail" not in html and "RuntimeError" not in html
    assert_clean(resp)


def test_missing_config_500_page(monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    resp = create_app().test_client().get("/summary/NVDA")
    assert resp.status_code == 500 and "SEC_EDGAR" not in text_of(resp)
    assert create_app().test_client().get("/").status_code == 200


def test_wrong_method_is_html_405(http):
    resp = http.post("/")
    assert resp.status_code == 405 and resp.mimetype == "text/html"


def test_api_routes_still_json(http):
    assert http.get("/api/health").get_json() == {"status": "ok"}
    assert http.get("/api/ratios/NVDX").mimetype == "application/json"


# ---------- headers ----------

@pytest.mark.parametrize("path", [
    "/", "/confirm?ticker=NVDA", "/summary/NVDA", "/confirm?ticker=", "/nope", "/api/health",
    "/api/nope", "/static/css/app.css",
])
def test_security_headers_everywhere(http, path):
    headers = http.get(path).headers
    csp = headers["Content-Security-Policy"]
    assert csp == (
        "default-src 'self'; style-src 'self' fonts.googleapis.com; font-src fonts.gstatic.com; "
        "script-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"


def test_no_inline_styles_that_the_csp_would_block(http):
    for path in ("/", "/confirm?ticker=NVDA", "/summary/NVDA", "/confirm?ticker=NVDX"):
        html = text_of(http.get(path))
        assert " style=" not in html and "<style" not in html


def test_all_pages_have_no_recommendation_words(http):
    for path in ("/", "/confirm?ticker=NVDA", "/summary/NVDA", "/confirm?ticker=NVDX", "/nope"):
        html = text_of(http.get(path))
        assert not any(w in html for w in FORBIDDEN_WORDS), path


# ---------- valuation ----------

from backend.prices import PriceError  # noqa: E402
from tests.conftest import CLOSES, PEER_METRICS, QUOTE, StubFinnhub, StubTiingo  # noqa: E402


def valuation_app(nvda_client, finnhub=None, tiingo=None):
    fh = finnhub if finnhub is not None else StubFinnhub(quote=QUOTE, peers=["AVGO", "AMD", "INTC", "MU", "TXN"], metrics=PEER_METRICS)
    ti = tiingo if tiingo is not None else StubTiingo(CLOSES)
    return create_app(nvda_client, finnhub=fh, tiingo=ti).test_client()


def section(html: str) -> str:
    return re.search(r'<section class="panel valuation".*?</section>', html, re.S).group(0)


def test_header_shows_price_in_israel_time_and_pe(nvda_client):
    html = text_of(valuation_app(nvda_client).get("/summary/NVDA"))
    head = re.search(r'<section class="panel summary-head">.*?</section>', html, re.S).group(0)
    assert "$203.48" in head and "04.11.2026 19:56" in head and "שעון ישראל" in head and "Finnhub" in head
    assert "מכפיל רווח (P/E TTM)" in head and ">25.6x<" in head


def test_valuation_section_values(nvda_client):
    resp = valuation_app(nvda_client).get("/summary/NVDA")
    html = text_of(resp)
    assert resp.status_code == 200 and html.count('<tr data-key=') == 9
    val = section(html)
    assert 'data-status="ok"' in val
    assert ">25.6x<" in val and ">$7.94<" in val                       # computed here, not Finnhub's 25.8
    assert "חציון החברה" in val and "3 רבעונים" in val
    assert "חציון המתחרות" in val and "4 חברות" in val and ">35.8x<" in val
    assert '<svg class="bar-track"' in val and 'width="100.0"' in val
    assert '<svg class="history-chart"' in val and 'class="median-line"' in val and val.count('class="col"') == 3
    assert 'data-peer="AVGO"' in val and 'data-peer="INTC"' in val and "הפסד, אין מכפיל" in val
    assert ">מובילה<" in val and val.count(">מובילה<") == 3
    assert "פער <bdi dir=\"ltr\" class=\"num\">-38%</bdi>" in val      # company vs AVGO, sign only
    assert "פרמיה" in val and "הנחה" in val and "זולה לא אומר ראויה להשקעה" in val
    assert "change good" not in val and "change bad" not in val      # premium/discount carry no color
    assert "SIC" in val and ">3674<" in val
    assert_clean(resp)


def test_valuation_source_panels(nvda_client):
    val = section(text_of(valuation_app(nvda_client).get("/summary/NVDA")))
    pe = re.search(r'data-kpi="pe">.*?</dd>', val, re.S).group(0)
    assert "<details" in pe and "Finnhub" in pe and "quote" in pe and "04.11.2026 19:56" in pe and "שעון ישראל" in pe
    assert "בדיקה צולבת: Finnhub מחשב מכפיל 25.8x" in pe and "price / EPS TTM" in pe
    eps = re.search(r'data-kpi="eps">.*?</dd>', val, re.S).group(0)
    for expected in ("WeightedAverageNumberOfDilutedSharesOutstanding", "מספר מניות בדילול", "24,285", "מיליוני מניות",
                     "192,880" if False else "120,067", "118,010", "45,197", "ספקים אחרים", "0001045810-26-000075"):
        assert expected in eps, expected
    assert 'href="https://www.sec.gov/Archives/edgar/data/1045810/' in eps


def test_valuation_history_points_table(nvda_client):
    val = section(text_of(valuation_app(nvda_client).get("/summary/NVDA")))
    points = re.search(r'<table class="ratio-table points-table">.*?</table>', val, re.S).group(0)
    assert points.count("<tr>") == 4 and "26.07.2026" in points and "25.01.2026" in points
    assert "206.84" in points and "24.07.2026" in points and "Tiingo" in points


def test_valuation_unavailable_without_keys_keeps_ratios(nvda_client):
    resp = create_app(nvda_client).test_client().get("/summary/NVDA")    # no keys in the test environment
    html = text_of(resp)
    assert resp.status_code == 200 and html.count('<tr data-key=') == 9
    val = section(html)
    assert 'data-status="unavailable"' in val and "לא זמין כרגע" in val
    assert ">$7.94<" in val                                           # EPS needs only SEC data
    assert "25.6x" not in html and "history-chart" not in val
    assert_clean(resp)


def test_valuation_provider_failure_keeps_page(nvda_client, caplog):
    fh = StubFinnhub(quote_error=PriceError(status=429), peers_error=PriceError(status=503))
    resp = valuation_app(nvda_client, finnhub=fh, tiingo=StubTiingo([], error=PriceError(status=404))).get("/summary/NVDA")
    html = text_of(resp)
    assert resp.status_code == 200 and html.count('<tr data-key=') == 9
    val = section(html)
    assert 'data-status="unavailable"' in val and val.count("לא זמין כרגע") >= 3
    assert_clean(resp)


def test_valuation_crash_never_breaks_the_page(nvda_client, caplog):
    class Broken:
        def quote(self, symbol):
            raise RuntimeError("boom")

    resp = valuation_app(nvda_client, finnhub=Broken()).get("/summary/NVDA")
    html = text_of(resp)
    assert resp.status_code == 200 and html.count('<tr data-key=') == 9
    assert 'data-status="unavailable"' in section(html) and "boom" not in html
    assert "Valuation failed" in caplog.text
    assert_clean(resp)


def test_valuation_loss_shows_no_multiple(nvda_submissions, nvda_facts, fake_time):
    facts = copy.deepcopy(nvda_facts)
    for fact in facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]:
        fact["val"] = -abs(fact["val"])
    client = make_client(nvda_submissions, facts)
    html = text_of(valuation_app(client).get("/summary/NVDA"))
    val = section(html)
    assert "הפסד ב-12 החודשים האחרונים" in val and "לחברה הפסד" in val
    assert re.search(r">\d+\.\dx<", re.search(r'data-kpi="pe">.*?</dd>', val, re.S).group(0)) is None
    assert "history-chart" not in val                                  # no positive P/E point to draw


def test_valuation_peer_names_are_escaped(nvda_client):
    fh = StubFinnhub(quote=QUOTE, peers=["<b>X</b>", "AVGO"], metrics=PEER_METRICS)
    html = text_of(valuation_app(nvda_client, finnhub=fh).get("/summary/NVDA"))
    assert "<b>X</b>" not in html and "&lt;b&gt;X&lt;/b&gt;" in html


def test_valuation_no_inline_styles_scripts_or_foreign_links(nvda_client):
    html = text_of(valuation_app(nvda_client).get("/summary/NVDA"))
    assert " style=" not in html and "<style" not in html and "<script" not in html.lower()
    external = [u for u in SEC_LINK.findall(html) if u.startswith("http")]
    assert external and all(u.startswith("https://www.sec.gov/") for u in external)


def test_api_valuation_json(nvda_client):
    http = valuation_app(nvda_client)
    resp = http.get("/api/valuation/nvda")
    body = resp.get_json()
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    assert body["ticker"] == "NVDA" and body["current"]["pe"]["status"] == "ok"
    assert body["peers"]["leaders"] == ["AVGO", "AMD", "MU"] and len(body["history"]["points"]) == 3
    assert "\\u05" not in resp.get_data(as_text=True)
    assert http.get("/api/valuation/NVDX").status_code == 404
    assert http.get("/api/valuation/bad%20one").status_code == 400
    headers = http.get("/api/valuation/NVDA").headers
    assert headers["X-Content-Type-Options"] == "nosniff" and "script-src 'none'" in headers["Content-Security-Policy"]


def test_api_valuation_without_keys_is_unavailable_not_an_error(nvda_client):
    body = create_app(nvda_client).test_client().get("/api/valuation/NVDA").get_json()
    assert body["current"]["status"] == "unavailable" and body["history"]["status"] == "unavailable"


# ---------- report reading: growth, efficiency, profit, 7-stage model ----------

from pathlib import Path  # noqa: E402

CLF_10K = "0000764065-22-000037"


def reading(html: str) -> str:
    return re.search(r'<section class="panel reading".*?</section>', html, re.S).group(0)


def comparison(html: str, basis: str) -> str:
    return re.search(rf'<div class="comparison" data-basis="{basis}">.*?</table>', reading(html), re.S).group(0)


def line(block: str, key: str) -> str:
    return re.search(rf'<tr data-line="{key}">.*?</tr>', block, re.S).group(0)


def clf_client():
    """CLF's 2021 annual report, served through the fake SEC session."""
    facts = json.loads((Path(__file__).parent / "fixtures" / "clf_companyfacts_min.json").read_text())
    subs = {"name": "CLEVELAND-CLIFFS INC.", "sic": "1000", "filings": {"recent": {
        "accessionNumber": [CLF_10K], "filingDate": ["2022-02-11"], "reportDate": ["2021-12-31"],
        "form": ["10-K"], "primaryDocument": ["clf-20211231.htm"]}}}
    return make_client(subs, facts)


def test_reading_section_on_a_quarterly_report(http):
    resp = http.get("/summary/NVDA")
    html = text_of(resp)
    block = reading(html)
    assert 'data-status="ok"' in block and "צמיחה, התייעלות ורווח" in block
    # a 10-Q has the quarterly comparison only
    assert block.count('<div class="comparison"') == 1 and 'data-basis="annual"' not in block
    q = comparison(html, "quarter")
    assert 'data-from="6" data-to="7"' in q
    assert re.search(r'משלב <bdi dir="ltr" class="num">6</bdi> לשלב <bdi dir="ltr" class="num">7</bdi>', q)
    revenue = line(q, "revenue")
    assert ">46,743<" in revenue and ">96,221<" in revenue and "+105.9%" in revenue and "change good" in revenue
    efficiency = line(q, "efficiency")
    assert ">39.2%<" in efficiency and ">33.8%<" in efficiency and "-5.4 pp" in efficiency and "change good" in efficiency and "▼" in efficiency
    net = line(q, "net_income")
    assert ">26,422<" in net and ">59,688<" in net and "%" not in net
    assert "מעבר מרווח נקי של 26.4 מיליארד דולר לרווח נקי של 59.7 מיליארד דולר." in block
    assert '<ul class="highlights">' not in block
    assert html.index('class="panel reading"') < html.index('class="panel ratios"')
    assert_clean(resp)


def test_reading_section_sources_link_to_sec(http):
    q = comparison(text_of(http.get("/summary/NVDA")), "quarter")
    revenue = line(q, "revenue")
    assert revenue.count('<details class="src"') == 2 and LATEST_ACCN in revenue
    assert "הדוח ב-SEC" in revenue
    assert "מחושב: הכנסות פחות רווח תפעולי." in line(q, "efficiency")


def test_reading_section_on_an_annual_report_shows_both_comparisons(fake_time):
    resp = page(clf_client(), "/summary/NVDA")
    html = text_of(resp)
    assert resp.status_code == 200
    block = reading(html)
    assert block.count('<div class="comparison"') == 2
    assert block.index('class="comparison" data-basis="quarter"') < block.index('class="comparison" data-basis="annual"')
    assert 'data-from="6" data-to="7"' in comparison(html, "quarter")
    annual = comparison(html, "annual")
    assert 'data-from="4" data-to="6"' in annual and "קפיצה של 2 שלבים בבת אחת." in annual
    assert ">102.7%<" in line(annual, "efficiency") and ">80.4%<" in line(annual, "efficiency")
    assert "+281.8%" in line(annual, "revenue")
    highlight = re.search(r'<li class="highlight highlight-up" data-basis="annual">(.*?)</li>', block).group(1)
    assert highlight == "מעבר מהפסד נקי של 81 מיליון דולר לרווח נקי של 3 מיליארד דולר בהשוואה השנתית."
    # the fourth quarter is derived, and the panel says from what
    assert "מחושב: שנה מלאה פחות תשעה חודשים." in line(comparison(html, "quarter"), "revenue")
    assert_clean(resp)


def losing_facts(nvda_facts, current_operating=None, current_net=None):
    """NVDA's latest quarter turned into a loss in both years; the current quarter can be set."""
    facts = copy.deepcopy(nvda_facts)
    gaap = facts["facts"]["us-gaap"]
    for tag, current in (("OperatingIncomeLoss", current_operating), ("NetIncomeLoss", current_net)):
        for fact in gaap[tag]["units"]["USD"]:
            if fact["accn"] != LATEST_ACCN:
                continue
            fact["val"] = -abs(fact["val"])
            if current is not None and (fact["start"], fact["end"]) == ("2026-04-27", "2026-07-26"):
                fact["val"] = current
    return facts


def test_reading_same_stage_is_shown_as_staying(nvda_submissions, nvda_facts):
    # still losing money, but spending less per dollar of revenue: stage 4 in both years
    facts = losing_facts(nvda_facts, current_operating=-10_000_000_000, current_net=-8_000_000_000)
    html = text_of(page(make_client(nvda_submissions, facts), "/summary/NVDA"))
    q = comparison(html, "quarter")
    assert 'data-from="4" data-to="4"' in q
    assert re.search(r'נשארה בשלב <bdi dir="ltr" class="num">4</bdi>', q) and "משלב" not in q
    assert "ירידה או עמידה באותו שלב: לא מעניינת ללונג" in q
    assert "מעבר מהפסד נקי של 26.4 מיליארד דולר להפסד נקי של 8 מיליארד דולר." in reading(html)
    assert '<ul class="highlights">' not in reading(html)


def test_reading_going_down_a_stage(nvda_submissions, nvda_facts):
    # losing money and spending more per dollar of revenue than a year earlier: from stage 4 down to 3
    html = text_of(page(make_client(nvda_submissions, losing_facts(nvda_facts)), "/summary/NVDA"))
    q = comparison(html, "quarter")
    assert 'data-from="4" data-to="3"' in q
    assert re.search(r'משלב <bdi dir="ltr" class="num">4</bdi> לשלב <bdi dir="ltr" class="num">3</bdi>', q)
    assert "לא מעניינת ללונג" in q and "change bad" in line(q, "efficiency")


def test_reading_turn_to_a_loss_is_highlighted(nvda_submissions, nvda_facts):
    facts = copy.deepcopy(nvda_facts)
    for fact in facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]:
        if fact["accn"] == LATEST_ACCN and (fact["start"], fact["end"]) == ("2026-04-27", "2026-07-26"):
            fact["val"] = -2_000_000_000
    block = reading(text_of(page(make_client(nvda_submissions, facts), "/summary/NVDA")))
    assert ('<li class="highlight highlight-down" data-basis="quarter">'
            "מעבר מרווח נקי של 26.4 מיליארד דולר להפסד נקי של 2 מיליארד דולר בהשוואה הרבעונית.</li>") in block


def test_reading_missing_lines_show_not_reported(nvda_submissions, nvda_facts):
    facts = copy.deepcopy(nvda_facts)
    del facts["facts"]["us-gaap"]["OperatingIncomeLoss"]
    resp = page(make_client(nvda_submissions, facts), "/summary/NVDA")
    q = comparison(text_of(resp), "quarter")
    assert line(q, "efficiency").count("לא דווח") == 2 and line(q, "operating_income").count("לא דווח") == 2
    assert ">96,221<" in line(q, "revenue")
    assert "אין נתון התייעלות, אי אפשר לבדוק את תנאי השלב." in q
    assert_clean(resp)


def test_reading_without_any_facts_says_not_reported(nvda_submissions):
    resp = page(make_client(nvda_submissions, {"facts": {"us-gaap": {}}}), "/summary/NVDA")
    block = reading(text_of(resp))
    assert resp.status_code == 200
    assert 'data-status="empty"' in block and "לא דווח" in block and "<table" not in block


def test_reading_crash_never_breaks_the_page(nvda_client, monkeypatch, caplog):
    import backend.app as app_module

    def boom(*args, **kwargs):
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(app_module, "filing_periods", boom)
    resp = create_app(nvda_client).test_client().get("/summary/NVDA")
    html = text_of(resp)
    assert resp.status_code == 200 and html.count('<tr data-key=') == 9
    assert 'data-status="unavailable"' in reading(html) and "לא זמין כרגע" in reading(html)
    assert "secret-internal-detail" not in html and "Report reading failed" in caplog.text
    assert_clean(resp)


def test_reading_view_crash_never_breaks_the_page(nvda_client, monkeypatch, caplog):
    import backend.app as app_module

    real = app_module.build_niv_view

    def boom(result):
        if result is not None:
            raise OverflowError("secret-internal-detail")
        return real(result)

    monkeypatch.setattr(app_module, "build_niv_view", boom)
    resp = create_app(nvda_client).test_client().get("/summary/NVDA")
    html = text_of(resp)
    assert resp.status_code == 200 and 'data-status="unavailable"' in reading(html)
    assert "secret-internal-detail" not in html and "Report reading failed" in caplog.text


def test_reading_survives_absurd_numbers_in_the_facts(nvda_submissions, nvda_facts):
    facts = copy.deepcopy(nvda_facts)
    for tag in ("Revenues", "NetIncomeLoss", "OperatingIncomeLoss"):
        for fact in facts["facts"]["us-gaap"][tag]["units"]["USD"]:
            if fact["accn"] == LATEST_ACCN and (fact["start"], fact["end"]) == ("2026-04-27", "2026-07-26"):
                fact["val"] = 1e308
    resp = page(make_client(nvda_submissions, facts), "/summary/NVDA")
    assert resp.status_code == 200 and html_has_reading(text_of(resp))
    assert_clean(resp)


def html_has_reading(html: str) -> bool:
    return 'class="panel reading"' in html and html.count('<tr data-key=') == 9


def test_reading_escapes_whatever_comes_from_the_facts(nvda_submissions, nvda_facts):
    facts = copy.deepcopy(nvda_facts)
    for fact in facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"]:
        if fact["accn"] == LATEST_ACCN:
            fact["form"] = XSS
            fact["filed"] = XSS
    resp = page(make_client(nvda_submissions, facts), "/summary/NVDA")
    assert XSS not in text_of(resp)
    assert_clean(resp)


def test_reading_has_no_inline_styles_scripts_or_foreign_links(fake_time):
    html = text_of(page(clf_client(), "/summary/NVDA"))
    block = reading(html)
    assert " style=" not in block and "<script" not in block
    assert all(url.startswith("https://www.sec.gov/") for url in SEC_LINK.findall(block))
    assert not any(w in html for w in FORBIDDEN_WORDS)


# ---------- lesson 14: previous quarter line and the efficiency breakdown ----------

NVDA_Q1_FY24 = "0001045810-23-000093"


def nvda_q1_client():
    """Nvidia's report for the quarter that ended on 30.4.2023, served through the fake SEC session."""
    facts = json.loads((Path(__file__).parent / "fixtures" / "nvda_q1fy24_companyfacts_min.json").read_text())
    subs = {"name": "NVIDIA CORP", "sic": "3674", "filings": {"recent": {
        "accessionNumber": [NVDA_Q1_FY24], "filingDate": ["2023-05-26"], "reportDate": ["2023-04-30"],
        "form": ["10-Q"], "primaryDocument": ["nvda-20230430.htm"]}}}
    return make_client(subs, facts)


def previous_block(html: str) -> str:
    return re.search(r'<div class="prev-quarter" data-line="previous_quarter">.*?</div>\s*</div>', reading(html), re.S).group(0)


def test_reading_lesson_14_report(fake_time):
    resp = page(nvda_q1_client(), "/summary/NVDA")
    html = text_of(resp)
    assert resp.status_code == 200
    q = comparison(html, "quarter")
    assert ">8,288<" in line(q, "revenue") and ">7,192<" in line(q, "revenue")
    assert "-13.2%" in line(q, "revenue") and "change bad" in line(q, "revenue")
    efficiency = line(q, "efficiency")
    assert ">77.5%<" in efficiency and ">70.2%<" in efficiency and "-7.2 pp" in efficiency
    assert "מחושב: עלות מכר ועוד הוצאות תפעול." in efficiency
    assert "CostOfRevenue" in efficiency and "OperatingExpenses" in efficiency
    previous = previous_block(html)
    assert "הכנסות מול הרבעון הקודם" in previous and ">6,051<" in previous and "+18.9%" in previous
    assert "change good" in previous and ">29.01.2023<" in previous
    assert "מחושב: שנה מלאה פחות תשעה חודשים." in previous
    # the two comparisons stay apart: the previous quarter is outside the same-quarter table
    assert "6,051" not in q
    assert_clean(resp)


def test_reading_previous_quarter_on_a_regular_quarterly_report(http):
    html = text_of(http.get("/summary/NVDA"))
    previous = previous_block(html)
    assert ">81,615<" in previous and "+17.9%" in previous and ">26.04.2026<" in previous


def test_reading_without_previous_quarter_has_no_block(fake_time):
    html = text_of(page(clf_client(), "/summary/NVDA"))
    assert 'class="prev-quarter"' not in html


def test_reading_previous_quarter_has_no_inline_styles_or_foreign_links(fake_time):
    resp = page(nvda_q1_client(), "/summary/NVDA")
    block = reading(text_of(resp))
    assert " style=" not in block and "<script" not in block
    assert all(url.startswith("https://www.sec.gov/") for url in SEC_LINK.findall(block))
