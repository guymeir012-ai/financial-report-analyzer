"""Rate-limited, retrying HTTP client for SEC EDGAR (www.sec.gov / data.sec.gov)."""
import difflib
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

from backend.config import ConfigError

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
TICKER_MAP_TTL = 24 * 60 * 60
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")

__all__ = [
    "ConfigError", "SecClient", "SecError", "InvalidTicker", "TickerNotFound",
    "normalize_ticker", "resolve_ticker",
]


class SecError(Exception):
    """Upstream SEC failure. `message_he` is safe to show to users."""

    def __init__(self, message_he: str, status: int | None = None) -> None:
        super().__init__(message_he)
        self.message_he = message_he
        self.status = status


class InvalidTicker(ValueError):
    pass


class TickerNotFound(LookupError):
    def __init__(self, ticker: str, suggestions: list[dict[str, Any]]) -> None:
        super().__init__(ticker)
        self.ticker = ticker
        self.suggestions = suggestions


class SecClient:
    def __init__(
        self,
        user_agent: str,
        session: requests.Session | None = None,
        min_interval: float = 0.11,
        timeout: float = 15,
        max_retries: int = 3,
    ) -> None:
        if not user_agent or not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", user_agent):
            raise ConfigError("SEC User-Agent must contain a contact email")
        self._user_agent = user_agent
        self._session = session or requests.Session()
        self._min_interval = min_interval
        self._timeout = timeout
        self._max_retries = max_retries
        self._rate_lock = threading.Lock()
        self._next_allowed = 0.0
        self._cache_lock = threading.Lock()
        self._ticker_cache: tuple[float, dict[str, dict[str, Any]]] | None = None

    def _throttle(self) -> None:
        # Holding the lock while sleeping serializes callers, so requests stay spaced.
        with self._rate_lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now += wait
            self._next_allowed = now + self._min_interval

    def _get_json(self, url: str) -> Any:
        if urlparse(url).hostname not in ALLOWED_HOSTS or not url.startswith("https://"):
            raise SecError("כתובת לא מורשית.")
        headers = {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}
        last_status: int | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                time.sleep(min(0.5 * 2 ** (attempt - 1), 8))
            self._throttle()
            try:
                resp = self._session.get(
                    url, headers=headers, timeout=self._timeout, allow_redirects=False
                )
            except requests.RequestException:
                last_status = None
                continue
            last_status = resp.status_code
            if resp.status_code in RETRY_STATUSES:
                continue
            if resp.status_code == 404:
                raise SecError("המידע המבוקש לא נמצא ב-SEC.", 404)
            if resp.status_code != 200:
                raise SecError("SEC החזיר תשובה לא צפויה, נסו שוב מאוחר יותר.", resp.status_code)
            try:
                return resp.json()
            except ValueError:
                raise SecError("התקבלה תשובה לא תקינה מ-SEC.", 200) from None
        raise SecError("שירות SEC אינו זמין כרגע, נסו שוב בעוד רגע.", last_status)

    def ticker_map(self) -> dict[str, dict[str, Any]]:
        """TICKER -> {cik: int, title: str}, cached in memory for ~24h."""
        with self._cache_lock:
            cached = self._ticker_cache
            if cached and time.monotonic() - cached[0] < TICKER_MAP_TTL:
                return cached[1]
        raw = self._get_json(TICKERS_URL)
        try:
            mapping = {
                str(row["ticker"]).upper(): {"cik": int(row["cik_str"]), "title": str(row["title"])}
                for row in raw.values()
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            raise SecError("התקבלה תשובה לא תקינה מ-SEC.") from None
        with self._cache_lock:
            self._ticker_cache = (time.monotonic(), mapping)
        return mapping

    def submissions(self, cik: int) -> dict[str, Any]:
        return self._get_json(SUBMISSIONS_URL.format(cik=_valid_cik(cik)))

    def company_facts(self, cik: int) -> dict[str, Any]:
        return self._get_json(COMPANY_FACTS_URL.format(cik=_valid_cik(cik)))


def _valid_cik(cik: int) -> int:
    if isinstance(cik, bool) or not isinstance(cik, int) or not 0 < cik < 10**10:
        raise ValueError("invalid CIK")
    return cik


def normalize_ticker(value: str) -> str:
    ticker = (value or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        raise InvalidTicker("סימול המניה אינו תקין.")
    return ticker


def resolve_ticker(client: SecClient, ticker: str) -> tuple[int, str]:
    """Return (cik, company title) or raise TickerNotFound with up to 5 suggestions."""
    ticker = normalize_ticker(ticker)
    mapping = client.ticker_map()
    # SEC lists share classes with a dash (BRK-B); accept the dotted spelling too.
    for candidate in (ticker, ticker.replace(".", "-")):
        if candidate in mapping:
            entry = mapping[candidate]
            return entry["cik"], entry["title"]
    raise TickerNotFound(ticker, _suggest(mapping, ticker))


def _suggest(mapping: dict[str, dict[str, Any]], ticker: str, limit: int = 5) -> list[dict[str, Any]]:
    picked: list[str] = []

    def add(symbols: list[str]) -> None:
        for symbol in symbols:
            if symbol not in picked and len(picked) < limit:
                picked.append(symbol)

    add(difflib.get_close_matches(ticker, list(mapping), n=limit, cutoff=0.6))
    if len(picked) < limit:
        titles = {sym: info["title"].upper() for sym, info in mapping.items()}
        if len(ticker) >= 3:
            add([s for s, t in titles.items() if any(w.startswith(ticker) for w in t.split())][:limit])
        by_title: dict[str, list[str]] = {}
        for sym, title in titles.items():
            by_title.setdefault(title, []).append(sym)
        for title in difflib.get_close_matches(ticker, list(by_title), n=limit, cutoff=0.6):
            add(by_title[title][:1])
    return [{"ticker": s, "title": mapping[s]["title"]} for s in picked]
