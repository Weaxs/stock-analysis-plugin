import assert from "node:assert/strict";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Readable } from "node:stream";
import type { IncomingMessage, ServerResponse } from "node:http";
import {
  createDashboardHandler,
  dashboardQuoteSymbol,
  normalizeWatchlist,
  readWatchlist,
  symbolRegion,
  writeWatchlist,
  type RunPy,
} from "../dsh/dashboard.js";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..");
const sourceWeb = join(repoRoot, "dsh", "web");
const temp = mkdtempSync(join(tmpdir(), "stock-dashboard-test-"));
// The handler serves built assets; tests stay build-free with a stand-in for the compiled app.js.
const webDir = join(temp, "web");
mkdirSync(webDir);
for (const file of ["index.html", "style.css"]) copyFileSync(join(sourceWeb, file), join(webDir, file));
writeFileSync(join(webDir, "app.js"), "/* compiled from app.ts */\n");
const realNow = Date.now;
let now = realNow();
Date.now = () => now;
const MINUTE = 60_000;

function mockResponse() {
  let status = 0;
  let headers: Record<string, unknown> = {};
  let body = "";
  const res = {
    writeHead(code: number, value?: Record<string, unknown>) {
      status = code;
      headers = value ?? {};
      return this;
    },
    end(chunk?: unknown) {
      if (chunk !== undefined) body += String(chunk);
      return this;
    },
  } as ServerResponse;
  return { res, status: () => status, headers: () => headers, body: () => body, json: () => JSON.parse(body) };
}

function get(url: string) {
  return { method: "GET", url, headers: { host: "127.0.0.1" } } as IncomingMessage;
}

function post(url: string, value: unknown, origin = "http://127.0.0.1") {
  const req = Readable.from([Buffer.from(JSON.stringify(value))]) as IncomingMessage;
  req.method = "POST";
  req.url = url;
  req.headers = { host: "127.0.0.1", origin, "content-type": "application/json" };
  return req;
}

async function call(handler: (req: IncomingMessage, res: ServerResponse) => Promise<void>, req: IncomingMessage) {
  const out = mockResponse();
  await handler(req, out.res);
  return out;
}

/** Deterministic Python stand-in that records every call and the peak concurrency. */
function fakePython(options: { phase?: string; fail?: (script: string, args: string[]) => boolean } = {}) {
  const calls: string[] = [];
  const timeouts = new Map<string, number | undefined>();
  let active = 0;
  let maxActive = 0;
  const runPy: RunPy = async (script, args, timeoutMs) => {
    const key = `${script}:${args.join(" ")}`;
    calls.push(key);
    timeouts.set(key, timeoutMs);
    active++;
    maxActive = Math.max(maxActive, active);
    await new Promise(resolve => setTimeout(resolve, 2));
    active--;
    if (options.fail?.(script, args)) throw new Error("secret stderr\n  at /private/provider.py");
    if (script === "trading_calendar.py") return JSON.stringify({ market: args[1], phase: options.phase ?? "post_market", phase_label: "盘后" });
    if (args[0] === "quote") return JSON.stringify({ symbol: args[1], name: `Name ${args[1]}`, price: 100, change_pct: 1.2, high: 101, low: 99 });
    if (args[0] === "market_indices") return JSON.stringify([{ code: "^HSI", name: "恒生指数", price: 20000, change_pct: -0.5 }]);
    if (args[0] === "kline") return JSON.stringify([{ date: "2026-01-01", close: 10, open: 9 }, { date: "2026-01-02", close: 11 }, { date: "2026-01-03", close: "bad" }]);
    if (args[0] === "sector_rankings") return JSON.stringify({ top: [{ name: "元件", change_pct: 5 }], bottom: [{ name: "白酒", change_pct: -3 }] });
    if (args[0] === "market_stats") return JSON.stringify({ up_count: 2, down_count: 1, flat_count: 1 });
    throw new Error(`unexpected call: ${key}`);
  };
  return { runPy, calls, timeouts, maxActive: () => maxActive };
}

