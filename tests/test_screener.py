import pandas as pd

from tools.screener import DEFAULT_FILTERS, _minmax, apply_filters, compute_scores, merge_filters


class TestMergeFilters:
    def test_defaults(self):
        result = merge_filters(None)
        assert result == DEFAULT_FILTERS

    def test_override(self):
        result = merge_filters({"filters": {"pe_max": 50}})
        assert result["pe_max"] == 50
        assert result["pe_min"] == 0

    def test_empty_config(self):
        result = merge_filters({})
        assert result == DEFAULT_FILTERS


class TestApplyFilters:
    def test_pe_filter(self):
        df = pd.DataFrame(
            {
                "pe": [10, 50, 150, -5],
                "pb": [2, 5, 8, 1],
                "market_cap": [1e10] * 4,
            }
        )
        filters = merge_filters(None)
        result = apply_filters(df, filters)
        assert len(result) == 2
        assert 150 not in result["pe"].values
        assert -5 not in result["pe"].values

    def test_no_matching(self):
        df = pd.DataFrame({"pe": [200, 300]})
        filters = merge_filters(None)
        result = apply_filters(df, filters)
        assert len(result) == 0

    def test_empty_df(self):
        df = pd.DataFrame(columns=["pe", "pb"])
        filters = merge_filters(None)
        result = apply_filters(df, filters)
        assert len(result) == 0


class TestMinmax:
    def test_uniform(self):
        s = pd.Series([5.0, 5.0, 5.0])
        result = _minmax(s)
        assert all(result == 50.0)

    def test_range(self):
        s = pd.Series([0.0, 50.0, 100.0])
        result = _minmax(s)
        assert abs(result.iloc[0] - 0) < 0.01
        assert abs(result.iloc[1] - 50) < 0.01
        assert abs(result.iloc[2] - 100) < 0.01


class TestComputeScores:
    def test_scores_computed(self):
        df = pd.DataFrame(
            {
                "pe": [10.0, 20.0, 30.0],
                "pb": [1.0, 2.0, 3.0],
                "change_pct": [5.0, 3.0, 1.0],
                "volume_ratio": [2.0, 1.5, 1.0],
                "turnover_rate": [3.0, 2.0, 1.0],
                "volume": [1e6, 8e5, 5e5],
            }
        )
        result = compute_scores(df)
        assert "value_score" in result.columns
        assert "momentum_score" in result.columns
        assert "liquidity_score" in result.columns
        assert "composite_score" in result.columns
        assert result["value_score"].iloc[0] > result["value_score"].iloc[2]

    def test_missing_columns(self):
        df = pd.DataFrame({"pe": [10.0, 20.0], "change_pct": [5.0, 3.0]})
        result = compute_scores(df)
        assert "composite_score" in result.columns
