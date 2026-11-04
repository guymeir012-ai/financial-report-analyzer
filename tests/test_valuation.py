"""EPS TTM, P/E, history, peers and the explanation, all from fixtures (no network)."""
import json

import pytest

from backend import valuation as v
from backend.filings import FilingFacts, filings_with_facts
from backend.prices import PriceError
from backend.ratios import load_company
from tests.conftest import LATEST_ACCN, NVDA_CIK, PREVIOUS_ACCN, make_facts, make_filing

TENK_ACCN = "0001045810-26-000021"
ANNUAL_NI, ANNUAL_SHARES, REPORTED_EPS = 120_067e6, 24_514e6, 4.90
TTM_LATEST = 120_067e6 + 118_010e6 - 45_197e6          # 192,880M
TTM_PREVIOUS = 120_067e6 + 58_321e6 - 18_775e6         # 159,613M
RECOMMENDATION_WORDS = ("קנייה", "מכירה", "המלצ", "Buy", "Sell", "לקנות", "למכור")


def filing_by(nvda_filings, accn):
    return next(x for x in nvda_filings if x.accession == accn)


# ---- EPS ----

def test_eps_ttm_latest_10q(nvda_facts, nvda_filings):
    res = v.eps_ttm(FilingFacts(nvda_facts, filing_by(nvda_filings, LATEST_ACCN)))
    assert res["status"] == "ok" and res["value"] == pytest.approx(TTM_LATEST / 24_285e6)
    assert res["key"] == "eps_ttm" and res["period_he"] == "12 חודשים אחרונים"
    assert "בדילול" in res["note_he"] and "ספקים אחרים" in res["note_he"]
    concepts = [i["concept"] for i in res["inputs"]]
    assert concepts.count("NetIncomeLoss") == 3 and "WeightedAverageNumberOfDilutedSharesOutstanding" in concepts
    shares = next(i for i in res["inputs"] if i["unit"] == "shares")
    assert (shares["start"], shares["end"], shares["value"]) == ("2026-04-27", "2026-07-26", 24_285e6)


def test_eps_ttm_previous_10q_and_10k(nvda_facts, nvda_filings):
    prev = v.eps_ttm(FilingFacts(nvda_facts, filing_by(nvda_filings, PREVIOUS_ACCN)))
    assert prev["value"] == pytest.approx(TTM_PREVIOUS / 24_391e6)
    tenk = v.eps_ttm(FilingFacts(nvda_facts, filing_by(nvda_filings, TENK_ACCN)))
    assert tenk["value"] == pytest.approx(ANNUAL_NI / ANNUAL_SHARES)
    assert tenk["formula"] == "NetIncomeLoss annual / WeightedAverageNumberOfDilutedSharesOutstanding"


def test_annual_eps_from_10k_matches_reported_eps_within_1_percent(nvda_facts, nvda_filings):
    ff = FilingFacts(nvda_facts, filing_by(nvda_filings, TENK_ACCN))
    ours = v.eps_annual(ff)
    reported = ff.reported_eps()
    assert ours["status"] == "ok" and reported is not None and reported.unit == "USD/shares"
    assert ours["value"] == pytest.approx(reported.value, rel=0.01)
    assert reported.value == REPORTED_EPS


def test_annual_eps_only_for_10k(nvda_facts, nvda_filings):
    res = v.eps_annual(FilingFacts(nvda_facts, filing_by(nvda_filings, LATEST_ACCN)))
    assert res["status"] == "not_reported" and "מספר מניות בדילול" in res["missing"]


def test_shares_pick_the_quarter_not_the_six_months(nvda_facts, nvda_filings):
    shares = FilingFacts(nvda_facts, filing_by(nvda_filings, LATEST_ACCN)).diluted_shares()
    assert shares.value == 24_285e6 and shares.start == "2026-04-27"


