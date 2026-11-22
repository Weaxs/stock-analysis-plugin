#!/usr/bin/env python3
"""Stock market data fetcher for A-shares (akshare), HK and US (yfinance)."""

import argparse
import contextlib
import json
import math
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cache import CACHE_DIR, DAY, WEEK, atomic_write, cached, cached_values  # noqa: E402
from _subproc import error_message, run_tool, scrub_secrets, socket_timeout, utf8_stdio  # noqa: E402
from trading_calendar import market_now  # noqa: E402

# Explicitly exchange-prefixed A-share codes (sh600519 / sz000858 / bj920001).
# Bare 6-digit codes are always stocks; the prefix is the disambiguation escape
# hatch for index codes (sh000001 = 上证指数, not 平安银行).
_A_PREFIXED_RE = re.compile(r"(?:sh|sz|bj)\d{6}", re.IGNORECASE)
_A_INDEX_RE = re.compile(r"(?:sh000\d{3}|sz399\d{3})", re.IGNORECASE)
# Yahoo has no Hang Seng Tech index; Tencent serves its live quote and daily history.
_TENCENT_HK_INDEX = {"^HSTECH": "hkHSTECH"}


def detect_market(symbol: str) -> str:
    s = symbol.upper()
    if _A_PREFIXED_RE.fullmatch(symbol):
        return "A"
    if s.endswith(".HK"):
        return "HK"
    if s.endswith(".T"):
        return "JP"
    if s.endswith((".KS", ".KQ")):
        return "KR"
    if s.endswith((".TW", ".TWO")):
        return "TW"
    if re.match(r"^\d{6}$", symbol):
        return "A"
    return "US"


def normalize_stock_code(symbol: str) -> dict:
    """Classify A-share stock code into board/type with limit-up/down ratio."""
    info = {"market": detect_market(symbol), "board": "main", "is_etf": False, "limit_pct": 0.10}
    if info["market"] != "A":
        info["limit_pct"] = None
        return info

    prefix2 = symbol[:2]
    prefix3 = symbol[:3]

    if prefix3 == "688":
        info["board"] = "STAR"
        info["limit_pct"] = 0.20
    elif prefix2 == "30":
        info["board"] = "ChiNext"
        info["limit_pct"] = 0.20
    elif prefix2 in ("92", "43", "81", "82", "83", "87", "88"):
        info["board"] = "BSE"
        info["limit_pct"] = 0.30
    elif prefix2 in ("51", "52", "56", "58", "15", "16", "18"):
        info["is_etf"] = True
        info["board"] = "ETF"
        info["limit_pct"] = None

    return info


def calc_limit_price(pre_close: float, ratio: float, direction: str = "up") -> float:
    """Calculate limit-up or limit-down price with decimal round-half-up."""
    from decimal import ROUND_HALF_UP, Decimal

    sign = 1 if direction == "up" else -1
    price = Decimal(str(pre_close)) * (1 + sign * Decimal(str(ratio)))
    return float(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# Chains that get sticky ordering: the CN free-source chains, where failures are
# typically chronic (regional blocking / rate-limiting — eastmoney is the primary
# leg of most of them). The yf chains (`kline:`/`quote:`) are
# excluded on purpose — their failures are usually transient yfinance blips, and
# stickiness would let one blip degrade HK/US quote richness (finnhub has no
# name/market_cap/pe/pb) for the whole TTL.
_STICKY_CHAINS = {
    "kline_a",
    "quote_a",
    "snapshot_a",
    "sector_constituents",
    "sina",
    "sector_rankings",
    "dragon_tiger",
    "hot_stocks",
}


class _QuoteMissing(ValueError):
    """The provider answered but has no quote for this code."""


class _ExchangeQuoteMissing(RuntimeError):
    """All attempted providers explicitly reported no quote."""


def _failover(sources: list, label: str, evidence: bool = False):
    """Return first truthy result or aggregate errors, preserving unanimous quote misses.

    Sticky ordering (issue #25): for chains in _STICKY_CHAINS (keyed by the label
    before ":"), the last winning source is tried first next time; the rest keep
    their declared order. Persisted via the sector disk cache below (tempdir, 24h
    TTL), so it survives across the one-process-per-CLI-call host model; a stale
    entry costs one failed attempt, then the new winner replaces it (self-healing).
    """
    chain = label.split(":", 1)[0]
    sticky = None
    if chain in _STICKY_CHAINS:
        sticky = _disk_cache_get(f"sticky-{chain}")
        if sticky:
            # Single-symbol A quotes always try Tencent first; sticky only reorders the fallbacks behind it.
            first = "tencent" if chain == "quote_a" else sticky
            sources = sorted(sources, key=lambda s: (s[0] != first, s[0] != sticky))
    errors = []
    misses = 0
    for name, fn in sources:
        try:
            result = fn()
            if result:
                if chain in _STICKY_CHAINS and name != sticky:
                    _disk_cache_set(f"sticky-{chain}", name)
                if evidence:
                    fetched_at = datetime.now(timezone.utc).isoformat()
                    rows = result if isinstance(result, list) else [result]
                    for row in rows:
                        if not isinstance(row, dict) or "error" in row:
                            continue
                        row.update(source=name, fetched_at=fetched_at)
                        row.setdefault("data_as_of", row.get("date"))
                        if chain.startswith("kline"):
                            row.setdefault(
                                "adjusted",
                                {"yfinance": "total_return", "alphavantage": "total_return", "longbridge": "qfq"}.get(
                                    name, "unknown"
                                ),
                            )
                return result
        except Exception as e:
            misses += isinstance(e, _QuoteMissing)
            errors.append(f"{name}: {type(e).__name__}: {scrub_secrets(str(e))}")
    if errors:
        error_type = _ExchangeQuoteMissing if misses == len(sources) else RuntimeError
        raise error_type(f"{label}: {'; '.join(errors)}")
    return None


def _akshare_retry(fn, *args, retries=2, delay=1, **kwargs):
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            from requests.exceptions import ChunkedEncodingError, HTTPError, Timeout
            from requests.exceptions import ConnectionError as RequestsConnectionError
            from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError

            transport_errors = (
                ConnectionError,
                TimeoutError,
                RequestsConnectionError,
                Timeout,
                ChunkedEncodingError,
                RequestsJSONDecodeError,
                json.JSONDecodeError,
            )
            http_errors = (HTTPError,)
            if type(exc).__module__.startswith("curl_cffi."):
                from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
                from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
                from curl_cffi.requests.exceptions import Timeout as CurlTimeout

                transport_errors += (CurlConnectionError, CurlTimeout)
                http_errors += (CurlHTTPError,)
            transient = isinstance(exc, transport_errors)
            if isinstance(exc, http_errors):
                status = getattr(exc.response, "status_code", None)
                transient = status in (408, 429) or isinstance(status, int) and 500 <= status < 600
            if type(exc).__module__.startswith("yfinance."):
                from yfinance import exceptions as yf_errors

                transient = isinstance(exc, getattr(yf_errors, "YFRateLimitError", ()))
            if not transient or attempt == retries:
                raise
            time.sleep(delay)


def _to_baostock_code(symbol: str) -> str:
    code = _cn_code(symbol)
    return f"{code[:2]}.{code[2:]}"


def _kline_efinance(symbol: str, period: str, count: int) -> list:
    import efinance as ef

    freq_map = {"daily": 101, "weekly": 102, "monthly": 103}
    df = ef.stock.get_quote_history(symbol, klt=freq_map.get(period, 101), fqt=1)
    if df is None or df.empty:
        raise ValueError("efinance returned empty data")
    col_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "turnover",
        "振幅": "amplitude",
        "涨跌幅": "change_pct",
        "换手率": "turnover_rate",
    }
    df = df.rename(columns=col_map)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = __import__("pandas").to_numeric(df[c], errors="coerce")
    keep = [
        c
        for c in ["date", "open", "high", "low", "close", "volume", "turnover", "change_pct", "turnover_rate"]
        if c in df.columns
    ]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


_BAOSTOCK_LOCK = threading.Lock()


def _kline_baostock(symbol: str, period: str, count: int) -> list:
    import io

    import baostock as bs
    import pandas as pd

    freq_map = {"daily": "d", "weekly": "w", "monthly": "m"}
    end = _cn_now()
    start = end - timedelta(days=count * 7 if period == "weekly" else count * 31 if period == "monthly" else count * 2)
    # baostock login/logout print status lines to stdout — silence them so the
    # tool's JSON output on stdout stays clean for the calling host.
    # One global baostock session per process: `klines` threads must not interleave login/query/logout.
    with _BAOSTOCK_LOCK:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.login()
        try:
            rs = bs.query_history_k_data_plus(
                _to_baostock_code(symbol),
                "date,open,high,low,close,volume,amount,pctChg",
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                frequency=freq_map.get(period, "d"),
                adjustflag="2",
            )
            rows = []
            while (rs.error_code == "0") and rs.next():
                rows.append(rs.get_row_data())
            df = pd.DataFrame(rows, columns=rs.fields)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                bs.logout()
    if df.empty:
        raise ValueError("baostock returned empty data")
    col_map = {"amount": "turnover", "pctChg": "change_pct"}
    df = df.rename(columns=col_map)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] /= 100  # Baostock uses shares; the A-share CLI contract uses lots.
    keep = [c for c in ["date", "open", "high", "low", "close", "volume", "turnover", "change_pct"] if c in df.columns]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


# --------------- tencent / sina (non-eastmoney A-share fallbacks, issue #25) ---------------
#
# akshare/efinance both resolve to eastmoney hosts, so an eastmoney-blocked network
# kills the whole A-share chain. Tencent (qt.gtimg.cn) and Sina (hq.sinajs.cn) are
# independent quote endpoints needing no credentials; both cover stocks, ETFs,
# indices and BSE codes.


def _cn_code(symbol: str) -> str:
    """Exchange-prefixed code shared by Tencent and Sina: 600519 → sh600519, BSE → bj920001."""
    if _A_PREFIXED_RE.fullmatch(symbol):
        return symbol.lower()
    if symbol.startswith(("43", "81", "82", "83", "87", "88", "92")):
        return f"bj{symbol}"
    return f"sh{symbol}" if symbol.startswith(("5", "6", "9")) else f"sz{symbol}"


