"""Niv's report method (MST lesson 13): the deterministic part only.

Two calculations on the P&L (revenue growth, efficiency), the net-profit transition
written as amounts, and placement in the 7-stage growth-company model.
Headline tagging, news and turning points are not computed here (see spec.md, task 7).
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Any

REVENUE_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")
COSTS_TAGS = ("CostsAndExpenses",)
OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
# Niv's "net income" is the consolidated line (incl. non-controlling interests), so ProfitLoss first.
NET_INCOME_TAGS = ("ProfitLoss", "NetIncomeLoss")

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
    more_efficient = eff_prior is not None and eff_current is not None and eff_current < eff_prior
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
        if to_stage >= 4 and not double_digit:
            notes.append("הצמיחה בהכנסות נמוכה מדו-ספרתית, תנאי השלב לא מתקיים במלואו.")
        if to_stage >= 4 and not more_efficient:
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


# ---------- pulling the P&L from EDGAR company facts ----------

def _parse(d: str) -> date:
    return date.fromisoformat(d)


def _days(e: dict[str, Any]) -> int:
    return (_parse(e["end"]) - _parse(e["start"])).days


def _find(facts: dict[str, Any], tags: tuple[str, ...], start: date, end: date) -> tuple[float, dict] | None:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        for e in gaap.get(tag, {}).get("units", {}).get("USD", []):
            if e.get("start") and _parse(e["start"]) == start and _parse(e["end"]) == end:
                return e["val"], {"tag": tag, "accn": e.get("accn"), "form": e.get("form"),
                                  "start": e["start"], "end": e["end"]}
    return None


def _fiscal_year_entry(facts: dict[str, Any], fiscal_year: int) -> dict[str, Any] | None:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in REVENUE_TAGS:
        for e in gaap.get(tag, {}).get("units", {}).get("USD", []):
            if (e.get("form") == "10-K" and e.get("fy") == fiscal_year and e.get("start")
                    and 350 <= _days(e) <= 380 and _parse(e["end"]).year in (fiscal_year, fiscal_year + 1)):
                return e
    return None


def _line(facts, tags, start, end, nine_month_end=None):
    """A value for [start, end]; with nine_month_end, the fourth quarter = full year minus nine months."""
    full = _find(facts, tags, start, end)
    if full is None:
        return None, None
    if nine_month_end is None:
        return full
    nine = _find(facts, tags, start, nine_month_end)
    if nine is None:
        return None, None
    value = full[0] - nine[0]
    return value, {"tag": full[1]["tag"], "derived": "שנה מלאה פחות תשעה חודשים",
                   "full_year": full[1], "nine_months": nine[1]}


def _period(facts, label, start, end, nine_month_end=None) -> PeriodPL:
    sources: dict[str, dict] = {}
    values = {}
    for key, tags in (("revenue", REVENUE_TAGS), ("costs", COSTS_TAGS),
                      ("operating_income", OPERATING_INCOME_TAGS), ("net_income", NET_INCOME_TAGS)):
        value, src = _line(facts, tags, start, end, nine_month_end)
        values[key] = value
        if src:
            sources[key] = src
    if values["costs"] is None and values["revenue"] is not None and values["operating_income"] is not None:
        values["costs"] = values["revenue"] - values["operating_income"]
        sources["costs"] = {"derived": "הכנסות פחות רווח תפעולי"}
    return PeriodPL(label=label, sources=sources, **values)


def q4_report_periods(facts: dict[str, Any], fiscal_year: int) -> dict[str, tuple[PeriodPL, PeriodPL]] | None:
    """For a Q4 (10-K) report: quarter vs same quarter last year, and year vs previous year."""
    fy = _fiscal_year_entry(facts, fiscal_year)
    if fy is None:
        return None
    start, end = _parse(fy["start"]), _parse(fy["end"])
    prior_start = start.replace(year=start.year - 1)
    prior_end = end.replace(year=end.year - 1)
    nine_end = _nine_month_end(facts, start, end)
    prior_nine_end = _nine_month_end(facts, prior_start, prior_end)
    annual = (_period(facts, f"שנה {fiscal_year - 1}", prior_start, prior_end),
              _period(facts, f"שנה {fiscal_year}", start, end))
    quarter = None
    if nine_end and prior_nine_end:
        quarter = (_period(facts, f"רבעון 4 {fiscal_year - 1}", prior_start, prior_end, prior_nine_end),
                   _period(facts, f"רבעון 4 {fiscal_year}", start, end, nine_end))
    return {"annual": annual, "quarter": quarter}


def _nine_month_end(facts, start: date, end: date) -> date | None:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in REVENUE_TAGS:
        for e in gaap.get(tag, {}).get("units", {}).get("USD", []):
            if e.get("start") and _parse(e["start"]) == start and 260 <= _days(e) <= 285 and _parse(e["end"]) < end:
                return _parse(e["end"])
    return None


def compare(prior: PeriodPL, current: PeriodPL) -> dict[str, Any]:
    """Everything Niv records for one comparison."""
    eff_p, eff_c = efficiency(prior.costs, prior.revenue), efficiency(current.costs, current.revenue)
    return {
        "periods": (prior.label, current.label),
        "revenue_growth_pct": revenue_growth_pct(current.revenue, prior.revenue),
        "efficiency_prior": eff_p,
        "efficiency_current": eff_c,
        "efficiency_change_points": efficiency_change_points(eff_p, eff_c),
        "net_profit": net_profit_transition(prior.net_income, current.net_income),
        "stage": classify_transition(prior, current),
        "sources": {"prior": prior.sources, "current": current.sources},
    }
