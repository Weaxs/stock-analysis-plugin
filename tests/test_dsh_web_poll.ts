import assert from "node:assert/strict";
import vm from "node:vm";
import { build } from "esbuild";

function element() {
  const listeners = new Map<string, Array<(event: unknown) => void>>();
  return {
    listeners,
    hidden: false,
    textContent: "",
    innerHTML: "",
    className: "",
    value: "",
    dataset: {},
    style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {} },
    clientWidth: 100,
    scrollWidth: 100,
    offsetWidth: 0,
    offsetHeight: 0,
    addEventListener(type: string, fn: (event: unknown) => void) {
      listeners.set(type, [...(listeners.get(type) ?? []), fn]);
    },
    append() {}, appendChild() {}, replaceChildren() {}, remove() {},
    setAttribute() {}, focus() {}, scrollBy() {}, scrollIntoView() {},
    querySelector() { return element(); },
    querySelectorAll() { return []; },
    closest() { return null; },
    getBoundingClientRect() { return { top: 0, left: 0, width: 100, height: 100 }; },
  };
}

const elements = new Map<string, ReturnType<typeof element>>();
const byId = (id: string) => {
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id)!;
};
const storage = new Map<string, string>();
const document = {
  visibilityState: "visible",
  documentElement: element(),
  getElementById: byId,
  createElement: element,
  createElementNS: element,
  createTextNode: (text: string) => ({ textContent: text }),
  querySelector: () => element(),
  querySelectorAll: () => [],
  addEventListener() {},
};
const window = {
  innerWidth: 1024,
  parent: null as unknown,
  addEventListener() {},
};
window.parent = window;

let resolveFirst!: (value: unknown) => void;
const firstSnapshot = new Promise(resolve => { resolveFirst = resolve; });
const snapshotCalls: string[] = [];
const writeCalls: unknown[] = [];
function payload() {
  return {
    watchlist: [], ribbon: [], phases: {},
    market: { region: "cn", indices: [] },
    stats: { value: null, error: null, updatedAt: null, loading: false },
    sectors: { value: { top: [], bottom: [] }, error: null, updatedAt: null, loading: false },
    pending: 0, fetchedAt: new Date().toISOString(),
  };
}
async function fetch(path: string, options: { method?: string; body?: string } = {}) {
  if (path.startsWith("./api/snapshot")) {
    snapshotCalls.push(path);
    const value = snapshotCalls.length === 1 ? await firstSnapshot : payload();
    return { ok: true, status: 200, json: async () => value };
  }
  if (path === "./api/watchlist") {
    writeCalls.push(JSON.parse(options.body ?? "{}"));
    return { ok: true, status: 200, json: async () => ({ symbols: ["AAPL"] }) };
  }
  throw new Error("unexpected fetch: " + path);
}

const timers = new Map<number, unknown>();
let nextTimer = 1;
const sandbox = {
  document,
  window,
  location: { search: "", origin: "http://dashboard.local" },
  localStorage: {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  },
  fetch,
  AbortController,
  URLSearchParams,
  Intl,
  console,
  setTimeout: (fn: unknown) => { const id = nextTimer++; timers.set(id, fn); return id; },
  clearTimeout: (id: number) => { timers.delete(id); },
};
const result = await build({
  entryPoints: ["dsh/web/app.ts"],
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  target: "es2020",
});
vm.runInNewContext(result.outputFiles[0].text, sandbox, { filename: "app.js" });

assert.equal(snapshotCalls.length, 1, "page load starts the first snapshot request");
byId("symbol-input").value = "AAPL";
for (const listener of byId("add-form").listeners.get("submit") ?? []) {
  listener({ preventDefault() {} });
}
await new Promise(resolve => setTimeout(resolve, 0));
assert.deepEqual(writeCalls, [{ op: "add", symbols: ["AAPL"] }]);
resolveFirst(payload());
const deadline = Date.now() + 2000;
while (snapshotCalls.length < 2 && Date.now() < deadline) {
  await new Promise(resolve => setTimeout(resolve, 5));
}
assert.equal(snapshotCalls.length, 2, "an addition during an unresolved poll triggers a follow-up snapshot");
console.log("dsh web poll: adding while a snapshot is in flight forces exactly one follow-up poll");
