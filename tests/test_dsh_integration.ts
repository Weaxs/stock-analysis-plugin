// Layer 1 integration: dsh plugin registration.
//
// Loads dsh/index.ts via jiti with @deepseek-ai/dsh-tools aliased to a stub
// (defineTool is identity, so the options object is captured verbatim).
// Verifies:
//   - the plugin exports the cordis function-plugin form (name/inject/apply)
//   - all 39 tools register, aligned with the canonical cross-host tool list
//     (openclaw.plugin.json contracts.tools — the repo invariant says every
//     host exposes the same set)
//   - the dsh bundle manifest (package.json dsh.bundle.patch + cordis.patch.yml)
//     is wired for the profile loader
//   - every tool's execute() dispatches to a tools/*.py CLI (stubbed executor)
//   - every skills/*/SKILL.md registers as a dsh skill

import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createJiti } from "jiti";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..");

interface ToolDef {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  output: {
    schema: { type: string };
    render: (args: unknown, value: string) => { type: string; text: string }[];
  };
  execute: (args: Record<string, unknown>, exec: unknown) => Promise<string>;
}

const tools: ToolDef[] = [];
const skills: { name: string; description: string; content: string; path?: string }[] = [];

const fakeCtx = {
  tools: {
    register(tool: ToolDef) {
      tools.push(tool);
    },
  },
  skills: {
    register(skill: (typeof skills)[number]) {
      skills.push(skill);
    },
  },
};

// --- Capture execFile calls without actually spawning Python --------------

const execCalls: { script: string; args: string[] }[] = [];

const jiti = createJiti(fileURLToPath(import.meta.url), {
  alias: {
    "@deepseek-ai/dsh-tools": join(here, "_stubs/dsh-tools.ts"),
  },
});

const mod = (await jiti.import("../dsh/index.ts")) as {
  name: string;
  inject: string[];
  apply: (ctx: unknown) => void;
  __setExecutor: (fn: (bin: string, argv: string[]) => Promise<string>) => void;
};

mod.__setExecutor(async (_bin, argv) => {
  const [scriptPath, ...rest] = argv;
  execCalls.push({ script: scriptPath, args: rest });
  return `mock:${scriptPath}`;
});

mod.apply(fakeCtx);

// --- Assertions -----------------------------------------------------------

let failures = 0;
function assert(cond: boolean, msg: string) {
  if (!cond) {
    console.error(`  FAIL: ${msg}`);
    failures++;
  }
}

console.log("Plugin entry:");
assert(mod.name === "stock-analysis", `plugin name: ${mod.name}`);
assert(mod.inject.includes("tools"), "inject must include 'tools'");
assert(mod.inject.includes("skills"), "inject must include 'skills'");

console.log("Bundle manifest:");
const pkg = JSON.parse(readFileSync(join(repoRoot, "dsh/package.json"), "utf8")) as {
  name: string;
  dsh?: { bundle?: { patch?: string } };
};
assert(pkg.dsh?.bundle?.patch === "./cordis.patch.yml", "package.json must declare dsh.bundle.patch");
const patch = readFileSync(join(repoRoot, "dsh/cordis.patch.yml"), "utf8");
assert(patch.includes("insert:"), "cordis.patch.yml must insert an entry");
assert(
  patch.includes(`name: "${pkg.name}"`) || patch.includes(`name: '${pkg.name}'`),
  `cordis.patch.yml must reference the package name ${pkg.name}`
);

console.log("Tools registered:");
const canonical = JSON.parse(
  readFileSync(join(repoRoot, "openclaw/openclaw.plugin.json"), "utf8")
) as { contracts: { tools: string[] } };
const registeredNames = tools.map((t) => t.name).sort();
const canonicalNames = [...canonical.contracts.tools].sort();

const missingFromCode = canonicalNames.filter((n) => !registeredNames.includes(n));
const extraInCode = registeredNames.filter((n) => !canonicalNames.includes(n));
assert(
  missingFromCode.length === 0,
  `tools in canonical list but not registered: ${missingFromCode.join(", ")}`
);
assert(
  extraInCode.length === 0,
  `tools registered but missing from canonical list: ${extraInCode.join(", ")}`
);
assert(
  registeredNames.length === canonicalNames.length,
  `count mismatch: code=${registeredNames.length} canonical=${canonicalNames.length}`
);

