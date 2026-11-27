"""Niv's report method (MST lesson 13): the deterministic part only.

Two calculations on the P&L (revenue growth, efficiency), the net-profit transition
written as amounts, and placement in the 7-stage growth-company model.
Headline tagging, news and turning points are not computed here (see spec.md, task 7).
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from backend import filings as f

REVENUE_TAGS = f.REVENUE_TAGS
COSTS_TAGS = ("CostsAndExpenses",)
OPERATING_INCOME_TAGS = f.OPERATING_INCOME_TAGS
# Niv's "net income" is the consolidated line (incl. non-controlling interests), so ProfitLoss first.
NET_INCOME_TAGS = ("ProfitLoss", "NetIncomeLoss")

# Niv's efficiency adds cost of sales to operating expenses only when the total does not include it already.
COST_OF_SALES_TAGS = f.COST_TAGS
OPERATING_EXPENSES_TAGS = ("OperatingExpenses",)

DOUBLE_DIGIT_GROWTH_PCT = 10.0

STAGES_HE = {
    1: "אין מוצר פעיל שמכניס כסף. מחפשים עדכונים על אבני דרך עסקיות.",
    2: "צפי להכנסות ראשונות אחרי גמר פיתוח המוצר.",
    3: "עמידה בצפי ההכנסות הראשונות ועלייה בביקושים. עוד אין התייעלות או רווח.",
    4: "צמיחה דו-ספרתית לפחות והתחלה של התייעלות. עדיין אין רווח תפעולי או נקי.",
    5: "צמיחה דו-ספרתית, התייעלות, ומעבר מהפסד תפעולי לרווח תפעולי. עדיין אין רווח נקי.",
    6: "צמיחה דו-ספרתית, התייעלות, ומעבר מהפסד נקי לרווח נקי.",
    7: "צמיחה דו-ספרתית, התייעלות, וגדילה של הרווח הנקי.",
}


# ---------- the two calculations ----------

def revenue_growth_pct(current: float | None, prior: float | None) -> float | None:
    """(current / same period - 1) * 100. None when either side is missing or prior is not positive."""
    if current is None or prior is None or prior <= 0:
        return None
    return (current / prior - 1) * 100


def guidance_growth_pct(guidance: float | None, current_revenue: float | None) -> float | None:
    """(next-quarter revenue guidance / current-quarter revenue - 1) * 100, as Niv computes it.

    The guidance is compared with the quarter just reported, never with the same quarter a year earlier.
    """
    return revenue_growth_pct(guidance, current_revenue)


def efficiency(costs: float | None, revenue: float | None) -> float | None:
    """(cost of sales + operating expenses) / revenue: what the company spent per dollar it took in."""
    if costs is None or revenue is None or revenue <= 0:
        return None
    return costs / revenue


def efficiency_change_points(prior: float | None, current: float | None) -> float | None:
    """Percentage points saved per dollar of revenue (positive = more efficient), as Niv records it."""
    if prior is None or current is None:
        return None
    return (prior - current) * 100


# ---------- net profit, amounts only ----------

def format_usd_he(amount: float) -> str:
    """Absolute amount in Hebrew words: millions below a billion, billions with one decimal at most."""
    a = abs(amount)
    if a >= 1e9:
        value = round(a / 1e9, 1)
        text = f"{value:g}"
        return f"{text} מיליארד דולר"
    return f"{round(a / 1e6):,} מיליון דולר"


def _profit_word(amount: float) -> str:
    return "רווח נקי" if amount >= 0 else "הפסד נקי"


def net_profit_transition(prior: float | None, current: float | None) -> dict[str, Any] | None:
    """'From X to Y' in amounts, never as a percentage."""
    if prior is None or current is None:
        return None
    text = (f"מעבר מ{_profit_word(prior)} של {format_usd_he(prior)} "
            f"ל{_profit_word(current)} של {format_usd_he(current)}")
    return {"from": prior, "to": current, "text_he": text}


# ---------- the 7-stage model ----------

@dataclass
class PeriodPL:
    label: str
    revenue: float | None
    costs: float | None
    operating_income: float | None
    net_income: float | None
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    end: str | None = None


def stage_position(p: PeriodPL) -> int | None:
    """Where a single period sits, from its own P&L only (no comparison)."""
    if p.revenue is None:
        return None
    if p.revenue <= 0:
        return 1
    if p.net_income is not None and p.net_income > 0:
        return 6
    if p.operating_income is not None and p.operating_income > 0:
        return 5
    return 4


def classify_transition(prior: PeriodPL, current: PeriodPL) -> dict[str, Any]:
    """Place the current period in the model relative to the comparable period, with Niv's starred rules."""
    growth = revenue_growth_pct(current.revenue, prior.revenue)
    eff_prior = efficiency(prior.costs, prior.revenue)
    eff_current = efficiency(current.costs, current.revenue)
    double_digit = growth is not None and growth >= DOUBLE_DIGIT_GROWTH_PCT
    efficiency_known = eff_prior is not None and eff_current is not None
    more_efficient = efficiency_known and eff_current < eff_prior
    notes: list[str] = []

    from_stage = stage_position(prior)
    if current.revenue is None:
        to_stage = None
        notes.append("אין נתון הכנסות, אי אפשר למקם במודל.")
    elif current.revenue <= 0:
        to_stage = 1
        notes.append("לפני הכנסות: שלבים 1 עד 3 נקבעים לפי עדכונים עסקיים, לא לפי רווח והפסד.")
    else:
        net_p, net_c = prior.net_income, current.net_income
        op_p, op_c = prior.operating_income, current.operating_income
        if net_c is not None and net_c > 0 and net_p is not None and net_p <= 0:
            to_stage = 6
        elif net_c is not None and net_c > 0 and net_p is not None and net_c > net_p:
            to_stage = 7
        elif net_c is not None and net_c > 0:
            to_stage = 6
            notes.append("יש רווח נקי, אבל הוא לא גדל לעומת התקופה המקבילה.")
        elif op_c is not None and op_c > 0:
            to_stage = 5
        else:
            to_stage = 4 if more_efficient else 3
        if to_stage >= 4 and growth is None:
            notes.append("אין נתון צמיחה בהכנסות, אי אפשר לבדוק את תנאי השלב.")
        elif to_stage >= 4 and not double_digit:
            notes.append("הצמיחה בהכנסות נמוכה מדו-ספרתית, תנאי השלב לא מתקיים במלואו.")
        if to_stage >= 4 and not efficiency_known:
            notes.append("אין נתון התייעלות, אי אפשר לבדוק את תנאי השלב.")
        elif to_stage >= 4 and not more_efficient:
            notes.append("אין התייעלות בהוצאות, תנאי השלב לא מתקיים במלואו.")

    verdict = None
    if from_stage is not None and to_stage is not None:
        if to_stage < from_stage or (to_stage == from_stage and to_stage != 7):
            verdict = "ירידה או עמידה באותו שלב: לא מעניינת ללונג, אפשר לבחון לשורט."
        elif to_stage - from_stage > 1:
            verdict = f"קפיצה של {to_stage - from_stage} שלבים בבת אחת."
        elif to_stage > from_stage:
            verdict = "עלייה של שלב אחד."
        else:
            verdict = "נשארת בשלב 7 עם רווח נקי גדל."

    return {
        "from_stage": from_stage,
        "to_stage": to_stage,
        "to_stage_he": STAGES_HE.get(to_stage) if to_stage else None,
        "double_digit_growth": double_digit,
        "more_efficient": more_efficient,
        "verdict_he": verdict,
        "notes_he": notes,
        # Niv's "joker" (a highlight that changes the company for the better) overrides the numbers;
        # it is not computed here.
        "joker_checked": False,
    }


