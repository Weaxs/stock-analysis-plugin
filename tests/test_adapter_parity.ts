// Cross-adapter parity: all four hosts must expose the same tools with the same
// description, top-level property names, required set, enum/type and property
// descriptions. Pi is the reference; openclaw uses real typebox (JSON Schema),
// dsh uses its per-property `required: true` DSL, hermes is read from schemas.py.
// The same sample calls then run through every host with the Python executor
// stubbed: each host must hand Python the same script, argv and JSON input.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { basename, dirname, join } from "node:path";
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
// name -> (args) => run the registered (null-stripping) execute once.
const runners: Record<string, Record<string, (args: unknown) => Promise<unknown>>> = { pi: {}, openclaw: {}, dsh: {} };
let sink: unknown[][] = [];
// Transport files are read while they still exist so JSON-input tools compare by content, not temp path.
const argValue = (arg: unknown) => {
  const text = String(arg);
  return text.endsWith(".json") && existsSync(text) ? { $input: JSON.parse(readFileSync(text, "utf8")) } : text;
};
const capture = (argv: unknown[]) => {
  sink.push([basename(String(argv[0])), ...argv.slice(1).map(argValue)]);
  return "{}";
};

const piSchemas: Record<string, JsonSchema> = {};
{
  const jiti = createJiti(fileURLToPath(import.meta.url));
  const mod = (await jiti.import("../pi/index.ts")) as { default?: (api: unknown) => void; __setProcessExecutor: (fn: unknown) => void };
  mod.__setProcessExecutor(async (_bin: string, argv: string[]) => ({ stdout: capture(argv), stderr: "", code: 0, killed: false }));
  const register = (mod.default ?? mod) as (api: unknown) => void;
  register({
    registerTool: (t: { name: string; description: string; parameters: never; execute: Function }) => {
      hosts.pi[t.name] = fromJsonSchema(t.description, t.parameters);
      piSchemas[t.name] = t.parameters;
      runners.pi[t.name] = args => t.execute("parity", args);
    },
    on() {},
  });
}

{
  (globalThis as any).__OPENCLAW_TEST__ = { fakeSdk: { definePluginEntry: (c: unknown) => c } };
  const jiti = createJiti(fileURLToPath(import.meta.url), {
    alias: { "openclaw/plugin-sdk/plugin-entry": join(here, "_stubs/openclaw-sdk.ts") },
  });
  const mod = (await jiti.import("../openclaw/index.ts")) as {
    default: { register: (api: unknown) => void };
    __setExecutor: (fn: (bin: string, argv: string[]) => Promise<string>) => void;
  };
  mod.__setExecutor(async (_bin, argv) => capture(argv));
  mod.default.register({
    rootDir: repoRoot,
    registerTool: (t: { name: string; description: string; parameters: never; execute: Function }) => {
      hosts.openclaw[t.name] = fromJsonSchema(t.description, t.parameters);
      runners.openclaw[t.name] = args => t.execute("parity", args);
    },
  });
}

