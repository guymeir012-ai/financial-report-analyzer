"""Financial ratios computed in Python from XBRL facts; every input carries its source."""
from datetime import date
from typing import Any

from backend import filings as f
from backend.filings import Filing, FilingFacts, SourcedValue
from backend.sec_client import SecClient

NOT_REPORTED_HE = "לא דווח"

NAMES_HE = {
    "revenue_growth_yoy": "צמיחת הכנסות לעומת שנה קודמת (Revenue Growth YoY)",
    "revenue_growth_qoq": "צמיחת הכנסות לעומת הדוח הקודם (Revenue Growth QoQ)",
    "gross_margin": "שיעור רווח גולמי (Gross Margin)",
    "operating_margin": "שיעור רווח תפעולי (Operating Margin)",
    "net_margin": "שיעור רווח נקי (Net Margin)",
    "roe": "תשואה להון (ROE)",
    "current_ratio": "יחס שוטף (Current Ratio)",
    "debt_to_equity": "חוב להון (Debt to Equity)",
    "equity_ratio": "הון למאזן (Equity Ratio)",
}
ITEMS_HE = {
    "revenue": "הכנסות",
    "revenue_prior": "הכנסות בתקופה המקבילה אשתקד",
    "revenue_previous_filing": "הכנסות בדוח הקודם",
    "gross_profit": "רווח גולמי",
    "operating_income": "רווח תפעולי",
    "net_income": "רווח נקי",
    "equity": "הון עצמי",
    "assets": "נכסים",
    "current_assets": "נכסים שוטפים",
    "current_liabilities": "התחייבויות שוטפות",
    "debt": "חוב",
}


def _result(
    key: str, status: str, formula: str, period_days: int | None = None,
    inputs: list[SourcedValue] | None = None, value: float | None = None,
    reason: str = "", missing: list[str] | None = None,
) -> dict[str, Any]:
    status_he = {
        "ok": "חושב",
        "not_reported": NOT_REPORTED_HE,
        "not_computable": f"לא ניתן לחישוב: {reason}",
    }[status]
    return {
        "key": key,
        "name_he": NAMES_HE[key],
        "value": value,
        "status": status,
        "status_he": status_he,
        "formula": formula,
        "period_days": period_days,
        "inputs": [i.to_dict() for i in inputs or []],
        "missing": missing or [],
    }


def _days(value: SourcedValue | None) -> int | None:
    if value is None or not value.start:
        return None
    return (date.fromisoformat(value.end) - date.fromisoformat(value.start)).days


def _divide(
    key: str, formula: str, numerator: float, denominator: float, inputs: list[SourcedValue],
    period_days: int | None, denominator_he: str, positive_only: bool = False,
) -> dict[str, Any]:
    if denominator == 0 or (positive_only and denominator < 0):
        reason = f"{denominator_he} שלילי או אפס" if positive_only else f"{denominator_he} הוא אפס"
        return _result(key, "not_computable", formula, period_days, inputs, reason=reason)
    return _result(key, "ok", formula, period_days, inputs, value=numerator / denominator)


def _missing(key: str, formula: str, period_days: int | None,
             found: dict[str, SourcedValue | None]) -> dict[str, Any] | None:
    gone = [ITEMS_HE[name] for name, val in found.items() if val is None]
    if not gone:
        return None
    present = [val for val in found.values() if val is not None]
    return _result(key, "not_reported", formula, period_days, present, missing=gone)


def _growth(key: str, formula: str, current: SourcedValue | None, base: SourcedValue | None,
            base_name: str, base_label: str) -> dict[str, Any]:
    found = {"revenue": current, base_name: base}
    period = _days(current)
    if (gap := _missing(key, formula, period, found)):
        return gap
    assert current and base
    inputs = [current, base]
    if base.value <= 0:
        return _result(key, "not_computable", formula, period, inputs,
                       reason=f"{base_label} אינן חיוביות")
    return _result(key, "ok", formula, period, inputs, value=current.value / base.value - 1)


