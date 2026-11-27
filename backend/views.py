"""Pure view-model helpers: turn `analyze()` output into what the templates display."""
import math
import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from backend import filings as f
from backend.niv_method import ROLE_PERIOD_HE
from backend.ratios import ITEMS_HE, UNAVAILABLE_HE
from backend.valuation import relative

PERCENT_KEYS = frozenset({
    "revenue_growth_yoy", "revenue_growth_qoq", "gross_margin", "operating_margin",
    "net_margin", "roe", "equity_ratio",
})
MULTIPLE_KEYS = frozenset({"current_ratio", "debt_to_equity", "pe_ttm"})
ONE_DECIMAL_KEYS = frozenset({"pe_ttm"})
DOLLAR_KEYS = frozenset({"eps_ttm", "eps_annual"})
LOWER_IS_BETTER = frozenset({"debt_to_equity"})
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
ISRAEL_CLOCK_HE = "שעון ישראל"
UNITS_HE = {"USD": "מיליוני דולר", "shares": "מיליוני מניות", "USD/shares": "דולר למניה"}
HISTORY_CHART = {"width": 640, "height": 170, "top": 14, "bottom": 30, "side": 20}

SEC_PREFIX = "https://www.sec.gov/"
NO_PREVIOUS_HE = "אין דוח קודם"
NO_CHANGE = "—"

_ARROWS = {"up": "▲", "down": "▼", "flat": ""}
_TONE_HE = {"good": "לטובה", "bad": "לרעה", "neutral": "ללא שינוי"}

# Hebrew names for XBRL tags shown in the source panel (items that need context are handled below).
TAG_NAMES_HE: dict[str, str] = {
    **{t: ITEMS_HE["revenue"] for t in f.REVENUE_TAGS},
    **{t: ITEMS_HE["gross_profit"] for t in f.GROSS_PROFIT_TAGS},
    **{t: "עלות ההכנסות" for t in f.COST_TAGS},
    **{t: ITEMS_HE["operating_income"] for t in f.OPERATING_INCOME_TAGS},
    "OperatingExpenses": "הוצאות תפעול",
    **{t: ITEMS_HE["net_income"] for t in f.NET_INCOME_TAGS},
    **{t: ITEMS_HE["equity"] for t in f.EQUITY_TAGS},
    **{t: ITEMS_HE["assets"] for t in f.ASSETS_TAGS},
    **{t: ITEMS_HE["current_assets"] for t in f.CURRENT_ASSETS_TAGS},
    **{t: ITEMS_HE["current_liabilities"] for t in f.CURRENT_LIABILITIES_TAGS},
    **{t: "אשראי לזמן קצר" for t in f.SHORT_TERM_BORROWINGS_TAGS},
    **{t: "הלוואות וערבות לתשלום" for t in f.NOTES_AND_LOANS_TAGS},
    **{t: "חוב לטווח ארוך" for t in f.DEBT_TOTAL_TAGS},
    **{t: "חוב לטווח ארוך, חלק לא שוטף" for t in f.DEBT_NONCURRENT_TAGS},
    **{t: "חוב, חלק שוטף" for t in f.DEBT_CURRENT_TAGS},
    **{t: "חוב והתחייבויות חכירה, כולל חלק שוטף" for t in f.DEBT_LEASE_INCL_CURRENT_TAGS},
    **{t: "חוב והתחייבויות חכירה, חלק לא שוטף" for t in f.DEBT_LEASE_NONCURRENT_TAGS},
    **{t: "חוב והתחייבויות חכירה, חלק שוטף" for t in f.DEBT_LEASE_CURRENT_TAGS},
    **{t: ITEMS_HE["diluted_shares"] for t in f.DILUTED_SHARES_TAGS},
    **{t: "רווח למניה בדילול, כפי שדווח" for t in f.EPS_DILUTED_TAGS},
    "NetIncomeLossAvailableToCommonStockholdersDiluted": "רווח נקי לבעלי המניות הרגילות (בדילול)",
    "NetIncomeLossAvailableToCommonStockholdersBasic": "רווח נקי לבעלי המניות הרגילות",
}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def format_value(key: str, value: float | None) -> str:
    """Percent with one decimal, dollars with two, or a multiple with an x (one decimal for P/E)."""
    if not _finite(value):
        return NO_CHANGE
    if key in PERCENT_KEYS:
        text = f"{value * 100:,.1f}"
        return ("0.0" if float(text.replace(",", "")) == 0 else text) + "%"
    if key in DOLLAR_KEYS:
        text = f"{abs(value):,.2f}"
        return ("-" if value < 0 and float(text.replace(",", "")) != 0 else "") + "$" + text
    places = 1 if key in ONE_DECIMAL_KEYS else 2
    text = f"{value:,.{places}f}"
    return (f"{0:.{places}f}" if float(text.replace(",", "")) == 0 else text) + "x"


