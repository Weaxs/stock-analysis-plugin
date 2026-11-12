"""Tests for tools/search_intel.py — web search engines (incl. SearXNG fallback)."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.search_intel import _bocha_search, _searxng_search, extract_article, search_comprehensive, search_news


@pytest.fixture(autouse=True)
def no_external_search_credentials(monkeypatch):
    for key in ("TAVILY_API_KEY", "BRAVE_API_KEY", "BOCHA_API_KEY", "SERPAPI_KEY", "SEARXNG_BASE_URLS"):
        monkeypatch.delenv(key, raising=False)


def _mock_requests(payload, ok=True, status_code=200):
    mock_resp = MagicMock()
    mock_resp.ok = ok
    mock_resp.status_code = status_code
    mock_resp.json.return_value = payload
    mock_req = MagicMock()
    mock_req.get.return_value = mock_resp
    mock_req.post.return_value = mock_resp
    return mock_req


class TestSearxngSearch:
    @patch.dict("os.environ", {}, clear=True)
    def test_no_base_urls_returns_empty(self):
        assert _searxng_search("test query") == []

    @patch.dict("os.environ", {"SEARXNG_BASE_URLS": "http://127.0.0.1:8080"})
    def test_parses_results(self):
        payload = {
            "results": [
                {"title": "T1", "url": "http://a.com/1", "content": "snippet one"},
                {"title": "T2", "url": "http://a.com/2", "content": "snippet two"},
            ]
        }
        with patch.dict(sys.modules, {"requests": _mock_requests(payload)}):
            results = _searxng_search("600519 最新消息")
        assert len(results) == 2
        assert results[0]["source"] == "searxng"
        assert results[0]["title"] == "T1"
        assert results[0]["url"] == "http://a.com/1"

    @patch.dict("os.environ", {"SEARXNG_BASE_URLS": "http://bad1:8080, http://good:8080/"})
    def test_fails_over_to_next_instance(self):
        payload = {"results": [{"title": "T", "url": "http://a.com", "content": "c"}]}
        mock_req = MagicMock()
        bad_resp = MagicMock(ok=False)
        good_resp = MagicMock(ok=True)
        good_resp.json.return_value = payload
        mock_req.get.side_effect = [bad_resp, good_resp]
        with patch.dict(sys.modules, {"requests": mock_req}):
            results = _searxng_search("query")
        assert len(results) == 1
        # trailing slash stripped before building the URL
        assert mock_req.get.call_args[0][0] == "http://good:8080/search"

    @patch.dict("os.environ", {"SEARXNG_BASE_URLS": "http://127.0.0.1:8080"})
    def test_exception_raises_with_reason(self):
        mock_req = MagicMock()
        mock_req.get.side_effect = Exception("network down")
        with patch.dict(sys.modules, {"requests": mock_req}), pytest.raises(RuntimeError, match="network down"):
            _searxng_search("query")

    @patch.dict("os.environ", {"SEARXNG_BASE_URLS": "http://down:8080, http://empty:8080"})
    def test_reachable_but_empty_is_not_an_error(self):
        # one instance down, another healthy but zero hits → legit empty, no raise
        mock_req = MagicMock()
        empty_resp = MagicMock(ok=True)
        empty_resp.json.return_value = {"results": []}
        mock_req.get.side_effect = [Exception("conn refused"), empty_resp]
        with patch.dict(sys.modules, {"requests": mock_req}):
            assert _searxng_search("query") == []


class TestBochaSearch:
    @patch.dict("os.environ", {}, clear=True)
    def test_no_api_key_returns_empty(self):
        assert _bocha_search("test query") == []

    @patch.dict("os.environ", {"BOCHA_API_KEY": "k"})
    def test_parses_bing_style_webpages_value(self):
        # Real Bocha response: data.webPages.value[] (issue #13)
        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "totalEstimatedMatches": 10000000,
                    "value": [
                        {"name": "T1", "url": "http://a.com/1", "snippet": "s1"},
                        {"name": "T2", "url": "http://a.com/2", "snippet": None},
                    ],
                }
            },
        }
        with patch.dict(sys.modules, {"requests": _mock_requests(payload)}):
            results = _bocha_search("600519 最新消息")
        assert len(results) == 2
        assert results[0]["source"] == "bocha"
        assert results[0]["title"] == "T1"
        assert results[0]["url"] == "http://a.com/1"
        assert results[0]["snippet"] == "s1"
        # None snippet degrades to empty string, not a crash
        assert results[1]["snippet"] == ""

    @patch.dict("os.environ", {"BOCHA_API_KEY": "k"})
    def test_http_error_raises_with_status(self):
        mock_req = _mock_requests({}, ok=False, status_code=403)
        with patch.dict(sys.modules, {"requests": mock_req}), pytest.raises(RuntimeError, match="403"):
            _bocha_search("query")

    @patch.dict("os.environ", {"BOCHA_API_KEY": "k"})
    def test_body_code_error_raises(self):
        # Bocha can signal failure via HTTP 200 + non-200 envelope code (e.g. quota)
        payload = {"code": 403, "msg": "no quota"}
        with (
            patch.dict(sys.modules, {"requests": _mock_requests(payload)}),
            pytest.raises(RuntimeError, match="no quota"),
        ):
            _bocha_search("query")


class TestSearchNewsEngineErrors:
    @patch.dict("os.environ", {"BOCHA_API_KEY": "k"}, clear=True)
    def test_engine_failure_recorded_in_engines_errors(self):
        mock_req = _mock_requests({}, ok=False, status_code=403)
        with patch.dict(sys.modules, {"requests": mock_req}):
            result = search_news("some query")
        assert result["results"] == []
        assert "bocha" in result["engines_errors"]
        assert "403" in result["engines_errors"]["bocha"]
        # unconfigured engines produce no error entries
        assert "tavily" not in result["engines_errors"]

    @patch.dict("os.environ", {"SERPAPI_KEY": "SECRET123"}, clear=True)
    def test_error_message_scrubs_api_key(self):
        # requests connection errors embed the prepared URL — api_key must not leak
        mock_req = MagicMock()
        mock_req.get.side_effect = Exception("Max retries exceeded with url: /search?api_key=SECRET123&q=x")
        with patch.dict(sys.modules, {"requests": mock_req}):
            result = search_news("some query")
        assert "serpapi" in result["engines_errors"]
        assert "SECRET123" not in result["engines_errors"]["serpapi"]

    @patch.dict("os.environ", {"BOCHA_API_KEY": "k"}, clear=True)
    def test_note_says_engines_failed_not_unconfigured(self):
        mock_req = _mock_requests({}, ok=False, status_code=403)
        with patch.dict(sys.modules, {"requests": mock_req}):
            result = search_news("some query")
        assert result["results"] == []
        assert "No search engines configured" not in result["note"]
        assert "engines_errors" in result["note"]

    @patch.dict("os.environ", {"BOCHA_API_KEY": "k"}, clear=True)
    def test_comprehensive_surfaces_engines_errors(self):
        mock_req = _mock_requests({}, ok=False, status_code=403)
        with patch.dict(sys.modules, {"requests": mock_req}):
            result = search_comprehensive("600519", "贵州茅台")
        assert result["engines_errors"]["bocha"]
        assert "403" in result["engines_errors"]["bocha"]


class TestSearchNewsSearxngFallback:
    @patch.dict("os.environ", {"SEARXNG_BASE_URLS": "http://sx:8080"}, clear=True)
    def test_searxng_used_as_last_resort(self):
        payload = {"results": [{"title": "T", "url": "http://a.com", "content": "c"}]}
        with patch.dict(sys.modules, {"requests": _mock_requests(payload)}):
            result = search_news("some query")
        assert result["engines_used"] == ["searxng"]
        assert result["results"][0]["source"] == "searxng"

    @patch.dict("os.environ", {}, clear=True)
    def test_note_mentions_searxng_when_nothing_configured(self):
        with patch.dict(sys.modules, {"requests": MagicMock()}):
            result = search_news("some query")
        assert result["results"] == []
        assert "SEARXNG_BASE_URLS" in result["note"]


def test_comprehensive_does_not_repeat_unavailable_engine_and_resets_next_call(monkeypatch):

    monkeypatch.setenv("BOCHA_API_KEY", "fixture")
    mock_req = _mock_requests({}, ok=False, status_code=503)
    with patch.dict(sys.modules, {"requests": mock_req}):
        first = search_comprehensive("600519")
        assert "bocha" in first["engines_errors"]
        assert mock_req.post.call_count == 1
        search_news("new invocation")
        assert mock_req.post.call_count == 2


def test_search_requests_use_remaining_budget_and_reach_healthy_fallback(monkeypatch):
    from tools import search_intel

    clock = [0.0]
    seen = []
    monkeypatch.setattr(search_intel._time, "monotonic", lambda: clock[0])
    monkeypatch.setenv("TAVILY_API_KEY", "fixture")
    monkeypatch.setenv("BRAVE_API_KEY", "fixture")
    monkeypatch.setenv("BOCHA_API_KEY", "fixture")
    req = MagicMock()

    def post(url, **kwargs):
        seen.append(kwargs["timeout"])
        if "tavily" in url:
            clock[0] += 30
            raise TimeoutError("slow")
        return _mock_requests(
            {"data": {"webPages": {"value": [{"url": "https://example.invalid", "name": "ok"}]}}}
        ).post()

    def get(url, **kwargs):
        seen.append(kwargs["timeout"])
        clock[0] += 12
        raise TimeoutError("slow")

    req.post.side_effect = post
    req.get.side_effect = get
    with patch.dict(sys.modules, {"requests": req}):
        result = search_news("fixture", 1)
    assert seen == [15, 15, 3]
    assert result["engines_used"] == ["bocha"]
    assert search_intel._SEARCH_STATE.get() is None


def test_searxng_instances_share_search_budget(monkeypatch):
    from tools import search_intel

    clock = [0.0]
    monkeypatch.setattr(search_intel._time, "monotonic", lambda: clock[0])
    monkeypatch.setenv("SEARXNG_BASE_URLS", "http://a,http://b,http://c,http://d")
    for key in ("TAVILY_API_KEY", "BRAVE_API_KEY", "BOCHA_API_KEY", "SERPAPI_KEY"):
        monkeypatch.delenv(key, raising=False)
    req = MagicMock()

    def get(url, **kwargs):
        clock[0] += kwargs["timeout"]
        raise TimeoutError("slow")

    req.get.side_effect = get
    with patch.dict(sys.modules, {"requests": req}):
        result = search_news("fixture")
    assert req.get.call_count == 3
    assert "budget exhausted" in result["engines_errors"]["searxng"]


@pytest.mark.parametrize("status", [200, 400])
def test_comprehensive_retries_healthy_empty_or_query_specific_failure(monkeypatch, status):
    monkeypatch.setenv("BOCHA_API_KEY", "fixture")
    req = _mock_requests({"data": {"webPages": {"value": []}}}, ok=status == 200, status_code=status)
    with patch.dict(sys.modules, {"requests": req}):
        result = search_comprehensive("600519")
    assert len(result["dimensions"]) == 6
    assert req.post.call_count == 6


@pytest.mark.parametrize("url", ["https://example.com/annual.PDF", "https://example.com/download?id=1"])
def test_extract_pdf_honors_long_text_limit_and_metadata(url):
    response = MagicMock()
    response.headers = {"content-type": "application/pdf"}
    response.iter_content.return_value = [b"%PDF-1.7\n", b"fixture"]
    response.raise_for_status.return_value = None
    pages = [SimpleNamespace(extract_text=lambda: "abcdefgh"), SimpleNamespace(extract_text=lambda: "ijkl")]
    reader = SimpleNamespace(pages=pages, metadata={"/Title": "年度报告", "/Author": "示例公司"})
    pypdf = SimpleNamespace(PdfReader=MagicMock(return_value=reader))

    with (
        patch.dict(sys.modules, {"pypdf": pypdf}),
        patch("newspaper.network.do_request", return_value=response) as fetch,
    ):
        result = extract_article(url, max_chars=10)

    assert result["title"] == "年度报告"
    assert result["authors"] == ["示例公司"]
    assert result["text"] == "abcdefgh\ni"
    assert result["truncated"] is True
    assert result["page_count"] == 2
    assert fetch.call_args.args[0] == url
    config = fetch.call_args.args[1]
    assert config.allow_binary_content is True
    assert config.requests_params["stream"] is True
    assert config.requests_params["timeout"] == 30


def test_extract_sec_document_sends_declared_user_agent(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "stock-analysis test@example.com")
    response = MagicMock(headers={"content-type": "application/pdf"})
    response.iter_content.return_value = [b"%PDF-1.7\n"]
    reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "10-K")], metadata={})
    pypdf = SimpleNamespace(PdfReader=MagicMock(return_value=reader))
    with (
        patch.dict(sys.modules, {"pypdf": pypdf}),
        patch("newspaper.network.do_request", return_value=response) as fetch,
    ):
        result = extract_article("https://www.sec.gov/Archives/edgar/data/320193/x.pdf")
    assert result["text"] == "10-K"
    assert fetch.call_args.args[1].browser_user_agent == "stock-analysis test@example.com"


def test_extract_sec_document_requires_user_agent(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with patch("newspaper.network.do_request") as fetch:
        result = extract_article("https://www.sec.gov/Archives/edgar/data/320193/x.htm")
    assert "SEC_USER_AGENT" in result["error"]
    fetch.assert_not_called()


def test_extract_html_honors_custom_limit():
    article = SimpleNamespace(
        title="标题",
        text="0123456789abcdef",
        authors=[],
        publish_date=None,
        top_image=None,
        download=MagicMock(),
        parse=lambda: None,
    )
    response = MagicMock(
        headers={"content-type": "text/html"}, content=b"<html>fixture</html>", status_code=200, history=[]
    )
    with patch("newspaper.Article", return_value=article), patch("newspaper.network.do_request", return_value=response):
        result = extract_article("https://example.com/story", max_chars=10)
    assert result["text"] == "0123456789"
    assert result["truncated"] is True
    article.download.assert_called_once_with(input_html="<html>fixture</html>")


def test_extract_html_detects_gbk_without_charset_header():
    article = SimpleNamespace(
        title="标题",
        text="中文正文",
        authors=[],
        publish_date=None,
        top_image=None,
        download=MagicMock(),
        parse=MagicMock(),
    )
    html = '<html><head><meta charset="gb2312"></head><body>中文正文</body></html>'
    response = MagicMock(headers={"content-type": "text/html"}, content=html.encode("gbk"), status_code=200, history=[])
    direct = SimpleNamespace(get=MagicMock(side_effect=AssertionError("unsafe direct request")))
    with (
        patch("newspaper.Article", return_value=article),
        patch("newspaper.network.do_request", return_value=response),
        patch.dict(sys.modules, {"requests": direct}),
    ):
        result = extract_article("https://example.com/gbk", max_chars=200)
    assert result["text"] == "中文正文"
    assert "中文正文" in article.download.call_args.kwargs["input_html"]


@pytest.mark.parametrize("content_type", ["video/mp4", "application/zip", "image/png"])
def test_extract_refuses_non_pdf_binary_without_reading_body(content_type):
    response = MagicMock(headers={"content-type": content_type})
    with (
        patch("newspaper.network.do_request", return_value=response),
        patch("newspaper.network.get_html") as get_html,
    ):
        result = extract_article("https://example.com/file")
    assert "Unsupported binary content-type" in result["error"]
    get_html.assert_not_called()
    response.close.assert_called_once()


def test_extract_rejects_private_host_before_fetch():
    with patch("newspaper.network.session.get") as fetch:
        result = extract_article("http://127.0.0.1:8000/admin")
    assert "non-public host" in result["error"]
    fetch.assert_not_called()


def test_extract_cli_validation_error_exits_nonzero():
    import subprocess
    from pathlib import Path

    result = subprocess.run(
        [sys.executable, "tools/search_intel.py", "extract", "https://example.com", "--max-chars", "0"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "max_chars" in result.stdout


@pytest.mark.parametrize("limit", [0, 200001, 1.5, True])
def test_extract_rejects_invalid_text_limit(limit):
    result = extract_article("https://example.com/story", max_chars=limit)
    assert "max_chars" in result["error"]
