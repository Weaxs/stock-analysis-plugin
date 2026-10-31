"""TTL cache for slow public datasets; live quotes and errors are never stored."""

import contextlib
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

CACHE_DIR = Path(os.environ.get("STOCK_CACHE_DIR") or Path.home() / ".stock-analysis" / "cache")
DAY = 86400
WEEK = 7 * DAY


def _namespace_prefix(namespace):
    return hashlib.sha256(namespace.encode()).hexdigest()[:12]


def _read(path, ttl):
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if 0 <= time.time() - entry["fetched_at"] < ttl:
            return entry
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def cached_values(namespace, ttl):
    for path in CACHE_DIR.glob(f"{_namespace_prefix(namespace)}-*.json"):
        entry = _read(path, ttl)
        if entry and str(entry.get("key", "")).startswith(namespace + ":"):
            yield entry.get("data")


def cached(key: str, ttl: int, fetch):
    path = CACHE_DIR / f"{_namespace_prefix(key.partition(':')[0])}-{hashlib.sha256(key.encode()).hexdigest()}.json"
    entry = _read(path, ttl)
    if entry and "data" in entry:
        return entry["data"]
    result = fetch()
    rows = result if isinstance(result, list) else [result]
    if not result or any(isinstance(row, dict) and (row.get("error") or row.get("warnings")) for row in rows):
        return result
    tmp = None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        candidate = path.with_suffix(f".tmp-{uuid4().hex}")
        # Exclusive creation fails immediately on Windows ACL denial; tempfile may retry billions of times.
        with open(candidate, "x", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            tmp = candidate
            json.dump(
                {"key": key, "fetched_at": time.time(), "data": result}, stream, ensure_ascii=False, allow_nan=False
            )
        os.replace(tmp, path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                Path(tmp).unlink(missing_ok=True)
    return result