def test_eps_missing_shares_is_not_reported():
    filing = make_filing(form="10-K", report_date="2026-06-30")
    facts = make_facts(filing.accession, [("NetIncomeLoss", "2025-07-01", "2026-06-30", 10e6, None, "10-K")])
    res = v.eps_ttm(FilingFacts(facts, filing))
    assert res["status"] == "not_reported" and res["missing"] == ["מספר מניות בדילול"] and res["value"] is None


def test_eps_zero_shares_not_computable():
    filing = make_filing(form="10-K", report_date="2026-06-30")
    facts = make_facts(filing.accession, [
        ("NetIncomeLoss", "2025-07-01", "2026-06-30", 10e6, None, "10-K"),
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "2025-07-01", "2026-06-30", 0, None, "10-K", "shares"),
    ])
    assert v.eps_ttm(FilingFacts(facts, filing))["status"] == "not_computable"


def test_eps_uses_income_available_to_common_when_reported():
    # A bank with preferred dividends: EPS starts from income to common shareholders, not from net income.
    filing = make_filing(form="10-K", report_date="2026-06-30")
    rows = [
        ("NetIncomeLoss", "2025-07-01", "2026-06-30", 100e6, None, "10-K"),
        ("NetIncomeLossAvailableToCommonStockholdersDiluted", "2025-07-01", "2026-06-30", 90e6, None, "10-K"),
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "2025-07-01", "2026-06-30", 10e6, None, "10-K", "shares"),
        ("EarningsPerShareDiluted", "2025-07-01", "2026-06-30", 9.0, None, "10-K", "USD/shares"),
    ]
    ff = FilingFacts(make_facts(filing.accession, rows), filing)
    ttm, annual = v.eps_ttm(ff), v.eps_annual(ff)
    assert ttm["value"] == 9.0 and annual["value"] == 9.0 == ff.reported_eps().value
    assert ttm["formula"].startswith("NetIncomeLossAvailableToCommonStockholdersDiluted annual")
    # ROE keeps using net income
    from backend.ratios import ttm_net_income
    assert ttm_net_income(ff)[2] == 100e6


def test_shares_in_usd_unit_are_ignored():
    filing = make_filing(form="10-K", report_date="2026-06-30")
    facts = make_facts(filing.accession, [
        ("NetIncomeLoss", "2025-07-01", "2026-06-30", 10e6, None, "10-K"),
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "2025-07-01", "2026-06-30", 5e6, None, "10-K", "USD"),
    ])
    assert v.eps_ttm(FilingFacts(facts, filing))["status"] == "not_reported"


# ---- P/E ----

def price(value, day="2026-11-04T00:00:00+00:00"):
    return v.provider_source("מחיר המניה", "Finnhub", "quote", value, day)


def test_pe_ok_and_sources(nvda_facts, nvda_filings):
    eps = v.eps_ttm(FilingFacts(nvda_facts, filing_by(nvda_filings, LATEST_ACCN)))
    pe = v.pe_from(price(203.48), eps)
    assert pe["status"] == "ok" and pe["value"] == pytest.approx(203.48 / eps["value"])
    assert pe["inputs"][0]["kind"] == "provider" and pe["inputs"][0]["provider"] == "Finnhub"
    assert pe["inputs"][1]["kind"] == "derived" and pe["inputs"][1]["value"] == eps["value"]


def test_pe_loss_is_not_computable_without_a_number():
    filing = make_filing(form="10-K", report_date="2026-06-30")
    facts = make_facts(filing.accession, [
        ("NetIncomeLoss", "2025-07-01", "2026-06-30", -10e6, None, "10-K"),
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "2025-07-01", "2026-06-30", 5e6, None, "10-K", "shares"),
    ])
    pe = v.pe_from(price(50.0), v.eps_ttm(FilingFacts(facts, filing)))
    assert pe["status"] == "not_computable" and pe["value"] is None
    assert pe["status_he"] == "לא ניתן לחישוב: הפסד ב-12 החודשים האחרונים"


