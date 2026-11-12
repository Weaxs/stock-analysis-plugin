import re
import subprocess
import sys
import time
from argparse import Namespace
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from tools import stock_data


def test_fund_quote_returns_published_nav_with_provider_date(monkeypatch):
    body = """
        var fS_name = "建信新兴市场混合(QDII)C";
        var fS_code = "018147";
        var Data_netWorthTrend = [
            {"x":1703952000000,"y":1.998,"equityReturn":-2.77,"unitMoney":""},
            {"x":1704038400000,"y":2.010,"equityReturn":0.60,"unitMoney":""}
        ];
        var Data_ACWorthTrend = [[1703952000000,1.998],[1704038400000,2.010]];
    """
    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(text=body, raise_for_status=lambda: None),
    )
    monkeypatch.setattr(
        "akshare.fund_value_estimation_em",
        lambda: pd.DataFrame(),
    )

    result = stock_data.cmd_fund_quote(Namespace(symbol="018147"))

    assert result["symbol"] == "018147"
    assert result["name"] == "建信新兴市场混合(QDII)C"
    assert result["asset_type"] == "open_end_fund"
    assert result["nav"] == 2.01
    assert result["accumulated_nav"] == 2.01
    assert result["change_pct"] == 0.6
    assert result["nav_date"] == "2024-01-01"
    assert result["data_as_of"] == "2024-01-01"
    assert result["as_of"] == "2024-01-01"
    assert result["as_of_basis"] == "provider"
    assert result["stale"] is True
    assert result["source"] == "eastmoney"
    assert result["source_url"] == "https://fund.eastmoney.com/018147.html"
    assert result["fetched_at"].endswith("+00:00")
    assert result["estimated_nav"] is None
    assert result["estimated_at"] is None
    assert result["estimate_status"] == "unavailable"
    assert "not a real-time price" in result["note"]
    assert "QDII" in result["note"]
    assert "price" not in result


def test_fund_quote_keeps_intraday_estimate_separate_from_official_nav(monkeypatch):
    body = """
        var fS_name = "示例指数基金";
        var fS_code = "018147";
        var Data_netWorthTrend = [{"x":1704038400000,"y":2.010,"equityReturn":0.60,"unitMoney":""}];
        var Data_ACWorthTrend = [[1704038400000,2.010]];
    """
    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(text=body, raise_for_status=lambda: None),
    )
    monkeypatch.setattr(
        "akshare.fund_value_estimation_em",
        lambda: pd.DataFrame(
            {
                "基金代码": ["018147"],
                "2024-01-02-估算数据-估算值": ["2.02"],
                "2024-01-02-估算数据-估算增长率": ["0.50%"],
            }
        ),
    )

    result = stock_data.cmd_fund_quote(Namespace(symbol="018147"))

    assert result["nav"] == 2.01
    assert result["nav_date"] == "2024-01-01"
    assert result["data_as_of"] == "2024-01-01"
    assert result["estimated_nav"] == 2.02
    assert result["estimated_change_pct"] == 0.5
    assert result["estimated_at"] == "2024-01-02"
    assert result["estimate_status"] == "available"
    assert "price" not in result


def test_fund_quote_placeholder_estimate_is_unavailable(monkeypatch):
    body = """
        var fS_name = "示例指数基金";
        var fS_code = "018147";
        var Data_netWorthTrend = [{"x":1704038400000,"y":2.010,"equityReturn":0.60,"unitMoney":""}];
        var Data_ACWorthTrend = [[1704038400000,2.010]];
    """
    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(text=body, raise_for_status=lambda: None),
    )
    monkeypatch.setattr(
        "akshare.fund_value_estimation_em",
        lambda: pd.DataFrame(
            {
                "基金代码": ["018147"],
                "2024-01-02-估算数据-估算值": ["---"],
                "2024-01-02-估算数据-估算增长率": ["---"],
            }
        ),
    )

    result = stock_data.cmd_fund_quote(Namespace(symbol="018147"))

    assert result["nav"] == 2.01
    assert result["estimated_nav"] is None
    assert result["estimated_change_pct"] is None
    assert result["estimated_at"] is None
    assert result["estimate_status"] == "unavailable"


