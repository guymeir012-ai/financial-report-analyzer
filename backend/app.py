"""Flask app: Hebrew HTML pages (home, confirm, summary) and a JSON API under /api/."""
import logging
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from backend import filings as f
from backend.config import (
    ConfigError, get_finnhub_key, get_price_cache_dir, get_sec_cache_dir, get_sec_user_agent,
    get_tiingo_key,
)
from backend.prices import FinnhubClient, TiingoClient
from backend.ratios import analyze, load_company
from backend.sec_client import (
    InvalidTicker, SecClient, SecError, TickerNotFound,
)
from backend.valuation import build_valuation
from backend.views import build_summary, build_valuation_view, filing_header

log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
EXAMPLE_TICKERS = ("NVDA", "AAPL", "MSFT")

HTTP_ERRORS_HE = {
    404: "הכתובת המבוקשת לא קיימת.",
    405: "השיטה המבוקשת אינה נתמכת בכתובת זו.",
}
GENERIC_ERROR_HE = "אירעה שגיאה בשרת. נסו שוב מאוחר יותר."
INVALID_TICKER_HE = "סימול המניה אינו תקין. יש להזין סימול באותיות לועזיות ובספרות בלבד."
EMPTY_TICKER_HE = "יש להזין סימול."
NOT_FOUND_TICKER_HE = "לא נמצאה חברה עם הסימול הזה."
NO_FILINGS_HE = "לא נמצאו דוחות תקופתיים (10-Q או 10-K) לחברה הזו."
UPSTREAM_HE = "לא ניתן לקבל נתונים מ-SEC כרגע. נסו שוב בעוד מספר דקות."
UPSTREAM_VALUATION_HE = "נתוני התמחור אינם זמינים כרגע."
PAGE_TITLES_HE = {
    404: "הדף לא נמצא",
    405: "הפעולה אינה נתמכת",
    502: "לא זמין כרגע",
}

