import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { createJiti } from "jiti";
import { pythonEnvironmentRoot, venvPythonPath } from "../scripts/venv-python.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..");

// --- Capture register() callback by stubbing OpenClaw SDK ----------------

interface ToolDef {
  name: string;
  canDeliverSourceReply?: boolean;
  catalogMode?: string;
  description: string;
  parameters: { __typebox: "object"; props: Record<string, unknown> };
  execute: (id: string, params: Record<string, unknown>) => Promise<unknown>;
}

interface PluginConfig {
  id: string;
  name: string;
  description: string;
  register: (api: unknown) => void;
}

let capturedConfig: PluginConfig | null = null;

const tools: ToolDef[] = [];
const pluginRoot = mkdtempSync(join(tmpdir(), "openclaw-plugin-root-"));
writeFileSync(join(pluginRoot, "openclaw.plugin.json"), "{}");
const installedPython = venvPythonPath(pythonEnvironmentRoot(pluginRoot));
mkdirSync(dirname(installedPython), { recursive: true });
writeFileSync(installedPython, "");
mkdirSync(join(pluginRoot, "tools"));

const fakeApi = {
  rootDir: pluginRoot,
  registerTool(tool: ToolDef) {
    tools.push(tool);
  },
};

const fakeSdk = {
  definePluginEntry(config: typeof capturedConfig) {
    capturedConfig = config;
    return config;
  },
};

const fakeTypebox = {
  Type: {
    Object: (props: Record<string, unknown>) => ({
      __typebox: "object" as const,
      props,
    }),
    String: (opts?: unknown) => ({ __typebox: "string", opts }),
    Number: (opts?: unknown) => ({ __typebox: "number", opts }),
    Null: () => ({ __typebox: "null" }),
    Boolean: (opts?: unknown) => ({ __typebox: "boolean", opts }),
    Optional: (schema: unknown) => ({ __typebox: "optional", schema }),
    Union: (schemas: unknown[], opts?: unknown) => ({
      __typebox: "union",
      schemas,
      opts,
    }),
    Literal: (value: unknown) => ({ __typebox: "literal", value }),
    Array: (item: unknown, opts?: unknown) => ({
      __typebox: "array",
      item,
      opts,
    }),
  },
};

// --- Capture execFile calls without actually spawning Python --------------

const analysisInputCalls: {path: string; input: unknown}[] = [];
const execCalls: { bin: string; script: string; args: string[] }[] = [];

const jiti = createJiti(fileURLToPath(import.meta.url), {
  alias: {
    "openclaw/plugin-sdk/plugin-entry": join(here, "_stubs/openclaw-sdk.ts"),
    typebox: join(here, "_stubs/typebox.ts"),
  },
});

// stash globals the stub modules will read
(globalThis as any).__OPENCLAW_TEST__ = { fakeSdk, fakeTypebox };


const mod = await jiti.import("../openclaw/index.ts") as {
  default: unknown;
  __setExecutor: (fn: (bin: string, argv: string[]) => Promise<string>) => void;
};

mod.__setExecutor(async (bin, argv) => {
  const [scriptPath, ...rest] = argv;
  if (scriptPath.endsWith("sector_rotation.py") || scriptPath.endsWith("stateless_analysis.py") || scriptPath.endsWith("trend_strategy.py")) { const path = rest[rest.indexOf("--input") + 1]; analysisInputCalls.push({path, input: JSON.parse(readFileSync(path, "utf8"))}); }
  execCalls.push({ bin, script: scriptPath, args: rest });
  return `mock:${scriptPath}`;
});

// TS flow analysis ignores the closure assignment in fakeSdk above and narrows
// capturedConfig to null from its initializer — assert the declared type back.
const config = capturedConfig as PluginConfig | null;
if (!config) {
  console.error("FAIL: definePluginEntry was never called");
  process.exit(1);
}

config.register(fakeApi);

// --- Assertions -----------------------------------------------------------

