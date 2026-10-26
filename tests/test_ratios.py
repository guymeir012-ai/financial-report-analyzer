import pytest

from backend.ratios import NOT_REPORTED_HE, analyze, compute_ratios
from tests.conftest import LATEST_ACCN, LATEST_URL, PREVIOUS_ACCN, make_facts, make_filing

REL = 1e-6
KEYS = ["revenue_growth_yoy", "revenue_growth_qoq", "gross_margin", "operating_margin",
        "net_margin", "roe", "current_ratio", "debt_to_equity", "equity_ratio"]


def by_key(results):
    return {r["key"]: r for r in results}


def test_result_order_and_shape(nvda_facts, latest, previous):
    results = compute_ratios(nvda_facts, latest, previous)
    assert [r["key"] for r in results] == KEYS
    for r in results:
        assert set(r) >= {"key", "name_he", "value", "status", "status_he", "formula", "period_days", "inputs"}
        assert "(" in r["name_he"] and any("֐" <= c <= "׿" for c in r["name_he"])


def test_latest_quarter_values(nvda_facts, latest, previous):
    r = by_key(compute_ratios(nvda_facts, latest, previous))
    expected = {
        "revenue_growth_yoy": 96221 / 46743 - 1,
        "revenue_growth_qoq": 96221 / 81615 - 1,
        "gross_margin": 72142 / 96221,
        "operating_margin": 63734 / 96221,
        "net_margin": 59688 / 96221,
        "roe": 59688 / 228984,
        "current_ratio": 197412 / 43019,
        "debt_to_equity": 33366 / 228984,
        "equity_ratio": 228984 / 320272,
    }
    for key, value in expected.items():
        assert r[key]["status"] == "ok", key
        assert r[key]["value"] == pytest.approx(value, rel=REL), key
    assert r["gross_margin"]["period_days"] == 90


def test_previous_quarter_values(nvda_facts, previous):
    r = by_key(compute_ratios(nvda_facts, previous))
    assert r["revenue_growth_yoy"]["value"] == pytest.approx(81615 / 44062 - 1, rel=REL)
    assert r["gross_margin"]["value"] == pytest.approx(61157 / 81615, rel=REL)
    assert r["operating_margin"]["value"] == pytest.approx(53536 / 81615, rel=REL)
    assert r["net_margin"]["value"] == pytest.approx(58321 / 81615, rel=REL)
    assert r["roe"]["value"] == pytest.approx(58321 / 195474, rel=REL)
    assert r["current_ratio"]["value"] == pytest.approx(150995 / 43884, rel=REL)
    assert r["debt_to_equity"]["value"] == pytest.approx(8470 / 195474, rel=REL)
    assert r["equity_ratio"]["value"] == pytest.approx(195474 / 259474, rel=REL)
    assert r["revenue_growth_qoq"]["status"] == "not_computable"  # no previous filing given


def test_ytd_values_never_used(nvda_facts, latest, previous):
    results = compute_ratios(nvda_facts, latest, previous)
    ytd = {177_837_000_000, 133_299_000_000, 117_270_000_000, 118_010_000_000}
    for r in results:
        for inp in r["inputs"]:
            assert inp["value"] not in ytd
            if inp["start"]:
                assert inp["start"] == "2026-04-27" or inp["start"] in ("2025-04-28", "2026-01-26")
    gm = by_key(results)["gross_margin"]
    assert gm["value"] == pytest.approx(72142 / 96221, rel=REL)


