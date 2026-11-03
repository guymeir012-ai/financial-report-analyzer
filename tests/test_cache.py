import hashlib
import json
import logging
import os
import re
import stat

import pytest
import requests

from backend import sec_client
from backend.sec_client import SecClient, SecError
from tests.conftest import NVDA_CIK, TEST_UA, TICKERS, FakeResponse, FakeSession

URL = sec_client.SUBMISSIONS_URL.format(cik=NVDA_CIK)
HOUR = 3600.0


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def make(tmp_path, routes, clock, **kwargs):
    session = FakeSession(routes)
    client = SecClient(TEST_UA, session=session, min_interval=0, max_retries=kwargs.pop("max_retries", 1),
                       cache_dir=tmp_path / "cache", wall_clock=clock, **kwargs)
    return client, session


def cache_files(tmp_path):
    folder = tmp_path / "cache"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_second_call_is_served_from_disk_without_network(tmp_path, fake_time):
    clock = Clock()
    first, s1 = make(tmp_path, {URL: FakeResponse(200, {"a": 1})}, clock)
    assert first.submissions(NVDA_CIK) == {"a": 1} and len(s1.calls) == 1
    # a brand-new client (empty memory) with a dead network still answers
    second, s2 = make(tmp_path, {}, clock)
    assert second.submissions(NVDA_CIK) == {"a": 1}
    assert s2.calls == []


def test_entry_format(tmp_path, fake_time):
    clock = Clock()
    client, _ = make(tmp_path, {URL: FakeResponse(200, {"שם": "ערך"})}, clock)
    client.submissions(NVDA_CIK)
    (path,) = cache_files(tmp_path)
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry == {"url": URL, "fetched_at": clock.now, "data": {"שם": "ערך"}}


def test_expiry_submissions_6h(tmp_path, fake_time):
    clock = Clock()
    client, session = make(tmp_path, {URL: [FakeResponse(200, {"v": 1}), FakeResponse(200, {"v": 2})]}, clock)
    assert client.submissions(NVDA_CIK) == {"v": 1}
    clock.now += 6 * HOUR - 1
    assert client.submissions(NVDA_CIK) == {"v": 1} and len(session.calls) == 1
    clock.now += 2
    assert client.submissions(NVDA_CIK) == {"v": 2} and len(session.calls) == 2


def test_expiry_tickers_24h_and_company_facts_6h(tmp_path, fake_time):
    clock = Clock()
    facts_url = sec_client.COMPANY_FACTS_URL.format(cik=NVDA_CIK)
    client, session = make(tmp_path, {sec_client.TICKERS_URL: FakeResponse(200, TICKERS),
                                       facts_url: FakeResponse(200, {"f": 1})}, clock)
    client.ticker_map()
    client.company_facts(NVDA_CIK)
    clock.now += 7 * HOUR
    fresh, s2 = make(tmp_path, {sec_client.TICKERS_URL: FakeResponse(200, TICKERS),
                                facts_url: FakeResponse(200, {"f": 2})}, clock)
    assert fresh.ticker_map()["NVDA"]["cik"] == NVDA_CIK
    assert fresh.company_facts(NVDA_CIK) == {"f": 2}
    assert [c[0] for c in s2.calls] == [facts_url]  # tickers still fresh, facts refetched
    clock.now += 18 * HOUR
    third, s3 = make(tmp_path, {sec_client.TICKERS_URL: FakeResponse(200, TICKERS)}, clock)
    third.ticker_map()
    assert [c[0] for c in s3.calls] == [sec_client.TICKERS_URL]


@pytest.mark.parametrize("junk", [
    "", "not json", "[]", "{}", '{"url": "x"}', '{"url": "%s", "data": 1}' % URL,
    '{"url": "%s", "fetched_at": "now", "data": 1}' % URL,
    '{"url": "https://data.sec.gov/other", "fetched_at": 1800000000, "data": {"evil": 1}}',
])
def test_corrupt_or_mismatched_file_is_ignored_and_replaced(tmp_path, fake_time, junk):
    clock = Clock()
    folder = tmp_path / "cache"
    folder.mkdir()
    (folder / (hashlib.sha256(URL.encode()).hexdigest() + ".json")).write_text(junk, encoding="utf-8")
    client, session = make(tmp_path, {URL: FakeResponse(200, {"ok": 1})}, clock)
    assert client.submissions(NVDA_CIK) == {"ok": 1} and len(session.calls) == 1
    again, s2 = make(tmp_path, {}, clock)
    assert again.submissions(NVDA_CIK) == {"ok": 1} and s2.calls == []


