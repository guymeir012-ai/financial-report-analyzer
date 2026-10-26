import pytest

from backend.filings import FilingFacts, latest_and_previous, list_periodic_filings
from backend import filings as f
from tests.conftest import LATEST_ACCN, LATEST_URL, PREVIOUS_ACCN, make_facts, make_filing


def test_lists_only_periodic_filings_newest_first(nvda_filings):
    assert [x.accession for x in nvda_filings] == [LATEST_ACCN, PREVIOUS_ACCN, "0001045810-26-000021"]
    assert [x.form for x in nvda_filings] == ["10-Q", "10-Q", "10-K"]
    assert nvda_filings[0].url == LATEST_URL
    assert nvda_filings[0].report_date == "2026-07-26"


def test_amendments_and_other_forms_skipped():
    subs = {"filings": {"recent": {
        "form": ["10-Q/A", "8-K", "10-K/A", "10-Q", "4"],
        "accessionNumber": ["a-1", "a-2", "a-3", "a-4", "a-5"],
        "filingDate": ["2026-05-05", "2026-05-04", "2026-05-03", "2026-05-01", "2026-05-02"],
        "reportDate": ["2026-03-31"] * 5,
        "primaryDocument": ["d.htm"] * 5,
    }}}
    result = list_periodic_filings(subs, 7)
    assert [x.accession for x in result] == ["a-4"]


def test_sorted_by_filing_date_not_input_order():
    subs = {"filings": {"recent": {
        "form": ["10-Q", "10-K"], "accessionNumber": ["old-1", "new-1"],
        "filingDate": ["2025-01-01", "2026-01-01"], "reportDate": ["2024-12-31", "2025-12-31"],
        "primaryDocument": ["a.htm", "b.htm"],
    }}}
    assert [x.accession for x in list_periodic_filings(subs, 7)] == ["new-1", "old-1"]


@pytest.mark.parametrize("subs", [{}, {"filings": {}}, {"filings": {"recent": {}}}])
def test_company_without_filings(subs):
    assert list_periodic_filings(subs, 7) == []
    assert latest_and_previous([]) == (None, None)


def test_latest_and_previous(nvda_filings):
    latest, previous = latest_and_previous(nvda_filings)
    assert (latest.accession, previous.accession) == (LATEST_ACCN, PREVIOUS_ACCN)
    assert latest_and_previous(nvda_filings[:1]) == (nvda_filings[0], None)


def test_ytd_facts_not_selected(nvda_facts, latest):
    rev = FilingFacts(nvda_facts, latest).duration(f.REVENUE_TAGS)
    assert rev.value == 96_221_000_000
    assert (rev.start, rev.end) == ("2026-04-27", "2026-07-26")
    assert rev.value != 177_837_000_000


def test_prior_year_comparative(nvda_facts, latest, previous):
    assert FilingFacts(nvda_facts, latest).duration(f.REVENUE_TAGS, prior_year=True).value == 46_743_000_000
    assert FilingFacts(nvda_facts, previous).duration(f.REVENUE_TAGS, prior_year=True).value == 44_062_000_000


def test_fact_from_other_filing_ignored(nvda_facts):
    other = make_filing(accession="9999999999-26-000001", report_date="2026-07-26")
    assert FilingFacts(nvda_facts, other).duration(f.REVENUE_TAGS) is None


def test_sourced_value_fields(nvda_facts, latest):
    v = FilingFacts(nvda_facts, latest).instant(f.EQUITY_TAGS)
    assert v.to_dict() == {
        "concept": "StockholdersEquity", "value": 228_984_000_000, "unit": "USD",
        "start": None, "end": "2026-07-26", "accession": LATEST_ACCN, "form": "10-Q",
        "filed": "2026-08-26", "url": LATEST_URL,
    }


def test_instant_ignores_facts_with_start_or_other_end():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("Assets", "2026-04-01", "2026-06-30", 1),
        ("Assets", None, "2026-03-31", 2),
        ("Assets", None, "2026-06-30", 3),
    ])
    assert FilingFacts(facts, filing).instant(f.ASSETS_TAGS).value == 3


