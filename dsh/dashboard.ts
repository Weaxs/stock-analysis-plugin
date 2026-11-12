import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Context } from "@deepseek-ai/cordis";

export const DASHBOARD_BASE = "/stock-analysis";
export type RunPy = (script: string, args: string[], timeoutMs?: number, signal?: AbortSignal) => Promise<string>;

export const REGIONS = ["cn", "hk", "us", "jp", "kr", "tw"] as const;
export type Region = (typeof REGIONS)[number];
const STATIC_FILES = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/style.css", ["style.css", "text/css; charset=utf-8"]],
] as const);
const MAX_WATCHLIST = 30;
const MAX_PYTHON = 4;
const TICK_MS = 10_000;
const SECOND = 1000;
const MINUTE = 60 * SECOND;

// Fixed index lists: the browser can only name a region, never a symbol or command.
const CN_INDEX_SYMBOLS = ["sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000016"];
export const INDEX_SYMBOLS: Record<Region, string[]> = {
  cn: CN_INDEX_SYMBOLS,
  hk: ["^HSI", "^HSCE", "^HSTECH"],
  us: ["^DJI", "^IXIC", "^GSPC", "^RUT"],
  jp: ["^N225", "^TOPX"],
  kr: ["^KS11", "^KQ11"],
  tw: ["^TWII"],
};
const RIBBON: Array<[Region, string]> = [
  ["cn", "sh000001"], ["cn", "sz399001"], ["cn", "sz399006"],
  ["hk", "^HSI"], ["hk", "^HSTECH"],
  ["us", "^IXIC"], ["us", "^GSPC"], ["us", "^DJI"],
  ["jp", "^N225"], ["kr", "^KS11"], ["tw", "^TWII"],
];
const TRADING_PHASES = new Set(["morning", "afternoon", "intraday"]);

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

