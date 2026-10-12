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
