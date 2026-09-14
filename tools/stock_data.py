#!/usr/bin/env python3
"""Stock market data fetcher for A-shares (akshare), HK and US (yfinance)."""

import argparse
import contextlib
import hashlib
import json
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path


def detect_market(symbol: str) -> str:
    s = symbol.upper()
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
    info = {"market": detect_market(symbol), "board": "main", "is_st": False, "is_etf": False, "limit_pct": 0.10}
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


def mark_st(info: dict, name: str) -> dict:
    """Mark ST status from stock name; adjusts limit_pct to 5%."""
    if name and "ST" in name.upper():
        info["is_st"] = True
        if info["board"] == "main":
            info["limit_pct"] = 0.05
    return info


def calc_limit_price(pre_close: float, ratio: float, direction: str = "up") -> float:
    """Calculate limit-up or limit-down price with banker's rounding."""
    import numpy as np

    sign = 1 if direction == "up" else -1
    return np.floor(pre_close * (1 + sign * ratio) * 100 + 0.5) / 100.0


def _failover(sources: list, label: str):
    """Return first truthy result; else raise RuntimeError aggregating each source's error."""
    errors = []
    for name, fn in sources:
        try:
            result = fn()
            if result:
                return result
        except Exception as e:
            errors.append(f"{name}: {e}")
    if errors:
        raise RuntimeError(f"{label}: {'; '.join(errors)}")
    return None


def _akshare_retry(fn, *args, retries=2, delay=1, **kwargs):
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(delay)


# Index codes collide with SZ stocks in the 000xxx range (000001 is both the
# SSE Composite and Ping An Bank), so indices need an explicit exchange mapping.
_BAOSTOCK_INDEX_CODES = {
    "000001": "sh",  # 上证综指
    "000016": "sh",  # 上证50
    "000300": "sh",  # 沪深300
    "000688": "sh",  # 科创50
    "000852": "sh",  # 中证1000
    "000905": "sh",  # 中证500
    "399001": "sz",  # 深证成指
    "399005": "sz",  # 中小100
    "399006": "sz",  # 创业板指
}


def _to_baostock_code(symbol: str) -> str:
    index_exchange = _BAOSTOCK_INDEX_CODES.get(symbol)
    if index_exchange:
        return f"{index_exchange}.{symbol}"
    if symbol.startswith(
        ("600", "601", "603", "605", "688", "689", "510", "512", "513", "515", "516", "518", "560", "588")
    ):
        return f"sh.{symbol}"
    return f"sz.{symbol}"


def _kline_efinance(symbol: str, period: str, count: int) -> list:
    import efinance as ef

    freq_map = {"daily": 101, "weekly": 102, "monthly": 103}
    df = ef.stock.get_quote_history(symbol, klt=freq_map.get(period, 101))
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


def _kline_baostock(symbol: str, period: str, count: int) -> list:
    import io

    import baostock as bs
    import pandas as pd

    freq_map = {"daily": "d", "weekly": "w", "monthly": "m"}
    end = datetime.now()
    start = end - timedelta(days=count * 7 if period == "weekly" else count * 31 if period == "monthly" else count * 2)
    # baostock login/logout print status lines to stdout — silence them so the
    # tool's JSON output on stdout stays clean for the calling host.
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
    keep = [c for c in ["date", "open", "high", "low", "close", "volume", "turnover", "change_pct"] if c in df.columns]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


def _quote_efinance(symbol: str) -> dict:
    import efinance as ef

    df = ef.stock.get_realtime_quotes([symbol])
    if df is None or df.empty:
        raise ValueError("efinance quote returned empty")
    r = df.iloc[0]
    col_map = {
        "股票代码": "symbol",
        "股票名称": "name",
        "最新价": "price",
        "涨跌额": "change",
        "涨跌幅": "change_pct",
        "成交量": "volume",
        "成交额": "turnover",
        "最高": "high",
        "最低": "low",
        "今开": "open",
        "昨收": "prev_close",
        "总市值": "market_cap",
        "市盈率": "pe",
        "换手率": "turnover_rate",
    }
    result = {}
    for cn_key, en_key in col_map.items():
        val = r.get(cn_key)
        if val is not None and val != "":
            with contextlib.suppress(ValueError, TypeError):
                val = float(val) if en_key not in ("symbol", "name") else val
        result[en_key] = val
    return _clean_row(result)


def _sanitize(obj):
    """Convert pandas types to JSON-serializable Python types."""
    import numpy as np
    import pandas as pd

    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else round(float(obj), 4)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        return None if obj != obj else round(obj, 4)
    return obj


def _clean_row(d: dict) -> dict:
    return {k: _sanitize(v) for k, v in d.items()}


# --------------- kline ---------------