# ---------- pulling the P&L of one filing from EDGAR company facts ----------

LINES = (("revenue", REVENUE_TAGS), ("costs", COSTS_TAGS),
         ("operating_income", OPERATING_INCOME_TAGS), ("net_income", NET_INCOME_TAGS))
# Read from the filing as well, only to build the costs line when there is no total.
PICKED_LINES = LINES + (("cost_of_sales", COST_OF_SALES_TAGS), ("operating_expenses", OPERATING_EXPENSES_TAGS))
Q4_DERIVED_HE = "שנה מלאה פחות תשעה חודשים"
COSTS_DERIVED_HE = "הכנסות פחות רווח תפעולי"
COSTS_SUM_HE = "עלות מכר ועוד הוצאות תפעול"
COSTS_IN_OPEX_HE = "הוצאות התפעול כבר כוללות את עלות המכר"
# The two ways of adding up the costs must agree with revenue minus operating income within this share of revenue.
COSTS_TOLERANCE = 0.005
ROLE_PERIOD_HE = "התקופה"
ROLE_PREVIOUS_QUARTER_HE = "הרבעון הקודם"
ROLE_FULL_YEAR_HE = "שנה מלאה"
ROLE_NINE_MONTHS_HE = "תשעה חודשים ראשונים"


def _pair(ff: f.FilingFacts, tags: tuple[str, ...]) -> tuple[f.SourcedValue | None, f.SourcedValue | None]:
    """(prior year, current) of the filing's own period, both from the same concept when possible."""
    partial: tuple[f.SourcedValue | None, f.SourcedValue | None] = (None, None)
    for tag in tags:
        current, prior = ff.duration((tag,)), ff.duration((tag,), prior_year=True)
        if current and prior:
            return prior, current
        if partial == (None, None) and (current or prior):
            partial = (prior, current)
    return partial


