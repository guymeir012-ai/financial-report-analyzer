"""On-disk JSON cache shared by the SEC and price clients.

One file per URL, named by the SHA-256 of the URL (never by user input), holding
{"url", "fetched_at", "data"}. Writes are atomic and the directory is private (0700).
Callers decide freshness from `fetched_at`; a corrupt or mismatched file reads as a miss.
"""
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from time import time as _wall_time
from typing import Any, Callable

log = logging.getLogger(__name__)


class JsonDiskCache:
    def __init__(self, cache_dir: str | os.PathLike[str], wall_clock: Callable[[], float] | None = None) -> None:
        self._dir = Path(cache_dir)
        self._wall_clock = wall_clock or _wall_time

    def now(self) -> float:
        return self._wall_clock()

    def path(self, url: str) -> Path:
        return self._dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"

    def read(self, url: str) -> tuple[float, Any] | None:
        """(fetched_at, data) for this URL, or None when absent or unusable."""
        path = self.path(url)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = entry["fetched_at"]
            if (entry["url"] != url or "data" not in entry
                    or isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float))):
                return None
            return float(fetched_at), entry["data"]
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("Ignoring unreadable cache file")
            return None

    def write(self, url: str, data: Any) -> None:
        path = self.path(url)
        tmp_name = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path.parent, 0o700)
            payload = {"url": url, "fetched_at": self._wall_clock(), "data": data}
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp_name, path)
            tmp_name = None
        except (OSError, TypeError, ValueError):
            log.warning("Could not write cache file")
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