let failures = 0;
function assert(cond: boolean, msg: string) {
  if (!cond) {
    console.error(`  FAIL: ${msg}`);
    failures++;
  }
}

const manifestPath = join(repoRoot, "openclaw/openclaw.plugin.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as {
  id: string;
  name: string;
  description: string;
  contracts: { tools: string[] };
};

console.log("Plugin entry:");
assert(config.id === manifest.id, `id mismatch: entry=${config.id} manifest=${manifest.id}`);
assert(!!config.name, "entry name empty");
assert(!!config.description, "entry description empty");

console.log("Tools registered:");
const registeredNames = tools.map((t) => t.name).sort();
const manifestNames = [...manifest.contracts.tools].sort();

const missingFromCode = manifestNames.filter((n) => !registeredNames.includes(n));
const missingFromManifest = registeredNames.filter((n) => !manifestNames.includes(n));

assert(
  missingFromCode.length === 0,
  `tools in manifest but not registered in code: ${missingFromCode.join(", ")}`
);
assert(
  missingFromManifest.length === 0,
  `tools registered in code but missing from manifest: ${missingFromManifest.join(", ")}`
);
assert(
  registeredNames.length === manifestNames.length,
  `count mismatch: code=${registeredNames.length} manifest=${manifestNames.length}`
);

const seen = new Set<string>();
for (const tool of tools) {
  assert(!seen.has(tool.name), `duplicate registration: ${tool.name}`);
  seen.add(tool.name);
  assert(!!tool.description, `${tool.name}: description empty`);
  assert(
    tool.parameters?.__typebox === "object",
    `${tool.name}: parameters must be Type.Object(...)`
  );
  assert(
    typeof tool.execute === "function",
    `${tool.name}: execute must be a function`
  );
}

if (failures === 0) {
  console.log(`  OK: ${tools.length} tools registered, manifest aligned`);
}

// --- Smoke-test every tool's execute() -----------------------------------

console.log("Tool execute() smoke test:");

