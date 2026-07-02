declare module "openclaw/plugin-sdk/plugin-entry" {
  interface ToolContent {
    type: "text";
    text: string;
  }

  interface ToolResult {
    content: ToolContent[];
  }

  interface ToolDefinition<P = unknown> {
    name: string;
    description: string;
    parameters: unknown;
    execute(id: string, params: P): Promise<ToolResult>;
  }

  interface PluginAPI {
    registerTool<P = any>(
      tool: ToolDefinition<P>,
      opts?: { optional?: boolean }
    ): void;
  }

  interface PluginEntryConfig {
    id: string;
    name: string;
    description: string;
    register(api: PluginAPI): void;
  }

  export function definePluginEntry(config: PluginEntryConfig): unknown;
}

declare module "typebox" {
  export const Type: {
    Object(props: Record<string, unknown>, opts?: unknown): unknown;
    String(opts?: { description?: string }): unknown;
    Number(opts?: { description?: string }): unknown;
    Boolean(opts?: { description?: string }): unknown;
    Optional(schema: unknown): unknown;
    Union(schemas: unknown[], opts?: { description?: string }): unknown;
    Literal<T extends string | number | boolean>(value: T): unknown;
    Array(item: unknown, opts?: { description?: string }): unknown;
  };
}
