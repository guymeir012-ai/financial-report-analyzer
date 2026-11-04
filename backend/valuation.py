"""Valuation computed in Python: trailing EPS and P/E from SEC facts and provider prices.

Every number carries its source. Provider numbers (Finnhub, Tiingo) are marked as such;
the company's own P/E is always computed here, never copied from a provider.
"""
import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any

from backend import filings as f
from backend.filings import Filing, FilingFacts, SourcedValue
from backend.prices import FinnhubClient, PriceError, TiingoClient
from backend.ratios import ITEMS_HE, TTM_PERIOD_HE, UNAVAILABLE_HE, make_result, ttm_net_income

log = logging.getLogger(__name__)

HISTORY_LIMIT = 20
PEERS_LIMIT = 8
LEADERS_COUNT = 3
MAX_PRICE_GAP_DAYS = 7
YEAR_GAP = (350, 380)
EPS_NOTE_HE = (
    "החלוקה היא במספר המניות בדילול של הרבעון האחרון, "
    "ולכן ייתכן הפרש קטן מול ספקים אחרים שמחברים רווח למניה רבעוני."
)
LOSS_HE = "הפסד ב-12 החודשים האחרונים"
CLOSING_HE = "מכפיל נמוך אומר שהשוק משלם פחות על כל דולר רווח. זולה לא אומר ראויה להשקעה."
LOSS_EXPLANATION_HE = "לחברה הפסד ב-12 החודשים האחרונים, ולכן אין מכפיל רווח להשוואה."
PE_FORMULA = "price / EPS TTM"
ITEMS_HE_EPS = "רווח למניה, 12 חודשים אחרונים"


# ---- sources for numbers that do not come from XBRL ----

def provider_source(name_he: str, provider: str, endpoint: str, value: float,
                    as_of: str, unit: str = "USD") -> dict[str, Any]:
    """A number taken from a price provider, with when it was valid."""
    return {"kind": "provider", "name_he": name_he, "provider": provider, "endpoint": endpoint,
            "value": value, "unit": unit, "as_of": as_of}


