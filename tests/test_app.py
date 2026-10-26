import pytest

from backend import sec_client
from backend.app import create_app
from backend.sec_client import SecClient
from tests.conftest import (
    NVDA_CIK, TEST_UA, TICKERS, FakeResponse, FakeSession,
)

FORBIDDEN = ("Traceback", "File \"", "Exception", "KeyError", ".py")


@pytest.fixture
def http(nvda_client):
    return create_app(nvda_client).test_client()


def assert_hebrew_error(resp, status):
    assert resp.status_code == status
    assert resp.mimetype == "application/json"
    body = resp.get_json()
    assert any("֐" <= c <= "׿" for c in body["error"])
    text = resp.get_data(as_text=True)
    assert not any(bad in text for bad in FORBIDDEN)
    return body


def test_health(http):
    resp = http.get("/api/health")
    assert resp.status_code == 200 and resp.get_json() == {"status": "ok"}


def test_filings_ok(http):
    resp = http.get("/api/filings/nvda")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["company"] == "NVIDIA CORP" and body["cik"] == NVDA_CIK
    assert body["latest"]["accession"] == "0001045810-26-000075"
    assert body["previous"]["accession"] == "0001045810-26-000052"


def test_ratios_ok_and_hebrew_not_escaped(http):
    resp = http.get("/api/ratios/NVDA")
    assert resp.status_code == 200
    raw = resp.get_data(as_text=True)
    assert "שיעור רווח גולמי" in raw and "\\u05" not in raw
    ratios = resp.get_json()["latest"]["ratios"]
    assert [r["key"] for r in ratios][0] == "revenue_growth_yoy"


@pytest.mark.parametrize("path", ["NV%20DA", "A" * 11, "%3Cscript%3E", "%20"])
def test_invalid_ticker_400(http, path):
    for prefix in ("filings", "ratios"):
        assert_hebrew_error(http.get(f"/api/{prefix}/{path}"), 400)


def test_unknown_ticker_404_with_suggestions(http):
    body = assert_hebrew_error(http.get("/api/ratios/NVDX"), 404)
    assert body["suggestions"][0]["ticker"] == "NVDA"


def test_no_periodic_filings_404(fake_time):
    session = FakeSession({
        sec_client.TICKERS_URL: FakeResponse(200, TICKERS),
        sec_client.SUBMISSIONS_URL.format(cik=999999): FakeResponse(200, {
            "filings": {"recent": {"form": ["8-K"], "accessionNumber": ["x-1"],
                                   "filingDate": ["2026-01-01"], "reportDate": [""],
                                   "primaryDocument": ["a.htm"]}}}),
    })
    http = create_app(SecClient(TEST_UA, session=session)).test_client()
    for prefix in ("filings", "ratios"):
        assert_hebrew_error(http.get(f"/api/{prefix}/EMPT"), 404)


def test_upstream_failure_502(fake_time):
    session = FakeSession({sec_client.TICKERS_URL: FakeResponse(503)})
    http = create_app(SecClient(TEST_UA, session=session, max_retries=1)).test_client()
    assert_hebrew_error(http.get("/api/ratios/NVDA"), 502)


def test_missing_config_500_generic(monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    http = create_app().test_client()
    assert http.get("/api/health").status_code == 200
    body = assert_hebrew_error(http.get("/api/ratios/NVDA"), 500)
    assert "SEC_EDGAR" not in body["error"]


def test_unexpected_error_500_no_leak(nvda_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("backend.app.analyze", boom)
    resp = create_app(nvda_client).test_client().get("/api/ratios/NVDA")
    assert_hebrew_error(resp, 500)
    assert "secret internal detail" not in resp.get_data(as_text=True)


def test_unknown_route_and_method_json(http):
    assert_hebrew_error(http.get("/nope"), 404)
    assert_hebrew_error(http.post("/api/health"), 405)