def test_pe_without_price_is_unavailable(nvda_facts, nvda_filings):
    eps = v.eps_ttm(FilingFacts(nvda_facts, filing_by(nvda_filings, LATEST_ACCN)))
    pe = v.pe_from(None, eps)
    assert pe["status"] == "unavailable" and pe["status_he"] == "לא זמין כרגע" and pe["value"] is None


def test_pe_inherits_missing_eps(nvda_facts, nvda_filings):
    filing = make_filing(form="10-K", report_date="2026-06-30")
    facts = make_facts(filing.accession, [("NetIncomeLoss", "2025-07-01", "2026-06-30", 10e6, None, "10-K")])
    pe = v.pe_from(price(50.0), v.eps_ttm(FilingFacts(facts, filing)))
    assert pe["status"] == "not_reported" and pe["missing"] == ["מספר מניות בדילול"]


# ---- history ----

CLOSES = [("2026-07-20", 203.28), ("2026-07-23", 208.76), ("2026-07-24", 206.84), ("2026-07-27", 196.51),
          ("2026-04-24", 100.0), ("2026-01-23", 50.0)]


@pytest.mark.parametrize("day, expected", [
    ("2026-07-24", ("2026-07-24", 206.84)),   # exact trading day
    ("2026-07-26", ("2026-07-24", 206.84)),   # Sunday -> Friday
    ("2026-07-21", ("2026-07-20", 203.28)),
    ("2026-08-10", None),                     # gap longer than 7 days
    ("2026-01-01", None),                     # before the first close
    ("not-a-date", None),
])
def test_price_on_or_before(day, expected):
    assert v.price_on_or_before(CLOSES, day) == expected


def test_pe_history_points(nvda_facts, nvda_filings):
    chain = filings_with_facts(nvda_facts, nvda_filings)
    assert [x.accession for x in chain] == [LATEST_ACCN, PREVIOUS_ACCN, TENK_ACCN]
    points = v.pe_history(nvda_facts, chain, CLOSES, "2026-11-04T00:00:00+00:00")
    assert [p["report_date"] for p in points] == ["2026-07-26", "2026-04-26", "2026-01-25"]
    assert points[0]["value"] == pytest.approx(206.84 / (TTM_LATEST / 24_285e6))
    assert points[0]["price"]["provider"] == "Tiingo" and points[0]["price"]["as_of"] == "2026-07-24"
    assert points[1]["value"] == pytest.approx(100.0 / (TTM_PREVIOUS / 24_391e6))
    assert points[2]["value"] == pytest.approx(50.0 / (ANNUAL_NI / ANNUAL_SHARES))
    assert all(p["url"].startswith("https://www.sec.gov/") for p in points)


def test_pe_history_missing_price_and_limit(nvda_facts, nvda_filings):
    chain = filings_with_facts(nvda_facts, nvda_filings)
    points = v.pe_history(nvda_facts, chain, [("2026-04-24", 100.0)], "x", limit=2)
    assert len(points) == 2
    assert points[0]["price"] is None and points[0]["value"] is None and points[0]["pe"]["status"] == "unavailable"
    assert points[1]["value"] == pytest.approx(100.0 / (TTM_PREVIOUS / 24_391e6))


def test_split_does_not_distort_history():
    # Before a 10:1 split: 10M shares, price 1000. After: 100M shares, price 100. Same P/E.
    old = make_filing(form="10-K", accession="0000000001-25-000001", report_date="2025-06-30", filing_date="2025-08-01")
    new = make_filing(form="10-K", accession="0000000001-26-000001", report_date="2026-06-30", filing_date="2026-08-01")
    facts = make_facts(old.accession, [
        ("NetIncomeLoss", "2024-07-01", "2025-06-30", 100e6, old.accession, "10-K"),
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "2024-07-01", "2025-06-30", 10e6, old.accession, "10-K", "shares"),
        ("NetIncomeLoss", "2025-07-01", "2026-06-30", 100e6, new.accession, "10-K"),
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "2025-07-01", "2026-06-30", 100e6, new.accession, "10-K", "shares"),
    ])
    points = v.pe_history(facts, [new, old], [("2025-06-30", 1000.0), ("2026-06-30", 100.0)], "x")
    assert points[0]["value"] == pytest.approx(100.0) and points[1]["value"] == pytest.approx(100.0)


