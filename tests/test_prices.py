"""Finnhub and Tiingo clients: keys only in headers, cache, retries, stale copies, validation."""
import hashlib
import json
import logging
from urllib.parse import urlencode

import pytest
import requests

from backend import prices
from backend.config import ConfigError
from backend.prices import FinnhubClient, PriceError, TiingoClient
from backend.sec_client import InvalidTicker
from tests.conftest import FakeResponse, FakeSession

KEY = "sekrit-token-123"
HOUR = 3600.0


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class Sleeper:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def finnhub_url(path, **params):
    return f"{prices.FINNHUB_BASE}{path}?{urlencode(sorted(params.items()))}"


QUOTE_URL = finnhub_url("/quote", symbol="NVDA")
PEERS_URL = finnhub_url("/stock/peers", symbol="NVDA")
METRIC_URL = finnhub_url("/stock/metric", metric="all", symbol="NVDA")
TIINGO_URL = f"{prices.TIINGO_BASE}/tiingo/daily/NVDA/prices?{urlencode([('columns', 'date,close'), ('startDate', '2021-01-01')])}"
QUOTE = {"c": 203.48, "pc": 206.69, "t": 1793814988, "h": 0, "l": 0, "o": 0}
METRIC = {"metric": {"peTTM": 25.2472, "epsTTM": 7.9108, "marketCapitalization": 4981228.94},
          "series": {}, "symbol": "NVDA"}
DAILY = [
    {"date": "2026-07-24T00:00:00.000Z", "close": 206.84},
    {"date": "2026-07-23T00:00:00.000Z", "close": 208.76},
    {"date": "2026-07-27T00:00:00.000Z", "close": 196.51},
]


def finnhub(routes, tmp_path=None, clock=None, **kwargs):
    session = FakeSession(routes)
    sleeper = Sleeper()
    client = FinnhubClient(KEY, session=session, cache_dir=tmp_path / "cache" if tmp_path else None,
                           wall_clock=clock or Clock(), sleep=sleeper, **kwargs)
    return client, session, sleeper


def tiingo(routes, tmp_path=None, clock=None, **kwargs):
    session = FakeSession(routes)
    client = TiingoClient(KEY, session=session, cache_dir=tmp_path / "cache" if tmp_path else None,
                          wall_clock=clock or Clock(), sleep=Sleeper(), **kwargs)
    return client, session


# ---- authentication and request shape ----

def test_finnhub_token_only_in_header():
    client, session, _ = finnhub({QUOTE_URL: FakeResponse(200, QUOTE)})
    client.quote("NVDA")
    url, kwargs = session.calls[0]
    assert KEY not in url and url == QUOTE_URL
    assert kwargs["headers"] == {"X-Finnhub-Token": KEY, "Accept": "application/json"}
    assert kwargs["timeout"] == 10 and kwargs["allow_redirects"] is False


def test_tiingo_token_only_in_header():
    client, session = tiingo({TIINGO_URL: FakeResponse(200, DAILY)})
    client.daily_closes("NVDA", "2021-01-01")
    url, kwargs = session.calls[0]
    assert KEY not in url and url == TIINGO_URL
    assert kwargs["headers"] == {"Authorization": f"Token {KEY}", "Accept": "application/json"}


@pytest.mark.parametrize("key", ["", "   ", None])
def test_missing_key_is_a_config_error(key):
    with pytest.raises(ConfigError):
        FinnhubClient(key)
    with pytest.raises(ConfigError):
        TiingoClient(key)


@pytest.mark.parametrize("bad", ["", "nv da", "../etc", "<x>", "A" * 11])
def test_invalid_symbol_rejected_before_any_request(bad):
    client, session, _ = finnhub({})
    for call in (client.quote, client.peers, client.metrics):
        with pytest.raises(InvalidTicker):
            call(bad)
    t, s2 = tiingo({})
    with pytest.raises(InvalidTicker):
        t.daily_closes(bad, "2021-01-01")
    assert session.calls == [] and s2.calls == []


