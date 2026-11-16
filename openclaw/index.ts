import { cliJson, failureMessage, withoutNulls } from "../scripts/cli-json.mjs";
import { commandTimeout } from "../scripts/cli-timeout.mjs";
import { execFileTree } from "../scripts/exec-process.mjs";
import { callTool, TOOL_SCHEMAS } from "../scripts/tool-calls.mjs";
import { pythonEnvironmentRoot, resolvePython, venvPythonPath } from "../scripts/venv-python.mjs";
import { AsyncLocalStorage } from "node:async_hooks";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "typebox";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

// Indirected so tests can replace the executor without touching node:child_process.
type Executor = (bin: string, argv: string[], signal?: AbortSignal) => Promise<string>;

export const __defaultExecutor: Executor = async (bin, argv, signal) => {
  try {
    const stdout = await execFileTree(bin, argv, { timeoutMs: commandTimeout(argv[0], argv.slice(1)), signal });
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
// Published form: tools/ ships next to index.ts. Dev form: tools/ is at repo root.
const toolsDir = existsSync(join(here, "tools"))
  ? join(here, "tools")
  : join(here, "..", "tools");
const fallbackRoot = dirname(toolsDir);

type JsonSchema = {
  type?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  anyOf?: JsonSchema[];
  enum?: (string | number | boolean)[];
  [key: string]: unknown;
};
type JsonSchemaTool = { name: string; description: string; parameters: JsonSchema };

// tools/tool_schemas.json -> TypeBox, the schema form OpenClaw's registerTool takes.
function typeboxSchema({ type, properties, required = [], items, anyOf, enum: values, ...options }: JsonSchema): unknown {
  if (anyOf) return Type.Union(anyOf.map(typeboxSchema), options);
  if (values) return Type.Union(values.map(value => Type.Literal(value)), options);
  switch (type) {
    case "object":
      return Type.Object(
        Object.fromEntries(Object.entries(properties ?? {}).map(([key, value]) =>
          [key, required.includes(key) ? typeboxSchema(value) : Type.Optional(typeboxSchema(value))])),
        options,
      );
    case "array":
      return Type.Array(items ? typeboxSchema(items) : Type.Unknown(), options);
    case "number":
      return Type.Number(options);
    case "boolean":
      return Type.Boolean(options);
    case "null":
      return Type.Null(options);
    case "string":
      return Type.String(options);
    case undefined:
      return Type.Unknown(options);
    default:
      throw new TypeError(`Unsupported tool schema type: ${type}`);
  }
}

function asText(text: string) {
  return { content: [{ type: "text" as const, text }] };
}

function asReport(text: string) {
  const result = asText(text);
  try {
    const report = JSON.parse(text);
    if (report?.final === true && !Object.hasOwn(report, "error") && report.format === "markdown"
        && typeof report.content === "string" && report.content.trim()) {
      return { ...result, details: { sourceReply: { text: report.content } } };
    }
  } catch {
    // Invalid output stays visible to the model and must never end the turn.
  }
  return result;
}

export default definePluginEntry({
  id: "stock-analysis",
  name: "Stock Analysis",
  description:
    "Stock analysis, screening, and strategy backtesting across A/HK/US/JP/KR/TW markets",
  register(api) {
    // OpenClaw executes a captured copy of the JS bundle; the venv stays in the installed plugin root.
    const repoRoot = api.rootDir || fallbackRoot;
    const runtimeToolsDir = existsSync(join(repoRoot, "tools")) ? join(repoRoot, "tools") : toolsDir;
    const pythonBin = () => {
      const venvPython = venvPythonPath(pythonEnvironmentRoot(repoRoot));
      // In a staged dev checkout, the venv still lives at the repo root.
      return existsSync(venvPython) ? venvPython : resolvePython(join(repoRoot, ".."));
    };
    // OpenClaw calls execute(toolCallId, params, signal?, onUpdate?); runPy forwards that call's signal.
    const callSignal = new AsyncLocalStorage<AbortSignal | undefined>();
    const runPy = (script: string, args: string[]) =>
      executor(pythonBin(), [join(runtimeToolsDir, script), ...args], callSignal.getStore());
    const registerTool: typeof api.registerTool = (tool) => api.registerTool({
      ...tool,
      execute: (id: string, params: unknown, signal?: unknown) =>
        callSignal.run(signal instanceof AbortSignal ? signal : undefined, () => tool.execute(id, withoutNulls(params) as never)),
    });
    for (const { name, description, parameters } of TOOL_SCHEMAS as JsonSchemaTool[]) {
      // final=true reports may end the turn with the rendered Markdown as the reply.
      const report = name.startsWith("render_");
      registerTool({
        name,
        description,
        ...(report ? { canDeliverSourceReply: true, catalogMode: "direct-only" as const } : {}),
        parameters: typeboxSchema(parameters),
        async execute(_id, params) {
          return (report ? asReport : asText)(await callTool(name, params, runPy));
        },
      });
    }
  },
});
