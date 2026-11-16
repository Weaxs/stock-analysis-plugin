import type { ExtensionAPI } from "pi-agent";
import { AsyncLocalStorage } from "node:async_hooks";
import { existsSync, readdirSync } from "node:fs";
import { fileURLToPath } from "url";

import { cliJson, failureMessage, withoutNulls } from "../scripts/cli-json.mjs";
import { commandTimeout } from "../scripts/cli-timeout.mjs";
import { execFileTree } from "../scripts/exec-process.mjs";
import { callTool, TOOL_SCHEMAS } from "../scripts/tool-calls.mjs";
import { resolvePython } from "../scripts/venv-python.mjs";

type ProcessResult = { stdout: string; stderr?: string; code: number | string; killed: boolean; truncated?: boolean };
type ProcessExecutor = (python: string, args: string[], options: { timeout: number; signal?: AbortSignal }) => Promise<ProcessResult>;

export const __defaultProcessExecutor: ProcessExecutor = async (python, args, { timeout, signal }) => {
  try {
    const stdout = await execFileTree(python, args, { timeoutMs: timeout, signal });
    return { stdout, stderr: "", code: 0, killed: false, truncated: false };
  } catch (error) {
    const failed = error as { stdout?: string; code?: unknown; killed?: boolean; name?: string };
    return {
      stdout: failed.stdout ?? "",
      stderr: "",
      code: failed.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER" ? failed.code : typeof failed.code === "number" ? failed.code : 1,
      killed: failed.killed ?? failed.name === "AbortError",
      truncated: failed.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER",
    };
  }
};
let processExecutor = __defaultProcessExecutor;
export function __setProcessExecutor(fn: ProcessExecutor) {
  processExecutor = fn;
}

export default (pi: ExtensionAPI) => {
  const tools: Parameters<ExtensionAPI["registerTool"]>[0][] = [];
  // Pi passes the call's AbortSignal as execute's third argument; py() forwards it to the process executor.
  const callSignal = new AsyncLocalStorage<AbortSignal | undefined>();
  const registerTool: ExtensionAPI["registerTool"] = (definition) => {
    const tool = {
      ...definition,
      execute: (id: string, params: unknown, ...rest: unknown[]) =>
        callSignal.run(rest[0] instanceof AbortSignal ? rest[0] : undefined, () => definition.execute(id, withoutNulls(params), ...rest)),
    };
    tools.push(tool);
    pi.registerTool({ ...tool, defaultActive: false });
  };
  let discoveryEnabled: boolean | undefined;
  const exposeAllTools = () => {
    for (const tool of tools) pi.registerTool(tool);
    discoveryEnabled = false;
  };
  const initializeDiscovery = () => {
    if (discoveryEnabled !== undefined) return;
    // Exposure metadata distinguishes native discovery from older tool-list APIs.
    if (!pi.getAllTools || !pi.getActiveTools || !pi.setActiveTools
        || !pi.getAllTools().some(tool => tool.name === "tool_search" && tool.exposure === "model-only")) {
      exposeAllTools();
      return;
    }
    pi.setActiveTools([...new Set([...pi.getActiveTools(), "tool_search"])]);
    if (!pi.getActiveTools().includes("tool_search")) {
      exposeAllTools();
      return;
    }
    for (const tool of tools) pi.registerTool({ ...tool, exposure: "deferred" });
    discoveryEnabled = true;
  };
  pi.on("session_start", initializeDiscovery);
  pi.on("before_agent_start", () => {
    initializeDiscovery();
    if (discoveryEnabled && !pi.getActiveTools?.().includes("tool_search")) {
      exposeAllTools();
    }
    return discoveryEnabled ? {
      message: {
        customType: "stock-analysis-discovery",
        content: "Stock analysis tools are available through tool_search. Search with exact tool names (e.g. get_quote, get_financials, screen_stocks, run_backtest) or keywords; their descriptions are Chinese, so Chinese keywords match on Pi 1.5+, while older Pi matches only English/ASCII terms. Load the tools needed for the task, then call them normally.",
        display: false,
      },
    } : undefined;
  });
  // Resolve the plugin root robustly: __dirname under jiti/CJS-style loaders,
  // else import.meta.url under native ESM (where __dirname is undefined).
  const baseDir =
    typeof __dirname !== "undefined"
      ? `${__dirname}/..`
      : fileURLToPath(new URL("..", import.meta.url));
  const toolsDir = `${baseDir}/tools`;
  const python = resolvePython(baseDir);
  const py = async (script: string, args: string[]) => {
    const result = await processExecutor(python, [`${toolsDir}/${script}`, ...args], { timeout: commandTimeout(script, args), signal: callSignal.getStore() });
    const failure = result.killed || result.code !== 0 ? failureMessage(result) : undefined;
    return { ...result, stdout: cliJson(result.stdout, failure) };
  };
  const asText = (text: string) => ({
    content: [{ type: "text" as const, text }],
    details: {},
  });

  for (const { name, description, parameters } of TOOL_SCHEMAS) {
    registerTool({
      name,
      description,
      parameters,
      async execute(_id, params) {
        return asText(await callTool(name, params, async (script: string, args: string[]) => (await py(script, args)).stdout));
      },
    });
  }

  // --- Skill Discovery ---

  // Same dynamic scan as dsh/hermes: every skills/<name>/SKILL.md is discovered without a list edit.
  pi.on("resources_discover", () => ({
    skillPaths: readdirSync(`${baseDir}/skills`, { withFileTypes: true })
      .filter(entry => entry.isDirectory() && existsSync(`${baseDir}/skills/${entry.name}/SKILL.md`))
      .map(entry => `${baseDir}/skills/${entry.name}/SKILL.md`),
  }));
};