const seen = new Set<string>();
for (const tool of tools) {
  assert(!seen.has(tool.name), `duplicate registration: ${tool.name}`);
  seen.add(tool.name);
  assert(!!tool.description, `${tool.name}: description empty`);
  assert(
    tool.parameters !== null && typeof tool.parameters === "object",
    `${tool.name}: parameters must be a schema map`
  );
  assert(tool.output?.schema?.type === "string", `${tool.name}: output schema must be a string`);
  const blocks = tool.output?.render({}, "x");
  assert(
    Array.isArray(blocks) && blocks[0]?.type === "text" && blocks[0]?.text === "x",
    `${tool.name}: output.render must relay the value as one text block`
  );
  assert(typeof tool.execute === "function", `${tool.name}: execute must be a function`);
}

if (failures === 0) {
  console.log(`  OK: ${tools.length} tools registered, canonical list aligned`);
}

// --- Smoke-test every tool's execute() -----------------------------------

console.log("Tool execute() smoke test:");

const sampleArgs: Record<string, Record<string, unknown>> = {
  get_kline: { symbol: "600519" },
  get_quote: { symbol: "600519" },
  get_capital_flow: {},
  get_news: { symbol: "600519" },
  get_financials: { symbol: "600519" },
  get_technical_analysis: { symbol: "600519" },
  analyze_pattern: { symbol: "600519" },
  get_market_indices: {},
  get_sector_rankings: {},
  get_stock_info: { symbol: "600519" },
  get_chip_distribution: { symbol: "600519" },
  get_market_stats: {},
  get_fundamental_context: { symbol: "600519" },
  screen_stocks: {},
  run_backtest: { strategy: "x.yaml", symbol: "600519" },
  evaluate_signal: { symbol: "600519", signal: "macd_golden_cross" },
  resolve_stock_name: { query: "贵州茅台" },
  check_trading_day: { market: "CN" },
  get_trading_days: { market: "CN" },
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
  render_market_report: { report: {} },
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
  parse_stock_list: { text: "600519,AAPL" },
};

for (const tool of tools) {
  const args = sampleArgs[tool.name] ?? {};
  const before = execCalls.length;
  let result: string | undefined;
  try {
    result = await tool.execute(args, {});
  } catch (err) {
    assert(false, `${tool.name}: execute threw: ${(err as Error).message}`);
    continue;
  }
  assert(typeof result === "string", `${tool.name}: execute must return the raw stdout string`);
  assert(execCalls.length > before, `${tool.name}: execute did not call Python`);
  const lastCall = execCalls[execCalls.length - 1];
  const scriptPath = lastCall.script.replace(/\\/g, "/"); // tolerate Windows separators
  assert(
    scriptPath.endsWith(".py") && scriptPath.includes("/tools/"),
    `${tool.name}: should invoke a script under tools/, got ${lastCall.script}`
  );
}

// Spot-check argv mapping on the base64-carrying tools.
const alertCall = execCalls.find((c) => c.script.includes("alert_rules.py"));
assert(!!alertCall, "check_alert_rules: no dispatch captured");
if (alertCall) {
  const idx = alertCall.args.indexOf("--rules-b64");
  const decoded = JSON.parse(Buffer.from(alertCall.args[idx + 1], "base64").toString("utf-8"));
  assert(
    decoded[0]?.type === "price_below" && decoded[0]?.value === 1450,
    "check_alert_rules: rules must round-trip through --rules-b64"
  );
}
const parseCall = execCalls.find((c) => c.script.includes("import_parser.py"));
assert(!!parseCall, "parse_stock_list: no dispatch captured");
if (parseCall) {
  const idx = parseCall.args.indexOf("--text-b64");
  const decoded = Buffer.from(parseCall.args[idx + 1], "base64").toString("utf-8");
  assert(decoded === "600519,AAPL", "parse_stock_list: text must round-trip through --text-b64");
}

if (failures === 0) {
  console.log(`  OK: ${tools.length} execute() calls dispatched`);
}

// --- Skills ---------------------------------------------------------------

console.log("Skills registered:");
const skillDirs = readdirSync(join(repoRoot, "skills"), { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => d.name)
  .sort();
const skillNames = skills.map((s) => s.name).sort();
assert(
  JSON.stringify(skillNames) === JSON.stringify(skillDirs),
  `skills mismatch: registered=${skillNames.join(",")} dirs=${skillDirs.join(",")}`
);
for (const skill of skills) {
  assert(!!skill.description, `skill ${skill.name}: description empty`);
  assert(skill.content.length > 0, `skill ${skill.name}: content empty`);
  assert(!skill.content.startsWith("---"), `skill ${skill.name}: frontmatter not stripped`);
  assert(
    skill.path?.replace(/\\/g, "/").endsWith(`${skill.name}/SKILL.md`),
    `skill ${skill.name}: path should point at its SKILL.md`
  );
}

if (failures === 0) {
  console.log(`  OK: ${skills.length} skills registered`);
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nAll dsh integration checks passed");
