import json
from pathlib import Path

import pytest

from backend.niv_method import (
    PeriodPL, classify_transition, compare, efficiency, efficiency_change_points,
    format_usd_he, net_profit_transition, q4_report_periods, revenue_growth_pct, stage_position,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def clf():
    facts = json.loads((FIXTURES / "clf_companyfacts_min.json").read_text())
    return q4_report_periods(facts, 2021)


# ---------- golden test: CLF Q4 2021, Niv's numbers from lesson 13 ----------

def test_clf_quarter_matches_niv(clf):
    prior, current = clf["quarter"]
    # prior-year Q4 revenue was heard as "256"; EDGAR (FY 2020 minus 9M 2020) gives 2,256.2M
    assert prior.revenue == pytest.approx(2_256_200_000)
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
    # open question: does Niv place a Q4 report on the annual or the quarterly basis?
    stage = compare(*clf["quarter"])["stage"]
    assert (stage["from_stage"], stage["to_stage"]) == (6, 7)


def test_clf_sources_point_to_filings(clf):
    _, current = clf["quarter"]
    assert current.sources["revenue"]["derived"] == "שנה מלאה פחות תשעה חודשים"
    assert current.sources["revenue"]["full_year"]["form"] == "10-K"
    assert current.sources["revenue"]["nine_months"]["form"] == "10-Q"
    assert current.sources["net_income"]["tag"] == "ProfitLoss"


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


def test_costs_derived_when_tag_missing():
    facts = json.loads((FIXTURES / "clf_companyfacts_min.json").read_text())
    del facts["facts"]["us-gaap"]["CostsAndExpenses"]
    prior, current = q4_report_periods(facts, 2021)["annual"]
    assert current.costs == pytest.approx(20_444e6 - 4_012e6)
    assert current.sources["costs"]["derived"] == "הכנסות פחות רווח תפעולי"


def test_unknown_fiscal_year():
    facts = json.loads((FIXTURES / "clf_companyfacts_min.json").read_text())
    assert q4_report_periods(facts, 2015) is None
    assert q4_report_periods({}, 2021) is None