def _parse_float(value):
    """Provider string field → float; empty/garbage → None (never raises)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gbk_var_payload(content: bytes) -> str:
    """Extract the quoted payload from a GBK `v_sh600519="..."` / `var hq_str_...="..."` body."""
    return content.decode("gbk", errors="replace").split('="', 1)[-1].rsplit('"', 1)[0]


def _unix_quote_time(value) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        with contextlib.suppress(ValueError, OverflowError, OSError):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return None


def _quote_time(value: str, fmt: str, tz=timezone(timedelta(hours=8))) -> str | None:
    try:
        return datetime.strptime(value, fmt).replace(tzinfo=tz).isoformat()
    except ValueError:
        return None


def _tencent_payload_row(f: list, symbol: str) -> dict:
    """Build the quote row from one Tencent `~`-separated payload (already split).
    Used by _quote_tencent and the cn-indices batch fallback."""
    f += [""] * (50 - len(f))  # pad optional tail fields (pe/pb/market_cap/volume_ratio…) on short payloads
    market = detect_market(symbol)
    foreign = market in ("HK", "US")  # HK and US share currency-unit turnover and lack the A-share ratio fields
    turnover = _parse_float(f[37])
    market_cap = _parse_float(f[45])
    if market == "US":
        as_of = _quote_time(f[30], "%Y-%m-%d %H:%M:%S", ZoneInfo("America/New_York"))
    else:
        as_of = _quote_time(f[30], "%Y/%m/%d %H:%M:%S" if foreign else "%Y%m%d%H%M%S")
    return {
        "symbol": symbol,
        "name": f[1],
        "price": _parse_float(f[3]),
        "change": _parse_float(f[31]),
        "change_pct": _parse_float(f[32]),
        "volume": _parse_float(f[6]),  # A: 手; HK: shares, same as yfinance
        "turnover": turnover * (1 if foreign else 1e4) if turnover is not None else None,
        "high": _parse_float(f[33]),
        "low": _parse_float(f[34]),
        "open": _parse_float(f[5]),
        "prev_close": _parse_float(f[4]),
        "market_cap": market_cap * 1e8 if market_cap is not None else None,
        "pe": _parse_float(f[39]),
        "pe_basis": "unknown",
        # Tencent's HK map labels f[47] DY and f[49] WK52_Low, and US f[46] is the English name.
        "pb": None if foreign else _parse_float(f[46]),
        "turnover_rate": None if foreign else _parse_float(f[38]),
        "volume_ratio": None if foreign else _parse_float(f[49]),
        "amplitude": _parse_float(f[43]),
        "data_as_of": as_of,
    }


def _tencent_hk_code(symbol: str) -> str:
    if not re.fullmatch(r"[0-9]{1,5}\.HK", symbol, re.IGNORECASE):
        raise ValueError(f"invalid HK symbol: {symbol}")
    return f"hk{symbol[:-3].zfill(5)}"


def _tencent_us_code(symbol: str) -> str:
    """Tencent spells class shares with a dot (BRK.B) where Yahoo uses a dash (BRK-B)."""
    if not re.fullmatch(r"[A-Za-z]{1,6}(?:[.-][A-Za-z])?", symbol):
        raise ValueError(f"invalid US symbol: {symbol}")
    return "us" + symbol.upper().replace("-", ".")


def _tencent_raw(codes: str) -> bytes:
    import requests

    resp = requests.get(f"https://qt.gtimg.cn/q={codes}", timeout=10)
    resp.raise_for_status()
    return resp.content


def _tencent_fields(code: str) -> list:
    body = _tencent_raw(code)
    if _A_PREFIXED_RE.fullmatch(code) and body.strip() == b'v_pv_none_match="1";':
        raise _QuoteMissing(f"tencent returned no quote for {code}")
    return _gbk_var_payload(body).split("~")


def _tencent_batch_fields(codes: list[str]) -> dict[str, list[str]]:
    quotes = {}
    for part in _tencent_raw(",".join(codes)).decode("gbk", errors="replace").split(";"):
        name, _, payload = part.strip().partition('="')
        fields = payload.rstrip('"').split("~")
        if name.startswith("v_") and len(fields) > 37:
            quotes[name[2:]] = fields
    return quotes


def _hk_tencent_fields(symbols: list[str]) -> dict[str, list[str]]:
    from concurrent.futures import ThreadPoolExecutor

    by_code = {_tencent_hk_code(symbol): symbol for symbol in symbols}
    codes = list(by_code)
    batches = [codes[i : i + 60] for i in range(0, len(codes), 60)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        return {
            by_code[code]: fields
            for quotes in pool.map(_tencent_batch_fields, batches)
            for code, fields in quotes.items()
            if code in by_code and (_parse_float(fields[3]) or 0) > 0
        }


def hk_quote_dividend_yields(symbols: list[str]) -> dict[str, float | None]:
    return {
        symbol: _sanitize(_parse_float(fields[47]))
        for symbol, fields in _hk_tencent_fields(symbols).items()
        if len(fields) > 47
    }


def _quote_tencent(symbol: str) -> dict:
    """Tencent A/HK/US quote via qt.gtimg.cn, with a GBK-encoded `~` payload.

    A-share turnover is 万元; HK/US turnover is already HKD/USD. Market cap is 亿
    in the quote currency; normalize monetary fields to currency units."""
    market = detect_market(symbol)
    if market == "HK":
        # Tencent's hk feed is delayed 15 minutes; r_hk uses the same fields for live quotes.
        code = f"r_{_tencent_hk_code(symbol)}"
    elif market == "US":
        code = _tencent_us_code(symbol)
    else:
        code = _cn_code(symbol)
    f = _tencent_fields(code)
    if len(f) < 35 or not f[1]:
        raise ValueError(f"tencent returned no quote for {symbol}")
    row = _clean_row(_tencent_payload_row(f, symbol))
    if market != "A" and (row["price"] is None or row["price"] <= 0):
        raise ValueError(f"tencent returned no quote for {symbol}")
    if normalize_stock_code(symbol)["is_etf"]:
        # f[73] is total fund shares and f[78] the intraday IOPV (blank for money/bond ETFs);
        # premium matches _quote_eastmoney.
        iopv = _parse_float(f[78]) if len(f) > 78 else None
        price = row.get("price")
        row.update(
            is_etf=True,
            fund_shares=_parse_float(f[73]) if len(f) > 73 else None,
            iopv=iopv,
            premium_discount_rate=round((price - iopv) / iopv * 100, 4) if price is not None and iopv else None,
        )
    return row


def _kline_tencent(symbol: str, period: str, count: int) -> list:
    """Tencent kline via web.ifzq.gtimg.cn. Rows are [date, open, close, high, low,
    volume] ascending (A: 手, HK/US: shares); raw series are accepted only for explicit indices.
    HK/US stocks need the hk/us endpoints: the generic one ignores qfq for them."""
    import requests

    p_map = {"daily": "day", "weekly": "week", "monthly": "month"}
    p = p_map.get(period, "day")
    market = detect_market(symbol)
    hk_index = symbol.upper() in _TENCENT_HK_INDEX
    endpoint = "fqkline"
    if hk_index:
        code = _TENCENT_HK_INDEX[symbol.upper()]
    elif market == "HK":
        endpoint, code = "hkfqkline", _tencent_hk_code(symbol)
    elif market == "US":
        # usfqkline only adjusts the exchange-suffixed code (AAPL.OQ) that the quote API reports.
        f = _tencent_fields(_tencent_us_code(symbol))
        if len(f) < 3 or not f[2]:
            raise ValueError(f"tencent returned no quote for {symbol}")
        endpoint, code = "usfqkline", f"us{f[2]}"
    else:
        code = _cn_code(symbol)
    resp = requests.get(
        f"https://web.ifzq.gtimg.cn/appstock/app/{endpoint}/get",
        params={"param": f"{code},{p},,,{count},qfq"},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise ValueError(f"tencent kline failed: {data.get('msg', '')}")
    node = data.get("data", {}).get(code, {})
    raw = _A_INDEX_RE.fullmatch(symbol) or hk_index
    rows = node.get(p) if raw else node.get(f"qfq{p}")
    if not rows:
        raise ValueError("tencent kline returned empty data for required adjustment")
    extra = {} if market == "A" else {"adjusted": "none" if raw else "qfq"}
    return [
        _clean_row(
            {
                "date": r[0],
                "open": _parse_float(r[1]),
                "high": _parse_float(r[3]),
                "low": _parse_float(r[4]),
                "close": _parse_float(r[2]),
                "volume": _parse_float(r[5]),
                **extra,
            }
        )
        for r in rows[-count:]
    ]


def _quote_sina(symbol: str) -> dict:
    """Sina quote via hq.sinajs.cn. Requires a finance.sina.com.cn Referer or the
    endpoint answers 403. GBK-encoded CSV; volume arrives in 股 → normalized to 手."""
    import requests

    code = _cn_code(symbol)
    resp = requests.get(
        f"https://hq.sinajs.cn/list={code}",
        headers={"Referer": "https://finance.sina.com.cn"},
        timeout=10,
    )
    resp.raise_for_status()
    if resp.content.strip() == f'var hq_str_{code}="";'.encode():
        raise _QuoteMissing(f"sina returned no quote for {symbol}")
    f = _gbk_var_payload(resp.content).split(",")
    if len(f) < 10 or not f[0]:
        raise ValueError(f"sina returned no quote for {symbol}")
    price = _parse_float(f[3])
    prev_close = _parse_float(f[2])
    volume = _parse_float(f[8])
    row = {
        "symbol": symbol,
        "name": f[0],
        "price": price,
        "change": (price - prev_close) if price is not None and prev_close else None,
        "change_pct": ((price / prev_close) - 1) * 100 if price is not None and prev_close else None,
        "volume": volume / 100 if volume is not None else None,
        "turnover": _parse_float(f[9]),  # already 元
        "high": _parse_float(f[4]),
        "low": _parse_float(f[5]),
        "open": _parse_float(f[1]),
        "prev_close": prev_close,
        "data_as_of": _quote_time(f"{f[30]} {f[31]}", "%Y-%m-%d %H:%M:%S") if len(f) > 31 else None,
    }
    if normalize_stock_code(symbol)["is_etf"]:
        row["is_etf"] = True
    return _clean_row(row)


def _kline_sina(symbol: str, period: str, count: int) -> list:
    """Sina daily kline via quotes.sina.cn (JSONP-wrapped; scale=240 = daily is the
    only supported period). Unadjusted; volume arrives in 股 → normalized to 手."""
    if period != "daily":
        raise ValueError("sina kline supports daily period only")
    import requests

    resp = requests.get(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_k=/CN_MarketDataService.getKLineData",
        params={"symbol": _cn_code(symbol), "scale": "240", "ma": "no", "datalen": str(min(count, 1023))},
        headers={"Referer": "https://finance.sina.com.cn"},
        timeout=10,
    )
    text = resp.text
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("sina kline returned unparseable data")
    rows = json.loads(text[start : end + 1])
    if not rows:
        raise ValueError("sina kline returned empty data")
    result = []
    for r in rows[-count:]:
        volume = _parse_float(r.get("volume"))
        result.append(
            _clean_row(
                {
                    "date": r.get("day"),
                    "open": _parse_float(r.get("open")),
                    "high": _parse_float(r.get("high")),
                    "low": _parse_float(r.get("low")),
                    "close": _parse_float(r.get("close")),
                    "volume": volume / 100 if volume is not None else None,
                }
            )
        )
    return result


def _sanitize(obj):
    """Convert pandas types to JSON-serializable Python types."""
    import numpy as np
    import pandas as pd

    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return None if pd.isna(obj) else obj.strftime("%Y-%m-%d")
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return round(float(obj), 4) if np.isfinite(obj) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        return round(obj, 4) if np.isfinite(obj) else None
    return obj


def _cn_now() -> datetime:
    return market_now("CN")


def _clean_row(d: dict) -> dict:
    return {k: _sanitize(v) for k, v in d.items()}


# --------------- kline ---------------


def kline_a(symbol: str, period: str, count: int, evidence: bool = False) -> list:
    """A-share qfq kline: akshare → tushare → efinance → tencent → baostock.

    Explicit indices keep raw points; unadjusted stock sources never enter the chain."""
    if _A_INDEX_RE.fullmatch(symbol):
        sources = [
            ("tencent", lambda: _kline_tencent(symbol, period, count)),
            ("sina", lambda: _kline_sina(symbol, period, count)),
            ("baostock", lambda: _kline_baostock(symbol, period, count)),
        ]
    else:
        symbol = symbol[2:] if _A_PREFIXED_RE.fullmatch(symbol) else symbol
        sources = [
            ("akshare", lambda: _kline_akshare(symbol, period, count)),
            ("tushare", lambda: _kline_tushare(symbol, period, count)),
            ("efinance", lambda: _kline_efinance(symbol, period, count)),
            ("tencent", lambda: _kline_tencent(symbol, period, count)),
            ("baostock", lambda: _kline_baostock(symbol, period, count)),
        ]
    with socket_timeout(25):
        return _failover(sources, label=f"kline_a:{symbol}", evidence=evidence)


def _kline_akshare(symbol: str, period: str, count: int) -> list:
    import akshare as ak

    period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
    end = _cn_now()
    start = end - timedelta(days=count * 7 if period == "weekly" else count * 31 if period == "monthly" else count * 2)

    # ETFs (51/52/56/58/15/16/18xxxx) are not covered by stock_zh_a_hist.
    # akshare 1.20.44-1.20.46 renamed several APIs; py3.10 stays below 1.20.40 with the old names.
    hist_fn = (
        ak.fund_etf_hist_em
        if normalize_stock_code(symbol)["is_etf"]
        else getattr(ak, "stock_zh_a_hist_em", None) or ak.stock_zh_a_hist
    )
    df = _akshare_retry(
        hist_fn,
        symbol=symbol,
        period=period_map.get(period, "daily"),
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="qfq",
    )
    col_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "turnover",
        "振幅": "amplitude",
        "涨跌幅": "change_pct",
        "涨跌额": "change",
        "换手率": "turnover_rate",
    }
    df = df.rename(columns=col_map)
    keep = [
        c
        for c in ["date", "open", "high", "low", "close", "volume", "turnover", "change_pct", "turnover_rate"]
        if c in df.columns
    ]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


def _tushare(api_name: str, params: dict, fields: str | None = None, error: str = "tushare returned no data"):
    """POST one tushare HTTP API call; returns (fields, items) or raises with the API message."""
    import os

    import requests

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise ValueError("TUSHARE_TOKEN not set")
    body = {"api_name": api_name, "token": token, "params": params}
    if fields:
        body["fields"] = fields
    data = requests.post("http://api.tushare.pro", json=body, timeout=15).json()
    if data.get("code") != 0 or not data.get("data", {}).get("items"):
        raise ValueError(f"{error}: {data.get('msg', '')}")
    return data["data"]["fields"], data["data"]["items"]


def _kline_tushare(symbol: str, period: str, count: int) -> list:
    """Tushare qfq kline via daily factors or the adjusted week/month endpoint."""
    import os

    import pandas as pd

    if not os.environ.get("TUSHARE_TOKEN"):
        raise ValueError("TUSHARE_TOKEN not set")
    if normalize_stock_code(symbol)["is_etf"]:
        raise ValueError("tushare stock adjustment does not cover ETFs")
    end = _cn_now()
    start = end - timedelta(days=count * 7 if period == "weekly" else count * 31 if period == "monthly" else count * 2)

    ts_code = f"{symbol}.{_cn_code(symbol)[:2].upper()}"
    params = {
        "ts_code": ts_code,
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
    }
    prices = ["open", "high", "low", "close"]
    if period != "daily":
        params["freq"] = "week" if period == "weekly" else "month"
    fields = prices if period == "daily" else [f"{c}_qfq" for c in prices]

    api = "daily" if period == "daily" else "stk_week_month_adj"
    columns, items = _tushare(api, params, ",".join(["trade_date", *fields, "vol", "amount", "pct_chg"]))
    df = pd.DataFrame(items, columns=columns)
    if not set(fields).issubset(df.columns):
        raise ValueError("tushare returned incomplete adjusted prices")
    df[fields] = df[fields].apply(pd.to_numeric, errors="coerce")
    if period == "daily":
        columns, items = _tushare(
            "adj_factor", params, "trade_date,adj_factor", "tushare returned no adjustment factors"
        )
        factors = pd.DataFrame(items, columns=columns)
        factors["adj_factor"] = pd.to_numeric(factors["adj_factor"], errors="coerce")
        if not (factors["adj_factor"].gt(0) & factors["adj_factor"].lt(float("inf"))).all():
            raise ValueError("tushare returned invalid adjustment factors")
        latest_factor = factors.sort_values("trade_date").iloc[-1]["adj_factor"]
        df = df.merge(factors, on="trade_date", how="left", validate="one_to_one")
        if df["adj_factor"].isna().any():
            raise ValueError("tushare adjustment factors missing for price dates")
        df["adjustment_factor"] = df["adj_factor"] / latest_factor
        df[prices] = df[prices].mul(df["adj_factor"] / latest_factor, axis=0)
    else:
        df = df.drop(columns=prices, errors="ignore")
        df = df.rename(columns={f"{c}_qfq": c for c in prices})
    if not df[prices].abs().lt(float("inf")).all().all():
        raise ValueError("tushare returned invalid adjusted prices")
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1000
    col_map = {"trade_date": "date", "vol": "volume", "amount": "turnover", "pct_chg": "change_pct"}
    df = df.rename(columns=col_map)
    df = df.sort_values("date")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    keep = [
        c
        for c in ["date", "open", "high", "low", "close", "volume", "turnover", "change_pct", "adjustment_factor"]
        if c in df.columns
    ]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


def _quote_tushare(symbol: str) -> dict:
    """Tushare realtime quote via HTTP API."""
    ts_code = f"{symbol}.SH" if symbol.startswith(("6", "9", "5")) else f"{symbol}.SZ"

    columns, items = _tushare("realtime_quote", {"ts_code": ts_code}, error="tushare quote failed")
    row = dict(zip(columns, items[0]))
    return _clean_row(
        {
            "symbol": symbol,
            "name": row.get("name"),
            "price": row.get("price"),
            "change": row.get("change"),
            "change_pct": row.get("pct_chg"),
            "volume": row.get("vol"),
            "turnover": row.get("amount"),
            "high": row.get("high"),
            "low": row.get("low"),
            "open": row.get("open"),
            "prev_close": row.get("pre_close"),
        }
    )


def _kline_yfinance(symbol: str, period: str, count: int) -> list:
    import yfinance as yf

    period_map = {"daily": "1d", "weekly": "1wk", "monthly": "1mo"}
    days = count * 2 if period == "daily" else count * 10 if period == "weekly" else count * 35
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    df = yf.download(
        _yf_hk_symbol(symbol), start=start, interval=period_map.get(period, "1d"), progress=False, auto_adjust=True
    )
    if df.empty:
        raise ValueError(f"yfinance returned empty data for {symbol}")
    df = df.reset_index()
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    col_map = {
        "Date": "date",
        "Datetime": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    df = df.rename(columns=col_map)
    keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


def _kline_finnhub(symbol: str, period: str, count: int) -> list:
    import os

    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise ValueError("FINNHUB_API_KEY not set")
    import finnhub

    client = finnhub.Client(api_key=api_key)
    resolution_map = {"daily": "D", "weekly": "W", "monthly": "M"}
    resolution = resolution_map.get(period, "D")
    days = count * 2 if period == "daily" else count * 10 if period == "weekly" else count * 35
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days)).timestamp())
    candles = client.stock_candles(symbol.replace(".HK", ""), resolution, start, end)
    if candles.get("s") != "ok" or not candles.get("c"):
        raise ValueError(f"finnhub returned no data for {symbol}")
    rows = []
    for i in range(len(candles["c"])):
        rows.append(
            _clean_row(
                {
                    "date": datetime.fromtimestamp(candles["t"][i], timezone.utc).strftime("%Y-%m-%d"),
                    "open": candles["o"][i],
                    "high": candles["h"][i],
                    "low": candles["l"][i],
                    "close": candles["c"][i],
                    "volume": candles["v"][i],
                }
            )
        )
    return rows[-count:]


def _kline_longbridge(symbol: str, period: str, count: int) -> list:
    """Longbridge kline via longport SDK. Requires LONGBRIDGE_* env vars."""
    import os

    app_key = os.environ.get("LONGBRIDGE_APP_KEY")
    app_secret = os.environ.get("LONGBRIDGE_APP_SECRET")
    access_token = os.environ.get("LONGBRIDGE_ACCESS_TOKEN")
    if not all([app_key, app_secret, access_token]):
        raise ValueError("LONGBRIDGE credentials not set (APP_KEY/APP_SECRET/ACCESS_TOKEN)")

    from longport.openapi import AdjustType, Config, Period, QuoteContext

    config = Config(app_key=app_key, app_secret=app_secret, access_token=access_token)
    ctx = QuoteContext(config)

    period_map = {"daily": Period.Day, "weekly": Period.Week, "monthly": Period.Month}

    candlesticks = ctx.candlesticks(symbol, period_map.get(period, Period.Day), count, AdjustType.ForwardAdj)
    if not candlesticks:
        raise ValueError(f"longbridge returned no kline for {symbol}")

    rows = []
    for c in candlesticks:
        rows.append(
            _clean_row(
                {
                    "date": str(c.timestamp)[:10],
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": int(c.volume),
                    "turnover": float(c.turnover) if c.turnover else None,
                }
            )
        )
    return rows[-count:]


def _quote_longbridge(symbol: str) -> dict:
    """Longbridge realtime quote."""
    import os

    app_key = os.environ.get("LONGBRIDGE_APP_KEY")
    app_secret = os.environ.get("LONGBRIDGE_APP_SECRET")
    access_token = os.environ.get("LONGBRIDGE_ACCESS_TOKEN")
    if not all([app_key, app_secret, access_token]):
        raise ValueError("LONGBRIDGE credentials not set")

    from longport.openapi import Config, QuoteContext

    config = Config(app_key=app_key, app_secret=app_secret, access_token=access_token)
    ctx = QuoteContext(config)

    quotes = ctx.quote([symbol])
    if not quotes:
        raise ValueError(f"longbridge returned no quote for {symbol}")

    q = quotes[0]
    timestamp = getattr(q, "timestamp", None)
    prev_close = float(q.prev_close) if q.prev_close else None
    price = float(q.last_done) if q.last_done else None
    return _clean_row(
        {
            "symbol": symbol,
            "data_as_of": timestamp.isoformat()
            if isinstance(timestamp, datetime) and timestamp.utcoffset() is not None
            else None,
            "name": getattr(q, "symbol", symbol),
            "price": price,
            "change": (price - prev_close) if price and prev_close else None,
            "change_pct": float(q.change_rate) * 100 if getattr(q, "change_rate", None) else None,
            "volume": int(q.volume) if q.volume else None,
            "turnover": float(q.turnover) if getattr(q, "turnover", None) else None,
            "high": float(q.high) if q.high else None,
            "low": float(q.low) if q.low else None,
            "open": float(q.open) if q.open else None,
            "prev_close": prev_close,
        }
    )


def _kline_alphavantage(symbol: str, period: str, count: int) -> list:
    """Alpha Vantage kline. US stocks only. Requires ALPHAVANTAGE_API_KEY."""
    import os

    import requests

    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not api_key:
        raise ValueError("ALPHAVANTAGE_API_KEY not set")

    fn_map = {
        "daily": "TIME_SERIES_DAILY_ADJUSTED",
        "weekly": "TIME_SERIES_WEEKLY_ADJUSTED",
        "monthly": "TIME_SERIES_MONTHLY_ADJUSTED",
    }
    ts_key_map = {
        "daily": "Time Series (Daily)",
        "weekly": "Weekly Adjusted Time Series",
        "monthly": "Monthly Adjusted Time Series",
    }

    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={
            "function": fn_map.get(period, fn_map["daily"]),
            "symbol": symbol,
            "apikey": api_key,
            "outputsize": "full" if count > 100 else "compact",
        },
        timeout=20,
    )
    data = resp.json()
    ts_key = ts_key_map.get(period, ts_key_map["daily"])
    ts = data.get(ts_key)
    if not ts:
        raise ValueError(
            f"alphavantage returned no data for {symbol}: {data.get('Note', data.get('Error Message', ''))}"
        )

    rows = []
    for date_str, vals in sorted(ts.items()):
        close = float(vals["4. close"])
        adjusted = float(vals.get("5. adjusted close", "nan"))
        if not math.isfinite(close) or not math.isfinite(adjusted) or close <= 0 or adjusted <= 0:
            raise ValueError("alphavantage returned invalid adjustment prices")
        factor = adjusted / close
        rows.append(
            _clean_row(
                {
                    "date": date_str,
                    "open": float(vals["1. open"]) * factor,
                    "high": float(vals["2. high"]) * factor,
                    "low": float(vals["3. low"]) * factor,
                    "close": adjusted,
                    "adjustment_factor": factor,
                    "volume": int(vals.get("6. volume", vals.get("5. volume", 0))),
                }
            )
        )
    return rows[-count:]


def _quote_alphavantage(symbol: str) -> dict:
    """Alpha Vantage quote. US stocks only."""
    import os

    import requests

    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not api_key:
        raise ValueError("ALPHAVANTAGE_API_KEY not set")

    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key},
        timeout=20,
    )
    data = resp.json()
    gq = data.get("Global Quote")
    if not gq:
        raise ValueError(f"alphavantage quote failed for {symbol}")

    trading_day = None
    with contextlib.suppress(ValueError, TypeError):
        trading_day = date.fromisoformat(gq.get("07. latest trading day")).isoformat()
    return _clean_row(
        {
            "symbol": symbol,
            "data_as_of": trading_day,
            "price": float(gq.get("05. price", 0)),
            "change": float(gq.get("09. change", 0)),
            "change_pct": float(gq.get("10. change percent", "0").rstrip("%")),
            "volume": int(gq.get("06. volume", 0)),
            "high": float(gq.get("03. high", 0)),
            "low": float(gq.get("04. low", 0)),
            "open": float(gq.get("02. open", 0)),
            "prev_close": float(gq.get("08. previous close", 0)),
        }
    )


def _yf_failover(symbol: str, yfinance, tencent, finnhub, longbridge, alphavantage, label: str, evidence: bool = False):
    """Shared HK/US/JP/KR/TW failover chain. HK: yfinance → tencent → finnhub → longbridge;
    US: yfinance → finnhub → longbridge → tencent → alphavantage; JP/KR/TW: yfinance only.

    Keyless Tencent backs up HK first (finnhub's free tier has no HK). For US, configured
    keyed sources go first and Tencent precedes alphavantage to spare its 25 calls/day.
    Each provider is a zero-arg callable; callers bind their own symbol/period/count.
    """
    market = detect_market(symbol)
    sources = [("yfinance", yfinance)]
    if market == "HK":
        sources += [("tencent", tencent), ("finnhub", finnhub), ("longbridge", longbridge)]
    elif market == "US":
        sources += [
            ("finnhub", finnhub),
            ("longbridge", longbridge),
            ("tencent", tencent),
            ("alphavantage", alphavantage),
        ]
    return _failover(sources, label=label, evidence=evidence)


def kline_yf(symbol: str, period: str, count: int, evidence: bool = False) -> list:
    """HK/US/JP/KR/TW kline; see _yf_failover for the chain."""
    if symbol.upper() in _TENCENT_HK_INDEX:
        with socket_timeout(25):
            sources = [("tencent", lambda: _kline_tencent(symbol, period, count))]
            return _failover(sources, label=f"kline_hk_index:{symbol}", evidence=evidence)
    return _yf_failover(
        symbol,
        lambda: _kline_yfinance(symbol, period, count),
        lambda: _kline_tencent(symbol, period, count),
        lambda: _kline_finnhub(symbol, period, count),
        lambda: _kline_longbridge(symbol, period, count),
        lambda: _kline_alphavantage(symbol, period, count),
        label=f"kline:{symbol}",
        evidence=evidence,
    )


def cmd_kline(args):
    market = detect_market(args.symbol)
    # Round counts up so 30/60/80/120/250-bar callers share one fetch; the last intraday bar may be up to 60 s stale.
    bucket = next((b for b in (60, 120, 250) if args.count <= b), args.count)

    def fetch():
        if market == "A":
            data = kline_a(args.symbol, args.period, bucket, evidence=True)
            if data:
                adjusted = "none" if _A_INDEX_RE.fullmatch(args.symbol) else "qfq"
                data = [dict(row, adjusted=adjusted) for row in data]
            return data
        return kline_yf(args.symbol, args.period, bucket, evidence=True)

    try:
        data = cached(f"kline:v1:{args.symbol}:{args.period}:{bucket}", 60, fetch)
        return data[-args.count :] if isinstance(data, list) else data
    except Exception as e:
        return {"error": error_message(e)}


# --------------- quote ---------------


# Eastmoney answers per-code (ulist), ranked-page (clist) and server-filtered (datacenter)
# queries, so no caller needs a whole-market spot table to read a few rows.
_EM_HEADERS = {"User-Agent": "Mozilla/5.0"}
_EM_PUSH2 = "https://push2.eastmoney.com/api/qt"
_EM_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EM_FS = {
    "A": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
    "HK": "m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2",
    "US": "m:105,m:106,m:107",
}
_EM_PAGE = 100  # clist caps pz at 100 rows


def _get_json(url: str, params: dict) -> dict:
    """GET an eastmoney JSON API; relative paths are push2 qt endpoints."""
    import requests

    url = url if "://" in url else f"{_EM_PUSH2}/{url}"
    resp = requests.get(url, params=params, headers=_EM_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _em_secid(symbol: str) -> str:
    code = _cn_code(symbol)
    return f"{1 if code[:2] == 'sh' else 0}.{code[2:]}"


def _em_ulist(secids: list, fields: str) -> list:
    data = _get_json("ulist.np/get", {"fltt": 2, "invt": 2, "secids": ",".join(secids), "fields": fields}).get("data")
    return (data or {}).get("diff") or []


def _em_clist(market: str, fid: str, fields: str, page: int = 1, size: int = _EM_PAGE, desc: bool = True):
    """One ranked page of a market list → (universe total, rows); pn is a page index of size pz."""
    params = {"np": 1, "fltt": 2, "invt": 2, "fs": _EM_FS[market], "fid": fid, "po": int(desc), "pn": page, "pz": size}
    data = _get_json("clist/get", {**params, "fields": fields}).get("data") or {}
    return int(data.get("total") or 0), data.get("diff") or []


def _em_datacenter(report: str, filter_: str, sort: str, size: int, order: str = "-1", url: str = _EM_DATACENTER):
    """One server-filtered datacenter page → (match count, rows); no match is an empty page."""
    body = _get_json(
        url,
        {
            "reportName": report,
            "columns": "ALL",
            "filter": filter_,
            "sortColumns": sort,
            "sortTypes": order,
            "pageSize": size,
            "pageNumber": 1,
            "source": "WEB",
            "client": "WEB",
        },
    )
    if body.get("result") is None:
        if body.get("code") == 9201:  # 返回数据为空
            return 0, []
        raise ValueError(f"eastmoney {report} unavailable: {str(body.get('message'))[:100]}")
    return int(body["result"].get("count") or 0), body["result"].get("data") or []


_EM_CODETABLE = "https://search-codetable.eastmoney.com/codetable/search/web"


def _em_codetable(keyword: str, count: int) -> list:
    """Eastmoney code-table search rows (stocks, funds, indices and BK boards) matching a keyword."""
    params = {"keyword": keyword, "pageIndex": 1, "pageSize": count, "client": "web", "clientType": "webSuggest"}
    body = _get_json(_EM_CODETABLE, {**params, "clientVersion": "lastest"})
    if str(body.get("code")) != "0":
        raise ValueError(f"eastmoney search unavailable: {str(body.get('msg'))[:100]}")
    return body.get("result") or []


_EM_QUOTE_FIELDS = "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f23,f24,f38,f441"


def _quote_eastmoney(symbol: str) -> dict:
    """One-code eastmoney quote. ETFs add iopv (盘中实时估值), fund_shares (最新份额) and
    premium_discount_rate = (price - iopv) / iopv * 100 (positive = 溢价); None without an IOPV."""
    row = next((r for r in _em_ulist([_em_secid(symbol)], _EM_QUOTE_FIELDS) if str(r.get("f12")) == symbol), {})

    def value(key):
        return _parse_float(row.get(key))

    if value("f2") is None:
        raise ValueError(f"eastmoney returned no quote for {symbol}")
    result = {
        "symbol": symbol,
        "name": row.get("f14"),
        "price": value("f2"),
        "change": value("f4"),
        "change_pct": value("f3"),
        "volume": value("f5"),
        "turnover": value("f6"),
        "high": value("f15"),
        "low": value("f16"),
        "open": value("f17"),
        "prev_close": value("f18"),
        "market_cap": value("f20"),
        "turnover_rate": value("f8"),
        "volume_ratio": value("f10"),
    }
    if normalize_stock_code(symbol)["is_etf"]:
        price, iopv = result["price"], value("f441")
        premium = round((price - iopv) / iopv * 100, 4) if iopv else None
        result.update(is_etf=True, iopv=iopv, fund_shares=value("f38"), premium_discount_rate=premium)
    else:
        result.update(
            pe=value("f9"), pe_basis="dynamic", pb=value("f23"), amplitude=value("f7"), change_pct_60d=value("f24")
        )
    return _clean_row(result)


def quote_a(symbol: str, evidence: bool = False) -> dict:
    """A-share quote with failover: tencent → eastmoney → tushare → sina, one code per request.

    Explicitly-prefixed symbols (sh000001, typically indices) go straight to
    tencent/sina — a bare secid would be ambiguous (sh000001 vs sz000001). ETF quotes use their own
    chain key (quote_a_etf) so a stock-side winner never reorders the ETF chain."""
    if _A_PREFIXED_RE.fullmatch(symbol):
        sources = [("tencent", lambda: _quote_tencent(symbol)), ("sina", lambda: _quote_sina(symbol))]
        with socket_timeout(25):
            return _failover(sources, label=f"quote_a:{symbol}", evidence=evidence)
    is_etf = normalize_stock_code(symbol)["is_etf"]
    sources = [
        ("tencent", lambda: _quote_tencent(symbol)),
        ("eastmoney", lambda: _quote_eastmoney(symbol)),
        ("tushare", lambda: _quote_tushare(symbol)),
        ("sina", lambda: _quote_sina(symbol)),
    ]
    chain = "quote_a_etf" if is_etf else "quote_a"
    with socket_timeout(25):
        return _failover(sources, label=f"{chain}:{symbol}", evidence=evidence)


def _quote_yfinance(symbol: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(_yf_hk_symbol(symbol))
    info = t.info
    if not info or "regularMarketPrice" not in info:
        raise ValueError(f"No data for {symbol}")
    return _clean_row(
        {
            "symbol": symbol,
            "data_as_of": _unix_quote_time(info.get("regularMarketTime")),
            "name": info.get("shortName"),
            "price": info.get("regularMarketPrice") or info.get("currentPrice"),
            "change": info.get("regularMarketChange"),
            "change_pct": info.get("regularMarketChangePercent"),
            "volume": info.get("regularMarketVolume"),
            "high": info.get("regularMarketDayHigh"),
            "low": info.get("regularMarketDayLow"),
            "open": info.get("regularMarketOpen"),
            "prev_close": info.get("regularMarketPreviousClose"),
            "market_cap": info.get("marketCap"),
            "pe": info.get("trailingPE"),
            "pe_basis": "trailing",
            "pb": info.get("priceToBook"),
        }
    )


def _quote_finnhub(symbol: str) -> dict:
    import os

    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise ValueError("FINNHUB_API_KEY not set")
    import finnhub

    client = finnhub.Client(api_key=api_key)
    q = client.quote(symbol.replace(".HK", ""))
    if not q or q.get("c") is None or q["c"] == 0:
        raise ValueError(f"finnhub returned no quote for {symbol}")
    return _clean_row(
        {
            "symbol": symbol,
            "data_as_of": _unix_quote_time(q.get("t")),
            "price": q["c"],
            "change": q["d"],
            "change_pct": q["dp"],
            "high": q["h"],
            "low": q["l"],
            "open": q["o"],
            "prev_close": q["pc"],
        }
    )


def quote_yf(symbol: str, evidence: bool = False) -> dict:
    """HK/US/JP/KR/TW quote; see _yf_failover for the chain."""
    if symbol.upper() in _TENCENT_HK_INDEX:
        sources = [("tencent", lambda: {"symbol": symbol, **_quote_tencent_hk_index(symbol.upper())})]
        return _failover(sources, label=f"quote_hk_index:{symbol}", evidence=evidence)
    return _yf_failover(
        symbol,
        lambda: _quote_yfinance(symbol),
        lambda: _quote_tencent(symbol),
        lambda: _quote_finnhub(symbol),
        lambda: _quote_longbridge(symbol),
        lambda: _quote_alphavantage(symbol),
        label=f"quote:{symbol}",
        evidence=evidence,
    )


def _quote_stale_marker(market: str):
    """Stale-data annotation for a quote fetched outside the market's live session.

    Quotes carry no date of their own, so a weekend/holiday or pre-market call
    would otherwise present the last trading day's close as if it were live
    (issue #31 follow-up). Returns None — no annotation — during live sessions
    and post-market (today's close), or when the calendar is unavailable.
    pre_market covers the call-auction window (e.g. CN 09:15-09:29), where the
    quote is actually auction-indicative — an accepted approximation rather than
    hand-maintaining per-market auction start times."""
    from trading_calendar import market_phase, prev_trading_days

    # detect_market labels A-shares "A"; the trading calendar calls the same market "CN"
    cal_market = "CN" if market == "A" else market
    try:
        info = market_phase(cal_market)
        if info.get("error") or info.get("phase") not in ("closed", "pre_market"):
            return None
        prev = prev_trading_days(cal_market, 1, from_date=info["date"])
    except Exception:
        return None  # calendar unavailable — don't guess
    if not prev:
        return None
    as_of = prev[0]
    return {
        "as_of": as_of,
        "as_of_basis": "trading_calendar",
        "stale": True,
        "note": f"market is not in a live session; quote is the last completed trading day {as_of}'s close",
    }


_YOUZHIYOUXING_TIAA = {
    "TIAA001001": "长钱账户",
    "TIAA001002": "海外长钱",
    "TIAA001004": "稳钱账户",
}


def _quote_tiaa(symbol: str, *, include_disclosures: bool = True) -> dict:
    import requests

    disclosure_name = _YOUZHIYOUXING_TIAA.get(symbol)
    result = {
        "symbol": symbol,
        "name": disclosure_name,
        "asset_type": "investment_advisory_portfolio",
        "type_description": "雪球投顾组合，无统一单位净值",
        "manager": None,
        "advisor_code": f"A{symbol[4:7]}",
        "advisor_org": None,
        "advisor_mapping_basis": "tiaa_prefix_best_effort",
        "advisor_source_url": None,
        "advisor_retrieved_at": None,
        "nav": None,
        "example_account_performance": None,
        "quote_type": None,
        "exchange": None,
        "source": None,
        "source_url": f"https://xueqiu.com/S/{symbol}",
        "data_as_of": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "official_disclosures": [],
        "public_search_results": [],
        "disclosure_provider": "youzhiyouxing" if disclosure_name else None,
        "disclosure_status": "unavailable" if disclosure_name else "unsupported_provider",
        "disclosures_partial": True,
        "disclosures_as_of": None,
        "note": (
            "投顾组合无统一净值；主理人及完整业绩/持仓需在对应投顾平台登录后查询；"
            "官方公开披露仅为部分历史信息，持有人收益随买入时点和调仓跟进不同"
        ),
    }
    try:
        from tiaa_disclosures import advisor_metadata

        result.update(advisor_metadata(symbol))
    except Exception:
        result.setdefault("warnings", []).append("advisor organization metadata unavailable")
    if not include_disclosures and disclosure_name:
        return result

    try:
        with requests.Session() as session:
            session.get(
                "https://xueqiu.com/",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
                allow_redirects=True,
            ).raise_for_status()
            response = session.get(
                "https://stock.xueqiu.com/v5/stock/quote.json",
                params={"symbol": symbol, "extend": "detail"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://xueqiu.com/"},
                timeout=10,
            )
            response.raise_for_status()
        payload = response.json()
        if payload.get("error_code") not in (None, 0):
            raise ValueError("xueqiu portfolio metadata unavailable")
        quote = (payload.get("data") or {}).get("quote") or {}
        if quote.get("symbol") != symbol:
            raise ValueError("xueqiu returned different portfolio metadata")
        result.update(
            name=quote.get("name") or disclosure_name,
            quote_type=quote.get("type"),
            exchange=quote.get("exchange"),
            source="xueqiu",
            data_as_of=_unix_quote_time(quote.get("timestamp")),
        )
    except Exception:
        result.setdefault("warnings", []).append(
            "xueqiu portfolio metadata unavailable; type guidance remains available"
        )
    if not include_disclosures:
        return result

    if disclosure_name and result["name"] in (None, disclosure_name):
        try:
            from tiaa_disclosures import public_disclosures

            disclosure = public_disclosures(symbol, disclosure_name)
            result["official_disclosures"] = disclosure["items"]
            result["disclosures_as_of"] = disclosure["as_of"]
            if disclosure["items"]:
                result["disclosure_status"] = "partial_public"
        except Exception:
            result.setdefault("warnings", []).append(
                "official public disclosures unavailable; type guidance remains available"
            )
    elif disclosure_name and result["name"]:
        result["disclosure_status"] = "unsupported_provider"
        result.setdefault("warnings", []).append("portfolio name does not match the registered disclosure source")
    else:
        from tiaa_disclosures import search_public

        result["public_search_results"] = search_public(symbol, result["name"])
        if result["public_search_results"]:
            result["disclosure_provider"] = "web_search"
            result["disclosure_status"] = "search_partial"
    return result


def cmd_quote(args):
    watchlist = getattr(args, "watchlist", False)
    if re.fullmatch(r"TIAA\d{6}", args.symbol, re.IGNORECASE):
        return _quote_tiaa(args.symbol.upper(), include_disclosures=not watchlist)
    # detect_market reads every ^index as US; known region indices keep their own calendar for freshness.
    market = next(
        (region.upper() for region, symbols in _REGION_INDEX_SYMBOLS.items() if args.symbol.upper() in symbols),
        None,
    ) or detect_market(args.symbol)
    # Preserve explicit exchange prefixes for stocks and indices sharing a fund code.
    fund_fallback = watchlist and re.fullmatch(r"[0-9]{6}", args.symbol)
    symbol = _cn_code(args.symbol) if fund_fallback else args.symbol
    try:
        result = quote_a(symbol, evidence=True) if market == "A" else quote_yf(symbol, evidence=True)
    except Exception as e:
        result = {"error": error_message(e)}
        if fund_fallback and isinstance(e, _ExchangeQuoteMissing):
            fund = cmd_fund_quote(args, trend_count=30)
            if "error" in fund:
                return {"error": f"fund_quote:{args.symbol}: {fund['error']}"}
            return fund
    # Exchange quotes cannot see open-end funds. Stock-only prefixes (SH 60/68, ChiNext 30, BSE) keep the bare error.
    if (
        isinstance(result, dict)
        and "error" in result
        and re.fullmatch(r"\d{6}", args.symbol)
        and not re.match(r"(?:60|68|30|43|83|87|92)", args.symbol)
    ):
        result["hint"] = (
            f"{args.symbol} 若为场外公募基金，请改用 get_fund_quote（stock_data.py fund_quote {args.symbol}）"
        )
        return result
    if isinstance(result, dict) and "error" not in result:
        if fund_fallback and "symbol" in result:
            result["symbol"] = args.symbol
        marker = _quote_stale_marker(market)
        if marker:
            result.update(marker)
        result.setdefault("data_as_of", None)
        if result["data_as_of"]:
            result["as_of"] = result["data_as_of"]
            result["as_of_basis"] = "provider"
            if marker:
                result["calendar_as_of"] = marker["as_of"]
                result["note"] = (
                    "Quote time is supplied by the provider; the market calendar is a separate freshness hint"
                )
            try:
                from trading_calendar import market_phase

                info = market_phase("CN" if market == "A" else market)
                value = result["data_as_of"]
                quoted_day = date.fromisoformat(value) if len(value) == 10 else None
                quoted = datetime.fromisoformat(value)
                if quoted.tzinfo:
                    quoted_day = quoted.astimezone(ZoneInfo(info["timezone"])).date()
                if quoted_day and quoted_day.isoformat() < info["date"]:
                    result["stale"] = True
                    result["note"] = "Provider quote predates the current market date"
            except (ValueError, KeyError, TypeError, OSError):
                pass  # Calendar/timestamp unavailable: retain the provider evidence without guessing.
    return result


QUOTES_DEADLINE = 30  # dsh/dashboard.ts allows the whole process 40 s
QUOTES_MAX = 40


def _batch(symbols_arg: str, one, label: str) -> list | dict:
    """Run `one(symbol)` for several symbols in one process for the dsh dashboard; rows follow input order,
    a failure or a row still running at the deadline stays per row."""
    symbols = [symbol.strip() for symbol in symbols_arg.split(",") if symbol.strip()]
    if not 0 < len(symbols) <= QUOTES_MAX:
        return {"error": f"{label}s takes 1-{QUOTES_MAX} comma-separated symbols"}
    rows = [{"error": f"{label} timed out"}] * len(symbols)
    workers = threading.BoundedSemaphore(8)

    def run(index, symbol):
        with workers:
            try:
                rows[index] = one(symbol)
            except Exception as e:
                rows[index] = {"error": error_message(e)}

    # Daemon threads: a provider that hangs past the deadline cannot keep the process (and every other row) alive.
    threads = [threading.Thread(target=run, args=item, daemon=True) for item in enumerate(symbols)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + QUOTES_DEADLINE
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    return list(rows)


def cmd_quotes(args):
    watchlist = getattr(args, "watchlist", False)
    return _batch(
        args.symbols, lambda symbol: cmd_quote(argparse.Namespace(symbol=symbol, watchlist=watchlist)), "quote"
    )


def cmd_klines(args):
    return _batch(
        args.symbols,
        lambda symbol: cmd_kline(argparse.Namespace(symbol=symbol, period="daily", count=args.count)),
        "kline",
    )


# --------------- open-end fund quote ---------------


def _fund_js_value(text: str, name: str):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*", text)
    if not match:
        raise ValueError(f"eastmoney fund data missing {name}")
    try:
        return json.JSONDecoder().raw_decode(text[match.end() :])[0]
    except json.JSONDecodeError as exc:
        raise ValueError(f"eastmoney fund data has invalid {name}") from exc


def _fund_nav_date(stamp) -> str:
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def fund_pingzhong_js(symbol: str) -> str:
    """Eastmoney's per-fund JS bundle (NAV, scale, holdings series)."""
    import requests

    response = requests.get(f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js", timeout=15)
    response.raise_for_status()
    return response.text


def cmd_fund_quote(args, *, trend_count: int = 0):
    symbol = args.symbol
    if not re.fullmatch(r"\d{6}", symbol):
        return {"error": "open-end fund symbol must be exactly 6 digits"}
    try:
        text = fund_pingzhong_js(symbol)
        if _fund_js_value(text, "fS_code") != symbol:
            raise ValueError(f"eastmoney returned a different fund for {symbol}")
        name = _fund_js_value(text, "fS_name")
        nav_rows = _fund_js_value(text, "Data_netWorthTrend")
        accumulated_rows = [] if trend_count else _fund_js_value(text, "Data_ACWorthTrend")
        points = []
        for row in reversed(nav_rows):
            if not isinstance(row, dict):
                continue
            nav = _parse_float(row.get("y"))
            if nav is None or not math.isfinite(nav) or nav <= 0:
                continue
            try:
                day = _fund_nav_date(row.get("x"))
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            points.append((row, day, nav))
            if len(points) >= max(1, trend_count):
                break
        if not points:
            raise ValueError(f"eastmoney returned no NAV for {symbol}")
        latest, nav_date, nav = points[0]
        accumulated_by_date = {_fund_nav_date(row[0]): row[1] for row in accumulated_rows}
        today = _cn_now().date().isoformat()
        result = _clean_row(
            {
                "symbol": symbol,
                "name": name,
                "asset_type": "open_end_fund",
                "nav": nav,
                "accumulated_nav": accumulated_by_date.get(nav_date),
                "change_pct": latest.get("equityReturn"),
                "nav_date": nav_date,
                "data_as_of": nav_date,
                "as_of": nav_date,
                "as_of_basis": "provider",
                "stale": nav_date < today,
                "source": "eastmoney",
                "source_url": f"https://fund.eastmoney.com/{symbol}.html",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "note": (
                    "nav is published end-of-day NAV, not a real-time price; QDII disclosure may lag "
                    "1-2 trading days; no intraday estimated NAV is provided"
                ),
                # Eastmoney retired its per-fund intraday estimate (fundgz); its remaining estimate
                # page lists every fund at once, so the estimate keys stay but are always unavailable.
                "estimated_nav": None,
                "estimated_change_pct": None,
                "estimated_at": None,
                "estimate_status": "unavailable",
            }
        )
        if trend_count:
            result["trend"] = [{"date": day, "close": nav} for _, day, nav in reversed(points)]
        return result
    except Exception as e:
        return {"error": error_message(e)}


# --------------- capital_flow ---------------


def _capital_flow_efinance(symbol: str) -> list:
    """Fallback: fetch individual stock capital flow via efinance."""
    import efinance as ef
    import pandas as pd

    df = ef.stock.get_today_bill(symbol)
    if df is None or df.empty:
        raise ValueError("efinance capital flow returned empty")
    col_map = {
        "日期": "date",
        "主力净流入": "main_net_inflow",
        "超大单净流入": "super_large_net",
        "大单净流入": "large_net",
        "中单净流入": "medium_net",
        "小单净流入": "small_net",
    }
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    for c in keep:
        if c != "date":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[keep].tail(10)
    return [_clean_row(r) for r in df.to_dict("records")]


def cmd_capital_flow(args):
    mode = getattr(args, "mode", "detail")

    if mode == "sector_flow":
        try:
            import akshare as ak

            df = _akshare_retry(getattr(ak, "stock_fund_flow_sector_rank_em", None) or ak.stock_sector_fund_flow_rank)
            if df is None or df.empty:
                return {"error": "Sector fund flow data unavailable"}
            col_map = {
                "名称": "name",
                "今日涨跌幅": "change_pct",
                "今日主力净流入-净额": "main_net_inflow",
                "今日主力净流入-净占比": "main_pct",
                "今日超大单净流入-净额": "super_large_net",
                "今日大单净流入-净额": "large_net",
            }
            df = df.rename(columns=col_map)
            keep = [c for c in col_map.values() if c in df.columns]
            return [_clean_row(r) for r in df[keep].head(20).to_dict("records")]
        except Exception as e:
            return {"error": error_message(e)}

    if detect_market(args.symbol) != "A":
        return {"error": "capital_flow only available for A-shares"}
    try:
        import akshare as ak

        # Exchange via the shared prefix helper: bare 9/5-prefix SH codes, BSE
        # (43/81-83/87/88/92) and explicitly-prefixed input (sh600519) all resolve;
        # akshare wants the bare 6-digit stock plus market ∈ {sh, sz, bj}.
        code = _cn_code(args.symbol)
        market, stock = code[:2], code[2:]
        # Positional: the 1.20.45 rename also renamed `stock=` to `symbol=`.
        flow = getattr(ak, "stock_fund_flow_individual_hist_em", None) or ak.stock_individual_fund_flow
        df = _akshare_retry(flow, stock, market)
        col_map = {
            "日期": "date",
            "主力净流入-净额": "main_net_inflow",
            "主力净流入-净占比": "main_pct",
            "超大单净流入-净额": "super_large_net",
            "大单净流入-净额": "large_net",
            "中单净流入-净额": "medium_net",
            "小单净流入-净额": "small_net",
        }
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]

        if mode == "summary":
            import pandas as pd

            df_all = df[keep].copy()
            for c in ["main_net_inflow", "super_large_net", "large_net", "medium_net", "small_net"]:
                if c in df_all.columns:
                    df_all[c] = pd.to_numeric(df_all[c], errors="coerce")
            summary = {"symbol": args.symbol}
            for days, label in [(5, "5d"), (10, "10d"), (20, "20d")]:
                tail = df_all.tail(days)
                summary[f"main_net_{label}"] = _sanitize(tail.get("main_net_inflow", pd.Series()).sum(min_count=days))
                summary[f"super_large_net_{label}"] = _sanitize(
                    tail.get("super_large_net", pd.Series()).sum(min_count=days)
                )
            recent = df_all["main_net_inflow"].tail(5) if "main_net_inflow" in df_all.columns else pd.Series()
            if len(recent) >= 3:
                pos_count = (recent > 0).sum()
                summary["trend"] = "inflow" if pos_count >= 4 else "outflow" if pos_count <= 1 else "mixed"
            return _clean_row(summary)

        df = df[keep].tail(10)
        return [_clean_row(r) for r in df.to_dict("records")]
    except Exception:
        try:
            return _capital_flow_efinance(args.symbol)
        except Exception as e2:
            return {"error": error_message(e2)}


# --------------- news ---------------


def _news_search_intel_fallback(symbol: str) -> list:
    """Fallback: use search_intel to find news when primary sources fail."""
    data = run_tool("search_intel.py", ["search", f"{symbol} 最新消息"])
    if isinstance(data, dict):
        data = data.get("results", [])
    if isinstance(data, list):
        return [{"title": item.get("title", ""), "url": item.get("url", ""), "source": "search"} for item in data[:10]]
    return []


def cmd_news(args):
    market = detect_market(args.symbol)
    try:
        if market == "A":
            import akshare as ak

            df = _akshare_retry(ak.stock_news_em, symbol=args.symbol)
            if df is None or df.empty:
                raise ValueError("akshare news empty")
            col_map = {
                "新闻标题": "title",
                "发布时间": "datetime",
                "新闻来源": "source",
                "新闻链接": "url",
                "新闻内容": "content",
            }
            df = df.rename(columns=col_map)
            keep = [c for c in col_map.values() if c in df.columns]
            cutoff = _cn_now() - timedelta(days=args.days)
            df = df[keep]
            if "datetime" in df.columns:
                import pandas as pd

                df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
                df = df[df["datetime"] >= cutoff]
            return [_clean_row(r) for r in df.head(20).to_dict("records")]
        else:
            import yfinance as yf

            t = yf.Ticker(_yf_hk_symbol(args.symbol))
            news = t.news
            if not news:
                raise ValueError("yfinance news empty")
            results = []
            for item in news[:20]:
                content = item.get("content") or item  # yfinance >= 0.2.50 nests fields under `content`
                results.append(
                    {
                        "title": content.get("title") or item.get("title", ""),
                        "publisher": (content.get("provider") or {}).get("displayName") or item.get("publisher", ""),
                        "url": (content.get("canonicalUrl") or {}).get("url") or item.get("link", ""),
                        "published": content.get("pubDate") or item.get("providerPublishTime", ""),
                        "type": content.get("contentType") or item.get("type", ""),
                    }
                )
            if not any(r["title"] for r in results):
                raise ValueError("yfinance news has no titles")
            return results
    except Exception:
        return _news_search_intel_fallback(args.symbol)


# --------------- financials ---------------


def _financials_a_row(r) -> dict:
    return _clean_row(
        {
            "report_date": r.get("日期"),
            "roe": r.get("净资产收益率(%)"),
            "net_profit_margin": r.get("销售净利率(%)"),
            "gross_margin": r.get("销售毛利率(%)"),
            "debt_ratio": r.get("资产负债率(%)"),
            "current_ratio": r.get("流动比率"),
        }
    )


def _cached_frame(key, fetch):
    import pandas as pd

    rows = cached(key, DAY, lambda: [_clean_row(r) for r in fetch().to_dict("records")])
    return pd.DataFrame(rows)


def _yf_info(symbol, ticker):
    def fetch():
        info = ticker.info
        if not isinstance(info, dict) or not any(
            info.get(k) for k in ("shortName", "longName", "currency", "exchange")
        ):
            raise ValueError("Yahoo basic information unavailable")
        return info

    return cached(f"yf-basic-info:{_yf_hk_symbol(symbol)}", DAY, fetch)


def _financials_a_frame(symbol, periods=1, annual=False):
    """Sina serves one page per report year, and akshare's default start_year=1900 walks every year
    since listing (~30 requests). Start at the current year and step back only until `periods` rows exist."""
    import akshare as ak
    import pandas as pd

    year = _cn_now().year
    # This year's annual report never exists within this year; one step back covers reports not yet out.
    first = year - periods if annual else year - -(-periods // 4) + 1
    for start in (first, first - 1):
        df = _cached_frame(
            f"financial-source:{symbol}:{start}",
            lambda start=start: _akshare_retry(
                ak.stock_financial_analysis_indicator, symbol=symbol, start_year=str(start)
            ),
        )
        dates = df["日期"].astype(str) if "日期" in df.columns else pd.Series(dtype=str)
        if (dates.str.endswith("12-31").sum() if annual else len(dates)) >= periods:
            break
    return df.sort_values("日期", ascending=False) if "日期" in df.columns else df


def financials_a(symbol: str, periods: int = 1, annual: bool = False) -> dict:
    try:
        df = _financials_a_frame(symbol, periods, annual)
        if annual and not df.empty:
            df = df[df["日期"].astype(str).str.endswith("12-31")]
        if df is None or df.empty:
            # error + note coexist (additive): consumers check either key
            return {"symbol": symbol, "error": "No financial data", "note": "No financial data"}
        result = {"symbol": symbol, **_financials_a_row(df.iloc[0])}
        if periods > 1:
            result["periods"] = [_financials_a_row(r) for r in df.head(periods).to_dict("records")]
            result["available_periods"] = len(result["periods"])
        return result
    except Exception:
        return {"symbol": symbol, "error": "Financial data unavailable", "note": "Financial data unavailable"}


def _yf_financial_periods(t, periods: int) -> list:
    """Best-effort annual history from yfinance's income statement (t.info is a
    snapshot only). Silent [] on any failure — multi-period is a bonus, never a blocker."""
    try:
        fin = t.financials
        if fin is None or fin.empty:
            return []
        rows = []
        for col in list(fin.columns)[:periods]:
            revenue = _sanitize(_parse_float(fin[col].get("Total Revenue")))
            profit = _sanitize(_parse_float(fin[col].get("Net Income")))
            gross = _sanitize(_parse_float(fin[col].get("Gross Profit")))
            rows.append(
                _clean_row(
                    {
                        "period_end": col,
                        "total_revenue": revenue,
                        "net_income": profit,
                        "net_profit_margin": profit / revenue * 100
                        if profit is not None and revenue and revenue > 0
                        else None,
                        "gross_margin": gross / revenue * 100
                        if gross is not None and revenue and revenue > 0
                        else None,
                    }
                )
            )
        return rows
    except Exception:
        return []


def financials_yf(symbol: str, periods: int = 1, annual: bool = False) -> dict:
    import yfinance as yf

    t = yf.Ticker(_yf_hk_symbol(symbol))
    warnings = []
    try:
        info = _yf_info(symbol, t)
    except Exception:
        info = {}
        warnings.append("Yahoo snapshot unavailable")
    result = _clean_row(
        {
            "symbol": symbol,
            "name": info.get("shortName"),
            "currency": info.get("financialCurrency"),
            "market_cap": info.get("marketCap"),
            "market_cap_currency": info.get("currency"),
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "pb": info.get("priceToBook"),
            "total_revenue": info.get("totalRevenue"),
            "net_income": info.get("netIncomeToCommon"),
            "profit_margin": info.get("profitMargins"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "dividend_yield": info.get("dividendYield"),
            "snapshot_units": {
                "pe": "trailing",
                "roe": "ratio",
                "profit_margin": "ratio",
                "debt_to_equity": "percent",
                "dividend_yield": "ratio",
            },
        }
    )
    if warnings:
        result["warnings"] = warnings
    if periods > 1 or (annual and detect_market(symbol) == "HK"):
        history = cached(f"yf-income-history:{_yf_hk_symbol(symbol)}", DAY, lambda: _yf_financial_periods(t, 40))[
            :periods
        ]
        if detect_market(symbol) == "HK" and annual:
            try:
                import akshare as ak

                hk_symbol = _yf_hk_symbol(symbol).split(".")[0].zfill(5)
                df = _cached_frame(
                    f"hk-annual-indicators:{hk_symbol}",
                    lambda: _akshare_retry(
                        ak.stock_financial_hk_analysis_indicator_em, symbol=hk_symbol, indicator="年度"
                    ),
                )
                if df.empty:
                    raise ValueError("HK annual indicators unavailable")
                if df is not None and not df.empty:
                    df = df.sort_values("REPORT_DATE", ascending=False)
                    history = [
                        _clean_row(
                            {
                                "report_date": str(r.get("REPORT_DATE"))[:10],
                                "roe": r.get("ROE_AVG"),
                                "gross_margin": r.get("GROSS_PROFIT_RATIO"),
                                "net_profit_margin": r.get("NET_PROFIT_RATIO"),
                                "currency": r.get("CURRENCY"),
                            }
                        )
                        for r in df.head(periods).to_dict("records")
                    ]
            except Exception:
                result.setdefault("warnings", []).append(
                    "HK annual indicators unavailable; available Yahoo history retained"
                )
        if history:
            result["periods"] = history
            result["available_periods"] = len(history)
            result["requested_periods"] = periods
            result["history_units"] = {"roe": "percent", "gross_margin": "percent", "net_profit_margin": "percent"}
    if not info and not result.get("periods"):
        result["error"] = "Financial data unavailable"
    return result


def _yf_dcf_inputs(symbol: str, periods: int) -> dict:
    import yfinance as yf

    if detect_market(symbol) == "A":
        code = _cn_code(symbol)
        if code.startswith("bj"):
            raise ValueError("Yahoo has no Beijing Stock Exchange statements")
        ticker = yf.Ticker(f"{code[2:]}.{'SS' if code.startswith('sh') else 'SZ'}")
    else:
        ticker = yf.Ticker(_yf_hk_symbol(symbol))
    tables = [ticker.income_stmt, ticker.cashflow, ticker.balance_sheet]
    warnings = ["Yahoo standardized annual statements; verify against filings before valuation."]
    try:
        currency, reason = _yf_info(symbol, ticker).get("financialCurrency"), "not reported"
    except Exception as e:
        currency, reason = None, error_message(e)
    if not currency:
        warnings.append(f"Yahoo financial currency unavailable ({reason}); rows cannot fill other sources")
    fields = {
        "revenue": (0, "Total Revenue"),
        "operating_income": (0, "Operating Income"),
        "pretax_income": (0, "Pretax Income"),
        "income_tax": (0, "Tax Provision"),
        "diluted_shares": (0, "Diluted Average Shares"),
        "operating_cash_flow": (1, "Operating Cash Flow"),
        "capital_expenditure": (1, "Capital Expenditure"),
        "depreciation_amortization": (1, "Depreciation And Amortization"),
        "cash": (2, "Cash And Cash Equivalents"),
        "lease_liabilities": (2, "Capital Lease Obligations"),
        "minority_interest": (2, "Minority Interest"),
        "shares_outstanding": (2, "Ordinary Shares Number"),
    }
    ends = sorted(
        {str(col)[:10] for table in tables if table is not None and not table.empty for col in table.columns},
        reverse=True,
    )[:periods]
    rows = []
    for end in ends:
        entry = {"period_end": end, "currency": currency}

        def cell(index, label, end=end):
            table = tables[index]
            column = next((col for col in getattr(table, "columns", []) if str(col)[:10] == end), None)
            return (
                _sanitize(_parse_float(table.loc[label, column]))
                if column is not None and label in table.index
                else None
            )

        for key, (index, label) in fields.items():
            value = cell(index, label)
            entry[key] = abs(value) if key == "capital_expenditure" and value is not None else value
        # Yahoo "Total Debt" adds lease liabilities; SEC/Lixinger total_debt excludes them.
        debt = [value for value in (cell(2, "Current Debt"), cell(2, "Long Term Debt")) if value is not None]
        entry["total_debt"] = sum(debt) if debt else None
        rows.append(entry)
    if not rows:
        raise ValueError("Yahoo annual statements unavailable")
    return {
        "source": "yfinance",
        "periods": rows,
        "warnings": warnings + ["lease_liabilities is Yahoo Capital Lease Obligations (may omit operating leases)."],
    }


DCF_FIELDS = (
    "revenue",
    "operating_income",
    "pretax_income",
    "income_tax",
    "operating_cash_flow",
    "capital_expenditure",
    "depreciation_amortization",
    "cash",
    "total_debt",
    "lease_liabilities",
    "minority_interest",
    "diluted_shares",
    "shares_outstanding",
)


def _fill_dcf_rows(result: dict, fallback: dict | None) -> dict:
    """Fill null primary fields from Yahoo for the same fiscal period and currency, recording each field's source."""
    for row in result["periods"]:
        match = None
        if fallback:
            end = datetime.fromisoformat(row["period_end"]).date()
            match = next(
                (
                    other
                    for other in fallback["periods"]
                    if abs((datetime.fromisoformat(other["period_end"]).date() - end).days) <= 10
                    and other.get("currency")
                    and other.get("currency") == row.get("currency")
                ),
                None,
            )
        sources = {}
        for key in DCF_FIELDS:
            # Yahoo leases are finance-lease-only "Capital Lease Obligations"; never mix them into another source.
            if row.get(key) is None and match and match.get(key) is not None and key != "lease_liabilities":
                row[key], sources[key] = match[key], fallback["source"]
            elif row.get(key) is not None:
                sources[key] = result["source"]
            else:
                row[key] = None
        pretax, tax = row["pretax_income"], row["income_tax"]
        row["effective_tax_rate"] = tax / pretax if tax is not None and pretax and pretax > 0 else None
        row["field_sources"] = sources
        row["missing_fields"] = [key for key in DCF_FIELDS if row[key] is None]
    result["units"] = {"amounts": "reported currency units", "shares": "shares", "effective_tax_rate": "ratio"}
    return result


def _dcf_inputs(symbol: str, periods: int) -> dict:
    market = detect_market(symbol)
    if market not in ("A", "HK", "US"):
        return {"error": "DCF inputs support A, HK and US stocks only"}
    warnings, primary = [], None
    if market == "US" or os.environ.get("LIXINGER_TOKEN"):
        label = "SEC companyfacts" if market == "US" else f"Lixinger {market}"
        try:
            if market == "US":
                import sec_edgar

                primary = sec_edgar.dcf_inputs(symbol, periods)
            else:
                import lixinger

                code = _cn_code(symbol)[2:] if market == "A" else symbol.split(".")[0].zfill(5)
                primary = lixinger.dcf_inputs("cn" if market == "A" else "hk", code, periods)
        except Exception as e:
            warnings.append(f"{label} unavailable: {error_message(e)}; Yahoo fallback used")
    try:
        yahoo = _yf_dcf_inputs(symbol, periods)
    except Exception as e:
        if primary is None:
            return {"error": f"DCF inputs unavailable: {error_message(e)}", "warnings": warnings}
        yahoo = None
        warnings.append(f"Yahoo fill unavailable: {error_message(e)}")
    result = _fill_dcf_rows(primary or yahoo, yahoo if primary else None)
    result["warnings"] = warnings + result["warnings"]
    return result


def cmd_financials(args):
    periods = getattr(args, "periods", 1)
    if not 1 <= periods <= 40:
        return {"error": f"periods must be between 1 and 40, got {periods}"}
    annual = getattr(args, "annual", False)
    market = detect_market(args.symbol)
    result = _financials_without_dcf(args, periods, annual, market)
    if getattr(args, "include_dcf_inputs", False) and "error" not in result:
        result["dcf_inputs"] = _dcf_inputs(args.symbol, periods)
    return result


def _financials_without_dcf(args, periods, annual, market):
    try:
        if market == "HK":
            if os.environ.get("LIXINGER_TOKEN"):
                try:
                    from lixinger import financials_hk

                    result = {
                        "symbol": args.symbol,
                        **financials_hk(args.symbol.split(".")[0].zfill(5), periods, annual),
                    }
                    snapshot = financials_yf(args.symbol, 1, False)
                    result.update(
                        {
                            key: value
                            for key, value in snapshot.items()
                            if key not in ("warnings", "error") and (not snapshot.get("error") or value is not None)
                        }
                    )
                    result["snapshot_available"] = not snapshot.get("error")
                    result["snapshot_source"] = "yfinance" if result["snapshot_available"] else None
                    if result["snapshot_available"]:
                        result["report_date"] = snapshot.get("report_date")
                    if snapshot.get("warnings"):
                        result.setdefault("warnings", []).extend(snapshot["warnings"])
                    return result
                except Exception:
                    warning = "Lixinger HK unavailable (permissions/company type/provider); existing sources retained"
            else:
                warning = "LIXINGER_TOKEN not configured; extended HK financial history unavailable"
            result = financials_yf(args.symbol, periods, annual)
            result.setdefault("warnings", []).append(warning)
            result.setdefault("source", "yfinance/eastmoney")
            if len(result.get("periods", [])) < periods:
                result["warnings"].append("Requested HK financial history incomplete")
            return result
        return (
            financials_a(args.symbol, periods, annual) if market == "A" else financials_yf(args.symbol, periods, annual)
        )
    except Exception as e:
        return {"error": error_message(e)}


# --------------- market_snapshot ---------------

# The screener only scores stocks that can pass its filters, so every leg walks the market
# largest-cap first and stops at market_cap_min; the row cap bounds a loose (or zero) floor.
_SNAPSHOT_MAX_ROWS = 3000

# screener filter field → eastmoney xuangu (server-side A-share condition screener) column
_XUANGU_FILTER_COLUMNS = {
    "market_cap": "TOTAL_MARKET_CAP",
    "pe": "PE9",
    "pb": "PBNEWMRQ",
    "turnover_rate": "TURNOVERRATE",
    "change_pct": "CHANGE_RATE",
    "volume_ratio": "VOLUME_RATIO",
}
_XUANGU_COLUMNS = {
    "SECURITY_CODE": "symbol",
    "SECURITY_NAME_ABBR": "name",
    "NEW_PRICE": "price",
    "CHANGE_RATE": "change_pct",
    "VOLUME": "volume",
    "DEAL_AMOUNT": "turnover",
    "PE9": "pe",
    "PBNEWMRQ": "pb",
    "TOTAL_MARKET_CAP": "market_cap",
    "TURNOVERRATE": "turnover_rate",
    "VOLUME_RATIO": "volume_ratio",
}
_XUANGU_URL = "https://data.eastmoney.com/dataapi/xuangu/list"


def _xuangu_page(conditions: str, page: int, size: int) -> dict:
    params = {"st": "TOTAL_MARKET_CAP", "sr": -1, "ps": size, "p": page, "sty": ",".join(_XUANGU_COLUMNS)}
    body = _get_json(_XUANGU_URL, {**params, "filter": conditions, "source": "SELECT_SECURITIES", "client": "WEB"})
    if not isinstance(body.get("result"), dict):
        raise ValueError(f"eastmoney screener unavailable: {str(body.get('message'))[:100]}")
    return body["result"]


def _snapshot_xuangu(filters: dict):
    """A-share candidates filtered server-side (PE9 is a TTM PE), largest market cap first."""
    conditions = ""
    for key, value in sorted(filters.items()):
        field, _, bound = key.rpartition("_")
        if field in _XUANGU_FILTER_COLUMNS and bound in ("min", "max"):
            conditions += f"({_XUANGU_FILTER_COLUMNS[field]}{'>=' if bound == 'min' else '<='}{value})"
    rows, page = [], 1
    while len(rows) < _SNAPSHOT_MAX_ROWS:
        result = _xuangu_page(conditions, page, 1000)
        for item in result.get("data") or []:
            rows.append(_clean_row({key: item.get(col) for col, key in _XUANGU_COLUMNS.items()} | {"pe_basis": "ttm"}))
        if not result.get("nextpage"):
            break
        page += 1
    total = None
    with contextlib.suppress(Exception):
        total = _xuangu_page("", 1, 1).get("count")
    return total, rows


def _snapshot_tencent(filters: dict):
    """Non-eastmoney A-share leg (issue #25): Tencent's board rank sorted by market cap."""
    floor = filters.get("market_cap_min") or 0
    rows, total = [], None
    while len(rows) < _SNAPSHOT_MAX_ROWS:
        params = {"_appver": "11.17.0", "board_code": "aStock", "sort_type": "marketValue", "direct": "down"}
        body = _get_json(
            "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList",
            {**params, "offset": len(rows), "count": 200},
        )
        data = body.get("data") or {}
        total, ranked = data.get("total"), data.get("rank_list") or []
        for item in ranked:
            cap = _parse_float(item.get("zsz"))  # 亿元
            if cap is None or cap * 1e8 < floor:
                return total, rows
            turnover = _parse_float(item.get("turnover"))  # 万元
            row = {
                "symbol": str(item.get("code", ""))[2:],
                "name": item.get("name"),
                "price": _parse_float(item.get("zxj")),
                "change_pct": _parse_float(item.get("zdf")),
                "volume": _parse_float(item.get("volume")),
                "turnover": turnover * 1e4 if turnover is not None else None,
                "pe": _parse_float(item.get("pe_ttm")),
                "pe_basis": "ttm",
                "pb": _parse_float(item.get("pn")),
                "market_cap": cap * 1e8,
                "turnover_rate": _parse_float(item.get("hsl")),
                "volume_ratio": _parse_float(item.get("lb")),
            }
            rows.append(_clean_row(row))
        if len(ranked) < 200:
            break
    if not rows and not total:
        raise ValueError("tencent rank returned empty data")
    return total, rows


# HK/US keep their historical column set: adding turnover_rate/volume_ratio (or US pb) would
# silently start applying A-share-tuned default filters to these markets.
_CLIST_SNAPSHOT_COLUMNS = {
    "HK": {"f12": "symbol", "f14": "name", "f2": "price", "f3": "change_pct", "f5": "volume", "f6": "turnover"}
    | {"f9": "pe", "f115": "pe_ttm", "f23": "pb", "f20": "market_cap", "f133": "dividend_yield"},
    "US": {"f12": "symbol", "f14": "name", "f2": "price", "f3": "change_pct", "f5": "volume", "f6": "turnover"}
    | {"f9": "pe", "f20": "market_cap"},
}


def _snapshot_clist(market: str, floor: float):
    columns = _CLIST_SNAPSHOT_COLUMNS[market]
    rows, total, page = [], 0, 1
    while len(rows) < _SNAPSHOT_MAX_ROWS:
        total, ranked = _em_clist(market, "f20", ",".join(columns), page=page)
        if market == "HK" and page == 1 and not ranked:
            raise ValueError("Eastmoney HK equity list unavailable")
        for item in ranked:
            cap = _parse_float(item.get("f20"))
            if market == "HK" and (cap is None or not math.isfinite(cap)):
                continue
            if cap is None or cap < floor:
                return total, rows
            row = {key: _parse_float(item.get(col)) for col, key in columns.items()}
            row.update(symbol=_clist_symbol(market, item.get("f12")), name=item.get("f14"))
            pe_ttm = row.pop("pe_ttm", None)
            row["pe_basis"] = "unknown"
            if pe_ttm and math.isfinite(pe_ttm):
                row.update(pe=pe_ttm, pe_basis="ttm")
            rows.append(_clean_row(row))
        if len(ranked) < _EM_PAGE:
            break
        page += 1
    if market == "HK" and not rows:
        raise ValueError("Eastmoney HK market caps unavailable")
    return total, rows


# Non-eastmoney HK/US leg: TradingView's public scanner.
_TV_BASE = ["name", "description", "close", "change", "volume", "Value.Traded", "price_earnings_ttm"]
_TV_STOCK_TYPE = {"left": "type", "operation": "equal", "right": "stock"}
# Global primary listings omit some HKD counters, including HSBC and CCB.
_TV_HK_STOCK_FILTERS = [_TV_STOCK_TYPE, {"left": "currency", "operation": "equal", "right": "HKD"}]
_TV_SNAPSHOT_COLUMNS = {
    "HK": ("hongkong", [*_TV_BASE, "price_book_fq", "market_cap_basic", "dividends_yield_current"]),
    "US": ("america", [*_TV_BASE, "market_cap_basic"]),
}
_TV_KEYS = {"close": "price", "change": "change_pct", "Value.Traded": "turnover"}
_TV_KEYS |= {"price_earnings_ttm": "pe", "price_book_fq": "pb", "market_cap_basic": "market_cap"}
_TV_KEYS["dividends_yield_current"] = "dividend_yield"


def _tradingview_scan(region: str, body: dict) -> dict:
    import requests

    resp = requests.post(f"https://scanner.tradingview.com/{region}/scan", json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _snapshot_tradingview(market: str, floor: float):
    region, columns = _TV_SNAPSHOT_COLUMNS[market]
    filters = (
        _TV_HK_STOCK_FILTERS
        if market == "HK"
        else [_TV_STOCK_TYPE, {"left": "is_primary", "operation": "equal", "right": True}]
    )
    rows, total = [], None
    for offset in range(0, _SNAPSHOT_MAX_ROWS, 1000):
        body = {"columns": columns, "filter": filters, "range": [offset, offset + 1000]}
        body["sort"] = {"sortBy": "market_cap_basic", "sortOrder": "desc"}
        payload = _tradingview_scan(region, body)
        total, data = payload.get("totalCount"), payload.get("data") or []
        for item in data:
            values = dict(zip(columns, item.get("d") or []))
            cap = _parse_float(values.get("market_cap_basic"))
            if market == "HK" and (cap is None or not math.isfinite(cap)):
                continue
            if cap is None or cap < floor:
                return total, rows
            row = {_TV_KEYS.get(col, col): _parse_float(values.get(col)) for col in columns[2:]}
            row.update(symbol=_clist_symbol(market, values.get("name")), name=values.get("description"))
            rows.append(_clean_row(row | {"pe_basis": "ttm"}))
        if len(data) < 1000:
            break
    if not rows:
        raise ValueError("tradingview scanner returned empty data")
    return total, rows


def _hkex_equity_symbols() -> list[str]:
    from io import BytesIO

    import pandas as pd
    import requests

    url = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    columns = ["Stock Code", "Category", "Trading Currency"]
    sheet = pd.read_excel(BytesIO(response.content), header=2, usecols=columns, dtype=str)
    codes = sheet.loc[(sheet["Category"] == "Equity") & (sheet["Trading Currency"] == "HKD"), "Stock Code"]
    if codes.empty or not codes.str.fullmatch(r"[0-9]{1,5}", na=False).all():
        raise ValueError("HKEX returned no usable equity codes")
    return sorted({f"{code.zfill(5)}.HK" for code in codes})


def _snapshot_hk_tencent(floor: float):
    # The board ranks have different coverage. Use the exchange's main board and GEM listing file.
    symbols = cached("hkex-hkd-equity-symbols", DAY, _hkex_equity_symbols)
    quotes = _hk_tencent_fields(symbols)
    if not quotes:
        raise ValueError("Tencent returned no HK equity quotes")
    rows, has_cap = [], False
    for symbol, fields in quotes.items():
        fields += [""] * (59 - len(fields))
        cap = _parse_float(fields[45])
        if cap is None or not math.isfinite(cap):
            continue
        has_cap = True
        if cap * 1e8 < floor:
            continue
        row = _tencent_payload_row(fields, symbol)
        keys = ("symbol", "name", "price", "change_pct", "volume", "turnover", "market_cap")
        rows.append(
            _clean_row(
                {key: row[key] for key in keys}
                | {
                    "pe": _parse_float(fields[57]),
                    "pb": _parse_float(fields[58]),
                    "pe_basis": "ttm",
                    "dividend_yield": _parse_float(fields[47]),
                }
            )
        )
    if not has_cap:
        raise ValueError("Tencent HK market caps unavailable")
    return len(symbols), sorted(rows, key=lambda row: row["market_cap"], reverse=True)


def _hk_snapshot_with_valuations(source, fetch, *args):
    total, rows = fetch(*args)
    if not total or (rows and any(all(row.get(key) is None for row in rows) for key in ("pe", "pb"))):
        raise ValueError("HK equity snapshot or valuation columns unavailable")
    return total, rows, source


def normalize_us_symbol(value) -> str:
    symbol = str(value).strip().upper()
    symbol = re.sub(r"^(?:105|106|107)[.]", "", symbol)
    symbol = re.sub(r"[.](?:OQ|N|A)$", "", symbol)
    return re.sub(r"[._]", "-", symbol)  # Eastmoney writes class shares as BRK_B; yfinance wants BRK-B


def cmd_market_snapshot(args):
    """Screener input: the stocks at or above market_cap_min (largest first), never the whole market."""
    market = args.market.upper()
    try:
        filters = json.loads(getattr(args, "filters", None) or "{}")
        if not isinstance(filters, dict):
            return {"error": "filters must be a JSON object"}
        floor = filters.get("market_cap_min") or 0
        if market == "A":
            legs = [("eastmoney", lambda: _snapshot_xuangu(filters)), ("tencent", lambda: _snapshot_tencent(filters))]
        elif market == "HK":
            legs = [
                ("eastmoney", lambda: _hk_snapshot_with_valuations("Eastmoney", _snapshot_clist, market, floor)),
                (
                    "tradingview",
                    lambda: _hk_snapshot_with_valuations("TradingView", _snapshot_tradingview, market, floor),
                ),
                ("tencent", lambda: _hk_snapshot_with_valuations("Tencent", _snapshot_hk_tencent, floor)),
            ]
        elif market in _CLIST_SNAPSHOT_COLUMNS:
            legs = [
                ("eastmoney", lambda: _snapshot_clist(market, floor)),
                ("tradingview", lambda: _snapshot_tradingview(market, floor)),
            ]
        else:
            return {"error": f"Unknown market: {market}"}
        with socket_timeout(25):
            total, rows, *source = _failover(legs, label=f"snapshot_{market.lower()}")
        result = {"market": market, "total_stocks": total, "rows": rows[:_SNAPSHOT_MAX_ROWS]}
        if source:
            result["source"] = source[0]
        if len(rows) >= _SNAPSHOT_MAX_ROWS:
            result["truncated"] = True
        return result
    except Exception as e:
        return {"error": f"{market} snapshot unavailable: {error_message(e)}"}


# --------------- market_indices ---------------

# region -> {index symbol -> display name}; CN is handled separately via akshare.
_REGION_INDEX_SYMBOLS = {
    "hk": {"^HSI": "恒生指数", "^HSCE": "恒生国企指数", "^HSTECH": "恒生科技指数"},
    "us": {"^DJI": "道琼斯", "^IXIC": "纳斯达克", "^GSPC": "标普500", "^RUT": "罗素2000"},
    "jp": {"^N225": "日经225", "^TOPX": "东证指数"},
    "kr": {"^KS11": "韩国KOSPI", "^KQ11": "韩国KOSDAQ"},
    "tw": {"^TWII": "台湾加权指数"},
}


def _quote_tencent_hk_index(symbol: str) -> dict:
    """Tencent r_hk index quote, using the same `~` field positions as _tencent_payload_row.
    Volume is omitted: the index feed's volume field has no confirmed unit."""
    f = _tencent_fields(f"r_{_TENCENT_HK_INDEX[symbol]}")
    price = _parse_float(f[3]) if len(f) > 34 else None
    if price is None or price <= 0:
        raise ValueError(f"tencent returned no quote for {symbol}")
    return {
        "price": price,
        "change": _parse_float(f[31]),
        "change_pct": _parse_float(f[32]),
        "high": _parse_float(f[33]),
        "low": _parse_float(f[34]),
        "open": _parse_float(f[5]),
        "prev_close": _parse_float(f[4]),
        "source": "tencent",
        "data_as_of": _quote_time(f[30], "%Y/%m/%d %H:%M:%S"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


_CN_INDICES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("sh000300", "沪深300"),
    ("sh000016", "上证50"),
]


_EM_INDEX_FIELDS = (
    ("price", "f2"),
    ("change", "f4"),
    ("change_pct", "f3"),
    ("volume", "f5"),
    ("turnover", "f6"),
    ("high", "f15"),
    ("low", "f16"),
    ("open", "f17"),
    ("prev_close", "f18"),
)


def _cn_indices_eastmoney() -> dict:
    secids = {f"{1 if code[:2] == 'sh' else 0}.{code[2:]}": code for code, _ in _CN_INDICES}
    quotes = {}
    for row in _em_ulist(list(secids), "f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18"):
        code = secids.get(f"{row.get('f13')}.{row.get('f12')}")
        if code and _parse_float(row.get("f2")) is not None:
            quotes[code] = {"name": row.get("f14")} | {key: _parse_float(row.get(col)) for key, col in _EM_INDEX_FIELDS}
    return quotes


def _cn_indices_tencent() -> dict:
    """One qt.gtimg.cn batch request for every CN index (eastmoney refuses some datacenter IPs)."""
    quotes = {}
    for code, fields in _tencent_batch_fields([code for code, _ in _CN_INDICES]).items():
        if _parse_float(fields[3]):
            row = _tencent_payload_row(fields, code)
            keys = ("name", "price", "change", "change_pct", "volume", "turnover", "high", "low", "open", "prev_close")
            quotes[code] = {key: row[key] for key in keys}
    return quotes


def cmd_market_indices(args):
    region = args.region.lower()
    try:
        if region == "cn":
            with socket_timeout(25):
                quotes = _failover(
                    [("eastmoney", _cn_indices_eastmoney), ("tencent", _cn_indices_tencent)], label="market_indices_cn"
                )
            return [
                _clean_row({"code": code, **quotes[code]})
                if code in quotes
                else {"code": code, "name": name, "note": "data unavailable"}
                for code, name in _CN_INDICES
            ]
        elif region in _REGION_INDEX_SYMBOLS:
            from concurrent.futures import ThreadPoolExecutor

            import yfinance as yf

            symbols = _REGION_INDEX_SYMBOLS[region]

            def fetch_index(item):
                sym, name = item
                try:
                    if sym in _TENCENT_HK_INDEX:
                        return _clean_row({"code": sym, "name": name, **_quote_tencent_hk_index(sym)})
                    info = yf.Ticker(sym).info
                    return _clean_row(
                        {
                            "code": sym,
                            "name": name,
                            "price": info.get("regularMarketPrice"),
                            "change": info.get("regularMarketChange"),
                            "change_pct": info.get("regularMarketChangePercent"),
                            "volume": info.get("regularMarketVolume"),
                            "high": info.get("regularMarketDayHigh"),
                            "low": info.get("regularMarketDayLow"),
                            "open": info.get("regularMarketOpen"),
                            "prev_close": info.get("regularMarketPreviousClose"),
                            "source": "yfinance",
                            "data_as_of": _unix_quote_time(info.get("regularMarketTime")),
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                except Exception:
                    return {"code": sym, "name": name, "note": "data unavailable"}

            # Preserve provider order while overlapping independent quote requests.
            with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as pool:
                return list(pool.map(fetch_index, symbols.items()))
        return {"error": f"Unknown region: {region}"}
    except Exception as e:
        return {"error": error_message(e)}


# --------------- sector_rankings ---------------


def _sector_rankings_efinance() -> list:
    """Fallback: fetch industry board rankings via efinance (raw rows; sorting and
    top/direction are applied uniformly by cmd_sector_rankings after the failover)."""
    import efinance as ef
    import pandas as pd

    df = ef.stock.get_realtime_quotes(fs=ef.stock.get_belong_board("行业板块"))
    if df is None or df.empty:
        raise ValueError("efinance sector data unavailable")
    col_map = {
        "股票名称": "name",
        "股票代码": "code",
        "涨跌幅": "change_pct",
        "成交量": "volume",
        "成交额": "turnover",
    }
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    df = df[keep]
    if "change_pct" in df.columns:
        df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce")
    return [_clean_row(r) for r in df.to_dict("records")]


def _sourced(name: str, fn):
    """Failover leg wrapper: tag each returned row with the provider name, so the
    caller can tell which source won (_failover returns only the result)."""

    def run():
        rows = fn()
        for r in rows:
            r["source"] = name
        return rows

    return name, run


def _sector_rankings_em(board_type: str) -> list:
    """Eastmoney board rankings (industry or concept) via akshare."""
    import akshare as ak
    import pandas as pd

    fetch = ak.stock_board_industry_name_em if board_type == "industry" else ak.stock_board_concept_name_em
    df = _akshare_retry(fetch)
    if df is None or df.empty:
        raise ValueError("eastmoney sector data unavailable")
    col_map = {
        "板块名称": "name",
        "板块代码": "code",
        "最新价": "price",
        "涨跌幅": "change_pct",
        "成交量": "volume",
        "成交额": "turnover",
        "换手率": "turnover_rate",
        "总市值": "market_cap",
        "上涨家数": "up_count",
        "下跌家数": "down_count",
        "领涨股票": "leading_stock",
        # industry boards name the column 领涨涨跌幅, concept boards 领涨股票-涨跌幅
        "领涨涨跌幅": "leading_change_pct",
        "领涨股票-涨跌幅": "leading_change_pct",
    }
    df = df.rename(columns=col_map)
    keep = list(dict.fromkeys(c for c in col_map.values() if c in df.columns))
    df = df[keep]
    if "change_pct" in df.columns:
        df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce")
    return [_clean_row(r) for r in df.to_dict("records")]


def _sector_rankings_ths() -> list:
    """THS industry board rankings via akshare stock_board_industry_summary_ths.

    There is no THS *concept* rankings table in this akshare version:
    stock_board_concept_name_ths returns only name/code columns and
    stock_board_concept_summary_ths is a concept timeline (日期/驱动事件), so the
    concept chain falls back to sina instead."""
    import akshare as ak

    df = _akshare_retry(ak.stock_board_industry_summary_ths)
    if df is None or df.empty:
        raise ValueError("ths sector data unavailable")
    col_map = {
        "板块": "name",
        "涨跌幅": "change_pct",
        "总成交量": "volume",
        "总成交额": "turnover",
        "净流入": "net_inflow",
        "上涨家数": "up_count",
        "下跌家数": "down_count",
        "领涨股": "leading_stock",
        "领涨股-最新价": "leading_price",
        "领涨股-涨跌幅": "leading_change_pct",
    }
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    return [_clean_row(r) for r in df[keep].to_dict("records")]


def _sector_rankings_sina(indicator: str) -> list:
    """Sina board rankings via akshare stock_sector_spot (概念/行业/...).

    Sina has no 上涨家数/下跌家数 — only 公司家数 (stock_count); the leading stock
    rides along in the same row (股票名称/个股-涨跌幅/个股-当前价)."""
    import akshare as ak

    df = _akshare_retry(ak.stock_sector_spot, indicator=indicator)
    if df is None or df.empty:
        raise ValueError(f"sina sector data unavailable (indicator={indicator})")
    col_map = {
        "板块": "name",
        "公司家数": "stock_count",
        "涨跌额": "change",
        "涨跌幅": "change_pct",
        "总成交量": "volume",
        "总成交额": "turnover",
        "股票名称": "leading_stock",
        "个股-涨跌幅": "leading_change_pct",
        "个股-当前价": "leading_price",
    }
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    return [_clean_row(r) for r in df[keep].to_dict("records")]


US_SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}


def _sector_rankings_us() -> list:
    """S&P 500 sector proxies from the SPDR Select Sector ETFs' last two daily closes."""
    import yfinance as yf

    closes = yf.download(list(US_SECTOR_ETFS), period="10d", auto_adjust=False, progress=False)["Close"]
    rows = []
    for symbol, name in US_SECTOR_ETFS.items():
        series = closes[symbol].dropna() if symbol in closes else []
        if len(series) < 2:
            continue
        rows.append(
            {
                "name": name,
                "etf": symbol,
                "close": round(float(series.iloc[-1]), 4),
                "change_pct": round(float(series.iloc[-1] / series.iloc[-2] - 1) * 100, 4),
                "as_of": str(series.index[-1])[:10],
            }
        )
    if not rows:
        raise ValueError("US sector ETF quotes unavailable")
    return rows


def cmd_sector_rankings(args):
    board_type = getattr(args, "board_type", "industry")
    direction = getattr(args, "direction", "top")
    market = getattr(args, "market", "A")
    if market == "US" and board_type != "industry":
        return {"error": "US sector rankings support board_type=industry only"}
    if market == "US":
        sources = [_sourced("yfinance", _sector_rankings_us)]
    else:
        sources = [_sourced("eastmoney", lambda: _sector_rankings_em(board_type))]
        if board_type == "industry":
            # efinance has no concept boards — it stays an industry-only leg
            sources += [_sourced("ths", _sector_rankings_ths), _sourced("efinance", _sector_rankings_efinance)]
        else:
            sources.append(_sourced("sina", lambda: _sector_rankings_sina("概念")))
    try:
        rows = _failover(
            sources, label="sector_rankings_us:industry" if market == "US" else f"sector_rankings:{board_type}"
        )
        if not rows:
            raise ValueError(f"no {board_type} board rankings data")
    except Exception as e:
        return {"error": error_message(e)}
    for r in rows:
        r["board_type"] = board_type
        if market == "US":
            r.update(market="US", proxy="SPDR Select Sector ETF")

    def _pct(row, default):
        v = row.get("change_pct")
        return v if isinstance(v, (int, float)) else default

    if direction == "bottom":
        return sorted(rows, key=lambda r: _pct(r, float("inf")))[: args.top]
    if direction == "both":
        return {
            "top": sorted(rows, key=lambda r: _pct(r, float("-inf")), reverse=True)[: args.top],
            "bottom": sorted(rows, key=lambda r: _pct(r, float("inf")))[: args.top],
        }
    return sorted(rows, key=lambda r: _pct(r, float("-inf")), reverse=True)[: args.top]


# --------------- sector constituents / stock sectors (issue #18) ---------------

# Small routing state (24h TTL) beside the user-private slow-data cache: sticky failover
# winners (`sticky-`, see _failover) and per-leg outage markers (`resolve-down-`).
_DATA_CACHE_DIR = CACHE_DIR / "state"
_DATA_CACHE_TTL = 24 * 3600


def _disk_cache_get(key: str):
    try:
        path = _DATA_CACHE_DIR / f"{key}.json"
        if path.exists() and time.time() - path.stat().st_mtime < _DATA_CACHE_TTL:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _disk_cache_set(key: str, data):
    try:
        with atomic_write(_DATA_CACHE_DIR / f"{key}.json") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
    except Exception:
        pass


def _fuzzy_match_sector(query: str, candidates: list):
    """Resolve a possibly-partial sector name. Priority: exact match > the unique
    candidate containing the query. The query is normalized first (whitespace
    stripped, trailing 板块/行业/概念 suffix dropped) so 情报 phrasing like
    「创新药板块」 hits the board 创新药. The reverse direction (candidate inside
    the query) is deliberately not tried — it would absorb inputs like 创新药ETF
    into the 创新药 board. Ambiguous or no match returns None."""
    if query in candidates:
        return query
    q = re.sub(r"\s+", "", query)
    for suffix in ("板块", "行业", "概念"):
        if q.endswith(suffix):
            q = q[: -len(suffix)]
    if not q:
        return None
    if q in candidates:
        return q
    matches = [c for c in candidates if q in c]
    return matches[0] if len(matches) == 1 else None


def _constituent_rows(df) -> list:
    col_map = {"代码": "code", "名称": "name"}
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    return [_clean_row(r) for r in df[keep].to_dict("records")]


def _constituents_em(sector: str, board_kind: str) -> dict:
    """Eastmoney industry/concept board constituents via akshare, fuzzy-resolved."""
    import akshare as ak

    cons_fn = ak.stock_board_industry_cons_em if board_kind == "industry" else ak.stock_board_concept_cons_em
    kind = "行业" if board_kind == "industry" else "概念"
    # Search the board by name (one request) instead of paging the whole board list (~5s per kind).
    keyword = re.sub(r"(板块|行业|概念)$", "", re.sub(r"\s+", "", sector)) or sector
    boards = {
        str(r.get("shortName")): str(r.get("code"))
        for r in _akshare_retry(_em_codetable, keyword, 50)
        if r.get("market") == 90 and r.get("securityTypeName") == kind and str(r.get("code", "")).startswith("BK")
    }
    exact = _fuzzy_match_sector(sector, list(boards))
    if not exact:
        raise ValueError(f"no eastmoney {board_kind} board matching '{sector}'")
    # A BK code also skips akshare's own name→code lookup, which pages the whole board list again.
    df = _akshare_retry(cons_fn, symbol=boards[exact])
    if df is None or df.empty:
        raise ValueError(f"eastmoney {board_kind} board '{exact}' returned no constituents")
    constituents = _constituent_rows(df)
    return {
        "sector": exact,
        "board_type": board_kind,
        "source": "eastmoney",
        "constituents": constituents,
        "count": len(constituents),
    }


# Sina (indicator, board_type) pairs to search per board_type, tried in order — the
# first fuzzy hit wins. 概念 carries concept boards (e.g. 白酒概念) and 地域 regional
# ones, so a sina fallback restricted to 新浪行业 missed them entirely (issue #18 smoke test).
_SINA_SECTOR_INDICATORS = {
    "industry": [("新浪行业", "industry"), ("行业", "industry")],
    "concept": [("概念", "concept")],
    "auto": [("新浪行业", "industry"), ("行业", "industry"), ("概念", "concept"), ("地域", "region")],
}


def _constituents_sina_indicator(sector: str, indicator: str, board_type: str) -> dict:
    """One sina indicator query: fuzzy-match the sector in the spot list, then fetch
    constituents. Raises on any failure so _failover moves to the next indicator."""
    import akshare as ak

    spot = _akshare_retry(ak.stock_sector_spot, indicator=indicator)
    if spot is None or spot.empty or "label" not in spot.columns:
        raise ValueError(f"sina sector list unavailable (indicator={indicator})")
    name_col = "板块" if "板块" in spot.columns else "label"
    exact = _fuzzy_match_sector(sector, [str(n) for n in spot[name_col].tolist()])
    if not exact:
        raise ValueError(f"no sina board matching '{sector}' (indicator={indicator})")
    label = str(spot.loc[spot[name_col] == exact, "label"].iloc[0])
    df = _akshare_retry(ak.stock_sector_detail, sector=label)
    if df is None or df.empty:
        raise ValueError(f"sina board '{exact}' returned no constituents")
    constituents = _constituent_rows(df)
    return {
        "sector": exact,
        "board_type": board_type,
        "source": "sina",
        "constituents": constituents,
        "count": len(constituents),
    }


def _constituents_sina(sector: str, board_type: str = "auto") -> dict:
    """Sina board constituents as fallback, fuzzy-resolved via the spot list(s)
    selected by board_type; the first indicator with a fuzzy hit wins."""
    indicators = _SINA_SECTOR_INDICATORS.get(board_type, _SINA_SECTOR_INDICATORS["auto"])
    sources = [(ind, lambda ind=ind, bt=bt: _constituents_sina_indicator(sector, ind, bt)) for ind, bt in indicators]
    return _failover(sources, label=f"sina:{sector}")


def sector_constituents_a(sector: str, board_type: str = "auto") -> dict:
    """A-share sector → constituents with failover: eastmoney industry/concept → sina."""
    sources = []
    if board_type in ("industry", "auto"):
        sources.append(("eastmoney_industry", lambda: _constituents_em(sector, "industry")))
    if board_type in ("concept", "auto"):
        sources.append(("eastmoney_concept", lambda: _constituents_em(sector, "concept")))
    sources.append(("sina", lambda: _constituents_sina(sector, board_type)))
    return _failover(sources, label=f"sector_constituents:{sector}")


def cmd_sector_constituents(args):
    return cached(
        f"sector-constituents:{args.sector.strip()}:{args.board_type}", WEEK, lambda: _sector_constituents(args)
    )


def _sector_constituents(args):
    # argparse 保证属性存在；空串仍可传入
    sector = args.sector.strip()
    if not sector:
        return {"error": "sector name required"}
    try:
        return sector_constituents_a(sector, args.board_type)
    except Exception as e:
        return {"error": error_message(e)}


def _board_entry(name, source: str, board_type: str) -> dict:
    """One stock→board membership record; keeps the {name, source, board_type}
    shape in one place instead of hand-building it per source."""
    return {"name": str(name), "source": source, "board_type": board_type}


class _DataMiss(ValueError):
    """Per-symbol data miss: the provider answered but doesn't cover this code
    (empty DataFrame / no industry field for an ETF/BSE/uncovered symbol). Never an
    outage — resolve_stock_sectors' negative cache must not mark the leg down."""


def _require_industry(value, source: str) -> str:
    """Unwrap a provider's industry field into a real name. pandas NaN is truthy —
    only a non-empty string counts; a missing one is a _DataMiss, not an outage."""
    if not (isinstance(value, str) and value):
        raise _DataMiss(f"{source} has no industry")
    return value


def _stock_boards_em(symbol: str, info_map=None) -> list:
    """A-share industry board from eastmoney individual info. Pass an already-fetched
    info_map (item → value) to reuse it instead of hitting the network again."""
    if info_map is None:
        import akshare as ak

        df = _akshare_retry(ak.stock_individual_info_em, symbol=symbol)
        if df is None or df.empty:
            raise _DataMiss("eastmoney individual info unavailable")
        info_map = {row.iloc[0]: row.iloc[1] for _, row in df.iterrows()}
    industry = _require_industry(info_map.get("行业"), "eastmoney individual info")
    return [_board_entry(industry, "eastmoney", "industry")]


def _stock_boards_efinance(symbol: str) -> list:
    """A-share concept/membership boards from efinance."""
    import efinance as ef

    df = ef.stock.get_belong_board(symbol)
    if df is None or df.empty or "板块名称" not in df.columns:
        raise _DataMiss("efinance belong-board unavailable")
    # pandas NaN is truthy and str(nan) == "nan" — drop non-string names or they
    # would pollute the dedup in resolve_stock_sectors as a bogus "nan" board
    return [
        _board_entry(name, "efinance", "concept") for name in df["板块名称"].tolist() if isinstance(name, str) and name
    ]


def _xq_symbol(symbol: str) -> str:
    """Xueqiu codes are exchange-prefixed: 600519 → SH600519, 000858 → SZ000858."""
    if symbol.startswith(("4", "8", "92")):
        return f"BJ{symbol}"
    return f"SH{symbol}" if symbol.startswith(("5", "6", "9")) else f"SZ{symbol}"


def _stock_boards_xueqiu(symbol: str) -> list:
    """A-share industry board from the Xueqiu F10 company profile — the non-eastmoney
    fallback. Returns [] (source skipped) unless XUEQIU_TOKEN is set: akshare's
    built-in xq_a_token is stale (xueqiu answers 400016), so a user token is required."""
    import os

    token = os.environ.get("XUEQIU_TOKEN", "")
    if not token:
        return []
    import akshare as ak

    df = _akshare_retry(ak.stock_individual_basic_info_xq, symbol=_xq_symbol(symbol), token=token)
    if df is None or df.empty:
        raise _DataMiss("xueqiu basic info unavailable")
    info_map = {row.iloc[0]: row.iloc[1] for _, row in df.iterrows()}
    # affiliate_industry is a nested dict: {'ind_code': 'BK0025', 'ind_name': '汽车整车'}
    industry = info_map.get("affiliate_industry")
    name = _require_industry(industry.get("ind_name") if isinstance(industry, dict) else None, "xueqiu basic info")
    return [_board_entry(name, "xueqiu", "industry")]


def _stock_boards_cninfo(symbol: str) -> list:
    """A-share industry from cninfo (官方披露站) via akshare stock_profile_cninfo —
    the token-free, non-eastmoney active leg (issue #35). Runs only as the
    last-resort fallback: its industry naming follows the CSRC classification
    (酒、饮料和精制茶制造业), not eastmoney board names (酿酒行业), so its entries
    don't resolve against eastmoney/sina board lists downstream."""
    import akshare as ak

    bare = symbol[2:] if _A_PREFIXED_RE.fullmatch(symbol) else symbol
    df = _akshare_retry(ak.stock_profile_cninfo, symbol=bare)
    if df is None or df.empty or "所属行业" not in df.columns:
        raise _DataMiss("cninfo profile unavailable")
    industry = _require_industry(df.iloc[0].get("所属行业"), "cninfo profile")
    return [_board_entry(industry, "cninfo", "industry")]


def _stock_boards_from_cache(symbol: str) -> list:
    """Reverse lookup against the get_sector_constituents disk cache — the honest
    fallback when eastmoney blocks the caller's IP and xueqiu has no token (the two
    live A-share sources are both eastmoney-flavored). Reads the week-lived get_sector_constituents
    entries and returns one entry per cached sector containing the symbol. Cold cache → [];
    expired or malformed entries are skipped."""
    boards = []
    for data in cached_values("sector-constituents", WEEK):
        if not isinstance(data, dict):
            continue
        sector = data.get("sector")
        if not sector:
            continue
        for row in data.get("constituents") or []:
            if isinstance(row, dict) and str(row.get("code")) == symbol:
                board = _board_entry(sector, "cache", data.get("board_type", ""))
                if board not in boards:
                    boards.append(board)
                break
    return boards


def _yf_hk_symbol(symbol: str) -> str:
    """Yahoo HK tickers are zero-padded to exactly 4 digits: 01801.HK → 1801.HK.
    A bare lstrip('0') would turn 0700.HK into the bogus 700.HK, so pad back."""
    code, sep, suffix = symbol.upper().partition(".HK")
    if not sep or not code.isdigit():
        return symbol
    return f"{code.lstrip('0').zfill(4)}{sep}{suffix}"


def _stock_sectors_hk(symbol: str) -> list:
    """HK stock GICS sector/industry from yfinance (English names). Retries ride on
    _akshare_retry — despite the name it is a generic fn(*args, retries=2, delay=1) wrapper."""
    import yfinance as yf

    # 5-digit inputs like 01801.HK 404 on Yahoo — normalize; the caller keeps the
    # user's original symbol for the result payload.
    yf_symbol = _yf_hk_symbol(symbol)
    info = _akshare_retry(lambda: yf.Ticker(yf_symbol).info) or {}
    sectors = [_board_entry(info[key], "yfinance", "gics") for key in ("sector", "industry") if info.get(key)]
    if not sectors:
        raise ValueError(f"no sector info for {symbol}")
    return sectors


# A failed A-share resolve leg is skipped for this long on subsequent calls
# (issue #35): the motivating failure mode — regional blocking of eastmoney — is
# chronic, while a 1h TTL keeps a transient blip from degrading the merged result
# for the whole day. An expired marker costs one retried attempt (self-healing).
_RESOLVE_DOWN_TTL = 3600


def _resolve_leg_down(name: str) -> bool:
    """True while leg `name`'s failure marker is fresh. The marker stores a plain
    timestamp — the effective TTL above is shorter than the 24h file lifetime that
    _disk_cache_get enforces on the private state cache dir."""
    ts = _disk_cache_get(f"resolve-down-{name}")
    return isinstance(ts, (int, float)) and time.time() - ts < _RESOLVE_DOWN_TTL


def resolve_stock_sectors(symbol: str, info_map=None) -> dict:
    # info_map only saves the em leg's fetch on a miss; the result is the same, so share the cache.
    return cached(f"stock-sectors:{symbol}", WEEK, lambda: _resolve_stock_sectors(symbol, info_map))


def _resolve_stock_sectors(symbol: str, info_map=None) -> dict:
    """Reverse map: stock → boards it belongs to (A/HK). Sources are merged (a single
    source failing is not fatal — the others' boards are still returned) rather than
    first-hit failover: each source covers a different flavor (industry vs concept).
    An already-fetched eastmoney info_map is reused for the em source instead of
    re-fetching; that leg then needs no network and bypasses the failure cache.

    A-share legs are socket-timeout-bounded and negative-cached per leg (issue #35):
    on an eastmoney-blackholed host the first call pays each dead leg's bounded
    timeout once, later calls skip them until the marker expires. The cninfo leg
    runs only when every other active leg came up empty — its CSRC industry names
    don't resolve against eastmoney/sina board lists, so it is a fallback, not an
    always-on flavor."""
    market = detect_market(symbol)
    result = {"symbol": symbol, "market": market}
    if market == "A":
        sources = [
            ("eastmoney", lambda: _stock_boards_em(symbol, info_map)),
            ("efinance", lambda: _stock_boards_efinance(symbol)),
            ("xueqiu", lambda: _stock_boards_xueqiu(symbol)),
            ("cache", lambda: _stock_boards_from_cache(symbol)),
        ]
    elif market == "HK":
        sources = [("yfinance", lambda: _stock_sectors_hk(symbol))]
    else:
        result["error"] = f"resolve_stock_sectors only supports A-share and HK stocks, got market {market}"
        return result
    sectors, errors = [], []

    def run_legs(legs, negative_cache: bool):
        for name, fn in legs:
            # em with an info_map in hand is a pure dict lookup — never skip it, and
            # a lookup miss must not mark a network leg down
            offline_em = name == "eastmoney" and info_map is not None
            if negative_cache and not offline_em and _resolve_leg_down(name):
                errors.append(f"{name}: skipped (failed within {_RESOLVE_DOWN_TTL}s)")
                continue
            try:
                sectors.extend(fn() or [])
                if negative_cache and not offline_em:
                    _disk_cache_set(f"resolve-down-{name}", 0)  # success clears the marker
            except _DataMiss as e:
                # per-symbol miss (provider answered but doesn't cover this code) —
                # not an outage: never poison the leg for every other symbol
                errors.append(f"{name}: {type(e).__name__}: {scrub_secrets(str(e))}")
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}: {scrub_secrets(str(e))}")
                if negative_cache and not offline_em:
                    _disk_cache_set(f"resolve-down-{name}", time.time())

    if market == "A":
        # akshare/efinance leave requests timeout-less — on a blackholed network a leg
        # hangs until the kernel TCP timeout; the chain-wide socket bound
        # (socket_timeout) gives every leg its turn within the caller's budget.
        with socket_timeout(25):
            run_legs(sources, negative_cache=True)
            # cninfo last: the token-free non-eastmoney active fallback (issue #35),
            # tried only when everything above yielded nothing
            if not sectors:
                run_legs([("cninfo", lambda: _stock_boards_cninfo(symbol))], negative_cache=True)
    else:
        run_legs(sources, negative_cache=False)
    seen = set()
    deduped = []
    for s in sectors:
        if s["name"] not in seen:
            seen.add(s["name"])
            deduped.append(s)
    if not deduped:
        error = "; ".join(errors) or "no sector data"
        if market == "A":
            # em/efinance are both eastmoney-flavored and xueqiu is usually untokened,
            # so an all-empty A-share result almost always means eastmoney blocked us.
            error += "; 东财/cninfo 均不可用且缓存为空，可先跑 get_sector_constituents 预热或设置 XUEQIU_TOKEN"
        result["error"] = error
        return result
    result["sectors"] = deduped
    if errors:
        result["warnings"] = errors
    return result


# --------------- stock_info ---------------


def cmd_stock_info(args):
    market = detect_market(args.symbol)
    try:
        if market == "A":
            import akshare as ak

            result = {"symbol": args.symbol, "market": "A"}
            is_etf = normalize_stock_code(args.symbol)["is_etf"]
            if is_etf:
                result["is_etf"] = True
            info_map = None
            try:
                df = _cached_frame(
                    f"a-basic-info:{args.symbol}",
                    lambda: _akshare_retry(ak.stock_individual_info_em, symbol=args.symbol),
                )
                if df is not None and not df.empty:
                    info_map = {}
                    for _, row in df.iterrows():
                        info_map[row.iloc[0]] = row.iloc[1]
                    result["name"] = info_map.get("股票简称")
                    result["industry"] = info_map.get("行业")
                    result["listing_date"] = str(info_map.get("上市时间", ""))
                    result["total_shares"] = info_map.get("总股本")
                    result["float_shares"] = info_map.get("流通股")
                    result["total_market_cap"] = info_map.get("总市值")
                    result["float_market_cap"] = info_map.get("流通市值")
                else:
                    result.setdefault("warnings", []).append("basic information unavailable")
            except Exception:
                result.setdefault("warnings", []).append("basic information unavailable")
            try:
                # boards 复用 resolve_stock_sectors；info_map 透传避免东财二次拉取
                resolved = resolve_stock_sectors(args.symbol, info_map=info_map)
                boards = [s["name"] for s in resolved.get("sectors", [])]
                if boards:
                    result["boards"] = boards[:10]
                if resolved.get("error") or resolved.get("warnings"):
                    result.setdefault("warnings", []).append("sector mapping incomplete")
            except Exception:
                result.setdefault("warnings", []).append("sector mapping unavailable")
            if is_etf:
                try:
                    # ETF 最新净值（天天基金-场内基金历史净值明细）。窄日期窗口 → 单页返回
                    # akshare 1.20.41 改名为 fund_exchange_nav_hist_em；py3.10 只能装到 <1.20.40，仍是旧名
                    end = _cn_now()
                    start = end - timedelta(days=30)
                    nav_df = _cached_frame(
                        f"etf-basic-nav:{args.symbol}",
                        lambda: _akshare_retry(
                            getattr(ak, "fund_exchange_nav_hist_em", None) or ak.fund_etf_fund_info_em,
                            fund=args.symbol,
                            start_date=start.strftime("%Y%m%d"),
                            end_date=end.strftime("%Y%m%d"),
                        ),
                    )
                    if nav_df is not None and not nav_df.empty:
                        latest = nav_df.iloc[-1]
                        result["nav"] = latest.get("单位净值")
                        result["accumulated_nav"] = latest.get("累计净值")
                        nav_date = latest.get("净值日期")
                        if nav_date is not None:
                            result["nav_date"] = str(nav_date)[:10]  # datetime.date/Timestamp → ISO date
                    else:
                        result.setdefault("warnings", []).append("ETF NAV unavailable")
                except Exception:
                    result.setdefault("warnings", []).append("ETF NAV unavailable")
            return _clean_row(result)
        else:
            import yfinance as yf

            t = yf.Ticker(_yf_hk_symbol(args.symbol))
            info = _yf_info(args.symbol, t)
            return _clean_row(
                {
                    "symbol": args.symbol,
                    "market": market,
                    "name": info.get("shortName"),
                    "industry": info.get("industry"),
                    "sector": info.get("sector"),
                    "country": info.get("country"),
                    "exchange": info.get("exchange"),
                    "market_cap": info.get("marketCap"),
                    "employees": info.get("fullTimeEmployees"),
                    "website": info.get("website"),
                    "description": (info.get("longBusinessSummary") or "")[:200],
                }
            )
    except Exception as e:
        return {"error": error_message(e)}


# --------------- chip_distribution ---------------


def cmd_chip_distribution(args):
    if detect_market(args.symbol) != "A":
        return {"error": "chip_distribution only available for A-shares"}
    try:
        import akshare as ak

        df = _akshare_retry(ak.stock_cyq_em, symbol=args.symbol)
        if df is None or df.empty:
            return {"symbol": args.symbol, "error": "Chip distribution data unavailable"}
        latest = df.iloc[-1] if len(df) > 0 else {}
        col_map = {
            "日期": "date",
            "获利比例": "profit_ratio",
            "平均成本": "avg_cost",
            "90%成本": "cost_90pct",
            "90成本-低": "cost_90_low",
            "90成本-高": "cost_90_high",
            "70%成本": "cost_70pct",
            "70成本-低": "cost_70_low",
            "70成本-高": "cost_70_high",
            "集中度": "concentration",
        }
        result = {"symbol": args.symbol}
        for cn_col, en_col in col_map.items():
            val = latest.get(cn_col)
            if val is not None:
                result[en_col] = _sanitize(val)
        if "cost_90_high" in result and "cost_90_low" in result:
            high, low = result.get("cost_90_high", 0), result.get("cost_90_low", 0)
            if high and low and (high + low) > 0:
                result["concentration_90"] = round((high - low) / ((high + low) / 2) * 100, 2)
        if "cost_70_high" in result and "cost_70_low" in result:
            h70, l70 = result.get("cost_70_high", 0), result.get("cost_70_low", 0)
            if h70 and l70 and (h70 + l70) > 0:
                result["concentration_70"] = round((h70 - l70) / ((h70 + l70) / 2) * 100, 2)
        return result
    except Exception as e:
        return {"error": error_message(e)}


# --------------- market_stats ---------------


def _clist_symbol(market: str, code) -> str:
    code = str(code)
    return f"{code.zfill(5)}.HK" if market == "HK" else normalize_us_symbol(code) if market == "US" else code


def _clist_change_at(market: str, rank: int, desc: bool = True):
    _, rows = _em_clist(market, "f3", "f3", page=rank, size=1, desc=desc)
    return _parse_float(rows[0].get("f3")) if rows else None


def _leading_count(market: str, total: int, desc: bool) -> int:
    """Ranks with a strictly positive (desc) / negative (asc) change, by binary search over single-row pages."""
    lo, hi = 0, total
    while lo < hi:
        mid = (lo + hi + 1) // 2
        change = _clist_change_at(market, mid, desc) or 0
        if change > 0 if desc else change < 0:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _movers(market: str, desc: bool) -> tuple:
    total, rows = _em_clist(market, "f3", "f12,f14,f3", size=5, desc=desc)
    movers = [
        {"symbol": _clist_symbol(market, r.get("f12")), "name": r.get("f14"), "change_pct": _parse_float(r.get("f3"))}
        for r in rows
    ]
    return total, movers if desc else movers[::-1]  # losers keep the old order: least negative first


def _median_change(market: str, total: int, up: int, flat: int, counted: int):
    """Median of the counted stocks; unchanged and unquoted rows rank between gainers and losers."""
    if counted <= 0:
        return None
    k = (counted + 1) // 2
    if up < k <= up + flat:
        return 0.0
    return _clist_change_at(market, k if k <= up else k + total - counted)


_EM_LIMIT_POOL = {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt"}


def _limit_samples(endpoint: str, sort: str, trade_date: str) -> list:
    params = {**_EM_LIMIT_POOL, "Pageindex": 0, "pagesize": 5, "sort": sort, "date": trade_date}
    pool = (_get_json(f"https://push2ex.eastmoney.com/{endpoint}", params).get("data") or {}).get("pool") or []
    return [
        {
            "symbol": p.get("c"),
            "name": p.get("n"),
            "change_pct": _sanitize(round(p["zdp"], 2) if isinstance(p.get("zdp"), (int, float)) else None),
        }
        for p in pool
    ]


def _market_stats_a() -> dict:
    """A-share breadth from eastmoney's 涨跌分布 (buckets ±11 are limit moves) — no per-stock rows."""
    body = _get_json("https://push2ex.eastmoney.com/getTopicZDFenBu", _EM_LIMIT_POOL).get("data") or {}
    buckets = {int(k): int(v) for item in body.get("fenbu") or [] for k, v in item.items()}
    if not buckets:
        raise ValueError("eastmoney breadth distribution unavailable")
    up = sum(v for k, v in buckets.items() if k > 0)
    down = sum(v for k, v in buckets.items() if k < 0)
    flat = buckets.get(0, 0)
    trade_date = str(body.get("qdate") or "")
    index_rows = _em_ulist(["1.000001", "0.399001", "0.899050"], "f6")  # 沪 + 深 + 北 market turnover
    turnovers = [_parse_float(r.get("f6")) for r in index_rows]
    total, gainers = _movers("A", True)
    _, losers = _movers("A", False)
    return {
        "total_stocks": up + down + flat,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "limit_up_count": buckets.get(11, 0),
        "limit_down_count": buckets.get(-11, 0),
        "median_change_pct": _median_change("A", total, up, flat, up + down + flat),
        "total_turnover": round(sum(turnovers), 0) if turnovers and None not in turnovers else None,
        "top5_gainers": gainers,
        "top5_losers": losers,
        "limit_up_samples": _limit_samples("getTopicZTPool", "fbt:asc", trade_date),
        "limit_down_samples": _limit_samples("getTopicDTPool", "fund:asc", trade_date),
    }


def _market_stats_clist(market: str) -> dict:
    """HK/US breadth: the up/down boundaries of the change-ranked list, found by binary search."""
    from concurrent.futures import ThreadPoolExecutor

    total, gainers = _movers(market, True)
    if not total:
        raise ValueError(f"eastmoney {market} list unavailable")
    # The two binary searches (~15 sequential requests each) and the loser list are independent.
    with ThreadPoolExecutor(max_workers=3) as pool:
        losers_f = pool.submit(_movers, market, False)
        up_f, down_f = (pool.submit(_leading_count, market, total, rising) for rising in (True, False))
        _, losers = losers_f.result()
        up, down = up_f.result(), down_f.result()
    flat = total - up - down
    return {
        "total_stocks": total,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "median_change_pct": _median_change(market, total, up, flat, total),
        "top5_gainers": gainers,
        "top5_losers": losers,
        # No daily price limits; a turnover total would need every row.
        **dict.fromkeys(("limit_up_count", "limit_down_count", "limit_up_samples", "limit_down_samples")),
        "total_turnover": None,
        "market": market,
        "source": "eastmoney_rank",
        "turnover_currency": "HKD" if market == "HK" else "USD",
    }


def _market_stats_tradingview_hk() -> dict:
    """Fetch quoted HK breadth and movers with bounded, server-ranked requests."""
    from concurrent.futures import ThreadPoolExecutor

    def ranked(operation, size, offset=0, order="desc"):
        payload = _tradingview_scan(
            "hongkong",
            {
                "columns": ["name", "description", "change"],
                "filter": [*_TV_HK_STOCK_FILTERS, {"left": "change", "operation": operation, "right": 0}],
                "sort": {"sortBy": "change", "sortOrder": order},
                "range": [offset, offset + size],
            },
        )
        count, data = payload.get("totalCount"), payload.get("data")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(data, list)
            or len(data) != min(size, max(0, count - offset))
        ):
            raise ValueError("invalid TradingView HK market breadth response")
        rows = []
        for item in data:
            code, name, change = item["d"]
            if (
                not re.fullmatch(r"\d{1,5}", str(code))
                or not isinstance(change, (int, float))
                or not math.isfinite(change)
            ):
                raise ValueError("invalid TradingView HK market breadth row")
            rows.append({"symbol": _clist_symbol("HK", code), "name": name, "change_pct": round(change, 4)})
        return count, rows

    with ThreadPoolExecutor(max_workers=3) as pool:
        up_f = pool.submit(ranked, "greater", 5)
        down_f = pool.submit(ranked, "less", 5, order="asc")
        flat_f = pool.submit(ranked, "equal", 1)
        up, gainers = up_f.result()
        down, losers = down_f.result()
        flat, _ = flat_f.result()
    total = up + down + flat  # Only quotes with a reported percent change count.
    if not total:
        raise ValueError("TradingView HK market breadth unavailable")
    mid = (total + 1) // 2
    median = 0.0
    if mid <= up:
        _, rows = ranked("greater", 1, mid - 1)
        median = rows[0]["change_pct"] if rows else None
    elif mid > up + flat:
        _, rows = ranked("less", 1, total - mid, order="asc")
        median = rows[0]["change_pct"] if rows else None
    return {
        "total_stocks": total,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "median_change_pct": median,
        "top5_gainers": gainers,
        "top5_losers": losers[::-1],
        **dict.fromkeys(
            ("limit_up_count", "limit_down_count", "limit_up_samples", "limit_down_samples", "total_turnover")
        ),
        "market": "HK",
        "source": "tradingview_rank",
        "turnover_currency": "HKD",
    }


def cmd_market_stats(args):
    market = getattr(args, "market", "A")
    try:
        with socket_timeout(25):
            if market == "HK":
                stats = _failover(
                    [("eastmoney", lambda: _market_stats_clist("HK")), ("tradingview", _market_stats_tradingview_hk)],
                    label="market_stats_hk",
                )
            else:
                stats = _market_stats_a() if market == "A" else _market_stats_clist(market)
        # The mean needs every stock's change; the key stays for the JSON contract.
        return _clean_row({"avg_change_pct": None, **stats})
    except Exception as e:
        return {"error": error_message(e)}


# --------------- fundamental_context ---------------


def cmd_fundamental_context(args):
    if detect_market(args.symbol) != "A":
        return {"error": "fundamental_context only available for A-shares"}
    try:
        import akshare as ak

        result = {"symbol": args.symbol}

        try:
            quote = quote_a(args.symbol)
            result["valuation"] = {
                "pe": quote.get("pe"),
                "pe_basis": quote.get("pe_basis"),
                "pb": quote.get("pb"),
                "market_cap": quote.get("market_cap"),
            }
        except Exception:
            result["valuation"] = {}

        try:
            df = _financials_a_frame(args.symbol)
            if df is not None and len(df) >= 1:
                r0 = df.iloc[0]
                profitability = {
                    "roe": _sanitize(r0.get("净资产收益率(%)")),
                    "gross_margin": _sanitize(r0.get("销售毛利率(%)")),
                    "net_margin": _sanitize(r0.get("销售净利率(%)")),
                    "report_date": _sanitize(r0.get("日期")),
                }
                result["profitability"] = profitability

                if len(df) >= 2:
                    growth = {}
                    for field, key in [("主营业务收入增长率(%)", "revenue_yoy"), ("净利润增长率(%)", "net_income_yoy")]:
                        v0 = r0.get(field)
                        if v0 is not None:
                            growth[key] = _sanitize(v0)
                    result["growth"] = growth
        except Exception:
            pass

        try:
            div_df = _akshare_retry(ak.stock_history_dividend_detail, symbol=args.symbol, indicator="分红")
            if div_df is not None and not div_df.empty:
                dividends = []
                for _, row in div_df.head(5).iterrows():
                    dividends.append(
                        _clean_row(
                            {
                                "year": row.get("报告期"),
                                "plan": row.get("分红方案"),
                                "record_date": row.get("股权登记日"),
                            }
                        )
                    )
                result["dividends"] = dividends
        except Exception:
            pass

        return result
    except Exception as e:
        return {"error": error_message(e)}


# --------------- short-term sentiment: limit_up_pool / dragon_tiger / hot_stocks (A-share only) ---------------


def _resolve_cn_data_date(raw: str) -> tuple[datetime, datetime]:
    """Resolve a requested date (YYYYMMDD) to the CN trading day its data belongs to.

    eastmoney's zt-pool/LHB endpoints silently clamp any date later than the latest
    trading day to that day and answer EMPTY for past non-trading days, so the
    requested date never proves the data's date (issue #31). Returns
    (requested, data_date) as datetimes — data_date is the last trading day at or
    before min(requested, today): weekends, holidays and future dates all resolve
    backward. Raises ValueError on malformed input."""
    from trading_calendar import cn_trade_dates  # flat sibling import

    req = datetime.strptime(raw, "%Y%m%d")
    d = min(req, _cn_now().replace(hour=0, minute=0, second=0, microsecond=0))
    trade_dates = cn_trade_dates(d.year)
    if d.month == 1:  # the walk-back can cross into December of the previous year
        trade_dates |= cn_trade_dates(d.year - 1)
    for _ in range(20):  # 春节/国庆 plus adjoining weekends fit well within 20 days
        # empty trade-date set (calendar fetch failed) → weekday-only approximation
        if d.weekday() < 5 and (not trade_dates or d.strftime("%Y-%m-%d") in trade_dates):
            break
        d -= timedelta(days=1)
    return req, d


def cmd_limit_up_pool(args):
    """A-share limit-up pool (涨停池) for one trading day via eastmoney.

    The requested date is first resolved to the trading day the data belongs to
    (_resolve_cn_data_date); when they differ the payload carries requested_date
    and stale=True so a weekend/future request can't be misread as that day's pool.
    Known limitation: on a trading day before that day's pool is published, the
    request resolves to itself and no stale label is emitted (the zt-pool payload
    carries no date column to cross-check against)."""
    try:
        req, data_day = _resolve_cn_data_date((args.date or _cn_now().strftime("%Y%m%d")).replace("-", ""))
    except ValueError:
        return {"error": f"limit_up_pool: invalid --date {args.date!r} (want YYYYMMDD)"}
    date = data_day.strftime("%Y%m%d")
    requested = req.strftime("%Y%m%d")
    result = {"date": date}
    if req != data_day:
        result["requested_date"] = requested
        result["stale"] = True
        result["note"] = f"{requested} is not a trading day or has no data yet; showing the last trading day {date}"
    try:
        import akshare as ak

        df = _akshare_retry(ak.stock_zt_pool_em, date=date)
        if df is None or df.empty:
            result["count"] = 0
            result["max_consecutive_boards"] = 0
            result["pool"] = []
            if date != requested:
                result["note"] = (
                    f"{requested} is not a trading day or has no data yet; "
                    f"last trading day {date} has no limit-up pool data"
                )
            else:
                result["note"] = f"no limit-up pool data for {date}"
            return result
        col_map = {
            "代码": "code",
            "名称": "name",
            "涨跌幅": "change_pct",
            "最新价": "price",
            "成交额": "turnover",
            "流通市值": "float_market_cap",
            "总市值": "total_market_cap",
            "换手率": "turnover_rate",
            "封板资金": "seal_amount",
            "首次封板时间": "first_seal_time",
            "最后封板时间": "last_seal_time",
            "炸板次数": "break_count",
            "涨停统计": "limit_up_stat",
            "连板数": "consecutive_boards",
            "所属行业": "industry",
        }
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]
        pool = [_clean_row(r) for r in df[keep].to_dict("records")]
        boards = [r["consecutive_boards"] for r in pool if isinstance(r.get("consecutive_boards"), (int, float))]
        result["count"] = len(pool)
        result["max_consecutive_boards"] = max(boards) if boards else 0
        result["pool"] = pool
        return result
    except Exception as e:
        return {"error": f"limit_up_pool date={requested}: {error_message(e)}"}


def _dragon_tiger_em(raw: str) -> list:
    """Eastmoney 龙虎榜 via akshare stock_lhb_detail_em."""
    import akshare as ak
    import pandas as pd

    df = _akshare_retry(ak.stock_lhb_detail_em, start_date=raw, end_date=raw)
    if df is None or df.empty:
        return []
    col_map = {
        "代码": "code",
        "名称": "name",
        "上榜日": "list_date",
        "收盘价": "close",
        "涨跌幅": "change_pct",
        "龙虎榜净买额": "net_buy",
        "龙虎榜买入额": "buy_amount",
        "龙虎榜卖出额": "sell_amount",
        "龙虎榜成交额": "lhb_turnover",
        "上榜原因": "reason",
        "解读": "interpretation",
    }
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    df = df[keep]
    if "net_buy" in df.columns:
        df["net_buy"] = pd.to_numeric(df["net_buy"], errors="coerce")
    return [_clean_row(r) for r in df.to_dict("records")]


def _dragon_tiger_sina(raw: str) -> list:
    """Sina 龙虎榜每日详情 via akshare stock_lhb_detail_daily_sina.

    Sina has no 涨跌幅/净买额/买入额/卖出额/上榜日/解读 — its 指标 column is the
    listing reason and list_date is filled from the query date. A stock listed for
    several reasons appears once per reason (sina's page layout)."""
    import akshare as ak

    df = _akshare_retry(ak.stock_lhb_detail_daily_sina, date=raw)
    if df is None or df.empty:
        return []
    col_map = {
        "股票代码": "code",
        "股票名称": "name",
        "收盘价": "close",
        "指标": "reason",
    }
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    rows = [_clean_row(r) for r in df[keep].to_dict("records")]
    date_iso = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    for r in rows:
        r["list_date"] = date_iso
    return rows


def cmd_dragon_tiger(args):
    """A-share dragon-tiger list (龙虎榜) for one trading day: eastmoney → sina.

    Same date resolution as cmd_limit_up_pool (issue #31): a non-trading or future
    date is served the last trading day's list, labeled with requested_date and
    stale=True."""
    try:
        req, data_day = _resolve_cn_data_date((args.date or _cn_now().strftime("%Y-%m-%d")).replace("-", ""))
    except ValueError:
        return {"error": f"dragon_tiger: invalid --date {args.date!r} (want YYYY-MM-DD)"}
    raw = data_day.strftime("%Y%m%d")
    date = data_day.strftime("%Y-%m-%d")
    requested_iso = req.strftime("%Y-%m-%d")
    sources = [
        _sourced("eastmoney", lambda: _dragon_tiger_em(raw)),
        _sourced("sina", lambda: _dragon_tiger_sina(raw)),
    ]
    try:
        rows = _failover(sources, label=f"dragon_tiger:{raw}")
    except Exception as e:
        return {"error": error_message(e)}
    stale = req != data_day
    if not rows:
        result = {"date": date, "count": 0, "items": []}
        if stale:
            result["requested_date"] = requested_iso
            result["stale"] = True
            result["note"] = (
                f"{requested_iso} is not a trading day or has no data yet; "
                f"last trading day {date} has no dragon-tiger data"
            )
        else:
            result["note"] = f"no dragon-tiger data for {date}"
        return result
    source = rows[0]["source"]
    if args.symbol:
        m = re.fullmatch(r"(?:sh|sz|bj)(\d{6})", str(args.symbol), re.IGNORECASE)
        want = m.group(1) if m else str(args.symbol)
        rows = [r for r in rows if str(r.get("code")) == want]
    if any(isinstance(r.get("net_buy"), (int, float)) for r in rows):
        rows = sorted(
            rows,
            key=lambda r: abs(r["net_buy"]) if isinstance(r.get("net_buy"), (int, float)) else 0,
            reverse=True,
        )
    items = [{k: v for k, v in r.items() if k != "source"} for r in rows[: args.top]]
    result = {"date": date, "count": len(items), "items": items, "source": source}
    if stale:
        result["requested_date"] = requested_iso
        result["stale"] = True
        result["note"] = f"{requested_iso} is not a trading day or has no data yet; showing the last trading day {date}"
    return result


def _strip_exchange_prefix(row: dict) -> dict:
    """Bare the exchange prefix on row["code"] ("SZ000001" → "000001", original kept in
    code_full). Bare 6-digit codes pass through unchanged."""
    m = re.fullmatch(r"(sh|sz|bj)(\d{6})", str(row.get("code") or ""), re.IGNORECASE)
    if m:
        row["code_full"] = row["code"]
        row["code"] = m.group(2)
    return row


def _hot_stocks_em() -> list:
    """Eastmoney 人气榜 via akshare stock_hot_rank_em."""
    import akshare as ak

    df = _akshare_retry(ak.stock_hot_rank_em)
    if df is None or df.empty:
        return []
    col_map = {
        "当前排名": "rank",
        "代码": "code",
        "股票名称": "name",
        "最新价": "price",
        "涨跌幅": "change_pct",
    }
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    return [_strip_exchange_prefix(_clean_row(r)) for r in df[keep].to_dict("records")]


def _hot_stocks_baidu() -> list:
    """Baidu 股市通热搜 via akshare stock_hot_search_baidu. No code/price columns —
    only the stock name — and 涨跌幅 strings carry a % suffix."""
    import akshare as ak

    df = _akshare_retry(
        ak.stock_hot_search_baidu,
        symbol="A股",
        date=_cn_now().strftime("%Y%m%d"),
        time="今日",
    )
    if df is None or df.empty:
        return []
    rows = []
    for i, r in enumerate(df.to_dict("records"), 1):
        pct = str(r.get("涨跌幅") or "").replace("%", "")
        rows.append(_clean_row({"rank": i, "name": r.get("名称/代码"), "change_pct": _parse_float(pct)}))
    return rows


def cmd_hot_stocks(args):
    """A-share popularity ranking (人气榜): eastmoney → baidu.

    The xueqiu leg was dropped: akshare's stock_hot_follow_xq accepts no user token
    and its built-in xq_a_token is stale (xueqiu answers 400016), so the leg could
    never win — failover errors now name only the sources that actually ran."""
    sources = [
        _sourced("eastmoney", _hot_stocks_em),
        _sourced("baidu", _hot_stocks_baidu),
    ]
    try:
        rows = _failover(sources, label="hot_stocks")
    except Exception as e:
        return {"error": error_message(e)}
    if not rows:
        return {"count": 0, "items": [], "note": "no hot-rank data available"}
    items = [{k: v for k, v in r.items() if k != "source"} for r in rows[: args.top]]
    return {"count": len(items), "items": items, "source": rows[0]["source"]}


# --------------- margin_trading / northbound_flow (A-share only) ---------------

# Eastmoney per-stock margin detail (RPTA_WEB_RZRQ_GGMX) → output keys
_MARGIN_COLUMNS = {
    "SCODE": "symbol",
    "SECNAME": "name",
    "RZYE": "margin_balance",
    "RZMRE": "margin_buy",
    "RZCHE": "margin_repay",
    "RQYL": "short_balance_shares",
    "RQMCL": "short_sell_shares",
    "RQCHL": "short_repay_shares",
    "RQYE": "short_balance",
    "RZRQYE": "total_balance",
}


def cmd_margin_trading(args):
    """Per-stock margin trading detail (融资融券) for the last N trading days, newest first.

    One server-filtered eastmoney query returns exactly this stock's rows (两融明细 T 日晚间才发布 —
    a morning run ends at the previous day)."""
    if detect_market(args.symbol) != "A":
        return {"error": "margin_trading only available for A-shares"}
    days = getattr(args, "days", 10)
    if days < 1:
        return {"error": f"days must be a positive integer, got {days}"}
    days = min(days, 60)
    bare = _cn_code(args.symbol)[2:]
    try:
        with socket_timeout(25):
            _, records = _em_datacenter("RPTA_WEB_RZRQ_GGMX", f'(SCODE="{bare}")', "DATE", days)
        rows = [
            _clean_row(
                {"date": str(r.get("DATE", ""))[:10]} | {key: r.get(col) for col, key in _MARGIN_COLUMNS.items()}
            )
            for r in records
        ]
        if not rows:
            return {"symbol": args.symbol, "error": f"no margin trading data for {args.symbol} (可能不是两融标的)"}
        if getattr(args, "include_ratio", False):
            try:
                with socket_timeout(25):
                    history = (
                        _failover(
                            [
                                ("akshare", lambda: _kline_akshare(bare, "daily", days + 10)),
                                ("efinance", lambda: _kline_efinance(bare, "daily", days + 10)),
                                ("baostock", lambda: _kline_baostock(bare, "daily", days + 10)),
                            ],
                            label=f"margin_turnover:{bare}",
                        )
                        or []
                    )
                    turnovers = {str(r.get("date"))[:10]: _sanitize(_parse_float(r.get("turnover"))) for r in history}
            except Exception:
                turnovers = {}
            for row in rows:
                turnover = turnovers.get(row["date"])
                buy = _sanitize(_parse_float(row.get("margin_buy")))
                row["turnover"] = turnover
                row["margin_buy_ratio_pct"] = (
                    round(buy / turnover * 100, 4) if buy is not None and turnover and turnover > 0 else None
                )
                if row["margin_buy_ratio_pct"] is None:
                    row["margin_buy_ratio_note"] = "matching-date CNY turnover unavailable"
        return rows
    except Exception as e:
        return {"error": error_message(e)}


def _northbound_row_is_shell(row: dict) -> bool:
    """停披期空壳行：date 之外每个数据字段都是 None。持股市值在停披后被回填 0.0
    （北向持仓市值约 1.9 万亿，0 是缺失哨兵而非真实值），视同 None。"""
    for k, v in row.items():
        if k == "date" or v is None:
            continue
        if k == "hold_market_cap" and not v:
            continue
        return False
    return True


def cmd_northbound_flow(args):
    """Market-level northbound (北向资金) flow history via eastmoney hsgt history.

    Data caliber: the exchanges stopped publishing daily northbound net-buy after
    2024-08-16 — later rows are shells (date only, flow fields all None,
    持股市值 backfilled 0.0) except quarter-end rows, which still disclose
    持股市值. Shell rows are filtered out of the tail(days) window; when nothing
    real remains, a clean error explains the disclosure stop (increase days to
    reach pre-2024-08 history). The series comes back ascending — returned
    newest-first."""
    days = getattr(args, "days", 10)
    if days < 1:
        return {"error": f"days must be a positive integer, got {days}"}
    try:
        import akshare as ak

        with socket_timeout(25):
            df = _akshare_retry(ak.stock_hsgt_hist_em, symbol="北向资金")
        if df is None or df.empty:
            return {"error": "northbound flow data unavailable"}
        col_map = {
            "日期": "date",
            "当日成交净买额": "net_buy",
            "买入成交额": "buy_turnover",
            "卖出成交额": "sell_turnover",
            "历史累计净买额": "accum_net_buy",
            "持股市值": "hold_market_cap",
        }
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]
        rows = [_clean_row(r) for r in df[keep].tail(days).to_dict("records")]
        rows = [r for r in rows if not _northbound_row_is_shell(r)]
        if not rows:
            return {
                "error": (
                    "北向资金日度净买额自 2024-08-16 起交易所停止披露，仅此前历史数据可用"
                    f"（当前 days={days} 窗口内无有效数据，加大 days 可取历史）"
                )
            }
        rows.reverse()
        return rows
    except Exception as e:
        return {"error": error_message(e)}


# --------------- CLI ---------------


def output(data):
    print(json.dumps(data, ensure_ascii=False, default=str))


def main():
    parser = argparse.ArgumentParser(description="Stock data fetcher")
    sub = parser.add_subparsers(dest="command")

    p_kline = sub.add_parser("kline")
    p_kline.add_argument("symbol")
    p_kline.add_argument("--period", default="daily", choices=["daily", "weekly", "monthly"])
    p_kline.add_argument("--count", type=int, default=60)

    p_quote = sub.add_parser("quote")
    p_quote.add_argument("symbol")

    p_quotes = sub.add_parser("quotes")
    p_quotes.add_argument("symbols", help=f"comma-separated, at most {QUOTES_MAX}")
    p_quotes.add_argument(
        "--watchlist", action="store_true", help="use watchlist quotes with fund NAV and TIAA metadata"
    )

    p_klines = sub.add_parser("klines")
    p_klines.add_argument("symbols", help=f"comma-separated, at most {QUOTES_MAX}; daily bars")
    p_klines.add_argument("--count", type=int, default=30)

    p_fund_quote = sub.add_parser("fund_quote")
    p_fund_quote.add_argument("symbol")

    p_cf = sub.add_parser("capital_flow")
    p_cf.add_argument("symbol", nargs="?", default="")
    p_cf.add_argument("--mode", default="detail", choices=["detail", "summary", "sector_flow"])

    p_news = sub.add_parser("news")
    p_news.add_argument("symbol")
    p_news.add_argument("--days", type=int, default=3)

    p_fin = sub.add_parser("financials")
    p_fin.add_argument("symbol")
    p_fin.add_argument("--periods", type=int, default=1)
    p_fin.add_argument("--annual", action="store_true", help="Select annual reports; periods=10 requests ten years")
    p_fin.add_argument("--include-dcf-inputs", action="store_true", help="Add annual A/HK/US DCF input fields")

    p_snap = sub.add_parser("market_snapshot")
    p_snap.add_argument("--market", default="A", choices=["A", "HK", "US"])
    p_snap.add_argument("--filters", default="{}", help="JSON screener filters; market_cap_min bounds the scan")

    p_idx = sub.add_parser("market_indices")
    p_idx.add_argument("--region", default="cn", choices=["cn", "hk", "us", "jp", "kr", "tw"])

    p_sec = sub.add_parser("sector_rankings")
    p_sec.add_argument("--top", type=int, default=10)
    p_sec.add_argument("--direction", default="top", choices=["top", "bottom", "both"])
    p_sec.add_argument("--board-type", default="industry", choices=["industry", "concept"])
    p_sec.add_argument("--market", default="A", choices=["A", "US"])

    p_cons = sub.add_parser("sector_constituents")
    p_cons.add_argument("sector")
    p_cons.add_argument("--board-type", default="auto", choices=["industry", "concept", "auto"])

    p_rss = sub.add_parser("resolve_stock_sectors")
    p_rss.add_argument("symbol")

    p_info = sub.add_parser("stock_info")
    p_info.add_argument("symbol")

    p_chip = sub.add_parser("chip_distribution")
    p_chip.add_argument("symbol")

    p_stats = sub.add_parser("market_stats")
    p_stats.add_argument("--market", default="A", choices=["A", "HK", "US"])

    p_fund = sub.add_parser("fundamental_context")
    p_fund.add_argument("symbol")

    p_zt = sub.add_parser("limit_up_pool")
    p_zt.add_argument("--date", default=None, help="Trading date in YYYYMMDD format (default: today)")

    p_lhb = sub.add_parser("dragon_tiger")
    p_lhb.add_argument("--date", default=None, help="Trading date in YYYY-MM-DD format (default: today)")
    p_lhb.add_argument("--symbol", default=None, help="Filter to a single stock code")
    p_lhb.add_argument("--top", type=int, default=20)

    p_hot = sub.add_parser("hot_stocks")
    p_hot.add_argument("--top", type=int, default=20)

    p_margin = sub.add_parser("margin_trading")
    p_margin.add_argument("symbol")
    p_margin.add_argument("--days", type=int, default=10)
    p_margin.add_argument("--include-ratio", action="store_true", help="Add same-day margin buys / CNY turnover")

    p_nb = sub.add_parser("northbound_flow")
    p_nb.add_argument("--days", type=int, default=10)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "kline": cmd_kline,
        "quote": cmd_quote,
        "quotes": cmd_quotes,
        "klines": cmd_klines,
        "fund_quote": cmd_fund_quote,
        "capital_flow": cmd_capital_flow,
        "news": cmd_news,
        "financials": cmd_financials,
        "market_snapshot": cmd_market_snapshot,
        "market_indices": cmd_market_indices,
        "sector_rankings": cmd_sector_rankings,
        "sector_constituents": cmd_sector_constituents,
        "resolve_stock_sectors": lambda a: resolve_stock_sectors(a.symbol),
        "stock_info": cmd_stock_info,
        "chip_distribution": cmd_chip_distribution,
        "market_stats": cmd_market_stats,
        "fundamental_context": cmd_fundamental_context,
        "limit_up_pool": cmd_limit_up_pool,
        "dragon_tiger": cmd_dragon_tiger,
        "hot_stocks": cmd_hot_stocks,
        "margin_trading": cmd_margin_trading,
        "northbound_flow": cmd_northbound_flow,
    }
    # Provider libraries (baostock etc.) print prose to stdout on failure,
    # which would corrupt the JSON-only contract — divert that noise to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        result = dispatch[args.command](args)
    output(result)
    if isinstance(result, dict) and "error" in result:
        sys.exit(1)
    if args.command in ("quotes", "klines"):
        # Timed-out daemon threads may hold stderr; normal shutdown would then abort and drop valid rows.
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    utf8_stdio()
    main()
