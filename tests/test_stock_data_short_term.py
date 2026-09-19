"""Tests for the short-term sentiment tools in tools/stock_data.py:
limit_up_pool, dragon_tiger, hot_stocks, sector_rankings --board-type."""

import sys
from argparse import Namespace
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tools.stock_data import cmd_dragon_tiger, cmd_hot_stocks, cmd_limit_up_pool, cmd_sector_rankings


@pytest.fixture(autouse=True)
def _sticky_isolation(tmp_path, monkeypatch):
    """The sector_rankings/dragon_tiger/hot_stocks chains are sticky (persisted in the
    tempdir disk cache) — isolate per test so mocked winners never leak into real CLI runs."""
    monkeypatch.setattr("tools.stock_data._DATA_CACHE_DIR", tmp_path)


def _zt_pool_df():
    return pd.DataFrame(
        {
            "代码": ["000001", "600519"],
            "名称": ["平安银行", "贵州茅台"],
            "涨跌幅": [9.98, 10.0],
            "最新价": [11.5, 1800.0],
            "成交额": [1.5e9, 5.0e9],
            "流通市值": [2.0e11, 2.2e12],
            "总市值": [2.1e11, 2.26e12],
            "换手率": [3.5, 0.5],
            "封板资金": [1.0e8, 5.0e8],
            "首次封板时间": ["093000", "100000"],
            "最后封板时间": ["093000", "143000"],
            "炸板次数": [0, 2],
            "涨停统计": ["1/1", "3/3"],
            "连板数": [1, 3],
            "所属行业": ["银行", "白酒"],
        }
    )


class TestLimitUpPool:
    def test_column_mapping_and_summary(self):
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = _zt_pool_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_limit_up_pool(Namespace(date="20240102"))
        assert result["date"] == "20240102"
        assert result["count"] == 2
        assert result["max_consecutive_boards"] == 3
        row = result["pool"][1]
        assert row["code"] == "600519"
        assert row["name"] == "贵州茅台"
        assert row["seal_amount"] == 5.0e8
        assert row["first_seal_time"] == "100000"
        assert row["break_count"] == 2
        assert row["consecutive_boards"] == 3
        assert row["industry"] == "白酒"
        mock_ak.stock_zt_pool_em.assert_called_once_with(date="20240102")

    def test_empty_is_note_not_error(self):
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = pd.DataFrame()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_limit_up_pool(Namespace(date="20240101"))
        assert result["count"] == 0
        assert result["pool"] == []
        assert "note" in result
        assert "error" not in result

    def test_failure_is_error(self):
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.side_effect = ConnectionError("boom")
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_limit_up_pool(Namespace(date="20240102"))
        assert "error" in result