const sampleArgs: Record<string, Record<string, unknown>> = {
  analyze_trend_strategy: { symbols: ["AAPL", "MSFT"] },
  calculate_rotation_metrics: {trade_counts: [{symbol: "XLK", buys: 4, wins: 3, streak: 2}]},
  backtest_rotation_allocation: {symbols: ["XLK", "XLE"], start_date: "2021-01-01", end_date: "2026-01-01"},
  "compare_stocks": {"companies": [{"symbol": "AAPL", "period": "2025", "metrics": [{"name": "pe", "value": 20, "unit": "multiple", "basis": "TTM"}]}, {"symbol": "MSFT", "period": "2025", "metrics": [{"name": "pe", "value": 30, "unit": "multiple", "basis": "TTM"}]}]},
  "analyze_disclosure_changes": {"previous": {"text": "利润下降", "source": "fixture:old", "period": "2024", "metrics": []}, "current": {"text": "利润增长", "source": "fixture:new", "period": "2025", "metrics": []}},
  "analyze_portfolio_risk": {"positions": [{"symbol": "AAPL", "price": 100, "quantity": 10, "currency": "USD"}], "base_currency": "USD", "cash": 100},
  "calculate_position_size": {"equity": 100000, "cash": 20000, "entry_price": 100, "stop_price": 95, "risk_fraction": 0.01, "lot_size": 100},
  "analyze_event_study": {"prices": [{"date": "2026-01-02", "close": 100}, {"date": "2026-01-05", "close": 110}], "benchmark_prices": [{"date": "2026-01-02", "close": 100}, {"date": "2026-01-05", "close": 100}], "event_dates": ["2026-01-05"], "price_basis": "adjusted", "horizons": [0]},
  "validate_backtest": {"datasets": [], "strategies": []},
  "compare_analysis_snapshots": {"previous": {"价格": 10}, "current": {"价格": 12}},

  get_announcements: { symbol: "600519", category: "inquiry", start_date: "2026-01-01", end_date: "2026-10-01", top: 80 },
  get_us_holdings: { cik: "0001067983" },
  get_insider_trades: { symbol: "AAPL", days: 30, top: 2 },
  get_valuation_history: { symbol: "600519" },
  get_index_valuation: { symbol: "000300" },
  get_earnings_calendar: { report_date: "2026-09-30", symbol: "600519" },
  get_shareholder_structure: { symbol: "600519", report_date: "2026-06-30" },
  get_etf_details: { symbol: "510300", benchmark: "sh000300" },
  get_dividends: { symbol: "600519" },
  get_kline: { symbol: "600519" },
  get_quote: { symbol: "600519" },
  get_capital_flow: {},
  get_news: { symbol: "600519" },
  get_financials: { symbol: "600519", periods: 4, annual: true },
  get_technical_analysis: { symbol: "600519", periods: "daily,weekly" },
  analyze_pattern: { symbol: "600519" },
  get_market_indices: {},
  get_sector_rankings: {},
  get_sector_constituents: { sector: "创新药" },
  resolve_stock_sectors: { symbol: "600519" },
  get_stock_info: { symbol: "600519" },
  get_chip_distribution: { symbol: "600519" },
  get_market_stats: {},
  get_limit_up_pool: {},
  get_dragon_tiger: {},
  get_hot_stocks: {},
  get_margin_trading: { symbol: "600519", include_ratio: true },
  get_northbound_flow: {},
  get_fundamental_context: { symbol: "600519" },
  screen_stocks: {},
  run_backtest: { strategy: "x.yaml", symbol: "600519" },
  evaluate_signal: { symbol: "600519", signal: "macd_golden_cross" },
  resolve_stock_name: { query: "贵州茅台" },
  check_trading_day: { market: "CN" },
  get_trading_days: { market: "CN" },
  get_trading_phase: { market: "CN" },
  calculate_ma: { symbol: "600519" },
  get_volume_analysis: { symbol: "600519" },
  search_stock_news: { query: "茅台" },
  search_comprehensive_intel: { symbol: "600519" },
  get_social_sentiment: { symbol: "AAPL" },
  get_trending_sentiment: {},
  extract_article: { url: "https://example.com" },
  screen_risk: { symbol: "600519" },
  detect_market_regime: {},
  get_market_review: {},
  run_watchlist_analysis: { symbols: "600519,000001" },
  detect_anomaly: { symbol: "600519" },
  diagnose_data_sources: {},
  get_market_capabilities: { market: "A" },
  render_stock_report: {
    report: {
      stock_name: "test",
      stock_code: "600519",
      decision_type: "hold",
      sentiment_score: 50,
      confidence: "low",
    },
    template: "brief",
  },
  render_market_report: { report: {}, save: true },
  build_watchlist_context: { symbols: "600519,AAPL" },
  analyze_position_context: {
    symbol: "600519",
    cost: 1500,
    quantity: 100,
    stop_loss: 1420,
    take_profit: 1680,
  },
  check_alert_rules: {
    symbol: "600519",
    rules: [{ type: "price_below", value: 1450 }],
  },
  record_signal: { symbol: "600519", direction: "buy", entry_price: 1500 },
  evaluate_signals: {},
  get_signal_summary: {},
  get_review_history: { limit: 5 },
  parse_stock_list: { text: "600519,AAPL" },
};

for (const tool of tools) {
  const args = sampleArgs[tool.name] ?? {};
  const before = execCalls.length;
  let result: any;
  try {
    result = await tool.execute("call_id_1", args);
  } catch (err) {
    assert(false, `${tool.name}: execute threw: ${(err as Error).message}`);
    continue;
  }
  assert(
    Array.isArray(result?.content) &&
      result.content[0]?.type === "text" &&
      typeof result.content[0]?.text === "string",
    `${tool.name}: result must be { content: [{ type: 'text', text: string }] }`
  );
  assert(
    execCalls.length > before,
    `${tool.name}: execute did not call Python`
  );
  const lastCall = execCalls[execCalls.length - 1];
  const scriptPath = lastCall.script.replace(/\\/g, "/"); // tolerate Windows separators
  assert(
    scriptPath.endsWith(".py") && scriptPath.includes("/tools/"),
    `${tool.name}: should invoke a script under tools/, got ${lastCall.script}`
  );
}

