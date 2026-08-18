// Stub for @deepseek-ai/dsh-tools — used in tests via jiti alias.
// defineTool is the identity function: the captured options object IS the
// definition handed to ctx.tools.register, so tests can inspect name,
// parameters, output.render, and invoke execute() directly.
export const defineTool = (opts: unknown) => opts;
