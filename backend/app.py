"""Flask JSON API: health, periodic filings and ratios for a US ticker."""
import logging
import threading
from typing import Any

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from backend import filings as f
from backend.config import ConfigError, get_sec_user_agent
from backend.ratios import analyze
from backend.sec_client import InvalidTicker, SecClient, SecError, TickerNotFound

log = logging.getLogger(__name__)

HTTP_ERRORS_HE = {
    404: "הכתובת המבוקשת לא קיימת.",
    405: "השיטה המבוקשת אינה נתמכת בכתובת זו.",
}
GENERIC_ERROR_HE = "אירעה שגיאה בשרת. נסו שוב מאוחר יותר."


def create_app(client: SecClient | None = None) -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    holder: dict[str, SecClient] = {}
    lock = threading.Lock()

    def get_client() -> SecClient:
        if client is not None:
            return client
        with lock:
            if "client" not in holder:
                holder["client"] = SecClient(get_sec_user_agent())
            return holder["client"]

    def error(message: str, status: int, **extra: Any):
        return jsonify({"error": message, **extra}), status

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/filings/<ticker>")
    def filings(ticker: str):
        symbol, cik, title, periodic = f.find_company_filings(get_client(), ticker)
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

    @app.errorhandler(InvalidTicker)
    def _invalid(exc: InvalidTicker):
        return error("סימול המניה אינו תקין. יש להזין סימול באותיות לועזיות ובספרות בלבד.", 400)

    @app.errorhandler(TickerNotFound)
    def _not_found(exc: TickerNotFound):
        return error("לא נמצאה חברה עם הסימול הזה.", 404, suggestions=exc.suggestions)

    @app.errorhandler(f.NoPeriodicFilings)
    def _no_filings(exc: f.NoPeriodicFilings):
        return error("לא נמצאו דוחות תקופתיים (10-Q או 10-K) לחברה הזו.", 404)

    @app.errorhandler(SecError)
    def _upstream(exc: SecError):
        log.warning("SEC request failed (status=%s)", exc.status)
        return error("לא ניתן לקבל נתונים מ-SEC כרגע. נסו שוב בעוד מספר דקות.", 502)

    @app.errorhandler(ConfigError)
    def _config(exc: ConfigError):
        log.error("Configuration error: %s", exc)
        return error(GENERIC_ERROR_HE, 500)

    @app.errorhandler(HTTPException)
    def _http(exc: HTTPException):
        status = exc.code or 500
        return error(HTTP_ERRORS_HE.get(status, GENERIC_ERROR_HE), status)

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        log.exception("Unhandled error")
        return error(GENERIC_ERROR_HE, 500)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