def test_symbol_is_normalized_and_share_classes_kept():
    client, session, _ = finnhub({finnhub_url("/quote", symbol="BRK-B"): FakeResponse(200, QUOTE)})
    assert client.quote(" brk-b ")["price"] == 203.48


@pytest.mark.parametrize("base", ["http://finnhub.io/api/v1", "https://evil.example/api/v1", "https://finnhub.io.evil/api"])
def test_disallowed_base_url_never_requested(base):
    client, session, _ = finnhub({}, base_url=base)
    with pytest.raises(PriceError):
        client.quote("NVDA")
    assert session.calls == []


# ---- Finnhub parsing ----

def test_quote_values_and_timestamp():
    client, _, _ = finnhub({QUOTE_URL: FakeResponse(200, QUOTE)})
    q = client.quote("NVDA")
    assert q["price"] == 203.48 and q["previous_close"] == 206.69 and q["timestamp"] == 1793814988
    assert q["fetched_at"] == 1_800_000_000.0


@pytest.mark.parametrize("body", [
    {"c": 0, "pc": 0, "t": 0}, {"c": None}, {}, [], {"c": "203"}, {"c": -1, "t": 5}, {"c": True, "t": 5},
])
def test_quote_without_a_real_price_is_none(body):
    client, _, _ = finnhub({QUOTE_URL: FakeResponse(200, body)})
    assert client.quote("NVDA") is None


def test_peers_validated_deduped_without_self():
    raw = ["NVDA", "AVGO", "MU", "../etc", "MRVL", "MRVL", 123, None, "bad symbol", "brk-b", "AMD"]
    client, _, _ = finnhub({PEERS_URL: FakeResponse(200, raw)})
    p = client.peers("NVDA")
    assert p["peers"] == ["AVGO", "MU", "MRVL", "BRK-B", "AMD"]


@pytest.mark.parametrize("body", [[], {}, "x", 0])
def test_peers_empty_or_wrong_shape_is_empty_list(body):
    client, _, _ = finnhub({PEERS_URL: FakeResponse(200, body)})
    assert client.peers("NVDA")["peers"] == []


def test_metrics_fields():
    client, _, _ = finnhub({METRIC_URL: FakeResponse(200, METRIC)})
    m = client.metrics("NVDA")
    assert m == {"pe_ttm": 25.2472, "eps_ttm": 7.9108, "market_cap": 4981228.94, "fetched_at": 1_800_000_000.0}


@pytest.mark.parametrize("metric", [
    {"peTTM": None, "epsTTM": -2.58, "marketCapitalization": 19399.361},
    {"peTTM": "25", "epsTTM": float("nan"), "marketCapitalization": True},
    {},
])
def test_metrics_missing_or_bad_numbers_are_none(metric):
    client, _, _ = finnhub({METRIC_URL: FakeResponse(200, {"metric": metric, "series": {}})})
    m = client.metrics("NVDA")
    assert m["pe_ttm"] is None
    assert m["eps_ttm"] == (-2.58 if metric.get("epsTTM") == -2.58 else None)
    assert m["market_cap"] == (19399.361 if metric.get("marketCapitalization") == 19399.361 else None)


def test_metrics_without_metric_block():
    client, _, _ = finnhub({METRIC_URL: FakeResponse(200, {"symbol": "ZZZZQQ", "series": {}})})
    assert client.metrics("NVDA") == {"pe_ttm": None, "eps_ttm": None, "market_cap": None,
                                      "fetched_at": 1_800_000_000.0}


# ---- Tiingo parsing ----

def test_daily_closes_sorted_and_cleaned():
    rows = DAILY + [{"date": "bad", "close": 1}, {"date": "2026-07-28T00:00:00.000Z", "close": None},
                    {"close": 5}, "junk", {"date": "2026-07-29T00:00:00.000Z", "close": "197"}]
    client, _ = tiingo({TIINGO_URL: FakeResponse(200, rows)})
    out = client.daily_closes("NVDA", "2021-01-01")
    assert out["closes"] == [("2026-07-23", 208.76), ("2026-07-24", 206.84), ("2026-07-27", 196.51)]
    assert out["fetched_at"] == 1_800_000_000.0


