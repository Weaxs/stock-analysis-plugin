from pathlib import Path

from . import schemas, tools

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"

_HANDLER_MAP = {
    "get_kline": tools.get_kline,
    "get_quote": tools.get_quote,
    "get_capital_flow": tools.get_capital_flow,
    "get_news": tools.get_news,
    "get_financials": tools.get_financials,
    "get_technical_analysis": tools.get_technical_analysis,
    "analyze_pattern": tools.analyze_pattern,
    "get_market_indices": tools.get_market_indices,
    "get_sector_rankings": tools.get_sector_rankings,
    "get_stock_info": tools.get_stock_info,
    "get_chip_distribution": tools.get_chip_distribution,
    "get_market_stats": tools.get_market_stats,
    "get_fundamental_context": tools.get_fundamental_context,
    "screen_stocks": tools.screen_stocks,
    "run_backtest": tools.run_backtest,
    "evaluate_signal": tools.evaluate_signal,
    "resolve_stock_name": tools.resolve_stock_name,
    "check_trading_day": tools.check_trading_day,
    "get_trading_days": tools.get_trading_days,
    "calculate_ma": tools.calculate_ma,
    "get_volume_analysis": tools.get_volume_analysis,
    "search_stock_news": tools.search_stock_news,
    "search_comprehensive_intel": tools.search_comprehensive_intel,
    "get_social_sentiment": tools.get_social_sentiment,
    "extract_article": tools.extract_article,
    "screen_risk": tools.screen_risk,
    "detect_market_regime": tools.detect_market_regime,
}


def register(ctx):
    for schema in schemas.TOOL_SCHEMAS:
        name = schema["name"]
        handler = _HANDLER_MAP[name]
        ctx.register_tool(
            name=name,
            toolset="stock-analysis",
            schema=schema,
            handler=handler,
        )

    for child in sorted(SKILLS_DIR.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, str(skill_md))
