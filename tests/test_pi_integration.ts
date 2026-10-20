import { existsSync, readFileSync } from "fs";
import { fileURLToPath } from "url";
import { createJiti } from "jiti";

const EXPECTED_TOOLS = [
  "compare_stocks",
  "analyze_trend_strategy",
  "calculate_rotation_metrics",
  "backtest_rotation_allocation",
  "analyze_disclosure_changes",
  "analyze_portfolio_risk",
  "calculate_position_size",
  "analyze_event_study",
  "validate_backtest",
  "compare_analysis_snapshots",
  "get_announcements",
  "get_us_holdings",
  "get_insider_trades",
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
  "sector-rotation",
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
  defaultActive?: boolean;
  description: string;
  parameters: Record<string, unknown>;
  execute?: Function;
}

const registeredTools: ToolRegistration[] = [];
let skillPaths: string[] = [];

const analysisInputCalls: {path: string; input: unknown}[] = [];
const calls: string[][] = [];
const timeouts: (number | undefined)[] = [];
let stdoutPayload = "{}";
let processKilled = false;
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
  exec: async (_python: string, args: string[], options?: { timeout?: number }) => { calls.push(args); if (args[0].endsWith("sector_rotation.py") || args[0].endsWith("stateless_analysis.py") || args[0].endsWith("trend_strategy.py")) { const path = args[args.indexOf("--input") + 1]; analysisInputCalls.push({path, input: JSON.parse(readFileSync(path, "utf8"))}); } timeouts.push(options?.timeout); return { stdout: stdoutPayload, code: processKilled ? 1 : 0, killed: processKilled }; },
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


for (const [name, command] of [["calculate_rotation_metrics", "analyze"], ["backtest_rotation_allocation", "backtest"]]) {
  const request = name === "calculate_rotation_metrics" ? {trade_counts: [{symbol: "XLK", buys: 4, wins: 3, streak: 2}]} : {symbols: ["XLK", "XLE"], start_date: "2021-01-01", end_date: "2026-01-01"};
  const tool = registeredTools.find(t => t.name === name)!;
  await tool.execute!("rotation", request);
  const transport = analysisInputCalls.at(-1)!;
  assert(calls.at(-1)![0].endsWith("sector_rotation.py") && calls.at(-1)![1] === command, `${name}: shared core action`);
  assert(JSON.stringify(transport.input) === JSON.stringify(request), `${name}: all parameters forwarded`);
  assert(!existsSync(transport.path), `${name}: transport removed`);
}

const nativeTrendRequest = {symbols: ["AAPL", "MSFT"], held_symbols: ["AAPL"], slots: 2};
await registeredTools.find(t => t.name === "analyze_trend_strategy")!.execute!("monthly_trend", nativeTrendRequest);
const nativeTrendInput = analysisInputCalls.at(-1)!;
assert(calls.at(-1)![0].endsWith("trend_strategy.py") && calls.at(-1)![1] === "fetch", "monthly_trend: native request command");
assert(JSON.stringify(nativeTrendInput.input) === JSON.stringify(nativeTrendRequest), "monthly_trend: symbols/state reach Python");
assert(!existsSync(nativeTrendInput.path), "monthly_trend: remove internal transport file");
assert(timeouts.at(-1) === 690000, "monthly_trend: bounded batch fetch timeout");

// Disclosure tools forward every option unchanged to the shared Python CLI.
await registeredTools.find(t => t.name === "get_announcements")!.execute!("disclosure", {symbol: "600519", category: "inquiry", start_date: "2026-01-01", end_date: "2026-10-01", top: 80});
assert(calls[calls.length - 1].some(x => x.endsWith("value_data.py")) && JSON.stringify(calls[calls.length - 1].slice(1)) === JSON.stringify(["announcements", "600519", "--category", "inquiry", "--start-date", "2026-01-01", "--end-date", "2026-10-01", "--top", "80"]), "get_announcements: argv mismatch");
await registeredTools.find(t => t.name === "get_us_holdings")!.execute!("disclosure", {cik: "0001067983"});
assert(calls[calls.length - 1].some(x => x.endsWith("sec_edgar.py")) && JSON.stringify(calls[calls.length - 1].slice(1)) === JSON.stringify(["holdings", "0001067983"]), "get_us_holdings: argv mismatch");
await registeredTools.find(t => t.name === "get_insider_trades")!.execute!("disclosure", {symbol: "AAPL", days: 30, top: 2});
assert(calls[calls.length - 1].some(x => x.endsWith("sec_edgar.py")) && JSON.stringify(calls[calls.length - 1].slice(1)) === JSON.stringify(["insider_trades", "AAPL", "--days", "30", "--top", "2"]), "get_insider_trades: argv mismatch");

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