def test_summarize_history_median_mean_and_empty():
    pts = [{"report_date": "2026-07-26", "value": 30.0}, {"report_date": "2026-04-26", "value": None},
           {"report_date": "2026-01-25", "value": 10.0}, {"report_date": "2025-10-26", "value": 200.0}]
    s = v.summarize_history(pts)
    assert s == {"count": 3, "median": 30.0, "mean": 80.0, "from": "2025-10-26", "to": "2026-07-26"}
    assert v.summarize_history([]) == {"count": 0, "median": None, "mean": None, "from": None, "to": None}


def test_year_ago_point():
    pts = [{"report_date": "2026-07-26"}, {"report_date": "2026-04-26"}, {"report_date": "2026-01-25"},
           {"report_date": "2025-10-26"}, {"report_date": "2025-07-27"}, {"report_date": "2025-04-27"}]
    assert v.year_ago_point(pts)["report_date"] == "2025-07-27"
    assert v.year_ago_point(pts[:4]) is None and v.year_ago_point([]) is None


# ---- peers ----

class StubFinnhub:
    def __init__(self, quote=None, peers=(), metrics=None, failing=(), peers_error=None, quote_error=None):
        self._quote, self._peers, self._metrics = quote, list(peers), metrics or {}
        self.failing, self.peers_error, self.quote_error = set(failing), peers_error, quote_error
        self.calls = []

    def quote(self, symbol):
        self.calls.append(("quote", symbol))
        if self.quote_error:
            raise self.quote_error
        return self._quote

    def peers(self, symbol):
        self.calls.append(("peers", symbol))
        if self.peers_error:
            raise self.peers_error
        return {"peers": self._peers, "fetched_at": 1_800_000_000.0}

    def metrics(self, symbol):
        self.calls.append(("metrics", symbol))
        if symbol in self.failing:
            raise PriceError(status=503)
        m = self._metrics.get(symbol, {"pe_ttm": None, "eps_ttm": None, "market_cap": None})
        return {**m, "fetched_at": 1_800_000_000.0}


PEER_METRICS = {
    "AVGO": {"pe_ttm": 41.5, "eps_ttm": 7.8, "market_cap": 1_589_762},
    "AMD": {"pe_ttm": 80.0, "eps_ttm": 2.0, "market_cap": 300_000},
    "INTC": {"pe_ttm": None, "eps_ttm": -1.2, "market_cap": 150_000},
    "MU": {"pe_ttm": 20.0, "eps_ttm": 5.0, "market_cap": 200_000},
    "TXN": {"pe_ttm": 30.0, "eps_ttm": 5.0, "market_cap": 180_000},
    "NVDA": {"pe_ttm": 25.8, "eps_ttm": 7.91, "market_cap": 4_981_229},
}


def test_peers_valuation_median_leaders_and_loss():
    fh = StubFinnhub(peers=["AVGO", "AMD", "INTC", "MU", "TXN", "ADI"], metrics=PEER_METRICS, failing={"ADI"})
    out = v.peers_valuation(fh, "NVDA")
    assert out["status"] == "ok" and out["count"] == 4 and out["median"] == 35.75 and out["mean"] == 42.875
    assert out["leaders"] == ["AVGO", "AMD", "MU"]
    by = {e["ticker"]: e for e in out["peers"]}
    assert by["INTC"]["status"] == "loss" and by["INTC"]["pe"] is None
    assert by["ADI"]["status"] == "unavailable"
    assert by["AVGO"]["status"] == "ok" and by["AVGO"]["as_of"] == "2027-01-15T08:00:00+00:00"
    assert sorted(c[1] for c in fh.calls if c[0] == "metrics") == ["ADI", "AMD", "AVGO", "INTC", "MU", "TXN"]


