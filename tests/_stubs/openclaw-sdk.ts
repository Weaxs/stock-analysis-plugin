// Stub for openclaw/plugin-sdk/plugin-entry — used in tests via jiti alias.
// Reads the test fixture from globalThis to avoid module-level cycles.
const g = globalThis as any;
export const definePluginEntry = (config: unknown) =>
  g.__OPENCLAW_TEST__.fakeSdk.definePluginEntry(config);
