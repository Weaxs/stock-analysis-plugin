import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createJiti } from "jiti";

const here = dirname(fileURLToPath(import.meta.url));
const tools: { name: string }[] = [];
const skills: unknown[] = [];
const plugin = (await createJiti(fileURLToPath(import.meta.url)).import("../dsh/index.ts")) as {
  apply: (ctx: unknown) => void;
};
plugin.apply({
  tools: { register: (tool: { name: string }) => tools.push(tool) },
  skills: { register: (skill: unknown) => skills.push(skill) },
});

const canonical = JSON.parse(readFileSync(join(here, "../openclaw/openclaw.plugin.json"), "utf8")) as {
  contracts: { tools: string[] };
};
assert.deepEqual(tools.map((tool) => tool.name).sort(), canonical.contracts.tools.sort());
assert.equal(skills.length, readdirSync(join(here, "../skills"), { withFileTypes: true }).filter((d) => d.isDirectory()).length);
