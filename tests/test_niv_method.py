import json
from pathlib import Path

import pytest

from backend.niv_method import (
    PeriodPL, classify_transition, compare, efficiency, efficiency_change_points, filing_periods,
    format_usd_he, net_profit_transition, report_analysis, revenue_growth_pct, stage_position,
    turning_lines,
)
from tests.conftest import make_facts, make_filing

FIXTURES = Path(__file__).parent / "fixtures"
CLF_10K_2021 = "0000764065-22-000037"


def clf_filing():
    return make_filing(form="10-K", accession=CLF_10K_2021, report_date="2021-12-31",
                       filing_date="2022-02-11", cik=764065, doc="clf-20211231.htm")


@pytest.fixture(scope="module")
def clf_facts():
    return json.loads((FIXTURES / "clf_companyfacts_min.json").read_text())


@pytest.fixture(scope="module")
def clf(clf_facts):
    return filing_periods(clf_facts, clf_filing())


# ---------- golden test: CLF Q4 2021, Niv's numbers from lesson 13 ----------

def test_clf_quarter_matches_niv(clf):
    prior, current = clf["quarter"]
    # Niv's prior-year Q4 revenue is 2,256M: FY 2020 minus the nine months as last filed before this 10-K
    assert prior.revenue == pytest.approx(2_256_000_000)
    assert current.revenue == pytest.approx(5_346_000_000)
    c = compare(prior, current)
    assert round(c["revenue_growth_pct"]) == 137
    assert round(c["efficiency_prior"] * 100) == 95
    assert round(c["efficiency_current"] * 100) == 79
    assert round(c["efficiency_change_points"]) == 16
    assert c["net_profit"]["text_he"] == "מעבר מרווח נקי של 74 מיליון דולר לרווח נקי של 899 מיליון דולר"


def test_clf_annual_matches_niv(clf):
    prior, current = clf["annual"]
    c = compare(prior, current)
    assert round(c["revenue_growth_pct"]) == 282
    # Niv said 102 (he read 1.026 as "1.02"); exact is 102.65, which rounds to 103
    assert c["efficiency_prior"] * 100 == pytest.approx(102.65, abs=0.01)
    assert int(c["efficiency_prior"] * 100) == 102
    assert round(c["efficiency_current"] * 100) == 80
    assert round(c["efficiency_change_points"]) == 22
    assert c["net_profit"]["text_he"] == "מעבר מהפסד נקי של 81 מיליון דולר לרווח נקי של 3 מיליארד דולר"


def test_clf_annual_stage_4_to_6(clf):
    stage = compare(*clf["annual"])["stage"]
    assert (stage["from_stage"], stage["to_stage"]) == (4, 6)
    assert stage["double_digit_growth"] and stage["more_efficient"]
    assert "קפיצה של 2" in stage["verdict_he"]


def test_clf_quarter_stage_6_to_7(clf):
    # Niv: no fixed basis for a Q4 report, both comparisons are shown
    stage = compare(*clf["quarter"])["stage"]
    assert (stage["from_stage"], stage["to_stage"]) == (6, 7)


def test_clf_sources_point_to_filings(clf):
    _, current = clf["quarter"]
    src = current.sources["revenue"]
    assert src["derived"] == "שנה מלאה פחות תשעה חודשים"
    full, nine = src["values"]
    assert (full["role_he"], full["form"], full["accession"]) == ("שנה מלאה", "10-K", CLF_10K_2021)
    assert (nine["role_he"], nine["form"], nine["end"]) == ("תשעה חודשים ראשונים", "10-Q", "2021-09-30")
    assert full["url"].startswith("https://www.sec.gov/Archives/edgar/data/764065/")
    assert current.sources["net_income"]["values"][0]["concept"] == "ProfitLoss"
    assert current.end == "2021-12-31"


def test_clf_nine_months_come_from_what_was_filed_before_the_report(clf):
    prior, current = clf["quarter"]
    # both nine-month figures are taken from the third 10-Q of 2021, not from later filings
    assert prior.sources["revenue"]["values"][1]["filed"] == "2021-10-26"
    assert current.sources["revenue"]["values"][1]["filed"] == "2021-10-26"


