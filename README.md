# pi-stock-analysis

Pi Agent extension for stock analysis, screening, and strategy backtesting. Covers A-shares, HK, and US markets.

Integrates capabilities from [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis), AlphaSift (multi-factor screening), and AlphaEvo (strategy backtesting with LLM-guided evolution).

## Install

```bash
# Clone into Pi extensions directory
cd ~/.pi/agent/extensions
git clone git@github.com:Weaxs/pi-stock-analysis.git

# Install Python dependencies
pip install -r pi-stock-analysis/tools/requirements.txt

# Install Node dependencies (if any)
cd pi-stock-analysis && npm install
```

Then launch `pi` in any project — the extension loads automatically.

## Skills

| Skill | Description |
|---|---|
| `/skill:stock-analysis` | Comprehensive stock analysis (technicals + fundamentals + news) |
| `/skill:stock-screener` | Multi-factor market-wide stock screening |
| `/skill:strategy-backtest` | Strategy backtesting with LLM-guided optimization |
| `/skill:bull-trend` | Trend-following analysis |
| `/skill:shrink-pullback` | Volume contraction pullback |
| `/skill:ma-crossover` | Moving average crossover |
| `/skill:volume-breakout` | Volume breakout detection |
| `/skill:bottom-volume` | Bottom volume accumulation |
| `/skill:dragon-head` | Market leader identification |
| `/skill:chan-theory` | Chan theory (Bi/Duan/ZhongShu) |
| `/skill:wave-theory` | Elliott wave analysis |
| `/skill:box-oscillation` | Box range oscillation |
| `/skill:emotion-cycle` | Market sentiment cycle |
| `/skill:one-yang-three-yin` | One bullish engulfing three bearish |

## Tools

The extension registers 8 tools for Pi Agent:

- `get_kline` — K-line / OHLCV data
- `get_quote` — Real-time quote
- `get_capital_flow` — Capital flow (A-shares only)
- `get_news` — Financial news
- `get_financials` — Key financial metrics
- `get_technical_analysis` — Technical indicator analysis
- `screen_stocks` — Market-wide stock screening
- `run_backtest` — Strategy backtesting

## CLI Usage (standalone)

Python tools can also be used directly:

```bash
# K-line data
python tools/stock_data.py kline 600519 --period daily --count 60
python tools/stock_data.py kline AAPL --period weekly
python tools/stock_data.py kline 00700.HK

# Real-time quote
python tools/stock_data.py quote 600519

# Technical analysis
python tools/technical.py analyze 600519

# Stock screening
python tools/screener.py screen --market A --top 20

# Backtesting
python tools/backtest.py run strategies/examples/rsi_oversold.yaml 600519
```

## Market Routing

| Symbol Pattern | Market | Data Source |
|---|---|---|
| 6-digit number (e.g. `600519`) | A-shares | akshare |
| Ends with `.HK` (e.g. `00700.HK`) | Hong Kong | yfinance |
| Letters (e.g. `AAPL`) | US | yfinance |

## License

MIT
