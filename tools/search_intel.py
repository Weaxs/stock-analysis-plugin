#!/usr/bin/env python3
"""Search intelligence & social sentiment — multi-engine web search + Reddit/X/A-share sentiment aggregation."""

import argparse
import json
import os
import re
import sys
import time as _time

# --------------- TTL Cache ---------------

_CACHE = {}
_CACHE_TTL = 600  # 10 minutes


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _CACHE[key] = {"data": data, "ts": _time.time()}


# --------------- Web Search ---------------


def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []
    try:
        import requests

        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=15,
        )
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", "")[:200],
                "source": "tavily",
            }
            for r in data.get("results", [])
        ]
    except Exception:
        return []


def _brave_search(query: str, count: int = 5) -> list[dict]:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return []
    try:
        import requests

        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            params={"q": query, "count": count},
            timeout=15,
        )
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", "")[:200],
                "source": "brave",
            }
            for r in data.get("web", {}).get("results", [])
        ]
    except Exception:
        return []


def _serpapi_search(query: str, num: int = 5) -> list[dict]:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return []
    try:
        import requests

        resp = requests.get(
            "https://serpapi.com/search",
            params={
                "api_key": api_key,
                "q": query,
                "num": num,
                "engine": "google",
            },
            timeout=15,
        )
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": r.get("snippet", "")[:200],
                "source": "serpapi",
            }
            for r in data.get("organic_results", [])
        ]
    except Exception:
        return []


def _bocha_search(query: str, max_results: int = 5) -> list[dict]:
    api_key = os.environ.get("BOCHA_API_KEY")
    if not api_key:
        return []
    try:
        import requests

        resp = requests.post(
            "https://api.bochaai.com/v1/web-search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"query": query, "count": max_results},
            timeout=15,
        )
        data = resp.json()
        results = []
        for item in data.get("web", {}).get("results", []):
            results.append(
                {
                    "title": item.get("name", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("snippet", "")[:200],
                    "source": "bocha",
                }
            )
        return results
    except Exception:
        return []


def _searxng_search(query: str, max_results: int = 5) -> list[dict]:
    """SearXNG self-hosted metasearch — no API key, quota-free fallback.

    Set SEARXNG_BASE_URLS to a comma-separated list of instance URLs,
    e.g. "http://127.0.0.1:8080,https://searx.example.com".
    """
    base_urls = [u.strip().rstrip("/") for u in os.environ.get("SEARXNG_BASE_URLS", "").split(",") if u.strip()]
    if not base_urls:
        return []
    try:
        import requests
    except ImportError:
        return []
    for base in base_urls:
        try:
            resp = requests.get(
                f"{base}/search",
                params={"q": query, "format": "json"},
                headers={"Accept": "application/json"},
                timeout=15,
            )
            if not resp.ok:
                continue
            data = resp.json()
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (r.get("content") or "")[:200],
                    "source": "searxng",
                }
                for r in data.get("results", [])
            ]
            if results:
                return results
        except Exception:
            continue
    return []


def search_news(query: str, max_results: int = 10) -> dict:
    results = []
    engines_tried = []
    engines_available = []

    for name, fn in [
        ("tavily", _tavily_search),
        ("brave", _brave_search),
        ("bocha", _bocha_search),
        ("serpapi", _serpapi_search),
        ("searxng", _searxng_search),
    ]:
        engines_tried.append(name)
        items = fn(query, max_results)
        if items:
            engines_available.append(name)
            results.extend(items)
            if len(results) >= max_results:
                break

    if not results:
        return {
            "query": query,
            "results": [],
            "engines_tried": engines_tried,
            "note": (
                "No search engines configured. Set TAVILY_API_KEY, BRAVE_API_KEY, BOCHA_API_KEY, "
                "SERPAPI_KEY, or SEARXNG_BASE_URLS in environment."
            ),
        }

    seen_urls = set()
    deduped = []
    for r in results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            deduped.append(r)

    return {
        "query": query,
        "results": deduped[:max_results],
        "engines_used": engines_available,
        "total": len(deduped),
    }


def search_comprehensive(symbol: str, name: str = None) -> dict:
    label = name or symbol
    dimensions = {
        "latest_news": f"{label} 最新消息 新闻",
        "announcements": f"{label} 公告 公司公告",
        "market_analysis": f"{label} 行情分析 走势分析",
        "risk_check": f"{label} 风险 预警 违规",
        "earnings": f"{label} 业绩 财报 营收",
        "industry": f"{label} 行业 赛道 趋势",
    }

    results = {}
    for dim, query in dimensions.items():
        data = search_news(query, max_results=3)
        results[dim] = data.get("results", [])

    return {"symbol": symbol, "name": label, "dimensions": results}


# --------------- A-share Sentiment Sources ---------------


def _eastmoney_guba_heat(symbol: str) -> dict:
    """东方财富股吧热度 (public API, no auth)."""
    try:
        import requests

        resp = requests.get(
            "https://guba.eastmoney.com/interface/GetData.aspx",
            params={"path": "newtopic/api", "type": "gethot", "code": symbol},
            headers={"Referer": "https://guba.eastmoney.com/"},
            timeout=10,
        )
        if not resp.ok:
            return {}
        data = resp.json()
        re_data = data.get("re") or data.get("data") or {}
        if not re_data:
            return {}
        return {
            "source": "eastmoney_guba",
            "symbol": symbol,
            "hot_rank": re_data.get("hot_rank"),
            "post_count_today": re_data.get("post_count"),
            "view_count_today": re_data.get("view_count"),
            "sentiment_ratio": re_data.get("sentiment"),
        }
    except Exception:
        return {}


