// Cross-adapter parity: all four hosts must expose the same tools with the same
// description, top-level property names, required set, enum/type and property
// descriptions. Pi is the reference; openclaw uses real typebox (JSON Schema),
// dsh uses its per-property `required: true` DSL, hermes is read from schemas.py.
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createJiti } from "jiti";
import { resolvePython } from "../scripts/venv-python.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..");

type Shape = { description: string; props: Record<string, string>; required: string[] };
type Prop = { type?: string; enum?: unknown[]; anyOf?: { const?: unknown }[]; description?: string; required?: boolean };

const propShape = (p: Prop) =>
  JSON.stringify({
    kind: (p.enum ?? p.anyOf?.map(x => x.const) ?? [p.type]).map(String).sort(),
    description: p.description ?? "",
  });

function fromJsonSchema(description: string, schema: { properties?: Record<string, Prop>; required?: string[] }): Shape {
  const props = Object.fromEntries(Object.entries(schema?.properties ?? {}).map(([k, v]) => [k, propShape(v)]));
  return { description, props, required: [...(schema?.required ?? [])].sort() };
}

const hosts: Record<string, Record<string, Shape>> = { pi: {}, openclaw: {}, dsh: {}, hermes: {} };

{
  const jiti = createJiti(fileURLToPath(import.meta.url));
  const mod = (await jiti.import("../pi/index.ts")) as { default?: (api: unknown) => void };
  const register = (mod.default ?? mod) as (api: unknown) => void;
  register({
    registerTool: (t: { name: string; description: string; parameters: never }) => {
      hosts.pi[t.name] = fromJsonSchema(t.description, t.parameters);
    },
    on() {},
    exec: async () => ({}),
  });
}

{
  (globalThis as any).__OPENCLAW_TEST__ = { fakeSdk: { definePluginEntry: (c: unknown) => c } };
  const jiti = createJiti(fileURLToPath(import.meta.url), {
    alias: { "openclaw/plugin-sdk/plugin-entry": join(here, "_stubs/openclaw-sdk.ts") },
  });
  const mod = (await jiti.import("../openclaw/index.ts")) as { default: { register: (api: unknown) => void } };
  mod.default.register({
    rootDir: repoRoot,
    registerTool: (t: { name: string; description: string; parameters: never }) => {
      hosts.openclaw[t.name] = fromJsonSchema(t.description, t.parameters);
    },
  });
}

{
  const jiti = createJiti(fileURLToPath(import.meta.url), {
    alias: { "@deepseek-ai/dsh-tools": join(here, "_stubs/dsh-tools.ts") },
  });
  const mod = (await jiti.import("../dsh/index.ts")) as { apply: (ctx: unknown) => void };
  mod.apply({
    tools: {
      register: (t: { name: string; description: string; parameters: Record<string, Prop | boolean> }) => {
        // dsh closes schemas with a top-level `additionalProperties: false` flag; it is not a parameter.
        const params = Object.entries(t.parameters).filter((e): e is [string, Prop] => e[0] !== "additionalProperties");
        hosts.dsh[t.name] = {
          description: t.description,
          props: Object.fromEntries(params.map(([k, v]) => [k, propShape(v)])),
          required: params.filter(([, v]) => v.required).map(([k]) => k).sort(),
        };
      },
    },
    skills: { register() {} },
  });
}

{
  const code = "import json,runpy,sys; print(json.dumps(runpy.run_path(sys.argv[1])['TOOL_SCHEMAS']))";
  const out = execFileSync(resolvePython(repoRoot), ["-c", code, join(repoRoot, "hermes/schemas.py")], { encoding: "utf8" });
  for (const t of JSON.parse(out) as { name: string; description: string; parameters: never }[]) {
    hosts.hermes[t.name] = fromJsonSchema(t.description, t.parameters);
  }
}

const issues: string[] = [];
const names = Object.keys(hosts.pi).sort();
if (names.length !== 72) issues.push(`pi registered ${names.length} tools, expected 72`);
for (const host of ["openclaw", "dsh", "hermes"]) {
  for (const extra of Object.keys(hosts[host]).filter(n => !hosts.pi[n])) issues.push(`${host}: extra tool ${extra}`);
  for (const name of names) {
    const ref = hosts.pi[name];
    const other = hosts[host][name];
    if (!other) {
      issues.push(`${host}: missing ${name}`);
      continue;
    }
    if (ref.description !== other.description) issues.push(`${host} ${name}: description differs`);
    if (ref.required.join() !== other.required.join()) issues.push(`${host} ${name}: required pi=[${ref.required}] ${host}=[${other.required}]`);
    const keys = [...new Set([...Object.keys(ref.props), ...Object.keys(other.props)])];
    for (const key of keys) {
      if (ref.props[key] !== other.props[key]) issues.push(`${host} ${name}.${key}: pi=${ref.props[key]} ${host}=${other.props[key]}`);
    }
  }
}

if (issues.length) {
  for (const issue of issues) console.error(`  FAIL: ${issue}`);
  console.error(`${issues.length} parity issue(s)`);
  process.exit(1);
}
console.log(`OK: ${names.length} tools identical across pi/openclaw/dsh/hermes`);
