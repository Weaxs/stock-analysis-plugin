"""Tests for tools/search_intel.py — web search engines (incl. SearXNG fallback)."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from tools.search_intel import _bocha_search, _searxng_search, search_comprehensive, search_news


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
