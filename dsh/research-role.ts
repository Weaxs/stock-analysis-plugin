import type { Context } from "@deepseek-ai/cordis";
import type {} from "@deepseek-ai/dsh-tools";

export const name = "stock-research-role";
export const inject = ["tools"];

const common = ["get_market_capabilities", "resolve_stock_name", "get_stock_info", "check_trading_day", "get_trading_phase"];
const roles = {
  fundamentals: [...common, "get_quote", "get_financials", "get_fundamental_context", "get_valuation_history",
    "get_index_valuation", "get_shareholder_structure", "get_us_holdings", "get_insider_trades", "get_dividends", "compare_stocks"],
  technical: [...common, "get_quote", "get_kline", "get_technical_analysis", "analyze_pattern", "get_volume_analysis",
    "detect_market_regime", "run_backtest", "validate_backtest", "analyze_trend_strategy", "backtest_rotation_allocation"],
  events: [...common, "get_news", "get_announcements", "get_earnings_calendar", "get_dividends", "search_stock_news",
    "search_comprehensive_intel", "extract_article", "analyze_disclosure_changes", "analyze_event_study"],
};

export function apply(ctx: Context, config: { role: keyof typeof roles }) {
  if (!config || !Object.hasOwn(roles, config.role)) throw new TypeError("Unknown stock research role");
  // Base and preset copies share names; masks also apply to inherited preset registrations.
  const allowed = new Set([...roles[config.role], "web_search", "web_fetch", "skill"]);
  ctx.tools.restrict({ allow: [...allowed] });
}
