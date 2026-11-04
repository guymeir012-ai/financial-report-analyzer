"""Environment configuration. Secrets live only in .env (never logged)."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# Loaded once at import; values already in the environment win.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ConfigError(Exception):
    """Required configuration is missing or malformed."""


def get_sec_user_agent() -> str:
    """Return SEC_EDGAR_USER_AGENT; SEC requires a name and a contact email."""
    value = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip().strip('"').strip("'").strip()
    if not value:
        raise ConfigError("SEC_EDGAR_USER_AGENT is not set")
    if not any(_EMAIL_RE.match(token) for token in value.split()):
        raise ConfigError("SEC_EDGAR_USER_AGENT must contain a contact email")
    return value


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip().strip('"').strip("'").strip()
    return value or None


def get_finnhub_key() -> str | None:
    """FINNHUB_API_KEY, or None when not configured (valuation is then shown as unavailable)."""
    return _optional("FINNHUB_API_KEY")


def get_tiingo_key() -> str | None:
    """TIINGO_API_KEY, or None when not configured."""
    return _optional("TIINGO_API_KEY")


def _cache_dir(env_name: str, default: str) -> Path:
    value = os.environ.get(env_name, "").strip()
    if value:
        return Path(value).expanduser()
    return Path(__file__).resolve().parent.parent / ".cache" / default


def get_sec_cache_dir() -> Path:
    """Directory for the on-disk SEC response cache (SEC_CACHE_DIR, default <repo>/.cache/sec)."""
    return _cache_dir("SEC_CACHE_DIR", "sec")


def get_price_cache_dir() -> Path:
    """Directory for the on-disk price cache (PRICE_CACHE_DIR, default <repo>/.cache/prices)."""
    return _cache_dir("PRICE_CACHE_DIR", "prices")