def test_inputs_carry_sources(nvda_facts, latest, previous):
    r = by_key(compute_ratios(nvda_facts, latest, previous))
    expected = {
        "revenue_growth_yoy": ["Revenues", "Revenues"],
        "gross_margin": ["GrossProfit", "Revenues"],
        "operating_margin": ["OperatingIncomeLoss", "Revenues"],
        "net_margin": ["NetIncomeLoss", "Revenues"],
        "roe": ["NetIncomeLoss", "StockholdersEquity"],
        "current_ratio": ["AssetsCurrent", "LiabilitiesCurrent"],
        "debt_to_equity": ["LongTermDebt", "StockholdersEquity"],
        "equity_ratio": ["StockholdersEquity", "Assets"],
    }
    for key, concepts in expected.items():
        assert [i["concept"] for i in r[key]["inputs"]] == concepts, key
        for inp in r[key]["inputs"]:
            assert inp["accession"] == LATEST_ACCN
            assert inp["url"] == LATEST_URL
            assert inp["unit"] == "USD" and inp["form"] == "10-Q" and inp["filed"] == "2026-08-26"
            if key != "revenue_growth_yoy" or inp is r[key]["inputs"][0]:
                assert inp["end"] == "2026-07-26"
    qoq = r["revenue_growth_qoq"]["inputs"]
    assert [i["accession"] for i in qoq] == [LATEST_ACCN, PREVIOUS_ACCN]
    assert [i["value"] for i in qoq] == [96_221_000_000, 81_615_000_000]
    assert qoq[1]["url"].endswith("/000104581026000052/nvda-20260426.htm")
    assert r["revenue_growth_yoy"]["inputs"][1]["value"] == 46_743_000_000


def test_missing_tag_is_not_reported(nvda_facts, latest):
    facts = {"facts": {"us-gaap": {k: v for k, v in nvda_facts["facts"]["us-gaap"].items()
                                   if k != "OperatingIncomeLoss"}}}
    r = by_key(compute_ratios(facts, latest))
    op = r["operating_margin"]
    assert op["status"] == "not_reported" and op["status_he"] == NOT_REPORTED_HE
    assert op["value"] is None
    assert r["net_margin"]["status"] == "ok"


def test_everything_missing():
    filing = make_filing()
    for r in compute_ratios({"facts": {"us-gaap": {}}}, filing):
        if r["key"] == "revenue_growth_qoq":
            assert r["status"] == "not_computable"
        else:
            assert (r["status"], r["status_he"], r["value"]) == ("not_reported", "לא דווח", None), r["key"]


def test_gross_profit_fallback_from_cost():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("Revenues", "2026-04-01", "2026-06-30", 1000),
        ("CostOfGoodsAndServicesSold", "2026-04-01", "2026-06-30", 600),
    ])
    gm = by_key(compute_ratios(facts, filing))["gross_margin"]
    assert gm["status"] == "ok" and gm["value"] == pytest.approx(0.4)
    assert [i["concept"] for i in gm["inputs"]] == ["Revenues", "CostOfGoodsAndServicesSold"]
    assert "CostOfGoodsAndServicesSold" in gm["formula"]


def test_cost_of_revenue_preferred_over_cogs_in_fallback():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("Revenues", "2026-04-01", "2026-06-30", 1000),
        ("CostOfRevenue", "2026-04-01", "2026-06-30", 700),
        ("CostOfGoodsAndServicesSold", "2026-04-01", "2026-06-30", 600),
    ])
    assert by_key(compute_ratios(facts, filing))["gross_margin"]["value"] == pytest.approx(0.3)


def test_gross_margin_not_reported_without_gross_profit_or_cost():
    filing = make_filing()
    facts = make_facts(filing.accession, [("Revenues", "2026-04-01", "2026-06-30", 1000)])
    gm = by_key(compute_ratios(facts, filing))["gross_margin"]
    assert gm["status"] == "not_reported" and gm["status_he"] == "לא דווח"


def test_zero_revenue_not_computable():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("Revenues", "2026-04-01", "2026-06-30", 0),
        ("GrossProfit", "2026-04-01", "2026-06-30", 0),
        ("NetIncomeLoss", "2026-04-01", "2026-06-30", -5),
        ("Revenues", "2025-04-01", "2025-06-30", 0),
    ])
    r = by_key(compute_ratios(facts, filing))
    for key in ("gross_margin", "net_margin", "revenue_growth_yoy"):
        assert r[key]["status"] == "not_computable", key
        assert r[key]["status_he"].startswith("לא ניתן לחישוב: ")
        assert r[key]["value"] is None


