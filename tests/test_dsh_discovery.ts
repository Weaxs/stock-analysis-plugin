import assert from "node:assert/strict";
import { Context } from "@deepseek-ai/cordis";
import type { Agent } from "@deepseek-ai/dsh-agent";
import SkillRegistry from "@deepseek-ai/dsh-skill";
import { createScope } from "@deepseek-ai/dsh-scope";
import SystemPrompt from "@deepseek-ai/dsh-system-prompt";
import ToolRuntime, { defineTool } from "@deepseek-ai/dsh-tools";
import { ToolCallId } from "@deepseek-ai/dsh-llm";
import * as stockPlugin from "../dsh/index.js";

const ctx = new Context();
await ctx.plugin(SystemPrompt);
await ctx.plugin(ToolRuntime);
await ctx.plugin(SkillRegistry);
const plugin = await ctx.plugin(stockPlugin);
try {
  ctx.tools.register(defineTool({
    name: "other_plugin", description: "Unrelated tool", parameters: {},
    output: {schema: {type: "string"}, render: (_args, value) => [{type: "text", text: value}]},
    async execute() { return "other"; },
  }));
  const first = {};
  const second = {};
  let scopeOwner = ctx;
  await ctx.plugin({name: "discovery-test-scopes", inject: ["tools"], apply(inner: Context) { scopeOwner = inner; }});
  const firstScope = createScope(scopeOwner, first);
  const secondScope = createScope(scopeOwner, second);
  const names = async (scope: object) => (await ctx.systemPrompt.assemble({scope})).tools.map(tool => tool.name);
  const search = async (scope: object, query: string, limit = 5) => {
    const result = await ctx.tools.execute({
      callId: ToolCallId("discovery-test"), name: "stock_tool_search",
      arguments: {query, limit}, agent: scope as Agent, signal: new AbortController().signal,
    });
    assert.equal(result.isError, false, JSON.stringify(result));
    return JSON.parse(String(result.value));
  };

  assert.deepEqual(await names(first), ["other_plugin", "stock_tool_search"]);
  const disabledAtStartup = secondScope.ctx.tools.restrict({deny: ["stock_tool_search"]});
  assert((await names(second)).includes("get_quote"), "unavailable search at startup keeps stock tools exposed");
  disabledAtStartup();
  assert.deepEqual(await names(second), ["other_plugin", "stock_tool_search"]);
  const found = await search(first, "get_quote", 1);
  assert.equal(found.loaded[0].name, "get_quote");
  assert.equal((await search(first, "stock quote", 1)).loaded[0].name, "get_quote", "a general stock keyword must not bury the requested quote capability");
  assert((await search(first, "股票回测")).loaded.some((tool: {name: string}) => tool.name === "run_backtest"), "Chinese capability terms must outrank generic stock characters");
  assert((await names(first)).includes("get_quote"));
  assert(!(await names(second)).includes("get_quote"), "loading must remain agent-local");

  firstScope.ctx.tools.restrict({deny: ["get_financials"]});
  const financials = await search(first, "财务");
  assert(!financials.loaded.some((tool: {name: string}) => tool.name === "get_financials"), "search cannot bypass permission restrictions");
  const chinese = await search(second, "财务");
  assert(chinese.loaded.some((tool: {name: string}) => tool.name === "get_financials"), "Chinese descriptions must be searchable");
  await search(second, "get_market_capabilities", 1);
  assert((await names(second)).includes("get_market_capabilities"));
  const capabilities = await ctx.tools.execute({
    callId: ToolCallId("discovered-python"), name: "get_market_capabilities",
    arguments: {market: "HK"}, agent: second as Agent, signal: new AbortController().signal,
  });
  assert.equal(capabilities.isError, false);
  assert.equal(JSON.parse(String(capabilities.value)).market, "HK", "a discovered tool must execute the real Python CLI");
  assert.deepEqual((await search(first, "nonexistent_zzzzz")).loaded, []);
  assert.equal(typeof (await search(first, "get_quote", 0)).error, "string");

  const blockSearch = firstScope.ctx.tools.restrict({deny: ["stock_tool_search"]});
  const fallback = await names(first);
  assert(fallback.includes("run_backtest"), "disabled search must restore the stock catalog");
  assert(!fallback.includes("get_financials"), "fallback must preserve permission restrictions");
  blockSearch();
  assert(!(await names(first)).includes("get_quote"), "re-enabled discovery starts with an empty loaded set");
  const disposePtc = ctx.on("system-prompt/assemble", async (_assembly, _context, next) => {
    const assembly = await next();
    return {...assembly, tools: [...assembly.tools, {name: "run_code", description: "PTC transport", parameters: {type: "object"}}]};
  });
  assert((await names(first)).includes("run_backtest"), "PTC presentation keeps the host-owned catalog");
  disposePtc();
  await firstScope.dispose();
  await secondScope.dispose();
} finally {
  await plugin.dispose();
  await ctx.fiber.dispose();
}

const eager = new Context();
await eager.plugin(SystemPrompt);
await eager.plugin(ToolRuntime);
await eager.plugin(SkillRegistry);
await eager.plugin(stockPlugin, {toolDiscovery: false});
try {
  assert.equal(eager.tools.schemas().length, 67);
  assert.equal(eager.tools.get("stock_tool_search"), undefined);
  assert.equal((await eager.systemPrompt.assemble({scope: {}})).tools.length, 67);
} finally { await eager.fiber.dispose(); }
console.log("dsh discovery: scoped loading, Chinese search, permissions, fallback, and real Python round-trip passed");
