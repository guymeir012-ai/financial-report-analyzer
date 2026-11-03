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