def test_peers_limit_and_empty_and_all_failing():
    fh = StubFinnhub(peers=[f"P{i}" for i in range(12)], metrics={})
    out = v.peers_valuation(fh, "NVDA", limit=8)
    assert len(out["peers"]) == 8 and out["count"] == 0 and out["median"] is None and out["leaders"] == []
    assert all(e["status"] == "no_data" for e in out["peers"])
    assert v.peers_valuation(StubFinnhub(peers=[]), "NVDA")["status"] == "empty"
    down = StubFinnhub(peers=["AVGO", "AMD"], failing={"AVGO", "AMD"})
    assert v.peers_valuation(down, "NVDA")["status"] == "unavailable"


@pytest.mark.parametrize("value, bench, expected", [
    (30.0, 20.0, 0.5), (20.0, 40.0, -0.5), (None, 20.0, None), (20.0, None, None), (20.0, 0.0, None), (-5.0, 20.0, None),
])
def test_relative(value, bench, expected):
    assert v.relative(value, bench) == (pytest.approx(expected) if expected is not None else None)


# ---- explanation and orchestration ----

QUOTE = {"price": 203.48, "previous_close": 206.69, "timestamp": 1793814988, "fetched_at": 1793820231.5}


class StubTiingo:
    def __init__(self, closes, error=None):
        self._closes, self.error, self.calls = closes, error, []

    def daily_closes(self, symbol, start):
        self.calls.append((symbol, start))
        if self.error:
            raise self.error
        return {"closes": self._closes, "fetched_at": 1_800_000_000.0}


def loaded_nvda(nvda_client):
    return load_company("NVDA", nvda_client)


def test_build_valuation_full(nvda_client):
    fh = StubFinnhub(quote=QUOTE, peers=["AVGO", "AMD", "INTC", "MU", "TXN"], metrics=PEER_METRICS)
    ti = StubTiingo(CLOSES)
    out = v.build_valuation(loaded_nvda(nvda_client), fh, ti)
    assert out["ticker"] == "NVDA" and out["sic"] == "3674"
    cur = out["current"]
    assert cur["status"] == "ok" and cur["pe"]["status"] == "ok"
    assert cur["pe"]["value"] == pytest.approx(203.48 / (TTM_LATEST / 24_285e6))
    assert cur["price"]["as_of"] == "2026-11-04T17:56:28+00:00" and cur["price"]["timestamp"] == 1793814988
    assert cur["cross_check"] == {"provider": "Finnhub", "pe_ttm": 25.8, "eps_ttm": 7.91, "market_cap": 4_981_229,
                                  "as_of": "2027-01-15T08:00:00+00:00"}
    assert out["history"]["status"] == "ok" and out["history"]["summary"]["count"] == 3
    assert ti.calls == [("NVDA", "2026-01-25")]
    assert out["peers"]["status"] == "ok" and out["peers"]["leaders"] == ["AVGO", "AMD", "MU"]
    text = " ".join(out["explanation_he"])
    assert text.endswith("זולה לא אומר ראויה להשקעה.")
    assert "חציון המתחרות (35.8, 4 חברות)" in text and "AVGO 41.5 (הנחה" in text
    assert not any(w in text for w in RECOMMENDATION_WORDS)
    json.dumps(out, ensure_ascii=False)  # the API returns it as JSON


def test_explanation_numbers_match_the_computed_ones(nvda_client):
    fh = StubFinnhub(quote=QUOTE, peers=["AVGO"], metrics=PEER_METRICS)
    out = v.build_valuation(loaded_nvda(nvda_client), fh, StubTiingo(CLOSES))
    pe = out["current"]["pe"]["value"]
    median = out["history"]["summary"]["median"]
    first = out["explanation_he"][0]
    assert f"{pe:.1f}" in first and f"({median:.1f})" in first
    diff = pe / median - 1
    assert f"{abs(diff) * 100:.0f}%" in first and ("גבוה" in first if diff > 0 else "נמוך" in first)
    peer_line = next(s for s in out["explanation_he"] if "חציון המתחרות" in s)
    assert f"{abs(pe / 41.5 - 1) * 100:.0f}%" in peer_line


