"""Real Chromium (Playwright) against a local server running the app with the NVDA fixture client."""
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
from werkzeug.serving import make_server  # noqa: E402

from backend import sec_client  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.sec_client import SecClient  # noqa: E402
from tests.conftest import (  # noqa: E402
    CLOSES, NVDA_CIK, PEER_METRICS, QUOTE, TEST_UA, TICKERS, FakeResponse, FakeSession, StubFinnhub, StubTiingo,
)


@pytest.fixture(scope="module")
def base_url(nvda_facts, nvda_submissions):
    session = FakeSession({
        sec_client.TICKERS_URL: FakeResponse(200, TICKERS),
        sec_client.SUBMISSIONS_URL.format(cik=NVDA_CIK): FakeResponse(200, nvda_submissions),
        sec_client.COMPANY_FACTS_URL.format(cik=NVDA_CIK): FakeResponse(200, nvda_facts),
    })
    finnhub = StubFinnhub(quote=QUOTE, peers=["AVGO", "AMD", "INTC", "MU", "TXN"], metrics=PEER_METRICS)
    app = create_app(SecClient(TEST_UA, session=session, min_interval=0), finnhub=finnhub, tiingo=StubTiingo(CLOSES))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            instance = pw.chromium.launch()
        except Exception as exc:  # browser binary missing
            pytest.skip(f"Chromium is not available: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def page(browser, base_url):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    # Only the local server is reachable; web fonts simply fall back.
    context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base_url) else route.abort())
    page = context.new_page()
    page.csp_errors = []
    page.on("console", lambda msg: page.csp_errors.append(msg.text) if "Content Security Policy" in msg.text else None)
    yield page
    context.close()


def source_summary(page, key, column=1):
    return page.locator(f"tr[data-key={key}] td:nth-of-type({column}) summary")


def test_full_flow_type_confirm_and_open_source(page, base_url):
    page.goto(base_url + "/")
    assert page.get_attribute("html", "dir") == "rtl"
    page.fill("#ticker", "nvda")
    page.click("button[type=submit]")
    page.wait_for_url("**/confirm?ticker=nvda")
    assert "NVIDIA CORP" in page.inner_text("h1")
    assert "26.07.2026" in page.inner_text("main")

    page.click("text=אישור, נתח")
    page.wait_for_url("**/summary/NVDA")
    assert page.locator("tr[data-key]").count() == 9
    assert source_summary(page, "gross_margin").inner_text().strip() == "75.0%"

    panel = page.locator("tr[data-key=roe] td:nth-of-type(1) .source")
    assert not panel.is_visible()
    source_summary(page, "roe").click()
    assert panel.is_visible()
    text = panel.inner_text()
    assert "NetIncomeLoss" in text and "StockholdersEquity" in text and "228,984" in text
    link = panel.locator("a").first
    assert link.get_attribute("href").startswith("https://www.sec.gov/")
    assert link.get_attribute("rel") == "noopener noreferrer"
    assert page.csp_errors == []


def test_panel_floats_without_moving_rows_and_stays_in_view(page, base_url):
    page.goto(base_url + "/summary/NVDA")
    next_row = page.locator("tr[data-key=current_ratio]")

    def document_y(locator):  # independent of any scrolling the click causes
        return locator.bounding_box()["y"] + page.evaluate("window.scrollY")

    before = document_y(next_row)
    for key, column in (("roe", 1), ("roe", 2), ("revenue_growth_yoy", 1)):
        source_summary(page, key, column).click()
        box = page.locator(f"tr[data-key={key}] td:nth-of-type({column}) .source").bounding_box()
        viewport = page.viewport_size
        assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"]
        assert box["y"] >= 0
        assert page.evaluate("document.documentElement.scrollWidth") <= viewport["width"]
    assert document_y(next_row) == before


