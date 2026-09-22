"""Tests for margin_trading / northbound_flow in tools/stock_data.py (A-share only)."""

import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pandas as pd

from tools.stock_data import cmd_margin_trading, cmd_northbound_flow
from tools.trading_calendar import prev_trading_days


def _real_trade_days(n: int) -> list:
    """Real prev_trading_days under the mocked-akshare context.

    akshare is patched to a MagicMock in every caller, so the sina calendar fetch
    yields nothing and cn_trade_dates degrades to its offline weekend-only
    approximation — deterministic and network-free."""
    return prev_trading_days("CN", n)


def _sse_detail(date: str):
    """One exchange-day payload (all stocks) as akshare stock_margin_detail_sse returns it."""
    return pd.DataFrame(
        {
            "信用交易日期": [date, date],
            "标的证券代码": ["600519", "601318"],
            "标的证券简称": ["贵州茅台", "中国平安"],
            "融资余额": [2.0e10, 1.5e10],
            "融资买入额": [3.0e8, 2.0e8],
            "融资偿还额": [2.5e8, 1.8e8],
            "融券余量": [1.0e5, 2.0e5],
            "融券卖出量": [1.0e4, 2.0e4],
            "融券偿还量": [9.0e3, 1.5e4],
        }
    )


def _szse_detail():
    return pd.DataFrame(
        {
            "证券代码": ["000001", "000002"],
            "证券简称": ["平安银行", "万科A"],
            "融资买入额": [1.0e8, 2.0e8],
            "融资余额": [5.0e9, 8.0e9],
            "融券卖出量": [1.0e4, 2.0e4],
            "融券余量": [5.0e5, 6.0e5],
            "融券余额": [6.0e6, 7.0e6],
            "融资融券余额": [5.006e9, 8.007e9],
        }
    )