function assertApiAccess(req: IncomingMessage) {
  let hostname = "";
  try { hostname = new URL(`http://${req.headers.host}`).hostname.replace(/^\[|\]$/g, "").split("%")[0]; } catch { /* rejected below */ }
  if (!["127.0.0.1", "localhost", "::1"].includes(hostname)) throw new HttpError(403, "只允许本机访问仪表盘 API");
  const site = req.headers["sec-fetch-site"];
  if (site && !["same-origin", "none"].includes(site)) throw new HttpError(403, "只允许同源页面访问仪表盘 API");
  const origin = req.headers.origin;
  if (origin && origin.replace(/^https?:\/\//, "") !== req.headers.host) throw new HttpError(403, "只允许同源页面访问仪表盘 API");
}

export function normalizeWatchlist(input: unknown): string[] {
  if (!Array.isArray(input)) throw new HttpError(400, "symbols 必须是股票代码数组");
  // Keyed by quote symbol: 600519 and sh600519 (also in older saved files) are one stock; the first spelling stays.
  const unique = new Map<string, string>();
  for (const value of input) {
    if (typeof value !== "string") throw new HttpError(400, "股票代码必须是字符串");
    const upper = value.trim().toUpperCase();
    if (!/^[A-Z0-9][A-Z0-9.-]{0,31}$/.test(upper)) throw new HttpError(400, "股票代码格式不正确");
    const symbol = /^(?:SH|SZ|BJ)\d{6}$/.test(upper) ? upper.toLowerCase() : upper;
    const key = dashboardQuoteSymbol(symbol);
    if (!unique.has(key)) unique.set(key, symbol);
  }
  const symbols = [...unique.values()];
  if (symbols.length > MAX_WATCHLIST) throw new HttpError(400, `自选股最多保存 ${MAX_WATCHLIST} 只`);
  return symbols;
}

export function readWatchlist(file: string): string[] {
  let raw: string;
  try {
    raw = readFileSync(file, "utf8");
  } catch (error) {
    if ((error as { code?: string }).code === "ENOENT") return [];
    throw new Error("无法读取自选股文件");
  }
  try {
    const value = JSON.parse(raw) as { symbols?: unknown };
    return normalizeWatchlist(value.symbols);
  } catch {
    throw new Error("自选股文件已损坏，请先修复或移走该文件");
  }
}

export function writeWatchlist(file: string, input: unknown): string[] {
  readWatchlist(file);
  const symbols = normalizeWatchlist(input);
  mkdirSync(dirname(file), { recursive: true });
  const temp = `${file}.${process.pid}-${randomUUID()}.tmp`;
  try {
    writeFileSync(temp, `${JSON.stringify({ version: 1, symbols }, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
    renameSync(temp, file);
  } finally {
    rmSync(temp, { force: true });
  }
  return symbols;
}

async function withWatchlistLock<T>(file: string, work: () => T): Promise<T> {
  mkdirSync(dirname(file), { recursive: true });
  const lock = file + ".lock";
  for (let attempt = 0; ; attempt++) {
    try {
      mkdirSync(lock);  // Atomic on local filesystems on both POSIX and Windows.
      break;
    } catch (error) {
      if ((error as { code?: string }).code !== "EEXIST") throw error;
      try {
        if (Date.now() - statSync(lock).mtimeMs > 30_000) rmSync(lock, { recursive: true, force: true });
      } catch { /* the owner may have released it between attempts */ }
      if (attempt >= 200) throw new HttpError(503, "自选股正被其他进程更新，请稍后重试");
      await new Promise(resolve => setTimeout(resolve, 25));
    }
  }
  try {
    return work();
  } finally {
    rmSync(lock, { recursive: true, force: true });
  }
}

export async function updateWatchlist(file: string, op: string, input: unknown): Promise<string[]> {
  const symbols = normalizeWatchlist(input);
  return withWatchlistLock(file, () => {
    const current = readWatchlist(file);
    if (op === "add") return writeWatchlist(file, [...current, ...symbols]);
    if (op === "remove") {
      const removed = new Set(symbols.map(dashboardQuoteSymbol));
      return writeWatchlist(file, current.filter(symbol => !removed.has(dashboardQuoteSymbol(symbol))));
    }
    throw new HttpError(400, "不支持自选股操作");
  });
}

function sendJson(res: ServerResponse, status: number, value: unknown) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(JSON.stringify(value));
}

async function readJson(req: IncomingMessage, max = 16 * 1024): Promise<any> {
  if (!/^application\/json\b/.test(req.headers["content-type"] ?? "")) throw new HttpError(415, "请使用 JSON 请求");
  return new Promise((resolve, reject) => {
    let size = 0;
    let tooLarge = false;
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > max) tooLarge = true;
      else chunks.push(chunk);
    });
    req.on("end", () => {
      if (tooLarge) return reject(new HttpError(413, "请求内容过大"));
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"));
      } catch {
        reject(new HttpError(400, "JSON 请求格式不正确"));
      }
    });
    req.on("error", reject);
  });
}

export function dashboardQuoteSymbol(symbol: string): string {
  if (!/^\d{6}$/.test(symbol)) return symbol;
  if (/^(?:43|81|82|83|87|88|92)/.test(symbol)) return `bj${symbol}`;
  return `${/^[569]/.test(symbol) ? "sh" : "sz"}${symbol}`;
}

export function symbolRegion(symbol: string): Region {
  if (/^(?:sh|sz|bj)?\d{6}$/.test(symbol)) return "cn";
  if (/\.HK$/.test(symbol)) return "hk";
  if (/\.T$/.test(symbol)) return "jp";
  if (/\.K[SQ]$/.test(symbol)) return "kr";
  if (/\.TWO?$/.test(symbol)) return "tw";
  return "us";
}

/** Provider messages may reach the page; paths, stacks and long bodies never do. */
function safeError(value: unknown, fallback: string): string {
  return typeof value === "string" && value && value.length <= 160 && !/[/\\\n]/.test(value) ? value : fallback;
}

type Kind = "phase" | "quote" | "indices" | "trend" | "sectors" | "stats";
interface Slot {
  value: unknown;
  error: string | null;
  updatedAt: number;
  checkedAt: number;
  running: boolean;
  force: boolean;
  failures: number;
}
interface Job {
  key: string;
  kind: Kind;
  region: Region;
  priority: number;
  run: () => Promise<unknown>;
}

/** Small priority queue so a burst of trend/stat work never delays quotes. */
function createPriorityLimiter(limit: number) {
  let active = 0;
  const waiting: Array<{ priority: number; start: () => void; cancel: () => void }> = [];
  const next = () => {
    while (active < limit && waiting.length) {
      waiting.sort((a, b) => a.priority - b.priority);
      active++;
      waiting.shift()!.start();
    }
  };
  return {
    run<T>(priority: number, fn: () => Promise<T>): Promise<T> {
      return new Promise<T>((resolve, reject) => {
        waiting.push({
          priority,
          start: () => fn().then(resolve, reject).finally(() => { active--; next(); }),
          cancel: () => reject(new Error("仪表盘已停止")),
        });
        next();
      });
    },
    /** Settle queued jobs without starting them. */
    clear() { for (const job of waiting.splice(0)) job.cancel(); },
    get queued() { return waiting.length + active; },
  };
}

function parseJson(raw: string): unknown {
  try { return JSON.parse(raw); } catch { return { error: "行情命令返回了无法解析的数据" }; }
}

function trendRows(value: unknown): Array<{ date: string; close: number }> {
  if (!Array.isArray(value)) throw new Error("K线数据格式不正确");
  return value
    .map(row => ({ date: String(row?.date ?? ""), close: Number(row?.close) }))
    .filter(row => Number.isFinite(row.close) && row.close > 0);
}

export interface DashboardOptions {
  runPy: RunPy;
  watchlistFile: string;
  webDir: string;
  /** Start the resident background warmer (production). Tests drive `tick()` directly. */
  warm?: boolean;
}

type DashboardHandler = ((req: IncomingMessage, res: ServerResponse) => Promise<void>) & {
  tick: () => Promise<void>;
  settle: () => Promise<void>;
  dispose: () => void;
};

export function createDashboardHandler(options: DashboardOptions): DashboardHandler {
  const { runPy, watchlistFile, webDir } = options;
  const limiter = createPriorityLimiter(MAX_PYTHON);
  const slots = new Map<string, Slot>();
  const inflight = new Set<Promise<void>>();
  let viewedRegion: Region = "cn";
  let lastSeen = 0;
  let disposed = false;
  const abort = new AbortController();

  const slot = (key: string): Slot => {
    let value = slots.get(key);
    if (!value) slots.set(key, value = { value: null, error: null, updatedAt: 0, checkedAt: 0, running: false, force: false, failures: 0 });
    return value;
  };

  const trading = (region: Region) => TRADING_PHASES.has((slots.get(`phase:${region}`)?.value as { phase?: string } | null)?.phase ?? "");

  /** How long a successful value stays fresh: tight while the market trades and someone watches. */
  function freshFor(kind: Kind, region: Region): number {
    const open = trading(region);
    const watched = Date.now() - lastSeen < 2 * MINUTE;
    if (kind === "phase") return watched ? MINUTE : 5 * MINUTE;
    // Weekends and holidays: nothing moves, so the resident warmer barely runs.
    if ((slots.get(`phase:${region}`)?.value as { phase?: string } | null)?.phase === "closed") return 2 * 60 * MINUTE;
    switch (kind) {
      case "quote":
      case "indices": return open ? (watched ? 30 * SECOND : 5 * MINUTE) : 30 * MINUTE;
      case "trend": return open ? 10 * MINUTE : 60 * MINUTE;
      case "sectors": return open ? (watched ? MINUTE : 5 * MINUTE) : 30 * MINUTE;
      case "stats": return open ? (watched ? 2 * MINUTE : 10 * MINUTE) : 60 * MINUTE;
    }
  }

  const py = async (script: string, args: string[], timeoutMs: number) => parseJson(await runPy(script, args, timeoutMs, abort.signal));

  function quoteJob(symbol: string, priority: number): Job {
    return {
      key: `quote:${symbol}`, kind: "quote", region: symbolRegion(symbol), priority,
      run: async () => {
        const value = await py("stock_data.py", ["quote", dashboardQuoteSymbol(symbol)], 20_000) as Record<string, unknown> | null;
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("报价数据格式不正确");
        if (value.error) throw new Error(safeError(value.error, "报价暂不可用"));
        return { ...value, symbol };
      },
    };
  }

  function trendJob(symbol: string, region: Region, priority: number): Job {
    return {
      key: `trend:${symbol}`, kind: "trend", region, priority,
      // Yahoo-backed regions regularly need 20-30 s from mainland networks.
      run: async () => trendRows(await py("stock_data.py", ["kline", symbol, "--period", "daily", "--count", "30"], region === "cn" ? 20_000 : 40_000)),
    };
  }

  function jobs(symbols: string[]): Job[] {
    const list: Job[] = [];
    for (const region of REGIONS) {
      list.push({
        key: `phase:${region}`, kind: "phase", region, priority: 0,
        run: async () => {
          const value = await py("trading_calendar.py", ["phase", region.toUpperCase()], 10_000) as Record<string, unknown>;
          if (!value || typeof value !== "object" || value.error) throw new Error("交易阶段暂不可用");
          return value;
        },
      });
    }
    for (const symbol of symbols) list.push(quoteJob(symbol, 1));
    for (const region of REGIONS) {
      const indexPriority = region === viewedRegion ? 1 : 2;
      if (region === "cn") {
        for (const code of CN_INDEX_SYMBOLS) list.push(quoteJob(code, indexPriority));
      } else {
        list.push({
          key: `indices:${region}`, kind: "indices", region, priority: indexPriority,
          run: async () => {
            const value = await py("stock_data.py", ["market_indices", "--region", region], 60_000);
            if (!Array.isArray(value)) throw new Error(safeError((value as { error?: unknown })?.error, "指数暂不可用"));
            return value;
          },
        });
      }
    }
    for (const symbol of symbols) list.push(trendJob(symbol, symbolRegion(symbol), 3));
    for (const region of REGIONS) {
      for (const code of INDEX_SYMBOLS[region]) {
        const ribbon = RIBBON.some(([, item]) => item === code);
        list.push(trendJob(code, region, ribbon || region === viewedRegion ? 3 : 4));
      }
    }
    list.push({
      key: "sectors", kind: "sectors", region: "cn", priority: 4,
      run: async () => {
        const value = await py("stock_data.py", ["sector_rankings", "--top", "5", "--direction", "both", "--board-type", "industry"], 25_000) as Record<string, unknown>;
        if (!value || !Array.isArray(value.top)) throw new Error(safeError(value?.error, "行业排行暂不可用"));
        return { top: value.top, bottom: Array.isArray(value.bottom) ? value.bottom : [] };
      },
    });
    list.push({
      // Slow (~15 s alone) but first-viewport content: start it in the first wave on its own worker.
      key: "stats", kind: "stats", region: "cn", priority: 2,
      run: async () => {
        const value = await py("stock_data.py", ["market_stats"], 130_000) as Record<string, unknown>;
        if (!value || typeof value !== "object" || value.error) throw new Error(safeError(value?.error, "市场宽度暂不可用"));
        return value;
      },
    });
    const unique = new Map<string, Job>();
    for (const job of list) {
      const previous = unique.get(job.key);
      if (!previous || job.priority < previous.priority) unique.set(job.key, job);
    }
    return [...unique.values()];
  }

  function due(job: Job, now: number): boolean {
    const entry = slot(job.key);
    if (entry.running) return false;
    if (entry.force || entry.checkedAt === 0) return true;
    const fresh = freshFor(job.kind, job.region);
    // Failures retry after 1, 2, 4… minutes so a flaky provider cannot monopolise the workers.
    const retry = Math.min(fresh, MINUTE * 2 ** Math.min(entry.failures - 1, 5));
    return now - entry.checkedAt >= (entry.error ? retry : fresh);
  }

  function start(job: Job) {
    const entry = slot(job.key);
    entry.running = true;
    entry.force = false;
    const task = limiter.run(job.priority, job.run).then(
      value => {
        entry.value = value;
        entry.error = null;
        entry.failures = 0;
        entry.updatedAt = Date.now();
      },
      error => {
        // Keep the last good value; the page marks it as possibly stale.
        entry.error = safeError(error instanceof Error ? error.message : null, "数据暂不可用");
        entry.failures++;
      },
    ).finally(() => {
      entry.running = false;
      entry.checkedAt = Date.now();
      inflight.delete(task);
    });
    inflight.add(task);
    return task;
  }

  function currentSymbols(): string[] {
    try { return readWatchlist(watchlistFile); } catch { return []; }
  }

  /** Start every due job; resolves when the jobs started by this tick finish. */
  async function tick(): Promise<void> {
    if (disposed) return;
    const symbols = currentSymbols();
    const list = jobs(symbols);
    const keep = new Set(list.map(job => job.key));
    for (const key of slots.keys()) if (!keep.has(key) && !slots.get(key)!.running) slots.delete(key);
    const now = Date.now();
    await Promise.all(list.filter(job => due(job, now)).sort((a, b) => a.priority - b.priority).map(start));
  }

  async function settle() {
    while (inflight.size) await Promise.all([...inflight]);
  }

  const kick = () => { void tick(); };

  function slotView(key: string) {
    const entry = slots.get(key);
    return {
      value: entry?.value ?? null,
      error: entry?.error ?? null,
      updatedAt: entry?.updatedAt ? new Date(entry.updatedAt).toISOString() : null,
      loading: !entry || entry.running || entry.checkedAt === 0,
    };
  }

  function indexRow(region: Region, code: string) {
    const trend = slotView(`trend:${code}`);
    if (region === "cn") {
      const quote = slotView(`quote:${code}`);
      return { region, code, quote: quote.value, error: quote.error, loading: quote.loading, updatedAt: quote.updatedAt, trend: trend.value };
    }
    const indices = slotView(`indices:${region}`);
    const quote = Array.isArray(indices.value) ? indices.value.find((row: { code?: string }) => row?.code === code) ?? null : null;
    return { region, code, quote, error: indices.error, loading: indices.loading, updatedAt: indices.updatedAt, trend: trend.value };
  }

  function snapshot(region: Region) {
    lastSeen = Date.now();
    viewedRegion = region;
    const symbols = readWatchlist(watchlistFile);
    kick();
    return {
      watchlist: symbols.map(symbol => {
        const quote = slotView(`quote:${symbol}`);
        const trend = slotView(`trend:${symbol}`);
        return {
          symbol,
          region: symbolRegion(symbol),
          quote: quote.value,
          quoteError: quote.error,
          quoteLoading: quote.loading,
          trend: trend.value,
          trendError: trend.error,
        };
      }),
      ribbon: RIBBON.map(([item, code]) => indexRow(item, code)),
      phases: Object.fromEntries(REGIONS.map(item => [item, slotView(`phase:${item}`).value])),
      market: { region, indices: INDEX_SYMBOLS[region].map(code => indexRow(region, code)) },
      // A-share breadth and sectors are the only whole-market statistics; other regions stay honest blanks.
      stats: slotView("stats"),
      sectors: slotView("sectors"),
      pending: limiter.queued,
      fetchedAt: new Date().toISOString(),
    };
  }

  function parseRegion(value: string | null): Region {
    const region = value ?? "cn";
    if (!(REGIONS as readonly string[]).includes(region)) throw new HttpError(400, "不支持的市场区域");
    return region as Region;
  }

  let timer: NodeJS.Timeout | undefined;
  if (options.warm) {
    kick();
    timer = setInterval(kick, TICK_MS);
    timer.unref();
  }

  const handler = (async (req: IncomingMessage, res: ServerResponse) => {
    try {
      const url = new URL(req.url ?? "/", "http://dashboard.local");
      if (url.pathname !== DASHBOARD_BASE && !url.pathname.startsWith(`${DASHBOARD_BASE}/`)) return sendJson(res, 404, { error: "Not found" });
      const rest = url.pathname.slice(DASHBOARD_BASE.length) || "/";
      if (rest.startsWith("/api/")) assertApiAccess(req);

      if (rest === "/api/snapshot") {
        if (req.method !== "GET") return sendJson(res, 405, { error: "Method not allowed" });
        return sendJson(res, 200, snapshot(parseRegion(url.searchParams.get("region"))));
      }
      if (rest === "/api/refresh") {
        if (req.method !== "POST") return sendJson(res, 405, { error: "Method not allowed" });
        for (const entry of slots.values()) if (!entry.running) entry.force = true;
        kick();
        return sendJson(res, 202, { ok: true, pending: limiter.queued });
      }
      if (rest === "/api/watchlist") {
        if (req.method === "GET") return sendJson(res, 200, { symbols: readWatchlist(watchlistFile) });
        if (req.method === "POST") {
          const body = await readJson(req);
          if (!body?.op) throw new HttpError(400, "必须指定 add 或 remove 操作");
          const saved = await updateWatchlist(watchlistFile, body.op, body.symbols);
          kick();
          return sendJson(res, 200, { symbols: saved });
        }
        return sendJson(res, 405, { error: "Method not allowed" });
      }
      if (rest.startsWith("/api/")) return sendJson(res, 404, { error: "Not found" });
      if (req.method !== "GET" && req.method !== "HEAD") return sendJson(res, 404, { error: "Not found" });

      const asset = STATIC_FILES.get(rest as "/" | "/app.js" | "/style.css");
      if (!asset) return sendJson(res, 404, { error: "Not found" });
      const [file, contentType] = asset;
      let body: Buffer;
      try {
        body = readFileSync(join(webDir, file));
      } catch {
        return sendJson(res, 404, { error: "Not found" });
      }
      res.writeHead(200, {
        "content-type": contentType,
        "content-length": body.length,
        "cache-control": "no-cache",
        "x-content-type-options": "nosniff",
      });
      res.end(req.method === "HEAD" ? undefined : body);
    } catch (error) {
      if (error instanceof HttpError) return sendJson(res, error.status, { error: error.message });
      const message = error instanceof Error && !/[/\\]/.test(error.message) ? error.message : "仪表盘服务暂时不可用";
      return sendJson(res, 500, { error: message });
    }
  }) as DashboardHandler;
  handler.tick = tick;
  handler.settle = settle;
  handler.dispose = () => {
    disposed = true;
    if (timer) clearInterval(timer);
    timer = undefined;
    limiter.clear();
    abort.abort();
  };
  return handler;
}

interface WebServerContext extends Context {
  webServer: {
    register(route: {
      kind: "prefix";
      path: string;
      handler: (req: IncomingMessage, res: ServerResponse) => Promise<void>;
    }): () => void;
  };
}

export function registerDashboard(ctx: Context, runPy: RunPy) {
  // Unit fixtures and headless profiles do not necessarily expose the Web carrier.
  if (typeof ctx.inject !== "function") return;
  const here = dirname(fileURLToPath(import.meta.url));
  // Published/dev bundle: dist/web next to dist/index.js; running dsh/index.ts from source uses the last build.
  const webDir = [join(here, "web"), join(here, "dist", "web")].find(dir => existsSync(join(dir, "app.js"))) ?? join(here, "web");
  const dataRoot = process.env.DSH_HOME || join(homedir(), ".dsh");
  const watchlistFile = process.env.STOCK_WATCHLIST_FILE || join(dataRoot, "stock-analysis", "watchlist.json");
  ctx.inject(["webServer"], nested => {
    const c = nested as WebServerContext;
    c.effect(() => {
      // The warmer lives exactly as long as the route: no Web carrier, no background requests.
      const handler = createDashboardHandler({ runPy, watchlistFile, webDir, warm: true });
      const disposeRoute = c.webServer.register({ kind: "prefix", path: DASHBOARD_BASE, handler });
      return () => {
        handler.dispose();
        disposeRoute();
      };
    });
  });
}
