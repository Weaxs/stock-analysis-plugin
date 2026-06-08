import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createJiti } from "jiti";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..");

// --- Capture register() callback by stubbing OpenClaw SDK ----------------

interface ToolDef {
  name: string;
  description: string;
  parameters: { __typebox: "object"; props: Record<string, unknown> };
  execute: (id: string, params: Record<string, unknown>) => Promise<unknown>;
}

let capturedConfig: {
  id: string;
  name: string;
  description: string;
  register: (api: unknown) => void;
} | null = null;

const tools: ToolDef[] = [];

const fakeApi = {
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
    Optional: (schema: unknown) => ({ __typebox: "optional", schema }),
    Union: (schemas: unknown[], opts?: unknown) => ({
      __typebox: "union",
      schemas,
      opts,
    }),
    Literal: (value: unknown) => ({ __typebox: "literal", value }),
  },
};

// --- Capture execFile calls without actually spawning Python --------------

const execCalls: { script: string; args: string[] }[] = [];

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

mod.__setExecutor(async (_bin, argv) => {
  const [scriptPath, ...rest] = argv;
  execCalls.push({ script: scriptPath, args: rest });
  return `mock:${scriptPath}`;
});

if (!capturedConfig) {
  console.error("FAIL: definePluginEntry was never called");
  process.exit(1);
}

capturedConfig.register(fakeApi);

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
assert(capturedConfig.id === manifest.id, `id mismatch: entry=${capturedConfig.id} manifest=${manifest.id}`);
assert(!!capturedConfig.name, "entry name empty");
assert(!!capturedConfig.description, "entry description empty");

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
  assert(
    lastCall.script.endsWith(".py") && lastCall.script.includes("/tools/"),
    `${tool.name}: should invoke a script under tools/, got ${lastCall.script}`
  );
}

if (failures === 0) {
  console.log(`  OK: ${tools.length} execute() calls dispatched`);
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nAll OpenClaw integration checks passed");