def test_10q_band_boundaries():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("Revenues", "2026-04-11", "2026-06-30", 80),   # 80 days
        ("NetIncomeLoss", "2026-04-10", "2026-06-30", 1),  # 81 days is fine
        ("OperatingIncomeLoss", "2026-04-12", "2026-06-30", 1),  # 79 days: out
        ("GrossProfit", "2026-03-22", "2026-06-30", 1),  # 100 days
        ("CostOfRevenue", "2026-03-21", "2026-06-30", 1),  # 101 days: out
    ])
    ff = FilingFacts(facts, filing)
    assert ff.duration(f.REVENUE_TAGS).value == 80
    assert ff.duration(f.NET_INCOME_TAGS) is not None
    assert ff.duration(f.OPERATING_INCOME_TAGS) is None
    assert ff.duration(f.GROSS_PROFIT_TAGS) is not None
    assert ff.duration(f.COST_TAGS) is None


def test_10k_picks_annual_not_quarter():
    filing = make_filing(form="10-K", report_date="2025-12-31", accession="0000000002-26-000001")
    facts = make_facts(filing.accession, [
        ("Revenues", "2025-10-01", "2025-12-31", 250),  # Q4
        ("Revenues", "2025-01-01", "2025-12-31", 1000),  # FY
        ("Revenues", "2024-01-01", "2024-12-31", 800),  # prior FY
    ])
    ff = FilingFacts(facts, filing)
    assert ff.duration(f.REVENUE_TAGS).value == 1000
    assert ff.duration(f.REVENUE_TAGS, prior_year=True).value == 800


def test_tag_fallback_order():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("SalesRevenueNet", "2026-04-01", "2026-06-30", 5),
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "2026-04-01", "2026-06-30", 7),
    ])
    assert FilingFacts(facts, filing).duration(f.REVENUE_TAGS).concept == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_empty_report_date_falls_back_to_max_end():
    filing = make_filing(report_date="")
    facts = make_facts(filing.accession, [
        ("Revenues", "2026-04-01", "2026-06-30", 100),
        ("Revenues", "2026-01-01", "2026-06-30", 190),
        ("Revenues", "2025-04-01", "2025-06-30", 90),
        ("Assets", None, "2026-06-30", 5),
    ])
    ff = FilingFacts(facts, filing)
    assert str(ff.report_date) == "2026-06-30"
    assert ff.duration(f.REVENUE_TAGS).value == 100
    assert ff.duration(f.REVENUE_TAGS, prior_year=True).value == 90
    assert ff.instant(f.ASSETS_TAGS).value == 5


def test_empty_report_date_without_facts_selects_nothing():
    filing = make_filing(report_date="")
    ff = FilingFacts({"facts": {"us-gaap": {}}}, filing)
    assert ff.duration(f.REVENUE_TAGS) is None and ff.instant(f.ASSETS_TAGS) is None


def test_non_usd_and_non_numeric_ignored():
    filing = make_filing()
    facts = make_facts(filing.accession, [("Revenues", "2026-04-01", "2026-06-30", "n/a")])
    facts["facts"]["us-gaap"]["Assets"] = {"units": {"EUR": [
        {"end": "2026-06-30", "val": 9, "accn": filing.accession, "form": "10-Q", "filed": "x"}]}}
    ff = FilingFacts(facts, filing)
    assert ff.duration(f.REVENUE_TAGS) is None
    assert ff.instant(f.ASSETS_TAGS) is None


def test_debt_chain_a_adds_short_term_borrowings():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("LongTermDebt", None, "2026-06-30", 100), ("ShortTermBorrowings", None, "2026-06-30", 20),
        ("LongTermDebtNoncurrent", None, "2026-06-30", 1),
    ])
    parts, formula, lease = FilingFacts(facts, filing).debt()
    assert lease is False
    assert [p.concept for p in parts] == ["LongTermDebt", "ShortTermBorrowings"]
    assert formula == "LongTermDebt + ShortTermBorrowings"


def test_debt_chain_b_components():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("LongTermDebtNoncurrent", None, "2026-06-30", 90), ("DebtCurrent", None, "2026-06-30", 10),
        ("ShortTermBorrowings", None, "2026-06-30", 5),
    ])
    parts, _, _ = FilingFacts(facts, filing).debt()
    assert [(p.concept, p.value) for p in parts] == [
        ("LongTermDebtNoncurrent", 90), ("DebtCurrent", 10), ("ShortTermBorrowings", 5)]