class TestLimitUpPoolDateResolution:
    """Issue #31: stock_zt_pool_em silently serves the latest trading day's pool for
    non-trading/future dates — the tool must resolve and label the real data date."""

    def test_weekend_resolves_to_last_friday_with_stale_label(self, monkeypatch):
        # 2024-01-06 was a Saturday; empty calendar set → weekday-only approximation
        monkeypatch.setattr("trading_calendar.cn_trade_dates", lambda year: set())
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = _zt_pool_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_limit_up_pool(Namespace(date="20240106"))
        mock_ak.stock_zt_pool_em.assert_called_once_with(date="20240105")
        assert result["date"] == "20240105"
        assert result["requested_date"] == "20240106"
        assert result["stale"] is True
        assert "note" in result
        assert result["count"] == 2

    def test_holiday_resolution_uses_calendar_across_year_boundary(self, monkeypatch):
        # 2024-01-01 (Monday) was New Year's Day → last trading day is 2023-12-29 (Friday)
        monkeypatch.setattr(
            "trading_calendar.cn_trade_dates",
            lambda year: {"2023-12-29"} if year == 2023 else {"2024-01-02", "2024-01-03"},
        )
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = _zt_pool_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_limit_up_pool(Namespace(date="20240101"))
        mock_ak.stock_zt_pool_em.assert_called_once_with(date="20231229")
        assert result["date"] == "20231229"
        assert result["requested_date"] == "20240101"
        assert result["stale"] is True

    def test_future_date_clamps_to_today_then_resolves(self, monkeypatch):
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2024, 1, 6, 12, 0)  # Saturday noon

        monkeypatch.setattr("tools.stock_data.datetime", _FixedDatetime)
        monkeypatch.setattr("trading_calendar.cn_trade_dates", lambda year: set())
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = _zt_pool_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_limit_up_pool(Namespace(date="20240109"))  # next Tuesday, still in the future
        mock_ak.stock_zt_pool_em.assert_called_once_with(date="20240105")
        assert result["date"] == "20240105"
        assert result["requested_date"] == "20240109"
        assert result["stale"] is True

    def test_trading_day_carries_no_stale_label(self, monkeypatch):
        monkeypatch.setattr("trading_calendar.cn_trade_dates", lambda year: set())
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = _zt_pool_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_limit_up_pool(Namespace(date="20240102"))  # Tuesday
        assert result["date"] == "20240102"
        assert "requested_date" not in result
        assert "stale" not in result

    def test_stale_and_empty_pool_keeps_labeling(self, monkeypatch):
        monkeypatch.setattr("trading_calendar.cn_trade_dates", lambda year: set())
        mock_ak = MagicMock()
        mock_ak.stock_zt_pool_em.return_value = pd.DataFrame()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_limit_up_pool(Namespace(date="20240106"))
        assert result["date"] == "20240105"
        assert result["count"] == 0
        assert result["pool"] == []
        assert result["stale"] is True
        assert "note" in result

    def test_invalid_date_is_clean_error(self):
        result = cmd_limit_up_pool(Namespace(date="2024-13-40"))
        assert "error" in result


def _lhb_df():
    return pd.DataFrame(
        {
            "代码": ["000001", "600519", "300750"],
            "名称": ["平安银行", "贵州茅台", "宁德时代"],
            "上榜日": ["2024-01-02"] * 3,
            "收盘价": [11.5, 1800.0, 160.0],
            "涨跌幅": [9.98, -5.0, 10.0],
            "龙虎榜净买额": [1.0e8, -3.0e8, 2.0e8],
            "龙虎榜买入额": [2.0e8, 1.0e8, 3.0e8],
            "龙虎榜卖出额": [1.0e8, 4.0e8, 1.0e8],
            "龙虎榜成交额": [3.0e8, 5.0e8, 4.0e8],
            "上榜原因": ["涨幅偏离", "跌幅偏离", "涨幅偏离"],
            "解读": ["买入积极", "卖出积极", "买入积极"],
        }
    )


class TestDragonTiger:
    def test_sorted_by_abs_net_buy_and_mapped(self):
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value = _lhb_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_dragon_tiger(Namespace(date="2024-01-02", symbol=None, top=20))
        assert result["date"] == "2024-01-02"
        assert result["count"] == 3
        # |net_buy|: 3e8 > 2e8 > 1e8
        assert [r["code"] for r in result["items"]] == ["600519", "300750", "000001"]
        row = result["items"][0]
        assert row["net_buy"] == -3.0e8
        assert row["reason"] == "跌幅偏离"
        assert row["list_date"] == "2024-01-02"
        mock_ak.stock_lhb_detail_em.assert_called_once_with(start_date="20240102", end_date="20240102")

    def test_symbol_filter_and_top(self):
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value = _lhb_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_dragon_tiger(Namespace(date=None, symbol="sz000001", top=1))
        assert result["count"] == 1
        assert result["items"][0]["code"] == "000001"

    def test_empty_is_note_not_error(self):
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value = pd.DataFrame()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_dragon_tiger(Namespace(date="2024-01-01", symbol=None, top=20))
        assert result["count"] == 0
        assert result["items"] == []
        assert "note" in result
        assert "error" not in result

    def test_failure_is_error(self):
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.side_effect = ConnectionError("boom")
        mock_ak.stock_lhb_detail_daily_sina.side_effect = ConnectionError("boom")
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_dragon_tiger(Namespace(date=None, symbol=None, top=20))
        assert "error" in result


