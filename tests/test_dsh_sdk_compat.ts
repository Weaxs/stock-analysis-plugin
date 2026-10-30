import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createJiti } from "jiti";
import { Check } from "typebox/value";
import { validateJsonSchemaValue, type JsonSchemaNode } from "@deepseek-ai/dsh-tools";
import { resolvePython } from "../scripts/venv-python.mjs";
import { rotationRequests } from "./fixtures/rotation";

const here = dirname(fileURLToPath(import.meta.url));
const tools: { name: string; parameters: JsonSchemaNode }[] = [];
const skills: unknown[] = [];
const plugin = (await createJiti(fileURLToPath(import.meta.url)).import("../dsh/index.ts")) as {
  apply: (ctx: unknown) => void;
};
plugin.apply({
  tools: { register: (tool: { name: string; parameters: JsonSchemaNode }) => tools.push(tool) },
  skills: { register: (skill: unknown) => skills.push(skill) },
});

// Compare the actual public schemas exposed by all four hosts, not their source DSLs.
type RegisteredTool = { name: string; parameters: Record<string, unknown> };
const piTools: RegisteredTool[] = [];
const pi = (await createJiti(fileURLToPath(import.meta.url)).import("../pi/index.ts")) as {
  default: (host: unknown) => void;
};
pi.default({
  registerTool: (tool: RegisteredTool) => piTools.push(tool),
  on() {},
  exec: async () => ({ stdout: "{}" }),
});

let capturedOpenClaw: { register: (api: unknown) => void } | null = null;
(globalThis as any).__OPENCLAW_TEST__ = {
  fakeSdk: {
    definePluginEntry(config: typeof capturedOpenClaw) {
      capturedOpenClaw = config;
      return config;
    },
  },
};
const openClawJiti = createJiti(fileURLToPath(import.meta.url), {
  alias: { "openclaw/plugin-sdk/plugin-entry": join(here, "_stubs/openclaw-sdk.ts") },
});
await openClawJiti.import("../openclaw/index.ts");
const openClawTools: RegisteredTool[] = [];
const openClawEntry = capturedOpenClaw as { register: (api: unknown) => void } | null;
assert(openClawEntry);
openClawEntry.register({
  rootDir: join(here, ".."),
  registerTool: (tool: RegisteredTool) => openClawTools.push(tool),
});

const hermes = spawnSync(
  resolvePython(join(here, "..")),
  ["-c", "import json; from hermes.schemas import TOOL_SCHEMAS; print(json.dumps(TOOL_SCHEMAS))"],
  { cwd: join(here, ".."), encoding: "utf8" },
);
assert.equal(hermes.status, 0, hermes.stderr);
const hermesTools = JSON.parse(hermes.stdout) as RegisteredTool[];
const hostTools: Record<string, Record<string, RegisteredTool>> = Object.fromEntries(
  Object.entries({ pi: piTools, openclaw: openClawTools, dsh: tools as unknown as RegisteredTool[], hermes: hermesTools }).map(
    ([host, registered]) => [host, Object.fromEntries(registered.map(tool => [tool.name, tool]))],
  ),
);

const canonical = JSON.parse(readFileSync(join(here, "../openclaw/openclaw.plugin.json"), "utf8")) as {
  contracts: { tools: string[] };
};
assert.deepEqual(tools.map((tool) => tool.name).sort(), canonical.contracts.tools.sort());
assert.equal(skills.length, readdirSync(join(here, "../skills"), { withFileTypes: true }).filter((d) => d.isDirectory()).length);

const fixtures = JSON.parse(readFileSync(join(here, "fixtures/stateless_analysis.json"), "utf8")) as Record<string, {args: Record<string, unknown>}>;
for (const [name, fixture] of Object.entries(fixtures)) {
  assert.deepEqual(validateJsonSchemaValue(tools.find(t => t.name === name)!.parameters, fixture.args), [], name);
}

const closedInputs: Record<string, Record<string, unknown>> = {
  ...Object.fromEntries(Object.entries(fixtures).map(([name, fixture]) => [name, fixture.args])),
  ...rotationRequests,
  analyze_trend_strategy: { symbols: ["AAPL", "MSFT"] },
};
for (const [name, args] of Object.entries(closedInputs)) {
  for (const [host, registered] of Object.entries(hostTools)) {
    const schema = registered[name].parameters;
    if (host === "dsh") {
      assert.deepEqual(validateJsonSchemaValue(schema as JsonSchemaNode, args), [], `${host} ${name} valid input`);
      assert(
        validateJsonSchemaValue(schema as JsonSchemaNode, { ...args, unexpected: true }).length > 0,
        `${host} ${name} must reject an unknown root property`,
      );
    } else {
      assert.equal(Check(schema, args), true, `${host} ${name} valid input`);
      assert.equal(Check(schema, { ...args, unexpected: true }), false, `${host} ${name} unknown root property`);
    }
  }
}

for (const host of ["openclaw", "dsh", "hermes"]) {
  assert.deepEqual(
    hostTools[host].analyze_trend_strategy.parameters,
    hostTools.pi.analyze_trend_strategy.parameters,
    `${host} analyze_trend_strategy schema must match Pi`,
  );
}

const comparison = tools.find(t => t.name === "compare_stocks")!;
assert.deepEqual(validateJsonSchemaValue(comparison.parameters, {
  companies: [{symbol: "AAPL", period: "2025", metrics: [{name: "roe", value: null, unit: "percent", basis: "annual"}]}],
}), [], "nullable nested metrics must compile in the actual dsh SDK");
assert(validateJsonSchemaValue(comparison.parameters, {companies: [{symbol: "AAPL"}]}).length > 0);

await import("./test_dsh_discovery.js");
