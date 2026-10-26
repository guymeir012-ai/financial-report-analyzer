"""Probe which Finnhub endpoints the configured key can use for P/E history.

Run: .venv/bin/python scripts/check_price_history.py [TICKER]
Needs FINNHUB_API_KEY in .env. The key is never printed.
"""
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

BASE = "https://finnhub.io/api/v1"


def probe(path: str, params: dict, key: str) -> tuple[int, dict | None]:
    resp = requests.get(
        f"{BASE}{path}",
        params=params,
        headers={"X-Finnhub-Token": key},
        timeout=15,
    )
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body


def main() -> int:
    load_dotenv()
    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key:
        print("FINNHUB_API_KEY is missing in .env; add a free key and rerun.")
        return 2

    ticker = (sys.argv[1] if len(sys.argv) > 1 else "NVDA").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,9}", ticker):
        print("Invalid ticker.")
        return 2

    now = int(time.time())
    five_years_ago = now - 5 * 365 * 24 * 3600

    status, body = probe(
        "/stock/candle",
        {"symbol": ticker, "resolution": "M", "from": five_years_ago, "to": now},
        key,
    )
    candles_ok = status == 200 and isinstance(body, dict) and body.get("s") == "ok"
    print(f"stock/candle (historical prices): HTTP {status}, available={candles_ok}")
    if not candles_ok and isinstance(body, dict) and "error" in body:
        print(f"  message: {body['error']}")

    status, body = probe("/quote", {"symbol": ticker}, key)
    quote_ok = status == 200 and isinstance(body, dict) and body.get("c")
    print(f"quote (current price): HTTP {status}, available={bool(quote_ok)}")

    status, body = probe("/stock/metric", {"symbol": ticker, "metric": "all"}, key)
    series = (body or {}).get("series", {}) if isinstance(body, dict) else {}
    pe_quarterly = series.get("quarterly", {}).get("pe", [])
    eps_quarterly = series.get("quarterly", {}).get("eps", [])
    print(
        f"stock/metric (basic financials): HTTP {status}, "
        f"quarterly P/E points={len(pe_quarterly)}, quarterly EPS points={len(eps_quarterly)}"
    )
    if pe_quarterly:
        oldest, newest = pe_quarterly[-1], pe_quarterly[0]
        print(f"  P/E history from {oldest.get('period')} to {newest.get('period')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
