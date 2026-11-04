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


# ---- valuation formatting and view ----

from backend.views import (  # noqa: E402
    build_source, build_valuation_view, format_as_of, format_israel_time, format_signed_percent,
)


@pytest.mark.parametrize("key, value, text", [
    ("pe_ttm", 25.66, "25.7x"), ("pe_ttm", 123.456, "123.5x"), ("pe_ttm", 0.04, "0.0x"), ("pe_ttm", None, "—"),
    ("eps_ttm", 7.9424, "$7.94"), ("eps_ttm", -2.056, "-$2.06"), ("eps_ttm", 1234.5, "$1,234.50"), ("eps_ttm", -0.001, "$0.00"),
    ("eps_annual", 4.8979, "$4.90"),
])
def test_format_value_valuation_keys(key, value, text):
    assert format_value(key, value) == text


@pytest.mark.parametrize("value, text", [
    (0.384, "+38%"), (-0.384, "-38%"), (0.004, "0%"), (0.0, "0%"), (None, "—"), (float("nan"), "—"), (1.37, "+137%"),
])
def test_format_signed_percent(value, text):
    assert format_signed_percent(value) == text


def test_format_israel_time():
    assert format_israel_time("2026-11-04T17:56:28+00:00") == "04.11.2026 19:56"   # winter: UTC+2
    assert format_israel_time("2026-07-24T13:30:00+00:00") == "24.07.2026 16:30"   # summer: UTC+3
    assert format_israel_time("2026-11-04T17:56:28") == "04.11.2026 19:56"         # naive = UTC
    assert format_israel_time(None) == "" and format_israel_time("garbage") == "garbage"


def test_format_as_of_date_versus_timestamp():
    assert format_as_of("2026-07-24") == ("24.07.2026", "")
    assert format_as_of("2026-11-04T17:56:28+00:00") == ("04.11.2026 19:56", "שעון ישראל")
    assert format_as_of(None) == ("", "")


def test_build_source_provider_derived_and_units():
    ratio = {
        "key": "pe_ttm", "name_he": "מכפיל רווח (P/E TTM)", "formula": "price / EPS TTM", "status": "ok",
        "status_he": "חושב", "note_he": None, "missing": [],
        "inputs": [
            {"kind": "provider", "name_he": "מחיר המניה", "provider": "Finnhub", "endpoint": "quote",
             "value": 203.48, "unit": "USD", "as_of": "2026-11-04T17:56:28+00:00"},
            {"kind": "derived", "name_he": "רווח למניה", "value": 7.9424, "status": "ok", "status_he": "חושב", "unit": "USD"},
            {"concept": "WeightedAverageNumberOfDilutedSharesOutstanding", "value": 24_285e6, "unit": "shares",
             "start": "2026-04-27", "end": "2026-07-26", "accession": "a", "form": "10-Q", "filed": "2026-08-26",
             "url": "https://www.sec.gov/x"},
            {"concept": "EarningsPerShareDiluted", "value": 4.9, "unit": "USD/shares", "start": "2025-01-27",
             "end": "2026-01-25", "accession": "b", "form": "10-K", "filed": "2026-02-25", "url": "https://www.sec.gov/y"},
        ],
    }
    src = build_source(ratio, "2026-07-26")
    provider, derived, shares, eps = src["components"]
    assert provider == {"kind": "provider", "name_he": "מחיר המניה", "provider": "Finnhub", "endpoint": "quote",
                        "value": "203.48", "unit_he": "דולר", "as_of": "04.11.2026 19:56", "as_of_label": "שעון ישראל"}
    assert derived["kind"] == "derived" and derived["value"] == "$7.94"
    assert shares["name_he"] == "מספר מניות בדילול" and shares["value"] == "24,285" and shares["unit_he"] == "מיליוני מניות"
    assert eps["value"] == "4.90" and eps["unit_he"] == "דולר למניה" and eps["name_he"] == "רווח למניה בדילול, כפי שדווח"
    assert src["extra_he"] == []


def _ratio_ok(key, value, inputs=None):
    return {"key": key, "name_he": {"pe_ttm": "מכפיל רווח (P/E TTM)", "eps_ttm": "רווח למניה (EPS TTM)"}[key],
            "value": value, "status": "ok", "status_he": "חושב", "formula": "f", "inputs": inputs or [], "missing": [],
            "note_he": None, "period_he": None}


def _point(day, pe, price=None):
    return {"report_date": day, "form": "10-Q", "accession": "a", "filed": day, "url": "https://www.sec.gov/x",
            "eps": _ratio_ok("eps_ttm", 8.0), "price": price, "pe": _ratio_ok("pe_ttm", pe), "value": pe}