{
  const jiti = createJiti(fileURLToPath(import.meta.url), {
    alias: { "@deepseek-ai/dsh-tools": join(here, "_stubs/dsh-tools.ts") },
  });
  const mod = (await jiti.import("../dsh/index.ts")) as {
    apply: (ctx: unknown) => void;
    __setExecutor: (fn: (bin: string, argv: string[]) => Promise<string>) => void;
  };
  mod.__setExecutor(async (_bin, argv) => capture(argv));
  mod.apply({
    tools: {
      register: (t: { name: string; description: string; parameters: Record<string, Prop | boolean>; execute: Function }) => {
        runners.dsh[t.name] = args => t.execute(args, { signal: new AbortController().signal });
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

// Three calls per tool from pi's schema: required-only, every property, and every property with
// alternate values (second enum member, 0, false) so default/falsy handling drift shows up.
type JsonSchema = { type?: string; enum?: unknown[]; anyOf?: (JsonSchema & { const?: unknown })[]; items?: JsonSchema; properties?: Record<string, JsonSchema>; required?: string[] };
function sample(schema: JsonSchema, alt: number): unknown {
  if (schema.enum) return schema.enum[alt % schema.enum.length];
  if (schema.anyOf) {
    const consts = schema.anyOf.filter(o => "const" in o);
    if (consts.length) return consts[alt % consts.length].const;
    return sample(schema.anyOf.find(o => o.type !== "null") ?? {}, alt);
  }
  switch (schema.type) {
    case "number":
    case "integer": return alt ? 0 : 7;
    case "boolean": return !alt;
    case "array": return [sample(schema.items ?? {}, alt)];
    case "object": return Object.fromEntries(Object.entries(schema.properties ?? {}).map(([k, p]) => [k, sample(p, alt)]));
    default: return alt ? "B2" : "A1";
  }
}
const cases: Record<string, unknown[]> = {};
for (const [name, schema] of Object.entries(piSchemas)) {
  const all = sample(schema, 0) as Record<string, unknown>;
  const required = Object.fromEntries((schema.required ?? []).map(k => [k, all[k]]));
  cases[name] = [required, all, sample(schema, 1)];
}

// argparse semantics: option order is irrelevant and --*-b64 payloads compare by decoded JSON.
function semantic(call: unknown[]): unknown[] {
  const [script, ...rest] = call;
  const first = rest.findIndex(a => typeof a === "string" && a.startsWith("--"));
  const positionals = first < 0 ? rest : rest.slice(0, first);
  const options: unknown[][] = [];
  for (let i = first < 0 ? rest.length : first; i < rest.length; i++) {
    const flag = String(rest[i]);
    const next = rest[i + 1];
    if (next === undefined || (typeof next === "string" && next.startsWith("--"))) {
      options.push([flag]);
      continue;
    }
    i++;
    if (!flag.endsWith("-b64") || typeof next !== "string") {
      options.push([flag, next]);
      continue;
    }
    const decoded = Buffer.from(next, "base64").toString("utf8");
    try {
      options.push([flag, { $b64: JSON.parse(decoded) }]);
    } catch {
      options.push([flag, { $b64: decoded }]);
    }
  }
  return [script, ...positionals, ...options.sort((a, b) => (stable(a) < stable(b) ? -1 : 1))];
}

const stable = (value: unknown): string =>
  Array.isArray(value) ? `[${value.map(stable).join(",")}]`
    : value && typeof value === "object"
      ? `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${stable((value as Record<string, unknown>)[k])}`).join(",")}}`
      : JSON.stringify(value);

const argvs: Record<string, Record<string, string[]>> = { pi: {}, openclaw: {}, dsh: {}, hermes: {} };
for (const host of ["pi", "openclaw", "dsh"]) {
  for (const [name, list] of Object.entries(cases)) {
    argvs[host][name] = [];
    for (const args of list) {
      sink = [];
      try {
        await runners[host][name]?.(structuredClone(args));
      } catch (error) {
        sink.push([`threw: ${(error as Error).message}`]);
      }
      argvs[host][name].push(stable(sink.map(semantic)));
    }
  }
}
{
  // Hermes handlers run in-process with _run stubbed, through the same null-stripping wrapper register() uses.
  const code = [
    "import json, os, sys",
    "sys.path.insert(0, sys.argv[1])",
    "import hermes",
    "from hermes import tools",
    "sink = []",
    "def arg(a):",
    "    a = str(a)",
    "    return {'$input': json.load(open(a, encoding='utf-8'))} if a.endswith('.json') and os.path.isfile(a) else a",
    "tools._run = lambda script, argv: sink.append([script, *map(arg, argv)]) or '{}'",
    "out = {}",
    "for name, calls in json.load(sys.stdin).items():",
    "    out[name] = []",
    "    for args in calls:",
    "        sink.clear()",
    "        try:",
    "            hermes._without_nulls(getattr(tools, name))(args)",
    "        except Exception as error:",
    "            sink.append([f'threw: {error}'])",
    "        out[name].append(list(sink))",
    "print(json.dumps(out))",
  ].join("\n");
  const out = execFileSync(resolvePython(repoRoot), ["-c", code, repoRoot], { encoding: "utf8", input: JSON.stringify(cases) });
  for (const [name, list] of Object.entries(JSON.parse(out) as Record<string, unknown[]>)) argvs.hermes[name] = (list as unknown[][][]).map(calls => stable(calls.map(semantic)));
}

const issues: string[] = [];
for (const host of ["openclaw", "dsh", "hermes"]) {
  for (const [name, ref] of Object.entries(argvs.pi)) {
    ref.forEach((expected, i) => {
      const got = argvs[host][name]?.[i];
      if (got !== expected) issues.push(`${host} ${name} call#${i}: pi=${expected} ${host}=${got}`);
    });
  }
}
const names = Object.keys(hosts.pi).sort();
if (names.length !== 73) issues.push(`pi registered ${names.length} tools, expected 73`);
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
console.log(`OK: ${names.length} tools identical across pi/openclaw/dsh/hermes (schemas + ${names.length * 3} argv/JSON-input calls)`);
