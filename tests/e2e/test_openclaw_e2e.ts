// Layer 1 e2e: OpenClaw host → real Python subprocess → JSON round-trip.
//
// openclaw/index.ts already exposes __setExecutor for tests. We do NOT call it
// here — the default executor spawns real python. Same 4 non-network tools.

import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { createJiti } from "jiti";

const here = dirname(fileURLToPath(import.meta.url));

interface ToolDef {
  name: string;
  execute: (id: string, params: Record<string, unknown>) => Promise<{
    content: { type: "text"; text: string }[];
  }>;
}

let capturedRegister: ((api: unknown) => void) | null = null;

const fakeSdk = {
  definePluginEntry(cfg: { register: (api: unknown) => void }) {
    capturedRegister = cfg.register;
    return cfg;
  },
};

// Real typebox shim (only needs the surface openclaw/index.ts touches)
const fakeTypebox = {
  Type: {
    Object: (props: unknown) => ({ __t: "object", props }),
    String: () => ({ __t: "string" }),
    Number: () => ({ __t: "number" }),
    Boolean: () => ({ __t: "boolean" }),
    Optional: (s: unknown) => ({ __t: "optional", s }),
    Union: (schemas: unknown[]) => ({ __t: "union", schemas }),
    Literal: (v: unknown) => ({ __t: "literal", v }),
    Array: (item: unknown) => ({ __t: "array", item }),
  },
};

(globalThis as any).__OPENCLAW_TEST__ = { fakeSdk, fakeTypebox };

const jiti = createJiti(fileURLToPath(import.meta.url), {
  alias: {
    "openclaw/plugin-sdk/plugin-entry": join(here, "..", "_stubs/openclaw-sdk.ts"),
    typebox: join(here, "..", "_stubs/typebox.ts"),
  },
});

const mod = (await jiti.import("../../openclaw/index.ts")) as {
  default: unknown;
  // Note: NOT calling __setExecutor — default spawns real python
};

if (!capturedRegister) {
  console.error("FAIL: definePluginEntry was not called");
  process.exit(1);
}

const tools: ToolDef[] = [];
capturedRegister({
  registerTool(t: ToolDef) {
    tools.push(t);
  },
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
  const result = await tool.execute("call_1", args);
  const text = result.content[0].text;
  return JSON.parse(text);
}

console.log("OpenClaw e2e host→python round-trip");

// Same 4 tools as Pi e2e
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
      stock_name: "oc-e2e",
      stock_code: "AAPL",
      decision_type: "hold",
      sentiment_score: 50,
      confidence: "low",
    },
    template: "brief",
  });
  assert(out.format === "markdown", "render: format");
  assert(String(out.content).includes("oc-e2e"), "render: content");
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
console.log("\nAll OpenClaw e2e round-trip checks passed");