def format_price(value: float | None) -> str:
    return f"{value:,.2f}" if _finite(value) else NO_CHANGE


def format_signed_percent(value: float | None) -> str:
    """+38% / -12% / 0% for a relative difference; no color, only the sign."""
    if not _finite(value):
        return NO_CHANGE
    rounded = round(value * 100)
    return f"{rounded:+d}%" if rounded else "0%"


def format_israel_time(iso_utc: str | None) -> str:
    """ISO timestamp (any zone) to DD.MM.YYYY HH:MM in Israel's clock."""
    if not iso_utc:
        return ""
    try:
        moment = datetime.fromisoformat(iso_utc)
    except ValueError:
        return iso_utc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ISRAEL_TZ).strftime("%d.%m.%Y %H:%M")


def format_as_of(as_of: str | None) -> tuple[str, str]:
    """(text, label): a plain date stays a date; a timestamp becomes Israel time with its label."""
    if not as_of:
        return "", ""
    if len(as_of) == 10:
        return format_date(as_of), ""
    return format_israel_time(as_of), ISRAEL_CLOCK_HE


def compute_change(key: str, current: float | None, previous: float | None) -> dict[str, Any]:
    """Change between two periods: pp for percentages, a plain difference for multiples.

    `direction` follows the sign of the number; `tone` says whether it is favorable.
    """
    if not _finite(current) or not _finite(previous):
        return {"text": NO_CHANGE, "direction": None, "tone": None, "arrow": "", "tone_he": ""}
    if key in MULTIPLE_KEYS:
        delta, places, unit = current - previous, 2, "x"
    else:
        delta, places, unit = (current - previous) * 100, 1, " pp"
    rounded = round(delta, places)
    if rounded == 0:
        direction, tone, text = "flat", "neutral", f"{0:.{places}f}{unit}"
    else:
        direction = "up" if rounded > 0 else "down"
        better = (direction == "down") if key in LOWER_IS_BETTER else (direction == "up")
        tone = "good" if better else "bad"
        text = f"{rounded:+,.{places}f}{unit}"
    return {"text": text, "direction": direction, "tone": tone,
            "arrow": _ARROWS[direction], "tone_he": _TONE_HE[tone]}


def format_millions(value: float) -> str:
    """USD amount shown in millions with thousands separators."""
    millions = value / 1_000_000
    text = f"{millions:,.0f}" if abs(millions) >= 1000 else f"{millions:,.1f}"
    return "0.0" if float(text.replace(",", "")) == 0 else text


def format_date(value: str | None) -> str:
    """ISO date to DD.MM.YYYY; anything unparsable is returned as is."""
    if not value:
        return ""
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


def safe_sec_url(url: Any) -> str | None:
    """Only links to www.sec.gov are rendered as links."""
    if isinstance(url, str) and url.startswith(SEC_PREFIX) and not re.search(r"[\s<>\"']", url):
        return url
    return None


def isolate_forms(text: str | None) -> str | None:
    """Keep form names such as 10-Q left-to-right inside Hebrew sentences."""
    if not text:
        return text
    return re.sub(r"\b(10-[QK])\b", "\u2066\\1\u2069", text)


def split_name(name_he: str) -> tuple[str, str]:
    """'שם בעברית (English Name)' -> ('שם בעברית', 'English Name')."""
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", name_he)
    return (match.group(1), match.group(2)) if match else (name_he, "")


