// Run after build:dsh against the real host: node tests/e2e/test_dsh_research.mjs <host package/root>
// Harness follows upstream agent-preset-registry and persistent runtime acceptance tests.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const repo = fileURLToPath(new URL("../../", import.meta.url));
const require = createRequire(resolve(process.argv[2], "probe.cjs"));
const pkg = async n => import(pathToFileURL(require.resolve(n)).href);
const { parse } = require("yaml");
const overlay = parse(readFileSync(new URL("../../dsh/research.patch.yml", import.meta.url), "utf8"));
const definitions = overlay.find(row => row.insert).insert;
const manifest = JSON.parse(readFileSync(new URL("../../openclaw/openclaw.plugin.json", import.meta.url), "utf8"));
const { Context } = await pkg('@deepseek-ai/cordis');
const { default: Loader } = await pkg('@deepseek-ai/cordis-plugin-loader');
const { default: Group } = await pkg('@deepseek-ai/cordis-plugin-group');
const { defineTool } = await pkg('@deepseek-ai/dsh-tools');
const { SessionId } = await pkg('@deepseek-ai/dsh-session');
const ctx = new Context();
try {
  await ctx.plugin(Loader);
  ctx.loader.builtins.group = Group;
  for (const [name, config] of [
    ['llm', undefined], ['session', undefined], ['session-projection', undefined],
    ['system-prompt', { personaPrefix: '' }], ['tools', undefined],
    ['agent', undefined], ['agent-loop', { agents: [] }],
    ['fs-local', undefined], ['working-directory', undefined], ['skill', undefined], ['web', undefined],
    ['subagent', undefined], ['subagent-spawn-in-process', undefined],
    ['agent-preset-registry', { default: 'stock-research' }],
  ]) {
    const module = await pkg('@deepseek-ai/dsh-' + name);
    await ctx.plugin(module.default ?? module, config);
  }

  const names = [...manifest.contracts.tools, "host_write"];
  for (const name of names) ctx.tools.register(defineTool({
    name, description: name, parameters: {},
    output: { schema: { type: "string" }, render: (_a, value) => [{ type: "text", text: value }] },
    execute: async () => '{}',
  }));
  ctx.loader.builtins.role = await import(pathToFileURL(resolve(repo, "dsh/dist/research-role.js")).href);
  assert.throws(() => ctx.loader.builtins.role.apply(ctx, {role: "unknown"}), /Unknown stock research role/);
  assert.throws(() => ctx.loader.builtins.role.apply(ctx, {role: "fundamentals"}), /scoped context/);
  for (const name of ["persona", "tool-web", "tool-skill", "tool-subagent"])
    ctx.loader.builtins[name] = await pkg("@deepseek-ai/dsh-" + name);
  // dsh-base already registers these globally; preset copies share the same names.
  await ctx.plugin(ctx.loader.builtins["tool-web"]);
  await ctx.plugin(ctx.loader.builtins["tool-skill"]);
  for (const row of definitions.filter(row => row.config?.id?.startsWith("stock-"))) {
    const plugins = row.config.plugins.map(p => ({...p, name: p.id === "stock-role"
      ? "cordis:role" : "cordis:" + p.name.split("/dsh-")[1]}));
    await ctx.plugin({ inject: ['agentPresets'], async* apply(child) {
      yield await child.agentPresets.register({...row.config, plugins});
    } });
  }
  const delegation = definitions.find(row => row.id === "stock-research-subagent");
  if (delegation) await ctx.plugin(ctx.loader.builtins["tool-subagent"], delegation.config);
  const headlessParent = await ctx.agents.create({sessionId: SessionId("headless-parent")});
  assert(ctx.tools.get("stock_research_subagent", headlessParent.agent), "headless parent can delegate without mounting a preset");
  const parentNames = ctx.tools.schemas().map(tool => tool.name).sort();
  const expected = {
    "stock-research": {present: "stock_research_subagent", absent: "undefined_tool"},
    "stock-fundamentals": {present: "get_financials", absent: "run_backtest"},
    "stock-technical": {present: "run_backtest", absent: "get_financials"},
    "stock-events": {present: "get_announcements", absent: "run_backtest"},
  };
  for (const [id, test] of Object.entries(expected)) {
    const handle = await ctx.agents.create({ sessionId: SessionId('proof-' + id),
      setup: async agentCtx => { await ctx.agentPresets.mount(agentCtx, id); },
    });
    const visible = ctx.tools.schemas(handle.agent).map(t => t.name);
    assert(visible.includes(test.present), `${id} required tool`);
    assert(!visible.includes(test.absent), `${id} excludes other role tool`);
    if (id !== "stock-research") {
      assert(!visible.includes("render_stock_report") && !visible.includes("host_write") && !visible.includes("stock_research_subagent"), `${id} cannot deliver or write`);
      assert(visible.includes("web_search") && visible.includes("web_fetch") && visible.includes("skill"), `${id} retains scoped evidence and methodology tools: ${visible.join(",")}`);
    }
  }
  assert.deepEqual(ctx.tools.schemas().map(t => t.name).sort(), parentNames);
  console.log('PASS real Cordis/tools/preset scoped restriction, parent unchanged');
} finally { await ctx.fiber.dispose(); }

// Published runtime proof adapted from ptc-runtime-node/tests/persistent-built.e2e.ts.
const rt = new Context();
try {
  for (const name of ['session-projection', 'fs-local', 'subprocess-local', 'sandbox-local', 'sandbox-policy'])
    await rt.plugin((await pkg('@deepseek-ai/dsh-' + name)).default);
  await rt.plugin((await pkg('@deepseek-ai/dsh-ptc-runtime-node')).default,
    overlay.find(row => row.id === 'ptc-runtime').config);
  const stateOwner = {};
  const run = program => rt.ptcRuntime.run(rt.ptcRuntime.resolve({ program, bindings: [], stateOwner }));
  const first = await run('let count: number = 40; const path = await import("node:path");');
  assert.equal(first.error, undefined, JSON.stringify(first));
  const second = await run('count += 2; console.log(path.basename("/a/b")); count');
  assert.equal(second.error, undefined, JSON.stringify(second));
  assert.equal(second.value, 42);
  assert.deepEqual(second.logs, ['b']);
  await rt.ptcRuntime.release(stateOwner);
  const fresh = await run('typeof count');
  assert.equal(fresh.value, 'undefined');
  console.log('PASS actual persistent PTC variables/import reuse and explicit release');
} finally { await rt.fiber.dispose(); }
