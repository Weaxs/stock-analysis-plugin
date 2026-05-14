#!/usr/bin/env python3
"""Stock market data fetcher for A-shares (akshare), HK and US (yfinance)."""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta


def detect_market(symbol: str) -> str:
    if symbol.upper().endswith(".HK"):
        return "HK"
    if re.match(r"^\d{6}$", symbol):
        return "A"
    return "US"


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
    import akshare as ak
    import pandas as pd

    period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
    end = datetime.now()
    start = end - timedelta(days=count * 7 if period == "weekly" else count * 31 if period == "monthly" else count * 2)

    df = ak.stock_zh_a_hist(
        symbol=symbol,
        period=period_map.get(period, "daily"),
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="qfq",
    )
    col_map = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
               "最低": "low", "成交量": "volume", "成交额": "turnover",
               "振幅": "amplitude", "涨跌幅": "change_pct", "涨跌额": "change",
               "换手率": "turnover_rate"}
    df = df.rename(columns=col_map)
    keep = [c for c in ["date", "open", "high", "low", "close", "volume", "turnover", "change_pct", "turnover_rate"] if c in df.columns]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


def kline_yf(symbol: str, period: str, count: int) -> list:
    import yfinance as yf

    period_map = {"daily": "1d", "weekly": "1wk", "monthly": "1mo"}
    days = count * 2 if period == "daily" else count * 10 if period == "weekly" else count * 35
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    df = yf.download(symbol, start=start, interval=period_map.get(period, "1d"), progress=False, auto_adjust=True)
    if df.empty:
        return []
    df = df.reset_index()
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    col_map = {"Date": "date", "Datetime": "date", "Open": "open", "High": "high",
               "Low": "low", "Close": "close", "Volume": "volume"}
    df = df.rename(columns=col_map)
    keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


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
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    row = df[df["代码"] == symbol]
    if row.empty:
        return {"error": f"Symbol {symbol} not found"}
    r = row.iloc[0]
    return _clean_row({
        "symbol": symbol, "name": r.get("名称"),
        "price": r.get("最新价"), "change": r.get("涨跌额"),
        "change_pct": r.get("涨跌幅"), "volume": r.get("成交量"),
        "turnover": r.get("成交额"), "high": r.get("最高"),
        "low": r.get("最低"), "open": r.get("今开"),
        "prev_close": r.get("昨收"), "market_cap": r.get("总市值"),
        "pe": r.get("市盈率-动态"), "pb": r.get("市净率"),
        "turnover_rate": r.get("换手率"), "volume_ratio": r.get("量比"),
    })


def quote_yf(symbol: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(symbol)
    info = t.info
    if not info or "regularMarketPrice" not in info:
        return {"error": f"No data for {symbol}"}
    return _clean_row({
        "symbol": symbol, "name": info.get("shortName"),
        "price": info.get("regularMarketPrice") or info.get("currentPrice"),
        "change": info.get("regularMarketChange"),
        "change_pct": info.get("regularMarketChangePercent"),
        "volume": info.get("regularMarketVolume"),
        "high": info.get("regularMarketDayHigh"),
        "low": info.get("regularMarketDayLow"),
        "open": info.get("regularMarketOpen"),
        "prev_close": info.get("regularMarketPreviousClose"),
        "market_cap": info.get("marketCap"),
        "pe": info.get("trailingPE"), "pb": info.get("priceToBook"),
    })


def cmd_quote(args):
    market = detect_market(args.symbol)
    try:
        return quote_a(args.symbol) if market == "A" else quote_yf(args.symbol)
    except Exception as e:
        return {"error": str(e)}


# --------------- capital_flow ---------------

def cmd_capital_flow(args):
    if detect_market(args.symbol) != "A":
        return {"error": "capital_flow only available for A-shares"}
    try:
        import akshare as ak

        market = "sh" if args.symbol.startswith("6") else "sz"
        df = ak.stock_individual_fund_flow(stock=args.symbol, market=market)
        col_map = {"日期": "date", "主力净流入-净额": "main_net_inflow",
                    "主力净流入-净占比": "main_pct",
                    "超大单净流入-净额": "super_large_net",
                    "大单净流入-净额": "large_net",
                    "中单净流入-净额": "medium_net",
                    "小单净流入-净额": "small_net"}
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]
        df = df[keep].tail(10)
        return [_clean_row(r) for r in df.to_dict("records")]
    except Exception as e:
        return {"error": str(e)}


# --------------- news ---------------