def _component_name(key: str, inp: dict[str, Any], report_date: str) -> str:
    tag = inp.get("concept", "")
    if tag in f.REVENUE_TAGS and inp.get("end") != report_date:
        if key == "revenue_growth_qoq":
            return ITEMS_HE["revenue_previous_filing"]
        if key == "revenue_growth_yoy":
            return ITEMS_HE["revenue_prior"]
    if key == "roe" and tag in f.NET_INCOME_TAGS:
        start, end = inp.get("start"), inp.get("end")
        try:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        except (TypeError, ValueError):
            days = 0
        if days >= f.PERIOD_BANDS["10-K"][0]:
            return ITEMS_HE["annual_net_income"]
        return ITEMS_HE["ytd_net_income" if end == report_date else "prior_ytd_net_income"]
    return TAG_NAMES_HE.get(tag, tag)


def _period_text(inp: dict[str, Any]) -> dict[str, str]:
    start, end = inp.get("start"), inp.get("end")
    return {"start": format_date(start) if start else "", "end": format_date(end)}


def _xbrl_value(inp: dict[str, Any]) -> tuple[str, str]:
    value, unit = inp.get("value"), inp.get("unit") or "USD"
    if not _finite(value):
        return NO_CHANGE, UNITS_HE.get(unit, unit)
    if unit == "USD/shares":
        return f"{value:,.2f}", UNITS_HE[unit]
    return format_millions(value), UNITS_HE.get(unit, unit)


def _component(key: str, inp: dict[str, Any], report_date: str) -> dict[str, Any]:
    kind = inp.get("kind") or "xbrl"
    if kind == "provider":
        as_of, label = format_as_of(inp.get("as_of"))
        return {
            "kind": "provider", "name_he": inp.get("name_he", ""), "provider": inp.get("provider", ""),
            "endpoint": inp.get("endpoint", ""), "value": format_price(inp.get("value")),
            "unit_he": "דולר", "as_of": as_of, "as_of_label": label,
        }
    if kind == "derived":
        value = inp.get("value")
        return {
            "kind": "derived", "name_he": inp.get("name_he", ""),
            "value": format_value("eps_ttm", value) if _finite(value) else NO_CHANGE,
            "unit_he": "", "status_he": isolate_forms(inp.get("status_he", "")),
        }
    value, unit_he = _xbrl_value(inp)
    return {
        "kind": "xbrl",
        "name_he": _component_name(key, inp, report_date),
        "tag": inp.get("concept", ""),
        "value": value,
        "unit_he": unit_he,
        "period": _period_text(inp),
        "form": inp.get("form", ""),
        "accession": inp.get("accession", ""),
        "filed": format_date(inp.get("filed")),
        "url": safe_sec_url(inp.get("url")),
    }


def build_source(ratio: dict[str, Any], report_date: str) -> dict[str, Any] | None:
    """Content of the source panel, or None when there is nothing to show."""
    inputs, missing = ratio.get("inputs") or [], ratio.get("missing") or []
    if not inputs and not missing:
        return None
    return {
        "name_he": split_name(ratio["name_he"])[0],
        "formula": ratio.get("formula", ""),
        "status": ratio.get("status"),
        "status_he": isolate_forms(ratio.get("status_he", "")),
        "note_he": ratio.get("note_he"),
        "components": [_component(ratio["key"], inp, report_date) for inp in inputs],
        "missing": list(missing),
        "extra_he": [],
    }


def build_cell(key: str, ratio: dict[str, Any] | None, report_date: str) -> dict[str, Any]:
    """One table cell: displayed text plus an optional source panel."""
    if ratio is None:
        return {"kind": "none", "ok": False, "text": NO_CHANGE, "source": None}
    ok = ratio.get("status") == "ok" and _finite(ratio.get("value"))
    text = format_value(key, ratio["value"]) if ok else isolate_forms(ratio.get("status_he")) or NO_CHANGE
    return {"kind": "value" if ok else "status", "ok": ok, "text": text,
            "source": build_source(ratio, report_date)}


def filing_header(filing: dict[str, Any] | None) -> dict[str, Any] | None:
    if not filing:
        return None
    return {
        "form": filing.get("form", ""),
        "accession": filing.get("accession", ""),
        "report_date": format_date(filing.get("report_date")),
        "filing_date": format_date(filing.get("filing_date")),
        "url": safe_sec_url(filing.get("url")),
    }


def _by_key(block: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in (block or {}).get("ratios") or []}