# ---------- any filing: 10-Q, shifted fiscal years, gaps ----------

def test_10q_gives_the_quarter_comparison_only(nvda_facts, latest):
    periods = filing_periods(nvda_facts, latest)
    assert periods["annual"] is None
    prior, current = periods["quarter"]
    assert (prior.revenue, current.revenue) == (46_743_000_000, 96_221_000_000)
    assert (prior.end, current.end) == ("2025-07-27", "2026-07-26")
    c = compare(prior, current)
    assert c["revenue_growth_pct"] == pytest.approx((96_221 / 46_743 - 1) * 100)
    # no CostsAndExpenses in the filing: costs = revenue minus operating income
    assert current.sources["costs"]["derived"] == "הכנסות פחות רווח תפעולי"
    assert c["efficiency_current"] == pytest.approx((96_221 - 63_734) / 96_221)
    assert (c["stage"]["from_stage"], c["stage"]["to_stage"]) == (6, 7)
    assert report_analysis(periods)["highlights"] == []


def shifted_year_facts(nine_months=True):
    """A 52/53-week fiscal year: the two years do not start or end on the same calendar day."""
    accn, q3 = "0000000001-26-000009", "0000000001-25-000007"
    rows = []
    for tag, fy_prior, fy_cur, nine_prior, nine_cur in (
        ("Revenues", 1000e6, 1500e6, 700e6, 1000e6),
        ("OperatingIncomeLoss", -50e6, 120e6, -60e6, 40e6),
        ("NetIncomeLoss", -80e6, 90e6, -70e6, 20e6),
    ):
        rows += [(tag, "2024-01-29", "2025-01-26", fy_prior, accn, "10-K"),
                 (tag, "2025-01-27", "2026-01-25", fy_cur, accn, "10-K")]
        if nine_months:
            rows += [(tag, "2024-01-29", "2024-10-27", nine_prior, q3, "10-Q"),
                     (tag, "2025-01-27", "2025-10-26", nine_cur, q3, "10-Q")]
    filing = make_filing(form="10-K", accession=accn, report_date="2026-01-25", filing_date="2026-03-15")
    return make_facts(accn, rows), filing


def test_10k_with_shifted_fiscal_year():
    periods = filing_periods(*shifted_year_facts())
    prior, current = periods["annual"]
    assert (prior.revenue, current.revenue) == (1000e6, 1500e6)
    q_prior, q_current = periods["quarter"]
    assert (q_prior.revenue, q_current.revenue) == (300e6, 500e6)
    assert (q_prior.net_income, q_current.net_income) == (-10e6, 70e6)
    r = report_analysis(periods)
    # the quarter's operating line was already a profit a year earlier (-50 minus -60), so it is not a turn
    assert {(h["basis"], h["line"]) for h in r["highlights"]} == {
        ("quarter", "net_income"), ("annual", "net_income"), ("annual", "operating_income")}


def test_10k_without_nine_months_has_no_quarter():
    periods = filing_periods(*shifted_year_facts(nine_months=False))
    assert periods["quarter"] is None
    assert list(report_analysis(periods)["comparisons"]) == ["annual"]


def test_net_income_uses_one_concept_for_both_periods():
    accn = "0000000001-26-000001"
    facts = make_facts(accn, [
        ("Revenues", "2026-04-01", "2026-06-30", 200e6), ("Revenues", "2025-04-01", "2025-06-30", 150e6),
        ("ProfitLoss", "2026-04-01", "2026-06-30", 21e6),  # no comparative under this concept
        ("NetIncomeLoss", "2026-04-01", "2026-06-30", 20e6), ("NetIncomeLoss", "2025-04-01", "2025-06-30", 5e6),
    ])
    prior, current = filing_periods(facts, make_filing(accession=accn))["quarter"]
    assert (prior.net_income, current.net_income) == (5e6, 20e6)
    assert current.sources["net_income"]["values"][0]["concept"] == "NetIncomeLoss"


def test_filing_without_facts_gives_empty_periods():
    prior, current = filing_periods({}, make_filing())["quarter"]
    assert current.revenue is None and current.sources == {}
    c = report_analysis({"quarter": (prior, current), "annual": None})["comparisons"]["quarter"]
    assert c["stage"]["to_stage"] is None and c["net_profit"] is None


