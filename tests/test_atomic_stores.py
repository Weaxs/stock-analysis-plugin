"""Denied durable writes must fail once and leave existing data unchanged."""

import builtins
import tempfile
from types import SimpleNamespace

import pytest

from tools import signal_tracker


@pytest.mark.parametrize("store_kind", ["signal", "sec"])
def test_windows_denied_store_does_not_spin_or_destroy_old_file(monkeypatch, tmp_path, store_kind):
    import sec_holders

    attempts = []
    real_os = tempfile._os

    def denied(*args, **kwargs):
        attempts.append(1)
        raise PermissionError("sandbox denied")

    class WindowsDenied:
        name = "nt"
        open = staticmethod(denied)

        def access(self, *args):
            return True

        def __getattr__(self, name):
            return getattr(real_os, name)

    path = tmp_path / "signals.jsonl"
    path.write_text('{"old": true}\n')
    monkeypatch.setattr(tempfile, "_os", WindowsDenied())
    monkeypatch.setattr(tempfile, "TMP_MAX", 4)
    monkeypatch.setattr(builtins, "open", denied)
    if store_kind == "signal":
        with pytest.raises(OSError):
            signal_tracker._save_records(path, [])
    else:
        closed = []
        monkeypatch.setattr(sec_holders._cache, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(
            sec_holders,
            "sec_get",
            lambda *a, **kw: SimpleNamespace(iter_content=lambda **kw: iter([]), close=lambda: closed.append(True)),
        )
        with pytest.raises(ValueError):
            sec_holders.bulk_file({"url": "https://example.invalid/data.zip"})
        assert closed == [True]
    assert attempts == [1]
    assert path.read_text() == '{"old": true}\n'
    assert [p for p in tmp_path.iterdir() if p.suffix != ".lock"] == [path]


@pytest.mark.parametrize("store_kind", ["signal", "sec", "routing"])
def test_replace_failure_keeps_old_store_and_cleans_temporary_file(monkeypatch, tmp_path, store_kind):
    import hashlib
    import io
    import zipfile

    import _cache
    import sec_holders
    import stock_data

    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(stock_data, "_DATA_CACHE_DIR", tmp_path)
    window = {"url": "https://example.invalid/data.zip"}
    name = {
        "signal": "signals.jsonl",
        "routing": "sticky-test.json",
        "sec": f"sec-13f-{hashlib.sha256(window['url'].encode()).hexdigest()[:16]}.zip",
    }[store_kind]
    path = tmp_path / name
    path.write_bytes(b"original store")
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr("data", "valid archive")
    closed = []
    monkeypatch.setattr(
        sec_holders,
        "sec_get",
        lambda *a, **kw: SimpleNamespace(
            iter_content=lambda **kw: iter([blob.getvalue()]), close=lambda: closed.append(True)
        ),
    )

    def deny(*args):
        raise PermissionError("replace denied")

    monkeypatch.setattr(_cache.os, "replace", deny)
    if store_kind == "signal":
        with pytest.raises(PermissionError):
            signal_tracker._save_records(path, [{"new": True}])
    elif store_kind == "sec":
        with pytest.raises(ValueError, match="PermissionError"):
            sec_holders.bulk_file(window)
        assert closed == [True]
    else:
        stock_data._disk_cache_set("sticky-test", {"new": True})
    assert path.read_bytes() == b"original store"
    assert [p for p in tmp_path.iterdir() if p.suffix != ".lock"] == [path]


def test_store_lock_wait_is_bounded_and_released_after_exception(tmp_path):
    from _cache import file_lock

    path = tmp_path / "store.lock"
    with pytest.raises(ValueError), file_lock(path):
        with pytest.raises(TimeoutError, match="File lock timed out"), file_lock(path, timeout=0.02):
            pytest.fail("another writer already owns the lock")
        raise ValueError("writer failed")
    with file_lock(path, timeout=0.02):
        pass
