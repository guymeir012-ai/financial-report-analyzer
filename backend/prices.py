"""Price data clients: Finnhub (quote, peers, basic financials) and Tiingo (end-of-day history).

API keys travel only in request headers, so they never reach URLs, cache keys or logs.
Every answer may be served from the on-disk cache (`backend.cache`); a stale copy is used
only when the provider fails transiently, exactly like the SEC client.
"""
import logging
import math
import time
from datetime import date
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

import requests

from backend.cache import JsonDiskCache
from backend.config import ConfigError
from backend.sec_client import normalize_ticker

FINNHUB_BASE = "https://finnhub.io/api/v1"
TIINGO_BASE = "https://api.tiingo.com"
ALLOWED_HOSTS = frozenset({"finnhub.io", "api.tiingo.com"})
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_BODY_BYTES = 5 * 1024 * 1024
TTL_QUOTE = 15 * 60
TTL_DAY = 24 * 60 * 60
UNAVAILABLE_HE = "לא זמין כרגע"

log = logging.getLogger(__name__)


class PriceError(Exception):
    """Provider failure. `message_he` is safe to show; `status` is the HTTP status (None = network)."""

    def __init__(self, message_he: str = UNAVAILABLE_HE, status: int | None = None) -> None:
        super().__init__(message_he)
        self.message_he = message_he
        self.status = status


def _number(value: Any) -> float | None:
    """A finite number, or None for anything else (bool, str, NaN, missing)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


class _JsonProvider:
    name = "provider"

    def __init__(
        self,
        api_key: str | None,
        session: requests.Session | None = None,
        cache_dir: Any = None,
        timeout: float = 10,
        wall_clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retry_sleep: float = 1.0,
        base_url: str | None = None,
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise ConfigError(f"{self.name} API key is not set")
        self._key = key
        self._session = session or requests.Session()
        self._timeout = timeout
        self._sleep = sleep
        self._retry_sleep = retry_sleep
        self._base = (base_url or self.default_base).rstrip("/")
        self._cache = JsonDiskCache(cache_dir, wall_clock) if cache_dir else None
        self._wall_clock = wall_clock or time.time

    default_base = ""

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _get(self, path: str, params: dict[str, str], ttl: int) -> tuple[Any, float]:
        """(data, fetched_at) for GET base+path?params, honoring the cache and its TTL."""
        url = f"{self._base}{path}?{urlencode(sorted(params.items()))}"
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise PriceError(UNAVAILABLE_HE)
        cached = self._cache.read(url) if self._cache else None
        if cached and 0 <= self._wall_clock() - cached[0] < ttl:
            return cached[1], cached[0]
        try:
            data = self._fetch(url)
        except PriceError as exc:
            if cached and (exc.status is None or exc.status in TRANSIENT_STATUSES):
                log.warning("%s unavailable (status=%s), serving stale cached copy", self.name, exc.status)
                return cached[1], cached[0]
            raise
        if self._cache:
            self._cache.write(url, data)
        return data, self._wall_clock()

    def _fetch(self, url: str) -> Any:
        last_status: int | None = None
        for attempt in range(2):
            if attempt:
                self._sleep(self._retry_sleep)
            try:
                resp = self._session.get(
                    url, headers=self._headers(), timeout=self._timeout, allow_redirects=False,
                )
            except requests.RequestException:
                last_status = None
                continue
            status = resp.status_code
            last_status = status
            if status in TRANSIENT_STATUSES:
                continue
            if status == 401:
                log.warning("%s rejected the API key (401)", self.name)
                raise PriceError(UNAVAILABLE_HE, 401)
            if status != 200:
                raise PriceError(UNAVAILABLE_HE, status)
            if len(resp.content or b"") > MAX_BODY_BYTES:
                log.warning("%s answer too large, ignored", self.name)
                raise PriceError(UNAVAILABLE_HE, 200)
            try:
                return resp.json()
            except ValueError:
                raise PriceError(UNAVAILABLE_HE, 200) from None
        raise PriceError(UNAVAILABLE_HE, last_status)


class FinnhubClient(_JsonProvider):
    name = "Finnhub"
    default_base = FINNHUB_BASE

    def _headers(self) -> dict[str, str]:
        return {"X-Finnhub-Token": self._key, "Accept": "application/json"}

    def quote(self, symbol: str) -> dict[str, Any] | None:
        """Last price with its epoch timestamp, or None when Finnhub has no price (unknown symbol)."""
        symbol = normalize_ticker(symbol)
        data, fetched_at = self._get("/quote", {"symbol": symbol}, TTL_QUOTE)
        if not isinstance(data, dict):
            return None
        price = _number(data.get("c"))
        if price is None or price <= 0:
            return None
        timestamp = _number(data.get("t"))
        return {
            "price": price,
            "previous_close": _number(data.get("pc")),
            "timestamp": int(timestamp) if timestamp and timestamp > 0 else None,
            "fetched_at": fetched_at,
        }

    def peers(self, symbol: str) -> dict[str, Any]:
        """Peer symbols (validated, deduplicated, without the company itself), in Finnhub's order."""
        symbol = normalize_ticker(symbol)
        data, fetched_at = self._get("/stock/peers", {"symbol": symbol}, TTL_DAY)
        peers: list[str] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, str):
                continue
            try:
                candidate = normalize_ticker(item)
            except ValueError:
                continue
            if candidate != symbol and candidate not in peers:
                peers.append(candidate)
        return {"peers": peers, "fetched_at": fetched_at}

    def metrics(self, symbol: str) -> dict[str, Any]:
        """Finnhub's own trailing P/E, trailing EPS and market cap (USD millions); None when absent."""
        symbol = normalize_ticker(symbol)
        data, fetched_at = self._get("/stock/metric", {"symbol": symbol, "metric": "all"}, TTL_DAY)
        metric = data.get("metric") if isinstance(data, dict) else None
        metric = metric if isinstance(metric, dict) else {}
        return {
            "pe_ttm": _number(metric.get("peTTM")),
            "eps_ttm": _number(metric.get("epsTTM")),
            "market_cap": _number(metric.get("marketCapitalization")),
            "fetched_at": fetched_at,
        }


class TiingoClient(_JsonProvider):
    name = "Tiingo"
    default_base = TIINGO_BASE

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._key}", "Accept": "application/json"}

    def daily_closes(self, symbol: str, start: str) -> dict[str, Any]:
        """Unadjusted end-of-day closes from `start` (ISO date) to today, oldest first."""
        symbol = normalize_ticker(symbol)
        start = date.fromisoformat(start).isoformat()  # strict YYYY-MM-DD, nothing else reaches the URL
        if len(start) != 10:
            raise ValueError("start must be YYYY-MM-DD")
        data, fetched_at = self._get(
            f"/tiingo/daily/{symbol}/prices", {"startDate": start, "columns": "date,close"}, TTL_DAY,
        )
        closes: list[tuple[str, float]] = []
        for row in data if isinstance(data, list) else []:
            if not isinstance(row, dict):
                continue
            day, close = row.get("date"), _number(row.get("close"))
            if not isinstance(day, str) or close is None:
                continue
            try:
                day = date.fromisoformat(day[:10]).isoformat()
            except ValueError:
                continue
            closes.append((day, close))
        closes.sort()
        return {"closes": closes, "fetched_at": fetched_at}