if (failures === 0) {
  console.log(`  OK: ${tools.length} execute() calls dispatched`);
}

// Spot-check argv mapping for the conditional params: periods>1 forwards
// `--periods N`, save=true appends a bare `--save` (no value).
const finCall = execCalls.find((c) => c.script.includes("stock_data.py") && c.args[0] === "financials");
assert(!!finCall, "get_financials: no dispatch captured");
if (finCall) {
  const idx = finCall.args.indexOf("--periods");
  assert(idx > 0 && finCall.args[idx + 1] === "4", "get_financials: periods=4 must pass --periods 4");
}
const techCall = execCalls.find((c) => c.script.includes("technical.py") && c.args[0] === "analyze");
assert(!!techCall, "get_technical_analysis: no dispatch captured");
if (techCall) {
  const idx = techCall.args.indexOf("--periods");
  assert(
    idx > 0 && techCall.args[idx + 1] === "daily,weekly",
    "get_technical_analysis: periods must pass --periods daily,weekly"
  );
}
const renderCall = execCalls.find((c) => c.script.includes("report_renderer.py") && c.args[0] === "market");
assert(!!renderCall, "render_market_report: no dispatch captured");
if (renderCall) {
  assert(
    renderCall.args[renderCall.args.length - 1] === "--save",
    "render_market_report: save=true must append a bare --save"
  );
}

// New flags and shared value-data CLI dispatch must reach Python unchanged.
assert(!!finCall && finCall.args.includes("--annual"), "get_financials: annual must append --annual");
const ratioCall = execCalls.find(c => c.args[0] === "margin_trading");
assert(!!ratioCall && ratioCall.args.includes("--include-ratio"), "get_margin_trading: ratio flag missing");
const valueCommands = ["valuation_history", "index_valuation", "earnings_calendar", "shareholder_structure", "etf_details", "dividends"];
for (const command of valueCommands) {
  assert(execCalls.some(c => c.script.includes("value_data.py") && c.args[0] === command), `${command}: shared CLI dispatch missing`);
}

// Negative gating: periods=1 / save omitted must not emit the flags.
const finTool = tools.find((t) => t.name === "get_financials");
assert(!!finTool, "get_financials: not registered");
if (finTool) {
  await finTool.execute("call_id_neg", { symbol: "600519", periods: 1 });
  const last = execCalls[execCalls.length - 1];
  assert(!last.args.includes("--periods"), "get_financials: periods=1 must not pass --periods");
}
if (finTool) {
  await finTool.execute("validation", {symbol: "600519", periods: 0});
  const last = execCalls[execCalls.length - 1];
  const idx = last.args.indexOf("--periods");
  assert(idx >= 0 && last.args[idx+1] === "0", "periods=0 must reach shared CLI validation");
}
const renderTool = tools.find((t) => t.name === "render_market_report");
assert(!!renderTool, "render_market_report: not registered");
if (renderTool) {
  await renderTool.execute("call_id_neg2", { report: {} });
  const last = execCalls[execCalls.length - 1];
  assert(!last.args.includes("--save"), "render_market_report: save omitted must not pass --save");
}

assert(execCalls.every((call) => call.bin === installedPython), "plugin tools must use installed root venv");
assert(execCalls.every((call) => dirname(call.script) === join(pluginRoot, "tools")), "plugin tools must use installed root payload");
rmSync(pluginRoot, { recursive: true, force: true });