def test_debt_chain_b_needs_current_portion():
    filing = make_filing()
    facts = make_facts(filing.accession, [("LongTermDebtNoncurrent", None, "2026-06-30", 90)])
    assert FilingFacts(facts, filing).debt() is None


def _debt(rows):
    filing = make_filing()
    return FilingFacts(make_facts(filing.accession, rows), filing).debt()


def test_debt_chain_c_lease_inclusive():
    parts, formula, lease = _debt([
        ("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", None, "2026-06-30", 43_558),
        ("NotesAndLoansPayable", None, "2026-06-30", 332),
    ])
    assert [(p.concept, p.value) for p in parts] == [
        ("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", 43_558),
        ("NotesAndLoansPayable", 332)]
    assert lease is True
    assert "NotesAndLoansPayable" in formula


def test_debt_chain_d_needs_both_parts():
    rows = [("LongTermDebtAndCapitalLeaseObligations", None, "2026-06-30", 90)]
    assert _debt(rows) is None
    parts, _, lease = _debt(rows + [("LongTermDebtAndCapitalLeaseObligationsCurrent", None, "2026-06-30", 10)])
    assert [p.value for p in parts] == [90, 10] and lease is True


def test_short_term_borrowings_beats_notes_and_loans():
    parts, _, _ = _debt([
        ("LongTermDebt", None, "2026-06-30", 100),
        ("ShortTermBorrowings", None, "2026-06-30", 72),
        ("NotesAndLoansPayable", None, "2026-06-30", 5),
    ])
    assert [p.concept for p in parts] == ["LongTermDebt", "ShortTermBorrowings"]


def test_notes_and_loans_used_when_no_short_term_borrowings():
    parts, _, lease = _debt([
        ("LongTermDebt", None, "2026-06-30", 100), ("NotesAndLoansPayable", None, "2026-06-30", 5)])
    assert [p.concept for p in parts] == ["LongTermDebt", "NotesAndLoansPayable"] and lease is False


def test_commercial_paper_alone_is_not_debt():
    assert _debt([("CommercialPaper", None, "2026-06-30", 5)]) is None


def test_is_bank_sic():
    from backend.filings import is_bank_sic
    assert all(is_bank_sic(x) for x in ("6021", 6022, " 6029 ", "6035", "6036"))
    assert not any(is_bank_sic(x) for x in ("3674", None, "", "garbage", "60210"))


def test_ttm_components_nvda(nvda_facts, latest, previous):
    parts = FilingFacts(nvda_facts, latest).ttm_net_income()
    assert [(k, v.value, v.accession) for k, v in parts.items()] == [
        ("annual", 120_067_000_000, "0001045810-26-000021"),
        ("ytd", 118_010_000_000, LATEST_ACCN),
        ("prior_ytd", 45_197_000_000, LATEST_ACCN),
    ]
    assert parts["annual"].url == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021"
    assert parts["annual"].form == "10-K"
    q1 = FilingFacts(nvda_facts, previous).ttm_net_income()
    assert [v.value for v in q1.values()] == [120_067_000_000, 58_321_000_000, 18_775_000_000]


def test_ttm_never_uses_def14a_fact(nvda_facts, latest):
    import copy
    facts = copy.deepcopy(nvda_facts)
    ni = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
    ni[:] = [x for x in ni if x["form"] != "10-K"]
    assert any(x["form"] == "DEF 14A" for x in ni)
    assert FilingFacts(facts, latest).ttm_net_income()["annual"] is None


def test_ttm_uses_single_concept():
    filing = make_filing()
    facts = make_facts(filing.accession, [
        ("NetIncomeLoss", "2026-01-01", "2026-06-30", 50),
        ("ProfitLoss", "2026-01-01", "2026-06-30", 55),
        ("ProfitLoss", "2025-01-01", "2025-06-30", 40),
        ("ProfitLoss", "2025-01-01", "2025-12-31", 100, "0000000009-26-000001", "10-K"),
    ])
    parts = FilingFacts(facts, filing).ttm_net_income()
    assert {p.concept for p in parts.values()} == {"ProfitLoss"}
    assert [p.value for p in parts.values()] == [100, 55, 40]