def test_stale_copy_served_when_sec_is_down(tmp_path, fake_time, caplog):
    clock = Clock()
    make(tmp_path, {URL: FakeResponse(200, {"old": 1})}, clock)[0].submissions(NVDA_CIK)
    clock.now += 10 * HOUR
    for failure in (FakeResponse(503), FakeResponse(429), requests.ConnectionError("down"),
                    FakeResponse(403), FakeResponse(200, None)):
        client, _ = make(tmp_path, {URL: failure}, clock)
        with caplog.at_level(logging.WARNING):
            assert client.submissions(NVDA_CIK) == {"old": 1}
    assert "stale" in caplog.text
    # the stale copy is not refreshed by a failure, so it keeps being retried
    assert json.loads(cache_files(tmp_path)[0].read_text())["fetched_at"] == 1_800_000_000.0


def test_404_never_falls_back_to_stale_copy(tmp_path, fake_time):
    clock = Clock()
    make(tmp_path, {URL: FakeResponse(200, {"old": 1})}, clock)[0].submissions(NVDA_CIK)
    clock.now += 10 * HOUR
    client, _ = make(tmp_path, {URL: FakeResponse(404)}, clock)
    with pytest.raises(SecError) as exc:
        client.submissions(NVDA_CIK)
    assert exc.value.status == 404


def test_failure_without_copy_raises_normal_error(tmp_path, fake_time):
    client, _ = make(tmp_path, {URL: FakeResponse(503)}, Clock())
    with pytest.raises(SecError) as exc:
        client.submissions(NVDA_CIK)
    assert exc.value.status == 503


@pytest.mark.parametrize("response", [FakeResponse(503), FakeResponse(404), FakeResponse(403),
                                      FakeResponse(200, None), requests.Timeout("slow")])
def test_only_successful_parsed_200_is_stored(tmp_path, fake_time, response):
    client, _ = make(tmp_path, {URL: response}, Clock())
    with pytest.raises(SecError):
        client.submissions(NVDA_CIK)
    assert cache_files(tmp_path) == []


def test_filenames_are_hashes_dir_is_private_no_temp_files(tmp_path, fake_time):
    clock = Clock()
    client, _ = make(tmp_path, {URL: FakeResponse(200, {"a": 1}),
                                sec_client.TICKERS_URL: FakeResponse(200, TICKERS)}, clock)
    client.submissions(NVDA_CIK)
    client.ticker_map()
    names = [p.name for p in cache_files(tmp_path)]
    assert len(names) == 2 and all(re.fullmatch(r"[0-9a-f]{64}\.json", n) for n in names)
    assert hashlib.sha256(URL.encode()).hexdigest() + ".json" in names
    assert not any(str(NVDA_CIK) in n or "sec.gov" in n for n in names)
    assert stat.S_IMODE(os.stat(tmp_path / "cache").st_mode) == 0o700


def test_existing_loose_dir_is_tightened(tmp_path, fake_time):
    folder = tmp_path / "cache"
    folder.mkdir(mode=0o755)
    os.chmod(folder, 0o755)
    make(tmp_path, {URL: FakeResponse(200, {"a": 1})}, Clock())[0].submissions(NVDA_CIK)
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700


def test_failed_write_is_harmless_and_leaves_no_temp_file(tmp_path, fake_time, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    client, _ = make(tmp_path, {URL: FakeResponse(200, {"a": 1})}, Clock())
    assert client.submissions(NVDA_CIK) == {"a": 1}
    assert cache_files(tmp_path) == []


def test_unusable_cache_dir_does_not_break_requests(tmp_path, fake_time):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    session = FakeSession({URL: FakeResponse(200, {"a": 1})})
    client = SecClient(TEST_UA, session=session, min_interval=0, cache_dir=blocker / "sub")
    assert client.submissions(NVDA_CIK) == {"a": 1}


def test_disabled_by_default(tmp_path, fake_time, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = FakeSession({URL: FakeResponse(200, {"a": 1})})
    client = SecClient(TEST_UA, session=session, min_interval=0)
    client.submissions(NVDA_CIK)
    client.submissions(NVDA_CIK)
    assert len(session.calls) == 2 and list(tmp_path.iterdir()) == []


def test_default_clock_is_real_wall_time(tmp_path, fake_time):
    # fake_time replaces sec_client.time without .time(); the cache must not depend on it
    session = FakeSession({URL: FakeResponse(200, {"a": 1})})
    client = SecClient(TEST_UA, session=session, min_interval=0, cache_dir=tmp_path / "c")
    client.submissions(NVDA_CIK)
    entry = json.loads(next((tmp_path / "c").iterdir()).read_text())
    assert entry["fetched_at"] > 1_700_000_000
