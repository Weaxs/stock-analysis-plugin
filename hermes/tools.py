import base64
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"


def _find_python() -> str:
    # Windows venvs ship Scripts/python.exe (there is no python3); POSIX venvs ship bin/python3.
    if sys.platform == "win32":
        venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = PROJECT_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    # On Windows "python3" is usually the Microsoft Store stub — prefer "python".
    return "python" if sys.platform == "win32" else "python3"


def _run(script: str, args: list) -> str:
    # argv list + shell=False: tool input is passed verbatim, never shell-interpreted.
    # This is what actually kills shell injection on Windows too — cmd.exe metacharacters
    # (& | % etc.) are inert when no shell is involved.
    python = _find_python()
    try:
        result = subprocess.run(
            [python, str(TOOLS_DIR / script), *[str(a) for a in args]],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Command timed out after 120s"})
    except Exception as e:
        return json.dumps({"error": str(e)})
    if result.returncode != 0:
        return json.dumps({"error": result.stderr.strip() or f"exit code {result.returncode}"})
    return result.stdout


def get_kline(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    period = args.get("period", "daily")
    count = args.get("count", 60)
    return _run("stock_data.py", ["kline", symbol, "--period", period, "--count", count])


def get_quote(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    return _run("stock_data.py", ["quote", symbol])


def get_capital_flow(args: dict, **kwargs) -> str:
    symbol = args.get("symbol", "")
    mode = args.get("mode", "detail")
    argv = ["capital_flow"]
    if symbol:
        argv.append(symbol)
    argv += ["--mode", mode]
    return _run("stock_data.py", argv)


def get_news(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    days = args.get("days", 3)
    return _run("stock_data.py", ["news", symbol, "--days", days])


def get_financials(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    return _run("stock_data.py", ["financials", symbol])


def get_technical_analysis(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    period = args.get("period", "daily")
    count = args.get("count", 120)
    return _run("technical.py", ["analyze", symbol, "--period", period, "--count", count])


def analyze_pattern(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    period = args.get("period", "daily")
    days = args.get("days", 60)
    return _run("pattern.py", ["analyze", symbol, "--period", period, "--days", days])


def get_market_indices(args: dict, **kwargs) -> str:
    region = args.get("region", "cn")
    return _run("stock_data.py", ["market_indices", "--region", region])


def get_sector_rankings(args: dict, **kwargs) -> str:
    top = args.get("top", 10)
    direction = args.get("direction", "top")
    return _run("stock_data.py", ["sector_rankings", "--top", top, "--direction", direction])


def get_sector_constituents(args: dict, **kwargs) -> str:
    sector = args["sector"]
    board_type = args.get("board_type", "auto")
    return _run("stock_data.py", ["sector_constituents", sector, "--board-type", board_type])


def resolve_stock_sectors(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    return _run("stock_data.py", ["resolve_stock_sectors", symbol])


def get_stock_info(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    return _run("stock_data.py", ["stock_info", symbol])


def get_chip_distribution(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    return _run("stock_data.py", ["chip_distribution", symbol])


def get_market_stats(args: dict, **kwargs) -> str:
    market = args.get("market", "A")
    return _run("stock_data.py", ["market_stats", "--market", market])


def get_fundamental_context(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    return _run("stock_data.py", ["fundamental_context", symbol])


def screen_stocks(args: dict, **kwargs) -> str:
    market = args.get("market", "A")
    top = args.get("top", 20)
    config = args.get("config", "")
    argv = ["screen", "--market", market, "--top", top]
    if config:
        argv += ["--config", config]
    if args.get("l2"):
        argv.append("--l2")
    return _run("screener.py", argv)


def run_backtest(args: dict, **kwargs) -> str:
    strategy = args["strategy"]
    symbol = args["symbol"]
    start = args.get("start", "")
    end = args.get("end", "")
    capital = args.get("capital", 1000000)
    argv = ["run", strategy, symbol]
    if start:
        argv += ["--start", start]
    if end:
        argv += ["--end", end]
    argv += ["--capital", capital]
    return _run("backtest.py", argv)


def evaluate_signal(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    signal = args["signal"]
    forward_days = args.get("forward_days", "3,5,10")
    lookback = args.get("lookback", 250)
    return _run(
        "backtest.py",
        ["evaluate_signal", symbol, signal, "--forward", forward_days, "--lookback", lookback],
    )


def resolve_stock_name(args: dict, **kwargs) -> str:
    query = args["query"]
    top = args.get("top", 5)
    return _run("name_resolver.py", ["resolve", query, "--top", top])


def check_trading_day(args: dict, **kwargs) -> str:
    market = args["market"]
    date = args.get("date", "")
    argv = ["check", market]
    if date:
        argv += ["--date", date]
    return _run("trading_calendar.py", argv)


def get_trading_days(args: dict, **kwargs) -> str:
    market = args["market"]
    direction = args.get("direction", "next")
    count = args.get("count", 5)
    date = args.get("date", "")
    argv = [direction, market, "--count", count]
    if date:
        argv += ["--date", date]
    return _run("trading_calendar.py", argv)


def calculate_ma(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    periods = args.get("periods", "5,10,20,30,60,120,250")
    kline_period = args.get("kline_period", "daily")
    return _run(
        "technical.py",
        ["calculate_ma", symbol, "--periods", periods, "--period", kline_period, "--count", 300],
    )


def get_volume_analysis(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    period = args.get("period", "daily")
    count = args.get("count", 60)
    return _run("volume_analysis.py", ["analyze", symbol, "--period", period, "--count", count])


def search_stock_news(args: dict, **kwargs) -> str:
    query = args["query"]
    count = args.get("count", 10)
    return _run("search_intel.py", ["search", query, "--count", count])


def search_comprehensive_intel(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    name = args.get("name", "")
    argv = ["comprehensive", symbol]
    if name:
        argv += ["--name", name]
    return _run("search_intel.py", argv)


def get_social_sentiment(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    return _run("search_intel.py", ["sentiment", symbol])


def get_trending_sentiment(args: dict, **kwargs) -> str:
    return _run("search_intel.py", ["trending"])


def extract_article(args: dict, **kwargs) -> str:
    url = args["url"]
    return _run("search_intel.py", ["extract", url])


def screen_risk(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    name = args.get("name", "")
    argv = ["screen", symbol]
    if name:
        argv += ["--name", name]
    return _run("risk_screening.py", argv)


def detect_market_regime(args: dict, **kwargs) -> str:
    market = args.get("market", "A")
    return _run("market_regime.py", ["detect", market])


def get_market_review(args: dict, **kwargs) -> str:
    market = args.get("market", "A")
    return _run("market_review.py", ["review", "--market", market])


def run_watchlist_analysis(args: dict, **kwargs) -> str:
    symbols = args["symbols"]
    workers = args.get("workers", 3)
    return _run("watchlist.py", ["analyze", symbols, "--workers", workers])


def detect_anomaly(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    return _run("anomaly_detect.py", ["detect", symbol])


def diagnose_data_sources(args: dict, **kwargs) -> str:
    market = args.get("market", "all")
    return _run("diagnostics.py", ["check", "--market", market])


def get_market_capabilities(args: dict, **kwargs) -> str:
    market = args.get("market")
    symbol = args.get("symbol")
    argv = ["get", "--symbol", symbol] if symbol else ["get", "--market", market or "A"]
    return _run("capabilities.py", argv)


def render_stock_report(args: dict, **kwargs) -> str:
    report = args["report"]
    template = args.get("template", "full")
    payload = base64.b64encode(json.dumps(report, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return _run("report_renderer.py", ["stock", "--template", template, "--input-b64", payload])


def render_market_report(args: dict, **kwargs) -> str:
    report = args["report"]
    template = args.get("template", "full")
    payload = base64.b64encode(json.dumps(report, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return _run("report_renderer.py", ["market", "--template", template, "--input-b64", payload])


def build_watchlist_context(args: dict, **kwargs) -> str:
    symbols = args["symbols"]
    workers = args.get("workers", 3)
    argv = ["build", symbols, "--workers", workers]
    if args.get("include_market_review"):
        argv.append("--include-market-review")
    return _run("watchlist_context.py", argv)


def analyze_position_context(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    cost = args["cost"]
    quantity = args["quantity"]
    stop_loss = args.get("stop_loss")
    take_profit = args.get("take_profit")
    argv = ["analyze", symbol, "--cost", cost, "--quantity", quantity]
    if stop_loss is not None:
        argv += ["--stop-loss", stop_loss]
    if take_profit is not None:
        argv += ["--take-profit", take_profit]
    return _run("position_context.py", argv)


def check_alert_rules(args: dict, **kwargs) -> str:
    symbol = args["symbol"]
    rules = args["rules"]
    payload = base64.b64encode(json.dumps(rules, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return _run("alert_rules.py", ["check", symbol, "--rules-b64", payload])


def parse_stock_list(args: dict, **kwargs) -> str:
    text = args["text"]
    payload = base64.b64encode(str(text).encode("utf-8")).decode("ascii")
    return _run("import_parser.py", ["parse", "--text-b64", payload])
