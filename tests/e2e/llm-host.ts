import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { createJiti } from "jiti";

interface ToolResult {
  content: { type: string; text: string }[];
  details?: unknown;
}

export interface RegisteredTool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  execute: (id: string, args: Record<string, unknown>) => Promise<ToolResult>;
}

export async function loadLLMHost(host: string): Promise<RegisteredTool[]> {
  if (!["pi", "openclaw", "dsh"].includes(host)) throw new Error("Expected pi, openclaw or dsh");
  const tools: RegisteredTool[] = [];
  const registerTool = (tool: RegisteredTool) => { tools.push(tool); };
  const jiti = createJiti(fileURLToPath(import.meta.url), {
    alias: host === "openclaw" ? {
      "openclaw/plugin-sdk/plugin-entry": fileURLToPath(new URL("../_stubs/openclaw-sdk.ts", import.meta.url)),
    } : {},
  });

  if (host === "pi") {
    const plugin = await jiti.import("../../pi/index.ts") as { default: (api: unknown) => void };
    const exec = promisify(execFile);
    plugin.default({
      registerTool,
      on() {},
      async exec(cmd: string, args: string[] = [], options?: { timeout?: number }) {
        const { stdout, stderr } = await exec(cmd, args, {
          maxBuffer: 32 * 1024 * 1024, timeout: options?.timeout,
        });
        return { stdout, stderr, code: 0, killed: false };
      },
    });
  } else if (host === "openclaw") {
    // Only the unavailable host entry API is stubbed; use real TypeBox schemas and executor.
    const globals = globalThis as typeof globalThis & { __OPENCLAW_TEST__?: unknown };
    globals.__OPENCLAW_TEST__ = { fakeSdk: { definePluginEntry: (config: unknown) => config } };
    try {
      const plugin = await jiti.import("../../openclaw/index.ts") as {
        default: { register: (api: unknown) => void };
      };
      plugin.default.register({ registerTool });
    } finally {
      delete globals.__OPENCLAW_TEST__;
    }
  } else {
    // Actual dsh SDK compiles/validates schemas; execute still spawns real Python.
    type DshTool = Omit<RegisteredTool, "execute"> & {
      execute: (args: Record<string, unknown>, context: unknown) => Promise<string>;
      output: { render: (args: Record<string, unknown>, value: string) => ToolResult["content"] };
    };
    const plugin = await jiti.import("../../dsh/index.ts") as { apply: (ctx: unknown) => void };
    plugin.apply({
      tools: {
        register(tool: DshTool) {
          registerTool({
            name: tool.name, description: tool.description, parameters: tool.parameters,
            async execute(_id, args) {
              const value = await tool.execute(args, {});
              return { content: tool.output.render(args, value) };
            },
          });
        },
      },
      skills: { register() {} },
    });
  }
  return tools;
}