def test_quote_recognizes_tiaa_without_running_stock_quote_providers(monkeypatch):
    calls = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, **kwargs):
            calls.append(url)
            if url == "https://xueqiu.com/":
                return SimpleNamespace(raise_for_status=lambda: None)
            assert url == "https://stock.xueqiu.com/v5/stock/quote.json"
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "error_code": 0,
                    "data": {
                        "quote": {
                            "symbol": "TIAA001001",
                            "name": "长钱账户",
                            "type": 29,
                            "exchange": "TIA",
                            "current": None,
                            "percent": None,
                            "timestamp": None,
                        }
                    },
                },
            )

    monkeypatch.setattr(requests, "Session", FakeSession)

    older = "".join(
        f"<url><loc>https://youzhiyouxing.cn/lite-posts/{20000 + index}</loc><lastmod>2026-01-{index + 1:02}</lastmod></url>"
        for index in range(29)
    )
    sitemap = f"""<?xml version='1.0' encoding='UTF-8'?>
        <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
          <url><loc>https://youzhiyouxing.cn/lite-posts/9999</loc><lastmod>2026-11-05</lastmod></url>
          <url><loc>https://youzhiyouxing.cn/lite-posts/10000</loc><lastmod>2026-11-06</lastmod></url>
          {older}
          <url><loc>https://youzhiyouxing.cn/lite-posts/3388</loc><lastmod>2025-12-01</lastmod></url>
        </urlset>
    """.encode()
    article = """
        <html><head>
          <meta property="og:title" content="长钱账户公开披露">
          <meta property="og:description" content="长钱账户示例账户与配置披露">
        </head><body>
          <span>2026年10月30日</span><h2>长钱账户公开披露</h2>
          <p>我们看到，2025年，长钱账户示例账户扣费后收益率为 4.10%。</p>
          <p>长钱账户：天弘恒生科技ETF联接A（012348）及备选基金。</p>
          <script>不应进入正文的脚本</script>
        </body></html>
    """

    def public_get(url, **kwargs):
        if url == "https://www.csisc.cn/zbscbzw/c100264/list_jjbm.shtml":
            table = "<table><tr><td>1</td><td>嘉实财富管理有限公司</td><td>A001</td></tr></table>"
            return SimpleNamespace(content=table.encode(), raise_for_status=lambda: None)
        if url == "https://youzhiyouxing.cn/sitemap.xml":
            return SimpleNamespace(content=sitemap, raise_for_status=lambda: None)
        if url == "https://youzhiyouxing.cn/n/lite-posts/9999":
            return SimpleNamespace(text=article, raise_for_status=lambda: None)
        if url == "https://youzhiyouxing.cn/n/lite-posts/3175":
            performance = """
                <html><head><meta property="og:title" content="示例账户收益披露"></head><body>
                  <span>2026年7月28日</span><h2>示例账户收益披露</h2>
                  <p>截至 6 月 30 日，长钱账户示例账户今年以来扣费后上涨 5.40%。</p>
                </body></html>
            """
            return SimpleNamespace(text=performance, raise_for_status=lambda: None)
        if url == "https://youzhiyouxing.cn/n/lite-posts/3388":
            component = """
                <html><head><meta property="og:title" content="组合成分基金披露"></head><body>
                  <span>2025年12月1日</span><h2>组合成分基金披露</h2>
                  <p>长钱账户成分基金：天弘恒生科技ETF联接A（012348）。</p>
                </body></html>
            """
            return SimpleNamespace(text=component, raise_for_status=lambda: None)
        if url == "https://youzhiyouxing.cn/n/lite-posts/10000":
            unrelated = """
                <html><head><meta property="og:title" content="无关投资故事"></head><body>
                  <h2>无关投资故事</h2><p>普通内容。</p>
                  <div>8 条回应</div><p>评论提到长钱账户示例账户收益。</p>
                </body></html>
            """
            return SimpleNamespace(text=unrelated, raise_for_status=lambda: None)
        assert re.fullmatch(r"https://youzhiyouxing.cn/n/lite-posts/200\d\d", url)
        return SimpleNamespace(text="<html><body><p>无关内容</p></body></html>", raise_for_status=lambda: None)

    monkeypatch.setattr(requests, "get", public_get)
    monkeypatch.setenv("TAVILY_API_KEY", "test-search-key")

    def public_post(url, **kwargs):
        assert url == "https://api.tavily.com/search"
        assert kwargs["json"]["api_key"] == "test-search-key"
        assert "长钱账户" in kwargs["json"]["query"]
        return SimpleNamespace(
            ok=True,
            status_code=200,
            json=lambda: {
                "results": [
                    {
                        "title": "长钱账户示例账户收益披露",
                        "url": "https://youzhiyouxing.cn/n/lite-posts/3175",
                        "content": "长钱账户示例账户收益",
                    }
                ]
            },
        )

    monkeypatch.setattr(requests, "post", public_post)

    def stock_provider_must_not_run(*args, **kwargs):
        raise AssertionError("stock provider must not run for TIAA")

    monkeypatch.setattr("yfinance.Ticker", stock_provider_must_not_run)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("LONGBRIDGE_APP_KEY", raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    result = stock_data.cmd_quote(Namespace(symbol="TIAA001001"))

    assert "error" not in result
    assert result["symbol"] == "TIAA001001"
    assert result["name"] == "长钱账户"
    assert result["asset_type"] == "investment_advisory_portfolio"
    assert result["type_description"] == "雪球投顾组合，无统一单位净值"
    assert result["manager"] is None
    assert result["nav"] is None
    assert result["example_account_performance"] is None
    assert result["source"] == "xueqiu"
    assert result["source_url"] == "https://xueqiu.com/S/TIAA001001"
    assert result["advisor_code"] == "A001"
    assert result["advisor_org"] == "嘉实财富管理有限公司"
    assert result["advisor_mapping_basis"] == "tiaa_prefix_best_effort"
    assert result["advisor_source_url"] == "https://www.csisc.cn/zbscbzw/c100264/list_jjbm.shtml"
    assert result["advisor_retrieved_at"].endswith("+00:00")
    assert result["data_as_of"] is None
    assert result["fetched_at"].endswith("+00:00")
    assert "无统一净值" in result["note"]
    assert "登录" in result["note"]
    assert "公开披露" in result["note"]
    assert result["disclosure_status"] == "partial_public"
    assert result["disclosures_partial"] is True
    assert result["disclosures_as_of"] == "2026-10-30"
    assert len(result["official_disclosures"]) == 3
    assert [item["title"] for item in result["official_disclosures"]] == [
        "长钱账户公开披露",
        "示例账户收益披露",
        "组合成分基金披露",
    ]
    disclosure = result["official_disclosures"][0]
    assert disclosure["title"] == "长钱账户公开披露"
    assert disclosure["published_at"] == "2026-10-30"
    assert disclosure["url"] == "https://youzhiyouxing.cn/n/lite-posts/9999"
    assert disclosure["summary"] == "长钱账户示例账户与配置披露"
    assert disclosure["source"] == "youzhiyouxing"
    assert disclosure["partial"] is True
    assert "4.10%" in disclosure["text"]
    assert "012348" in disclosure["text"]
    assert "不应进入正文" not in disclosure["text"]
    assert calls == ["https://xueqiu.com/", "https://stock.xueqiu.com/v5/stock/quote.json"]


def test_quote_does_not_route_unknown_tiaa_to_youzhiyouxing(monkeypatch):
    public_calls = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, **kwargs):
            if url == "https://xueqiu.com/":
                return SimpleNamespace(raise_for_status=lambda: None)
            assert url == "https://stock.xueqiu.com/v5/stock/quote.json"
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "error_code": 0,
                    "data": {"quote": {"symbol": "TIAA003001", "name": "步步盈", "type": 29}},
                },
            )

    def unexpected_public_fetch(url, **kwargs):
        public_calls.append(url)
        if url == "https://www.csisc.cn/zbscbzw/c100264/list_jjbm.shtml":
            table = "<table><tr><td>3</td><td>上海华夏财富投资管理有限公司</td><td>A003</td></tr></table>"
            return SimpleNamespace(content=table.encode(), raise_for_status=lambda: None)
        return SimpleNamespace(
            content=b"<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'></urlset>",
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(requests, "Session", FakeSession)
    monkeypatch.setattr(requests, "get", unexpected_public_fetch)

    result = stock_data.cmd_quote(Namespace(symbol="TIAA003001"))

    assert result["name"] == "步步盈"
    assert result["advisor_code"] == "A003"
    assert result["advisor_org"] == "上海华夏财富投资管理有限公司"
    assert result["advisor_mapping_basis"] == "tiaa_prefix_best_effort"
    assert result["official_disclosures"] == []
    assert result["disclosure_status"] == "unsupported_provider"
    assert all("youzhiyouxing.cn" not in url for url in public_calls)

    monkeypatch.setenv("TAVILY_API_KEY", "test-search-key")

    def public_post(url, **kwargs):
        assert url == "https://api.tavily.com/search"
        assert "TIAA003001" in kwargs["json"]["query"]
        assert "步步盈" in kwargs["json"]["query"]
        return SimpleNamespace(
            ok=True,
            status_code=200,
            json=lambda: {
                "results": [
                    {
                        "title": "步步盈公开报告",
                        "url": "https://advisor.example.com/report",
                        "content": "步步盈示例账户与成分基金公开说明",
                    }
                ]
            },
        )

    monkeypatch.setattr(requests, "post", public_post)
    searched = stock_data.cmd_quote(Namespace(symbol="TIAA003001"))

    assert searched["official_disclosures"] == []
    assert searched["disclosure_status"] == "search_partial"
    assert searched["disclosure_provider"] == "web_search"
    assert searched["public_search_results"] == [
        {
            "title": "步步盈公开报告",
            "url": "https://advisor.example.com/report",
            "snippet": "步步盈示例账户与成分基金公开说明",
            "source": "tavily",
            "publisher": "advisor.example.com",
            "verification": "unverified_search_result",
            "partial": True,
        }
    ]
    assert all("youzhiyouxing.cn" not in url for url in public_calls)


def test_quote_failure_for_six_digit_code_points_to_fund_quote(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise ValueError("sina: no quote; tencent: no quote")

    monkeypatch.setattr(stock_data, "quote_a", unavailable)
    result = stock_data.cmd_quote(Namespace(symbol="018147"))
    assert result["error"].startswith("sina: no quote"), "provider evidence stays in the error key"
    assert "get_fund_quote" in result["hint"] and "018147" in result["hint"]

    assert "hint" not in stock_data.cmd_quote(Namespace(symbol="600519")), "stock-only prefixes keep the bare error"

    monkeypatch.setattr(stock_data, "quote_yf", unavailable)
    us = stock_data.cmd_quote(Namespace(symbol="AAPL"))
    assert "hint" not in us, "only six-digit codes can be open-end funds"


def test_tiaa_disclosure_deadline_cancels_slow_articles(monkeypatch):
    from tools import tiaa_disclosures

    monkeypatch.setattr(tiaa_disclosures, "_DISCLOSURE_DEADLINE", 0.05)
    monkeypatch.setattr(tiaa_disclosures, "_search_urls", lambda *_args: [])
    urls = "".join(
        f"<url><loc>https://youzhiyouxing.cn/lite-posts/{30000 + index}</loc><lastmod>2026-01-01</lastmod></url>"
        for index in range(40)
    )
    sitemap = f"<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>{urls}</urlset>".encode()
    calls = []
    timeouts = []

    def public_get(url, **kwargs):
        if url == tiaa_disclosures._SITEMAP:
            return SimpleNamespace(content=sitemap, raise_for_status=lambda: None)
        calls.append(url)
        timeouts.append(kwargs.get("timeout"))
        time.sleep(0.2)
        return SimpleNamespace(text="<html><body>unrelated</body></html>", raise_for_status=lambda: None)

    monkeypatch.setattr(requests, "get", public_get)
    started = time.monotonic()
    with pytest.raises(tiaa_disclosures._DisclosureTimeout):
        tiaa_disclosures.public_disclosures("TIAA009999", "长钱账户")

    assert time.monotonic() - started < 1
    assert len(calls) < 40
    assert timeouts and set(timeouts) == {(1, 3)}


def test_tiaa_search_also_respects_disclosure_deadline(monkeypatch):
    from tools import tiaa_disclosures

    monkeypatch.setattr(tiaa_disclosures, "_DISCLOSURE_DEADLINE", 0.05)
    monkeypatch.setattr(tiaa_disclosures, "_sitemap_urls", lambda: [])
    monkeypatch.setattr(tiaa_disclosures, "_search_urls", lambda *_args: time.sleep(0.2) or [])
    started = time.monotonic()
    with pytest.raises(tiaa_disclosures._DisclosureTimeout):
        tiaa_disclosures.public_disclosures("TIAA009998", "长钱账户")
    assert time.monotonic() - started < 1


def test_search_only_article_is_not_truncated_by_sitemap(monkeypatch):
    from tools import tiaa_disclosures

    monkeypatch.setattr(
        tiaa_disclosures,
        "_sitemap_urls",
        lambda: [f"https://youzhiyouxing.cn/n/lite-posts/{20000 + i}" for i in range(40)],
    )
    target = "https://youzhiyouxing.cn/n/lite-posts/29999"
    monkeypatch.setattr(tiaa_disclosures, "_search_urls", lambda *_args: [target])
    seen = []
    monkeypatch.setattr(
        tiaa_disclosures,
        "_article",
        lambda url, _name: (
            seen.append(url)
            or ({"url": url, "published_at": "2026-01-01", "title": "target"} if url == target else None)
        ),
    )
    result = tiaa_disclosures.public_disclosures("TIAA009997", "长钱账户")
    assert target in seen
    assert result["items"] == [{"url": target, "published_at": "2026-01-01", "title": "target"}]


def test_concurrent_timeout_errors_do_not_escape(monkeypatch):
    from concurrent.futures import TimeoutError as FutureTimeoutError

    from tools import tiaa_disclosures

    monkeypatch.setattr(tiaa_disclosures, "_sitemap_urls", lambda: [])
    monkeypatch.setattr(tiaa_disclosures, "_search_urls", lambda *_args: (_ for _ in ()).throw(FutureTimeoutError()))
    with pytest.raises(tiaa_disclosures._DisclosureTimeout):
        tiaa_disclosures.public_disclosures("TIAA009996", "长钱账户")

    monkeypatch.setattr(tiaa_disclosures, "_sitemap_urls", lambda: ["https://youzhiyouxing.cn/n/lite-posts/1"])
    monkeypatch.setattr(tiaa_disclosures, "_article", lambda _url, _name: (_ for _ in ()).throw(FutureTimeoutError()))
    with pytest.raises(tiaa_disclosures._DisclosureTimeout):
        tiaa_disclosures.public_disclosures("TIAA009995", "长钱账户")


def test_disclosure_deadline_also_allows_process_exit(monkeypatch):
    script = """
import time
from tools import tiaa_disclosures as disclosures

disclosures._DISCLOSURE_DEADLINE = 0.05
disclosures._sitemap_urls = lambda: []
disclosures._search_urls = lambda *_args: time.sleep(2) or []
started = time.monotonic()
try:
    disclosures.public_disclosures("TIAA009994", "长钱账户")
except disclosures._DisclosureTimeout:
    pass
assert time.monotonic() - started < 1
"""
    started = time.monotonic()
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=1)
    assert completed.returncode == 0, completed.stderr
    assert time.monotonic() - started < 1


def test_disclosure_timeout_is_retried_not_cached(monkeypatch):
    from tools import tiaa_disclosures

    monkeypatch.setattr(tiaa_disclosures, "_DISCLOSURE_DEADLINE", 0.05)
    monkeypatch.setattr(tiaa_disclosures, "_search_urls", lambda *_args: [])
    monkeypatch.setattr(tiaa_disclosures, "_sitemap_urls", lambda: ["https://youzhiyouxing.cn/n/lite-posts/1"])
    attempts = []

    def article(url, _name):
        attempts.append(url)
        if len(attempts) == 1:
            time.sleep(0.2)
            return None
        return {"url": url, "published_at": "2026-01-01", "title": "recovered"}

    monkeypatch.setattr(tiaa_disclosures, "_article", article)
    with pytest.raises(tiaa_disclosures._DisclosureTimeout):
        tiaa_disclosures.public_disclosures("TIAA009993", "长钱账户")
    time.sleep(0.25)
    result = tiaa_disclosures.public_disclosures("TIAA009993", "长钱账户")
    assert result["items"] == [
        {"url": "https://youzhiyouxing.cn/n/lite-posts/1", "published_at": "2026-01-01", "title": "recovered"}
    ]
    assert len(attempts) == 2


def test_partial_disclosure_is_returned_with_error_not_cached(monkeypatch):
    from tools import tiaa_disclosures

    monkeypatch.setattr(tiaa_disclosures, "_DISCLOSURE_DEADLINE", 0.05)
    monkeypatch.setattr(tiaa_disclosures, "_search_urls", lambda *_args: [])
    monkeypatch.setattr(
        tiaa_disclosures,
        "_sitemap_urls",
        lambda: ["https://youzhiyouxing.cn/n/lite-posts/1", "https://youzhiyouxing.cn/n/lite-posts/2"],
    )

    def article(url, _name):
        if url.endswith("/2"):
            time.sleep(0.2)
            return None
        return {"url": url, "published_at": "2026-01-01", "title": "partial"}

    monkeypatch.setattr(tiaa_disclosures, "_article", article)
    first = tiaa_disclosures.public_disclosures("TIAA009991", "长钱账户")
    assert first["items"] == [
        {"url": "https://youzhiyouxing.cn/n/lite-posts/1", "published_at": "2026-01-01", "title": "partial"}
    ]
    assert first["error"] == "disclosure_timeout"

    monkeypatch.setattr(
        tiaa_disclosures,
        "_article",
        lambda url, _name: {"url": url, "published_at": "2026-01-02", "title": "recovered"},
    )
    second = tiaa_disclosures.public_disclosures("TIAA009991", "长钱账户")
    assert second["items"][0]["title"] == "recovered"


def test_article_workers_do_not_block_process_exit():
    script = """
import time
from tools import tiaa_disclosures as disclosures

disclosures._DISCLOSURE_DEADLINE = 0.05
disclosures._sitemap_urls = lambda: ["https://youzhiyouxing.cn/n/lite-posts/1"]
disclosures._search_urls = lambda *_args: []
disclosures._article = lambda _url, _name: time.sleep(0.2)
started = time.monotonic()
try:
    disclosures.public_disclosures("TIAA009992", "长钱账户")
except disclosures._DisclosureTimeout:
    pass
assert time.monotonic() - started < 1
"""
    started = time.monotonic()
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=1)
    assert completed.returncode == 0, completed.stderr
    assert time.monotonic() - started < 1


def test_tiaa_search_budget_timeout_is_not_swallowed(monkeypatch):
    from tools import tiaa_disclosures

    monkeypatch.setattr(
        "search_intel.search_news",
        lambda *a, **k: {"results": [], "engines_errors": {"tavily": "time budget exhausted"}},
    )
    with pytest.raises(tiaa_disclosures._DisclosureTimeout):
        tiaa_disclosures._search_urls("长钱账户")


def test_tiaa_advisor_page_without_rows_is_error_not_cached(monkeypatch):
    from unittest.mock import Mock

    from tools import tiaa_disclosures

    monkeypatch.setattr(tiaa_disclosures.requests, "get", lambda *a, **k: Mock(content=b"<html>changed</html>"))
    with pytest.raises(ValueError):
        tiaa_disclosures.advisor_metadata("TIAA001001")
    body = "<tr><td>1</td><td>嘉实财富管理有限公司</td><td>A001</td></tr>".encode()
    monkeypatch.setattr(tiaa_disclosures.requests, "get", lambda *a, **k: Mock(content=body))
    assert tiaa_disclosures.advisor_metadata("TIAA001001")["advisor_org"] == "嘉实财富管理有限公司"
