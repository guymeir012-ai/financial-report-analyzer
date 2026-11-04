import pytest

from backend.config import ConfigError, get_sec_user_agent


def test_returns_valid_user_agent(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Jane Doe jane@example.com")
    assert get_sec_user_agent() == "Jane Doe jane@example.com"


def test_strips_surrounding_quotes(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", '"Jane Doe jane@example.com"')
    assert get_sec_user_agent() == "Jane Doe jane@example.com"


@pytest.mark.parametrize("value", [None, "", "   ", "Jane Doe", "Jane @ example", "jane@nodot"])
def test_missing_or_invalid_user_agent_raises(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    else:
        monkeypatch.setenv("SEC_EDGAR_USER_AGENT", value)
    with pytest.raises(ConfigError):
        get_sec_user_agent()


def test_error_does_not_leak_value(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "secret-name-no-email")
    with pytest.raises(ConfigError) as exc:
        get_sec_user_agent()
    assert "secret-name" not in str(exc.value)


def test_cache_dir_default_and_override(monkeypatch, tmp_path):
    from backend.config import get_sec_cache_dir

    monkeypatch.delenv("SEC_CACHE_DIR", raising=False)
    assert get_sec_cache_dir().parts[-2:] == (".cache", "sec")
    monkeypatch.setenv("SEC_CACHE_DIR", str(tmp_path / "x"))
    assert get_sec_cache_dir() == tmp_path / "x"
    monkeypatch.setenv("SEC_CACHE_DIR", "   ")
    assert get_sec_cache_dir().parts[-2:] == (".cache", "sec")


@pytest.mark.parametrize("raw, expected", [
    (None, None), ("", None), ("   ", None), ("abc123", "abc123"), ('"abc123"', "abc123"), ("'abc123' ", "abc123"),
])
def test_optional_price_keys(monkeypatch, raw, expected):
    from backend.config import get_finnhub_key, get_tiingo_key

    for name, getter in (("FINNHUB_API_KEY", get_finnhub_key), ("TIINGO_API_KEY", get_tiingo_key)):
        if raw is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, raw)
        assert getter() == expected


def test_price_cache_dir_default_and_override(monkeypatch, tmp_path):
    from backend.config import get_price_cache_dir

    monkeypatch.delenv("PRICE_CACHE_DIR", raising=False)
    assert get_price_cache_dir().parts[-2:] == (".cache", "prices")
    monkeypatch.setenv("PRICE_CACHE_DIR", str(tmp_path / "p"))
    assert get_price_cache_dir() == tmp_path / "p"
    monkeypatch.setenv("PRICE_CACHE_DIR", "  ")
    assert get_price_cache_dir().parts[-2:] == (".cache", "prices")