def test_unknown_growth_and_efficiency_are_not_called_weak():
    s = classify_transition(p(None, None, None, 10), p(100, None, None, 20))
    assert s["to_stage"] == 7
    assert s["notes_he"] == ["אין נתון צמיחה בהכנסות, אי אפשר לבדוק את תנאי השלב.",
                             "אין נתון התייעלות, אי אפשר לבדוק את תנאי השלב."]


# ---------- the calculations ----------

def test_growth_formula():
    assert revenue_growth_pct(150, 100) == pytest.approx(50)
    assert revenue_growth_pct(80, 100) == pytest.approx(-20)


@pytest.mark.parametrize("current,prior", [(None, 100), (100, None), (100, 0), (100, -5)])
def test_growth_missing_or_invalid(current, prior):
    assert revenue_growth_pct(current, prior) is None


def test_efficiency_and_change():
    assert efficiency(79, 100) == pytest.approx(0.79)
    assert efficiency(10, 0) is None
    assert efficiency(None, 100) is None
    assert efficiency_change_points(0.95, 0.79) == pytest.approx(16)
    assert efficiency_change_points(0.79, 0.95) == pytest.approx(-16)
    assert efficiency_change_points(None, 0.5) is None


@pytest.mark.parametrize("amount,text", [
    (74_000_000, "74 מיליון דולר"), (-81_000_000, "81 מיליון דולר"),
    (3_033_000_000, "3 מיליארד דולר"), (1_450_000_000, "1.4 מיליארד דולר"),
    (999_400_000, "999 מיליון דולר"), (1_234_000, "1 מיליון דולר"),
])
def test_format_usd_he(amount, text):
    assert format_usd_he(amount) == text


def test_net_profit_never_percent():
    t = net_profit_transition(-10_000_000, -4_000_000)
    assert t["text_he"] == "מעבר מהפסד נקי של 10 מיליון דולר להפסד נקי של 4 מיליון דולר"
    assert "%" not in t["text_he"]
    assert net_profit_transition(None, 5) is None
    assert net_profit_transition(5_000_000, -2_000_000)["text_he"].endswith("להפסד נקי של 2 מיליון דולר")


# ---------- the 7-stage model ----------

def p(rev, costs, op, net, label="x"):
    return PeriodPL(label, rev, costs, op, net)


def test_position():
    assert stage_position(p(None, None, None, None)) is None
    assert stage_position(p(0, 10, -10, -10)) == 1
    assert stage_position(p(100, 120, -20, -25)) == 4
    assert stage_position(p(100, 90, 10, -2)) == 5
    assert stage_position(p(100, 80, 20, 15)) == 6


def test_operating_turn_is_stage_5():
    s = classify_transition(p(100, 110, -10, -15), p(130, 125, 5, -1))
    assert (s["from_stage"], s["to_stage"]) == (4, 5)
    assert s["verdict_he"] == "עלייה של שלב אחד."


def test_growing_net_profit_is_stage_7_and_staying_is_fine():
    s = classify_transition(p(100, 80, 20, 10), p(120, 90, 30, 20))
    assert (s["from_stage"], s["to_stage"]) == (6, 7)


def test_same_stage_is_not_interesting_for_long():
    s = classify_transition(p(100, 120, -20, -25), p(115, 130, -15, -20))
    assert (s["from_stage"], s["to_stage"]) == (4, 4)
    assert "לא מעניינת ללונג" in s["verdict_he"]


def test_moving_down_is_not_interesting_for_long():
    s = classify_transition(p(100, 80, 20, 15), p(90, 95, -5, -8))
    assert s["to_stage"] < s["from_stage"]
    assert "לא מעניינת ללונג" in s["verdict_he"]


def test_unmet_conditions_are_flagged():
    s = classify_transition(p(100, 80, 20, 10), p(103, 85, 18, 12))
    assert s["to_stage"] == 7
    assert not s["double_digit_growth"] and not s["more_efficient"]
    assert len(s["notes_he"]) == 2


def test_flat_net_profit_is_flagged():
    s = classify_transition(p(100, 80, 20, 12), p(120, 95, 25, 12))
    assert s["to_stage"] == 6
    assert any("לא גדל" in n for n in s["notes_he"])