class TestMarginTrading:
    def test_sse_routing_and_fields(self):
        mock_ak = MagicMock()
        mock_ak.stock_margin_detail_sse.side_effect = lambda date: _sse_detail(date)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            expected_day = _real_trade_days(1)[0]
            result = cmd_margin_trading(Namespace(symbol="600519", days=1))
        assert isinstance(result, list)
        assert len(result) == 1
        row = result[0]
        assert row["date"] == expected_day
        assert row["symbol"] == "600519"
        assert row["name"] == "贵州茅台"
        assert row["margin_balance"] == 2.0e10
        assert row["margin_buy"] == 3.0e8
        assert row["margin_repay"] == 2.5e8
        assert row["short_balance_shares"] == 1.0e5
        assert row["short_sell_shares"] == 1.0e4
        assert row["short_repay_shares"] == 9.0e3
        mock_ak.stock_margin_detail_sse.assert_called_with(date=expected_day.replace("-", ""))
        mock_ak.stock_margin_detail_szse.assert_not_called()

    def test_szse_routing_and_fields(self):
        mock_ak = MagicMock()
        mock_ak.stock_margin_detail_szse.side_effect = lambda date: _szse_detail()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            expected_day = _real_trade_days(1)[0]
            result = cmd_margin_trading(Namespace(symbol="000001", days=1))
        assert len(result) == 1
        row = result[0]
        assert row["date"] == expected_day
        assert row["symbol"] == "000001"
        assert row["margin_balance"] == 5.0e9
        assert row["short_balance"] == 6.0e6
        assert row["total_balance"] == 5.006e9
        assert "margin_repay" not in row
        mock_ak.stock_margin_detail_szse.assert_called_with(date=expected_day.replace("-", ""))
        mock_ak.stock_margin_detail_sse.assert_not_called()

    def test_prefixed_symbol_accepted(self):
        mock_ak = MagicMock()
        mock_ak.stock_margin_detail_sse.side_effect = lambda date: _sse_detail(date)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_margin_trading(Namespace(symbol="sh600519", days=1))
        assert isinstance(result, list)
        assert result[0]["symbol"] == "600519"

    def test_calendar_wiring_market_and_days(self):
        """cmd → trading_calendar wiring: CN calendar, days passed through (unclamped)."""
        mock_ak = MagicMock()
        mock_ak.stock_margin_detail_sse.side_effect = lambda date: _sse_detail(date)
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            patch("trading_calendar.prev_trading_days", wraps=prev_trading_days) as cal_spy,
        ):
            result = cmd_margin_trading(Namespace(symbol="600519", days=3))
        cal_spy.assert_called_once_with("CN", 3)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_newest_first_across_days(self):
        mock_ak = MagicMock()
        mock_ak.stock_margin_detail_sse.side_effect = lambda date: _sse_detail(date)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            expected_days = _real_trade_days(4)
            result = cmd_margin_trading(Namespace(symbol="600519", days=4))
        assert [r["date"] for r in result] == expected_days
        assert mock_ak.stock_margin_detail_sse.call_count == 4

    def test_latest_day_not_published_is_skipped(self):
        """两融明细 T 日晚间才发布：上午跑时最近一天必抛（SSE Length mismatch /
        SZSE read_excel 解析异常）— 跳过该天继续，返回其余天序列而非整单 error。"""
        mock_ak = MagicMock()
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            days = _real_trade_days(4)
            latest_raw = days[0].replace("-", "")

            def detail(date: str):
                if date == latest_raw:
                    raise ValueError("Length mismatch")
                return _sse_detail(date)

            mock_ak.stock_margin_detail_sse.side_effect = detail
            result = cmd_margin_trading(Namespace(symbol="600519", days=4))
        assert isinstance(result, list)
        assert [r["date"] for r in result] == days[1:]

    def test_provider_failure_all_days_is_clean_error(self):
        mock_ak = MagicMock()
        mock_ak.stock_margin_detail_sse.side_effect = ConnectionError("boom")
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_margin_trading(Namespace(symbol="600519", days=3))
        assert isinstance(result, dict)
        assert "error" in result

    def test_days_clamped_to_60(self):
        """--days 每天一次 akshare 调用；超过 60 钳制到 60，避免慢源下击穿宿主 120s 子进程预算。"""
        mock_ak = MagicMock()
        mock_ak.stock_margin_detail_sse.side_effect = lambda date: _sse_detail(date)
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            patch("trading_calendar.prev_trading_days", wraps=prev_trading_days) as cal_spy,
        ):
            result = cmd_margin_trading(Namespace(symbol="600519", days=250))
        cal_spy.assert_called_once_with("CN", 60)
        assert isinstance(result, list)
        assert len(result) == 60

    def test_non_a_share_rejected(self):
        mock_ak = MagicMock()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_margin_trading(Namespace(symbol="AAPL", days=10))
        assert "error" in result
        mock_ak.stock_margin_detail_sse.assert_not_called()

    def test_bse_symbol_rejected(self):
        mock_ak = MagicMock()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_margin_trading(Namespace(symbol="920001", days=10))
        assert "error" in result
        mock_ak.stock_margin_detail_sse.assert_not_called()
        mock_ak.stock_margin_detail_szse.assert_not_called()

    def test_not_a_margin_underlying_is_clean_error(self):
        mock_ak = MagicMock()
        mock_ak.stock_margin_detail_sse.side_effect = lambda date: _sse_detail(date)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_margin_trading(Namespace(symbol="600999", days=4))
        assert isinstance(result, dict)
        assert "error" in result
        assert result["symbol"] == "600999"

    def test_invalid_days_rejected(self):
        result = cmd_margin_trading(Namespace(symbol="600519", days=0))
        assert "error" in result

    def test_concurrent_fetch_out_of_order_still_newest_first(self):
        """各日抓取经 ThreadPoolExecutor 并发（max_workers=5）；乱序完成/乱序返回时
        输出仍按日期降序，保持"最新在前"契约，不依赖执行器返回顺序。"""
        mock_ak = MagicMock()
        mock_ak.stock_margin_detail_sse.side_effect = lambda date: _sse_detail(date)
        days = _real_trade_days(4)
        used = {}

        class ReverseExecutor:
            """逆序跑、逆序返回的同步执行器 — 模拟并发下乱序完成/乱序返回。"""

            def __init__(self, max_workers=None, **kwargs):
                used["max_workers"] = max_workers

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def map(self, fn, items):
                items = list(items)
                results = {d: fn(d) for d in reversed(items)}  # 乱序完成
                return [results[d] for d in reversed(items)]  # 乱序返回

        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            patch("concurrent.futures.ThreadPoolExecutor", ReverseExecutor),
        ):
            result = cmd_margin_trading(Namespace(symbol="600519", days=4))
        assert used.get("max_workers") == 5  # 并发执行且限流 5
        assert [r["date"] for r in result] == days  # 最新在前，不受完成顺序影响

    def test_concurrent_fetch_keeps_per_day_isolation(self):
        """并发下保留 per-day 故障隔离：单日抛错跳过，其余天正常返回。"""
        mock_ak = MagicMock()
        days = _real_trade_days(4)
        latest_raw = days[0].replace("-", "")

        def detail(date: str):
            if date == latest_raw:
                raise ValueError("Length mismatch")
            return _sse_detail(date)

        mock_ak.stock_margin_detail_sse.side_effect = detail
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_margin_trading(Namespace(symbol="600519", days=4))
        assert isinstance(result, list)
        assert [r["date"] for r in result] == days[1:]


