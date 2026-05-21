from unittest.mock import patch

from tools.market_review import _calc_temperature, _signal_to_stance, review_market


class TestCalcTemperature:
    def test_all_up_market(self):
        stats = {"up_count": 4000, "down_count": 500, "limit_up_count": 80, "limit_down_count": 5}
        indices = [{"change_pct": 2.5}, {"change_pct": 3.0}, {"change_pct": 1.8}]
        result = _calc_temperature(stats, indices)
        assert result["signal"] == "green"
        assert result["score"] > 60

    def test_all_down_market(self):
        stats = {"up_count": 500, "down_count": 4000, "limit_up_count": 5, "limit_down_count": 80}
        indices = [{"change_pct": -2.5}, {"change_pct": -3.0}, {"change_pct": -1.8}]
        result = _calc_temperature(stats, indices)
        assert result["signal"] == "red"
        assert result["score"] < 40

    def test_neutral_market(self):
        stats = {"up_count": 2000, "down_count": 2000, "limit_up_count": 20, "limit_down_count": 20}
        indices = [{"change_pct": 0.1}, {"change_pct": -0.1}]
        result = _calc_temperature(stats, indices)
        assert result["signal"] == "yellow"
        assert 40 <= result["score"] <= 60

    def test_no_data(self):
        result = _calc_temperature(None, None)
        assert result["score"] == 50
        assert result["signal"] == "yellow"

    def test_only_indices(self):
        indices = [{"change_pct": 3.0}]
        result = _calc_temperature(None, indices)
        assert result["score"] > 50

    def test_only_stats(self):
        stats = {"up_count": 3000, "down_count": 1000, "limit_up_count": 50, "limit_down_count": 10}
        result = _calc_temperature(stats, None)
        assert result["score"] > 50

    def test_index_score_clamped(self):
        indices = [{"change_pct": 10.0}]
        result = _calc_temperature(None, indices)
        assert result["score"] <= 100

    def test_index_score_clamped_negative(self):
        indices = [{"change_pct": -10.0}]
        result = _calc_temperature(None, indices)
        assert result["score"] >= 0

    def test_empty_stats(self):
        stats = {"up_count": 0, "down_count": 0, "limit_up_count": 0, "limit_down_count": 0}
        indices = [{"change_pct": 1.0}]
        result = _calc_temperature(stats, indices)
        assert "score" in result


class TestSignalToStance:
    def test_green(self):
        assert _signal_to_stance("green") == "offensive"

    def test_yellow(self):
        assert _signal_to_stance("yellow") == "balanced"

    def test_red(self):
        assert _signal_to_stance("red") == "defensive"

    def test_unknown(self):
        assert _signal_to_stance("unknown") == "balanced"


class TestReviewMarket:
    @patch("tools.market_review._run_tool")
    def test_a_market_structure(self, mock_run):
        mock_run.return_value = None
        result = review_market("A")
        assert result["market"] == "A"
        assert "date" in result
        assert "temperature" in result
        assert "strategy_stance" in result
        assert result["temperature"]["signal"] == "yellow"

    @patch("tools.market_review._run_tool")
    def test_hk_market(self, mock_run):
        mock_run.return_value = None
        result = review_market("HK")
        assert result["market"] == "HK"
        assert "indices" in result

    @patch("tools.market_review._run_tool")
    def test_us_market(self, mock_run):
        mock_run.return_value = None
        result = review_market("US")
        assert result["market"] == "US"

    @patch("tools.market_review._run_tool")
    def test_unknown_market(self, mock_run):
        result = review_market("XX")
        assert "error" in result

    @patch("tools.market_review._run_tool")
    def test_with_real_data(self, mock_run):
        def side_effect(script, args, timeout=30):
            if "market_stats" in args:
                return {"up_count": 3000, "down_count": 1500, "limit_up_count": 40, "limit_down_count": 8}
            if "market_indices" in args:
                return [{"change_pct": 1.2}, {"change_pct": 0.8}]
            return None

        mock_run.side_effect = side_effect
        result = review_market("A")
        assert result["temperature"]["signal"] == "green"
        assert result["strategy_stance"] == "offensive"
