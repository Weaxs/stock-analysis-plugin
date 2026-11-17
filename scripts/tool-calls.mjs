import TOOL_SCHEMAS from "../tools/tool_schemas.json" with { type: "json" };
import { withJsonInput } from "./json-input.mjs";

export { TOOL_SCHEMAS };

// Shared host -> CLI mapping for the Pi, OpenClaw and dsh adapters; parameter
// schemas live in tools/tool_schemas.json and Hermes keeps its Python twin
// (hermes/tools.py). tests/test_adapter_parity.ts pins all of them together.
// `input` tools ship their JSON through a temp file (argv limits on Windows).
export const TOOL_CALLS = {
  get_announcements: {
    script: "value_data.py",
    argv: (p) => ["announcements", p.symbol, "--category", p.category ?? "all", "--start-date", p.start_date ?? "", "--end-date", p.end_date ?? "", "--top", String(p.top ?? 50)],
  },
  get_us_holdings: {
    script: "sec_edgar.py",
    argv: (p) => ["holdings", p.cik, ...(p.mode ? ["--mode", p.mode] : [])],
  },
  get_insider_trades: {
    script: "sec_edgar.py",
    argv: (p) => ["insider_trades", p.symbol, "--days", String(p.days ?? 90), "--top", String(p.top ?? 10)],
  },
  get_valuation_history: {
    script: "value_data.py",
    argv: (p) => ["valuation_history", p.symbol, "--years", String(p.years ?? 5)],
  },
  get_index_valuation: {
    script: "value_data.py",
    argv: (p) => ["index_valuation", p.symbol, "--years", String(p.years ?? 5)],
  },
  get_cds_index: {
    script: "credit_data.py",
    argv: (p) => ["cds_index", "--source", p.source ?? "cn", "--days", String(p.days ?? 30), "--index", p.index ?? "", "--tenor", p.tenor ?? ""],
  },
  get_earnings_calendar: {
    script: "value_data.py",
    argv: (p) => ["earnings_calendar", "--report-date", p.report_date ?? "", "--symbol", p.symbol ?? "", "--top", String(p.top ?? 50)],
  },
  get_shareholder_structure: {
    script: "value_data.py",
    argv: (p) => ["shareholder_structure", p.symbol, "--report-date", p.report_date ?? ""],
  },
  get_etf_details: {
    script: "value_data.py",
    argv: (p) => ["etf_details", p.symbol, "--benchmark", p.benchmark ?? "", "--days", String(p.days ?? 60), ...(p.include_full_holdings ? ["--include-full-holdings"] : [])],
  },
  get_dividends: {
    script: "value_data.py",
    argv: (p) => ["dividends", "--market", p.market ?? "A", "--symbol", p.symbol ?? "", "--mode", p.mode ?? "history", "--report-date", p.report_date ?? "", "--years", String(p.years ?? 10), "--top", String(p.top ?? 20)],
  },
  get_kline: {
    script: "stock_data.py",
    argv: (p) => [
      "kline",
      p.symbol,
      "--period",
      p.period ?? "daily",
      "--count",
      String(p.count ?? 60),
    ],
  },
  get_quote: {
    script: "stock_data.py",
    argv: (p) => ["quote", p.symbol],
  },
  get_fund_quote: {
    script: "stock_data.py",
    argv: (p) => ["fund_quote", p.symbol],
  },
  get_capital_flow: {
    script: "stock_data.py",
    argv: (p) => {
      const args = ["capital_flow"];
      if (p.symbol) args.push(p.symbol);
      args.push("--mode", p.mode ?? "detail");
      return args;
    },
  },
  get_news: {
    script: "stock_data.py",
    argv: (p) => ["news", p.symbol, "--days", String(p.days ?? 3)],
  },
  get_financials: {
    script: "stock_data.py",
    argv: (p) => {
      const args = ["financials", p.symbol];
      if (p.periods !== undefined && p.periods !== 1) args.push("--periods", String(p.periods));
      if (p.annual) args.push("--annual");
      if (p.include_dcf_inputs) args.push("--include-dcf-inputs");
      return args;
    },
  },
  get_technical_analysis: {
    script: "technical.py",
    argv: (p) => {
      const args = [
        "analyze",
        p.symbol,
        "--period",
        p.period ?? "daily",
        "--count",
        String(p.count ?? 120),
      ];
      if (p.periods) args.push("--periods", p.periods);
      return args;
    },
  },
  analyze_pattern: {
    script: "pattern.py",
    argv: (p) => [
      "analyze",
      p.symbol,
      "--period",
      p.period ?? "daily",
      "--days",
      String(p.days ?? 60),
    ],
  },
  get_market_indices: {
    script: "stock_data.py",
    argv: (p) => ["market_indices", "--region", p.region ?? "cn"],
  },
  get_sector_rankings: {
    script: "stock_data.py",
    argv: (p) => [
      "sector_rankings",
      "--top",
      String(p.top ?? 10),
      "--direction",
      p.direction ?? "top",
      "--board-type",
      p.board_type ?? "industry",
      "--market",
      p.market ?? "A",
    ],
  },
  get_sector_constituents: {
    script: "stock_data.py",
    argv: (p) => [
      "sector_constituents",
      p.sector,
      "--board-type",
      p.board_type ?? "auto",
    ],
  },
  resolve_stock_sectors: {
    script: "stock_data.py",
    argv: (p) => ["resolve_stock_sectors", p.symbol],
  },
  get_stock_info: {
    script: "stock_data.py",
    argv: (p) => ["stock_info", p.symbol],
  },
  get_chip_distribution: {
    script: "stock_data.py",
    argv: (p) => ["chip_distribution", p.symbol],
  },
  get_market_stats: {
    script: "stock_data.py",
    argv: (p) => ["market_stats", "--market", p.market ?? "A"],
  },
  get_limit_up_pool: {
    script: "stock_data.py",
    argv: (p) => {
      const args = ["limit_up_pool"];
      if (p.date) args.push("--date", p.date);
      return args;
    },
  },
  get_dragon_tiger: {
    script: "stock_data.py",
    argv: (p) => {
      const args = ["dragon_tiger"];
      if (p.date) args.push("--date", p.date);
      if (p.symbol) args.push("--symbol", p.symbol);
      args.push("--top", String(p.top ?? 20));
      return args;
    },
  },
  get_hot_stocks: {
    script: "stock_data.py",
    argv: (p) => ["hot_stocks", "--top", String(p.top ?? 20)],
  },
  get_margin_trading: {
    script: "stock_data.py",
    argv: (p) => ["margin_trading", p.symbol, "--days", String(p.days ?? 10), ...(p.include_ratio ? ["--include-ratio"] : [])],
  },
  get_northbound_flow: {
    script: "stock_data.py",
    argv: (p) => ["northbound_flow", "--days", String(p.days ?? 10)],
  },
  get_fundamental_context: {
    script: "stock_data.py",
    argv: (p) => ["fundamental_context", p.symbol],
  },
  screen_stocks: {
    script: "screener.py",
    argv: (p) => {
      const args = ["screen", "--market", p.market ?? "A", "--top", String(p.top ?? 20)];
      if (p.config) args.push("--config", p.config);
      if (p.l2) args.push("--l2");
      return args;
    },
  },
  calculate_rotation_metrics: {
    script: "sector_rotation.py",
    input: (p) => p,
    argv: (p, path) => ["analyze", "--input", path],
  },
  backtest_rotation_allocation: {
    script: "sector_rotation.py",
    input: (p) => p,
    argv: (p, path) => ["backtest", "--input", path],
  },
  analyze_trend_strategy: {
    script: "trend_strategy.py",
    input: (p) => p,
    argv: (p, path) => ["fetch", "--input", path],
  },
  run_backtest: {
    script: "backtest.py",
    argv: (p) => {
      const args = ["run", p.strategy, p.symbol];
      if (p.start) args.push("--start", p.start);
      if (p.end) args.push("--end", p.end);
      args.push("--capital", String(p.capital ?? 1000000));
      return args;
    },
  },
  evaluate_signal: {
    script: "backtest.py",
    argv: (p) => [
      "evaluate_signal",
      p.symbol,
      p.signal,
      "--forward",
      p.forward_days ?? "3,5,10",
      "--lookback",
      String(p.lookback ?? 250),
    ],
  },
  resolve_stock_name: {
    script: "name_resolver.py",
    argv: (p) => ["resolve", p.query, "--top", String(p.top ?? 5)],
  },
  check_trading_day: {
    script: "trading_calendar.py",
    argv: (p) => {
      const args = ["check", p.market];
      if (p.date) args.push("--date", p.date);
      return args;
    },
  },
  get_trading_days: {
    script: "trading_calendar.py",
    argv: (p) => {
      const args = [p.direction ?? "next", p.market, "--count", String(p.count ?? 5)];
      if (p.date) args.push("--date", p.date);
      return args;
    },
  },
  get_trading_phase: {
    script: "trading_calendar.py",
    argv: (p) => ["phase", p.market],
  },
  calculate_ma: {
    script: "technical.py",
    argv: (p) => [
      "calculate_ma",
      p.symbol,
      "--periods",
      p.periods ?? "5,10,20,30,60,120,250",
      "--period",
      p.kline_period ?? "daily",
      "--count",
      "300",
    ],
  },
  get_volume_analysis: {
    script: "volume_analysis.py",
    argv: (p) => [
      "analyze",
      p.symbol,
      "--period",
      p.period ?? "daily",
      "--count",
      String(p.count ?? 60),
    ],
  },
  search_stock_news: {
    script: "search_intel.py",
    argv: (p) => ["search", p.query, "--count", String(p.count ?? 10)],
  },
  search_comprehensive_intel: {
    script: "search_intel.py",
    argv: (p) => {
      const args = ["comprehensive", p.symbol];
      if (p.name) args.push("--name", p.name);
      return args;
    },
  },
  get_social_sentiment: {
    script: "search_intel.py",
    argv: (p) => ["sentiment", p.symbol],
  },
  get_trending_sentiment: {
    script: "search_intel.py",
    argv: () => ["trending"],
  },
  extract_article: {
    script: "search_intel.py",
    argv: (p) => ["extract", p.url, "--max-chars", String(p.max_chars ?? 3000)],
  },
  screen_risk: {
    script: "risk_screening.py",
    argv: (p) => {
      const args = ["screen", p.symbol];
      if (p.name) args.push("--name", p.name);
      return args;
    },
  },
  detect_market_regime: {
    script: "market_regime.py",
    argv: (p) => ["detect", p.market ?? "A"],
  },
  get_market_review: {
    script: "market_review.py",
    argv: (p) => ["review", "--market", p.market ?? "A"],
  },
  get_watchlist: {
    script: "watchlist.py",
    argv: () => ["list"],
  },
  run_watchlist_analysis: {
    script: "watchlist.py",
    argv: (p) => ["analyze", p.symbols, "--workers", String(p.workers ?? 3)],
  },
  detect_anomaly: {
    script: "anomaly_detect.py",
    argv: (p) => ["detect", p.symbol],
  },
  diagnose_data_sources: {
    script: "diagnostics.py",
    argv: (p) => ["check", "--market", p.market ?? "all"],
  },
  get_market_capabilities: {
    script: "capabilities.py",
    argv: (p) =>
      p.symbol
        ? ["get", "--symbol", p.symbol]
        : ["get", "--market", p.market ?? "A"],
  },
  render_stock_report: {
    script: "report_renderer.py",
    input: (p) => p.report,
    argv: (p, path) => ["stock", "--template", p.template ?? "full", "--input", path, ...(p.final ? ["--final"] : [])],
  },
  render_market_report: {
    script: "report_renderer.py",
    input: (p) => p.report,
    argv: (p, path) => ["market", "--input", path, ...(p.final ? ["--final"] : []), ...(p.save ? ["--save"] : [])],
  },
  get_review_history: {
    script: "market_review.py",
    argv: (p) => ["history", "--limit", String(p.limit ?? 10)],
  },
  build_watchlist_context: {
    script: "watchlist_context.py",
    argv: (p) => {
      const args = ["build", p.symbols, "--workers", String(p.workers ?? 3)];
      if (p.include_market_review) args.push("--include-market-review");
      return args;
    },
  },
  analyze_position_context: {
    script: "position_context.py",
    argv: (p) => {
      const args = [
        "analyze",
        p.symbol,
        "--cost",
        String(p.cost),
        "--quantity",
        String(p.quantity),
      ];
      if (p.stop_loss !== undefined) args.push("--stop-loss", String(p.stop_loss));
      if (p.take_profit !== undefined) args.push("--take-profit", String(p.take_profit));
      return args;
    },
  },
  check_alert_rules: {
    script: "alert_rules.py",
    argv: (p) => {
      const b64 = Buffer.from(JSON.stringify(p.rules), "utf-8").toString("base64");
      return ["check", p.symbol, "--rules-b64", b64];
    },
  },
  record_signal: {
    script: "signal_tracker.py",
    argv: (p) => {
      const args = [
        "record",
        p.symbol,
        "--direction",
        p.direction,
        "--horizon-days",
        String(p.horizon_days ?? 10),
      ];
      if (p.entry_price !== undefined) args.push("--entry-price", String(p.entry_price));
      if (p.target_price !== undefined) args.push("--target-price", String(p.target_price));
      if (p.stop_price !== undefined) args.push("--stop-price", String(p.stop_price));
      if (p.source) args.push("--source", p.source);
      if (p.note) args.push("--note", p.note);
      return args;
    },
  },
  evaluate_signals: {
    script: "signal_tracker.py",
    argv: (p) => {
      const args = ["evaluate"];
      if (p.symbol) args.push("--symbol", p.symbol);
      return args;
    },
  },
  get_signal_summary: {
    script: "signal_tracker.py",
    argv: (p) => {
      const args = ["summary"];
      if (p.source) args.push("--source", p.source);
      if (p.symbol) args.push("--symbol", p.symbol);
      args.push("--status", p.status ?? "all");
      return args;
    },
  },
  parse_stock_list: {
    script: "import_parser.py",
    argv: (p) => {
      const b64 = Buffer.from(String(p.text), "utf-8").toString("base64");
      return ["parse", "--text-b64", b64];
    },
  },
  compare_stocks: {
    script: "stateless_analysis.py",
    input: (p) => p,
    argv: (p, path) => ["peers", "--input", path],
  },
  analyze_disclosure_changes: {
    script: "stateless_analysis.py",
    input: (p) => p,
    argv: (p, path) => ["disclosure", "--input", path],
  },
  analyze_portfolio_risk: {
    script: "stateless_analysis.py",
    input: (p) => p,
    argv: (p, path) => ["portfolio", "--input", path],
  },
  plan_portfolio_rebalance: {
    script: "stateless_analysis.py",
    input: (p) => p,
    argv: (p, path) => ["rebalance", "--input", path],
  },
  calculate_position_size: {
    script: "stateless_analysis.py",
    input: (p) => p,
    argv: (p, path) => ["position_size", "--input", path],
  },
  calculate_dcf_valuation: {
    script: "stateless_analysis.py",
    input: (p) => p,
    argv: (p, path) => ["dcf", "--input", path],
  },
  analyze_event_study: {
    script: "stateless_analysis.py",
    input: (p) => p,
    argv: (p, path) => ["event_study", "--input", path],
  },
  validate_backtest: {
    script: "stateless_analysis.py",
    input: (p) => p,
    argv: (p, path) => ["robustness", "--input", path],
  },
  compare_analysis_snapshots: {
    script: "stateless_analysis.py",
    input: (p) => p,
    argv: (p, path) => ["snapshot", "--input", path],
  },
};

/** Run one tool call; `run(script, argv)` resolves to the CLI's JSON text. */
export function callTool(name, args, run) {
  const { script, argv, input } = TOOL_CALLS[name];
  return input ? withJsonInput(input(args), path => run(script, argv(args, path))) : run(script, argv(args));
}
