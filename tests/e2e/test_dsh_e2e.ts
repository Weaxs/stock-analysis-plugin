// Layer 1 e2e: dsh host → real Python subprocess → JSON round-trip.
//
// dsh/index.ts already exposes __setExecutor for tests. We do NOT call it
// here — the default executor spawns real python. Same 4 non-network tools
// as the pi/openclaw e2e.

import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { createJiti } from "jiti";

const here = dirname(fileURLToPath(import.meta.url));

interface ToolDef {
  name: string;
  execute: (args: Record<string, unknown>, exec: unknown) => Promise<string>;
}

// defineTool identity stub — the options object is the definition.
const jiti = createJiti(fileURLToPath(import.meta.url), {
  alias: {
    "@deepseek-ai/dsh-tools": join(here, "..", "_stubs/dsh-tools.ts"),
  },
});

const mod = (await jiti.import("../../dsh/index.ts")) as {
  apply: (ctx: unknown) => void;
  // Note: NOT calling __setExecutor — default spawns real python
};

const tools: ToolDef[] = [];
mod.apply({
  tools: {
    register(t: ToolDef) {
      tools.push(t);
    },
  },
  skills: { register() {} },
});

// --- Assertions -----------------------------------------------------------

let failures = 0;
function assert(cond: boolean, msg: string) {
  if (!cond) {
    console.error(`  FAIL: ${msg}`);
    failures++;
  }
}

async function callTool(name: string, args: Record<string, unknown>): Promise<any> {
  const tool = tools.find((t) => t.name === name);
  if (!tool) throw new Error(`tool not registered: ${name}`);
  // dsh contract: execute returns the canonical value (the CLI's JSON text).
  const text = await tool.execute(args, {});
  return JSON.parse(text);
}

console.log("dsh e2e host→python round-trip");

// Same 4 tools as Pi/OpenClaw e2e
{
  const out = await callTool("get_market_capabilities", { market: "US" });
  assert(out.market === "US", "capabilities: market");
  assert(out.meta?.provider === "capabilities", "capabilities: meta");
  console.log("  OK: get_market_capabilities");
}

{
  const out = await callTool("parse_stock_list", { text: "AAPL 600519" });
  const symbols = new Set(out.items.map((i: any) => i.symbol));
  assert(symbols.has("AAPL"), "parse: AAPL");
  assert(symbols.has("600519"), "parse: 600519");
  console.log("  OK: parse_stock_list");
}

{
  const out = await callTool("render_stock_report", {
    report: {
      stock_name: "dsh-e2e",
      stock_code: "AAPL",
      decision_type: "hold",
      sentiment_score: 50,
      confidence: "low",
    },
    template: "brief",
  });
  assert(out.format === "markdown", "render: format");
  assert(String(out.content).includes("dsh-e2e"), "render: content");
  console.log("  OK: render_stock_report");
}

{
  const out = await callTool("diagnose_data_sources", { market: "A" });
  assert(out.meta?.provider === "diagnostics", "diagnose: meta");
  console.log("  OK: diagnose_data_sources");
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("\nAll dsh e2e round-trip checks passed");