def build_summary(analysis: dict[str, Any]) -> dict[str, Any]:
    """View model of the summary page from `analyze()` output."""
    latest_block, previous_block = analysis["latest"], analysis.get("previous")
    latest_filing, previous_filing = latest_block["filing"], (previous_block or {}).get("filing")
    latest_by_key, previous_by_key = _by_key(latest_block), _by_key(previous_block)
    rows = []
    for key, current in latest_by_key.items():
        prior = previous_by_key.get(key) if previous_block else None
        usable = (current.get("status") == "ok" and prior is not None
                  and prior.get("status") == "ok")
        name, name_en = split_name(current["name_he"])
        rows.append({
            "key": key,
            "name": name,
            "name_en": name_en,
            "note_he": isolate_forms(current.get("note_he")),
            "period_he": current.get("period_he"),
            "current": build_cell(key, current, latest_filing.get("report_date", "")),
            "previous": (build_cell(key, prior, previous_filing.get("report_date", ""))
                         if previous_block else
                         {"kind": "none", "ok": False, "text": NO_PREVIOUS_HE, "source": None}),
            "change": (compute_change(key, current["value"], prior["value"]) if usable
                       else compute_change(key, None, None)),
        })
    return {
        "company": analysis.get("company", ""),
        "ticker": analysis.get("ticker", ""),
        "cik": analysis.get("cik"),
        "is_bank": bool(analysis.get("is_bank")),
        "notice_he": analysis.get("notice_he"),
        "latest": filing_header(latest_filing),
        "previous": filing_header(previous_filing),
        "rows": rows,
    }


# ---- valuation ----

def _unavailable_cell() -> dict[str, Any]:
    return {"kind": "status", "ok": False, "text": UNAVAILABLE_HE, "source": None}


def _bar(label: str, sub: str, value: float | None, kind: str) -> dict[str, Any]:
    return {"label": label, "sub": sub, "value": value, "text": format_value("pe_ttm", value) if _finite(value) else sub or NO_CHANGE,
            "kind": kind, "width": 0.0}


def _scale_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top = max((b["value"] for b in bars if _finite(b["value"]) and b["value"] > 0), default=None)
    for b in bars:
        b["width"] = round(b["value"] / top * 100, 1) if top and _finite(b["value"]) and b["value"] > 0 else 0.0
    return bars


def _history_chart(points: list[dict[str, Any]], median: float | None) -> dict[str, Any] | None:
    """Geometry of the quarterly P/E column chart (oldest on the left), numbers only."""
    ordered = list(reversed(points))
    values = [p["value"] for p in ordered if _finite(p.get("value")) and p["value"] > 0]
    if not values:
        return None
    width, height = HISTORY_CHART["width"], HISTORY_CHART["height"]
    top, bottom, side = HISTORY_CHART["top"], HISTORY_CHART["bottom"], HISTORY_CHART["side"]
    span = max(values + ([median] if _finite(median) else []))
    plot_h = height - top - bottom
    inner = width - 2 * side
    slot = inner / len(ordered)
    bar_w = round(slot * 0.62, 1)

    def y_of(value: float) -> float:
        return round(top + plot_h - value / span * plot_h, 1)

    columns = []
    for i, p in enumerate(ordered):
        x = round(side + i * slot + (slot - bar_w) / 2, 1)
        value = p.get("value") if _finite(p.get("value")) and p["value"] > 0 else None
        label = ""
        try:
            label = date.fromisoformat(p["report_date"]).strftime("%m/%y")
        except (TypeError, ValueError):
            pass
        columns.append({
            "x": x, "w": bar_w, "y": y_of(value) if value else top + plot_h, "h": round(plot_h - (y_of(value) - top), 1) if value else 0,
            "label": label, "label_x": round(x + bar_w / 2, 1), "text": format_value("pe_ttm", value) if value else NO_CHANGE,
            "missing": value is None,
        })
    return {
        "width": width, "height": height, "columns": columns, "baseline_y": top + plot_h,
        "label_y": height - 10, "median_y": y_of(median) if _finite(median) else None,
        "median_text": format_value("pe_ttm", median) if _finite(median) else "",
        "show_labels": len(ordered) <= 12,
    }


