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
        "roe": 192880 / 228984,
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
    assert r["roe"]["value"] == pytest.approx(159613 / 195474, rel=REL)
    assert r["current_ratio"]["value"] == pytest.approx(150995 / 43884, rel=REL)
    assert r["debt_to_equity"]["value"] == pytest.approx(8470 / 195474, rel=REL)
    assert r["equity_ratio"]["value"] == pytest.approx(195474 / 259474, rel=REL)
    assert r["revenue_growth_qoq"]["status"] == "not_computable"  # no previous filing given


def test_ytd_values_never_used(nvda_facts, latest, previous):
    results = compute_ratios(nvda_facts, latest, previous)
    ytd = {177_837_000_000, 133_299_000_000, 117_270_000_000, 118_010_000_000}
    for r in results:
        if r["key"] == "roe":  # TTM deliberately uses YTD parts
            continue
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
    roe = r["roe"]
    assert [i["concept"] for i in roe["inputs"]] == ["NetIncomeLoss"] * 3 + ["StockholdersEquity"]
    assert [i["accession"] for i in roe["inputs"]] == ["0001045810-26-000021", LATEST_ACCN, LATEST_ACCN, LATEST_ACCN]
    assert roe["inputs"][0]["url"].endswith("/data/1045810/000104581026000021")
    assert roe["period_days"] is None and roe["period_he"] == "12 חודשים אחרונים"
    assert "ROE TTM" in roe["name_he"]
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
    filing = make_filing(form="10-K", report_date="2026-06-30")
    facts = make_facts(filing.accession, [
        ("NetIncomeLoss", "2025-07-01", "2026-06-30", 10),
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


def test_all_results_have_uniform_schema(nvda_facts, latest, previous):
    for r in compute_ratios(nvda_facts, latest, previous):
        assert "note_he" in r and "period_he" in r
        assert r["note_he"] is None
        assert r["period_he"] == ("12 חודשים אחרונים" if r["key"] == "roe" else None)


def test_roe_missing_components_not_reported(nvda_facts, latest):
    import copy
    facts = copy.deepcopy(nvda_facts)
    ni = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
    ni[:] = [x for x in ni if x["form"] != "10-K"]  # only the DEF 14A FY fact remains
    roe = by_key(compute_ratios(facts, latest))["roe"]
    assert roe["status"] == "not_reported" and roe["status_he"] == "לא דווח" and roe["value"] is None
    assert roe["missing"] == ["רווח נקי שנתי"]


def test_roe_10k_uses_annual_income():
    filing = make_filing(form="10-K", report_date="2025-12-31", accession="0000000002-26-000001")
    facts = make_facts(filing.accession, [
        ("NetIncomeLoss", "2025-01-01", "2025-12-31", 150),
        ("StockholdersEquity", None, "2025-12-31", 1000),
    ])
    roe = by_key(compute_ratios(facts, filing))["roe"]
    assert roe["value"] == pytest.approx(0.15) and len(roe["inputs"]) == 2


def test_roe_10q_missing_prior_ytd():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("NetIncomeLoss", "2026-01-01", "2026-06-30", 50),
        ("NetIncomeLoss", "2025-01-01", "2025-12-31", 100, "0000000009-26-000001", "10-K"),
        ("StockholdersEquity", None, "2026-06-30", 1000),
    ])
    roe = by_key(compute_ratios(facts, filing))["roe"]
    assert roe["status"] == "not_reported"
    assert roe["missing"] == ["רווח נקי מתחילת השנה הקודמת"]


LEASE_FACTS = [
    ("StockholdersEquity", None, "2026-06-30", 1000),
    ("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", None, "2026-06-30", 400),
    ("ShortTermBorrowings", None, "2026-06-30", 100),
]


def test_lease_note_on_debt_to_equity_only():
    filing = make_filing()
    r = by_key(compute_ratios(make_facts(filing.accession, LEASE_FACTS), filing))
    assert r["debt_to_equity"]["value"] == pytest.approx(0.5)
    assert r["debt_to_equity"]["note_he"] == "החוב כולל התחייבויות חכירה מימונית (finance leases), כפי שדווחו בדוח."
    assert all(x["note_he"] is None for k, x in r.items() if k != "debt_to_equity")


def test_no_lease_note_for_plain_long_term_debt():
    filing = make_filing()
    facts = make_facts(filing.accession, [("StockholdersEquity", None, "2026-06-30", 1000),
                                          ("LongTermDebt", None, "2026-06-30", 400)])
    assert by_key(compute_ratios(facts, filing))["debt_to_equity"]["note_he"] is None


BANK_FACTS = [
    ("RevenuesNetOfInterestExpense", "2026-04-01", "2026-06-30", 500),
    ("GrossProfit", "2026-04-01", "2026-06-30", 400),
    ("OperatingIncomeLoss", "2026-04-01", "2026-06-30", 200),
    ("NetIncomeLoss", "2026-04-01", "2026-06-30", 100),
    ("AssetsCurrent", None, "2026-06-30", 10), ("LiabilitiesCurrent", None, "2026-06-30", 5),
    ("StockholdersEquity", None, "2026-06-30", 1000), ("Assets", None, "2026-06-30", 5000),
    ("LongTermDebt", None, "2026-06-30", 400),
]


def test_bank_ratios_not_applicable():
    filing = make_filing()
    facts = make_facts(filing.accession, BANK_FACTS)
    r = by_key(compute_ratios(facts, filing, is_bank=True))
    assert [x["key"] for x in compute_ratios(facts, filing, is_bank=True)] == KEYS
    for key in ("gross_margin", "operating_margin", "current_ratio", "debt_to_equity"):
        assert r[key]["status"] == "not_applicable"
        assert r[key]["status_he"] == "לא רלוונטי לבנקים"
        assert r[key]["value"] is None and r[key]["inputs"] == []
    assert r["net_margin"]["value"] == pytest.approx(0.2)
    assert r["equity_ratio"]["value"] == pytest.approx(0.2)


def test_non_bank_unchanged():
    filing = make_filing()
    r = by_key(compute_ratios(make_facts(filing.accession, BANK_FACTS), filing))
    assert r["gross_margin"]["value"] == pytest.approx(0.8)
    assert r["debt_to_equity"]["value"] == pytest.approx(0.4)
    assert r["current_ratio"]["value"] == pytest.approx(2)


def test_formulas_use_actual_concepts():
    filing = make_filing()
    r = by_key(compute_ratios(make_facts(filing.accession, BANK_FACTS), filing))
    for key in ("gross_margin", "operating_margin", "net_margin"):
        assert r[key]["formula"].endswith("/ RevenuesNetOfInterestExpense"), key
    assert r["operating_margin"]["formula"].startswith("OperatingIncomeLoss")


@pytest.mark.parametrize("sic,expected", [("6022", True), ("3674", False), (None, False), ("zzz", False)])
def test_analyze_bank_flag(nvda_submissions, nvda_facts, fake_time, sic, expected):
    import copy
    subs = copy.deepcopy(nvda_submissions)
    if sic is None:
        subs.pop("sic", None)
    else:
        subs["sic"] = sic
    result = analyze("NVDA", _client_with(subs, nvda_facts, fake_time))
    assert result["is_bank"] is expected and result["sic"] == sic
    statuses = {r["key"]: r["status"] for r in result["latest"]["ratios"]}
    assert (statuses["gross_margin"] == "not_applicable") is expected
    assert (by_key(result["previous"]["ratios"])["current_ratio"]["status"] == "not_applicable") is expected
