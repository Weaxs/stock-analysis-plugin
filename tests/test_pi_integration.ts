import { existsSync } from "fs";
import { fileURLToPath } from "url";
import { createJiti } from "jiti";

const EXPECTED_TOOLS = [
  "get_valuation_history",
  "get_index_valuation",
  "get_earnings_calendar",
  "get_shareholder_structure",
  "get_etf_details",
  "get_dividends",
  "get_kline",
  "get_quote",
  "get_capital_flow",
  "get_news",
  "get_financials",
  "get_technical_analysis",
  "analyze_pattern",
  "get_market_indices",
  "get_sector_rankings",
  "get_sector_constituents",
  "resolve_stock_sectors",
  "get_stock_info",
  "get_chip_distribution",
  "get_market_stats",
  "get_limit_up_pool",
  "get_dragon_tiger",
  "get_hot_stocks",
  "get_margin_trading",
  "get_northbound_flow",
  "get_fundamental_context",
  "screen_stocks",
  "run_backtest",
  "evaluate_signal",
  "resolve_stock_name",
  "check_trading_day",
  "get_trading_days",
  "get_trading_phase",
  "calculate_ma",
  "get_volume_analysis",
  "search_stock_news",
  "search_comprehensive_intel",
  "get_social_sentiment",
  "get_trending_sentiment",
  "extract_article",
  "screen_risk",
  "detect_market_regime",
  "get_market_review",
  "run_watchlist_analysis",
  "detect_anomaly",
  "diagnose_data_sources",
  "get_market_capabilities",
  "render_stock_report",
  "render_market_report",
  "build_watchlist_context",
  "analyze_position_context",
  "check_alert_rules",
  "record_signal",
  "evaluate_signals",
  "get_signal_summary",
  "get_review_history",
  "parse_stock_list",
].sort();

const EXPECTED_SKILLS = [
  "bottom-volume",
  "box-oscillation",
  "bull-trend",
  "chan-theory",
  "dragon-head",
  "emotion-cycle",
  "event-driven",
  "expectation-repricing",
  "growth-quality",
  "hot-theme",
  "ma-crossover",
  "market-review",
  "one-yang-three-yin",
  "shrink-pullback",
  "stock-analysis",
  "stock-screener",
  "strategy-backtest",
  "volume-breakout",
  "wave-theory",
  "wisburg-research",
].sort();

interface ToolRegistration {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  execute?: Function;
}

const registeredTools: ToolRegistration[] = [];
let skillPaths: string[] = [];

const calls: string[][] = [];
const mockPi = {
  registerTool(config: ToolRegistration & { execute: Function }) {
    registeredTools.push({
      name: config.name,
      description: config.description,
      parameters: config.parameters,
      execute: config.execute,
    });
  },
  on(event: string, handler: Function) {
    if (event === "resources_discover") {
      const result = handler();
      skillPaths = result.skillPaths ?? [];
    }
  },
  exec: async (_python: string, args: string[]) => { calls.push(args); return { stdout: "{}", exitCode: 0 }; },
};

const jiti = createJiti(fileURLToPath(import.meta.url));
const extensionModule = jiti("../pi/index.ts") as { default: Function };
extensionModule.default(mockPi);

let failures = 0;

function assert(condition: boolean, message: string) {
  if (!condition) {
    console.error(`  FAIL: ${message}`);
    failures++;
  }
}

// --- Tools ---
console.log("Tools:");

const toolNames = registeredTools.map((t) => t.name).sort();
assert(
  toolNames.length === EXPECTED_TOOLS.length,
  `expected ${EXPECTED_TOOLS.length} tools, got ${toolNames.length}`
);

const missingTools = EXPECTED_TOOLS.filter((n) => !toolNames.includes(n));
const extraTools = toolNames.filter((n) => !EXPECTED_TOOLS.includes(n));
assert(missingTools.length === 0, `missing tools: ${missingTools.join(", ")}`);
assert(extraTools.length === 0, `unexpected tools: ${extraTools.join(", ")}`);

for (const tool of registeredTools) {
  assert(!!tool.description, `${tool.name}: missing description`);
  assert(
    (tool.parameters as any)?.type === "object",
    `${tool.name}: parameters.type should be "object"`
  );
}

if (missingTools.length === 0 && extraTools.length === 0) {
  console.log(`  OK: ${toolNames.length} tools registered`);
}

// --- Skills ---
console.log("Skills:");

const skillNames = skillPaths
  .map((p) => {
    const parts = p.split("/");
    return parts[parts.length - 2];
  })
  .sort();

assert(
  skillNames.length === EXPECTED_SKILLS.length,
  `expected ${EXPECTED_SKILLS.length} skills, got ${skillNames.length}`
);

const missingSkills = EXPECTED_SKILLS.filter((n) => !skillNames.includes(n));
const extraSkills = skillNames.filter((n) => !EXPECTED_SKILLS.includes(n));
assert(
  missingSkills.length === 0,
  `missing skills: ${missingSkills.join(", ")}`
);
assert(
  extraSkills.length === 0,
  `unexpected skills: ${extraSkills.join(", ")}`
);

for (const p of skillPaths) {
  assert(existsSync(p), `SKILL.md not found: ${p}`);
}

if (missingSkills.length === 0 && extraSkills.length === 0) {
  console.log(`  OK: ${skillNames.length} skills registered`);
}

// --- Summary ---
if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
} else {
  console.log("\nAll Pi integration checks passed");
}

for (const [name, args, command] of [
  ["get_valuation_history", {symbol: "600519", years: 10}, "valuation_history"],
  ["get_index_valuation", {symbol: "000300"}, "index_valuation"],
  ["get_earnings_calendar", {report_date: "2026-09-30"}, "earnings_calendar"],
  ["get_shareholder_structure", {symbol: "0700.HK"}, "shareholder_structure"],
  ["get_etf_details", {symbol: "510300", benchmark: "sh000300"}, "etf_details"],
  ["get_dividends", {mode: "ranking"}, "dividends"],
] as const) {
  const tool = registeredTools.find(t => t.name === name);
  await tool!.execute!("test", args);
  const argv = calls[calls.length - 1];
  assert(argv[0].endsWith("value_data.py") && argv[1] === command, `${name}: CLI dispatch missing`);
}
await registeredTools.find(t => t.name === "get_financials")!.execute!("test", {symbol: "600519", periods: 10, annual: true});
assert(calls[calls.length-1].includes("--annual"), "annual flag missing");
await registeredTools.find(t => t.name === "get_margin_trading")!.execute!("test", {symbol: "600519", include_ratio: true});
assert(calls[calls.length-1].includes("--include-ratio"), "margin ratio flag missing");

await registeredTools.find(t => t.name === "get_financials")!.execute!("validation", {symbol: "600519", periods: 0});
const invalidArgs = calls[calls.length-1];
assert(invalidArgs[invalidArgs.indexOf("--periods")+1] === "0", "periods=0 must reach shared CLI validation");

await registeredTools.find(t => t.name === "get_dividends")!.execute!("hk", {mode: "ranking", market: "HK"});
let hkArgs = calls[calls.length-1];
assert(hkArgs[hkArgs.indexOf("--market")+1] === "HK", "HK ranking market must reach CLI");
await registeredTools.find(t => t.name === "get_earnings_calendar")!.execute!("hk", {symbol: "0700.HK"});
hkArgs = calls[calls.length-1];
assert(hkArgs[hkArgs.indexOf("--report-date")+1] === "", "HK calendar must allow omitted report_date");
if (failures) process.exit(1);
