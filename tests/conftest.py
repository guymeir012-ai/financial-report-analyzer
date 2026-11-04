import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from backend import sec_client
from backend.filings import Filing, filing_url, list_periodic_filings

FIXTURES = Path(__file__).parent / "fixtures"
NVDA_CIK = 1045810
LATEST_ACCN = "0001045810-26-000075"
PREVIOUS_ACCN = "0001045810-26-000052"
LATEST_URL = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/nvda-20260726.htm"
TEST_UA = "Test Suite tester@example.com"


@pytest.fixture(autouse=True)
def block_network(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return
    # unit tests never build real price clients from a developer's .env
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("network access is not allowed in unit tests")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", refuse)


@pytest.fixture(scope="session")
def nvda_facts() -> dict[str, Any]:
    return json.loads((FIXTURES / "nvda_companyfacts_min.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def nvda_submissions() -> dict[str, Any]:
    return json.loads((FIXTURES / "nvda_submissions_min.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def nvda_filings(nvda_submissions) -> list[Filing]:
    return list_periodic_filings(nvda_submissions, NVDA_CIK)


@pytest.fixture
def latest(nvda_filings) -> Filing:
    return next(x for x in nvda_filings if x.accession == LATEST_ACCN)


@pytest.fixture
def previous(nvda_filings) -> Filing:
    return next(x for x in nvda_filings if x.accession == PREVIOUS_ACCN)


def make_filing(form="10-Q", accession="0000000001-26-000001", report_date="2026-06-30",
                filing_date="2026-08-01", cik=1, doc="x-20260630.htm") -> Filing:
    return Filing(form, accession, filing_date, report_date, doc, cik, filing_url(cik, accession, doc))


def make_facts(accession: str, rows: list[tuple]) -> dict[str, Any]:
    """rows: (tag, start|None, end, value[, accn, form, unit]); builds a minimal companyfacts document."""
    gaap: dict[str, Any] = {}
    for tag, start, end, val, *extra in rows:
        accn, form, unit = (extra + [None, None, None])[:3]
        fact = {"end": end, "val": val, "accn": accn or accession, "form": form or "10-Q",
                "filed": "2026-08-01"}
        if start:
            fact["start"] = start
        gaap.setdefault(tag, {"units": {}})["units"].setdefault(unit or "USD", []).append(fact)
    return {"cik": 1, "entityName": "TEST", "facts": {"us-gaap": gaap}}


class FakeResponse:
    def __init__(self, status_code=200, data: Any = None, headers=None, content: bytes | None = None):
        self.status_code = status_code
        self._data = data
        self.headers = headers or {}
        self.content = content if content is not None else (
            json.dumps(data).encode("utf-8") if data is not None else b"")

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakeSession:
    """routes: url -> response | list of responses (consumed in order, last repeats)."""

    def __init__(self, routes: dict[str, Any]):
        self.routes = {k: (list(v) if isinstance(v, list) else [v]) for k, v in routes.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        queue = self.routes.get(url)
        if queue is None:
            return FakeResponse(404)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_time(monkeypatch):
    clock = SimpleNamespace(now=1000.0, sleeps=[])

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(sec_client, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    return clock


TICKERS = {
    "0": {"cik_str": NVDA_CIK, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "3": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
    "4": {"cik_str": 999999, "ticker": "EMPT", "title": "EMPTY FILER INC"},
}


@pytest.fixture
def nvda_session(nvda_facts, nvda_submissions):
    return FakeSession({
        sec_client.TICKERS_URL: FakeResponse(200, TICKERS),
        sec_client.SUBMISSIONS_URL.format(cik=NVDA_CIK): FakeResponse(200, nvda_submissions),
        sec_client.COMPANY_FACTS_URL.format(cik=NVDA_CIK): FakeResponse(200, nvda_facts),
    })


@pytest.fixture
def nvda_client(nvda_session, fake_time):
    return sec_client.SecClient(TEST_UA, session=nvda_session)


# ---- price provider stubs (shared by valuation and page tests) ----

class StubFinnhub:
    def __init__(self, quote=None, peers=(), metrics=None, failing=(), peers_error=None, quote_error=None):
        self._quote, self._peers, self._metrics = quote, list(peers), metrics or {}
        self.failing, self.peers_error, self.quote_error = set(failing), peers_error, quote_error
        self.calls = []

    def quote(self, symbol):
        self.calls.append(("quote", symbol))
        if self.quote_error:
            raise self.quote_error
        return self._quote

    def peers(self, symbol):
        self.calls.append(("peers", symbol))
        if self.peers_error:
            raise self.peers_error
        return {"peers": self._peers, "fetched_at": 1_800_000_000.0}

    def metrics(self, symbol):
        from backend.prices import PriceError

        self.calls.append(("metrics", symbol))
        if symbol in self.failing:
            raise PriceError(status=503)
        m = self._metrics.get(symbol, {"pe_ttm": None, "eps_ttm": None, "market_cap": None})
        return {**m, "fetched_at": 1_800_000_000.0}


class StubTiingo:
    def __init__(self, closes, error=None):
        self._closes, self.error, self.calls = closes, error, []

    def daily_closes(self, symbol, start):
        self.calls.append((symbol, start))
        if self.error:
            raise self.error
        return {"closes": self._closes, "fetched_at": 1_800_000_000.0}


QUOTE = {"price": 203.48, "previous_close": 206.69, "timestamp": 1793814988, "fetched_at": 1793820231.5}
CLOSES = [("2026-07-20", 203.28), ("2026-07-23", 208.76), ("2026-07-24", 206.84), ("2026-07-27", 196.51),
          ("2026-04-24", 100.0), ("2026-01-23", 50.0)]
PEER_METRICS = {
    "AVGO": {"pe_ttm": 41.5, "eps_ttm": 7.8, "market_cap": 1_589_762},
    "AMD": {"pe_ttm": 80.0, "eps_ttm": 2.0, "market_cap": 300_000},
    "INTC": {"pe_ttm": None, "eps_ttm": -1.2, "market_cap": 150_000},
    "MU": {"pe_ttm": 20.0, "eps_ttm": 5.0, "market_cap": 200_000},
    "TXN": {"pe_ttm": 30.0, "eps_ttm": 5.0, "market_cap": 180_000},
    "NVDA": {"pe_ttm": 25.8, "eps_ttm": 7.91, "market_cap": 4_981_229},
}