def test_pre_revenue_and_missing():
    assert classify_transition(p(0, 5, -5, -5), p(0, 6, -6, -6))["to_stage"] == 1
    missing = classify_transition(p(100, 80, 20, 10), p(None, None, None, None))
    assert missing["to_stage"] is None and missing["verdict_he"] is None
    assert classify_transition(p(100, 80, 20, 10), p(120, 90, 30, 20))["joker_checked"] is False


# ---------- a Q4 report: both comparisons, highlights from either (Niv, 22.11.2026) ----------

def test_clf_q4_shows_both_stages_without_choosing(clf):
    r = report_analysis(clf)
    stages = {b: (c["stage"]["from_stage"], c["stage"]["to_stage"]) for b, c in r["comparisons"].items()}
    assert stages == {"quarter": (6, 7), "annual": (4, 6)}


def test_clf_q4_highlights_the_annual_turn_to_profit(clf):
    r = report_analysis(clf)
    texts = [h["text_he"] for h in r["highlights"]]
    assert "מעבר מהפסד נקי של 81 מיליון דולר לרווח נקי של 3 מיליארד דולר בהשוואה השנתית." in texts
    assert all(h["basis"] == "annual" for h in r["highlights"])
    assert all(h["direction"] == "up" for h in r["highlights"])


def test_turn_in_quarter_only_is_flagged():
    prior, current = p(100, 110, -10, -5), p(130, 120, 10, 4)
    r = report_analysis({"quarter": (prior, current), "annual": (p(400, 380, 20, 10), p(500, 470, 30, 15))})
    assert [(h["basis"], h["line"]) for h in r["highlights"]] == [("quarter", "net_income"), ("quarter", "operating_income")]


def test_turn_down_is_flagged():
    t = turning_lines(p(100e6, 80e6, 20e6, 10e6), p(90e6, 95e6, -5e6, -8e6))
    assert {x["direction"] for x in t} == {"down"}
    assert t[0]["text_he"] == "מעבר מרווח נקי של 10 מיליון דולר להפסד נקי של 8 מיליון דולר"


def test_no_turn_and_missing_quarter():
    assert turning_lines(p(100, 80, 20, 10), p(120, 90, 30, 20)) == []
    assert turning_lines(p(100, 80, None, None), p(120, 90, 30, 20)) == []
    r = report_analysis({"annual": (p(100, 80, 20, 10), p(120, 90, 30, 20)), "quarter": None})
    assert list(r["comparisons"]) == ["annual"] and r["highlights"] == []


# ---------- golden test: Nvidia Q1 FY2024, Niv's numbers from lesson 14 ----------
# The stage of this report is deliberately not asserted: it depends on questions still open with Niv.

NVDA_Q1_FY24 = "0001045810-23-000093"


def nvda_q1_filing():
    return make_filing(form="10-Q", accession=NVDA_Q1_FY24, report_date="2023-04-30",
                       filing_date="2023-05-26", cik=1045810, doc="nvda-20230430.htm")


@pytest.fixture(scope="module")
def nvda_q1():
    facts = json.loads((FIXTURES / "nvda_q1fy24_companyfacts_min.json").read_text())
    return report_analysis(filing_periods(facts, nvda_q1_filing()))["comparisons"]["quarter"]


def test_nvda_revenue_against_the_same_quarter(nvda_q1):
    assert nvda_q1["values"]["revenue"] == (8_288_000_000, 7_192_000_000)
    assert round(nvda_q1["revenue_growth_pct"], 1) == -13.2
    assert nvda_q1["period_ends"] == ("2022-05-01", "2023-04-30")


def test_nvda_revenue_against_the_previous_quarter(nvda_q1):
    previous = nvda_q1["previous_quarter"]
    # the previous quarter was a fourth quarter: full year 26,974 minus nine months 20,923
    assert previous["revenue"] == 6_051_000_000 and previous["end"] == "2023-01-29"
    assert round(previous["revenue_growth_pct"], 1) == 18.9
    full, nine = previous["source"]["values"]
    assert previous["source"]["derived"] == "שנה מלאה פחות תשעה חודשים"
    assert (full["form"], full["value"], nine["form"], nine["value"]) == ("10-K", 26_974_000_000, "10-Q", 20_923_000_000)