def _lhb_sina_df():
    return pd.DataFrame(
        {
            "序号": [1, 2],
            "股票代码": ["000001", "600519"],
            "股票名称": ["平安银行", "贵州茅台"],
            "收盘价": [11.5, 1800.0],
            "对应值": [7.5, 8.1],
            "成交量": [1000, 2000],
            "成交额": [1.0e8, 2.0e8],
            "指标": ["日涨幅偏离值达7%", "日跌幅偏离值达7%"],
        }
    )


class TestDragonTigerFailover:
    def test_em_down_sina_mapped(self):
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.side_effect = ConnectionError("em down")
        mock_ak.stock_lhb_detail_daily_sina.return_value = _lhb_sina_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_dragon_tiger(Namespace(date="2024-01-02", symbol=None, top=20))
        assert result["source"] == "sina"
        assert result["count"] == 2
        row = result["items"][0]
        # sina has no 涨跌幅/净买额/解读 — those keys are absent
        assert row == {
            "code": "000001",
            "name": "平安银行",
            "close": 11.5,
            "reason": "日涨幅偏离值达7%",
            "list_date": "2024-01-02",
        }
        mock_ak.stock_lhb_detail_daily_sina.assert_called_once_with(date="20240102")

    def test_em_success_sina_not_called(self):
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value = _lhb_df()
        mock_ak.stock_lhb_detail_daily_sina.side_effect = AssertionError("sina must not be called")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_dragon_tiger(Namespace(date="2024-01-02", symbol=None, top=20))
        assert result["source"] == "eastmoney"
        assert result["count"] == 3

    def test_symbol_filter_applies_to_sina(self):
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.side_effect = ConnectionError("em down")
        mock_ak.stock_lhb_detail_daily_sina.return_value = _lhb_sina_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_dragon_tiger(Namespace(date="2024-01-02", symbol="sh600519", top=20))
        assert result["count"] == 1
        assert result["items"][0]["code"] == "600519"

    def test_all_empty_is_note(self):
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value = pd.DataFrame()
        mock_ak.stock_lhb_detail_daily_sina.return_value = pd.DataFrame()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_dragon_tiger(Namespace(date="2024-01-01", symbol=None, top=20))
        assert result["count"] == 0
        assert "note" in result
        assert "error" not in result


class TestDragonTigerDateResolution:
    """Issue #31: same non-trading/future date resolution and stale labeling as
    limit_up_pool, keeping the two short-term tools aligned."""

    def test_weekend_resolves_to_last_friday_with_stale_label(self, monkeypatch):
        monkeypatch.setattr("trading_calendar.cn_trade_dates", lambda year: set())
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value = _lhb_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_dragon_tiger(Namespace(date="2024-01-06", symbol=None, top=20))  # Saturday
        mock_ak.stock_lhb_detail_em.assert_called_once_with(start_date="20240105", end_date="20240105")
        assert result["date"] == "2024-01-05"
        assert result["requested_date"] == "2024-01-06"
        assert result["stale"] is True
        assert result["count"] == 3

    def test_trading_day_carries_no_stale_label(self, monkeypatch):
        monkeypatch.setattr("trading_calendar.cn_trade_dates", lambda year: set())
        mock_ak = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value = _lhb_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_dragon_tiger(Namespace(date="2024-01-02", symbol=None, top=20))  # Tuesday
        assert result["date"] == "2024-01-02"
        assert "requested_date" not in result
        assert "stale" not in result

    def test_invalid_date_is_clean_error(self):
        result = cmd_dragon_tiger(Namespace(date="not-a-date", symbol=None, top=20))
        assert "error" in result


