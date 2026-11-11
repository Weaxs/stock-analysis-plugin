"""Public, login-free TIAA disclosures from the official YouZhiYouXing site."""

import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cache import cached  # noqa: E402

_SITEMAP = "https://youzhiyouxing.cn/sitemap.xml"
_ADVISOR_URL = "https://www.csisc.cn/zbscbzw/c100264/list_jjbm.shtml"
_ARTICLE_URL = re.compile(r"https://youzhiyouxing\.cn/(?:n/)?lite-posts/(\d+)(?:[/?#].*)?")
_DISCLOSURE_TERMS = ("示例账户", "成分基金", "配置", "业绩", "收益", "调仓", "持仓")
_MAX_ARTICLES = 40
_DISCLOSURE_DEADLINE = 30


class _DisclosureTimeout(Exception):
    """A bounded disclosure attempt failed and must not be cached as an empty result."""


def _until(deadline, fn, *args):
    """Run a blocking provider call in a daemon thread and stop waiting at the deadline.

    A provider that ignores its own timeout can then neither stretch the request nor keep
    the CLI process alive after it has answered.
    """
    box = {}

    def run():
        try:
            box["value"] = fn(*args)
        except BaseException as exc:  # re-raised in the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()))
    if worker.is_alive():
        raise _DisclosureTimeout
    if "error" in box:
        raise box["error"]
    return box.get("value")


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1
        if tag == "meta":
            values = dict(attrs)
            if values.get("property") in ("og:title", "og:description"):
                self.meta[values["property"]] = values.get("content", "")
        if tag in ("p", "div", "h1", "h2", "h3", "li", "br"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.ignored:
            self.ignored -= 1
        if tag in ("p", "div", "h1", "h2", "h3", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)

    def text(self):
        lines = []
        for line in "".join(self.parts).splitlines():
            clean = re.sub(r"\s+", " ", line).strip()
            if clean:
                lines.append(clean)
        title = self.meta.get("og:title")
        title_lines = [index for index, line in enumerate(lines) if line == title]
        if title_lines:
            lines = lines[title_lines[-1] + 1 :]
        comments = next((index for index, line in enumerate(lines) if line.endswith("条回应")), len(lines))
        return "\n".join(lines[:comments])


def _sitemap_urls():
    response = requests.get(_SITEMAP, headers={"User-Agent": "Mozilla/5.0"}, timeout=(2, 8))
    response.raise_for_status()
    root = ET.fromstring(response.content)
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    entries = []
    for node in root.findall("s:url", namespace):
        location = node.findtext("s:loc", default="", namespaces=namespace)
        match = _ARTICLE_URL.fullmatch(location)
        if match:
            entries.append((node.findtext("s:lastmod", default="", namespaces=namespace), match.group(1)))
    return [
        f"https://youzhiyouxing.cn/n/lite-posts/{article_id}" for _, article_id in sorted(entries, reverse=True)[:40]
    ]


def _search_urls(portfolio_name, budget_seconds=45):
    try:
        from search_intel import search_news

        query = f"site:youzhiyouxing.cn/n/lite-posts/ {portfolio_name} 示例账户 成分基金"
        result = search_news(query, max_results=10, budget_seconds=budget_seconds)
        if any("time budget exhausted" in str(error).lower() for error in result.get("engines_errors", {}).values()):
            raise _DisclosureTimeout
        urls = []
        for row in result.get("results", []):
            match = _ARTICLE_URL.fullmatch(row.get("url", ""))
            if match:
                urls.append(f"https://youzhiyouxing.cn/n/lite-posts/{match.group(1)}")
        return urls
    except Exception:
        return []


def _article(url, portfolio_name):
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=(1, 3))
        response.raise_for_status()
        parser = _ArticleParser()
        parser.feed(response.text)
        text = parser.text()
        if portfolio_name not in text or not any(term in text for term in _DISCLOSURE_TERMS):
            return None
        date_match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", response.text)
        published_at = None
        if date_match:
            published_at = "-".join([date_match.group(1), *(part.zfill(2) for part in date_match.groups()[1:])])
        return {
            "title": parser.meta.get("og:title", ""),
            "url": url,
            "published_at": published_at,
            "summary": parser.meta.get("og:description", ""),
            "text": text[:3000],
            "source": "youzhiyouxing",
            "partial": True,
        }
    except Exception:
        return None


