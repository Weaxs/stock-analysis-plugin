import pytest

from tools.stock_data import calc_limit_price, detect_market, normalize_stock_code


class TestDetectMarket:
    def test_a_share_6_digit(self):
        assert detect_market("600519") == "A"
        assert detect_market("000001") == "A"
        assert detect_market("300750") == "A"
        assert detect_market("688981") == "A"

    def test_a_share_explicit_exchange_prefix(self):
        assert detect_market("sh000001") == "A"
        assert detect_market("SZ399006") == "A"

    def test_hk(self):
        assert detect_market("0700.HK") == "HK"
        assert detect_market("9988.hk") == "HK"

    def test_us(self):
        assert detect_market("AAPL") == "US"
        assert detect_market("TSLA") == "US"
        assert detect_market("MSFT") == "US"

    def test_jp(self):
        assert detect_market("7203.T") == "JP"
        assert detect_market("6758.t") == "JP"

    def test_kr(self):
        assert detect_market("005930.KS") == "KR"
        assert detect_market("035720.KQ") == "KR"

    def test_tw(self):
        assert detect_market("2330.TW") == "TW"
        assert detect_market("6510.TWO") == "TW"


class TestNormalizeStockCode:
    def test_main_board(self):
        info = normalize_stock_code("600519")
        assert info["market"] == "A"
        assert info["board"] == "main"
        assert info["limit_pct"] == 0.10

    def test_star_board(self):
        info = normalize_stock_code("688981")
        assert info["board"] == "STAR"
        assert info["limit_pct"] == 0.20

    def test_chinext(self):
        info = normalize_stock_code("300750")
        assert info["board"] == "ChiNext"
        assert info["limit_pct"] == 0.20

    def test_bse(self):
        info = normalize_stock_code("430047")
        assert info["board"] == "BSE"
        assert info["limit_pct"] == 0.30

    def test_etf(self):
        info = normalize_stock_code("510300")
        assert info["board"] == "ETF"
        assert info["is_etf"] is True
        assert info["limit_pct"] is None

    def test_us_no_limit(self):
        info = normalize_stock_code("AAPL")
        assert info["market"] == "US"
        assert info["limit_pct"] is None

    def test_hk_no_limit(self):
        info = normalize_stock_code("0700.HK")
        assert info["market"] == "HK"
        assert info["limit_pct"] is None


class TestCalcLimitPrice:
    def test_limit_up_10pct(self):
        result = calc_limit_price(10.0, 0.10, "up")
        assert abs(result - 11.0) < 0.01

    def test_limit_down_10pct(self):
        result = calc_limit_price(10.0, 0.10, "down")
        assert abs(result - 9.0) < 0.01

    def test_limit_up_20pct(self):
        result = calc_limit_price(10.0, 0.20, "up")
        assert abs(result - 12.0) < 0.01

    def test_limit_down_20pct(self):
        result = calc_limit_price(10.0, 0.20, "down")
        assert abs(result - 8.0) < 0.01

    def test_exchange_rounding(self):
        result = calc_limit_price(13.57, 0.10, "up")
        assert abs(result - 14.93) < 0.01

    def test_exact_half_cent_uses_decimal_half_up(self):
        assert calc_limit_price(1.15, 0.10, "up") == 1.27
        assert calc_limit_price(1.45, 0.30, "down") == 1.02


class TestComputeMarketStats:
    def test_main_board_st_uses_five_percent_for_limit_up_and_down(self):
        from tools.stock_data import compute_market_stats

        result = compute_market_stats(
            [
                {"symbol": "600001", "name": "ST 示例", "price": 10.5, "prev_close": 10, "change_pct": 5},
                {"symbol": "000002", "name": "*ST 示例", "price": 9.5, "prev_close": 10, "change_pct": -5},
            ]
        )
        assert result["limit_up_count"] == 1
        assert result["limit_down_count"] == 1
        assert result["limit_up_samples"][0]["symbol"] == "600001"
        assert result["limit_down_samples"][0]["symbol"] == "000002"

    @pytest.mark.parametrize(
        ("symbol", "name", "price", "change_pct", "expected_up", "expected_down"),
        [
            ("600519", "普通主板", 11, 10, 1, 0),
            ("000001", "普通主板", 9, -10, 0, 1),
            ("300001", "ST 创业板", 12, 20, 1, 0),
            ("688001", "*ST 科创板", 8, -20, 0, 1),
            ("430047", "ST 北交所", 13, 30, 1, 0),
        ],
    )
    def test_regular_and_non_main_board_limit_rules_are_unchanged(
        self, symbol, name, price, change_pct, expected_up, expected_down
    ):
        from tools.stock_data import compute_market_stats

        result = compute_market_stats(
            [{"symbol": symbol, "name": name, "price": price, "prev_close": 10, "change_pct": change_pct}]
        )
        assert result["limit_up_count"] == expected_up
        assert result["limit_down_count"] == expected_down