class TestHotStocks:
    def test_prefix_stripped_and_code_full_kept(self):
        mock_ak = MagicMock()
        mock_ak.stock_hot_rank_em.return_value = pd.DataFrame(
            {
                "当前排名": [1, 2],
                "代码": ["SZ000001", "SH600519"],
                "股票名称": ["平安银行", "贵州茅台"],
                "最新价": [11.5, 1800.0],
                "涨跌幅": [1.2, -0.5],
            }
        )
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_hot_stocks(Namespace(top=20))
        assert result["count"] == 2
        assert result["items"][0] == {
            "rank": 1,
            "code": "000001",
            "name": "平安银行",
            "price": 11.5,
            "change_pct": 1.2,
            "code_full": "SZ000001",
        }
        assert result["items"][1]["code"] == "600519"
        assert result["items"][1]["code_full"] == "SH600519"

    def test_top_truncation(self):
        mock_ak = MagicMock()
        mock_ak.stock_hot_rank_em.return_value = pd.DataFrame(
            {
                "当前排名": [1, 2, 3],
                "代码": ["SZ000001", "SH600519", "SZ300750"],
                "股票名称": ["a", "b", "c"],
                "最新价": [1.0, 2.0, 3.0],
                "涨跌幅": [1.0, 2.0, 3.0],
            }
        )
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_hot_stocks(Namespace(top=2))
        assert result["count"] == 2

    def test_failure_is_error(self):
        mock_ak = MagicMock()
        mock_ak.stock_hot_rank_em.side_effect = ConnectionError("boom")
        mock_ak.stock_hot_follow_xq.side_effect = ConnectionError("boom")
        mock_ak.stock_hot_search_baidu.side_effect = ConnectionError("boom")
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_hot_stocks(Namespace(top=20))
        assert "error" in result


class TestHotStocksFailover:
    def test_em_down_xq_mapped(self):
        mock_ak = MagicMock()
        mock_ak.stock_hot_rank_em.side_effect = ConnectionError("em down")
        mock_ak.stock_hot_follow_xq.return_value = pd.DataFrame(
            {
                "股票代码": ["SH600519", "SZ000001"],
                "股票简称": ["贵州茅台", "平安银行"],
                "关注": [3721227, 1000000],
                "最新价": [1800.0, 11.5],
            }
        )
        mock_ak.stock_hot_search_baidu.side_effect = AssertionError("baidu must not be called")
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_hot_stocks(Namespace(top=20))
        assert result["source"] == "xueqiu"
        # xq: rank from row position, code prefix stripped, no change_pct column
        assert result["items"][0] == {
            "rank": 1,
            "code": "600519",
            "code_full": "SH600519",
            "name": "贵州茅台",
            "price": 1800.0,
        }
        assert "change_pct" not in result["items"][0]

    def test_em_xq_down_baidu_mapped(self):
        mock_ak = MagicMock()
        mock_ak.stock_hot_rank_em.side_effect = ConnectionError("em down")
        mock_ak.stock_hot_follow_xq.side_effect = ConnectionError("xq down")
        mock_ak.stock_hot_search_baidu.return_value = pd.DataFrame(
            {
                "名称/代码": ["中天科技", "金健米业"],
                "涨跌幅": ["-1.13%", "+9.98%"],
                "综合热度": [1088000, 1008000],
            }
        )
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_hot_stocks(Namespace(top=20))
        assert result["source"] == "baidu"
        # baidu: no code/price columns; 涨跌幅 % suffix parsed to float
        assert result["items"] == [
            {"rank": 1, "name": "中天科技", "change_pct": -1.13},
            {"rank": 2, "name": "金健米业", "change_pct": 9.98},
        ]

    def test_em_success_fallbacks_not_called(self):
        mock_ak = MagicMock()
        mock_ak.stock_hot_rank_em.return_value = pd.DataFrame(
            {
                "当前排名": [1],
                "代码": ["SZ000001"],
                "股票名称": ["平安银行"],
                "最新价": [11.5],
                "涨跌幅": [1.2],
            }
        )
        mock_ak.stock_hot_follow_xq.side_effect = AssertionError("xq must not be called")
        mock_ak.stock_hot_search_baidu.side_effect = AssertionError("baidu must not be called")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_hot_stocks(Namespace(top=20))
        assert result["source"] == "eastmoney"
        assert result["count"] == 1