def build_valuation_view(val: dict[str, Any] | None, report_date: str) -> dict[str, Any]:
    """View model of the valuation section; `val` is `build_valuation()` output or None."""
    if val is None:
        return {"status": "unavailable", "status_he": UNAVAILABLE_HE, "price": None,
                "pe": _unavailable_cell(), "eps": _unavailable_cell(), "cross_check": None,
                "history": {"status": "unavailable", "status_he": UNAVAILABLE_HE, "points": [], "chart": None,
                            "summary": {"count": 0, "median": NO_CHANGE, "mean": NO_CHANGE, "from": "", "to": ""}},
                "peers": {"status": "unavailable", "status_he": UNAVAILABLE_HE, "rows": [], "count": 0,
                          "median": NO_CHANGE, "leaders": []},
                "bars": [], "explanation": [UNAVAILABLE_HE], "sic": None}
    current = val["current"]
    price = current.get("price")
    price_view = None
    if price:
        as_of, label = format_as_of(price.get("as_of"))
        price_view = {"text": format_price(price.get("value")), "as_of": as_of, "as_of_label": label,
                      "provider": price.get("provider", "")}
    pe_cell = build_cell("pe_ttm", current["pe"], report_date)
    eps_cell = build_cell("eps_ttm", current["eps"], report_date)
    cross = current.get("cross_check")
    cross_view = None
    if cross and (_finite(cross.get("pe_ttm")) or _finite(cross.get("eps_ttm"))):
        as_of, label = format_as_of(cross.get("as_of"))
        cross_view = {"provider": cross.get("provider", ""), "pe": format_value("pe_ttm", cross.get("pe_ttm")),
                      "eps": format_value("eps_ttm", cross.get("eps_ttm")), "as_of": as_of, "as_of_label": label}
        if pe_cell["source"]:
            pe_cell["source"]["extra_he"].append(
                f"בדיקה צולבת: {cross_view['provider']} מחשב מכפיל {cross_view['pe']} ורווח למניה {cross_view['eps']}, "
                f"נכון ל-{as_of} {label}".strip() + ".")

    history = val["history"]
    summary = history.get("summary") or {}
    points = []
    for p in history.get("points") or []:
        price_p = p.get("price")
        points.append({
            "report_date": format_date(p.get("report_date")), "form": p.get("form", ""),
            "pe": build_cell("pe_ttm", p["pe"], p.get("report_date") or ""),
            "eps": build_cell("eps_ttm", p["eps"], p.get("report_date") or ""),
            "price": format_price(price_p["value"]) if price_p else NO_CHANGE,
            "price_date": format_date(price_p["as_of"]) if price_p else "",
            "url": safe_sec_url(p.get("url")),
        })
    history_view = {
        "status": history.get("status"), "status_he": history.get("status_he") or "",
        "points": points,
        "summary": {"count": summary.get("count", 0), "median": format_value("pe_ttm", summary.get("median")),
                    "mean": format_value("pe_ttm", summary.get("mean")),
                    "from": format_date(summary.get("from")), "to": format_date(summary.get("to"))},
        "chart": _history_chart(history.get("points") or [], summary.get("median")),
    }

    peers = val["peers"]
    company_pe = current["pe"]["value"] if current["pe"].get("status") == "ok" else None
    rows = []
    for e in peers.get("peers") or []:
        as_of, label = format_as_of(e.get("as_of"))
        status_he = {"ok": "", "loss": "הפסד, אין מכפיל", "unavailable": UNAVAILABLE_HE, "no_data": "לא דווח"}.get(e.get("status"), "")
        rows.append({
            "ticker": e.get("ticker", ""), "status": e.get("status"), "status_he": status_he,
            "pe": format_value("pe_ttm", e.get("pe")) if _finite(e.get("pe")) else status_he or NO_CHANGE,
            "gap": format_signed_percent(relative(company_pe, e.get("pe"))),
            "market_cap": (f"{e['market_cap'] / 1000:,.0f}" if _finite(e.get("market_cap")) else NO_CHANGE),
            "leader": e.get("ticker") in (peers.get("leaders") or []),
            "as_of": as_of, "as_of_label": label,
        })
    peers_view = {
        "status": peers.get("status"), "status_he": peers.get("status_he") or "",
        "rows": rows, "count": peers.get("count", 0), "median": format_value("pe_ttm", peers.get("median")),
        "median_gap": format_signed_percent(relative(company_pe, peers.get("median"))),
        "leaders": list(peers.get("leaders") or []), "fetched_at": format_as_of(peers.get("fetched_at"))[0],
    }

    bars = [_bar("החברה היום", "" if company_pe else current["pe"].get("status_he", UNAVAILABLE_HE), company_pe, "company")]
    if history.get("status") == "ok":
        bars.append(_bar(f"חציון החברה, {summary.get('count', 0)} רבעונים", "", summary.get("median"), "history"))
    if peers.get("status") == "ok":
        bars.append(_bar(f"חציון המתחרות, {peers.get('count', 0)} חברות", "", peers.get("median"), "peers"))
        by_ticker = {e["ticker"]: e for e in peers.get("peers") or []}
        for leader in peers.get("leaders") or []:
            e = by_ticker.get(leader) or {}
            bars.append(_bar(leader, "הפסד" if e.get("status") == "loss" else "", e.get("pe"), "leader"))
    return {
        "status": current.get("status"), "status_he": current.get("status_he") or "",
        "price": price_view, "pe": pe_cell, "eps": eps_cell, "cross_check": cross_view,
        "history": history_view, "peers": peers_view, "bars": _scale_bars(bars),
        "explanation": [isolate_forms(s) for s in val.get("explanation_he") or []],
        "sic": val.get("sic"),
    }