def test_nvda_efficiency_adds_cost_of_sales_to_operating_expenses(nvda_q1):
    # (2,857 + 3,563) / 8,288 = 77 cents, against (2,544 + 2,508) / 7,192 = 70 cents
    assert nvda_q1["values"]["costs"] == (2_857_000_000 + 3_563_000_000, 2_544_000_000 + 2_508_000_000)
    assert round(nvda_q1["efficiency_prior"] * 100) == 77
    assert round(nvda_q1["efficiency_current"] * 100) == 70
    assert round(nvda_q1["efficiency_change_points"]) == 7
    source = nvda_q1["sources"]["current"]["costs"]
    assert source["derived"] == "עלות מכר ועוד הוצאות תפעול"
    assert [v["concept"] for v in source["values"]] == ["CostOfRevenue", "OperatingExpenses"]


def test_nvda_operating_expenses_are_taken_as_reported(nvda_q1):
    # the comparable quarter's 3,563 includes a one-time charge; nothing is adjusted (open question for Niv)
    assert nvda_q1["sources"]["prior"]["costs"]["values"][1]["value"] == 3_563_000_000


def test_nvda_net_profit(nvda_q1):
    assert nvda_q1["values"]["net_income"] == (1_618_000_000, 2_043_000_000)
    assert nvda_q1["net_profit"]["text_he"] == "מעבר מרווח נקי של 1.6 מיליארד דולר לרווח נקי של 2 מיליארד דולר"


def test_nvda_guidance_growth(nvda_q1):
    from backend.niv_method import guidance_growth_pct

    current_revenue = nvda_q1["values"]["revenue"][1]
    assert round(guidance_growth_pct(11_000_000_000, current_revenue)) == 53


# ---------- guidance growth ----------

def test_guidance_growth_formula_and_gaps():
    from backend.niv_method import guidance_growth_pct

    assert guidance_growth_pct(900_000, 1_000_000) == pytest.approx(-10)
    assert guidance_growth_pct(1_000_000, 1_000_000) == pytest.approx(0)
    for guidance, revenue in ((None, 100), (100, None), (100, 0), (100, -5)):
        assert guidance_growth_pct(guidance, revenue) is None


# ---------- the costs line: never count the cost of sales twice ----------

def quarter_facts(rows):
    accn = "0000000001-26-000001"
    full = []
    for tag, current, prior in rows:
        if current is not None:
            full.append((tag, "2026-04-01", "2026-06-30", current))
        if prior is not None:
            full.append((tag, "2025-04-01", "2025-06-30", prior))
    return filing_periods(make_facts(accn, full), make_filing(accession=accn))["quarter"]


def test_costs_are_the_sum_when_it_agrees_with_operating_income():
    prior, current = quarter_facts([("Revenues", 1000e6, 800e6), ("CostOfRevenue", 400e6, 350e6),
                                    ("OperatingExpenses", 300e6, 300e6), ("OperatingIncomeLoss", 300e6, 150e6)])
    assert (prior.costs, current.costs) == (650e6, 700e6)
    assert current.sources["costs"]["derived"] == "עלות מכר ועוד הוצאות תפעול"


def test_costs_are_operating_expenses_alone_when_they_include_the_cost_of_sales():
    _, current = quarter_facts([("Revenues", 1000e6, 800e6), ("CostOfRevenue", 400e6, 350e6),
                                ("OperatingExpenses", 700e6, 650e6), ("OperatingIncomeLoss", 300e6, 150e6)])
    assert current.costs == 700e6
    assert current.sources["costs"]["derived"] == "הוצאות התפעול כבר כוללות את עלות המכר"
    assert [v["concept"] for v in current.sources["costs"]["values"]] == ["OperatingExpenses"]


def test_costs_fall_back_to_the_difference_when_the_lines_do_not_add_up():
    # another operating item sits between the two lines and operating income
    _, current = quarter_facts([("Revenues", 1000e6, 800e6), ("CostOfRevenue", 400e6, 350e6),
                                ("OperatingExpenses", 250e6, 250e6), ("OperatingIncomeLoss", 300e6, 150e6)])
    assert current.costs == 700e6
    assert current.sources["costs"]["derived"] == "הכנסות פחות רווח תפעולי"


