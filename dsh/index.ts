import { callTool, TOOL_SCHEMAS } from "../scripts/tool-calls.mjs";
import { cliJson, failureMessage, withoutNulls } from "../scripts/cli-json.mjs";
import { commandTimeout } from "../scripts/cli-timeout.mjs";
import { execFileTree } from "../scripts/exec-process.mjs";
import { AsyncLocalStorage } from "node:async_hooks";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Context } from "@deepseek-ai/cordis";
import { defineTool, type ParameterSchemaSpec } from "@deepseek-ai/dsh-tools";
// Type-only import: pulls in the `Context.skills` service augmentation.
import type {} from "@deepseek-ai/dsh-skill";
import { resolvePython, venvPythonPath } from "../scripts/venv-python.mjs";
import { createToolDiscovery } from "./discovery.js";
import { registerDashboard } from "./dashboard.js";

// Indirected so tests can replace the executor without touching node:child_process.
type Executor = (bin: string, argv: string[], timeoutMs?: number, signal?: AbortSignal) => Promise<string>;

export const __defaultExecutor: Executor = async (bin, argv, timeoutMs, signal) => {
  try {
    const stdout = await execFileTree(bin, argv, { timeoutMs: timeoutMs ?? commandTimeout(argv[0], argv.slice(1)), signal });
    return cliJson(stdout);
  } catch (error) {
    // stderr and command arguments are never surfaced.
    const failed = error as { stdout?: string; code?: unknown; killed?: boolean };
    return cliJson(failed.stdout ?? "", failureMessage(failed));
  }
};

let executor: Executor = __defaultExecutor;
export function __setExecutor(fn: Executor) {
  executor = fn;
}

const here = dirname(fileURLToPath(import.meta.url));
// Published: <pkg>/dist -> <pkg>. Dev bundle: dsh/dist -> repo root.
const pkgRoot = [join(here, ".."), join(here, "..", "..")].find(root => existsSync(join(root, "tools"))) ?? join(here, "..");
const toolsDir = join(pkgRoot, "tools");

function pythonBin(): string {
  // Staged-payload layout: the venv may still live at the repo root one level up.
  const own = venvPythonPath(pkgRoot);
  return existsSync(own) ? own : resolvePython(join(pkgRoot, ".."));
}

// The running tool call's exec.signal, so every execute body cancels its child without threading it by hand.
const callSignal = new AsyncLocalStorage<AbortSignal | undefined>();

async function runPy(script: string, args: string[], timeoutMs?: number, signal?: AbortSignal): Promise<string> {
  return executor(pythonBin(), [join(toolsDir, script), ...args], timeoutMs, signal ?? callSignal.getStore());
}

// One canonical stdout contract shared by every tool: execute returns the
// CLI's raw JSON text (same relay shape as the pi/openclaw adapters), render
// relays it to the model as a single text block.
const textOutput = {
  schema: { type: "string" as const },
  render: (_args: unknown, value: string) => [{ type: "text" as const, text: value }],
};

type JsonSchema = {
  type?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean;
  items?: JsonSchema;
  anyOf?: JsonSchema[];
  [key: string]: unknown;
};
type JsonSchemaTool = { name: string; description: string; parameters: JsonSchema };

// tools/tool_schemas.json -> dsh's DSL: per-property `required: true`, explicit object openness,
// oneOf for anyOf, and `json` for an untyped value.
function dslValue({ properties, required, items, anyOf, ...rest }: JsonSchema): Record<string, unknown> {
  return {
    ...(rest.type === undefined && !anyOf ? { type: "json" } : {}),
    ...rest,
    ...(rest.type === "object" ? { additionalProperties: rest.additionalProperties ?? true } : {}),
    ...(properties ? { properties: dslProperties(properties, required) } : {}),
    ...(items ? { items: dslValue(items) } : {}),
    ...(anyOf ? { oneOf: anyOf.map(dslValue) } : {}),
  };
}

function dslProperties(properties: Record<string, JsonSchema>, required: string[] = []) {
  return Object.fromEntries(
    Object.entries(properties).map(([key, value]) => [key, { ...dslValue(value), ...(required.includes(key) ? { required: true } : {}) }]),
  );
}

// --- Skills ---------------------------------------------------------------

// The same pkgRoot fallback covers published packages and the dev bundle.
const skillsDir = join(pkgRoot, "skills");

// Minimal single-line `key: value` read from the leading YAML frontmatter.
function frontmatterValue(raw: string, key: string): string | undefined {
  const block = raw.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  const line = block?.[1].match(new RegExp(`^${key}:\\s*(.+)$`, "m"));
  return line?.[1].trim();
}

// Hermes-style dynamic scan: every skills/<name>/SKILL.md becomes a registered
// skill, so adding a skill directory needs no adapter edit.
function registerSkills(ctx: Context) {
  if (!existsSync(skillsDir)) return;
  for (const child of readdirSync(skillsDir, { withFileTypes: true })) {
    if (!child.isDirectory()) continue;
    const skillFile = join(skillsDir, child.name, "SKILL.md");
    if (!existsSync(skillFile)) continue;
    const raw = readFileSync(skillFile, "utf-8");
    ctx.skills.register({
      name: child.name,
      description: frontmatterValue(raw, "description") ?? child.name,
      // The registry-facing body drops the frontmatter (metadata is carried by
      // the explicit name/description fields above).
      content: raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "").trimStart(),
      source: "bundled",
      path: skillFile,
      resourceBase: { kind: "directory", path: join(skillsDir, child.name) },
    });
  }
}

// --- Plugin entry (cordis function-plugin form) ----------------------------

export const name = "stock-analysis";
export const inject = ["tools", "skills"];

export function apply(ctx: Context, config: {toolDiscovery?: boolean} = {}) {
  if (config.toolDiscovery !== undefined && typeof config.toolDiscovery !== "boolean") {
    throw new TypeError("toolDiscovery must be a boolean");
  }
  const discovery = config.toolDiscovery === false ? undefined : createToolDiscovery(ctx);
  const register = (definition: Parameters<Context["tools"]["register"]>[0]) => {
    const tool = {
      ...definition,
      execute: (args: unknown, exec: Parameters<typeof definition.execute>[1]) =>
        callSignal.run(exec?.signal, () => definition.execute(withoutNulls(args), exec)),
    };
    return ctx.tools.register(tool, discovery?.(tool));
  };
  for (const schema of TOOL_SCHEMAS as JsonSchemaTool[]) {
    const { name, description, parameters: root } = schema;
    const tool = defineTool({
      name,
      description,
      parameters: dslProperties(root.properties ?? {}, root.required) as ParameterSchemaSpec,
      output: textOutput,
      execute: args => callTool(name, args, runPy),
    });
    // Match the other hosts' closed root object for structured JSON-input tools.
    if (root.additionalProperties === false) tool.parameters.additionalProperties = false;
    register(tool);
  }

  registerDashboard(ctx, runPy);
  registerSkills(ctx);
}