def _industry_board_df():
    return pd.DataFrame(
        {
            "板块名称": ["半导体", "银行"],
            "板块代码": ["BK1036", "BK0475"],
            "最新价": [1000.0, 900.0],
            "涨跌幅": [3.5, 0.5],
            "成交量": [100, 200],
            "成交额": [1.0e9, 2.0e9],
            "换手率": [2.0, 1.0],
            "总市值": [1.0e12, 2.0e12],
            "上涨家数": [80, 30],
            "下跌家数": [20, 10],
            "领涨股票": ["中芯国际", "招商银行"],
            "领涨涨跌幅": [10.0, 2.0],
        }
    )


def _concept_board_df():
    df = _industry_board_df().rename(columns={"领涨涨跌幅": "领涨股票-涨跌幅"})
    df["板块名称"] = ["人工智能", "新能源车"]
    return df


class TestSectorRankingsBoardType:
    def test_industry_default_unchanged_plus_board_type(self):
        mock_ak = MagicMock()
        mock_ak.stock_board_industry_name_em.return_value = _industry_board_df()
        mock_ak.stock_board_concept_name_em.side_effect = AssertionError("concept api must not be called")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_sector_rankings(Namespace(top=10, direction="top", board_type="industry"))
        assert [r["name"] for r in result] == ["半导体", "银行"]
        assert all(r["board_type"] == "industry" for r in result)
        assert result[0]["leading_change_pct"] == 10.0

    def test_concept_routes_to_concept_api(self):
        mock_ak = MagicMock()
        mock_ak.stock_board_concept_name_em.return_value = _concept_board_df()
        mock_ak.stock_board_industry_name_em.side_effect = AssertionError("industry api must not be called")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_sector_rankings(Namespace(top=10, direction="top", board_type="concept"))
        assert [r["name"] for r in result] == ["人工智能", "新能源车"]
        assert all(r["board_type"] == "concept" for r in result)
        # concept boards name the column 领涨股票-涨跌幅
        assert result[0]["leading_change_pct"] == 10.0

    def test_concept_failure_has_no_efinance_fallback(self):
        mock_ak = MagicMock()
        mock_ak.stock_board_concept_name_em.side_effect = ConnectionError("boom")
        mock_ak.stock_sector_spot.side_effect = ConnectionError("boom")
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            patch("tools.stock_data.time.sleep"),
            patch("tools.stock_data._sector_rankings_efinance") as mock_ef,
        ):
            result = cmd_sector_rankings(Namespace(top=10, direction="top", board_type="concept"))
        assert "error" in result
        mock_ef.assert_not_called()


def _industry_board_ths_df():
    return pd.DataFrame(
        {
            "序号": [1, 2],
            "板块": ["半导体", "银行"],
            "涨跌幅": [3.5, 0.5],
            "总成交量": [100, 200],
            "总成交额": [1.0e9, 2.0e9],
            "净流入": [1.0e8, -1.0e8],
            "上涨家数": [80, 30],
            "下跌家数": [20, 10],
            "均价": [10.0, 9.0],
            "领涨股": ["中芯国际", "招商银行"],
            "领涨股-最新价": [100.0, 40.0],
            "领涨股-涨跌幅": [10.0, 2.0],
        }
    )