def _valuation(pe=25.0, history_median=50.0, peers_status="ok"):
    pts = [_point("2026-07-26", pe, {"kind": "provider", "value": 200.0, "as_of": "2026-07-24"}),
           _point("2026-04-26", history_median), _point("2026-01-25", None)]
    return {
        "ticker": "NVDA", "company": "NVIDIA", "sic": "3674",
        "current": {"status": "ok", "status_he": "", "filing": None,
                    "eps": _ratio_ok("eps_ttm", 8.0), "price": {"kind": "provider", "value": 200.0, "provider": "Finnhub",
                                                              "as_of": "2026-11-04T17:56:28+00:00"},
                    "pe": _ratio_ok("pe_ttm", pe, inputs=[{"kind": "provider", "name_he": "מחיר", "provider": "Finnhub",
                                                           "endpoint": "quote", "value": 200.0, "as_of": "2026-11-04T17:56:28+00:00"}]),
                    "cross_check": {"provider": "Finnhub", "pe_ttm": 25.8, "eps_ttm": 7.91, "market_cap": 1.0,
                                    "as_of": "2026-11-04T18:00:00+00:00"}},
        "history": {"status": "ok", "status_he": "", "points": pts,
                    "summary": {"count": 2, "median": history_median, "mean": history_median, "from": "2026-04-26", "to": "2026-07-26"}},
        "peers": {"status": peers_status, "status_he": "", "count": 2, "median": 40.0, "mean": 40.0, "leaders": ["AVGO", "INTC"],
                  "peers": [{"ticker": "AVGO", "status": "ok", "pe": 50.0, "eps_ttm": 1.0, "market_cap": 1_589_762, "as_of": "2026-11-04T18:00:00+00:00"},
                            {"ticker": "INTC", "status": "loss", "pe": None, "eps_ttm": -1.0, "market_cap": 150_000, "as_of": None},
                            {"ticker": "MU", "status": "ok", "pe": 20.0, "eps_ttm": 1.0, "market_cap": None, "as_of": None}],
                  "fetched_at": "2026-11-04T18:00:00+00:00"},
        "explanation_he": ["משפט אחד.", "דוח 10-Q מוזכר."],
    }


def test_build_valuation_view_bars_chart_peers_and_cross_check():
    v = build_valuation_view(_valuation(), "2026-07-26")
    assert v["status"] == "ok" and v["price"] == {"text": "200.00", "as_of": "04.11.2026 19:56", "as_of_label": "שעון ישראל", "provider": "Finnhub"}
    assert v["pe"]["text"] == "25.0x" and v["eps"]["text"] == "$8.00"
    assert v["pe"]["source"]["extra_he"] == ["בדיקה צולבת: Finnhub מחשב מכפיל 25.8x ורווח למניה $7.91, נכון ל-04.11.2026 20:00 שעון ישראל."]
    labels = [(b["label"], b["width"], b["text"]) for b in v["bars"]]
    assert labels == [("החברה היום", 50.0, "25.0x"), ("חציון החברה, 2 רבעונים", 100.0, "50.0x"),
                      ("חציון המתחרות, 2 חברות", 80.0, "40.0x"), ("AVGO", 100.0, "50.0x"), ("INTC", 0.0, "הפסד")]
    chart = v["history"]["chart"]
    assert chart and len(chart["columns"]) == 3 and chart["columns"][0]["label"] == "01/26" and chart["columns"][0]["missing"]
    assert chart["columns"][1]["h"] > chart["columns"][2]["h"] > 0 and chart["median_text"] == "50.0x"
    assert chart["columns"][1]["h"] == pytest.approx(chart["baseline_y"] - chart["median_y"], abs=0.2)
    assert v["history"]["summary"] == {"count": 2, "median": "50.0x", "mean": "50.0x", "from": "26.04.2026", "to": "26.07.2026"}
    assert v["history"]["points"][0]["price"] == "200.00" and v["history"]["points"][0]["price_date"] == "24.07.2026"
    assert v["history"]["points"][1]["price"] == "—"
    rows = {r["ticker"]: r for r in v["peers"]["rows"]}
    assert rows["AVGO"]["gap"] == "-50%" and rows["AVGO"]["leader"] and rows["AVGO"]["market_cap"] == "1,590"
    assert rows["MU"]["gap"] == "+25%" and not rows["MU"]["leader"] and rows["MU"]["market_cap"] == "—"
    assert rows["INTC"]["pe"] == "הפסד, אין מכפיל" and rows["INTC"]["gap"] == "—"
    assert v["peers"]["median_gap"] == "-38%" and v["peers"]["median"] == "40.0x"
    assert v["explanation"][1] == "דוח ⁦10-Q⁩ מוזכר." and v["sic"] == "3674"


def test_build_valuation_view_none_and_partial():
    none = build_valuation_view(None, "2026-07-26")
    assert none["status"] == "unavailable" and none["pe"]["text"] == "לא זמין כרגע" and none["bars"] == []
    assert none["history"]["chart"] is None and none["peers"]["rows"] == []
    partial = _valuation(peers_status="unavailable")
    partial["current"]["pe"] = {**_ratio_ok("pe_ttm", None), "status": "unavailable", "status_he": "לא זמין כרגע", "value": None}
    partial["current"]["price"] = None
    partial["current"]["cross_check"] = None
    v = build_valuation_view(partial, "2026-07-26")
    assert v["price"] is None and v["pe"]["text"] == "לא זמין כרגע"
    assert [b["label"] for b in v["bars"]] == ["החברה היום", "חציון החברה, 2 רבעונים"]
    assert v["bars"][0]["width"] == 0.0 and v["bars"][0]["text"] == "לא זמין כרגע"
    assert v["peers"]["median_gap"] == "—"