def kline_a(symbol: str, period: str, count: int) -> list:
    """A-share kline with failover: akshare → tushare → efinance → pytdx → baostock."""
    return _failover(
        [
            ("akshare", lambda: _kline_akshare(symbol, period, count)),
            ("tushare", lambda: _kline_tushare(symbol, period, count)),
            ("efinance", lambda: _kline_efinance(symbol, period, count)),
            ("pytdx", lambda: _kline_pytdx(symbol, period, count)),
            ("baostock", lambda: _kline_baostock(symbol, period, count)),
        ],
        label=f"kline_a:{symbol}",
    )


def _kline_akshare(symbol: str, period: str, count: int) -> list:
    import akshare as ak

    period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
    end = datetime.now()
    start = end - timedelta(days=count * 7 if period == "weekly" else count * 31 if period == "monthly" else count * 2)

    # ETFs (51/52/56/58/15/16/18xxxx) are not covered by stock_zh_a_hist
    hist_fn = ak.fund_etf_hist_em if normalize_stock_code(symbol)["is_etf"] else ak.stock_zh_a_hist
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


def _kline_tushare(symbol: str, period: str, count: int) -> list:
    """Tushare kline via HTTP API. Requires TUSHARE_TOKEN."""
    import os

    import pandas as pd
    import requests

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise ValueError("TUSHARE_TOKEN not set")

    api_name_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
    end = datetime.now()
    start = end - timedelta(days=count * 7 if period == "weekly" else count * 31 if period == "monthly" else count * 2)

    ts_code = f"{symbol}.SH" if symbol.startswith(("6", "9", "5")) else f"{symbol}.SZ"

    resp = requests.post(
        "http://api.tushare.pro",
        json={
            "api_name": api_name_map.get(period, "daily"),
            "token": token,
            "params": {
                "ts_code": ts_code,
                "start_date": start.strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
            },
            "fields": "trade_date,open,high,low,close,vol,amount,pct_chg",
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0 or not data.get("data", {}).get("items"):
        raise ValueError(f"tushare returned no data: {data.get('msg', '')}")

    df = pd.DataFrame(data["data"]["items"], columns=data["data"]["fields"])
    col_map = {"trade_date": "date", "vol": "volume", "amount": "turnover", "pct_chg": "change_pct"}
    df = df.rename(columns=col_map)
    df = df.sort_values("date")
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = [c for c in ["date", "open", "high", "low", "close", "volume", "turnover", "change_pct"] if c in df.columns]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


def _quote_tushare(symbol: str) -> dict:
    """Tushare realtime quote via HTTP API."""
    import os

    import requests

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise ValueError("TUSHARE_TOKEN not set")

    ts_code = f"{symbol}.SH" if symbol.startswith(("6", "9", "5")) else f"{symbol}.SZ"

    resp = requests.post(
        "http://api.tushare.pro",
        json={
            "api_name": "realtime_quote",
            "token": token,
            "params": {"ts_code": ts_code},
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0 or not data.get("data", {}).get("items"):
        raise ValueError(f"tushare quote failed: {data.get('msg', '')}")

    row = dict(zip(data["data"]["fields"], data["data"]["items"][0]))
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


# Public TDX quote servers, tried in order — a datacenter IP often gets only
# some of them refused, so a single hardcoded endpoint is a needless failure mode.
_TDX_SERVERS = [
    ("119.147.212.81", 7709),  # Tencent, Shenzhen
    ("180.153.18.170", 7709),  # Shanghai
    ("202.108.253.130", 7709),  # Beijing
    ("59.173.18.69", 7709),  # Wuhan
]


def _pytdx_connect(api) -> None:
    """Connect to the first reachable TDX server.

    pytdx's connect() returns True/False instead of raising (and is not a context
    manager), so wrap it here to keep a failed connection from surfacing as a
    misleading TypeError downstream.
    """
    errors = []
    for ip, port in _TDX_SERVERS:
        try:
            if api.connect(ip, port):
                return
            errors.append(f"{ip}:{port} refused")
        except Exception as e:
            errors.append(f"{ip}:{port} {e}")
    raise ConnectionError(f"pytdx failed to connect to any TDX server ({'; '.join(errors)})")


def _kline_pytdx(symbol: str, period: str, count: int) -> list:
    """pytdx kline from TDX market servers. No credentials needed."""
    from pytdx.hq import TdxHq_API

    market = 1 if symbol.startswith(("6", "9", "5")) else 0
    freq_map = {"daily": 9, "weekly": 5, "monthly": 6}

    api = TdxHq_API()
    try:
        _pytdx_connect(api)
        data = api.get_security_bars(freq_map.get(period, 9), market, symbol, 0, count)
    finally:
        api.disconnect()

    if not data:
        raise ValueError("pytdx returned empty data")

    rows = []
    for bar in data:
        rows.append(
            _clean_row(
                {
                    "date": bar["datetime"][:10],
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["vol"],
                    "turnover": bar.get("amount"),
                }
            )
        )
    return rows[-count:]


def _quote_pytdx(symbol: str) -> dict:
    """pytdx realtime quote from TDX market servers."""
    from pytdx.hq import TdxHq_API

    market = 1 if symbol.startswith(("6", "9", "5")) else 0

    api = TdxHq_API()
    try:
        _pytdx_connect(api)
        data = api.get_security_quotes([(market, symbol)])
    finally:
        api.disconnect()

    if not data:
        raise ValueError("pytdx quote returned empty")

    q = data[0]
    prev_close = q.get("last_close")
    price = q.get("price")
    return _clean_row(
        {
            "symbol": symbol,
            "name": q.get("name", ""),
            "price": price,
            "change": (price - prev_close) if price and prev_close else None,
            "change_pct": ((price / prev_close) - 1) * 100 if price and prev_close else None,
            "volume": q.get("vol"),
            "high": q.get("high"),
            "low": q.get("low"),
            "open": q.get("open"),
            "prev_close": prev_close,
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
                    "date": datetime.fromtimestamp(candles["t"][i]).strftime("%Y-%m-%d"),
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
    prev_close = float(q.prev_close) if q.prev_close else None
    price = float(q.last_done) if q.last_done else None
    return _clean_row(
        {
            "symbol": symbol,
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
            "outputsize": "compact",
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
        rows.append(
            _clean_row(
                {
                    "date": date_str,
                    "open": float(vals["1. open"]),
                    "high": float(vals["2. high"]),
                    "low": float(vals["3. low"]),
                    "close": float(vals["4. close"]),
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

    return _clean_row(
        {
            "symbol": symbol,
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


def _yf_failover(symbol: str, yfinance, finnhub, longbridge, alphavantage, label: str):
    """Shared HK/US/JP/KR/TW failover chain: yfinance → finnhub/longbridge (HK/US) → alphavantage (US only).

    Each provider is a zero-arg callable; callers bind their own symbol/period/count.
    """
    market = detect_market(symbol)
    sources = [("yfinance", yfinance)]
    if market in ("HK", "US"):
        sources += [("finnhub", finnhub), ("longbridge", longbridge)]
    if market == "US":
        sources.append(("alphavantage", alphavantage))
    return _failover(sources, label=label)


def kline_yf(symbol: str, period: str, count: int) -> list:
    """HK/US/JP/KR/TW kline. Failover: yfinance → finnhub/longbridge (HK/US) → alphavantage (US only)."""
    return _yf_failover(
        symbol,
        lambda: _kline_yfinance(symbol, period, count),
        lambda: _kline_finnhub(symbol, period, count),
        lambda: _kline_longbridge(symbol, period, count),
        lambda: _kline_alphavantage(symbol, period, count),
        label=f"kline:{symbol}",
    )


def cmd_kline(args):
    market = detect_market(args.symbol)
    try:
        if market == "A":
            data = kline_a(args.symbol, args.period, args.count)
        else:
            data = kline_yf(args.symbol, args.period, args.count)
        return data
    except Exception as e:
        return {"error": str(e)}


# --------------- quote ---------------


def quote_a(symbol: str) -> dict:
    """A-share quote with failover: akshare → tushare → efinance → pytdx. ETFs try fund spot first."""
    sources = []
    if normalize_stock_code(symbol)["is_etf"]:
        sources.append(("akshare_etf", lambda: _quote_akshare_etf(symbol)))
    sources += [
        ("akshare", lambda: _quote_akshare(symbol)),
        ("tushare", lambda: _quote_tushare(symbol)),
        ("efinance", lambda: _quote_efinance(symbol)),
        ("pytdx", lambda: _quote_pytdx(symbol)),
    ]
    return _failover(sources, label=f"quote_a:{symbol}")


def _quote_akshare_etf(symbol: str) -> dict:
    """A-share ETF realtime quote via akshare fund_etf_spot_em."""
    import akshare as ak

    df = _akshare_retry(ak.fund_etf_spot_em)
    row = df[df["代码"] == symbol]
    if row.empty:
        raise ValueError(f"ETF {symbol} not found in akshare fund spot")
    r = row.iloc[0]
    return _clean_row(
        {
            "symbol": symbol,
            "name": r.get("名称"),
            "price": r.get("最新价"),
            "change": r.get("涨跌额"),
            "change_pct": r.get("涨跌幅"),
            "volume": r.get("成交量"),
            "turnover": r.get("成交额"),
            "high": r.get("最高价"),
            "low": r.get("最低价"),
            "open": r.get("开盘价"),
            "prev_close": r.get("昨收"),
            "market_cap": r.get("总市值"),
            "turnover_rate": r.get("换手率"),
            "volume_ratio": r.get("量比"),
            "is_etf": True,
        }
    )


def _quote_akshare(symbol: str) -> dict:
    import akshare as ak

    df = _akshare_retry(ak.stock_zh_a_spot_em)
    row = df[df["代码"] == symbol]
    if row.empty:
        raise ValueError(f"Symbol {symbol} not found in akshare")
    r = row.iloc[0]
    return _clean_row(
        {
            "symbol": symbol,
            "name": r.get("名称"),
            "price": r.get("最新价"),
            "change": r.get("涨跌额"),
            "change_pct": r.get("涨跌幅"),
            "volume": r.get("成交量"),
            "turnover": r.get("成交额"),
            "high": r.get("最高"),
            "low": r.get("最低"),
            "open": r.get("今开"),
            "prev_close": r.get("昨收"),
            "market_cap": r.get("总市值"),
            "pe": r.get("市盈率-动态"),
            "pb": r.get("市净率"),
            "turnover_rate": r.get("换手率"),
            "volume_ratio": r.get("量比"),
            "amplitude": r.get("振幅"),
            "change_pct_60d": r.get("60日涨跌幅"),
        }
    )


def _quote_yfinance(symbol: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(_yf_hk_symbol(symbol))
    info = t.info
    if not info or "regularMarketPrice" not in info:
        raise ValueError(f"No data for {symbol}")
    return _clean_row(
        {
            "symbol": symbol,
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
            "price": q["c"],
            "change": q["d"],
            "change_pct": q["dp"],
            "high": q["h"],
            "low": q["l"],
            "open": q["o"],
            "prev_close": q["pc"],
        }
    )


def quote_yf(symbol: str) -> dict:
    """HK/US/JP/KR/TW quote. Failover: yfinance → finnhub/longbridge (HK/US) → alphavantage (US only)."""
    return _yf_failover(
        symbol,
        lambda: _quote_yfinance(symbol),
        lambda: _quote_finnhub(symbol),
        lambda: _quote_longbridge(symbol),
        lambda: _quote_alphavantage(symbol),
        label=f"quote:{symbol}",
    )


def cmd_quote(args):
    market = detect_market(args.symbol)
    try:
        return quote_a(args.symbol) if market == "A" else quote_yf(args.symbol)
    except Exception as e:
        return {"error": str(e)}


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

            df = _akshare_retry(ak.stock_sector_fund_flow_rank)
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
            return {"error": str(e)}

    if detect_market(args.symbol) != "A":
        return {"error": "capital_flow only available for A-shares"}
    try:
        import akshare as ak

        market = "sh" if args.symbol.startswith("6") else "sz"
        df = _akshare_retry(ak.stock_individual_fund_flow, stock=args.symbol, market=market)
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
                summary[f"main_net_{label}"] = _sanitize(tail.get("main_net_inflow", pd.Series()).sum())
                summary[f"super_large_net_{label}"] = _sanitize(tail.get("super_large_net", pd.Series()).sum())
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
            return {"error": str(e2)}


# --------------- news ---------------


def _news_search_intel_fallback(symbol: str) -> list:
    """Fallback: use search_intel to find news when primary sources fail."""
    import subprocess
    from pathlib import Path

    tools_dir = Path(__file__).parent
    try:
        result = subprocess.run(
            [sys.executable, str(tools_dir / "search_intel.py"), "search", f"{symbol} 最新消息"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout.strip())
        if isinstance(data, list):
            return [
                {"title": item.get("title", ""), "url": item.get("url", ""), "source": "search"} for item in data[:10]
            ]
        return []
    except (json.JSONDecodeError, TypeError):
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
            cutoff = datetime.now() - timedelta(days=args.days)
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
                results.append(
                    {
                        "title": item.get("title", ""),
                        "publisher": item.get("publisher", ""),
                        "url": item.get("link", ""),
                        "published": item.get("providerPublishTime", ""),
                        "type": item.get("type", ""),
                    }
                )
            return results
    except Exception:
        return _news_search_intel_fallback(args.symbol)


# --------------- financials ---------------


def financials_a(symbol: str) -> dict:
    import akshare as ak

    try:
        df = _akshare_retry(ak.stock_financial_analysis_indicator, symbol=symbol)
        if df is None or df.empty:
            return {"symbol": symbol, "error": "No financial data"}
        r = df.iloc[0]
        return _clean_row(
            {
                "symbol": symbol,
                "report_date": r.get("日期"),
                "roe": r.get("净资产收益率(%)"),
                "net_profit_margin": r.get("销售净利率(%)"),
                "gross_margin": r.get("销售毛利率(%)"),
                "debt_ratio": r.get("资产负债率(%)"),
                "current_ratio": r.get("流动比率"),
            }
        )
    except Exception:
        return {"symbol": symbol, "note": "Financial data unavailable"}


def financials_yf(symbol: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(_yf_hk_symbol(symbol))
    info = t.info
    return _clean_row(
        {
            "symbol": symbol,
            "name": info.get("shortName"),
            "market_cap": info.get("marketCap"),
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "pb": info.get("priceToBook"),
            "total_revenue": info.get("totalRevenue"),
            "net_income": info.get("netIncomeToCommon"),
            "profit_margin": info.get("profitMargins"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "dividend_yield": info.get("dividendYield"),
        }
    )


def cmd_financials(args):
    market = detect_market(args.symbol)
    try:
        return financials_a(args.symbol) if market == "A" else financials_yf(args.symbol)
    except Exception as e:
        return {"error": str(e)}


# --------------- market_snapshot ---------------


def snapshot_a() -> list:
    try:
        return _snapshot_akshare()
    except Exception:
        pass
    try:
        import efinance as ef

        df = ef.stock.get_realtime_quotes()
        if df is None or df.empty:
            raise ValueError("efinance snapshot empty")
        col_map = {
            "股票代码": "symbol",
            "股票名称": "name",
            "最新价": "price",
            "涨跌幅": "change_pct",
            "涨跌额": "change",
            "成交量": "volume",
            "成交额": "turnover",
            "市盈率": "pe",
            "换手率": "turnover_rate",
            "最高": "high",
            "最低": "low",
            "今开": "open",
            "昨收": "prev_close",
        }
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]
        return [_clean_row(r) for r in df[keep].to_dict("records")]
    except Exception:
        return [{"error": "A-share snapshot unavailable from all sources"}]


def _snapshot_akshare() -> list:
    import akshare as ak

    df = _akshare_retry(ak.stock_zh_a_spot_em)
    col_map = {
        "代码": "symbol",
        "名称": "name",
        "最新价": "price",
        "涨跌幅": "change_pct",
        "涨跌额": "change",
        "成交量": "volume",
        "成交额": "turnover",
        "市盈率-动态": "pe",
        "市净率": "pb",
        "总市值": "market_cap",
        "换手率": "turnover_rate",
        "量比": "volume_ratio",
        "最高": "high",
        "最低": "low",
        "今开": "open",
        "昨收": "prev_close",
        "60日涨跌幅": "change_pct_60d",
    }
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    df = df[keep]
    return [_clean_row(r) for r in df.to_dict("records")]


def snapshot_hk() -> list:
    try:
        import akshare as ak

        df = _akshare_retry(ak.stock_hk_spot_em)
        col_map = {
            "代码": "symbol",
            "名称": "name",
            "最新价": "price",
            "涨跌幅": "change_pct",
            "成交量": "volume",
            "成交额": "turnover",
            "市盈率": "pe",
            "市净率": "pb",
            "总市值": "market_cap",
        }
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]
        return [_clean_row(r) for r in df[keep].to_dict("records")]
    except Exception:
        return [{"error": "HK snapshot unavailable"}]


def snapshot_us() -> list:
    try:
        import akshare as ak

        df = _akshare_retry(ak.stock_us_spot_em)
        col_map = {
            "代码": "symbol",
            "名称": "name",
            "最新价": "price",
            "涨跌幅": "change_pct",
            "成交量": "volume",
            "成交额": "turnover",
            "市盈率": "pe",
            "总市值": "market_cap",
        }
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]
        return [_clean_row(r) for r in df[keep].to_dict("records")]
    except Exception:
        return [{"error": "US snapshot unavailable"}]


def cmd_market_snapshot(args):
    try:
        m = args.market.upper()
        if m == "A":
            return snapshot_a()
        elif m == "HK":
            return snapshot_hk()
        elif m == "US":
            return snapshot_us()
        return {"error": f"Unknown market: {m}"}
    except Exception as e:
        return {"error": str(e)}


# --------------- market_indices ---------------

# region -> {index symbol -> display name}; CN is handled separately via akshare.
_REGION_INDEX_SYMBOLS = {
    "hk": {"^HSI": "恒生指数", "^HSCE": "恒生国企指数", "^HSTECH": "恒生科技指数"},
    "us": {"^DJI": "道琼斯", "^IXIC": "纳斯达克", "^GSPC": "标普500", "^RUT": "罗素2000"},
    "jp": {"^N225": "日经225", "^TOPX": "东证指数"},
    "kr": {"^KS11": "韩国KOSPI", "^KQ11": "韩国KOSDAQ"},
    "tw": {"^TWII": "台湾加权指数"},
}


def cmd_market_indices(args):
    region = args.region.lower()
    try:
        if region == "cn":
            import akshare as ak

            indices = [
                ("sh000001", "上证指数"),
                ("sz399001", "深证成指"),
                ("sz399006", "创业板指"),
                ("sh000688", "科创50"),
                ("sh000300", "沪深300"),
                ("sh000016", "上证50"),
            ]
            results = []
            try:
                df = _akshare_retry(ak.stock_zh_index_spot_em)
            except Exception:
                # eastmoney refuses datacenter/proxy IPs (RemoteDisconnected) — sina is the fallback
                df = _akshare_retry(ak.stock_zh_index_spot_sina)
            for code, name in indices:
                clean_code = code[2:] if code[:2] in ("sh", "sz") else code
                row = df[df["代码"] == clean_code]
                if row.empty:
                    row = df[df["代码"] == code]
                if row.empty:
                    results.append({"code": code, "name": name, "note": "data unavailable"})
                    continue
                r = row.iloc[0]
                results.append(
                    _clean_row(
                        {
                            "code": code,
                            "name": r.get("名称", name),
                            "price": r.get("最新价"),
                            "change": r.get("涨跌额"),
                            "change_pct": r.get("涨跌幅"),
                            "volume": r.get("成交量"),
                            "turnover": r.get("成交额"),
                            "high": r.get("最高"),
                            "low": r.get("最低"),
                            "open": r.get("今开"),
                            "prev_close": r.get("昨收"),
                        }
                    )
                )
            return results
        elif region in _REGION_INDEX_SYMBOLS:
            import yfinance as yf

            symbols = _REGION_INDEX_SYMBOLS[region]
            results = []
            for sym, name in symbols.items():
                try:
                    t = yf.Ticker(sym)
                    info = t.info
                    results.append(
                        _clean_row(
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
                            }
                        )
                    )
                except Exception:
                    results.append({"code": sym, "name": name, "note": "data unavailable"})
            return results
        return {"error": f"Unknown region: {region}"}
    except Exception as e:
        return {"error": str(e)}


# --------------- sector_rankings ---------------


def _sector_rankings_efinance(top: int, direction: str):
    """Fallback: fetch sector rankings via efinance."""
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
    if direction == "bottom":
        df = df.sort_values("change_pct", ascending=True).head(top)
    elif direction == "both":
        top_df = df.sort_values("change_pct", ascending=False).head(top)
        bottom_df = df.sort_values("change_pct", ascending=True).head(top)
        return {
            "top": [_clean_row(r) for r in top_df.to_dict("records")],
            "bottom": [_clean_row(r) for r in bottom_df.to_dict("records")],
        }
    else:
        df = df.sort_values("change_pct", ascending=False).head(top)
    return [_clean_row(r) for r in df.to_dict("records")]


def cmd_sector_rankings(args):
    try:
        import akshare as ak
        import pandas as pd

        df = _akshare_retry(ak.stock_board_industry_name_em)
        if df is None or df.empty:
            raise ValueError("akshare sector data unavailable")
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
            "领涨涨跌幅": "leading_change_pct",
        }
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]
        df = df[keep]
        if "change_pct" in df.columns:
            df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce")

        direction = getattr(args, "direction", "top")
        if direction == "bottom":
            df = df.sort_values("change_pct", ascending=True).head(args.top)
        elif direction == "both":
            top_df = df.sort_values("change_pct", ascending=False).head(args.top)
            bottom_df = df.sort_values("change_pct", ascending=True).head(args.top)
            return {
                "top": [_clean_row(r) for r in top_df.to_dict("records")],
                "bottom": [_clean_row(r) for r in bottom_df.to_dict("records")],
            }
        else:
            df = df.head(args.top)
        return [_clean_row(r) for r in df.to_dict("records")]
    except Exception:
        try:
            direction = getattr(args, "direction", "top")
            return _sector_rankings_efinance(args.top, direction)
        except Exception as e2:
            return {"error": str(e2)}


# --------------- sector constituents / stock sectors (issue #18) ---------------

# No repo-wide disk-cache convention exists yet; tempdir keeps this cross-platform
# (Windows has no ~/.cache). Sector membership changes slowly — 24h TTL.
_SECTOR_CACHE_DIR = Path(tempfile.gettempdir()) / "pi-stock-analysis"
_SECTOR_CACHE_TTL = 24 * 3600


def _sector_cache_get(key: str):
    try:
        path = _SECTOR_CACHE_DIR / f"{key}.json"
        if path.exists() and time.time() - path.stat().st_mtime < _SECTOR_CACHE_TTL:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _sector_cache_set(key: str, data):
    try:
        _SECTOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_SECTOR_CACHE_DIR / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
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

    if board_kind == "industry":
        name_fn, cons_fn = ak.stock_board_industry_name_em, ak.stock_board_industry_cons_em
    else:
        name_fn, cons_fn = ak.stock_board_concept_name_em, ak.stock_board_concept_cons_em
    names_df = _akshare_retry(name_fn)
    if names_df is None or names_df.empty or "板块名称" not in names_df.columns:
        raise ValueError(f"eastmoney {board_kind} board list unavailable")
    exact = _fuzzy_match_sector(sector, [str(n) for n in names_df["板块名称"].tolist()])
    if not exact:
        raise ValueError(f"no eastmoney {board_kind} board matching '{sector}'")
    df = _akshare_retry(cons_fn, symbol=exact)
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
    sector = (args.sector or "").strip()
    if not sector:
        return {"error": "sector name required"}
    board_type = getattr(args, "board_type", "auto")
    # Sector names are user input — hash them instead of sanitizing, so names that
    # differ only in whitespace/punctuation (创新药 vs 创新 药) can't collide.
    cache_key = hashlib.sha256(f"{sector}|{board_type}".encode()).hexdigest()[:16]
    cached = _sector_cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        result = sector_constituents_a(sector, board_type)
    except Exception as e:
        return {"error": str(e)}
    _sector_cache_set(cache_key, result)
    return result


def _board_entry(name, source: str, board_type: str) -> dict:
    """One stock→board membership record; keeps the {name, source, board_type}
    shape in one place instead of hand-building it per source."""
    return {"name": str(name), "source": source, "board_type": board_type}


def _stock_boards_em(symbol: str, info_map=None) -> list:
    """A-share industry board from eastmoney individual info. Pass an already-fetched
    info_map (item → value) to reuse it instead of hitting the network again."""
    if info_map is None:
        import akshare as ak

        df = _akshare_retry(ak.stock_individual_info_em, symbol=symbol)
        if df is None or df.empty:
            raise ValueError("eastmoney individual info unavailable")
        info_map = {row.iloc[0]: row.iloc[1] for _, row in df.iterrows()}
    industry = info_map.get("行业")
    # pandas NaN is truthy — only a real string counts as an industry name
    if not (isinstance(industry, str) and industry):
        raise ValueError("eastmoney individual info has no industry")
    return [_board_entry(industry, "eastmoney", "industry")]


def _stock_boards_efinance(symbol: str) -> list:
    """A-share concept/membership boards from efinance."""
    import efinance as ef

    df = ef.stock.get_belong_board(symbol)
    if df is None or df.empty or "板块名称" not in df.columns:
        raise ValueError("efinance belong-board unavailable")
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
        raise ValueError("xueqiu basic info unavailable")
    info_map = {row.iloc[0]: row.iloc[1] for _, row in df.iterrows()}
    # affiliate_industry is a nested dict: {'ind_code': 'BK0025', 'ind_name': '汽车整车'}
    industry = info_map.get("affiliate_industry")
    name = industry.get("ind_name") if isinstance(industry, dict) else None
    if not name:
        raise ValueError("xueqiu basic info has no industry")
    return [_board_entry(name, "xueqiu", "industry")]


def _stock_boards_from_cache(symbol: str) -> list:
    """Reverse lookup against the get_sector_constituents disk cache — the honest
    fallback when eastmoney blocks the caller's IP and xueqiu has no token (the two
    live A-share sources are both eastmoney-flavored). Reads every unexpired
    constituents payload in the cache dir and returns one entry per cached sector
    containing the symbol. Cold cache → []; malformed cache files are skipped
    (_sector_cache_get returns None on expiry, bad JSON, or read errors)."""
    boards = []
    for path in _SECTOR_CACHE_DIR.glob("*.json"):
        data = _sector_cache_get(path.stem)
        if not isinstance(data, dict):
            continue
        sector = data.get("sector")
        if not sector:
            continue
        for row in data.get("constituents") or []:
            if isinstance(row, dict) and str(row.get("code")) == symbol:
                boards.append(_board_entry(sector, "cache", data.get("board_type", "")))
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
    _akshare_retry — despite the name it is a generic fn(*args, retries=2, delay=1)
    wrapper. It only retries on exceptions; an empty info dict is a data problem,
    not a transient failure, so it is checked after the call and not retried."""
    import yfinance as yf

    # 5-digit inputs like 01801.HK 404 on Yahoo — normalize; the caller keeps the
    # user's original symbol for the result payload.
    yf_symbol = _yf_hk_symbol(symbol)
    info = _akshare_retry(lambda: yf.Ticker(yf_symbol).info) or {}
    sectors = [_board_entry(info[key], "yfinance", "gics") for key in ("sector", "industry") if info.get(key)]
    if not sectors:
        raise ValueError(f"no sector info for {symbol}")
    return sectors


def resolve_stock_sectors(symbol: str, info_map=None) -> dict:
    """Reverse map: stock → boards it belongs to (A/HK). A single source failing
    is not fatal — the other source's boards are still returned. An already-fetched
    eastmoney info_map is reused for the em source instead of re-fetching."""
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
    for name, fn in sources:
        try:
            sectors.extend(fn() or [])
        except Exception as e:
            errors.append(f"{name}: {e}")
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
            error += "; 东财不可用且缓存为空，可先跑 get_sector_constituents 预热或设置 XUEQIU_TOKEN"
        result["error"] = error
        return result
    result["sectors"] = deduped
    return result


# --------------- stock_info ---------------


def cmd_stock_info(args):
    market = detect_market(args.symbol)
    try:
        if market == "A":
            import akshare as ak

            result = {"symbol": args.symbol, "market": "A"}
            info_map = None
            try:
                df = _akshare_retry(ak.stock_individual_info_em, symbol=args.symbol)
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
            except Exception:
                pass
            try:
                # boards 来自个股→板块反向映射（resolve_stock_sectors 的核心逻辑）；
                # 之前的实现把股票代码传给 stock_board_industry_cons_em（它要的是板块名），
                # 永远抛异常被吞掉，是死代码。info_map 已拉过就透传复用，避免东财二次调用。
                resolved = resolve_stock_sectors(args.symbol, info_map=info_map)
                boards = [s["name"] for s in resolved.get("sectors", [])]
                if boards:
                    result["boards"] = boards[:10]
            except Exception:
                pass
            return _clean_row(result)
        else:
            import yfinance as yf

            t = yf.Ticker(_yf_hk_symbol(args.symbol))
            info = t.info
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
        return {"error": str(e)}


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
        return {"error": str(e)}


# --------------- market_stats ---------------


def compute_market_stats(data: list) -> dict:
    """Compute breadth/limit/turnover statistics from a market snapshot list."""
    import numpy as np

    changes = []
    turnovers = []
    limit_ups = []
    limit_downs = []
    up_count = down_count = flat_count = 0

    for s in data:
        chg = s.get("change_pct")
        if chg is not None:
            changes.append(float(chg))
            if chg > 0:
                up_count += 1
            elif chg < 0:
                down_count += 1
            else:
                flat_count += 1
        t = s.get("turnover")
        if t is not None:
            turnovers.append(float(t))

        price = s.get("price")
        prev = s.get("prev_close")
        sym = s.get("symbol", "")
        if price and prev and prev > 0:
            code_info = normalize_stock_code(sym)
            lp = code_info.get("limit_pct")
            if lp:
                limit_up = calc_limit_price(float(prev), lp, "up")
                limit_down = calc_limit_price(float(prev), lp, "down")
                if abs(float(price) - limit_up) < 0.01:
                    limit_ups.append({"symbol": sym, "name": s.get("name"), "change_pct": chg})
                elif abs(float(price) - limit_down) < 0.01:
                    limit_downs.append({"symbol": sym, "name": s.get("name"), "change_pct": chg})

    changes_arr = np.array(changes) if changes else np.array([0])
    sorted_data = sorted(data, key=lambda x: x.get("change_pct") or 0, reverse=True)

    return _clean_row(
        {
            "total_stocks": len(data),
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "limit_up_count": len(limit_ups),
            "limit_down_count": len(limit_downs),
            "avg_change_pct": round(float(changes_arr.mean()), 2),
            "median_change_pct": round(float(np.median(changes_arr)), 2),
            "total_turnover": round(sum(turnovers), 0) if turnovers else None,
            "top5_gainers": [
                {"symbol": s.get("symbol"), "name": s.get("name"), "change_pct": s.get("change_pct")}
                for s in sorted_data[:5]
            ],
            "top5_losers": [
                {"symbol": s.get("symbol"), "name": s.get("name"), "change_pct": s.get("change_pct")}
                for s in sorted_data[-5:]
            ],
            "limit_up_samples": limit_ups[:5],
            "limit_down_samples": limit_downs[:5],
        }
    )


def cmd_market_stats(args):
    try:
        data = snapshot_a()
        if isinstance(data, dict) and "error" in data:
            return data
        if not data:
            return {"error": "No market data"}
        return compute_market_stats(data)
    except Exception as e:
        return {"error": str(e)}


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
                "pb": quote.get("pb"),
                "market_cap": quote.get("market_cap"),
            }
        except Exception:
            result["valuation"] = {}

        try:
            df = _akshare_retry(ak.stock_financial_analysis_indicator, symbol=args.symbol)
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
        return {"error": str(e)}


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

    p_cf = sub.add_parser("capital_flow")
    p_cf.add_argument("symbol", nargs="?", default="")
    p_cf.add_argument("--mode", default="detail", choices=["detail", "summary", "sector_flow"])

    p_news = sub.add_parser("news")
    p_news.add_argument("symbol")
    p_news.add_argument("--days", type=int, default=3)

    p_fin = sub.add_parser("financials")
    p_fin.add_argument("symbol")

    p_snap = sub.add_parser("market_snapshot")
    p_snap.add_argument("--market", default="A", choices=["A", "HK", "US"])

    p_idx = sub.add_parser("market_indices")
    p_idx.add_argument("--region", default="cn", choices=["cn", "hk", "us", "jp", "kr", "tw"])

    p_sec = sub.add_parser("sector_rankings")
    p_sec.add_argument("--top", type=int, default=10)
    p_sec.add_argument("--direction", default="top", choices=["top", "bottom", "both"])

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
    p_stats.add_argument("--market", default="A", choices=["A"])

    p_fund = sub.add_parser("fundamental_context")
    p_fund.add_argument("symbol")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "kline": cmd_kline,
        "quote": cmd_quote,
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
    }
    # Provider libraries (baostock etc.) print prose to stdout on failure,
    # which would corrupt the JSON-only contract — divert that noise to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        result = dispatch[args.command](args)
    output(result)


if __name__ == "__main__":
    # Windows defaults stdio to a legacy code page (cp1252) that cannot encode the
    # Chinese text these tools emit — force UTF-8 so stdout never crashes there.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    main()
