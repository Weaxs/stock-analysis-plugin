import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createJiti } from "jiti";
import { validateJsonSchemaValue, type JsonSchemaNode } from "@deepseek-ai/dsh-tools";

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

const canonical = JSON.parse(readFileSync(join(here, "../openclaw/openclaw.plugin.json"), "utf8")) as {
  contracts: { tools: string[] };
};
assert.deepEqual(tools.map((tool) => tool.name).sort(), canonical.contracts.tools.sort());
assert.equal(skills.length, readdirSync(join(here, "../skills"), { withFileTypes: true }).filter((d) => d.isDirectory()).length);

const fixtures = JSON.parse(readFileSync(join(here, "fixtures/stateless_analysis.json"), "utf8")) as Record<string, {args: unknown}>;
for (const [name, fixture] of Object.entries(fixtures)) {
  assert.deepEqual(validateJsonSchemaValue(tools.find(t => t.name === name)!.parameters, fixture.args), [], name);
}
const comparison = tools.find(t => t.name === "compare_stocks")!;
assert.deepEqual(validateJsonSchemaValue(comparison.parameters, {
  companies: [{symbol: "AAPL", period: "2025", metrics: [{name: "roe", value: null, unit: "percent", basis: "annual"}]}],
}), [], "nullable nested metrics must compile in the actual dsh SDK");
assert(validateJsonSchemaValue(comparison.parameters, {companies: [{symbol: "AAPL"}]}).length > 0);
