"""Tests for tools/search_intel.py — web search engines (incl. SearXNG fallback)."""

import sys
from unittest.mock import MagicMock, patch

from tools.search_intel import _searxng_search, search_news


def _mock_requests(payload, ok=True):
    mock_resp = MagicMock()
    mock_resp.ok = ok
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
    def test_exception_returns_empty(self):
        mock_req = MagicMock()
        mock_req.get.side_effect = Exception("network down")
        with patch.dict(sys.modules, {"requests": mock_req}):
            assert _searxng_search("query") == []


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