class TestNorthboundFlow:
    def _hist_df(self, n=15):
        dates = pd.date_range("2024-01-01", periods=n).strftime("%Y-%m-%d")
        return pd.DataFrame(
            {
                "日期": list(dates),
                "当日成交净买额": [float(i) for i in range(n)],
                "买入成交额": [100.0 + i for i in range(n)],
                "卖出成交额": [90.0 + i for i in range(n)],
                "历史累计净买额": [1000.0 + i for i in range(n)],
                "持股市值": [2.0e4 + i for i in range(n)],
            }
        )

    def _shell_df(self, n=5, hold_zero=True):
        """停披期空壳行：日期齐全，数据字段全 NaN；现网还会把 持股市值 回填 0.0
        （hold_zero=True 模拟该 payload —— 0 是缺失哨兵，北向持仓市值不可能为 0）。"""
        dates = pd.date_range("2026-09-16", periods=n).strftime("%Y-%m-%d")
        return pd.DataFrame(
            {
                "日期": list(dates),
                "当日成交净买额": [float("nan")] * n,
                "买入成交额": [float("nan")] * n,
                "卖出成交额": [float("nan")] * n,
                "历史累计净买额": [float("nan")] * n,
                "持股市值": [0.0 if hold_zero else float("nan")] * n,
            }
        )

    def test_column_mapping_newest_first_and_tail(self):
        mock_ak = MagicMock()
        mock_ak.stock_hsgt_hist_em.return_value = self._hist_df(15)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_northbound_flow(Namespace(days=10))
        mock_ak.stock_hsgt_hist_em.assert_called_once_with(symbol="北向资金")
        assert isinstance(result, list)
        assert len(result) == 10
        assert result[0]["date"] == "2024-01-15"  # newest first
        assert result[0]["net_buy"] == 14.0
        assert result[0]["buy_turnover"] == 114.0
        assert result[0]["sell_turnover"] == 104.0
        assert result[0]["accum_net_buy"] == 1014.0
        assert result[0]["hold_market_cap"] == 2.0e4 + 14
        assert result[-1]["date"] == "2024-01-06"

    def test_empty_is_clean_error(self):
        mock_ak = MagicMock()
        mock_ak.stock_hsgt_hist_em.return_value = pd.DataFrame()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_northbound_flow(Namespace(days=10))
        assert isinstance(result, dict)
        assert "error" in result

    def test_provider_failure_is_clean_error(self):
        mock_ak = MagicMock()
        mock_ak.stock_hsgt_hist_em.side_effect = ConnectionError("boom")
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_northbound_flow(Namespace(days=10))
        assert isinstance(result, dict)
        assert "error" in result

    def test_invalid_days_rejected(self):
        result = cmd_northbound_flow(Namespace(days=0))
        assert "error" in result

    def _mixed_df(self):
        """停披前后的混合序列（升序，与现网一致）：2024-08-16 及之前行数据完整；
        季末行仅 持股市值 有值；其余停披期行为空壳（流量字段全 NaN、持股市值 0.0）。"""
        return pd.DataFrame(
            {
                "日期": ["2024-08-14", "2024-08-15", "2024-09-30", "2024-10-08"],
                "当日成交净买额": [-71.6644, 122.0584, float("nan"), float("nan")],
                "买入成交额": [344.1149, 566.6319, float("nan"), float("nan")],
                "卖出成交额": [415.7793, 444.5735, float("nan"), float("nan")],
                "历史累计净买额": [1.756, 1.768, float("nan"), float("nan")],
                "持股市值": [1.889e12, 1.916e12, 1.95e12, 0.0],
            }
        )

    def test_shell_rows_filtered_and_history_returned(self):
        """空壳行（date 之外全 None，含 持股市值=0.0 哨兵）被过滤；仅 持股市值 的
        季末行保留；历史行正常返回、仍最新在前。"""
        mock_ak = MagicMock()
        mock_ak.stock_hsgt_hist_em.return_value = self._mixed_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_northbound_flow(Namespace(days=4))
        assert isinstance(result, list)
        assert [r["date"] for r in result] == ["2024-09-30", "2024-08-15", "2024-08-14"]
        quarter_end = result[0]
        assert quarter_end["net_buy"] is None
        assert quarter_end["hold_market_cap"] == 1.95e12
        assert result[1]["net_buy"] == 122.0584
        assert result[2]["net_buy"] == -71.6644

    def test_all_shell_rows_is_clean_error(self):
        """窗口内全是空壳 → 干净 error，文案说明 2024-08-16 起停止披露、加大 days 可取历史。"""
        mock_ak = MagicMock()
        mock_ak.stock_hsgt_hist_em.return_value = self._shell_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_northbound_flow(Namespace(days=5))
        assert isinstance(result, dict)
        assert "error" in result
        assert "2024-08-16" in result["error"]
        assert "停止披露" in result["error"]

    def test_all_nan_rows_is_clean_error(self):
        """空壳判定不依赖 持股市值 回填 0.0：全 NaN 行同样是空壳。"""
        mock_ak = MagicMock()
        mock_ak.stock_hsgt_hist_em.return_value = self._shell_df(hold_zero=False)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_northbound_flow(Namespace(days=5))
        assert isinstance(result, dict)
        assert "error" in result
        assert "2024-08-16" in result["error"]

    def test_recent_shell_window_errors_even_when_history_exists(self):
        """完整序列含历史数据，但 tail(days) 近期窗口全是空壳时仍报错（提示加大 days）。"""
        mock_ak = MagicMock()
        mock_ak.stock_hsgt_hist_em.return_value = pd.concat([self._hist_df(3), self._shell_df(5)], ignore_index=True)
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_northbound_flow(Namespace(days=5))
        assert isinstance(result, dict)
        assert "error" in result
        assert "2024-08-16" in result["error"]
