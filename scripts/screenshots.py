"""Save screenshots of the pages to docs/screenshots/ (offline: NVDA fixtures, no SEC access).

Run from the repo root: python scripts/screenshots.py
"""
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import sec_client  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.sec_client import SecClient  # noqa: E402
from tests.conftest import (  # noqa: E402
    FIXTURES, NVDA_CIK, TEST_UA, TICKERS, FakeResponse, FakeSession,
)

OUT = ROOT / "docs" / "screenshots"


def fixture_client() -> SecClient:
    load = lambda name: json.loads((FIXTURES / name).read_text(encoding="utf-8"))  # noqa: E731
    session = FakeSession({
        sec_client.TICKERS_URL: FakeResponse(200, TICKERS),
        sec_client.SUBMISSIONS_URL.format(cik=NVDA_CIK): FakeResponse(200, load("nvda_submissions_min.json")),
        sec_client.COMPANY_FACTS_URL.format(cik=NVDA_CIK): FakeResponse(200, load("nvda_companyfacts_min.json")),
    })
    return SecClient(TEST_UA, session=session, min_interval=0)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    server = make_server("127.0.0.1", 0, create_app(fixture_client()), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
            # Offline run: only the local server may be reached (web fonts fall back to system fonts).
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
            page = context.new_page()

            def shot(path: str, name: str, before=None) -> None:
                page.goto(base + path)
                if before:
                    before()
                page.screenshot(path=str(OUT / name), full_page=True)
                print("saved", OUT / name)

            shot("/", "1-home.png")
            shot("/confirm?ticker=NVDA", "2-confirm.png")
            shot("/summary/NVDA", "3-summary.png")
            shot("/summary/NVDA", "4-summary-source.png",
                 before=lambda: page.click("tr[data-key=roe] td:nth-of-type(1) summary"))
            shot("/confirm?ticker=NVDX", "5-unknown-ticker.png")
            browser.close()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
