#!/usr/bin/env python3
"""Search intelligence & social sentiment — multi-engine web search + Reddit/X sentiment aggregation."""

import argparse
import json
import os
import sys
from datetime import datetime


def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []
    try:
        import requests
        resp = requests.post("https://api.tavily.com/search", json={
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }, timeout=15)
        data = resp.json()
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": r.get("content", "")[:200], "source": "tavily"}
                for r in data.get("results", [])]
    except Exception:
        return []


def _brave_search(query: str, count: int = 5) -> list[dict]:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return []
    try:
        import requests
        resp = requests.get("https://api.search.brave.com/res/v1/web/search",
                            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                            params={"q": query, "count": count}, timeout=15)
        data = resp.json()
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": r.get("description", "")[:200], "source": "brave"}
                for r in data.get("web", {}).get("results", [])]
    except Exception:
        return []


def _serpapi_search(query: str, num: int = 5) -> list[dict]:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return []
    try:
        import requests
        resp = requests.get("https://serpapi.com/search", params={
            "api_key": api_key, "q": query, "num": num, "engine": "google",
        }, timeout=15)
        data = resp.json()
        return [{"title": r.get("title", ""), "url": r.get("link", ""),
                 "snippet": r.get("snippet", "")[:200], "source": "serpapi"}
                for r in data.get("organic_results", [])]
    except Exception:
        return []


def _bocha_search(query: str, max_results: int = 5) -> list[dict]:
    api_key = os.environ.get("BOCHA_API_KEY")
    if not api_key:
        return []
    try:
        import requests
        resp = requests.post("https://api.bochaai.com/v1/web-search",
                             headers={"Authorization": f"Bearer {api_key}",
                                      "Content-Type": "application/json"},
                             json={"query": query, "count": max_results},
                             timeout=15)
        data = resp.json()
        results = []
        for item in data.get("web", {}).get("results", []):
            results.append({"title": item.get("name", ""), "url": item.get("url", ""),
                            "snippet": item.get("snippet", "")[:200], "source": "bocha"})
        return results
    except Exception:
        return []


def search_news(query: str, max_results: int = 10) -> dict:
    results = []
    engines_tried = []
    engines_available = []

    for name, fn in [("tavily", _tavily_search), ("brave", _brave_search),
                      ("bocha", _bocha_search), ("serpapi", _serpapi_search)]:
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
            "note": "No search engines configured. Set TAVILY_API_KEY, BRAVE_API_KEY, or SERPAPI_KEY in environment.",
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


def get_social_sentiment(symbol: str) -> dict:
    api_url = os.environ.get("SENTIMENT_API_URL", "https://api.adanos.org")
    api_key = os.environ.get("SENTIMENT_API_KEY")

    result = {"symbol": symbol, "sources": {}}

    try:
        import requests
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        try:
            resp = requests.get(f"{api_url}/sentiment/reddit/{symbol}",
                                headers=headers, timeout=10)
            if resp.ok:
                result["sources"]["reddit"] = resp.json()
        except Exception:
            pass

        try:
            resp = requests.get(f"{api_url}/sentiment/twitter/{symbol}",
                                headers=headers, timeout=10)
            if resp.ok:
                result["sources"]["twitter"] = resp.json()
        except Exception:
            pass

        try:
            resp = requests.get(f"{api_url}/sentiment/polymarket/{symbol}",
                                headers=headers, timeout=10)
            if resp.ok:
                result["sources"]["polymarket"] = resp.json()
        except Exception:
            pass

    except ImportError:
        result["error"] = "requests library not available"

    if not result["sources"]:
        result["note"] = "No sentiment data available. Set SENTIMENT_API_URL and SENTIMENT_API_KEY, or ensure the sentiment API is accessible."

    return result


def cmd_search(args):
    return search_news(args.query, args.count)


def cmd_comprehensive(args):
    return search_comprehensive(args.symbol, args.name)


def cmd_sentiment(args):
    return get_social_sentiment(args.symbol)


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
    p_sent.add_argument("symbol", help="Stock ticker (e.g. AAPL)")

    p_ext = sub.add_parser("extract")
    p_ext.add_argument("url", help="Article URL to extract")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {"search": cmd_search, "comprehensive": cmd_comprehensive,
                "sentiment": cmd_sentiment, "extract": cmd_extract}
    result = dispatch[args.command](args)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