def cmd_news(args):
    try:
        import akshare as ak

        if detect_market(args.symbol) == "A":
            df = ak.stock_news_em(symbol=args.symbol)
            if df is None or df.empty:
                return []
            col_map = {"新闻标题": "title", "发布时间": "datetime",
                        "新闻来源": "source", "新闻链接": "url",
                        "新闻内容": "content"}
            df = df.rename(columns=col_map)
            keep = [c for c in col_map.values() if c in df.columns]
            cutoff = datetime.now() - timedelta(days=args.days)
            df = df[keep]
            if "datetime" in df.columns:
                import pandas as pd
                df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
                df = df[df["datetime"] >= cutoff]
            return [_clean_row(r) for r in df.head(20).to_dict("records")]
        return [{"note": f"News for non-A-shares ({args.symbol}) not yet supported via akshare"}]
    except Exception as e:
        return {"error": str(e)}


# --------------- financials ---------------

def financials_a(symbol: str) -> dict:
    import akshare as ak

    try:
        df = ak.stock_financial_analysis_indicator(symbol=symbol)
        if df is None or df.empty:
            return {"symbol": symbol, "error": "No financial data"}
        r = df.iloc[0]
        return _clean_row({
            "symbol": symbol,
            "report_date": r.get("日期"),
            "roe": r.get("净资产收益率(%)"),
            "net_profit_margin": r.get("销售净利率(%)"),
            "gross_margin": r.get("销售毛利率(%)"),
            "debt_ratio": r.get("资产负债率(%)"),
            "current_ratio": r.get("流动比率"),
        })
    except Exception:
        return {"symbol": symbol, "note": "Financial data unavailable"}


def financials_yf(symbol: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(symbol)
    info = t.info
    return _clean_row({
        "symbol": symbol, "name": info.get("shortName"),
        "market_cap": info.get("marketCap"),
        "pe": info.get("trailingPE"), "forward_pe": info.get("forwardPE"),
        "pb": info.get("priceToBook"),
        "total_revenue": info.get("totalRevenue"),
        "net_income": info.get("netIncomeToCommon"),
        "profit_margin": info.get("profitMargins"),
        "roe": info.get("returnOnEquity"),
        "debt_to_equity": info.get("debtToEquity"),
        "dividend_yield": info.get("dividendYield"),
    })


def cmd_financials(args):
    market = detect_market(args.symbol)
    try:
        return financials_a(args.symbol) if market == "A" else financials_yf(args.symbol)
    except Exception as e:
        return {"error": str(e)}


# --------------- market_snapshot ---------------

def snapshot_a() -> list:
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    col_map = {"代码": "symbol", "名称": "name", "最新价": "price",
               "涨跌幅": "change_pct", "涨跌额": "change",
               "成交量": "volume", "成交额": "turnover",
               "市盈率-动态": "pe", "市净率": "pb",
               "总市值": "market_cap", "换手率": "turnover_rate",
               "量比": "volume_ratio", "最高": "high", "最低": "low",
               "今开": "open", "昨收": "prev_close",
               "60日涨跌幅": "change_pct_60d"}
    df = df.rename(columns=col_map)
    keep = [c for c in col_map.values() if c in df.columns]
    df = df[keep]
    return [_clean_row(r) for r in df.to_dict("records")]


def snapshot_hk() -> list:
    try:
        import akshare as ak
        df = ak.stock_hk_spot_em()
        col_map = {"代码": "symbol", "名称": "name", "最新价": "price",
                    "涨跌幅": "change_pct", "成交量": "volume",
                    "成交额": "turnover", "市盈率": "pe", "市净率": "pb",
                    "总市值": "market_cap"}
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]
        return [_clean_row(r) for r in df[keep].to_dict("records")]
    except Exception:
        return [{"error": "HK snapshot unavailable"}]


def snapshot_us() -> list:
    try:
        import akshare as ak
        df = ak.stock_us_spot_em()
        col_map = {"代码": "symbol", "名称": "name", "最新价": "price",
                    "涨跌幅": "change_pct", "成交量": "volume",
                    "成交额": "turnover", "市盈率": "pe",
                    "总市值": "market_cap"}
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
    p_cf.add_argument("symbol")

    p_news = sub.add_parser("news")
    p_news.add_argument("symbol")
    p_news.add_argument("--days", type=int, default=3)

    p_fin = sub.add_parser("financials")
    p_fin.add_argument("symbol")

    p_snap = sub.add_parser("market_snapshot")
    p_snap.add_argument("--market", default="A", choices=["A", "HK", "US"])

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
    }
    result = dispatch[args.command](args)
    output(result)


if __name__ == "__main__":
    main()