class TestDetectMarketEdgeCases:
    def test_empty_string_falls_back_to_us(self):
        assert detect_market("") == "US"

    def test_bse_codes_are_a_share(self):
        assert detect_market("830799") == "A"
        assert detect_market("430047") == "A"
        assert detect_market("920001") == "A"

    def test_prefixed_codes(self):
        assert detect_market("sh600519") == "A"
        assert detect_market("bj920001") == "A"


class TestNormalizeStockCodeEdgeCases:
    def test_empty_string(self):
        info = normalize_stock_code("")
        assert info["market"] == "US"  # detect_market fallback for unrecognized input
        assert info["limit_pct"] is None

    def test_bse_8_prefix(self):
        info = normalize_stock_code("830799")
        assert info["board"] == "BSE"
        assert info["limit_pct"] == 0.30

    def test_bse_92_prefix(self):
        info = normalize_stock_code("920001")
        assert info["board"] == "BSE"
        assert info["limit_pct"] == 0.30

    def test_sh_prefixed_code(self):
        info = normalize_stock_code("sh600519")
        assert info["market"] == "A"
        assert info["board"] == "main"
        assert info["limit_pct"] == 0.10


class TestCalcLimitPriceEdgeCases:
    def test_unknown_direction_treated_as_down(self):
        result = calc_limit_price(10.0, 0.10, "sideways")
        assert abs(result - 9.0) < 0.01

    def test_zero_price(self):
        assert calc_limit_price(0.0, 0.10, "up") == 0.0

    def test_negative_price(self):
        assert calc_limit_price(-10.0, 0.10, "up") == -11.0

    def test_zero_ratio_keeps_price(self):
        assert calc_limit_price(10.0, 0.0, "up") == 10.0


def test_financials_annual_history_is_newest_first(monkeypatch):
    from argparse import Namespace

    import pandas as pd

    from tools import stock_data

    rows = pd.DataFrame(
        {
            "日期": ["2020-12-31", "2024-12-31", "2024-09-30", "2023-12-31"],
            "净资产收益率(%)": [1, 4, 3, 2],
            "销售毛利率(%)": [10, 40, 30, 20],
            "销售净利率(%)": [5, 20, 15, 10],
        }
    )
    monkeypatch.setattr(stock_data, "_akshare_retry", lambda *a, **k: rows)
    result = stock_data.cmd_financials(Namespace(symbol="600519", periods=3, annual=True))
    assert result["report_date"] == "2024-12-31"
    assert [r["roe"] for r in result["periods"]] == [4, 2, 1]


def test_financial_cache_persists_provider_date_objects(monkeypatch):
    from argparse import Namespace
    from datetime import date
    from unittest.mock import MagicMock

    import pandas as pd

    from tools import stock_data

    fetch = MagicMock(return_value=pd.DataFrame({"日期": [date(2025, 12, 31)], "净资产收益率(%)": [15]}))
    monkeypatch.setattr(stock_data, "_akshare_retry", fetch)
    args = Namespace(symbol="600519", periods=1, annual=False)
    stock_data.cmd_financials(args)
    result = stock_data.cmd_financials(args)
    assert fetch.call_count == 1
    assert result["report_date"] == "2025-12-31"


def test_basic_info_partial_results_are_not_cached(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    from tools import stock_data

    fetch = MagicMock(side_effect=ValueError("down"))
    monkeypatch.setattr(stock_data, "_akshare_retry", fetch)
    monkeypatch.setattr(stock_data, "resolve_stock_sectors", lambda *a, **k: {"error": "down"})
    for _ in range(2):
        result = stock_data.cmd_stock_info(Namespace(symbol="600519"))
        assert result.get("warnings") or result.get("error")
    assert fetch.call_count == 2


def test_hk_annual_source_failure_keeps_available_yahoo_data(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    import pandas as pd

    from tools import stock_data

    ticker = MagicMock()
    ticker.info = {"shortName": "Tencent"}
    ticker.financials = pd.DataFrame({"2025-12-31": [100, 20]}, index=["Total Revenue", "Net Income"])
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: ticker)
    monkeypatch.setattr(stock_data, "_akshare_retry", MagicMock(side_effect=ValueError("down")))
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=10, annual=True))
    assert result["name"] == "Tencent"
    assert result["periods"][0]["net_income"] == 20
    assert result["warnings"]


def test_non_a_history_derives_margins_from_available_income_rows(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    import pandas as pd

    from tools import stock_data

    ticker = MagicMock()
    ticker.info = {"shortName": "Example"}
    ticker.financials = pd.DataFrame(
        {"2025-12-31": [100, 20, 40]}, index=["Total Revenue", "Net Income", "Gross Profit"]
    )
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: ticker)
    result = stock_data.cmd_financials(Namespace(symbol="AAPL", periods=2, annual=False))
    assert result["periods"][0]["net_profit_margin"] == 20
    assert result["periods"][0]["gross_margin"] == 40


