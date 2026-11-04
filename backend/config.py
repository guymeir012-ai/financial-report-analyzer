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


def get_sec_cache_dir() -> Path:
    """Directory for the on-disk SEC response cache (SEC_CACHE_DIR, default <repo>/.cache/sec)."""
    value = os.environ.get("SEC_CACHE_DIR", "").strip()
    if value:
        return Path(value).expanduser()
    return Path(__file__).resolve().parent.parent / ".cache" / "sec"
