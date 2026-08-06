declare module "pi-agent" {
  // Mirrors the real @earendil-works/pi-coding-agent extension API (v0.83+).
  interface TextContent {
    type: "text";
    text: string;
  }

  interface AgentToolResult {
    content: TextContent[];
    details: Record<string, unknown>;
  }

  interface ToolDefinition {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    execute(
      toolCallId: string,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      params: any,
      ...rest: unknown[]
    ): Promise<AgentToolResult>;
  }

  interface ExecResult {
    stdout: string;
    stderr: string;
    code: number;
    killed: boolean;
  }

  interface ResourcesDiscoverResult {
    skillPaths?: string[];
    promptPaths?: string[];
    themePaths?: string[];
  }

  interface ExtensionAPI {
    exec(command: string, args?: string[], options?: { timeout?: number }): Promise<ExecResult>;
    registerTool(tool: ToolDefinition): void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    on(event: string, handler: (...args: any[]) => any): void;
  }
}
