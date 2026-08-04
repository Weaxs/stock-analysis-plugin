from tools.stock_data import calc_limit_price, detect_market, mark_st, normalize_stock_code


class TestDetectMarket:
    def test_a_share_6_digit(self):
        assert detect_market("600519") == "A"
        assert detect_market("000001") == "A"
        assert detect_market("300750") == "A"
        assert detect_market("688981") == "A"

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


class TestMarkSt:
    def test_st_detected(self):
        info = {"board": "main", "limit_pct": 0.10, "is_st": False}
        result = mark_st(info, "ST某某")
        assert result["is_st"] is True
        assert result["limit_pct"] == 0.05

    def test_star_st_keeps_limit(self):
        info = {"board": "STAR", "limit_pct": 0.20, "is_st": False}
        result = mark_st(info, "*ST某某")
        assert result["is_st"] is True
        assert result["limit_pct"] == 0.20

    def test_normal_stock(self):
        info = {"board": "main", "limit_pct": 0.10, "is_st": False}
        result = mark_st(info, "贵州茅台")
        assert result["is_st"] is False
        assert result["limit_pct"] == 0.10

    def test_empty_name(self):
        info = {"board": "main", "limit_pct": 0.10, "is_st": False}
        result = mark_st(info, "")
        assert result["is_st"] is False


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

    def test_banker_rounding(self):
        result = calc_limit_price(13.57, 0.10, "up")
        assert abs(result - 14.93) < 0.01
