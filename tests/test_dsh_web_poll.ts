import assert from "node:assert/strict";
import vm from "node:vm";
import { build } from "esbuild";

function element() {
  const listeners = new Map<string, Array<(event: unknown) => void>>();
  const styleProps = new Map<string, string>();
  return {
    listeners,
    hidden: false,
    disabled: false,
    textContent: "",
    innerHTML: "",
    className: "",
    value: "",
    dataset: {} as Record<string, string>,
    children: [] as unknown[],
    style: {
      props: styleProps,
      setProperty(name: string, value: string) { styleProps.set(name, value); },
      removeProperty(name: string) { styleProps.delete(name); },
    },
    classList: { add() {}, remove() {}, toggle() {} },
    clientWidth: 100,
    scrollWidth: 100,
    offsetWidth: 0,
    offsetHeight: 0,
    addEventListener(type: string, fn: (event: unknown) => void) {
      listeners.set(type, [...(listeners.get(type) ?? []), fn]);
    },
    append(...nodes: unknown[]) { this.children.push(...nodes); },
    appendChild(node: unknown) { this.children.push(node); },
    replaceChildren() { this.children.length = 0; },
    remove() {},
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
const windowListeners = new Map<string, Array<(event: unknown) => void>>();
const window = {
  innerWidth: 1024,
  parent: null as unknown,
  addEventListener(type: string, fn: (event: unknown) => void) {
    windowListeners.set(type, [...(windowListeners.get(type) ?? []), fn]);
  },
};
window.parent = window;

let resolveFirst!: (value: unknown) => void;
const firstSnapshot = new Promise(resolve => { resolveFirst = resolve; });
const snapshotCalls: string[] = [];
const writeCalls: unknown[] = [];
const fundAndAdvisor = [
  {
    symbol: "018147", region: "fund",
    quote: { name: "建信新兴市场混合(QDII)C", nav: 1.716, nav_date: "2026-11-18", change_pct: 1.06 },
    trend: [{ date: "2026-11-18", close: 1.716 }],
  },
  {
    symbol: "TIAA001001", region: "advisor",
    quote: { name: "长钱账户", advisor_org: "嘉实财富管理有限公司" },
    trend: null,
  },
];
function payload() {
  return {
    watchlist: fundAndAdvisor, ribbon: [], phases: {},
    market: { region: "cn", indices: [] },
    stats: {},
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
const rows = JSON.stringify(byId("rows").children);
for (const label of ["场外基金", "1.7160", "净值日 2026-11-18", "投顾组合", "无统一净值", "嘉实财富管理有限公司"]) {
  assert(rows.includes(label), `watchlist is missing ${label}`);
}
const filters = JSON.stringify(byId("filters").children);
assert(filters.includes("场外基金 1") && filters.includes("投顾组合 1"));
assert(!rows.includes("美股"));

// Undo bar appears after removing an item and re-adds it when clicked.
const removeBtn = { dataset: { remove: "018147", label: "建信新兴市场混合(QDII)C" } };
for (const listener of byId("rows").listeners.get("click") ?? []) {
  listener({ target: { closest: (sel: string) => (sel === "button[data-remove]" ? removeBtn : null) } });
}
await new Promise(resolve => setTimeout(resolve, 10));
assert.deepEqual(writeCalls.at(-1), { op: "remove", symbols: ["018147"] });
assert.equal(byId("watch-undo").hidden, false, "undo bar becomes visible after remove");
const undoBtn = { dataset: { undo: "018147" } };
for (const listener of byId("watch-undo").listeners.get("click") ?? []) {
  listener({ target: { closest: (sel: string) => (sel === "button[data-undo]" ? undoBtn : null) } });
}
await new Promise(resolve => setTimeout(resolve, 10));
assert.deepEqual(writeCalls.at(-1), { op: "add", symbols: ["018147"] });
assert.equal(byId("watch-undo").hidden, true, "undo bar hides once clicked");

// Theme message clears stale inline palette variables when a token is omitted.
for (const listener of windowListeners.get("message") ?? []) {
  listener({ origin: "http://dashboard.local", source: window, data: { type: "stock-analysis:theme", scheme: "dark", palette: { bg: "#111111" } } });
}
assert.equal(document.documentElement.style.props.get("--bg"), "#111111");
for (const listener of windowListeners.get("message") ?? []) {
  listener({ origin: "http://dashboard.local", source: window, data: { type: "stock-analysis:theme", scheme: "light", palette: {} } });
}
assert.equal(document.documentElement.style.props.has("--bg"), false, "omitted theme token is removed");

console.log("dsh web: watchlist polling, fund NAV, advisor rows, undo and theme cleanup passed");