# Scripts are off entirely: the pages use no JavaScript.
CSP = (
    "default-src 'self'; style-src 'self' fonts.googleapis.com; font-src fonts.gstatic.com; "
    "script-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


_UNSET: Any = object()


def create_app(client: SecClient | None = None, finnhub: Any = _UNSET, tiingo: Any = _UNSET) -> Flask:
    """`finnhub` / `tiingo`: a client, None (not configured), or unset = built from .env on first use."""
    app = Flask(
        __name__,
        template_folder=str(FRONTEND_DIR / "templates"),
        static_folder=str(FRONTEND_DIR / "static"),
    )
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True
    holder: dict[str, Any] = {}
    lock = threading.Lock()

    def get_client() -> SecClient:
        if client is not None:
            return client
        with lock:
            if "client" not in holder:
                holder["client"] = SecClient(get_sec_user_agent(), cache_dir=get_sec_cache_dir())
            return holder["client"]

    def get_price_clients() -> tuple[Any, Any]:
        with lock:
            if "finnhub" not in holder:
                if finnhub is not _UNSET:
                    holder["finnhub"] = finnhub
                else:
                    key = get_finnhub_key()
                    holder["finnhub"] = FinnhubClient(key, cache_dir=get_price_cache_dir()) if key else None
                    if not key:
                        log.warning("FINNHUB_API_KEY is not set; valuation will be shown as unavailable")
                if tiingo is not _UNSET:
                    holder["tiingo"] = tiingo
                else:
                    key = get_tiingo_key()
                    holder["tiingo"] = TiingoClient(key, cache_dir=get_price_cache_dir()) if key else None
                    if not key:
                        log.warning("TIINGO_API_KEY is not set; P/E history will be shown as unavailable")
            return holder["finnhub"], holder["tiingo"]

    def valuation_for(loaded: dict[str, Any]) -> dict[str, Any] | None:
        """Never lets a valuation problem take the ratios page down."""
        try:
            fh, ti = get_price_clients()
            return build_valuation(loaded, fh, ti)
        except Exception:  # noqa: BLE001 - logged, the section shows "unavailable"
            log.exception("Valuation failed")
            return None

    def is_api() -> bool:
        return request.path.startswith("/api/")

    def error(message: str, status: int, **extra: Any):
        return jsonify({"error": message, **extra}), status

    def home_page(status: int = 200, message: str | None = None, ticker: str = "",
                  suggestions: list[dict[str, Any]] | None = None):
        html = render_template(
            "home.html", message=message, ticker=ticker, suggestions=suggestions or [],
            examples=EXAMPLE_TICKERS,
        )
        return html, status

    def error_page(status: int, message: str, **extra: Any):
        title = PAGE_TITLES_HE.get(status, "אירעה שגיאה")
        return render_template("error.html", status=status, title=title, message=message, **extra), status

    @app.after_request
    def _security_headers(resp):
        for name, value in SECURITY_HEADERS.items():
            resp.headers[name] = value
        return resp

    # ---- pages ----

    @app.get("/")
    def home():
        return home_page()

    @app.get("/confirm")
    def confirm():
        raw = request.args.get("ticker", "")
        if not raw.strip():
            return home_page(400, EMPTY_TICKER_HE)
        symbol, cik, title, periodic, _ = f.find_company_filings(get_client(), raw)
        latest, _previous = f.latest_and_previous(periodic)
        return render_template(
            "confirm.html", company=title, ticker=symbol, cik=cik, filing=filing_header(latest.to_dict()),
        )

    @app.get("/summary/<ticker>")
    def summary(ticker: str):
        loaded = load_company(ticker, get_client())
        analysis = analyze(ticker, get_client(), loaded)
        s = build_summary(analysis)
        v = build_valuation_view(valuation_for(loaded), analysis["latest"]["filing"].get("report_date", ""))
        return render_template("summary.html", s=s, v=v)

    # ---- JSON API ----

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/filings/<ticker>")
    def filings(ticker: str):
        symbol, cik, title, periodic, _ = f.find_company_filings(get_client(), ticker)
        latest, previous = f.latest_and_previous(periodic)
        return jsonify({
            "company": title,
            "cik": cik,
            "ticker": symbol,
            "latest": latest.to_dict() if latest else None,
            "previous": previous.to_dict() if previous else None,
        })

    @app.get("/api/ratios/<ticker>")
    def ratios(ticker: str):
        return jsonify(analyze(ticker, get_client()))

    @app.get("/api/valuation/<ticker>")
    def valuation(ticker: str):
        loaded = load_company(ticker, get_client())
        result = valuation_for(loaded)
        if result is None:
            return error(UPSTREAM_VALUATION_HE, 502)
        return jsonify(result)

    # ---- errors: JSON under /api/, Hebrew HTML elsewhere (never internals) ----

    @app.errorhandler(InvalidTicker)
    def _invalid(exc: InvalidTicker):
        if is_api():
            return error(INVALID_TICKER_HE, 400)
        return home_page(400, INVALID_TICKER_HE)

    @app.errorhandler(TickerNotFound)
    def _not_found(exc: TickerNotFound):
        if is_api():
            return error(NOT_FOUND_TICKER_HE, 404, suggestions=exc.suggestions)
        return home_page(404, NOT_FOUND_TICKER_HE, ticker=exc.ticker, suggestions=exc.suggestions)

    @app.errorhandler(f.NoPeriodicFilings)
    def _no_filings(exc: f.NoPeriodicFilings):
        if is_api():
            return error(NO_FILINGS_HE, 404)
        return error_page(404, NO_FILINGS_HE, company=exc.company, ticker=exc.ticker)

    @app.errorhandler(SecError)
    def _upstream(exc: SecError):
        log.warning("SEC request failed (status=%s)", exc.status)
        if is_api():
            return error(UPSTREAM_HE, 502)
        return error_page(502, UPSTREAM_HE)

    @app.errorhandler(ConfigError)
    def _config(exc: ConfigError):
        log.error("Configuration error: %s", exc)
        if is_api():
            return error(GENERIC_ERROR_HE, 500)
        return error_page(500, GENERIC_ERROR_HE)

    @app.errorhandler(HTTPException)
    def _http(exc: HTTPException):
        status = exc.code or 500
        message = HTTP_ERRORS_HE.get(status, GENERIC_ERROR_HE)
        if is_api():
            return error(message, status)
        return error_page(status, message)

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        log.exception("Unhandled error")
        if is_api():
            return error(GENERIC_ERROR_HE, 500)
        return error_page(500, GENERIC_ERROR_HE)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
