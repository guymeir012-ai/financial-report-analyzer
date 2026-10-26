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
DEBT_LEASE_INCL_CURRENT_TAGS = ("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",)
DEBT_LEASE_NONCURRENT_TAGS = ("LongTermDebtAndCapitalLeaseObligations",)
DEBT_LEASE_CURRENT_TAGS = ("LongTermDebtAndCapitalLeaseObligationsCurrent",)
NOTES_AND_LOANS_TAGS = ("NotesAndLoansPayable",)
BANK_SICS = frozenset({"6021", "6022", "6029", "6035", "6036"})

ALL_TAGS = (
    REVENUE_TAGS + GROSS_PROFIT_TAGS + COST_TAGS + OPERATING_INCOME_TAGS + NET_INCOME_TAGS
    + EQUITY_TAGS + ASSETS_TAGS + CURRENT_ASSETS_TAGS + CURRENT_LIABILITIES_TAGS
    + SHORT_TERM_BORROWINGS_TAGS + DEBT_TOTAL_TAGS + DEBT_NONCURRENT_TAGS + DEBT_CURRENT_TAGS
    + DEBT_LEASE_INCL_CURRENT_TAGS + DEBT_LEASE_NONCURRENT_TAGS + DEBT_LEASE_CURRENT_TAGS
    + NOTES_AND_LOANS_TAGS
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


def is_bank_sic(sic: Any) -> bool:
    return str(sic).strip() in BANK_SICS


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
        accession = fact.get("accn") or self.filing.accession
        # Facts borrowed from another filing link to that filing's folder.
        url = (self.filing.url if accession == self.filing.accession
               else filing_url(self.filing.cik, accession, ""))
        return SourcedValue(
            concept=tag, value=fact["val"], unit="USD", start=fact.get("start"),
            end=fact["end"], accession=accession,
            form=fact.get("form") or self.filing.form, filed=fact.get("filed") or "", url=url,
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

    def debt(self) -> tuple[list[SourcedValue], str, bool] | None:
        """Debt components, a formula, and whether finance leases are included."""
        borrowings = (self.instant(SHORT_TERM_BORROWINGS_TAGS)
                      or self.instant(NOTES_AND_LOANS_TAGS))
        lease = False
        total = self.instant(DEBT_TOTAL_TAGS)
        if total:
            parts = [total]
        elif (noncurrent := self.instant(DEBT_NONCURRENT_TAGS)) and (
            current := self.instant(DEBT_CURRENT_TAGS)
        ):
            parts = [noncurrent, current]
        elif incl := self.instant(DEBT_LEASE_INCL_CURRENT_TAGS):
            parts, lease = [incl], True
        elif (lease_nc := self.instant(DEBT_LEASE_NONCURRENT_TAGS)) and (
            lease_cur := self.instant(DEBT_LEASE_CURRENT_TAGS)
        ):
            parts, lease = [lease_nc, lease_cur], True
        else:
            return None
        if borrowings:
            parts.append(borrowings)
        return parts, " + ".join(p.concept for p in parts), lease

    def _all_usd(self, tag: str) -> Iterable[dict[str, Any]]:
        units = (self._gaap.get(tag) or {}).get("units") or {}
        return [x for x in units.get("USD") or [] if _is_number(x.get("val"))]

    def ttm_net_income(self) -> dict[str, SourcedValue | None]:
        """Components of trailing-twelve-month net income (one concept for all parts).

        10-K: {"annual"}. 10-Q: {"annual", "ytd", "prior_ytd"}; TTM = annual + ytd - prior_ytd.
        """
        if self.filing.form == "10-K":
            return {"annual": self.duration(NET_INCOME_TAGS)}
        empty: dict[str, SourcedValue | None] = {"annual": None, "ytd": None, "prior_ytd": None}
        if self.report_date is None:
            return empty
        best = empty
        for tag in NET_INCOME_TAGS:
            parts = self._ttm_parts(tag)
            if all(parts.values()):
                return parts
            if sum(v is not None for v in parts.values()) > sum(v is not None for v in best.values()):
                best = parts
        return best

    def _ttm_parts(self, tag: str) -> dict[str, SourcedValue | None]:
        assert self.report_date is not None
        parts: dict[str, SourcedValue | None] = {"annual": None, "ytd": None, "prior_ytd": None}
        own = [x for x in self._entries(tag)]

        def span(fact: dict[str, Any]) -> tuple[date, date] | None:
            start, end = _parse_date(fact.get("start")), _parse_date(fact.get("end"))
            return (start, end) if start and end else None

        ytd_candidates = [
            (x, s, e) for x in own if (se := span(x)) for s, e in [se]
            if e == self.report_date and (e - s).days < PERIOD_BANDS["10-K"][0]
        ]
        if not ytd_candidates:
            return parts
        ytd, ytd_start, ytd_end = max(
            ytd_candidates, key=lambda c: ((c[2] - c[1]).days, c[0].get("filed") or ""))
        ytd_days = (ytd_end - ytd_start).days
        parts["ytd"] = self._sourced(tag, ytd)

        prior = [
            x for x in own if (se := span(x))
            and abs((se[1] - se[0]).days - ytd_days) <= 7
            and PRIOR_YEAR_GAP[0] <= (self.report_date - se[1]).days <= PRIOR_YEAR_GAP[1]
        ]
        if prior:
            parts["prior_ytd"] = self._sourced(tag, max(prior, key=lambda x: x.get("filed") or ""))

        annual = [
            x for x in self._all_usd(tag) if x.get("form") == "10-K" and (se := span(x))
            and PERIOD_BANDS["10-K"][0] <= (se[1] - se[0]).days <= PERIOD_BANDS["10-K"][1]
            and 0 <= (ytd_start - se[1]).days <= 7
        ]
        if annual:
            parts["annual"] = self._sourced(tag, max(annual, key=lambda x: x.get("filed") or ""))
        return parts


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
) -> tuple[str, int, str, list[Filing], str | None]:
    """Resolve a ticker; return (ticker, cik, title, periodic filings newest first, SIC)."""
    ticker = normalize_ticker(ticker)
    cik, title = resolve_ticker(client, ticker)
    submissions = client.submissions(cik)
    filings = list_periodic_filings(submissions, cik)
    if not filings:
        raise NoPeriodicFilings(ticker, title)
    sic = submissions.get("sic")
    return ticker, cik, title, filings, str(sic) if sic else None
