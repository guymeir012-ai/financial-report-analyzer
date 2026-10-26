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
    """rows: (tag, start|None, end, value); builds a minimal companyfacts document."""
    gaap: dict[str, Any] = {}
    for tag, start, end, val in rows:
        fact = {"end": end, "val": val, "accn": accession, "form": "10-Q", "filed": "2026-08-01"}
        if start:
            fact["start"] = start
        gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(fact)
    return {"cik": 1, "entityName": "TEST", "facts": {"us-gaap": gaap}}


class FakeResponse:
    def __init__(self, status_code=200, data: Any = None, headers=None):
        self.status_code = status_code
        self._data = data
        self.headers = headers or {}

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
