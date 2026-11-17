"""Tests for margin_trading / northbound_flow in tools/stock_data.py (A-share only)."""

import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tools import stock_data
from tools.stock_data import cmd_margin_trading, cmd_northbound_flow


def _rpta(symbol="600519", dates=("2026-10-30", "2026-10-29")):
    """RPTA_WEB_RZRQ_GGMX rows as eastmoney datacenter returns them, newest first."""
    return [
        {
            "DATE": f"{d} 00:00:00",
            "SCODE": symbol,
            "SECNAME": "贵州茅台",
            "RZYE": 1.5e10,
            "RZMRE": 3e8,
            "RZCHE": 2.5e8,
            "RQYL": 1000,
            "RQMCL": 100,
            "RQCHL": 50,
            "RQYE": 1.6e6,
            "RZRQYE": 1.5016e10,
        }
        for d in dates
    ]


@pytest.fixture
def datacenter(monkeypatch):
    calls = []

    def install(rows=None, error=None):
        def fake(report, filter_, sort, size, order="-1", url=None):
            calls.append({"report": report, "filter": filter_, "sort": sort, "size": size})
            if error:
                raise error
            return len(rows or []), (rows or [])[:size]

        monkeypatch.setattr(stock_data, "_em_datacenter", fake)
        return calls

    return install


class TestMarginTrading:
    def test_one_server_filtered_query_and_fields(self, datacenter):
        calls = datacenter(_rpta())
        result = cmd_margin_trading(Namespace(symbol="600519", days=2))
        assert calls == [{"report": "RPTA_WEB_RZRQ_GGMX", "filter": '(SCODE="600519")', "sort": "DATE", "size": 2}]
        assert [r["date"] for r in result] == ["2026-10-30", "2026-10-29"]
        row = result[0]
        assert row["symbol"] == "600519"
        assert row["name"] == "贵州茅台"
        assert row["margin_balance"] == 1.5e10
        assert row["margin_buy"] == 3e8
        assert row["margin_repay"] == 2.5e8
        assert row["short_balance_shares"] == 1000
        assert row["short_sell_shares"] == 100
        assert row["short_repay_shares"] == 50
        assert row["short_balance"] == 1.6e6
        assert row["total_balance"] == 1.5016e10

    def test_prefixed_symbol_accepted(self, datacenter):
        calls = datacenter(_rpta("000001"))
        result = cmd_margin_trading(Namespace(symbol="sz000001", days=1))
        assert calls[0]["filter"] == '(SCODE="000001")'
        assert result[0]["symbol"] == "000001"

    def test_days_clamped_to_60(self, datacenter):
        calls = datacenter(_rpta())
        cmd_margin_trading(Namespace(symbol="600519", days=500))
        assert calls[0]["size"] == 60

    def test_non_a_share_rejected(self, datacenter):
        calls = datacenter(_rpta())
        assert "error" in cmd_margin_trading(Namespace(symbol="AAPL", days=10))
        assert calls == []

    def test_invalid_days_rejected(self, datacenter):
        calls = datacenter(_rpta())
        assert "error" in cmd_margin_trading(Namespace(symbol="600519", days=0))
        assert calls == []

    def test_not_a_margin_underlying_is_clean_error(self, datacenter):
        datacenter([])
        result = cmd_margin_trading(Namespace(symbol="600999", days=4))
        assert result["symbol"] == "600999"
        assert "两融标的" in result["error"]

    def test_provider_failure_is_clean_error(self, datacenter):
        datacenter(error=ConnectionError("eastmoney down"))
        result = cmd_margin_trading(Namespace(symbol="600519", days=4))
        assert "eastmoney down" in result["error"]


def test_margin_buy_ratio_uses_matching_day_turnover(monkeypatch, datacenter):
    datacenter(_rpta(dates=("2026-10-30", "2026-10-29")))
    monkeypatch.setattr(stock_data, "_kline_akshare", lambda *a, **k: [{"date": "2026-10-30", "turnover": 1.2e9}])
    rows = cmd_margin_trading(Namespace(symbol="600519", days=2, include_ratio=True))
    assert rows[0]["margin_buy_ratio_pct"] == 25
    assert rows[0]["turnover"] == 1.2e9
    assert rows[1]["margin_buy_ratio_pct"] is None
    assert rows[1]["margin_buy_ratio_note"] == "matching-date CNY turnover unavailable"


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