const analysisFixtures = JSON.parse(readFileSync(new URL("./fixtures/stateless_analysis.json", import.meta.url), "utf8")) as Record<string, {command: string; args: Record<string, unknown>}>;
for (const [name, fixture] of Object.entries(analysisFixtures)) {
  await registeredTools.find(t => t.name === name)!.execute!("stateless", fixture.args);
  const captured = analysisInputCalls.at(-1)!;
  assert(JSON.stringify(captured.input) === JSON.stringify(fixture.args), `${name}: input must reach CLI unchanged`);
  assert(!existsSync(captured.path), `${name}: temporary input must be removed`);
  assert(calls.at(-1)![1] === fixture.command, `${name}: wrong CLI command`);
}

if (failures) process.exit(1);

stdoutPayload = "";
processKilled = true;
const timedOut = await registeredTools.find(tool => tool.name === "parse_stock_list")!.execute!("timeout", {text: "AAPL"});
assert(JSON.parse(timedOut.content[0].text).error === "Python command timed out", "Pi timeout must produce a JSON error");
processKilled = false;
const emptyOutput = await registeredTools.find(tool => tool.name === "parse_stock_list")!.execute!("empty-output", {text: "AAPL"});
assert(JSON.parse(emptyOutput.content[0].text).error === "Python command returned no JSON", "Pi empty stdout must produce a JSON error");
stdoutPayload = "{}";
if (failures) process.exit(1);

await registeredTools.find(t => t.name === "get_etf_details")!.execute!("full", {symbol: "510300", include_full_holdings: true});
assert(calls[calls.length-1].includes("--include-full-holdings"), "full equity report flag must reach CLI");
await registeredTools.find(t => t.name === "get_etf_details")!.execute!("brief", {symbol: "510300"});
assert(!calls[calls.length-1].includes("--include-full-holdings"), "full report must be opt-in");
await registeredTools.find(t => t.name === "get_us_holdings")!.execute!("issuer", {cik: "0000320193", mode: "issuer"});
assert(JSON.stringify(calls[calls.length - 1].slice(1)) === JSON.stringify(["holdings", "0000320193", "--mode", "issuer"]), "issuer mode must reach CLI");

if (failures) process.exit(1);

stdoutPayload = JSON.stringify({price: 100, source: "fixture", fetched_at: "2026-10-14T00:00:00+00:00", data_as_of: null});
const quoteEvidence = await registeredTools.find(t => t.name === "get_quote")!.execute!("evidence", {symbol: "600519"});
assert(quoteEvidence.content[0].text === stdoutPayload, "quote evidence must pass through unchanged");
assert(timeouts.at(-1) === 150000, "quote host budget must leave 30 seconds for CLI exit");
await registeredTools.find(t => t.name === "screen_stocks")!.execute!("budget", {market: "A"});
assert(timeouts.at(-1) === 1860000, "screen host must cover all L2 worker waves");
await registeredTools.find(t => t.name === "get_us_holdings")!.execute!("budget", {cik: "320193"});
assert(timeouts.at(-1) === 330000, "holdings host must cover cold download budget");
if (failures) process.exit(1);