def test_only_one_source_open_at_a_time(page, base_url):
    page.goto(base_url + "/summary/NVDA")
    # an open panel covers the rows below it, so the second number is one above the first
    source_summary(page, "net_margin").click()
    source_summary(page, "gross_margin").click()
    assert page.locator("details.src[open]").count() == 1
    assert page.locator("tr[data-key=gross_margin] details").first.get_attribute("open") is not None
    source_summary(page, "gross_margin").click()  # clicking the number again closes it
    assert page.locator("details.src[open]").count() == 0


def test_numbers_are_ltr_inside_rtl_page(page, base_url):
    page.goto(base_url + "/summary/NVDA")
    assert page.evaluate("getComputedStyle(document.body).direction") == "rtl"
    number = page.locator("tr[data-key=gross_margin] summary bdi").first
    assert number.evaluate("el => getComputedStyle(el).direction") == "ltr"
    assert page.locator("h1 bdi").first.evaluate("el => getComputedStyle(el).direction") == "ltr"


def test_no_javascript_runs(page, base_url):
    page.goto(base_url + "/summary/NVDA")
    assert page.locator("script").count() == 0
    assert page.evaluate("typeof window.__x") == "undefined"
    assert page.csp_errors == []


def test_unknown_ticker_suggestion_click_leads_to_confirm(page, base_url):
    page.goto(base_url + "/")
    page.fill("#ticker", "NVDX")
    page.click("button[type=submit]")
    assert "האם התכוונת ל:" in page.inner_text("main")
    page.click(".suggestions a")
    page.wait_for_url("**/confirm?ticker=NVDA")
    assert "NVIDIA CORP" in page.inner_text("h1")


def test_narrow_phone_layout_has_no_horizontal_scroll(browser, base_url):
    context = browser.new_context(viewport={"width": 390, "height": 800})
    context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base_url) else route.abort())
    page = context.new_page()
    page.goto(base_url + "/summary/NVDA")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    source_summary(page, "roe").click()
    panel = page.locator("tr[data-key=roe] td:nth-of-type(1) .source")
    assert panel.is_visible()
    box = panel.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    context.close()


def test_valuation_section_chart_and_sources(page, base_url):
    page.goto(base_url + "/summary/NVDA")
    section = page.locator("#valuation")
    section.scroll_into_view_if_needed()
    assert section.is_visible() and section.get_attribute("data-status") == "ok"
    assert "$203.48" in page.inner_text("section.summary-head") and "שעון ישראל" in page.inner_text("section.summary-head")
    assert page.locator("#valuation svg.history-chart rect.col").count() == 3
    assert page.locator("#valuation svg.bar-track").count() == 6          # company, history, peers, 3 leaders
    assert page.locator("#valuation .peer").count() == 5

    pe = page.locator('#valuation [data-kpi="pe"]')
    assert pe.locator("summary").inner_text().strip() == "25.6x"
    panel = pe.locator(".source")
    assert not panel.is_visible()
    pe.locator("summary").click()
    assert panel.is_visible()
    text = panel.inner_text()
    assert "Finnhub" in text and "quote" in text and "בדיקה צולבת" in text and "25.8x" in text
    box = panel.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
    assert page.evaluate("document.documentElement.scrollWidth") <= page.viewport_size["width"]

    pe.locator("summary").click()
    eps = page.locator('#valuation [data-kpi="eps"]')
    eps.locator("summary").click()
    eps_text = eps.locator(".source").inner_text()
    assert "מספר מניות בדילול" in eps_text and "24,285" in eps_text and "ספקים אחרים" in eps_text
    assert page.locator("details.src[open]").count() == 1
    assert page.csp_errors == []


def test_valuation_history_points_open_with_sources(page, base_url):
    page.goto(base_url + "/summary/NVDA")
    page.locator("#valuation details.points > summary").click()
    rows = page.locator("#valuation table.points-table tbody tr")
    assert rows.count() == 3
    rows.first.locator("td:nth-of-type(1) summary").click()
    text = rows.first.locator("td:nth-of-type(1) .source").inner_text()
    assert "Tiingo" in text and "206.84" in text and "24.07.2026" in text
