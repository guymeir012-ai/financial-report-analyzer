"""Find periodic filings (10-Q / 10-K) and pick the XBRL facts that belong to one filing."""
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable

from backend.sec_client import SecClient, normalize_ticker, resolve_ticker

PERIODIC_FORMS = ("10-Q", "10-K")
# Allowed duration (days) of the reporting period, by form.
PERIOD_BANDS = {"10-Q": (80, 100), "10-K": (350, 380)}
# A prior-year comparative period ends this many days before the report date.
PRIOR_YEAR_GAP = (350, 380)

REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "RevenuesNetOfInterestExpense",
)
GROSS_PROFIT_TAGS = ("GrossProfit",)
COST_TAGS = ("CostOfRevenue", "CostOfGoodsAndServicesSold")
OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
NET_INCOME_TAGS = ("NetIncomeLoss", "ProfitLoss")
EQUITY_TAGS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
ASSETS_TAGS = ("Assets",)
CURRENT_ASSETS_TAGS = ("AssetsCurrent",)
CURRENT_LIABILITIES_TAGS = ("LiabilitiesCurrent",)
SHORT_TERM_BORROWINGS_TAGS = ("ShortTermBorrowings",)
DEBT_TOTAL_TAGS = ("LongTermDebt",)
DEBT_NONCURRENT_TAGS = ("LongTermDebtNoncurrent",)
DEBT_CURRENT_TAGS = ("LongTermDebtCurrent", "DebtCurrent")

ALL_TAGS = (
    REVENUE_TAGS + GROSS_PROFIT_TAGS + COST_TAGS + OPERATING_INCOME_TAGS + NET_INCOME_TAGS
    + EQUITY_TAGS + ASSETS_TAGS + CURRENT_ASSETS_TAGS + CURRENT_LIABILITIES_TAGS
    + SHORT_TERM_BORROWINGS_TAGS + DEBT_TOTAL_TAGS + DEBT_NONCURRENT_TAGS + DEBT_CURRENT_TAGS
)


class NoPeriodicFilings(LookupError):
    def __init__(self, ticker: str, company: str) -> None:
        super().__init__(ticker)
        self.ticker = ticker
        self.company = company


@dataclass(frozen=True)
class Filing:
    form: str
    accession: str
    filing_date: str
    report_date: str
    primary_document: str
    cik: int
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourcedValue:
    concept: str
    value: float
    unit: str
    start: str | None
    end: str
    accession: str
    form: str
    filed: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def filing_url(cik: int, accession: str, primary_document: str) -> str:
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    return f"{folder}/{primary_document}" if primary_document else folder


