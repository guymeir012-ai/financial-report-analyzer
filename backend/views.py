"""Pure view-model helpers: turn `analyze()` output into what the templates display."""
import math
import re
from datetime import date
from typing import Any

from backend import filings as f
from backend.ratios import ITEMS_HE

PERCENT_KEYS = frozenset({
    "revenue_growth_yoy", "revenue_growth_qoq", "gross_margin", "operating_margin",
    "net_margin", "roe", "equity_ratio",
})
MULTIPLE_KEYS = frozenset({"current_ratio", "debt_to_equity"})
LOWER_IS_BETTER = frozenset({"debt_to_equity"})

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
}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def format_value(key: str, value: float | None) -> str:
    """Percent with one decimal, or a multiple with two decimals and an x."""
    if not _finite(value):
        return NO_CHANGE
    if key in PERCENT_KEYS:
        text = f"{value * 100:,.1f}"
        return ("0.0" if float(text.replace(",", "")) == 0 else text) + "%"
    text = f"{value:,.2f}"
    return ("0.00" if float(text.replace(",", "")) == 0 else text) + "x"


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


def build_source(ratio: dict[str, Any], report_date: str) -> dict[str, Any] | None:
    """Content of the source panel, or None when there is nothing to show."""
    inputs, missing = ratio.get("inputs") or [], ratio.get("missing") or []
    if not inputs and not missing:
        return None
    components = []
    for inp in inputs:
        value = inp.get("value")
        components.append({
            "name_he": _component_name(ratio["key"], inp, report_date),
            "tag": inp.get("concept", ""),
            "value": format_millions(value) if _finite(value) else NO_CHANGE,
            "period": _period_text(inp),
            "form": inp.get("form", ""),
            "accession": inp.get("accession", ""),
            "filed": format_date(inp.get("filed")),
            "url": safe_sec_url(inp.get("url")),
        })
    return {
        "name_he": split_name(ratio["name_he"])[0],
        "formula": ratio.get("formula", ""),
        "status": ratio.get("status"),
        "status_he": isolate_forms(ratio.get("status_he", "")),
        "components": components,
        "missing": list(missing),
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