def _value(sv: f.SourcedValue, role_he: str) -> dict[str, Any]:
    return {"role_he": role_he, **sv.to_dict()}


def _direct(sv: f.SourcedValue | None) -> tuple[float | None, dict[str, Any] | None]:
    if sv is None:
        return None, None
    return sv.value, {"derived": None, "values": [_value(sv, ROLE_PERIOD_HE)]}


def _fourth_quarter(ff: f.FilingFacts, annual: f.SourcedValue | None) -> tuple[float | None, dict[str, Any] | None]:
    """Fourth quarter = full year minus the first nine months (the 10-K does not report it)."""
    if annual is None:
        return None, None
    nine = ff.nine_months_of(annual)
    if nine is None:
        return None, None
    return annual.value - nine.value, {
        "derived": Q4_DERIVED_HE,
        "values": [_value(annual, ROLE_FULL_YEAR_HE), _value(nine, ROLE_NINE_MONTHS_HE)],
    }


Picked = dict[str, tuple[float | None, dict[str, Any] | None]]


def _costs(picked: Picked) -> tuple[float | None, dict[str, Any] | None]:
    """Cost of sales plus operating expenses, without counting the cost of sales twice.

    A reported total is used as is. Otherwise the two lines are added up only when the sum agrees with
    revenue minus operating income; operating expenses alone are used when they already agree with it
    (the company folds the cost of sales into them); anything else falls back to that difference.
    Without an operating-income line the inclusion cannot be checked, so nothing is guessed.
    """
    if picked["costs"][0] is not None:
        return picked["costs"]
    (revenue, revenue_src), (operating, operating_src) = picked["revenue"], picked["operating_income"]
    if revenue is None or operating is None:
        return None, None
    expected = revenue - operating
    tolerance = abs(revenue) * COSTS_TOLERANCE
    (sales, sales_src), (opex, opex_src) = picked["cost_of_sales"], picked["operating_expenses"]
    if sales is not None and opex is not None and abs(sales + opex - expected) <= tolerance:
        return sales + opex, {"derived": COSTS_SUM_HE, "values": sales_src["values"] + opex_src["values"]}
    if opex is not None and abs(opex - expected) <= tolerance:
        return opex, {"derived": COSTS_IN_OPEX_HE, "values": opex_src["values"]}
    return expected, {"derived": COSTS_DERIVED_HE, "values": revenue_src["values"] + operating_src["values"]}


def _period(label: str, picked: Picked, end: str | None) -> PeriodPL:
    values = {key: picked[key][0] for key, _ in LINES}
    sources = {key: picked[key][1] for key, _ in LINES if picked[key][1]}
    values["costs"], costs_source = _costs(picked)
    if costs_source:
        sources["costs"] = costs_source
    return PeriodPL(label=label, sources=sources, end=end, **values)


def _end_of(values: list[f.SourcedValue | None]) -> str | None:
    return next((sv.end for sv in values if sv is not None), None)


def _quarter_start(source: dict[str, Any] | None) -> str | None:
    """First day of the quarter a revenue source describes (the day after the nine months, when derived)."""
    values = (source or {}).get("values") or []
    if not values:
        return None
    if source.get("derived") == Q4_DERIVED_HE and len(values) > 1:
        nine_end = date.fromisoformat(values[1]["end"])
        return (nine_end + timedelta(days=1)).isoformat()
    return values[0].get("start")


def _previous_quarter(ff: f.FilingFacts, current: PeriodPL) -> dict[str, Any] | None:
    """Revenue of the quarter right before the current one, kept apart from the same-quarter comparison.

    Reported directly when that quarter had its own 10-Q; when it was a fourth quarter it is
    the full year minus the first nine months.
    """
    source = current.sources.get("revenue")
    start = _quarter_start(source)
    if start is None:
        return None
    concept = source["values"][0]["concept"]
    for tag in (concept, *(t for t in REVENUE_TAGS if t != concept)):
        direct = ff.period_ending_before(tag, start, f.PERIOD_BANDS["10-Q"])
        if direct:
            return {"revenue": direct.value, "end": direct.end,
                    "source": {"derived": None, "values": [_value(direct, ROLE_PREVIOUS_QUARTER_HE)]}}
        annual = ff.period_ending_before(tag, start, f.PERIOD_BANDS["10-K"], form="10-K")
        value, derived = _fourth_quarter(ff, annual)
        if value is not None:
            return {"revenue": value, "end": annual.end, "source": derived}
    return None