def compute_ratios(
    facts_json: dict[str, Any], filing: Filing, previous_filing: Filing | None = None
) -> list[dict[str, Any]]:
    ff = FilingFacts(facts_json, filing)
    revenue = ff.duration(f.REVENUE_TAGS)
    operating = ff.duration(f.OPERATING_INCOME_TAGS)
    net = ff.duration(f.NET_INCOME_TAGS)
    equity = ff.instant(f.EQUITY_TAGS)
    assets = ff.instant(f.ASSETS_TAGS)
    cur_assets = ff.instant(f.CURRENT_ASSETS_TAGS)
    cur_liab = ff.instant(f.CURRENT_LIABILITIES_TAGS)
    period = _days(revenue)
    out: list[dict[str, Any]] = []

    # Revenue growth, year over year
    prior = ff.duration(f.REVENUE_TAGS, prior_year=True)
    out.append(_growth(
        "revenue_growth_yoy",
        f"{revenue.concept if revenue else 'Revenues'} / prior-year {prior.concept if prior else 'Revenues'} - 1",
        revenue, prior, "revenue_prior", "ההכנסות אשתקד",
    ))

    # Revenue growth versus the previous filing (only like-for-like periods)
    out.append(_growth_vs_previous(facts_json, filing, previous_filing, revenue))

    # Margins
    gross_formula, gross_value, gross_inputs = _gross_profit(ff, revenue)
    out.append(_margin("gross_margin", gross_formula, period, revenue, gross_value,
                       gross_inputs, "gross_profit"))
    for key, formula, item, name in (
        ("operating_margin", "OperatingIncomeLoss / Revenues", operating, "operating_income"),
        ("net_margin", "NetIncomeLoss / Revenues", net, "net_income"),
    ):
        out.append(_margin(key, formula, period, revenue, item.value if item else None,
                           [item] if item else [], name))

    # Return on equity (period income over period-end equity, not annualized)
    net_period = _days(net)
    if (gap := _missing("roe", "NetIncomeLoss / StockholdersEquity", net_period,
                        {"net_income": net, "equity": equity})):
        out.append(gap)
    else:
        out.append(_divide("roe", f"{net.concept} / {equity.concept}", net.value, equity.value,
                           [net, equity], net_period, "ההון העצמי", positive_only=True))

    # Current ratio
    if (gap := _missing("current_ratio", "AssetsCurrent / LiabilitiesCurrent", None,
                        {"current_assets": cur_assets, "current_liabilities": cur_liab})):
        out.append(gap)
    else:
        out.append(_divide("current_ratio", "AssetsCurrent / LiabilitiesCurrent",
                           cur_assets.value, cur_liab.value, [cur_assets, cur_liab], None,
                           "ההתחייבויות השוטפות"))

    # Debt to equity
    debt = ff.debt()
    parts = debt[0] if debt else []
    debt_formula = debt[1] if debt else "LongTermDebt"
    de_formula = f"({debt_formula}) / StockholdersEquity" if len(parts) > 1 else f"{debt_formula} / StockholdersEquity"
    found_de = {"debt": parts[0] if parts else None, "equity": equity}
    if (gap := _missing("debt_to_equity", de_formula, None, found_de)):
        gap["inputs"] = [i.to_dict() for i in [*parts, *([equity] if equity else [])]]
        out.append(gap)
    else:
        out.append(_divide("debt_to_equity", de_formula, sum(p.value for p in parts), equity.value,
                           [*parts, equity], None, "ההון העצמי", positive_only=True))

    # Equity ratio
    if (gap := _missing("equity_ratio", "StockholdersEquity / Assets", None,
                        {"equity": equity, "assets": assets})):
        out.append(gap)
    else:
        out.append(_divide("equity_ratio", f"{equity.concept} / {assets.concept}",
                           equity.value, assets.value, [equity, assets], None, "הנכסים",
                           positive_only=True))
    return out


def _margin(key: str, formula: str, period: int | None, revenue: SourcedValue | None,
            profit: float | None, profit_inputs: list[SourcedValue], profit_name: str) -> dict[str, Any]:
    inputs = list(profit_inputs)
    if revenue and revenue not in inputs:
        inputs.append(revenue)
    missing = [ITEMS_HE[n] for n, v in ((profit_name, profit), ("revenue", revenue)) if v is None]
    if missing:
        return _result(key, "not_reported", formula, period, inputs, missing=missing)
    return _divide(key, formula, profit, revenue.value, inputs, period, "ההכנסות")


def _gross_profit(ff: FilingFacts, revenue: SourcedValue | None):
    """Gross profit as (formula, value, inputs); falls back to revenue minus cost."""
    reported = ff.duration(f.GROSS_PROFIT_TAGS)
    if reported:
        return f"{reported.concept} / Revenues", reported.value, [reported]
    cost = ff.duration(f.COST_TAGS)
    if revenue and cost:
        formula = f"({revenue.concept} - {cost.concept}) / {revenue.concept}"
        return formula, revenue.value - cost.value, [revenue, cost]
    return "GrossProfit / Revenues", None, [cost] if cost else []


def _growth_vs_previous(
    facts_json: dict[str, Any], filing: Filing, previous_filing: Filing | None,
    revenue: SourcedValue | None,
) -> dict[str, Any]:
    key, formula = "revenue_growth_qoq", "Revenues / previous-filing Revenues - 1"
    period = _days(revenue)
    inputs = [revenue] if revenue else []
    if previous_filing is None:
        return _result(key, "not_computable", formula, period, inputs, reason="אין דוח קודם להשוואה")
    if previous_filing.form != filing.form:
        return _result(
            key, "not_computable", formula, period, inputs,
            reason=f"אורכי התקופות שונים ({previous_filing.form} מול {filing.form})",
        )
    previous = FilingFacts(facts_json, previous_filing).duration(f.REVENUE_TAGS)
    return _growth(key, formula, revenue, previous, "revenue_previous_filing", "ההכנסות בדוח הקודם")


def analyze(ticker: str, client: SecClient) -> dict[str, Any]:
    ticker, cik, title, periodic = f.find_company_filings(client, ticker)
    facts_json = client.company_facts(cik)
    available = f.filings_with_facts(facts_json, periodic)
    notice_he: str | None = None
    if available:
        chain = available
        pending = periodic[: periodic.index(available[0])]
        if pending:
            newest = pending[0]
            notice_he = (
                f"קיים דוח חדש יותר ({newest.form} מתאריך {newest.filing_date}) "
                "שעדיין לא זמין בנתונים המובנים של SEC. הניתוח מבוסס על הדוח האחרון שזמין."
            )
    else:
        chain, pending = periodic, []
        notice_he = "הנתונים המובנים של SEC לחברה הזו עדיין לא זמינים."
    latest = chain[0]
    previous = chain[1] if len(chain) > 1 else None
    return {
        "company": title,
        "cik": cik,
        "ticker": ticker,
        "latest": {
            "filing": latest.to_dict(),
            "ratios": compute_ratios(facts_json, latest, previous),
        },
        "previous": {
            "filing": previous.to_dict(),
            "ratios": compute_ratios(facts_json, previous, chain[2] if len(chain) > 2 else None),
        } if previous else None,
        "pending_filings": [x.to_dict() for x in pending],
        "notice_he": notice_he,
    }