def advisor_metadata(symbol):
    advisor_code = f"A{symbol[4:7]}"

    def fetch():
        response = requests.get(_ADVISOR_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        response.raise_for_status()
        text = response.content.decode("utf-8-sig")
        rows = re.findall(
            r"<tr\b[^>]*>\s*<td\b[^>]*>\s*\d+\s*</td>\s*"
            r"<td\b[^>]*>\s*(.*?)\s*</td>\s*<td\b[^>]*>\s*(A\d{3})\s*</td>",
            text,
            re.S,
        )
        return {
            "organizations": {code: unescape(re.sub(r"<[^>]+>", "", name)).strip() for name, code in rows},
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }

    data = cached("csisc-fund-advisor-organizations", 24 * 60 * 60, fetch)
    return {
        "advisor_code": advisor_code,
        "advisor_org": data["organizations"].get(advisor_code),
        "advisor_mapping_basis": "tiaa_prefix_best_effort",
        "advisor_source_url": _ADVISOR_URL,
        "advisor_retrieved_at": data["retrieved_at"],
    }


def public_disclosures(symbol, portfolio_name):
    def fetch():
        deadline = time.monotonic() + _DISCLOSURE_DEADLINE
        urls = _sitemap_urls()
        search_results = []

        timeout_state = {"search": False, "articles": False}
        if time.monotonic() < deadline:
            try:
                search_results = _until(
                    deadline, _search_urls, portfolio_name, max(1, int(deadline - time.monotonic()))
                )
            # concurrent.futures.TimeoutError only aliases the builtin from Python 3.11.
            except (TimeoutError, FutureTimeoutError, _DisclosureTimeout):
                timeout_state["search"] = True
            if time.monotonic() >= deadline:
                timeout_state["search"] = True
        else:
            timeout_state["search"] = True
        urls = list(dict.fromkeys([*search_results, *urls]))[:_MAX_ARTICLES]
        items = []
        for url in urls:
            if time.monotonic() >= deadline:
                timeout_state["articles"] = True
                break
            try:
                item = _until(deadline, _article, url, portfolio_name)
                if item:
                    items.append(item)
            except _DisclosureTimeout:
                timeout_state["articles"] = True
                break
            except (TimeoutError, FutureTimeoutError):
                timeout_state["articles"] = True
        if time.monotonic() >= deadline and urls:
            timeout_state["articles"] = True
        items.sort(key=lambda item: item["published_at"] or "", reverse=True)
        items = items[:3]
        result = {
            "items": items,
            "as_of": max((item["published_at"] for item in items if item["published_at"]), default=None),
        }
        if any(timeout_state.values()):
            if not items:
                raise _DisclosureTimeout
            result["error"] = "disclosure_timeout"
        return result

    return cached(f"tiaa-public-disclosures:{symbol}", 6 * 60 * 60, fetch)


def search_public(symbol, portfolio_name):
    def fetch():
        try:
            from search_intel import search_news

            label = portfolio_name or symbol
            result = search_news(f'"{symbol}" "{label}" 示例账户 成分基金 业绩', max_results=3)
            rows = []
            for row in result.get("results", []):
                parsed = urlparse(row.get("url", ""))
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    continue
                rows.append(
                    {
                        "title": row.get("title", ""),
                        "url": row["url"],
                        "snippet": row.get("snippet", ""),
                        "source": row.get("source", ""),
                        "publisher": parsed.netloc,
                        "verification": "unverified_search_result",
                        "partial": True,
                    }
                )
            return rows
        except Exception:
            return []

    return cached(f"tiaa-public-search:{symbol}", 6 * 60 * 60, fetch)
