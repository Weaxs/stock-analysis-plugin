declare module "pi-agent" {
  interface ToolDefinition {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    execute(args: Record<string, unknown>): Promise<string>;
  }

  interface ExecResult {
    stdout: string;
    stderr: string;
    exitCode: number;
  }

  interface ExtensionAPI {
    exec(command: string): Promise<ExecResult>;
    registerTool(tool: ToolDefinition): void;
    on(event: string, handler: (...args: unknown[]) => unknown): void;
  }
}