def list_periodic_filings(submissions: dict[str, Any], cik: int) -> list[Filing]:
    """10-Q and 10-K filings (amendments skipped), newest filing date first."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []

    def column(name: str, i: int) -> str:
        values = recent.get(name) or []
        return (values[i] if i < len(values) else "") or ""

    found: list[Filing] = []
    for i, form in enumerate(forms):
        accession = column("accessionNumber", i)
        if form not in PERIODIC_FORMS or not accession:
            continue
        doc = column("primaryDocument", i)
        found.append(
            Filing(form, accession, column("filingDate", i), column("reportDate", i),
                   doc, cik, filing_url(cik, accession, doc))
        )
    found.sort(key=lambda f: f.filing_date, reverse=True)
    return found


def latest_and_previous(filings: list[Filing]) -> tuple[Filing | None, Filing | None]:
    return (
        filings[0] if filings else None,
        filings[1] if len(filings) > 1 else None,
    )


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class FilingFacts:
    """Facts of one filing, selected from a companyfacts document (USD only)."""

    def __init__(self, facts_json: dict[str, Any], filing: Filing) -> None:
        facts = facts_json.get("facts") if isinstance(facts_json, dict) else None
        self._gaap: dict[str, Any] = (facts or {}).get("us-gaap") or {}
        self.filing = filing
        self.report_date = self._resolve_report_date()
        self.band = PERIOD_BANDS[filing.form]

    def _entries(self, tag: str) -> Iterable[dict[str, Any]]:
        units = (self._gaap.get(tag) or {}).get("units") or {}
        for fact in units.get("USD") or []:
            if fact.get("accn") == self.filing.accession and _is_number(fact.get("val")):
                yield fact

    def _resolve_report_date(self) -> date | None:
        declared = _parse_date(self.filing.report_date)
        if declared or self.filing.report_date:
            return declared
        # No reportDate in the submissions: latest period end among this filing's facts.
        ends = [
            end for tag in ALL_TAGS for fact in self._entries(tag)
            if (end := _parse_date(fact.get("end")))
        ]
        return max(ends) if ends else None

    def _sourced(self, tag: str, fact: dict[str, Any]) -> SourcedValue:
        return SourcedValue(
            concept=tag, value=fact["val"], unit="USD", start=fact.get("start"),
            end=fact["end"], accession=self.filing.accession,
            form=fact.get("form") or self.filing.form, filed=fact.get("filed") or "",
            url=self.filing.url,
        )

    def _pick(self, tags: Iterable[str], accept) -> SourcedValue | None:
        for tag in tags:
            matches = [f for f in self._entries(tag) if accept(f)]
            if matches:
                best = max(matches, key=lambda f: f.get("filed") or "")
                return self._sourced(tag, best)
        return None

    def duration(self, tags: Iterable[str], prior_year: bool = False) -> SourcedValue | None:
        """Income-statement item for the filing's period, or its prior-year comparative."""
        if self.report_date is None:
            return None
        low, high = self.band

        def accept(fact: dict[str, Any]) -> bool:
            start, end = _parse_date(fact.get("start")), _parse_date(fact.get("end"))
            if start is None or end is None or not low <= (end - start).days <= high:
                return False
            gap = (self.report_date - end).days
            return PRIOR_YEAR_GAP[0] <= gap <= PRIOR_YEAR_GAP[1] if prior_year else gap == 0

        return self._pick(tags, accept)

    def instant(self, tags: Iterable[str]) -> SourcedValue | None:
        """Balance-sheet item at the report date."""
        if self.report_date is None:
            return None
        return self._pick(
            tags,
            lambda f: not f.get("start") and _parse_date(f.get("end")) == self.report_date,
        )

    def debt(self) -> tuple[list[SourcedValue], str] | None:
        """Debt components and a formula, following the plan's two chains."""
        borrowings = self.instant(SHORT_TERM_BORROWINGS_TAGS)
        extra = [borrowings] if borrowings else []
        total = self.instant(DEBT_TOTAL_TAGS)
        if total:
            parts = [total, *extra]
        else:
            noncurrent = self.instant(DEBT_NONCURRENT_TAGS)
            current = self.instant(DEBT_CURRENT_TAGS)
            if not (noncurrent and current):
                return None
            parts = [noncurrent, current, *extra]
        return parts, " + ".join(p.concept for p in parts)


def filings_with_facts(facts_json: dict[str, Any], filings: list[Filing]) -> list[Filing]:
    """Filings that have at least one USD fact in companyfacts (order kept)."""
    gaap = ((facts_json or {}).get("facts") or {}).get("us-gaap") or {}
    accessions = {
        fact.get("accn")
        for tag in ALL_TAGS
        for fact in ((gaap.get(tag) or {}).get("units") or {}).get("USD") or []
    }
    return [x for x in filings if x.accession in accessions]


def find_company_filings(
    client: SecClient, ticker: str
) -> tuple[str, int, str, list[Filing]]:
    """Resolve a ticker; return (ticker, cik, company title, periodic filings newest first)."""
    ticker = normalize_ticker(ticker)
    cik, title = resolve_ticker(client, ticker)
    filings = list_periodic_filings(client.submissions(cik), cik)
    if not filings:
        raise NoPeriodicFilings(ticker, title)
    return ticker, cik, title, filings