// The same plugin uses native discovery when available and stays eager otherwise.
for (const mode of ["legacy", "legacy-metadata", "native", "no-search", "search-blocked"] as const) {
  const tools = new Map<string, ToolRegistration & { exposure?: string }>();
  tools.set("read", {name: "read", description: "Read files", parameters: {}});
  tools.set("other_plugin", {name: "other_plugin", description: "Other plugin", parameters: {}});
  if (mode === "native" || mode === "search-blocked") tools.set("tool_search", {
    name: "tool_search", description: "Discover tools", parameters: {}, exposure: "model-only",
  });
  if (mode === "legacy-metadata") tools.set("tool_search", {
    name: "tool_search", description: "Older search extension", parameters: {},
  });
  let active = ["read", "other_plugin"];
  const handlers = new Map<string, Function>();
  const host = {
    ...mockPi,
    registerTool(tool: ToolRegistration & {exposure?: string}) {
      const previous = tools.get(tool.name);
      tools.set(tool.name, tool);
      if (!tool.exposure && tool.defaultActive !== false && (!previous || previous.exposure === "deferred" || previous.defaultActive === false) && !active.includes(tool.name)) active.push(tool.name);
    },
    on(event: string, handler: Function) { handlers.set(event, handler); },
    ...(mode === "legacy" ? {} : {
      getAllTools: () => [...tools.values()],
      getActiveTools: () => [...active],
      setActiveTools: (names: string[]) => { active = mode === "search-blocked" ? names.filter(name => name !== "tool_search") : names; },
    }),
  };
  extensionModule.default(host);
  await handlers.get("session_start")?.({}, {});
  if (mode === "native") {
    assert(EXPECTED_TOOLS.every(name => tools.get(name)?.exposure === "deferred"), "native Pi: stock tools must be deferred");
    assert(JSON.stringify(active.slice().sort()) === JSON.stringify(["other_plugin", "read", "tool_search"]), "native Pi: only search is activated, other tools are preserved");
    const hint = await handlers.get("before_agent_start")?.({}, {});
    assert(hint?.message?.content.includes("English") && hint.message.content.includes("tool_search"), "native Pi: tell the model how to discover Chinese-described tools");
    const quote = tools.get("get_quote")!;
    active.push("get_quote"); // Native tool_search loads a matching registered definition.
    await quote.execute!("discovery", {symbol: "600519"});
    assert(calls.at(-1)?.includes("600519") === true, "native Pi: discovered quote still calls the Python CLI");
    await handlers.get("session_start")?.({}, {});
    assert(active.includes("get_quote"), "native Pi: repeated startup must preserve loaded tools");
    extensionModule.default(host); // Reload creates a fresh extension factory and preserves host activity.
    await handlers.get("session_start")?.({}, {});
    assert(active.includes("get_quote"), "native Pi: reloading the extension must preserve loaded tools");
    assert(!active.includes("run_backtest"), "native Pi: reload must not expose unloaded tools");
    active = active.filter(name => name !== "tool_search");
    const disabledHint = await handlers.get("before_agent_start")?.({}, {});
    assert(EXPECTED_TOOLS.every(name => active.includes(name) && !tools.get(name)?.exposure), "native Pi: disabling search restores all stock tools");
    assert(!disabledHint, "native Pi: disabled search must not leave discovery instructions");
  } else {
    assert(EXPECTED_TOOLS.every(name => active.includes(name) && !tools.get(name)?.exposure), `${mode}: preserve all stock tools`);
    assert(!handlers.get("before_agent_start") || !(await handlers.get("before_agent_start")!({}, {})), `${mode}: no unavailable discovery instructions`);
  }
}
if (failures) process.exit(1);

const temperaturePayload = readFileSync(new URL("./fixtures/trend_temperature.json", import.meta.url), "utf8");
stdoutPayload = temperaturePayload;
const temperatureTool = registeredTools.find(t => t.name === "get_technical_analysis")!;
const temperatureResult = await temperatureTool.execute!("temperature", {symbol: "TEST"});
assert(temperatureResult.content[0].text === temperaturePayload, "technical analysis must preserve experimental temperature metadata");
assert(temperatureTool.description.includes("实验性") && temperatureTool.description.includes("非星球指标") && temperatureTool.description.includes("validation_status=not_validated") && temperatureTool.description.includes("direction") && temperatureTool.description.includes("trend_horizons"), "temperature provenance must reach the model");
if (failures) process.exit(1);
