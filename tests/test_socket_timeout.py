"""Exercise real HTTP and overlapping guards without external providers."""

import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from tools._subproc import socket_timeout


@pytest.fixture
def slow_http():
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            release.wait(0.5)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("kwargs", [{}, {"timeout": None}])
def test_requests_default_is_bounded(slow_http, kwargs):
    with requests.Session() as session:
        session.trust_env = False
        with socket_timeout(0.05), pytest.raises(requests.exceptions.Timeout):
            session.get(slow_http, **kwargs)


def test_explicit_timeout_nested_exception_and_restore(monkeypatch):
    seen = []
    original_send = requests.sessions.Session.send
    original_socket = socket.getdefaulttimeout()

    def transport(self, request, **kwargs):
        seen.append(kwargs.get("timeout"))
        response = requests.Response()
        response.status_code = 200
        response._content = b"ok"
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", transport)
    with requests.Session() as session:
        session.trust_env = False
        with socket_timeout(2):
            session.get("http://localhost/")
            with pytest.raises(ValueError), socket_timeout(1):
                session.get("http://localhost/")
                session.get("http://localhost/", timeout=(3, 4))
                raise ValueError("restore inner guard")
            session.get("http://localhost/")
        session.get("http://localhost/")
    assert seen == [2, 1, (3, 4), 2, None]
    assert socket.getdefaulttimeout() == original_socket
    assert requests.sessions.Session.send is original_send


def test_overlapping_guards_restore_after_last_exit(monkeypatch):
    entered = threading.Event()
    first_exited = threading.Event()
    original_send = requests.sessions.Session.send
    original_socket = socket.getdefaulttimeout()
    seen = []

    def transport(self, request, **kwargs):
        seen.append(kwargs.get("timeout"))
        response = requests.Response()
        response.status_code = 200
        response._content = b"ok"
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", transport)

    def second():
        with socket_timeout(2):
            entered.set()
            assert first_exited.wait(3)
            with requests.Session() as session:
                session.trust_env = False
                session.get("http://localhost/")
            assert socket.getdefaulttimeout() == 2

    with ThreadPoolExecutor(max_workers=1) as pool:
        with socket_timeout(1):
            future = pool.submit(second)
            assert entered.wait(3)
            assert socket.getdefaulttimeout() == 1
        first_exited.set()
        future.result(timeout=3)
    assert seen == [2]
    assert socket.getdefaulttimeout() == original_socket
    assert requests.sessions.Session.send is original_send
