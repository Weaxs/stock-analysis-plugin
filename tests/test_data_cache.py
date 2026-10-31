import importlib
import sys
from pathlib import Path


def test_cache_hit_expiry_and_failures(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    cache = importlib.import_module("_cache")
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    now = [1000.0]
    monkeypatch.setattr(cache.time, "time", lambda: now[0])
    calls = []

    def fetch():
        calls.append(1)
        return {"price": len(calls)}

    assert cache.cached("data", 10, fetch) == {"price": 1}
    assert cache.cached("data", 10, fetch) == {"price": 1}
    now[0] = 1011
    assert cache.cached("data", 10, fetch) == {"price": 2}
    assert cache.cached("error", 10, lambda: {"error": "down"}) == {"error": "down"}
    assert cache.cached("error", 10, lambda: {"price": 3}) == {"price": 3}


def test_corrupt_and_partial_cache_are_refetched(monkeypatch, tmp_path):
    import _cache

    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    _cache.cached("value", 10, lambda: {"ok": 1})
    next(tmp_path.glob("*.json")).write_text("broken")
    assert _cache.cached("value", 10, lambda: {"ok": 2}) == {"ok": 2}
    _cache.cached("partial", 10, lambda: {"ok": 1, "warnings": ["missing section"]})
    assert _cache.cached("partial", 10, lambda: {"ok": 2}) == {"ok": 2}


def test_cache_replace_failure_does_not_fail_fetch(monkeypatch, tmp_path):
    import _cache

    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)

    def fail(*_):
        raise OSError("unwritable")

    monkeypatch.setattr(_cache.os, "replace", fail)
    assert _cache.cached("value", 10, lambda: {"ok": 1}) == {"ok": 1}
    assert not list(tmp_path.iterdir())


def test_windows_cache_denial_does_not_retry(monkeypatch, tmp_path):
    import os
    import tempfile

    import _cache

    attempts = []
    real_os = os

    class DeniedWindows:
        name = "nt"

        def __getattr__(self, name):
            return getattr(real_os, name)

        def access(self, *args):
            return True

        def open(self, *args, **kwargs):
            attempts.append(1)
            raise PermissionError("sandbox denied")

    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(tempfile, "_os", DeniedWindows())
    monkeypatch.setattr(_cache, "os", DeniedWindows())
    monkeypatch.setattr(tempfile, "TMP_MAX", 4)
    monkeypatch.setattr(Path, "open", lambda *a, **kw: DeniedWindows().open())
    assert _cache.cached("denied", 10, lambda: {"ok": 1}) == {"ok": 1}
    # One read attempt and at most one write attempt; never retry permission denial.
    assert len(attempts) <= 2


def test_cache_directory_override(tmp_path):
    import json
    import os
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", "import tools._cache as c; c.cached('env', 60, lambda: {'ok': 1}); print(c.CACHE_DIR)"],
        env={**os.environ, "STOCK_CACHE_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == str(tmp_path)
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["data"] == {"ok": 1}


def test_concurrent_cache_writes_are_complete(monkeypatch, tmp_path):
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import _cache

    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    barrier = threading.Barrier(8)

    def fetch(value):
        barrier.wait(timeout=3)
        return {"value": value, "payload": "x" * 10000}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_cache.cached, "same-key", 60, lambda value=i: fetch(value)) for i in range(8)]
        results = [future.result(timeout=5) for future in futures]
    assert [result["value"] for result in results] == list(range(8))
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    data = json.loads(files[0].read_text())["data"]
    assert data in results


def test_cache_keeps_owner_only_permissions(monkeypatch, tmp_path):
    import os
    import stat

    import _cache
    import pytest

    if os.name == "nt":
        pytest.skip("POSIX permission bits")
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    previous = os.umask(0o022)
    try:
        assert _cache.cached("private", 60, lambda: {"ok": 1}) == {"ok": 1}
    finally:
        os.umask(previous)
    assert stat.S_IMODE(next(tmp_path.glob("*.json")).stat().st_mode) == 0o600
