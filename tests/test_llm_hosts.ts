import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { loadLLMHost } from "./e2e/llm-host.js";

const expected = JSON.parse(readFileSync(new URL("../openclaw/openclaw.plugin.json", import.meta.url), "utf8")) as {
  contracts: { tools: string[] };
};
for (const host of ["pi", "openclaw", "dsh"]) {
  const tools = await loadLLMHost(host);
  assert.deepEqual(tools.map(t => t.name).sort(), [...expected.contracts.tools].sort(), `${host}: complete registration`);
  for (const [name, args] of [
    ["get_market_capabilities", { market: "HK" }],
    ["parse_stock_list", { text: "NVDA AAPL" }],
  ] as const) {
    const tool = tools.find(t => t.name === name)!;
    assert.equal(tool.parameters.type, "object", `${host}: real JSON schema`);
    const result = JSON.parse((await tool.execute("offline-llm-adapter", args)).content[0].text);
    if (name === "get_market_capabilities") {
      assert.equal(result.market, "HK");
      assert(result.unsupported.some((item: { tool: string }) => item.tool === "get_capital_flow"));
    } else {
      assert.deepEqual(result.items.map((item: { symbol: string }) => item.symbol).sort(), ["AAPL", "NVDA"]);
    }
  }
  console.log(`${host}: LLM tool schemas and real Python dispatch passed`);
}
await assert.rejects(loadLLMHost("unknown"), /Expected pi, openclaw or dsh/);