def test_costs_are_not_guessed_without_an_operating_income_line():
    prior, current = quarter_facts([("Revenues", 1000e6, 800e6), ("CostOfRevenue", 400e6, 350e6),
                                    ("OperatingExpenses", 300e6, 300e6), ("NetIncomeLoss", 50e6, 20e6)])
    assert prior.costs is None and current.costs is None and "costs" not in current.sources
    assert compare(prior, current)["efficiency_current"] is None


def test_reported_total_costs_win_over_everything():
    _, current = quarter_facts([("Revenues", 1000e6, 800e6), ("CostsAndExpenses", 690e6, 640e6),
                                ("CostOfRevenue", 400e6, 350e6), ("OperatingExpenses", 300e6, 300e6),
                                ("OperatingIncomeLoss", 300e6, 150e6)])
    assert current.costs == 690e6 and current.sources["costs"]["derived"] is None


# ---------- the previous quarter, kept apart from the same quarter ----------

def test_previous_quarter_reported_in_its_own_10q(nvda_facts, latest):
    periods = filing_periods(nvda_facts, latest)
    previous = periods["previous_quarter"]
    assert (previous["revenue"], previous["end"]) == (81_615_000_000, "2026-04-26")
    assert previous["source"]["derived"] is None
    assert previous["source"]["values"][0]["role_he"] == "הרבעון הקודם"
    c = report_analysis(periods)["comparisons"]["quarter"]
    assert c["previous_quarter"]["revenue_growth_pct"] == pytest.approx((96_221 / 81_615 - 1) * 100)
    # a different comparison: it never changes the same-quarter numbers or the stage
    assert c["revenue_growth_pct"] == pytest.approx((96_221 / 46_743 - 1) * 100)
    assert (c["stage"]["from_stage"], c["stage"]["to_stage"]) == (6, 7)


def test_previous_quarter_of_a_10k_is_the_third_quarter():
    facts, filing = shifted_year_facts()
    facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        {"start": "2025-07-28", "end": "2025-10-26", "val": 400e6, "accn": "0000000001-25-000007",
         "form": "10-Q", "filed": "2025-11-20"})
    periods = filing_periods(facts, filing)
    assert (periods["previous_quarter"]["revenue"], periods["previous_quarter"]["end"]) == (400e6, "2025-10-26")
    quarter = report_analysis(periods)["comparisons"]["quarter"]
    assert quarter["previous_quarter"]["revenue_growth_pct"] == pytest.approx(25)
    assert "previous_quarter" not in report_analysis(periods)["comparisons"]["annual"]


def test_previous_quarter_missing_is_none(clf):
    # the fixture has no three-month third quarter
    assert clf["previous_quarter"] is None
    assert "previous_quarter" not in report_analysis(clf)["comparisons"]["quarter"]


def test_previous_quarter_under_another_revenue_concept():
    accn = "0000000001-26-000001"
    facts = make_facts(accn, [
        ("Revenues", "2026-04-01", "2026-06-30", 200e6), ("Revenues", "2025-04-01", "2025-06-30", 150e6),
        ("SalesRevenueNet", "2026-01-01", "2026-03-31", 160e6, "0000000001-26-000000"),
    ])
    previous = filing_periods(facts, make_filing(accession=accn))["previous_quarter"]
    assert previous["revenue"] == 160e6 and previous["source"]["values"][0]["concept"] == "SalesRevenueNet"


def test_previous_quarter_ignores_periods_that_do_not_touch_the_current_one():
    accn = "0000000001-26-000001"
    facts = make_facts(accn, [
        ("Revenues", "2026-04-01", "2026-06-30", 200e6), ("Revenues", "2025-04-01", "2025-06-30", 150e6),
        ("Revenues", "2025-10-01", "2025-12-31", 170e6, "0000000001-26-000000"),  # two quarters back
        ("Revenues", "2026-01-01", "2026-06-30", 380e6),  # year to date, overlaps
    ])
    assert filing_periods(facts, make_filing(accession=accn))["previous_quarter"] is None