def _xueqiu_heat(symbol: str) -> dict:
    """雪球讨论热度 (public API)."""
    try:
        import requests

        xq_symbol = f"SH{symbol}" if symbol.startswith(("6", "9", "5")) else f"SZ{symbol}"

        resp = requests.get(
            "https://stock.xueqiu.com/v5/stock/hot_stock/info.json",
            params={"symbol": xq_symbol},
            headers={"User-Agent": "Mozilla/5.0", "Cookie": "xq_a_token=placeholder"},
            timeout=10,
        )
        if not resp.ok:
            return {}
        data = resp.json().get("data") or {}
        if not data:
            return {}
        return {
            "source": "xueqiu",
            "symbol": symbol,
            "followers": data.get("followers"),
            "discussion_count": data.get("discussion_count"),
            "hot_value": data.get("value"),
            "rank": data.get("rank"),
        }
    except Exception:
        return {}


# --------------- Social Sentiment (market-aware) ---------------


def get_social_sentiment(symbol: str) -> dict:
    """Market-aware social sentiment. A-shares: eastmoney + xueqiu. US/HK: Reddit/X/Polymarket."""
    result = {"symbol": symbol, "sources": {}}

    if re.match(r"^\d{6}$", symbol):
        market = "A"
    elif symbol.upper().endswith(".HK"):
        market = "HK"
    else:
        market = "US"

    if market == "A":
        guba = _eastmoney_guba_heat(symbol)
        if guba:
            result["sources"]["eastmoney_guba"] = guba

        xueqiu = _xueqiu_heat(symbol)
        if xueqiu:
            result["sources"]["xueqiu"] = xueqiu
    else:
        api_url = os.environ.get("SENTIMENT_API_URL", "https://api.adanos.org")
        api_key = os.environ.get("SENTIMENT_API_KEY")

        try:
            import requests

            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

            for source in ["reddit", "twitter", "polymarket"]:
                try:
                    resp = requests.get(f"{api_url}/sentiment/{source}/{symbol}", headers=headers, timeout=10)
                    if resp.ok:
                        result["sources"][source] = resp.json()
                except Exception:
                    pass
        except ImportError:
            result["error"] = "requests library not available"

    if not result["sources"]:
        if market == "A":
            result["note"] = "No A-share sentiment data available. 东方财富/雪球 APIs may be temporarily unavailable."
        else:
            result["note"] = (
                "No sentiment data available. Set SENTIMENT_API_URL and SENTIMENT_API_KEY, "
                "or ensure the sentiment API is accessible."
            )

    result["additional_context"] = {
        "wisburg_mcp": "Use 'list-feed' and 'list-market-daily' MCP tools for institutional research context.",
    }

    return result


# --------------- Trending Aggregation ---------------


def get_trending_sentiment() -> dict:
    """Fetch trending sentiment from Reddit/X/Polymarket. Results cached for 10 min."""
    cache_key = "trending_sentiment"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    api_url = os.environ.get("SENTIMENT_API_URL", "https://api.adanos.org")
    api_key = os.environ.get("SENTIMENT_API_KEY")

    result = {"trending": {}, "fetched_at": None}

    try:
        import requests

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        for source in ["reddit", "twitter", "polymarket"]:
            try:
                resp = requests.get(f"{api_url}/trending/{source}", headers=headers, timeout=10)
                if resp.ok:
                    result["trending"][source] = resp.json()
            except Exception:
                pass
    except ImportError:
        result["error"] = "requests library not available"

    if not result["trending"]:
        result["note"] = "No trending data available. Set SENTIMENT_API_URL and SENTIMENT_API_KEY."

    result["fetched_at"] = _time.strftime("%Y-%m-%d %H:%M:%S")
    result["additional_context"] = {
        "wisburg_mcp": "Use 'list-feed' for latest research feed and 'list-market-daily' for market daily digest.",
    }

    _cache_set(cache_key, result)
    return result


# --------------- Article Extraction ---------------


def extract_article(url: str) -> dict:
    try:
        from newspaper import Article

        a = Article(url, language="zh")
        a.download()
        a.parse()
        return {
            "url": url,
            "title": a.title or "",
            "text": (a.text or "")[:3000],
            "authors": a.authors or [],
            "publish_date": str(a.publish_date) if a.publish_date else None,
            "top_image": a.top_image or None,
        }
    except ImportError:
        return {"url": url, "error": "newspaper4k not installed. pip install newspaper4k"}
    except Exception as e:
        return {"url": url, "error": str(e)}


# --------------- CLI Commands ---------------


def cmd_search(args):
    return search_news(args.query, args.count)


def cmd_comprehensive(args):
    return search_comprehensive(args.symbol, args.name)


def cmd_sentiment(args):
    return get_social_sentiment(args.symbol)


def cmd_trending(args):
    return get_trending_sentiment()


def cmd_extract(args):
    return extract_article(args.url)


def main():
    parser = argparse.ArgumentParser(description="Search intelligence & social sentiment")
    sub = parser.add_subparsers(dest="command")

    p_search = sub.add_parser("search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--count", type=int, default=10)

    p_comp = sub.add_parser("comprehensive")
    p_comp.add_argument("symbol", help="Stock symbol")
    p_comp.add_argument("--name", default=None, help="Stock name for better search")

    p_sent = sub.add_parser("sentiment")
    p_sent.add_argument("symbol", help="Stock ticker (e.g. AAPL, 600519, 00700.HK)")

    sub.add_parser("trending")

    p_ext = sub.add_parser("extract")
    p_ext.add_argument("url", help="Article URL to extract")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "search": cmd_search,
        "comprehensive": cmd_comprehensive,
        "sentiment": cmd_sentiment,
        "trending": cmd_trending,
        "extract": cmd_extract,
    }
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
