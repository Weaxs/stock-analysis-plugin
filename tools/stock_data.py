#!/usr/bin/env python3
"""Stock market data fetcher for A-shares (akshare), HK and US (yfinance)."""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta


def detect_market(symbol: str) -> str:
    if symbol.upper().endswith(".HK"):
        return "HK"
    if re.match(r"^\d{6}$", symbol):
        return "A"
    return "US"


def normalize_stock_code(symbol: str) -> dict:
    """Classify A-share stock code into board/type with limit-up/down ratio."""
    info = {"market": detect_market(symbol), "board": "main", "is_st": False,
            "is_etf": False, "limit_pct": 0.10}
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


def _akshare_retry(fn, *args, retries=2, delay=1):
    for attempt in range(retries + 1):
        try:
            return fn(*args)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(delay)


def _to_baostock_code(symbol: str) -> str:
    if symbol.startswith(("600", "601", "603", "605", "688", "689",
                          "510", "512", "513", "515", "516", "518",
                          "560", "588")):
        return f"sh.{symbol}"
    return f"sz.{symbol}"


def _kline_efinance(symbol: str, period: str, count: int) -> list:
    import efinance as ef
    freq_map = {"daily": 101, "weekly": 102, "monthly": 103}
    df = ef.stock.get_quote_history(symbol, klt=freq_map.get(period, 101))
    if df is None or df.empty:
        raise ValueError("efinance returned empty data")
    col_map = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
               "最低": "low", "成交量": "volume", "成交额": "turnover",
               "振幅": "amplitude", "涨跌幅": "change_pct", "换手率": "turnover_rate"}
    df = df.rename(columns=col_map)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = __import__("pandas").to_numeric(df[c], errors="coerce")
    keep = [c for c in ["date", "open", "high", "low", "close", "volume", "turnover",
                         "change_pct", "turnover_rate"] if c in df.columns]
    df = df[keep].tail(count)
    return [_clean_row(r) for r in df.to_dict("records")]


def _kline_baostock(symbol: str, period: str, count: int) -> list:
    import baostock as bs
    import pandas as pd
    freq_map = {"daily": "d", "weekly": "w", "monthly": "m"}
    end = datetime.now()
    start = end - timedelta(days=count * 7 if period == "weekly" else count * 31 if period == "monthly" else count * 2)
    lg = bs.login()
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
    col_map = {"股票代码": "symbol", "股票名称": "name", "最新价": "price",
               "涨跌额": "change", "涨跌幅": "change_pct", "成交量": "volume",
               "成交额": "turnover", "最高": "high", "最低": "low",
               "今开": "open", "昨收": "prev_close", "总市值": "market_cap",
               "市盈率": "pe", "换手率": "turnover_rate"}
    result = {}
    for cn_key, en_key in col_map.items():
        val = r.get(cn_key)
        if val is not None and val != "":
            try:
                val = float(val) if en_key not in ("symbol", "name") else val
            except (ValueError, TypeError):
                pass
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
    try:
        return _kline_akshare(symbol, period, count)
    except Exception:
        pass
    try:
        return _kline_efinance(symbol, period, count)
    except Exception:
        pass
    return _kline_baostock(symbol, period, count)


