"""The shared on-disk JSON cache used by the SEC and price clients."""
import hashlib
import json
import os
import stat

import pytest

from backend.cache import JsonDiskCache

URL = "https://example.test/a?b=1"


def test_round_trip_and_filename(tmp_path):
    cache = JsonDiskCache(tmp_path / "c", wall_clock=lambda: 123.0)
    assert cache.read(URL) is None
    cache.write(URL, {"x": [1, "שלום"]})
    assert cache.read(URL) == (123.0, {"x": [1, "שלום"]})
    (path,) = (tmp_path / "c").iterdir()
    assert path.name == hashlib.sha256(URL.encode()).hexdigest() + ".json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"url": URL, "fetched_at": 123.0, "data": {"x": [1, "שלום"]}}
    assert stat.S_IMODE(os.stat(tmp_path / "c").st_mode) == 0o700


@pytest.mark.parametrize("junk", [
    "", "nope", "[]", "{}", '{"url": "x"}', '{"url": "%s", "data": 1}' % URL,
    '{"url": "%s", "fetched_at": true, "data": 1}' % URL,
    '{"url": "https://other.test", "fetched_at": 1, "data": {"evil": 1}}',
])
def test_corrupt_or_foreign_entry_is_ignored(tmp_path, junk):
    cache = JsonDiskCache(tmp_path)
    (tmp_path / (hashlib.sha256(URL.encode()).hexdigest() + ".json")).write_text(junk, encoding="utf-8")
    assert cache.read(URL) is None


def test_failed_write_leaves_nothing_behind(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    cache = JsonDiskCache(tmp_path / "c")
    cache.write(URL, {"a": 1})
    assert cache.read(URL) is None and list((tmp_path / "c").iterdir()) == []


def test_unwritable_location_is_harmless(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cache = JsonDiskCache(blocker / "sub")
    cache.write(URL, {"a": 1})
    assert cache.read(URL) is None


def test_default_clock_is_wall_time(tmp_path):
    cache = JsonDiskCache(tmp_path)
    cache.write(URL, 1)
    assert cache.read(URL)[0] > 1_700_000_000