const hkRankingTool = tools.find(t => t.name === "get_dividends")!;
await hkRankingTool.execute("hk", {mode: "ranking", market: "HK"});
const hkRankingArgs = execCalls[execCalls.length-1].args;
assert(hkRankingArgs[hkRankingArgs.indexOf("--market")+1] === "HK", "HK ranking market must reach CLI");
const hkCalendarTool = tools.find(t => t.name === "get_earnings_calendar")!;
await hkCalendarTool.execute("hk", {symbol: "0700.HK"});
const hkCalendarArgs = execCalls[execCalls.length-1].args;
assert(hkCalendarArgs[hkCalendarArgs.indexOf("--report-date")+1] === "", "HK calendar must allow omitted report_date");

const equityTool = tools.find(t => t.name === "get_etf_details")!;
await equityTool.execute("full", {symbol: "510300", include_full_holdings: true});
assert(execCalls[execCalls.length-1].args.includes("--include-full-holdings"), "full report flag must reach CLI");
await equityTool.execute("brief", {symbol: "510300"});
assert(!execCalls[execCalls.length-1].args.includes("--include-full-holdings"), "full report must be opt-in");


// Disclosure tools forward every option unchanged to the shared Python CLI.
await tools.find(t => t.name === "get_announcements")!.execute("disclosure", {symbol: "600519", category: "inquiry", start_date: "2026-01-01", end_date: "2026-10-01", top: 80});
assert(execCalls[execCalls.length - 1].script.endsWith("value_data.py") && JSON.stringify(execCalls[execCalls.length - 1].args) === JSON.stringify(["announcements", "600519", "--category", "inquiry", "--start-date", "2026-01-01", "--end-date", "2026-10-01", "--top", "80"]), "get_announcements: argv mismatch");
for (const [name, command] of [["calculate_rotation_metrics", "analyze"], ["backtest_rotation_allocation", "backtest"]]) {
  const request = name === "calculate_rotation_metrics" ? {trade_counts: [{symbol: "XLK", buys: 4, wins: 3, streak: 2}]} : {symbols: ["XLK", "XLE"], start_date: "2021-01-01", end_date: "2026-01-01"};
  const tool = tools.find(t => t.name === name)!;
  await tool.execute("rotation", request);
  const transport = analysisInputCalls.at(-1)!;
  assert(execCalls.at(-1)!.script.endsWith("sector_rotation.py") && execCalls.at(-1)!.args[0] === command, `${name}: shared core action`);
  assert(JSON.stringify(transport.input) === JSON.stringify(request), `${name}: all parameters forwarded`);
  assert(!existsSync(transport.path), `${name}: transport removed`);
}

const nativeTrendRequest = {symbols: ["AAPL", "MSFT"], held_symbols: ["AAPL"], slots: 2};
await tools.find(t => t.name === "analyze_trend_strategy")!.execute("monthly_trend", nativeTrendRequest);
const nativeTrendInput = analysisInputCalls.at(-1)!;
assert(execCalls.at(-1)!.script.endsWith("trend_strategy.py") && execCalls.at(-1)!.args[0] === "fetch", "monthly_trend: native request command");
assert(JSON.stringify(nativeTrendInput.input) === JSON.stringify(nativeTrendRequest), "monthly_trend: symbols/state reach Python");
assert(!existsSync(nativeTrendInput.path), "monthly_trend: remove internal transport file");
await tools.find(t => t.name === "get_us_holdings")!.execute("disclosure", {cik: "0001067983"});
assert(execCalls[execCalls.length - 1].script.endsWith("sec_edgar.py") && JSON.stringify(execCalls[execCalls.length - 1].args) === JSON.stringify(["holdings", "0001067983"]), "get_us_holdings: argv mismatch");
await tools.find(t => t.name === "get_insider_trades")!.execute("disclosure", {symbol: "AAPL", days: 30, top: 2});
assert(execCalls[execCalls.length - 1].script.endsWith("sec_edgar.py") && JSON.stringify(execCalls[execCalls.length - 1].args) === JSON.stringify(["insider_trades", "AAPL", "--days", "30", "--top", "2"]), "get_insider_trades: argv mismatch");
await tools.find(t => t.name === "get_us_holdings")!.execute("issuer", {cik: "0000320193", mode: "issuer"});
assert(JSON.stringify(execCalls[execCalls.length - 1].args) === JSON.stringify(["holdings", "0000320193", "--mode", "issuer"]), "issuer mode must reach CLI");