@pytest.mark.parametrize("body", [{}, "x", 0, {"detail": "Error"}])
def test_daily_closes_wrong_shape_is_empty(body):
    client, _ = tiingo({TIINGO_URL: FakeResponse(200, body)})
    assert client.daily_closes("NVDA", "2021-01-01")["closes"] == []


@pytest.mark.parametrize("start", ["2021-1-1", "yesterday", "2021-01-01T00:00", "", "2021-13-01", "'; DROP"])
def test_daily_closes_rejects_bad_start_date(start):
    client, session = tiingo({})
    with pytest.raises(ValueError):
        client.daily_closes("NVDA", start)
    assert session.calls == []


def test_tiingo_404_unknown_ticker_raises():
    client, _ = tiingo({TIINGO_URL: FakeResponse(404, {"detail": "Error: Ticker 'NVDA' not found"})})
    with pytest.raises(PriceError) as exc:
        client.daily_closes("NVDA", "2021-01-01")
    assert exc.value.status == 404


# ---- failures, retries and never leaking the key ----

def test_retries_once_on_transient_then_succeeds():
    client, session, sleeper = finnhub({QUOTE_URL: [FakeResponse(429), FakeResponse(200, QUOTE)]})
    assert client.quote("NVDA")["price"] == 203.48
    assert len(session.calls) == 2 and sleeper.calls == [1.0]


@pytest.mark.parametrize("failure, status", [
    (FakeResponse(429), 429), (FakeResponse(503), 503), (requests.ConnectionError("down"), None),
    (requests.Timeout("slow"), None),
])
def test_gives_up_after_one_retry(failure, status):
    client, session, _ = finnhub({QUOTE_URL: failure})
    with pytest.raises(PriceError) as exc:
        client.quote("NVDA")
    assert exc.value.status == status and len(session.calls) == 2


@pytest.mark.parametrize("failure, status", [
    (FakeResponse(401, {"error": "Invalid API key."}), 401), (FakeResponse(403), 403),
    (FakeResponse(404), 404), (FakeResponse(200, None), 200), (FakeResponse(302), 302),
])
def test_non_transient_failures_do_not_retry(failure, status):
    client, session, sleeper = finnhub({QUOTE_URL: failure})
    with pytest.raises(PriceError) as exc:
        client.quote("NVDA")
    assert exc.value.status == status and len(session.calls) == 1 and sleeper.calls == []
    assert exc.value.message_he == prices.UNAVAILABLE_HE


def test_invalid_key_logged_without_the_key(caplog):
    client, _, _ = finnhub({QUOTE_URL: FakeResponse(401, {"error": "Invalid API key."})})
    with caplog.at_level(logging.WARNING):
        with pytest.raises(PriceError) as exc:
            client.quote("NVDA")
    assert "key" in caplog.text.lower() and KEY not in caplog.text and KEY not in str(exc.value)


def test_oversized_body_rejected():
    big = FakeResponse(200, {"c": 1, "t": 1}, content=b"x" * (prices.MAX_BODY_BYTES + 1))
    client, _, _ = finnhub({QUOTE_URL: big})
    with pytest.raises(PriceError):
        client.quote("NVDA")


# ---- disk cache ----

def test_cache_hit_needs_no_network_and_file_has_no_token(tmp_path):
    clock = Clock()
    first, s1, _ = finnhub({QUOTE_URL: FakeResponse(200, QUOTE)}, tmp_path, clock)
    assert first.quote("NVDA")["price"] == 203.48 and len(s1.calls) == 1
    second, s2, _ = finnhub({}, tmp_path, clock)
    assert second.quote("NVDA")["price"] == 203.48 and s2.calls == []
    (path,) = sorted((tmp_path / "cache").iterdir())
    assert path.name == hashlib.sha256(QUOTE_URL.encode()).hexdigest() + ".json"
    assert KEY not in path.read_text(encoding="utf-8")