# ---- report reading: growth, efficiency, net profit, 7-stage model ----

NIV_BASIS_HE = {"quarter": "הרבעון מול הרבעון המקביל אשתקד", "annual": "השנה מול השנה הקודמת"}
NIV_LINES_HE = {"revenue": "הכנסות", "costs": "עלות מכר והוצאות תפעול",
                "operating_income": "רווח תפעולי", "net_income": "רווח נקי"}
NIV_EFFICIENCY_FORMULA = "(Cost of sales + Operating expenses) / Revenue"
NOT_REPORTED_CELL_HE = "לא דווח"


def _niv_components(line: str, source: dict[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for v in (source or {}).get("values") or []:
        value, unit_he = _xbrl_value(v)
        tag = v.get("concept", "")
        name, role = TAG_NAMES_HE.get(tag) or NIV_LINES_HE[line], v.get("role_he")
        out.append({
            "kind": "xbrl", "name_he": f"{name}, {role}" if role and role != ROLE_PERIOD_HE else name,
            "tag": tag, "value": value, "unit_he": unit_he, "period": _period_text(v),
            "form": v.get("form", ""), "accession": v.get("accession", ""),
            "filed": format_date(v.get("filed")), "url": safe_sec_url(v.get("url")),
        })
    return out


def _niv_cell(text: str | None, name_he: str, formula: str, parts: list[tuple[str, dict[str, Any] | None]]) -> dict[str, Any]:
    """A clickable number with its source panel; `parts` = (line, source) pairs it was computed from."""
    if text is None:
        return {"kind": "status", "ok": False, "text": NOT_REPORTED_CELL_HE, "source": None}
    components = [c for line, src in parts for c in _niv_components(line, src)]
    notes = list(dict.fromkeys(src["derived"] for _, src in parts if src and src.get("derived")))
    return {"kind": "value", "ok": True, "text": text, "source": {
        "name_he": name_he, "formula": formula, "status": "ok", "status_he": "",
        "note_he": None, "components": components, "missing": [],
        "extra_he": [f"מחושב: {n}." for n in notes],
    }}


def _niv_change(delta: float | None, unit: str, good_when_positive: bool = True) -> dict[str, Any]:
    if not _finite(delta):
        return {"text": NO_CHANGE, "direction": None, "tone": None, "arrow": "", "tone_he": ""}
    rounded = round(delta, 1)
    if rounded == 0:
        direction, tone, text = "flat", "neutral", f"0.0{unit}"
    else:
        direction = "up" if rounded > 0 else "down"
        tone = "good" if (rounded > 0) == good_when_positive else "bad"
        text = f"{rounded:+,.1f}{unit}"
    return {"text": text, "direction": direction, "tone": tone,
            "arrow": _ARROWS[direction], "tone_he": _TONE_HE[tone]}


def _niv_stage(stage: dict[str, Any]) -> dict[str, Any]:
    start, end = stage.get("from_stage"), stage.get("to_stage")
    return {
        "from": start, "to": end, "known": end is not None,
        "moved": start is not None and end is not None and start != end,
        "to_he": stage.get("to_stage_he"), "verdict_he": stage.get("verdict_he"),
        "notes_he": list(stage.get("notes_he") or []),
    }


def _niv_comparison(basis: str, c: dict[str, Any]) -> dict[str, Any]:
    prior_src, current_src = c["sources"]["prior"], c["sources"]["current"]
    values = c["values"]

    def amount(line: str, side: int) -> dict[str, Any]:
        value = values[line][side]
        src = (prior_src, current_src)[side].get(line)
        return _niv_cell(format_millions(value) if _finite(value) else None, NIV_LINES_HE[line],
                         (src or {}).get("values", [{}])[0].get("concept", ""), [(line, src)])

    def eff(side: int) -> dict[str, Any]:
        value = (c["efficiency_prior"], c["efficiency_current"])[side]
        src = (prior_src, current_src)[side]
        return _niv_cell(f"{value * 100:,.1f}%" if _finite(value) else None, "התייעלות", NIV_EFFICIENCY_FORMULA,
                         [("costs", src.get("costs")), ("revenue", src.get("revenue"))])

    growth = c["revenue_growth_pct"]
    rows = [
        {"key": "revenue", "name": NIV_LINES_HE["revenue"], "unit_he": "מיליוני דולר",
         "prior": amount("revenue", 0), "current": amount("revenue", 1),
         "change": _niv_change(growth, "%")},
        {"key": "efficiency", "name": "התייעלות", "unit_he": "הוצאות חלקי הכנסות",
         "prior": eff(0), "current": eff(1),
         # the sign follows the ratio itself (it fell = spending less per dollar), the color says it is favorable
         "change": _niv_change(-c["efficiency_change_points"] if _finite(c["efficiency_change_points"]) else None,
                               " pp", good_when_positive=False)},
        {"key": "operating_income", "name": NIV_LINES_HE["operating_income"], "unit_he": "מיליוני דולר",
         "prior": amount("operating_income", 0), "current": amount("operating_income", 1),
         "change": _niv_change(None, "")},
        {"key": "net_income", "name": NIV_LINES_HE["net_income"], "unit_he": "מיליוני דולר",
         "prior": amount("net_income", 0), "current": amount("net_income", 1),
         "change": _niv_change(None, "")},
    ]
    prior_end, current_end = c.get("period_ends") or (None, None)
    previous = None
    pq = c.get("previous_quarter")
    if pq and _finite(pq.get("revenue")):
        source = pq.get("source")
        previous = {
            "end": format_date(pq.get("end")),
            "revenue": _niv_cell(format_millions(pq["revenue"]), "הכנסות הרבעון הקודם",
                                 (source or {}).get("values", [{}])[0].get("concept", ""), [("revenue", source)]),
            "change": _niv_change(pq.get("revenue_growth_pct"), "%"),
        }
    return {
        "basis": basis, "title": NIV_BASIS_HE[basis], "previous_quarter": previous,
        "prior_end": format_date(prior_end), "current_end": format_date(current_end),
        "stage": _niv_stage(c["stage"]), "rows": rows,
        "net_text_he": (c.get("net_profit") or {}).get("text_he"),
    }


def build_niv_view(result: dict[str, Any] | None) -> dict[str, Any]:
    """View model of the report-reading section; `result` is `report_analysis()` output or None."""
    if result is None:
        return {"status": "unavailable", "status_he": UNAVAILABLE_HE, "comparisons": [], "highlights": []}
    comparisons = [_niv_comparison(basis, c) for basis, c in result["comparisons"].items()]
    has_data = any(cell["ok"] for comp in comparisons for row in comp["rows"]
                   for cell in (row["prior"], row["current"]))
    if not has_data:
        return {"status": "empty", "status_he": NOT_REPORTED_CELL_HE, "comparisons": [], "highlights": []}
    return {
        "status": "ok", "status_he": "", "comparisons": comparisons,
        "highlights": [{"text_he": h["text_he"], "direction": h["direction"], "basis": h["basis"]}
                       for h in result["highlights"]],
    }