try {
  // ---------- watchlist storage ----------
  assert.deepEqual(normalizeWatchlist([" aapl ", "AAPL", "00700.hk", "sh600519"]), ["AAPL", "00700.HK", "sh600519"]);
  assert.throws(() => normalizeWatchlist("AAPL"), /数组/);
  assert.throws(() => normalizeWatchlist(["AAPL;rm"]), /代码/);
  assert.throws(() => normalizeWatchlist(Array.from({ length: 31 }, (_, i) => `S${i}`)), /30/);
  assert.equal(dashboardQuoteSymbol("002444"), "sz002444");
  assert.equal(dashboardQuoteSymbol("520500"), "sh520500");
  assert.equal(dashboardQuoteSymbol("920001"), "bj920001");
  assert.equal(dashboardQuoteSymbol("sh600519"), "sh600519");
  assert.equal(dashboardQuoteSymbol("AAPL"), "AAPL");
  assert.deepEqual(
    ["600519", "sh000001", "00700.HK", "7203.T", "005930.KS", "035720.KQ", "2330.TW", "AAPL", "BRK-B"].map(symbolRegion),
    ["cn", "cn", "hk", "jp", "kr", "kr", "tw", "us", "us"],
  );

  const watchlistFile = join(temp, "nested", "watchlist.json");
  writeWatchlist(watchlistFile, ["aapl", "00700.hk"]);
  assert.deepEqual(readWatchlist(watchlistFile), ["AAPL", "00700.HK"]);
  writeFileSync(watchlistFile, "{broken", "utf8");
  assert.throws(() => readWatchlist(watchlistFile), /损坏/);
  {
    const handler = createDashboardHandler({ runPy: async () => "{}", watchlistFile, webDir });
    const out = await call(handler, post("/stock-analysis/api/watchlist", { op: "add", symbols: ["AAPL"] }));
    assert.equal(out.status(), 500);
    assert.equal(readFileSync(watchlistFile, "utf8"), "{broken", "failed POST must preserve the original bytes");
    const snap = await call(handler, get("/stock-analysis/api/snapshot"));
    assert.equal(snap.status(), 500, "a corrupt watchlist is reported, never treated as empty");
  }

  // ---------- snapshot is served from memory and never waits for Python ----------
  {
    const file = join(temp, "never", "watchlist.json");
    writeWatchlist(file, ["002444"]);
    const handler = createDashboardHandler({ runPy: () => new Promise<string>(() => {}), watchlistFile: file, webDir });
    let done = false;
    const pending = call(handler, get("/stock-analysis/api/snapshot?region=cn")).then(out => { done = true; return out; });
    await new Promise(resolve => setTimeout(resolve, 20));
    assert(done, "snapshot must answer while every provider hangs");
    const snap = (await pending).json();
    assert.deepEqual(snap.watchlist.map((item: { symbol: string }) => item.symbol), ["002444"]);
    assert.equal(snap.watchlist[0].quote, null);
    assert.equal(snap.watchlist[0].quoteLoading, true);
    assert(snap.pending > 0, "the snapshot reports queued background work");
    handler.dispose();
  }

  // ---------- warm-up fetches the fixed job set within the worker budget ----------
  const file = join(temp, "api", "watchlist.json");
  writeWatchlist(file, ["600519", "AAPL", "00700.HK"]);
  const py = fakePython();
  const handler = createDashboardHandler({ runPy: py.runPy, watchlistFile: file, webDir });
  {
    await handler.tick();
    await handler.settle();
    assert(py.maxActive() <= 4, `python concurrency ${py.maxActive()} exceeded four`);
    for (const expected of [
      "stock_data.py:quote sh600519",
      "stock_data.py:quote AAPL",
      "stock_data.py:quote sh000001",
      "stock_data.py:market_indices --region hk",
      "stock_data.py:kline 600519 --period daily --count 30",
      "stock_data.py:kline ^HSI --period daily --count 30",
      "stock_data.py:sector_rankings --top 5 --direction both --board-type industry",
      "stock_data.py:market_stats",
      "trading_calendar.py:phase JP",
    ]) assert(py.calls.includes(expected), `missing ${expected}`);
    assert(!py.calls.includes("stock_data.py:market_indices --region cn"), "A-share indices use direct quotes");
    assert.equal(py.timeouts.get("stock_data.py:market_stats"), 130_000);
    assert.equal(py.timeouts.get("stock_data.py:kline 600519 --period daily --count 30"), 20_000);
    assert.equal(py.timeouts.get("stock_data.py:kline AAPL --period daily --count 30"), 40_000, "Yahoo-backed klines get a longer budget");

    const out = await call(handler, get("/stock-analysis/api/snapshot?region=hk&script=evil.py&argv=bad"));
    assert.equal(out.status(), 200);
    assert.equal(out.headers()["cache-control"], "no-store");
    const snap = out.json();
    assert.deepEqual(snap.watchlist.map((item: { symbol: string; region: string }) => [item.symbol, item.region]), [["600519", "cn"], ["AAPL", "us"], ["00700.HK", "hk"]]);
    assert.equal(snap.watchlist[0].quote.symbol, "600519", "quotes keep the saved watchlist code");
    assert.deepEqual(snap.watchlist[0].trend, [{ date: "2026-01-01", close: 10 }, { date: "2026-01-02", close: 11 }], "trends are compact and drop invalid rows");
    assert.equal(snap.market.region, "hk");
    assert.deepEqual(snap.market.indices.map((row: { code: string }) => row.code), ["^HSI", "^HSCE", "^HSTECH"]);
    assert.equal(snap.market.indices[0].quote.price, 20000);
    assert.equal(snap.market.indices[1].quote, null, "missing provider rows stay empty rather than invented");
    assert.equal(snap.ribbon[0].code, "sh000001");
    assert.equal(snap.ribbon[0].quote.price, 100);
    assert.equal(snap.stats.value.up_count, 2);
    assert.equal(snap.sectors.value.bottom[0].name, "白酒");
    assert.equal(snap.phases.cn.phase, "post_market");
    assert(!py.calls.some(call => /evil|bad/.test(call)), "query parameters cannot select commands");
  }

  // ---------- freshness follows trading phase and viewers ----------
  {
    const before = py.calls.length;
    await handler.tick();
    await handler.settle();
    assert.equal(py.calls.length, before, "fresh values are not refetched");

    now += 6 * MINUTE;
    await handler.tick();
    await handler.settle();
    const phaseOnly = py.calls.slice(before);
    assert(phaseOnly.length > 0 && phaseOnly.every(call => call.startsWith("trading_calendar.py")), "outside trading hours only the phase refreshes within 30 minutes");

    now += 30 * MINUTE;
    const mark = py.calls.length;
    await handler.tick();
    await handler.settle();
    assert(py.calls.slice(mark).includes("stock_data.py:quote sh600519"), "closed-market quotes refresh every 30 minutes");
  }
  {
    const tradingFile = join(temp, "trading", "watchlist.json");
    writeWatchlist(tradingFile, ["600519"]);
    const trading = fakePython({ phase: "afternoon" });
    const live = createDashboardHandler({ runPy: trading.runPy, watchlistFile: tradingFile, webDir });
    await live.tick();
    await live.settle();
    const quoteCalls = () => trading.calls.filter(call => call === "stock_data.py:quote sh600519").length;
    now += 40_000;
    await live.tick();
    await live.settle();
    assert.equal(quoteCalls(), 1, "without a viewer, trading quotes wait five minutes");
    await call(live, get("/stock-analysis/api/snapshot"));
    await live.settle();
    assert.equal(quoteCalls(), 2, "an open page tightens trading quotes to 30 seconds");
    now += 5 * MINUTE;
    await live.tick();
    await live.settle();
    assert.equal(quoteCalls(), 3);
  }

  {
    const holidayFile = join(temp, "holiday", "watchlist.json");
    writeWatchlist(holidayFile, ["600519"]);
    const holiday = fakePython({ phase: "closed" });
    const idle = createDashboardHandler({ runPy: holiday.runPy, watchlistFile: holidayFile, webDir });
    await idle.tick();
    await idle.settle();
    const quoteCalls = () => holiday.calls.filter(call => call === "stock_data.py:quote sh600519").length;
    now += 61 * MINUTE;
    await idle.tick();
    await idle.settle();
    assert.equal(quoteCalls(), 1, "non-trading days do not refresh hourly");
    now += 60 * MINUTE;
    await idle.tick();
    await idle.settle();
    assert.equal(quoteCalls(), 2, "non-trading days refresh every two hours");
  }

  // ---------- failures keep the last value, are sanitized and back off ----------
  {
    const failFile = join(temp, "fail", "watchlist.json");
    writeWatchlist(failFile, ["AAPL"]);
    let failing = false;
    const flaky = fakePython({ fail: (_script, args) => failing && args[1] === "AAPL" });
    const failHandler = createDashboardHandler({ runPy: flaky.runPy, watchlistFile: failFile, webDir });
    await failHandler.tick();
    await failHandler.settle();
    failing = true;
    await call(failHandler, post("/stock-analysis/api/refresh", {}));
    await failHandler.settle();
    const snap = (await call(failHandler, get("/stock-analysis/api/snapshot"))).json();
    const item = snap.watchlist[0];
    assert.equal(item.quote.price, 100, "a failed refresh keeps the last good quote");
    assert.equal(item.quoteError, "数据暂不可用");
    assert(!JSON.stringify(snap).includes("secret") && !JSON.stringify(snap).includes("/private"), "provider errors are sanitized");
    const attempts = () => flaky.calls.filter(call => call === "stock_data.py:quote AAPL").length;
    const afterFailure = attempts();
    now += 30_000;
    await failHandler.tick();
    await failHandler.settle();
    assert.equal(attempts(), afterFailure, "a failure is not retried within a minute");
    now += MINUTE;
    await failHandler.tick();
    await failHandler.settle();
    assert.equal(attempts(), afterFailure + 1, "the first retry happens after a minute");
    now += MINUTE + 1000;
    await failHandler.tick();
    await failHandler.settle();
    assert.equal(attempts(), afterFailure + 1, "repeated failures back off exponentially");
  }

  // ---------- manual refresh ----------
  {
    const mark = py.calls.length;
    const wrong = await call(handler, get("/stock-analysis/api/refresh"));
    assert.equal(wrong.status(), 405);
    const out = await call(handler, post("/stock-analysis/api/refresh", {}));
    assert.equal(out.status(), 202);
    await handler.settle();
    assert(py.calls.slice(mark).includes("stock_data.py:quote AAPL"), "refresh refetches fresh entries");
    const crossSite = await call(handler, post("/stock-analysis/api/refresh", {}, "https://evil.example"));
    assert.equal(crossSite.status(), 403);
  }

  // ---------- watchlist edits ----------
  {
    const out = await call(handler, post("/stock-analysis/api/watchlist", { op: "add", symbols: ["7203.T", "aapl"] }));
    assert.equal(out.status(), 200);
    assert.deepEqual(out.json().symbols, ["600519", "AAPL", "00700.HK", "7203.T"]);
    await handler.settle();
    assert(py.calls.includes("stock_data.py:quote 7203.T"), "new symbols are fetched right away");
    const removed = await call(handler, post("/stock-analysis/api/watchlist", { op: "remove", symbols: ["600519"] }));
    assert.deepEqual(removed.json().symbols, ["AAPL", "00700.HK", "7203.T"]);
    const listed = await call(handler, get("/stock-analysis/api/watchlist"));
    assert.deepEqual(listed.json().symbols, ["AAPL", "00700.HK", "7203.T"]);
    for (const body of [{ op: "add", symbols: ["BAD;CODE"] }, null, { op: "rename", symbols: ["AAPL"] }, { symbols: ["AAPL"] }]) {
      const bad = await call(handler, post("/stock-analysis/api/watchlist", body));
      assert.equal(bad.status(), 400);
    }
    assert.equal(readWatchlist(file).length, 3, "invalid writes cannot change state");

    const mergeFile = join(temp, "merge", "watchlist.json");
    writeWatchlist(mergeFile, ["600519"]);
    const merge = createDashboardHandler({ runPy: py.runPy, watchlistFile: mergeFile, webDir });
    await Promise.all([
      call(merge, post("/stock-analysis/api/watchlist", { op: "add", symbols: ["AAPL"] })),
      call(merge, post("/stock-analysis/api/watchlist", { op: "add", symbols: ["MSFT"] })),
    ]);
    assert.deepEqual(readWatchlist(mergeFile), ["600519", "AAPL", "MSFT"], "incremental adds cannot erase another tab's update");
    await merge.settle();
  }

  // ---------- request validation and same-origin guard ----------
  {
    const bad = await call(handler, get("/stock-analysis/api/snapshot?region=bad"));
    assert.equal(bad.status(), 400);
    assert.match(bad.json().error, /市场/);
    assert.equal((await call(handler, get("/stock-analysis/api/market?region=cn"))).status(), 404, "retired routes are gone");

    const evil = get("/stock-analysis/api/snapshot");
    evil.headers.host = "evil.example";
    evil.headers.origin = "https://evil.example";
    assert.equal((await call(handler, evil)).status(), 403, "an evil Host cannot pass the same-origin check");
    const crossSite = get("/stock-analysis/api/snapshot");
    crossSite.headers["sec-fetch-site"] = "cross-site";
    assert.equal((await call(handler, crossSite)).status(), 403);
    const ipv6 = get("/stock-analysis/api/snapshot");
    ipv6.headers.host = "[::1]:49488";
    assert.equal((await call(handler, ipv6)).status(), 200, "IPv6 loopback remains supported");
    const crossWrite = await call(handler, post("/stock-analysis/api/watchlist", { op: "add", symbols: ["MSFT"] }, "https://evil.example"));
    assert.equal(crossWrite.status(), 403);
    assert(!readWatchlist(file).includes("MSFT"), "cross-origin write cannot change state");
  }

  // ---------- static assets ----------
  {
    const page = await call(handler, get("/stock-analysis/"));
    assert.equal(page.status(), 200);
    assert.match(page.body(), /股票仪表盘/);
    assert.match(page.headers()["content-type"] as string, /text\/html/);
    assert.match(page.body(), /<script src="\.\/app\.js"/, "the page loads the compiled bundle");
    assert.match((await call(handler, get("/stock-analysis/app.js"))).headers()["content-type"] as string, /javascript/);
    for (const asset of ["app.js", "style.css"]) assert.equal((await call(handler, get(`/stock-analysis/${asset}`))).status(), 200);
    assert.equal((await call(handler, get("/stock-analysis/../package.json"))).status(), 404);
    const appSource = readFileSync(join(sourceWeb, "app.ts"), "utf8");
    assert(appSource.includes("./api/snapshot"), "the page reads one memory snapshot");
    assert(!/api\/(?:market|index-trend|watchlist\/quote)/.test(appSource), "the page does not call retired per-symbol routes");
  }
  await handler.settle();

  // ---------- resident warmer lifecycle ----------
  {
    const warmFile = join(temp, "warm", "watchlist.json");
    writeWatchlist(warmFile, ["600519"]);
    const warm = fakePython();
    const resident = createDashboardHandler({ runPy: warm.runPy, watchlistFile: warmFile, webDir, warm: true });
    await new Promise(resolve => setTimeout(resolve, 5));
    assert(warm.calls.length > 0, "the warmer starts without waiting for a page");
    resident.dispose();
    await resident.settle();
  }

  // ---------- package manifest ----------
  const pkg = JSON.parse(readFileSync(join(repoRoot, "dsh", "package.json"), "utf8"));
  assert.equal(pkg.exports["./client"], "./dist/client.js");
  assert.equal(pkg.dsh.client.platform, "web");
  assert(pkg.dsh.client.inject.includes("@deepseek-ai/dsh-client-ui-sidebar"));
  assert(pkg.dsh.client.inject.includes("@deepseek-ai/dsh-client-ui-layout"));
  const publish = readFileSync(join(repoRoot, ".github", "workflows", "publish.yml"), "utf8");
  assert(publish.includes("dist/client.js"), "unscoped publish must rewrite the browser module id too");
  assert(publish.includes(`grep -q 'id: "dsh-stock-analysis"' dist/client.js`), "publish verifies the compiled (double-quoted) client id");
  const clientSource = readFileSync(join(repoRoot, "dsh", "client.ts"), "utf8");
  const unscopedClient = clientSource.replaceAll(pkg.name, "dsh-stock-analysis");
  assert(unscopedClient.includes('id: "dsh-stock-analysis"'), "the unscoped bundle must register the unscoped client id");
  assert(!unscopedClient.includes(`id: "${pkg.name}"`), "the scoped id must not remain after the twin rewrite");

  console.log("dsh dashboard: snapshot, warmer freshness, failures, watchlist, security, assets, and package manifest passed");
} finally {
  Date.now = realNow;
  rmSync(temp, { recursive: true, force: true });
}