def test_quote_expires_after_15_minutes(tmp_path):
    clock = Clock()
    client, session, _ = finnhub({QUOTE_URL: [FakeResponse(200, QUOTE), FakeResponse(200, {**QUOTE, "c": 210.0})]},
                                 tmp_path, clock)
    client.quote("NVDA")
    clock.now += 15 * 60 - 1
    assert client.quote("NVDA")["price"] == 203.48 and len(session.calls) == 1
    clock.now += 2
    assert client.quote("NVDA")["price"] == 210.0 and len(session.calls) == 2


def test_peers_metrics_and_history_expire_after_24_hours(tmp_path):
    clock = Clock()
    client, session, _ = finnhub({PEERS_URL: FakeResponse(200, ["AMD"]), METRIC_URL: FakeResponse(200, METRIC)},
                                 tmp_path, clock)
    t_client, t_session = tiingo({TIINGO_URL: FakeResponse(200, DAILY)}, tmp_path, clock)
    client.peers("NVDA"), client.metrics("NVDA"), t_client.daily_closes("NVDA", "2021-01-01")
    clock.now += 24 * HOUR - 1
    client.peers("NVDA"), client.metrics("NVDA"), t_client.daily_closes("NVDA", "2021-01-01")
    assert len(session.calls) == 2 and len(t_session.calls) == 1
    clock.now += 2
    client.peers("NVDA"), client.metrics("NVDA"), t_client.daily_closes("NVDA", "2021-01-01")
    assert len(session.calls) == 4 and len(t_session.calls) == 2


def test_fetched_at_comes_from_the_cached_copy(tmp_path):
    clock = Clock()
    client, _, _ = finnhub({METRIC_URL: FakeResponse(200, METRIC)}, tmp_path, clock)
    client.metrics("NVDA")
    clock.now += HOUR
    assert client.metrics("NVDA")["fetched_at"] == 1_800_000_000.0


@pytest.mark.parametrize("failure", [FakeResponse(503), FakeResponse(429), requests.ConnectionError("down")])
def test_stale_copy_only_on_transient_failure(tmp_path, failure, caplog):
    clock = Clock()
    finnhub({METRIC_URL: FakeResponse(200, METRIC)}, tmp_path, clock)[0].metrics("NVDA")
    clock.now += 30 * HOUR
    client, _, _ = finnhub({METRIC_URL: failure}, tmp_path, clock)
    with caplog.at_level(logging.WARNING):
        assert client.metrics("NVDA")["pe_ttm"] == 25.2472
    assert "stale" in caplog.text


@pytest.mark.parametrize("failure, status", [
    (FakeResponse(401), 401), (FakeResponse(403), 403), (FakeResponse(404), 404), (FakeResponse(200, None), 200),
])
def test_non_transient_failure_never_serves_stale_copy(tmp_path, failure, status):
    clock = Clock()
    finnhub({METRIC_URL: FakeResponse(200, METRIC)}, tmp_path, clock)[0].metrics("NVDA")
    clock.now += 30 * HOUR
    client, _, _ = finnhub({METRIC_URL: failure}, tmp_path, clock)
    with pytest.raises(PriceError) as exc:
        client.metrics("NVDA")
    assert exc.value.status == status


def test_only_parsed_200_is_stored(tmp_path):
    client, _, _ = finnhub({QUOTE_URL: FakeResponse(404)}, tmp_path)
    with pytest.raises(PriceError):
        client.quote("NVDA")
    assert not (tmp_path / "cache").exists() or list((tmp_path / "cache").iterdir()) == []


def test_cache_disabled_by_default():
    client, session, _ = finnhub({QUOTE_URL: FakeResponse(200, QUOTE)})
    client.quote("NVDA")
    client.quote("NVDA")
    assert len(session.calls) == 2


def test_cache_entry_format_is_shared_with_sec_cache(tmp_path):
    clock = Clock()
    client, _, _ = finnhub({QUOTE_URL: FakeResponse(200, QUOTE)}, tmp_path, clock)
    client.quote("NVDA")
    (path,) = (tmp_path / "cache").iterdir()
    assert json.loads(path.read_text(encoding="utf-8")) == {"url": QUOTE_URL, "fetched_at": clock.now, "data": QUOTE}
