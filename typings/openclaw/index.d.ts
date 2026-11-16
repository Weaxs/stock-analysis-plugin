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
    canDeliverSourceReply?: boolean;
    catalogMode?: "direct-only";
    description: string;
    parameters: unknown;
    execute(id: string, params: P): Promise<ToolResult>;
  }

  interface PluginAPI {
    rootDir?: string;
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
  type Options = Record<string, unknown>;
  export const Type: {
    Object(props: Record<string, unknown>, opts?: Options): unknown;
    String(opts?: Options): unknown;
    Number(opts?: Options): unknown;
    Null(opts?: Options): unknown;
    Unknown(opts?: Options): unknown;
    Boolean(opts?: Options): unknown;
    Optional(schema: unknown): unknown;
    Union(schemas: unknown[], opts?: Options): unknown;
    Literal<T extends string | number | boolean>(value: T): unknown;
    Array(item: unknown, opts?: Options): unknown;
  };
}
