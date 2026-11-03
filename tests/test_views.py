import math

import pytest

from backend import views
from backend.views import (
    build_summary, compute_change, format_date, format_millions, format_value, isolate_forms,
    safe_sec_url, split_name,
)


@pytest.mark.parametrize("key,value,text", [
    ("gross_margin", 0.7497, "75.0%"),
    ("revenue_growth_yoy", 1.0586, "105.9%"),
    ("roe", -0.0123, "-1.2%"),
    ("net_margin", -0.00001, "0.0%"),
    ("current_ratio", 4.5949, "4.59x"),
    ("debt_to_equity", 0.1457, "0.15x"),
    ("debt_to_equity", 1234.5, "1,234.50x"),
    ("equity_ratio", 0.7149, "71.5%"),
])
def test_format_value(key, value, text):
    assert format_value(key, value) == text


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "x", True])
def test_format_value_never_invents_a_number(bad):
    assert format_value("gross_margin", bad) == "—"


@pytest.mark.parametrize("key,cur,prev,text,direction,tone", [
    ("gross_margin", 0.75, 0.74, "+1.0 pp", "up", "good"),
    ("net_margin", 0.62, 0.715, "-9.5 pp", "down", "bad"),
    ("current_ratio", 4.59, 3.44, "+1.15x", "up", "good"),
    ("current_ratio", 3.0, 3.5, "-0.50x", "down", "bad"),
    # debt to equity: lower is better, the sign still follows the number
    ("debt_to_equity", 0.15, 0.04, "+0.11x", "up", "bad"),
    ("debt_to_equity", 0.04, 0.15, "-0.11x", "down", "good"),
    ("gross_margin", 0.7500, 0.7504, "0.0 pp", "flat", "neutral"),
    ("debt_to_equity", 0.151, 0.150, "0.00x", "flat", "neutral"),
])
def test_compute_change(key, cur, prev, text, direction, tone):
    change = compute_change(key, cur, prev)
    assert (change["text"], change["direction"], change["tone"]) == (text, direction, tone)
    assert change["arrow"] == {"up": "▲", "down": "▼", "flat": ""}[direction]


@pytest.mark.parametrize("cur,prev", [(None, 0.5), (0.5, None), (None, None), (math.nan, 1.0)])
def test_change_needs_both_values(cur, prev):
    change = compute_change("roe", cur, prev)
    assert change["text"] == "—" and change["tone"] is None and change["arrow"] == ""


def test_lower_is_better_only_debt_to_equity():
    assert views.LOWER_IS_BETTER == {"debt_to_equity"}
    assert views.PERCENT_KEYS.isdisjoint(views.MULTIPLE_KEYS)


@pytest.mark.parametrize("value,text", [
    (96_221_000_000, "96,221"), (228_984_000_000, "228,984"), (-5_500_000_000, "-5,500"),
    (12_340_000, "12.3"), (0, "0.0"), (-1_000, "0.0"),
])
def test_format_millions(value, text):
    assert format_millions(value) == text


def test_format_date_and_urls():
    assert format_date("2026-07-26") == "26.07.2026"
    assert format_date("garbage") == "garbage" and format_date(None) == ""
    assert safe_sec_url("https://www.sec.gov/Archives/x.htm")
    for bad in ("http://www.sec.gov/x", "https://www.sec.gov.evil.com/x", "https://evil.com/",
                "javascript:alert(1)", "https://www.sec.gov/x\"onclick=1", None, 5):
        assert safe_sec_url(bad) is None


def test_split_name_and_isolate():
    assert split_name("יחס שוטף (Current Ratio)") == ("יחס שוטף", "Current Ratio")
    assert split_name("ללא אנגלית") == ("ללא אנגלית", "")
    assert "⁦" in isolate_forms("מול 10-K") and isolate_forms(None) is None


def _ratio(key, value, status="ok", **extra):
    base = {"key": key, "name_he": "שם (Name)", "value": value, "status": status,
            "status_he": {"ok": "חושב", "not_reported": "לא דווח"}.get(status, "לא רלוונטי לבנקים"),
            "formula": "A / B", "inputs": [], "missing": [], "note_he": None, "period_he": None}
    return {**base, **extra}


def _analysis(latest, previous):
    filing = {"form": "10-Q", "accession": "a", "filing_date": "2026-08-01",
              "report_date": "2026-06-30", "url": "https://www.sec.gov/x"}
    return {"company": "T", "ticker": "T", "cik": 1, "notice_he": None, "is_bank": False,
            "latest": {"filing": filing, "ratios": latest},
            "previous": {"filing": filing, "ratios": previous} if previous is not None else None}


def test_build_summary_matches_by_key_not_position():
    summary = build_summary(_analysis(
        [_ratio("gross_margin", 0.5), _ratio("roe", 0.1)],
        [_ratio("roe", 0.05), _ratio("gross_margin", 0.4)],
    ))
    rows = {r["key"]: r for r in summary["rows"]}
    assert rows["gross_margin"]["change"]["text"] == "+10.0 pp"
    assert rows["roe"]["change"]["text"] == "+5.0 pp"


def test_build_summary_missing_values_and_no_previous():
    summary = build_summary(_analysis([_ratio("gross_margin", None, "not_reported", missing=["רווח גולמי"])], None))
    row = summary["rows"][0]
    assert row["current"]["text"] == "לא דווח" and not row["current"]["ok"]
    assert row["current"]["source"]["missing"] == ["רווח גולמי"]
    assert row["previous"]["text"] == "אין דוח קודם" and row["change"]["text"] == "—"
    assert summary["previous"] is None


def test_build_summary_no_change_when_one_side_not_ok():
    summary = build_summary(_analysis([_ratio("roe", 0.1)], [_ratio("roe", None, "not_reported")]))
    assert summary["rows"][0]["change"]["text"] == "—"
    summary = build_summary(_analysis([_ratio("roe", 0.1)], []))  # key absent in previous
    assert summary["rows"][0]["previous"]["kind"] == "none"
    assert summary["rows"][0]["change"]["text"] == "—"


def test_source_unsafe_url_is_dropped():
    inp = {"concept": "Revenues", "value": 2e9, "start": "2026-04-01", "end": "2026-06-30",
           "accession": "a", "form": "10-Q", "filed": "2026-08-01", "url": "javascript:alert(1)"}
    src = views.build_source(_ratio("gross_margin", 0.5, inputs=[inp]), "2026-06-30")
    assert src["components"][0]["url"] is None
    assert src["components"][0]["value"] == "2,000" and src["components"][0]["name_he"] == "הכנסות"
    assert views.build_source(_ratio("gross_margin", None, "not_applicable"), "") is None


def test_roe_components_are_labelled_by_period():
    def inp(start, end, form="10-Q"):
        return {"concept": "NetIncomeLoss", "value": 1e6, "start": start, "end": end,
                "accession": "a", "form": form, "filed": "", "url": ""}
    ratio = _ratio("roe", 0.1, inputs=[inp("2025-01-27", "2026-01-25", "10-K"),
                                        inp("2026-01-26", "2026-07-26"), inp("2025-01-27", "2025-07-27")])
    names = [c["name_he"] for c in views.build_source(ratio, "2026-07-26")["components"]]
    assert names == ["רווח נקי שנתי", "רווח נקי מתחילת השנה", "רווח נקי מתחילת השנה הקודמת"]