def _iso_utc(epoch: int | float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


# ---- EPS and P/E ----

def eps_ttm(ff: FilingFacts) -> dict[str, Any]:
    """Trailing-twelve-month EPS = TTM net income / diluted shares of the latest quarter (or year)."""
    numerator, found, ttm = ttm_net_income(ff, f.EPS_NUMERATOR_TAGS)
    shares = ff.diluted_shares()
    sh_name = shares.concept if shares else f.DILUTED_SHARES_TAGS[0]
    formula = f"{numerator} / {sh_name}"
    found["diluted_shares"] = shares
    inputs = [v for v in found.values() if v is not None]
    gone = [ITEMS_HE[n] for n, v in found.items() if v is None]
    if gone:
        return make_result("eps_ttm", "not_reported", formula, None, inputs, missing=gone,
                           period_he=TTM_PERIOD_HE, note_he=EPS_NOTE_HE)
    assert shares is not None and ttm is not None
    if shares.value <= 0:
        return make_result("eps_ttm", "not_computable", formula, None, inputs,
                           reason="מספר המניות אינו חיובי", period_he=TTM_PERIOD_HE, note_he=EPS_NOTE_HE)
    return make_result("eps_ttm", "ok", formula, None, inputs, value=ttm / shares.value,
                       period_he=TTM_PERIOD_HE, note_he=EPS_NOTE_HE)


def eps_annual(ff: FilingFacts) -> dict[str, Any]:
    """Annual EPS from a 10-K (net income / diluted shares), for cross-checking against the reported EPS."""
    net = ff.duration(f.EPS_NUMERATOR_TAGS)
    shares = ff.diluted_shares() if ff.filing.form == "10-K" else None
    formula = f"{net.concept if net else 'NetIncomeLoss'} / {shares.concept if shares else f.DILUTED_SHARES_TAGS[0]}"
    found = {"net_income": net, "diluted_shares": shares}
    inputs = [v for v in found.values() if v is not None]
    gone = [ITEMS_HE[n] for n, v in found.items() if v is None]
    if gone:
        return make_result("eps_annual", "not_reported", formula, None, inputs, missing=gone)
    assert net is not None and shares is not None
    if shares.value <= 0:
        return make_result("eps_annual", "not_computable", formula, None, inputs, reason="מספר המניות אינו חיובי")
    return make_result("eps_annual", "ok", formula, None, inputs, value=net.value / shares.value)


def pe_from(price: dict[str, Any] | None, eps: dict[str, Any]) -> dict[str, Any]:
    """P/E = price / EPS TTM. The price is a provider source; EPS is the computed result."""
    if price is None:
        return make_result("pe_ttm", "unavailable", PE_FORMULA, period_he=TTM_PERIOD_HE)
    res: dict[str, Any]
    if eps["status"] != "ok":
        res = make_result("pe_ttm", eps["status"], PE_FORMULA, period_he=TTM_PERIOD_HE,
                          missing=list(eps.get("missing") or []),
                          reason=eps["status_he"].split(": ", 1)[-1] if eps["status"] == "not_computable" else "")
    elif eps["value"] <= 0:
        res = make_result("pe_ttm", "not_computable", PE_FORMULA, reason=LOSS_HE, period_he=TTM_PERIOD_HE)
    else:
        res = make_result("pe_ttm", "ok", PE_FORMULA, value=price["value"] / eps["value"], period_he=TTM_PERIOD_HE)
    res["inputs"] = [price, {"kind": "derived", "name_he": ITEMS_HE_EPS, "value": eps.get("value"),
                             "status": eps["status"], "status_he": eps["status_he"], "unit": "USD"}]
    return res


# ---- history ----

def price_on_or_before(closes: list[tuple[str, float]], day: str,
                       max_gap_days: int = MAX_PRICE_GAP_DAYS) -> tuple[str, float] | None:
    """Last close on or before `day` (ISO), but no older than `max_gap_days`."""
    try:
        target = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    best: tuple[str, float] | None = None
    for close_day, close in closes:
        if close_day <= day and (best is None or close_day > best[0]):
            best = (close_day, close)
    if best is None or (target - date.fromisoformat(best[0])).days > max_gap_days:
        return None
    return best


def pe_history(facts_json: dict[str, Any], filings: list[Filing], closes: list[tuple[str, float]],
               closes_as_of: str, limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
    """One point per filing (newest first): price at period end / EPS TTM as reported then."""
    points = []
    for filing in filings[:limit]:
        eps = eps_ttm(FilingFacts(facts_json, filing))
        found = price_on_or_before(closes, filing.report_date)
        price = None
        if found:
            price = provider_source("מחיר סגירה", "Tiingo", "daily prices", found[1], found[0])
            price["as_of_fetch"] = closes_as_of
        pe = pe_from(price, eps)
        points.append({
            "report_date": filing.report_date, "form": filing.form, "accession": filing.accession,
            "filed": filing.filing_date, "url": filing.url,
            "eps": eps, "price": price, "pe": pe,
            "value": pe["value"] if pe["status"] == "ok" else None,
        })
    return points


def summarize_history(points: list[dict[str, Any]]) -> dict[str, Any]:
    values = [p["value"] for p in points if p.get("value") is not None and p["value"] > 0]
    return {
        "count": len(values),
        "median": statistics.median(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
        "from": points[-1]["report_date"] if points else None,
        "to": points[0]["report_date"] if points else None,
    }


def year_ago_point(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The point whose period ended about a year before the newest point."""
    if not points:
        return None
    try:
        newest = date.fromisoformat(points[0]["report_date"])
    except (TypeError, ValueError):
        return None
    for point in points[1:]:
        try:
            gap = (newest - date.fromisoformat(point["report_date"])).days
        except (TypeError, ValueError):
            continue
        if YEAR_GAP[0] <= gap <= YEAR_GAP[1]:
            return point
    return None


# ---- peers ----

def relative(value: float | None, benchmark: float | None) -> float | None:
    """value / benchmark - 1, positive = premium over the benchmark."""
    if value is None or benchmark is None or benchmark <= 0 or value <= 0:
        return None
    return value / benchmark - 1


def peers_valuation(finnhub: FinnhubClient, ticker: str, limit: int = PEERS_LIMIT,
                    max_workers: int = 4) -> dict[str, Any]:
    """Peers' trailing P/E and market cap as computed by Finnhub, with the median and the leaders."""
    found = finnhub.peers(ticker)
    symbols = found["peers"][:limit]
    if not symbols:
        return {"status": "empty", "peers": [], "count": 0, "median": None, "mean": None,
                "leaders": [], "fetched_at": _iso_utc(found["fetched_at"])}

    def one(symbol: str) -> dict[str, Any]:
        try:
            m = finnhub.metrics(symbol)
        except PriceError as exc:
            log.warning("Finnhub metrics unavailable for a peer (status=%s)", exc.status)
            return {"ticker": symbol, "status": "unavailable", "pe": None, "eps_ttm": None,
                    "market_cap": None, "as_of": None}
        pe = m["pe_ttm"] if m["pe_ttm"] is not None and m["pe_ttm"] > 0 else None
        if pe is not None:
            status = "ok"
        elif m["eps_ttm"] is not None and m["eps_ttm"] <= 0:
            status = "loss"
        else:
            status = "no_data"
        return {"ticker": symbol, "status": status, "pe": pe, "eps_ttm": m["eps_ttm"],
                "market_cap": m["market_cap"], "as_of": _iso_utc(m["fetched_at"])}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        entries = list(pool.map(one, symbols))
    values = [e["pe"] for e in entries if e["pe"] is not None]
    with_cap = [e for e in entries if e["market_cap"] is not None]
    leaders = [e["ticker"] for e in sorted(with_cap, key=lambda e: -e["market_cap"])[:LEADERS_COUNT]]
    status = "ok" if any(e["status"] != "unavailable" for e in entries) else "unavailable"
    return {
        "status": status, "peers": entries, "count": len(values),
        "median": statistics.median(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
        "leaders": leaders, "fetched_at": _iso_utc(found["fetched_at"]),
    }


# ---- explanation (plain Python, numbers only from the inputs above) ----

def _pct(value: float) -> str:
    return f"{abs(value) * 100:.0f}%"


def _x(value: float) -> str:
    return f"{value:.1f}"


def explain(current: dict[str, Any], history: dict[str, Any], peers: dict[str, Any]) -> list[str]:
    pe = current["pe"]
    if pe["status"] == "not_computable" and LOSS_HE in pe["status_he"]:
        return [LOSS_EXPLANATION_HE, CLOSING_HE]
    if pe["status"] != "ok":
        return [CLOSING_HE]
    value = pe["value"]
    out: list[str] = []
    summary = history.get("summary") or {}
    diff = relative(value, summary.get("median"))
    if diff is not None:
        if abs(diff) < 0.02:
            out.append(f"מכפיל הרווח היום הוא {_x(value)}, קרוב לחציון של {summary['count']} "
                       f"הרבעונים האחרונים ({_x(summary['median'])}).")
        else:
            word = "גבוה" if diff > 0 else "נמוך"
            out.append(f"מכפיל הרווח היום הוא {_x(value)}, {word} ב-{_pct(diff)} מהחציון של "
                       f"{summary['count']} הרבעונים האחרונים ({_x(summary['median'])}).")
    points = history.get("points") or []
    ago = year_ago_point(points)
    if points and ago and points[0].get("price") and ago.get("price"):
        eps_now, eps_ago = points[0]["eps"].get("value"), ago["eps"].get("value")
        price_now, price_ago = points[0]["price"]["value"], ago["price"]["value"]
        eps_change = relative(eps_now, eps_ago)
        price_change = relative(price_now, price_ago)
        if eps_change is not None and price_change is not None:
            eps_word = "עלה" if eps_change >= 0 else "ירד"
            price_word = "עלה" if price_change >= 0 else "ירד"
            out.append(f"בין {_he_date(ago['report_date'])} ל-{_he_date(points[0]['report_date'])} הרווח למניה "
                       f"{eps_word} ב-{_pct(eps_change)} והמחיר {price_word} ב-{_pct(price_change)}, "
                       f"וזה מה שמסביר את כיוון המכפיל.")
    if peers.get("status") == "ok" and peers.get("median"):
        diff = relative(value, peers["median"])
        if diff is not None:
            word = "פרמיה" if diff >= 0 else "הנחה"
            out.append(f"מול חציון המתחרות ({_x(peers['median'])}, {peers['count']} חברות) "
                       f"המניה נסחרת ב{word} של {_pct(diff)}.")
        by_ticker = {e["ticker"]: e for e in peers["peers"]}
        parts = []
        for leader in peers.get("leaders") or []:
            entry = by_ticker.get(leader)
            rel = relative(value, entry["pe"]) if entry else None
            if rel is not None:
                parts.append(f"{leader} {_x(entry['pe'])} ({'פרמיה' if rel >= 0 else 'הנחה'} {_pct(rel)})")
            elif entry and entry["status"] == "loss":
                parts.append(f"{leader} (הפסד, אין מכפיל)")
        if parts:
            out.append("מול המובילות בסקטור: " + ", ".join(parts) + ".")
    out.append(CLOSING_HE)
    return out


def _he_date(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return value or ""


# ---- orchestration ----

def _unavailable_block(**extra: Any) -> dict[str, Any]:
    return {"status": "unavailable", "status_he": UNAVAILABLE_HE, **extra}


def build_valuation(loaded: dict[str, Any], finnhub: FinnhubClient | None,
                    tiingo: TiingoClient | None, history_limit: int = HISTORY_LIMIT) -> dict[str, Any]:
    """Valuation of the company in `loaded` (from `ratios.load_company`); every part fails alone."""
    ticker, facts_json = loaded["ticker"], loaded["facts_json"]
    chain = f.filings_with_facts(facts_json, loaded["periodic"])
    latest = chain[0] if chain else None

    # current
    eps = eps_ttm(FilingFacts(facts_json, latest)) if latest else make_result(
        "eps_ttm", "not_reported", PE_FORMULA, missing=[ITEMS_HE["net_income"]], period_he=TTM_PERIOD_HE)
    price = None
    cross_check = None
    current_status = "ok"
    if finnhub is None:
        current_status = "unavailable"
    else:
        try:
            quote = finnhub.quote(ticker)
        except PriceError as exc:
            log.warning("Finnhub quote unavailable (status=%s)", exc.status)
            quote = None
            current_status = "unavailable"
        if quote:
            price = provider_source(ITEMS_HE["price"], "Finnhub", "quote", quote["price"],
                                    _iso_utc(quote["timestamp"]) or _iso_utc(quote["fetched_at"]))
            price["timestamp"] = quote["timestamp"]
        elif current_status == "ok":
            current_status = "unavailable"
        try:
            m = finnhub.metrics(ticker)
            cross_check = {"provider": "Finnhub", "pe_ttm": m["pe_ttm"], "eps_ttm": m["eps_ttm"],
                           "market_cap": m["market_cap"], "as_of": _iso_utc(m["fetched_at"])}
        except PriceError as exc:
            log.warning("Finnhub metrics unavailable (status=%s)", exc.status)
    current = {
        "status": current_status, "status_he": UNAVAILABLE_HE if current_status != "ok" else "",
        "filing": latest.to_dict() if latest else None,
        "eps": eps, "price": price, "pe": pe_from(price, eps), "cross_check": cross_check,
    }

    # history
    if tiingo is None or not chain:
        history: dict[str, Any] = _unavailable_block(points=[], summary=summarize_history([]))
    else:
        window = chain[:history_limit]
        start = min(x.report_date for x in window if x.report_date) if any(x.report_date for x in window) else None
        try:
            if start is None:
                raise PriceError()
            closes = tiingo.daily_closes(ticker, start)
            points = pe_history(facts_json, window, closes["closes"], _iso_utc(closes["fetched_at"]) or "",
                                history_limit)
            history = {"status": "ok", "status_he": "", "points": points, "summary": summarize_history(points)}
        except PriceError as exc:
            log.warning("Tiingo history unavailable (status=%s)", exc.status)
            history = _unavailable_block(points=[], summary=summarize_history([]))

    # peers
    if finnhub is None:
        peers: dict[str, Any] = _unavailable_block(peers=[], count=0, median=None, mean=None, leaders=[])
    else:
        try:
            peers = peers_valuation(finnhub, ticker)
        except PriceError as exc:
            log.warning("Finnhub peers unavailable (status=%s)", exc.status)
            peers = _unavailable_block(peers=[], count=0, median=None, mean=None, leaders=[])

    return {
        "ticker": ticker, "company": loaded["company"], "sic": loaded["sic"],
        "current": current, "history": history, "peers": peers,
        "explanation_he": explain(current, history, peers),
    }
