import threading

import pytest
import requests

from backend import sec_client
from backend.config import ConfigError
from backend.sec_client import (
    InvalidTicker, SecClient, SecError, TickerNotFound, normalize_ticker, resolve_ticker,
)
from tests.conftest import NVDA_CIK, TEST_UA, FakeResponse, FakeSession

URL = sec_client.SUBMISSIONS_URL.format(cik=NVDA_CIK)


def client_for(routes, **kwargs):
    session = FakeSession(routes)
    return SecClient(TEST_UA, session=session, **kwargs), session


@pytest.mark.parametrize("bad", ["", "   ", "NV DA", "../etc", "A" * 11, "<script>", "-X", "NV/DA", None])
def test_invalid_tickers_rejected(bad):
    with pytest.raises(InvalidTicker):
        normalize_ticker(bad)


@pytest.mark.parametrize("raw,expected", [(" nvda ", "NVDA"), ("brk-b", "BRK-B"), ("BRK.B", "BRK.B"), ("A" * 10, "A" * 10)])
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("ua", ["", "No Email Here", None])
def test_rejects_bad_user_agent(ua):
    with pytest.raises(ConfigError):
        SecClient(ua)


def test_sends_user_agent_and_disables_redirects(fake_time):
    client, session = client_for({URL: FakeResponse(200, {"ok": 1})})
    assert client.submissions(NVDA_CIK) == {"ok": 1}
    _, kwargs = session.calls[0]
    assert kwargs["headers"]["User-Agent"] == TEST_UA
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == 15


def test_retries_on_429_then_succeeds(fake_time):
    client, session = client_for({URL: [FakeResponse(429), FakeResponse(503), FakeResponse(200, {"ok": 1})]})
    assert client.submissions(NVDA_CIK) == {"ok": 1}
    assert len(session.calls) == 3
    assert any(s >= 0.5 for s in fake_time.sleeps)


def test_retries_on_connection_error(fake_time):
    client, session = client_for({URL: [requests.ConnectionError("boom"), FakeResponse(200, {"ok": 1})]})
    assert client.submissions(NVDA_CIK) == {"ok": 1}
    assert len(session.calls) == 2


def test_gives_up_after_max_retries(fake_time):
    client, session = client_for({URL: FakeResponse(429)}, max_retries=3)
    with pytest.raises(SecError) as exc:
        client.submissions(NVDA_CIK)
    assert len(session.calls) == 4
    assert any("֐" <= ch <= "׿" for ch in exc.value.message_he)


def test_gives_up_on_persistent_connection_error(fake_time):
    client, session = client_for({URL: requests.Timeout("slow")}, max_retries=2)
    with pytest.raises(SecError):
        client.submissions(NVDA_CIK)
    assert len(session.calls) == 3


@pytest.mark.parametrize("status", [400, 403, 404])
def test_no_retry_on_client_errors(fake_time, status):
    client, session = client_for({URL: FakeResponse(status)})
    with pytest.raises(SecError):
        client.submissions(NVDA_CIK)
    assert len(session.calls) == 1


def test_invalid_json_raises_sec_error(fake_time):
    client, _ = client_for({URL: FakeResponse(200, None)})
    with pytest.raises(SecError):
        client.submissions(NVDA_CIK)


def test_rate_limiter_spaces_requests(fake_time):
    client, session = client_for({URL: FakeResponse(200, {})}, min_interval=0.11)
    start = fake_time.now
    for _ in range(5):
        client.submissions(NVDA_CIK)
    assert fake_time.now - start == pytest.approx(0.11 * 4)
    assert len(session.calls) == 5


def test_rate_limiter_is_thread_safe(fake_time):
    client, session = client_for({URL: FakeResponse(200, {})}, min_interval=0.11)
    threads = [threading.Thread(target=client.submissions, args=(NVDA_CIK,)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(session.calls) == 6
    assert sum(fake_time.sleeps) == pytest.approx(0.11 * 5)


@pytest.mark.parametrize("cik", [0, -1, "1045810", None, 10**10, True])
def test_rejects_invalid_cik(fake_time, cik):
    client, session = client_for({})
    with pytest.raises(ValueError):
        client.company_facts(cik)
    assert session.calls == []


def test_only_sec_hosts_allowed(fake_time):
    client, session = client_for({})
    with pytest.raises(SecError):
        client._get_json("https://evil.example.com/x.json")
    with pytest.raises(SecError):
        client._get_json("http://www.sec.gov/files/company_tickers.json")
    assert session.calls == []


def test_url_templates_use_padded_cik(fake_time):
    client, session = client_for({})
    for call in (client.submissions, client.company_facts):
        with pytest.raises(SecError):
            call(320193)
    assert [u for u, _ in session.calls] == [
        "https://data.sec.gov/submissions/CIK0000320193.json",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    ]


def test_ticker_map_cached_with_ttl(fake_time):
    from tests.conftest import TICKERS
    client, session = client_for({sec_client.TICKERS_URL: FakeResponse(200, TICKERS)})
    mapping = client.ticker_map()
    assert mapping["NVDA"] == {"cik": NVDA_CIK, "title": "NVIDIA CORP"}
    client.ticker_map()
    assert len(session.calls) == 1
    fake_time.now += 24 * 3600 + 1
    client.ticker_map()
    assert len(session.calls) == 2


def test_resolve_ticker_found(nvda_client):
    assert resolve_ticker(nvda_client, "nvda") == (NVDA_CIK, "NVIDIA CORP")


def test_resolve_ticker_accepts_dot_class_spelling(nvda_client):
    assert resolve_ticker(nvda_client, "BRK.B")[0] == 1067983


def test_unknown_ticker_gives_suggestions(nvda_client):
    with pytest.raises(TickerNotFound) as exc:
        resolve_ticker(nvda_client, "NVDX")
    suggestions = exc.value.suggestions
    assert 1 <= len(suggestions) <= 5
    assert suggestions[0] == {"ticker": "NVDA", "title": "NVIDIA CORP"}


def test_unknown_ticker_suggests_by_company_name(nvda_client):
    with pytest.raises(TickerNotFound) as exc:
        resolve_ticker(nvda_client, "MICROSOF")
    assert "MSFT" in [s["ticker"] for s in exc.value.suggestions]


def test_unknown_ticker_with_no_matches_has_empty_suggestions(nvda_client):
    with pytest.raises(TickerNotFound) as exc:
        resolve_ticker(nvda_client, "ZZZZ9")
    assert exc.value.suggestions == []