def test_growth_from_zero_prior_not_computable():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("Revenues", "2026-04-01", "2026-06-30", 10), ("Revenues", "2025-04-01", "2025-06-30", 0)])
    assert by_key(compute_ratios(facts, filing))["revenue_growth_yoy"]["status"] == "not_computable"


@pytest.mark.parametrize("equity", [-50, 0])
def test_non_positive_equity(equity):
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("NetIncomeLoss", "2026-04-01", "2026-06-30", 10),
        ("StockholdersEquity", None, "2026-06-30", equity),
        ("Assets", None, "2026-06-30", 200),
        ("LongTermDebt", None, "2026-06-30", 80),
    ])
    r = by_key(compute_ratios(facts, filing))
    for key in ("roe", "debt_to_equity"):
        assert r[key]["status"] == "not_computable" and r[key]["value"] is None
        assert "הון" in r[key]["status_he"]
    if equity < 0:
        assert r["equity_ratio"]["status"] == "ok"
        assert r["equity_ratio"]["value"] == pytest.approx(-0.25)


def test_zero_assets_and_zero_current_liabilities():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("StockholdersEquity", None, "2026-06-30", 5), ("Assets", None, "2026-06-30", 0),
        ("AssetsCurrent", None, "2026-06-30", 5), ("LiabilitiesCurrent", None, "2026-06-30", 0),
    ])
    r = by_key(compute_ratios(facts, filing))
    assert r["equity_ratio"]["status"] == "not_computable"
    assert r["current_ratio"]["status"] == "not_computable"