def filing_periods(facts_json: dict[str, Any], filing: f.Filing) -> dict[str, Any]:
    """The comparisons Niv reads in one report, each as (comparable period, current period).

    10-Q: the quarter against the same quarter a year earlier.
    10-K: the year against the previous year, and the fourth quarter against the same quarter
    a year earlier (derived, because the 10-K reports the full year only).
    "previous_quarter" holds the revenue of the quarter right before the current one, or None.
    """
    ff = f.FilingFacts(facts_json, filing)
    pairs = {key: _pair(ff, tags) for key, tags in PICKED_LINES}
    prior_end = _end_of([pairs[key][0] for key, _ in LINES])
    current_end = _end_of([pairs[key][1] for key, _ in LINES])
    own = (_period("prior", {k: _direct(v[0]) for k, v in pairs.items()}, prior_end),
           _period("current", {k: _direct(v[1]) for k, v in pairs.items()}, current_end))
    if filing.form != "10-K":
        return {"quarter": own, "annual": None, "previous_quarter": _previous_quarter(ff, own[1])}
    quarter = (_period("prior", {k: _fourth_quarter(ff, v[0]) for k, v in pairs.items()}, prior_end),
               _period("current", {k: _fourth_quarter(ff, v[1]) for k, v in pairs.items()}, current_end))
    has_quarter = any(p.revenue is not None or p.net_income is not None for p in quarter)
    return {"quarter": quarter if has_quarter else None, "annual": own,
            "previous_quarter": _previous_quarter(ff, quarter[1]) if has_quarter else None}


def compare(prior: PeriodPL, current: PeriodPL) -> dict[str, Any]:
    """Everything Niv records for one comparison."""
    eff_p, eff_c = efficiency(prior.costs, prior.revenue), efficiency(current.costs, current.revenue)
    return {
        "periods": (prior.label, current.label),
        "period_ends": (prior.end, current.end),
        "values": {key: (getattr(prior, key), getattr(current, key)) for key, _ in LINES},
        "revenue_growth_pct": revenue_growth_pct(current.revenue, prior.revenue),
        "efficiency_prior": eff_p,
        "efficiency_current": eff_c,
        "efficiency_change_points": efficiency_change_points(eff_p, eff_c),
        "net_profit": net_profit_transition(prior.net_income, current.net_income),
        "stage": classify_transition(prior, current),
        "sources": {"prior": prior.sources, "current": current.sources},
    }


# ---------- one report: every comparison it has, highlights from any of them ----------
# Niv (answer of 22.11.2026): there is no fixed basis. A line such as a move from loss to profit
# matters as part of the report whether it shows up in the quarterly or the annual comparison.

_BASIS_HE = {"quarter": "בהשוואה הרבעונית", "annual": "בהשוואה השנתית"}


def turning_lines(prior: PeriodPL, current: PeriodPL) -> list[dict[str, Any]]:
    """Lines that turned from a loss to a profit (or back), net or operating."""
    found = []
    for key, word in (("net_income", "נקי"), ("operating_income", "תפעולי")):
        a, b = getattr(prior, key), getattr(current, key)
        if a is None or b is None:
            continue
        if a <= 0 < b:
            found.append({"line": key, "direction": "up",
                          "text_he": f"מעבר מהפסד {word} של {format_usd_he(a)} לרווח {word} של {format_usd_he(b)}"})
        elif b <= 0 < a:
            found.append({"line": key, "direction": "down",
                          "text_he": f"מעבר מרווח {word} של {format_usd_he(a)} להפסד {word} של {format_usd_he(b)}"})
    return found


def report_analysis(periods: dict[str, Any]) -> dict[str, Any]:
    """The comparisons side by side; no basis is chosen. Turning lines from any of them are flagged."""
    result: dict[str, Any] = {"comparisons": {}, "highlights": []}
    for basis in ("quarter", "annual"):
        pair = periods.get(basis)
        if pair is None:
            continue
        result["comparisons"][basis] = compare(*pair)
        previous = periods.get("previous_quarter") if basis == "quarter" else None
        if previous:
            # kept apart from the same-quarter numbers: a different comparison, never mixed into the stage
            result["comparisons"][basis]["previous_quarter"] = {
                **previous, "revenue_growth_pct": revenue_growth_pct(pair[1].revenue, previous["revenue"]),
            }
        for t in turning_lines(*pair):
            result["highlights"].append({**t, "basis": basis,
                                         "text_he": f"{t['text_he']} {_BASIS_HE[basis]}."})
    return result
