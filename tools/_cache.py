"""TTL cache for slow public datasets; live quotes and errors are never stored."""

import contextlib
import errno
import hashlib
import json
import os
import sys
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


@contextlib.contextmanager
def file_lock(path, timeout=5):
    """Coordinate processes using a stable lock inode, with bounded waiting."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\0")
                lock.flush()

            def acquire():
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("File lock timed out") from None
                time.sleep(min(0.05, remaining))
        try:
            yield
        finally:
            release()


@contextlib.contextmanager
def atomic_write(path, *, binary=False):
    """Publish a private same-directory file only after its writer succeeds."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_suffix(f".tmp-{uuid4().hex}")
    tmp = None
    try:
        # tempfile may retry ACL denial billions of times on Windows.
        with open(
            candidate,
            "x+b" if binary else "x",
            encoding=None if binary else "utf-8",
            opener=lambda name, flags: os.open(name, flags, 0o600),
        ) as stream:
            tmp = candidate
            yield stream
        os.replace(candidate, path)
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)


def cached(key: str, ttl: int, fetch):
    path = CACHE_DIR / f"{_namespace_prefix(key.partition(':')[0])}-{hashlib.sha256(key.encode()).hexdigest()}.json"
    entry = _read(path, ttl)
    if entry and "data" in entry:
        return entry["data"]
    with contextlib.ExitStack() as stack:
        # shortcut: slow fetches may outlast lock wait; prefer duplicate work to blocking callers indefinitely.
        with contextlib.suppress(OSError):
            stack.enter_context(file_lock(path.with_suffix(".lock")))
        entry = _read(path, ttl)
        if entry and "data" in entry:
            return entry["data"]
        result = fetch()
        rows = result if isinstance(result, list) else [result]
        if not result or any(isinstance(row, dict) and (row.get("error") or row.get("warnings")) for row in rows):
            return result
        try:
            with atomic_write(path) as stream:
                json.dump(
                    {"key": key, "fetched_at": time.time(), "data": result}, stream, ensure_ascii=False, allow_nan=False
                )
        except (OSError, ValueError, TypeError):
            pass
        return result