class TestSectorRankingsIndustryFailover:
    def test_em_down_ths_mapped(self):
        mock_ak = MagicMock()
        mock_ak.stock_board_industry_name_em.side_effect = ConnectionError("em down")
        mock_ak.stock_board_industry_summary_ths.return_value = _industry_board_ths_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_sector_rankings(Namespace(top=10, direction="top", board_type="industry"))
        assert [r["name"] for r in result] == ["半导体", "银行"]
        row = result[0]
        assert row["source"] == "ths"
        assert row["board_type"] == "industry"
        assert row["net_inflow"] == 1.0e8
        assert row["up_count"] == 80
        assert row["leading_stock"] == "中芯国际"
        assert row["leading_change_pct"] == 10.0

    def test_em_ths_down_efinance_leg(self):
        mock_ak = MagicMock()
        mock_ak.stock_board_industry_name_em.side_effect = ConnectionError("em down")
        mock_ak.stock_board_industry_summary_ths.side_effect = ConnectionError("ths down")
        with (
            patch.dict(sys.modules, {"akshare": mock_ak}),
            patch("tools.stock_data.time.sleep"),
            patch("tools.stock_data._sector_rankings_efinance") as mock_ef,
        ):
            mock_ef.return_value = [{"name": "半导体", "change_pct": 3.5}]
            result = cmd_sector_rankings(Namespace(top=5, direction="top", board_type="industry"))
        # efinance is a regular failover leg — uniform source/board_type tagging
        assert result == [{"name": "半导体", "change_pct": 3.5, "source": "efinance", "board_type": "industry"}]

    def test_em_success_ths_not_called(self):
        mock_ak = MagicMock()
        mock_ak.stock_board_industry_name_em.return_value = _industry_board_df()
        mock_ak.stock_board_industry_summary_ths.side_effect = AssertionError("ths must not be called")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_sector_rankings(Namespace(top=10, direction="top", board_type="industry"))
        assert all(r["source"] == "eastmoney" for r in result)


def _concept_board_sina_df():
    return pd.DataFrame(
        {
            "label": ["gn_ai", "gn_ev"],
            "板块": ["人工智能", "新能源车"],
            "公司家数": [100, 200],
            "平均价格": [10.0, 20.0],
            "涨跌额": [0.5, -0.3],
            "涨跌幅": [2.5, -1.5],
            "总成交量": [1000, 2000],
            "总成交额": [1.0e9, 2.0e9],
            "股票代码": ["300001", "600001"],
            "个股-涨跌幅": [9.0, -5.0],
            "个股-当前价": [30.0, 15.0],
            "个股-涨跌额": [2.0, -0.8],
            "股票名称": ["龙头股A", "龙头股B"],
        }
    )


class TestSectorRankingsConceptFailover:
    def test_em_down_sina_mapped(self):
        mock_ak = MagicMock()
        mock_ak.stock_board_concept_name_em.side_effect = ConnectionError("em down")
        mock_ak.stock_sector_spot.return_value = _concept_board_sina_df()
        with patch.dict(sys.modules, {"akshare": mock_ak}), patch("tools.stock_data.time.sleep"):
            result = cmd_sector_rankings(Namespace(top=10, direction="top", board_type="concept"))
        # sina spot has no explicit ordering — sorted by change_pct desc after failover
        assert [r["name"] for r in result] == ["人工智能", "新能源车"]
        row = result[0]
        assert row["source"] == "sina"
        assert row["board_type"] == "concept"
        assert row["stock_count"] == 100
        assert row["leading_stock"] == "龙头股A"
        assert row["leading_change_pct"] == 9.0
        mock_ak.stock_sector_spot.assert_called_once_with(indicator="概念")

    def test_em_success_sina_not_called(self):
        mock_ak = MagicMock()
        mock_ak.stock_board_concept_name_em.return_value = _concept_board_df()
        mock_ak.stock_sector_spot.side_effect = AssertionError("sina must not be called")
        with patch.dict(sys.modules, {"akshare": mock_ak}):
            result = cmd_sector_rankings(Namespace(top=10, direction="top", board_type="concept"))
        assert all(r["source"] == "eastmoney" for r in result)