def _kline_akshare(symbol: str, period: str, count: int) -> list:
    import akshare as ak

    period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
    end = datetime.now()
    start = end - timedelta(days=count * 7 if period == "weekly" else count * 31 if period == "monthly" else count * 2)

    df = _akshare_retry(ak.stock_zh_a_hist,
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
    try:
        return _quote_akshare(symbol)
    except Exception:
        return _quote_efinance(symbol)


def _quote_akshare(symbol: str) -> dict:
    import akshare as ak

    df = _akshare_retry(ak.stock_zh_a_spot_em)
    row = df[df["代码"] == symbol]
    if row.empty:
        raise ValueError(f"Symbol {symbol} not found in akshare")
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
        "amplitude": r.get("振幅"), "change_pct_60d": r.get("60日涨跌幅"),
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
    mode = getattr(args, "mode", "detail")

    if mode == "sector_flow":
        try:
            import akshare as ak
            df = _akshare_retry(ak.stock_sector_fund_flow_rank)
            if df is None or df.empty:
                return {"error": "Sector fund flow data unavailable"}
            col_map = {"名称": "name", "今日涨跌幅": "change_pct",
                        "今日主力净流入-净额": "main_net_inflow",
                        "今日主力净流入-净占比": "main_pct",
                        "今日超大单净流入-净额": "super_large_net",
                        "今日大单净流入-净额": "large_net"}
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
        col_map = {"日期": "date", "主力净流入-净额": "main_net_inflow",
                    "主力净流入-净占比": "main_pct",
                    "超大单净流入-净额": "super_large_net",
                    "大单净流入-净额": "large_net",
                    "中单净流入-净额": "medium_net",
                    "小单净流入-净额": "small_net"}
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
    except Exception as e:
        return {"error": str(e)}


# --------------- news ---------------

def cmd_news(args):
    market = detect_market(args.symbol)
    try:
        if market == "A":
            import akshare as ak

            df = _akshare_retry(ak.stock_news_em, symbol=args.symbol)
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
        else:
            import yfinance as yf
            t = yf.Ticker(args.symbol)
            news = t.news
            if not news:
                return []
            results = []
            for item in news[:20]:
                results.append({
                    "title": item.get("title", ""),
                    "publisher": item.get("publisher", ""),
                    "url": item.get("link", ""),
                    "published": item.get("providerPublishTime", ""),
                    "type": item.get("type", ""),
                })
            return results
    except Exception as e:
        return {"error": str(e)}


# --------------- financials ---------------

def financials_a(symbol: str) -> dict:
    import akshare as ak

    try:
        df = _akshare_retry(ak.stock_financial_analysis_indicator, symbol=symbol)
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
    try:
        return _snapshot_akshare()
    except Exception:
        pass
    try:
        import efinance as ef
        df = ef.stock.get_realtime_quotes()
        if df is None or df.empty:
            raise ValueError("efinance snapshot empty")
        col_map = {"股票代码": "symbol", "股票名称": "name", "最新价": "price",
                   "涨跌幅": "change_pct", "涨跌额": "change",
                   "成交量": "volume", "成交额": "turnover",
                   "市盈率": "pe", "换手率": "turnover_rate",
                   "最高": "high", "最低": "low", "今开": "open", "昨收": "prev_close"}
        df = df.rename(columns=col_map)
        keep = [c for c in col_map.values() if c in df.columns]
        return [_clean_row(r) for r in df[keep].to_dict("records")]
    except Exception:
        return [{"error": "A-share snapshot unavailable from all sources"}]


def _snapshot_akshare() -> list:
    import akshare as ak

    df = _akshare_retry(ak.stock_zh_a_spot_em)
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
        df = _akshare_retry(ak.stock_hk_spot_em)
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
        df = _akshare_retry(ak.stock_us_spot_em)
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


# --------------- market_indices ---------------

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
            df = _akshare_retry(ak.stock_zh_index_spot_em)
            for code, name in indices:
                clean_code = code[2:] if code[:2] in ("sh", "sz") else code
                row = df[df["代码"] == clean_code]
                if row.empty:
                    row = df[df["代码"] == code]
                if row.empty:
                    results.append({"code": code, "name": name, "note": "data unavailable"})
                    continue
                r = row.iloc[0]
                results.append(_clean_row({
                    "code": code, "name": r.get("名称", name),
                    "price": r.get("最新价"), "change": r.get("涨跌额"),
                    "change_pct": r.get("涨跌幅"), "volume": r.get("成交量"),
                    "turnover": r.get("成交额"), "high": r.get("最高"),
                    "low": r.get("最低"), "open": r.get("今开"),
                    "prev_close": r.get("昨收"),
                }))
            return results
        elif region in ("hk", "us"):
            import yfinance as yf
            if region == "hk":
                symbols = {"^HSI": "恒生指数", "^HSCE": "恒生国企指数", "^HSTECH": "恒生科技指数"}
            else:
                symbols = {"^DJI": "道琼斯", "^IXIC": "纳斯达克", "^GSPC": "标普500", "^RUT": "罗素2000"}
            results = []
            for sym, name in symbols.items():
                try:
                    t = yf.Ticker(sym)
                    info = t.info
                    results.append(_clean_row({
                        "code": sym, "name": name,
                        "price": info.get("regularMarketPrice"),
                        "change": info.get("regularMarketChange"),
                        "change_pct": info.get("regularMarketChangePercent"),
                        "volume": info.get("regularMarketVolume"),
                        "high": info.get("regularMarketDayHigh"),
                        "low": info.get("regularMarketDayLow"),
                        "open": info.get("regularMarketOpen"),
                        "prev_close": info.get("regularMarketPreviousClose"),
                    }))
                except Exception:
                    results.append({"code": sym, "name": name, "note": "data unavailable"})
            return results
        return {"error": f"Unknown region: {region}"}
    except Exception as e:
        return {"error": str(e)}


# --------------- sector_rankings ---------------

def cmd_sector_rankings(args):
    try:
        import akshare as ak
        import pandas as pd
        df = _akshare_retry(ak.stock_board_industry_name_em)
        if df is None or df.empty:
            return {"error": "Sector data unavailable"}
        col_map = {"板块名称": "name", "板块代码": "code",
                    "最新价": "price", "涨跌幅": "change_pct",
                    "成交量": "volume", "成交额": "turnover",
                    "换手率": "turnover_rate",
                    "总市值": "market_cap",
                    "上涨家数": "up_count", "下跌家数": "down_count",
                    "领涨股票": "leading_stock", "领涨涨跌幅": "leading_change_pct"}
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
    except Exception as e:
        return {"error": str(e)}


# --------------- stock_info ---------------

def cmd_stock_info(args):
    market = detect_market(args.symbol)
    try:
        if market == "A":
            import akshare as ak
            result = {"symbol": args.symbol, "market": "A"}
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
                board_df = _akshare_retry(ak.stock_board_industry_cons_em, symbol=args.symbol)
                if board_df is not None and not board_df.empty and "板块名称" in board_df.columns:
                    result["boards"] = board_df["板块名称"].tolist()[:10]
            except Exception:
                pass
            return _clean_row(result)
        else:
            import yfinance as yf
            t = yf.Ticker(args.symbol)
            info = t.info
            return _clean_row({
                "symbol": args.symbol, "market": market,
                "name": info.get("shortName"),
                "industry": info.get("industry"),
                "sector": info.get("sector"),
                "country": info.get("country"),
                "exchange": info.get("exchange"),
                "market_cap": info.get("marketCap"),
                "employees": info.get("fullTimeEmployees"),
                "website": info.get("website"),
                "description": (info.get("longBusinessSummary") or "")[:200],
            })
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
            "日期": "date", "获利比例": "profit_ratio",
            "平均成本": "avg_cost", "90%成本": "cost_90pct",
            "90成本-低": "cost_90_low", "90成本-高": "cost_90_high",
            "70%成本": "cost_70pct",
            "70成本-低": "cost_70_low", "70成本-高": "cost_70_high",
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

def cmd_market_stats(args):
    try:
        import numpy as np
        data = snapshot_a()
        if isinstance(data, dict) and "error" in data:
            return data
        if not data:
            return {"error": "No market data"}

        prices = []
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

        return _clean_row({
            "total_stocks": len(data),
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "limit_up_count": len(limit_ups),
            "limit_down_count": len(limit_downs),
            "avg_change_pct": round(float(changes_arr.mean()), 2),
            "median_change_pct": round(float(np.median(changes_arr)), 2),
            "total_turnover": round(sum(turnovers), 0) if turnovers else None,
            "top5_gainers": [{"symbol": s.get("symbol"), "name": s.get("name"), "change_pct": s.get("change_pct")} for s in sorted_data[:5]],
            "top5_losers": [{"symbol": s.get("symbol"), "name": s.get("name"), "change_pct": s.get("change_pct")} for s in sorted_data[-5:]],
            "limit_up_samples": limit_ups[:5],
            "limit_down_samples": limit_downs[:5],
        })
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
                    r1 = df.iloc[1]
                    growth = {}
                    for field, key in [("主营业务收入增长率(%)", "revenue_yoy"),
                                       ("净利润增长率(%)", "net_income_yoy")]:
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
                    dividends.append(_clean_row({
                        "year": row.get("报告期"),
                        "plan": row.get("分红方案"),
                        "record_date": row.get("股权登记日"),
                    }))
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
    p_idx.add_argument("--region", default="cn", choices=["cn", "hk", "us"])

    p_sec = sub.add_parser("sector_rankings")
    p_sec.add_argument("--top", type=int, default=10)
    p_sec.add_argument("--direction", default="top", choices=["top", "bottom", "both"])

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
        "stock_info": cmd_stock_info,
        "chip_distribution": cmd_chip_distribution,
        "market_stats": cmd_market_stats,
        "fundamental_context": cmd_fundamental_context,
    }
    result = dispatch[args.command](args)
    output(result)


if __name__ == "__main__":
    main()