def test_user_directory_sector_cache_survives_legacy_temp_cleanup(monkeypatch, tmp_path):
    from argparse import Namespace

    from tools import stock_data

    monkeypatch.setattr(stock_data, "_DATA_CACHE_DIR", tmp_path / "legacy")
    monkeypatch.setattr(
        stock_data,
        "sector_constituents_a",
        lambda *a, **k: {"sector": "白酒", "board_type": "industry", "constituents": [{"code": "600519"}]},
    )
    stock_data.cmd_sector_constituents(Namespace(sector="白酒", board_type="industry"))
    for path in (tmp_path / "legacy").glob("*.json"):
        path.unlink()
    assert stock_data._stock_boards_from_cache("600519") == [
        {"name": "白酒", "source": "cache", "board_type": "industry"}
    ]


def test_hk_financial_backend_uses_five_digits(monkeypatch):
    from argparse import Namespace
    from unittest.mock import MagicMock

    import pandas as pd

    from tools import stock_data

    ticker = MagicMock()
    ticker.info = {"shortName": "Tencent"}
    ticker.financials = pd.DataFrame()
    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: ticker)
    calls = []

    def fetch(fn, **kwargs):
        calls.append(kwargs)
        return pd.DataFrame(
            {"REPORT_DATE": ["2025-12-31"], "ROE_AVG": [15], "GROSS_PROFIT_RATIO": [40], "NET_PROFIT_RATIO": [20]}
        )

    monkeypatch.setattr(stock_data, "_akshare_retry", fetch)
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=10, annual=True))
    assert calls[0]["symbol"] == "00700"
    assert result["periods"][0]["roe"] == 15


def test_hk_annual_history_survives_yahoo_snapshot_failure(monkeypatch):
    from argparse import Namespace

    import pandas as pd

    from tools import stock_data

    class Ticker:
        @property
        def info(self):
            raise ValueError("snapshot down")

        financials = pd.DataFrame()

    monkeypatch.setattr("yfinance.Ticker", lambda *a, **k: Ticker())
    monkeypatch.setattr(
        stock_data, "_akshare_retry", lambda *a, **k: pd.DataFrame({"REPORT_DATE": ["2025-12-31"], "ROE_AVG": [15]})
    )
    result = stock_data.cmd_financials(Namespace(symbol="0700.HK", periods=10, annual=True))
    assert result["periods"][0]["roe"] == 15
    assert result["warnings"]


def test_quote_and_kline_failover_carry_provider_evidence():
    from tools.stock_data import _failover

    quote = _failover([("unavailable", lambda: None), ("fixture", lambda: {"price": 10})], "quote:AAPL", evidence=True)
    assert quote["source"] == "fixture"
    assert quote["data_as_of"] is None
    assert quote["fetched_at"].endswith("+00:00")
    bars = _failover([("yfinance", lambda: [{"date": "2024-01-02", "close": 10}])], "kline:AAPL", evidence=True)
    assert bars[0]["source"] == "yfinance"
    assert bars[0]["data_as_of"] == "2024-01-02"
    assert bars[0]["adjusted"] == "total_return"


def test_provider_timestamp_can_reveal_stale_quote_during_live_session(monkeypatch):
    from argparse import Namespace

    from tools import stock_data

    monkeypatch.setattr(
        stock_data, "quote_a", lambda *args, **kwargs: {"price": 10, "data_as_of": "2000-01-01T15:00:00+08:00"}
    )
    monkeypatch.setattr(stock_data, "_quote_stale_marker", lambda market: None)
    result = stock_data.cmd_quote(Namespace(symbol="600519"))
    assert result["stale"] is True
    assert result["as_of_basis"] == "provider"
    assert result["as_of"] == "2000-01-01T15:00:00+08:00"


def test_yahoo_quote_preserves_time_from_same_info_response(monkeypatch):
    from argparse import Namespace
    from types import SimpleNamespace

    from tools import stock_data

    calls = []

    def ticker(symbol):
        calls.append(symbol)
        return SimpleNamespace(
            info={"shortName": "Fixture", "regularMarketPrice": 100, "regularMarketTime": 1704205800}
        )

    monkeypatch.setattr("yfinance.Ticker", ticker)
    monkeypatch.setattr(stock_data, "_quote_stale_marker", lambda market: None)
    quote = stock_data.cmd_quote(Namespace(symbol="AAPL"))
    assert quote["data_as_of"] == "2024-01-02T14:30:00+00:00"
    assert quote["as_of_basis"] == "provider"
    assert calls == ["AAPL"]