const analysisFixtures = JSON.parse(readFileSync(new URL("./fixtures/stateless_analysis.json", import.meta.url), "utf8")) as Record<string, {command: string; args: Record<string, unknown>}>;
for (const [name, fixture] of Object.entries(analysisFixtures)) {
  await tools.find(t => t.name === name)!.execute("stateless", fixture.args);
  const captured = analysisInputCalls.at(-1)!;
  assert(JSON.stringify(captured.input) === JSON.stringify(fixture.args), `${name}: input must reach CLI unchanged`);
  assert(!existsSync(captured.path), `${name}: temporary input must be removed`);
  assert(execCalls.at(-1)!.args[0] === fixture.command, `${name}: wrong CLI command`);
}

const evidencePayload = JSON.stringify({price: 100, source: "fixture", fetched_at: "2026-10-14T00:00:00+00:00", data_as_of: null});
mod.__setExecutor(async () => evidencePayload);
const evidenceTool = tools.find(t => t.name === "get_quote")!;
const evidenceResult = await evidenceTool.execute("evidence", {symbol: "600519"}) as {content: {text: string}[]};
assert(evidenceResult.content[0].text === evidencePayload, "quote evidence must pass through unchanged");

const temperaturePayload = readFileSync(join(repoRoot, "tests/fixtures/trend_temperature.json"), "utf8");
mod.__setExecutor(async () => temperaturePayload);
const temperatureTool = tools.find(t => t.name === "get_technical_analysis")!;
const temperatureResult = await temperatureTool.execute("temperature", {symbol: "TEST"}) as {content: {text: string}[]};
assert(temperatureResult.content[0].text === temperaturePayload, "technical analysis must preserve experimental temperature metadata");
assert(temperatureTool.description.includes("实验性") && temperatureTool.description.includes("非星球指标") && temperatureTool.description.includes("validation_status=not_validated") && temperatureTool.description.includes("direction") && temperatureTool.description.includes("trend_horizons"), "temperature provenance must reach the model");

for (const name of ["render_stock_report", "render_market_report"]) {
  const tool = tools.find(t => t.name === name)!;
  assert(tool.canDeliverSourceReply === true && tool.catalogMode === "direct-only", `${name}: direct final delivery capability`);
  for (const final of [false, true]) {
    const payload = JSON.stringify({format: "markdown", content: "# 报告\n价格 100.25\n", ...(final ? {final: true} : {})});
    mod.__setExecutor(async (_bin, argv) => {
      assert(argv.includes("--final") === final, `${name}: final flag is explicit`);
      return payload;
    });
    const result: any = await tool.execute("report", {report: {}, final});
    assert(result.content[0].text === payload, `${name}: preserve raw JSON for old hosts and code mode`);
    assert(final ? result.details?.sourceReply?.text === "# 报告\n价格 100.25\n" : !result.details?.sourceReply, `${name}: deliver only final Markdown`);
  }
  for (const payload of ["invalid-json", "null", '{"final":true,"error":"failed"}', '{"final":true,"format":"markdown","content":"   "}', '{"final":true,"format":"html","content":"<b>report</b>"}', '{"final":true,"format":"markdown","content":"report","error":""}']) {
    mod.__setExecutor(async () => payload);
    const result: any = await tool.execute("report", {report: {}, final: true});
    assert(!result.details?.sourceReply && result.content[0].text === payload, `${name}: invalid/error output cannot end turn`);
  }
}
assert(tools.filter(t => t.canDeliverSourceReply).length === 2, "only renderers may deliver a source reply");

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nAll OpenClaw integration checks passed");
