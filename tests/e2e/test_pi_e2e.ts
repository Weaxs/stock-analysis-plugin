// Layer 1 e2e: Pi host → real Python subprocess → JSON round-trip.
// Complements test_pi_integration.ts (which mocks the executor).
//
// Runs 4 non-network tools end-to-end. Anything that needs akshare/yfinance/LLM
// belongs in a later layer, not here.

import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { existsSync } from "fs";
import { createJiti } from "jiti";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..");
const isWin = process.platform === "win32";
const venvPython = join(
  repoRoot,
  ".venv",
  isWin ? "Scripts" : "bin",
  "python3"
);
const python = existsSync(venvPython) ? venvPython : "python3";

// --- Load pi/index.ts and capture the tool registrations ------------------

interface ToolResult {
  content: { type: string; text: string }[];
  details: unknown;
}
interface Registered {
  name: string;
  execute: (toolCallId: string, args: Record<string, unknown>) => Promise<ToolResult>;
}
const registered: Registered[] = [];

const mockPi = {
  registerTool(cfg: Registered) {
    registered.push(cfg);
  },
  on() {},
  exec: async (cmd: string, args: string[] = []) => {
    // pi/index.ts calls pi.exec(python, [script, ...args]) — argv form, no shell.
    const { stdout, stderr } = await execFileAsync(cmd, args, {
      maxBuffer: 32 * 1024 * 1024,
    });
    return { stdout, stderr, code: 0, killed: false };
  },
};

const jiti = createJiti(fileURLToPath(import.meta.url));
const mod = (await jiti.import("../../pi/index.ts")) as {
  default: (pi: unknown) => void;
};

// pi/index.ts uses its own venv detection via __dirname/../.venv — fine on dev
// machines. On CI, sys.executable === python3 already resolves.
mod.default(mockPi);

// --- Assertions -----------------------------------------------------------

let failures = 0;
function assert(cond: boolean, msg: string) {
  if (!cond) {
    console.error(`  FAIL: ${msg}`);
    failures++;
  }
}

async function callTool(name: string, args: Record<string, unknown>): Promise<any> {
  const tool = registered.find((t) => t.name === name);
  if (!tool) throw new Error(`tool not registered: ${name}`);
  const result = await tool.execute("e2e-call", args);
  return JSON.parse(result.content[0].text);
}

console.log(`Pi e2e host→python round-trip (python: ${python})`);

// 1. get_market_capabilities — static, no network
{
  const out = await callTool("get_market_capabilities", { market: "HK" });
  assert(out.market === "HK", "capabilities: market should be HK");
  assert(out.meta?.provider === "capabilities", "capabilities: meta.provider");
  assert(
    Array.isArray(out.supported) && out.supported.includes("get_kline"),
    "capabilities: HK supports get_kline"
  );
  console.log("  OK: get_market_capabilities");
}

// 2. parse_stock_list — regex + resolver (resolver hits network but stops at non-CN inputs)
{
  const out = await callTool("parse_stock_list", { text: "600519 00700.HK AAPL" });
  const symbols = new Set(out.items.map((i: any) => i.symbol));
  assert(symbols.has("600519"), "parse: has 600519");
  assert(symbols.has("00700.HK"), "parse: has 00700.HK");
  assert(symbols.has("AAPL"), "parse: has AAPL");
  console.log("  OK: parse_stock_list");
}

// 3. render_stock_report — pure jinja, no network
{
  const out = await callTool("render_stock_report", {
    report: {
      stock_name: "pi-e2e",
      stock_code: "600519",
      decision_type: "buy",
      sentiment_score: 60,
      confidence: "medium",
    },
    template: "brief",
  });
  assert(out.format === "markdown", "render: format");
  assert(String(out.content).includes("pi-e2e"), "render: content contains stock_name");
  console.log("  OK: render_stock_report");
}

// 4. diagnose_data_sources — probe only, no network
{
  const out = await callTool("diagnose_data_sources", { market: "A" });
  assert(out.meta?.provider === "diagnostics", "diagnose: meta.provider");
  assert(Array.isArray(out.markets), "diagnose: markets array");
  console.log("  OK: diagnose_data_sources");
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nAll Pi e2e round-trip checks passed");