def test_build_valuation_without_finnhub_key(nvda_client):
    out = v.build_valuation(loaded_nvda(nvda_client), None, StubTiingo(CLOSES))
    assert out["current"]["status"] == "unavailable" and out["current"]["pe"]["status"] == "unavailable"
    assert out["current"]["eps"]["status"] == "ok"            # EPS needs only SEC
    assert out["history"]["status"] == "ok"
    assert out["peers"]["status"] == "unavailable" and out["peers"]["peers"] == []
    assert out["explanation_he"] == [v.CLOSING_HE]


def test_build_valuation_tiingo_down_keeps_the_rest(nvda_client, caplog):
    fh = StubFinnhub(quote=QUOTE, peers=["AVGO"], metrics=PEER_METRICS)
    out = v.build_valuation(loaded_nvda(nvda_client), fh, StubTiingo([], error=PriceError(status=503)))
    assert out["history"]["status"] == "unavailable" and out["history"]["points"] == []
    assert out["current"]["pe"]["status"] == "ok" and out["peers"]["status"] == "ok"
    assert "מכפיל הרווח היום" not in " ".join(out["explanation_he"])
    assert any("חציון המתחרות" in s for s in out["explanation_he"])


def test_build_valuation_quote_and_peers_down(nvda_client):
    fh = StubFinnhub(quote=None, peers=[], quote_error=PriceError(status=429), peers_error=PriceError(status=503))
    out = v.build_valuation(loaded_nvda(nvda_client), fh, StubTiingo(CLOSES))
    assert out["current"]["status"] == "unavailable" and out["current"]["price"] is None
    assert out["peers"]["status"] == "unavailable"
    assert out["history"]["status"] == "ok"


def test_build_valuation_unknown_symbol_at_finnhub(nvda_client):
    fh = StubFinnhub(quote=None, peers=[], metrics={})
    out = v.build_valuation(loaded_nvda(nvda_client), fh, None)
    assert out["current"]["status"] == "unavailable" and out["peers"]["status"] == "empty"
    assert out["history"]["status"] == "unavailable"


def test_loss_explanation():
    filing = make_filing(form="10-K", report_date="2026-06-30")
    facts = make_facts(filing.accession, [
        ("NetIncomeLoss", "2025-07-01", "2026-06-30", -10e6, None, "10-K"),
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "2025-07-01", "2026-06-30", 5e6, None, "10-K", "shares"),
    ])
    pe = v.pe_from(price(50.0), v.eps_ttm(FilingFacts(facts, filing)))
    out = v.explain({"pe": pe}, {"summary": {"median": 20.0, "count": 4}, "points": []},
                    {"status": "ok", "median": 20.0, "count": 3, "peers": [], "leaders": []})
    assert out == [v.LOSS_EXPLANATION_HE, v.CLOSING_HE]


def test_year_over_year_sentence_uses_history_points():
    def point(day, eps, px):
        return {"report_date": day, "eps": {"value": eps}, "price": {"value": px}, "value": px / eps}
    pts = [point("2026-07-26", 8.0, 200.0), point("2026-04-26", 7.0, 180.0), point("2026-01-25", 6.0, 150.0),
           point("2025-10-26", 5.0, 120.0), point("2025-07-27", 4.0, 160.0)]
    pe = {"status": "ok", "value": 25.0, "status_he": "חושב"}
    out = v.explain({"pe": pe}, {"summary": v.summarize_history(pts), "points": pts}, {"status": "empty"})
    yoy = next(s for s in out if "הרווח למניה" in s)
    assert "27.07.2025" in yoy and "26.07.2026" in yoy and "עלה ב-100%" in yoy and "עלה ב-25%" in yoy
    assert out[-1] == v.CLOSING_HE and len(out) == 3