def test_debt_components_all_listed_in_inputs():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("StockholdersEquity", None, "2026-06-30", 100),
        ("LongTermDebtNoncurrent", None, "2026-06-30", 40),
        ("LongTermDebtCurrent", None, "2026-06-30", 10),
        ("ShortTermBorrowings", None, "2026-06-30", 10),
    ])
    de = by_key(compute_ratios(facts, filing))["debt_to_equity"]
    assert de["value"] == pytest.approx(0.6)
    assert [i["concept"] for i in de["inputs"]] == [
        "LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "StockholdersEquity"]


def test_debt_not_reported():
    filing = make_filing()
    facts = make_facts(filing.accession, [("StockholdersEquity", None, "2026-06-30", 100)])
    de = by_key(compute_ratios(facts, filing))["debt_to_equity"]
    assert de["status"] == "not_reported" and de["status_he"] == "לא דווח"


def test_annual_10k_uses_annual_band_and_period_days():
    filing = make_filing(form="10-K", report_date="2025-12-31", accession="0000000002-26-000001")
    facts = make_facts(filing.accession, [
        ("Revenues", "2025-10-01", "2025-12-31", 250),
        ("Revenues", "2025-01-01", "2025-12-31", 1000),
        ("Revenues", "2024-01-01", "2024-12-31", 800),
        ("GrossProfit", "2025-01-01", "2025-12-31", 400),
    ])
    r = by_key(compute_ratios(facts, filing))
    assert r["revenue_growth_yoy"]["value"] == pytest.approx(0.25)
    assert r["gross_margin"]["value"] == pytest.approx(0.4)
    assert r["gross_margin"]["period_days"] == 364


def test_qoq_not_computable_when_previous_is_10k(nvda_facts, latest):
    tenk = make_filing(form="10-K", accession=PREVIOUS_ACCN, report_date="2026-04-26")
    qoq = by_key(compute_ratios(nvda_facts, latest, tenk))["revenue_growth_qoq"]
    assert qoq["status"] == "not_computable"
    assert qoq["status_he"].startswith("לא ניתן לחישוב: ")
    assert qoq["value"] is None


def test_qoq_without_previous(nvda_facts, latest):
    assert by_key(compute_ratios(nvda_facts, latest))["revenue_growth_qoq"]["status"] == "not_computable"


def test_qoq_previous_revenue_missing_is_not_reported(nvda_facts, latest):
    other = make_filing(accession="1111111111-26-000001", report_date="2026-04-26")
    assert by_key(compute_ratios(nvda_facts, latest, other))["revenue_growth_qoq"]["status"] == "not_reported"


def test_empty_report_date_filing_still_computes(nvda_facts, latest):
    from dataclasses import replace
    r = by_key(compute_ratios(nvda_facts, replace(latest, report_date="")))
    assert r["gross_margin"]["value"] == pytest.approx(72142 / 96221, rel=REL)
    assert r["revenue_growth_yoy"]["value"] == pytest.approx(96221 / 46743 - 1, rel=REL)


def test_analyze_orchestrates(nvda_client):
    result = analyze(" nvda ", nvda_client)
    assert (result["company"], result["cik"], result["ticker"]) == ("NVIDIA CORP", 1045810, "NVDA")
    assert result["latest"]["filing"]["accession"] == LATEST_ACCN
    assert result["previous"]["filing"]["accession"] == PREVIOUS_ACCN
    gm = by_key(result["latest"]["ratios"])["gross_margin"]
    assert gm["value"] == pytest.approx(72142 / 96221, rel=REL)
    # previous quarter's own QoQ compares against the 10-K, so it is not computable
    assert by_key(result["previous"]["ratios"])["revenue_growth_qoq"]["status"] == "not_computable"


def _with_pending(nvda_submissions, extra_accn="0009999999-26-000001"):
    import copy
    subs = copy.deepcopy(nvda_submissions)
    recent = subs["filings"]["recent"]
    for col, val in (("form", "10-Q"), ("accessionNumber", extra_accn), ("filingDate", "2026-10-20"),
                     ("reportDate", "2026-09-27"), ("primaryDocument", "nvda-20260927.htm")):
        recent[col].insert(0, val)
    return subs


def _client_with(subs, nvda_facts, fake_time):
    from backend import sec_client
    from backend.sec_client import SecClient
    from tests.conftest import NVDA_CIK, TEST_UA, TICKERS, FakeResponse, FakeSession
    return SecClient(TEST_UA, session=FakeSession({
        sec_client.TICKERS_URL: FakeResponse(200, TICKERS),
        sec_client.SUBMISSIONS_URL.format(cik=NVDA_CIK): FakeResponse(200, subs),
        sec_client.COMPANY_FACTS_URL.format(cik=NVDA_CIK): FakeResponse(200, nvda_facts),
    }))


def test_analyze_normal_has_no_pending(nvda_client):
    result = analyze("NVDA", nvda_client)
    assert result["pending_filings"] == [] and result["notice_he"] is None


def test_analyze_skips_filing_without_facts(nvda_submissions, nvda_facts, fake_time):
    client = _client_with(_with_pending(nvda_submissions), nvda_facts, fake_time)
    result = analyze("NVDA", client)
    assert result["latest"]["filing"]["accession"] == LATEST_ACCN
    assert result["previous"]["filing"]["accession"] == PREVIOUS_ACCN
    assert [x["accession"] for x in result["pending_filings"]] == ["0009999999-26-000001"]
    assert "2026-10-20" in result["notice_he"] and "10-Q" in result["notice_he"]
    gm = by_key(result["latest"]["ratios"])["gross_margin"]
    assert gm["value"] == pytest.approx(72142 / 96221, rel=REL)


def test_analyze_no_facts_at_all(nvda_submissions, fake_time):
    client = _client_with(nvda_submissions, {"facts": {"us-gaap": {}}}, fake_time)
    result = analyze("NVDA", client)
    assert result["latest"]["filing"]["accession"] == LATEST_ACCN
    assert result["notice_he"] == "הנתונים המובנים של SEC לחברה הזו עדיין לא זמינים."
    assert result["pending_filings"] == []
    assert all(r["status"] != "ok" for r in result["latest"]["ratios"])


def test_bank_revenue_tag_fallback():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("RevenuesNetOfInterestExpense", "2026-04-01", "2026-06-30", 500),
        ("NetIncomeLoss", "2026-04-01", "2026-06-30", 100),
    ])
    nm = by_key(compute_ratios(facts, filing))["net_margin"]
    assert nm["value"] == pytest.approx(0.2)
    assert nm["inputs"][1]["concept"] == "RevenuesNetOfInterestExpense"
