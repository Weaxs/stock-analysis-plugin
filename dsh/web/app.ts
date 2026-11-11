// Dashboard page logic; esbuild compiles it to dist/web/app.js as an IIFE loaded by index.html.

type Region = "cn" | "hk" | "us" | "jp" | "kr" | "tw";
type Numeric = number | string | null | undefined;
type Direction = "up" | "down" | "flat";
type SortKey = "order" | "price" | "change_pct" | "change" | "turnover" | "trend";

interface Quote {
  symbol?: string;
  name?: string;
  price?: Numeric;
  change?: Numeric;
  change_pct?: Numeric;
  high?: Numeric;
  low?: Numeric;
  prev_close?: Numeric;
  turnover?: Numeric;
  source?: string;
  as_of?: string;
  data_as_of?: string;
  stale?: boolean;
}

interface TrendRow {
  date: string;
  close: Numeric;
}

interface Phase {
  phase?: string;
  phase_label?: string;
  local_time?: string;
  timezone?: string;
  sessions?: unknown;
}

interface Slot<T> {
  value: T | null;
  error: string | null;
  updatedAt: string | null;
  loading: boolean;
}

interface WatchItem {
  symbol: string;
  region: Region;
  quote: Quote | null;
  quoteError: string | null;
  quoteLoading: boolean;
  trend: TrendRow[] | null;
  trendError: string | null;
}

interface IndexRow {
  region: Region;
  code: string;
  quote: Quote | null;
  error: string | null;
  loading: boolean;
  trend: TrendRow[] | null;
}

interface Stats {
  up_count?: Numeric;
  down_count?: Numeric;
  flat_count?: Numeric;
  limit_up_count?: Numeric;
  limit_down_count?: Numeric;
  avg_change_pct?: Numeric;
  median_change_pct?: Numeric;
  total_turnover?: Numeric;
  total_stocks?: Numeric;
}

interface Sector {
  name: string;
  change_pct?: Numeric;
  leading_stock?: string;
  leading_change_pct?: Numeric;
}

interface Snapshot {
  watchlist: WatchItem[];
  ribbon: IndexRow[];
  phases: Partial<Record<Region, Phase | null>>;
  market: { region: Region; indices: IndexRow[] };
  stats: Slot<Stats>;
  sectors: Slot<{ top: Sector[]; bottom: Sector[] }>;
  pending: number;
  fetchedAt: string;
}

const POLL_IDLE = 15000;
const POLL_BUSY = 1500;
const REQUEST_TIMEOUT = 15000;
const CACHE_KEY = "stock-dashboard:v2:snapshot";
const PREF_KEY = "stock-dashboard:v2:prefs";
const CACHE_TTL = 24 * 60 * 60 * 1000;
const REGION_NAMES: Record<Region, string> = { cn: "A股", hk: "港股", us: "美股", jp: "日股", kr: "韩股", tw: "台股" };
const REGIONS = Object.keys(REGION_NAMES) as Region[];
const TRADING = new Set(["morning", "afternoon", "intraday"]);
const INDEX_NAMES: Record<string, string> = {
  sh000001: "上证指数", sz399001: "深证成指", sz399006: "创业板指", sh000688: "科创50", sh000300: "沪深300", sh000016: "上证50",
  "^HSI": "恒生指数", "^HSCE": "国企指数", "^HSTECH": "恒生科技",
  "^DJI": "道琼斯", "^IXIC": "纳斯达克", "^GSPC": "标普500", "^RUT": "罗素2000",
  "^N225": "日经225", "^TOPX": "东证指数", "^KS11": "KOSPI", "^KQ11": "KOSDAQ", "^TWII": "台湾加权",
};
const SVG_NS = "http://www.w3.org/2000/svg";
const SORT_KEYS = new Set<SortKey>(["order", "price", "change_pct", "change", "turnover", "trend"]);

const isRegion = (value: unknown): value is Region => typeof value === "string" && value in REGION_NAMES;

const state = {
  snap: null as Snapshot | null,
  region: "cn" as Region,
  sort: { key: "order" as SortKey, dir: -1 },
  filter: "all" as Region | "all",
  prices: new Map<string, number | null>(),
  pollTimer: undefined as ReturnType<typeof setTimeout> | undefined,
  inflight: false,
  saving: false,
  error: null as string | null,
  lastOk: 0,
  fromCache: false,
};

function $<T extends HTMLElement = HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`missing #${id}`);
  return node as T;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: unknown): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function svgEl<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}

function icon(paths: string[], size = 14): SVGSVGElement {
  const node = svgEl("svg", { viewBox: "0 0 20 20", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "aria-hidden": "true" });
  for (const d of paths) node.append(svgEl("path", { d }));
  return node;
}

// ---------- formatting ----------

function num(value: Numeric): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

const formatters = new Map<number, Intl.NumberFormat>();
function fixed(value: number, digits: number): string {
  let formatter = formatters.get(digits);
  if (!formatter) {
    formatter = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    formatters.set(digits, formatter);
  }
  return formatter.format(value);
}

function priceDigits(symbol: string, region: Region, price: number | null, isIndex: boolean): number {
  if (isIndex) return 2;
  if (region === "cn") return /^(?:sh|sz)?[15]\d{5}$/.test(symbol) ? 3 : 2;
  if (region === "hk") return price !== null && price < 10 ? 3 : 2;
  return 2;
}

function fmtPrice(value: Numeric, digits: number): string {
  const parsed = num(value);
  return parsed === null ? "—" : fixed(parsed, digits);
}

function fmtSigned(value: Numeric, digits: number, suffix = ""): string {
  const parsed = num(value);
  if (parsed === null) return "—";
  return `${parsed > 0 ? "+" : parsed < 0 ? "−" : ""}${fixed(Math.abs(parsed), digits)}${suffix}`;
}

const fmtPct = (value: Numeric) => fmtSigned(value, 2, "%");

function fmtAmount(value: Numeric): string {
  const parsed = num(value);
  if (parsed === null || parsed <= 0) return "—";
  if (parsed >= 1e12) return `${fixed(parsed / 1e12, 2)}万亿`;
  if (parsed >= 1e8) return `${fixed(parsed / 1e8, 2)}亿`;
  if (parsed >= 1e4) return `${fixed(parsed / 1e4, 1)}万`;
  return fixed(parsed, 0);
}

function dir(value: Numeric): Direction {
  const parsed = num(value);
  if (parsed === null) return "flat";
  return parsed > 0 ? "up" : parsed < 0 ? "down" : "flat";
}

function clock(value: string | number, withSeconds = false): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", ...(withSeconds ? { second: "2-digit" } : {}), hour12: false }).format(date);
}

function stamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "");
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

const shortDate = (value: string) => (/^\d{4}-(\d{2})-(\d{2})/.exec(value || "") || []).slice(1).join("/") || value || "";

function periodChange(rows: TrendRow[] | null | undefined): number | null {
  if (!Array.isArray(rows) || rows.length < 2) return null;
  const first = num(rows[0].close);
  const last = num(rows[rows.length - 1].close);
  return first && last ? (last / first - 1) * 100 : null;
}

function skeleton(width: number): HTMLSpanElement {
  const node = el("span", "sk");
  node.style.width = `${width}px`;
  return node;
}

// ---------- sparkline with hover readout ----------

function spark(rows: TrendRow[] | null | undefined, width: number, height: number, digits = 2): SVGSVGElement | null {
  const points = Array.isArray(rows)
    ? rows.map(row => ({ date: row.date, close: num(row.close) })).filter((row): row is { date: string; close: number } => row.close !== null && row.close > 0)
    : [];
  if (points.length < 2) return null;
  const pad = 2;
  const closes = points.map(row => row.close);
  const first = closes[0];
  const last = closes[closes.length - 1];
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const xs = points.map((_, index) => pad + index / (points.length - 1) * (width - pad * 2));
  const ys = closes.map(close => pad + (max - close) / range * (height - pad * 2));
  const line = xs.map((x, index) => `${index ? "L" : "M"}${x.toFixed(1)},${ys[index].toFixed(1)}`).join("");
  const svg = svgEl("svg", { class: `spark ${dir(last - first)}`, width, height, viewBox: `0 0 ${width} ${height}`, role: "img" });
  svg.setAttribute("aria-label", `近 ${points.length} 个交易日走势，区间 ${fmtPct(periodChange(points))}`);
  svg.append(
    svgEl("line", { class: "base", x1: pad, x2: width - pad, y1: ys[0], y2: ys[0] }),
    svgEl("path", { class: "area", d: `${line}L${xs[xs.length - 1].toFixed(1)},${height}L${xs[0].toFixed(1)},${height}Z` }),
    svgEl("path", { class: "line", d: line }),
    svgEl("circle", { class: "end", cx: xs[xs.length - 1], cy: ys[ys.length - 1], r: 2 }),
  );
  let guide: SVGLineElement | null = null;
  let dot: SVGCircleElement | null = null;
  const tip = $("tip");
  svg.addEventListener("pointermove", event => {
    const box = svg.getBoundingClientRect();
    const x = (event.clientX - box.left) / box.width * width;
    let index = 0;
    for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - x) < Math.abs(xs[index] - x)) index = i;
    if (!guide || !dot) {
      guide = svgEl("line", { class: "guide", y1: 0, y2: height });
      dot = svgEl("circle", { class: "hover-dot", r: 2.6 });
      svg.append(guide, dot);
    }
    guide.setAttribute("x1", String(xs[index]));
    guide.setAttribute("x2", String(xs[index]));
    dot.setAttribute("cx", String(xs[index]));
    dot.setAttribute("cy", String(ys[index]));
    const change = (closes[index] / first - 1) * 100;
    tip.replaceChildren(
      document.createTextNode(`${shortDate(points[index].date)}  收 `),
      el("b", "", fixed(closes[index], digits)),
      document.createTextNode("  "),
      el("span", "", `较首日 ${fmtPct(change)}`),
    );
    tip.hidden = false;
    const left = Math.min(window.innerWidth - tip.offsetWidth - 8, Math.max(8, event.clientX - tip.offsetWidth / 2));
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(8, box.top - tip.offsetHeight - 8)}px`;
  });
  svg.addEventListener("pointerleave", () => {
    tip.hidden = true;
    guide?.remove();
    dot?.remove();
    guide = null;
    dot = null;
  });
  return svg;
}

// ---------- storage ----------

function readJson<T>(key: string): T | null {
  try {
    return JSON.parse(localStorage.getItem(key) || "null") as T | null;
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Quota/private mode must not block quotes.
  }
}

function savePrefs() {
  writeJson(PREF_KEY, { region: state.region, sort: state.sort, filter: state.filter });
}

// ---------- API ----------

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  try {
    let response: Response;
    try {
      response = await fetch(path, { ...options, signal: controller.signal });
    } catch (error) {
      throw new Error((error as Error).name === "AbortError" ? "行情服务响应超时" : "无法连接行情服务，请确认 dsh 正在运行");
    }
    let value: unknown;
    try {
      value = await response.json();
    } catch {
      throw new Error("行情服务返回了无法解析的数据");
    }
    if (!response.ok) {
      const message = (value as { error?: unknown } | null)?.error;
      throw new Error(typeof message === "string" ? message : `请求失败（${response.status}）`);
    }
    return value as T;
  } finally {
    clearTimeout(timer);
  }
}

// ---------- rendering ----------

const isTrading = (phase: Phase | null | undefined) => TRADING.has(phase?.phase ?? "");

function renderStatus() {
  const node = $("status");
  node.replaceChildren();
  const snap = state.snap;
  if (state.error) {
    node.append(el("span", "warn", snap ? `更新失败，显示的是 ${clock(state.lastOk || snap.fetchedAt)} 的数据 · ${state.error}` : state.error));
    return;
  }
  if (!snap) {
    node.textContent = "正在连接行情服务…";
    return;
  }
  const open = REGIONS.filter(region => isTrading(snap.phases?.[region]));
  if (open.length) {
    node.append(el("span", "live"), document.createTextNode(`${open.map(region => REGION_NAMES[region]).join("、")}交易中 · `));
  }
  node.append(document.createTextNode(state.fromCache ? "显示上次数据，正在更新…" : `${clock(state.lastOk, true)} 已同步`));
  if (!state.fromCache && snap.pending > 0) node.append(document.createTextNode(` · 后台更新 ${snap.pending} 项`));
  $("refresh").classList.toggle("busy", !state.fromCache && snap.pending > 0);
}

function groupByRegion(list: IndexRow[]): Map<Region, IndexRow[]> {
  const groups = new Map<Region, IndexRow[]>();
  for (const item of list) {
    const group = groups.get(item.region) ?? [];
    group.push(item);
    groups.set(item.region, group);
  }
  return groups;
}

function renderRibbon() {
  const track = $("ribbon");
  track.replaceChildren();
  const snap = state.snap;
  if (!snap) {
    for (let i = 0; i < 6; i++) {
      const tile = el("div", "tile");
      tile.append(skeleton(56), skeleton(60));
      track.append(tile);
    }
    return;
  }
  for (const [region, rows] of groupByRegion(snap.ribbon || [])) {
    const group = el("div", "rgroup");
    const phase = snap.phases?.[region];
    const head = el("div", "rgroup-head");
    head.append(el("span", `dot${isTrading(phase) ? " on" : ""}`), el("span", "", REGION_NAMES[region]), el("span", "phase", phase?.phase_label || ""));
    const tiles = el("div", "rgroup-tiles");
    for (const row of rows) {
      const tile = el("button", "tile");
      tile.type = "button";
      tile.dataset.region = region;
      tile.title = `查看${REGION_NAMES[region]}市场`;
      tile.append(el("span", "tile-name", INDEX_NAMES[row.code] || row.quote?.name || row.code));
      if (row.quote && num(row.quote.price) !== null) {
        tile.append(
          el("span", `tile-price ${dir(row.quote.change_pct)}`, fmtPrice(row.quote.price, 2)),
          el("span", `tile-pct ${dir(row.quote.change_pct)}`, fmtPct(row.quote.change_pct)),
        );
      } else if (row.loading) {
        tile.append(skeleton(64), skeleton(40));
      } else {
        tile.append(el("span", "tile-price flat", "—"));
        tile.title = row.error || "该指数暂时没有报价";
      }
      const chart = spark(row.trend, 114, 22);
      if (chart) tile.append(chart);
      tiles.append(tile);
    }
    group.append(head, tiles);
    track.append(group);
  }
  updateRibbonNav();
}

function updateRibbonNav() {
  const track = $("ribbon");
  const wrap = $("ribbon-wrap");
  const left = track.scrollLeft > 4;
  const right = track.scrollLeft + track.clientWidth < track.scrollWidth - 4;
  wrap.classList.toggle("more-left", left);
  wrap.classList.toggle("more-right", right);
  wrap.querySelector<HTMLButtonElement>(".prev")!.hidden = !left;
  wrap.querySelector<HTMLButtonElement>(".next")!.hidden = !right;
}

function breadthBar(up: number, flat: number, down: number): HTMLSpanElement {
  const total = up + flat + down || 1;
  const bar = el("span", "breadth-bar");
  bar.setAttribute("role", "img");
  bar.setAttribute("aria-label", `上涨 ${up} 家，平盘 ${flat} 家，下跌 ${down} 家`);
  for (const [cls, count] of [["u", up], ["f", flat], ["d", down]] as const) {
    const part = el("span", cls);
    part.style.width = `${count / total * 100}%`;
    bar.append(part);
  }
  return bar;
}

function statsCounts(stats: Stats): [number, number, number] {
  return [num(stats.up_count) ?? 0, num(stats.flat_count) ?? 0, num(stats.down_count) ?? 0];
}

function renderPulse() {
  const node = $("pulse");
  node.replaceChildren();
  const slot = state.snap?.stats;
  const stats = slot?.value;
  node.append(el("span", "label", "A股"));
  if (!slot || !stats) {
    node.append(el("span", "", slot?.error && !slot.loading ? "全市场涨跌统计暂时失败，稍后自动重试" : "正在统计全市场涨跌，首次约需 15–60 秒…"));
    return;
  }
  const [up, flat, down] = statsCounts(stats);
  const breadth = el("span", "breadth");
  const upText = el("span", "up", "涨 ");
  upText.append(el("strong", "up", up));
  const downText = el("span", "down", "跌 ");
  downText.append(el("strong", "down", down));
  breadth.append(upText, breadthBar(up, flat, down), downText);
  const item = (label: string, value: string | number, cls = "") => {
    const span = el("span", "", `${label} `);
    span.append(el("strong", cls, value));
    return span;
  };
  node.append(
    breadth,
    item("涨停", num(stats.limit_up_count) ?? "—", "up"),
    item("跌停", num(stats.limit_down_count) ?? "—", "down"),
    item("中位数", fmtPct(stats.median_change_pct), dir(stats.median_change_pct)),
    item("成交额", fmtAmount(stats.total_turnover)),
  );
  if (slot.updatedAt) node.append(el("span", "", `统计于 ${clock(slot.updatedAt)}`));
}

function renderFilters() {
  const node = $("filters");
  const items = state.snap?.watchlist || [];
  const counts = new Map<Region, number>();
  for (const item of items) counts.set(item.region, (counts.get(item.region) || 0) + 1);
  if (counts.size < 2) {
    node.hidden = true;
    state.filter = "all";
    return;
  }
  if (state.filter !== "all" && !counts.has(state.filter)) state.filter = "all";
  node.hidden = false;
  node.replaceChildren();
  const chips: Array<[Region | "all", string, number]> = [
    ["all", "全部", items.length],
    ...REGIONS.filter(region => counts.has(region)).map((region): [Region, string, number] => [region, REGION_NAMES[region], counts.get(region) ?? 0]),
  ];
  for (const [key, label, count] of chips) {
    const chip = el("button", "chip", `${label} ${count}`);
    chip.type = "button";
    chip.dataset.filter = key;
    chip.setAttribute("aria-pressed", String(state.filter === key));
    node.append(chip);
  }
}

function sortValue(item: WatchItem, key: Exclude<SortKey, "order">): number | null {
  if (key === "trend") return periodChange(item.trend);
  return num(item.quote?.[key]);
}

function visibleItems(): WatchItem[] {
  const items = (state.snap?.watchlist || []).filter(item => state.filter === "all" || item.region === state.filter);
  const { key, dir: direction } = state.sort;
  if (key === "order") return items;
  return items
    .map((item, index) => ({ item, index, value: sortValue(item, key) }))
    .sort((a, b) => {
      if (a.value === null && b.value === null) return a.index - b.index;
      if (a.value === null) return 1;
      if (b.value === null) return -1;
      return (b.value - a.value) * direction || a.index - b.index;
    })
    .map(entry => entry.item);
}

function rangeCell(quote: Quote | null, digits: number): HTMLElement {
  const low = num(quote?.low);
  const high = num(quote?.high);
  const price = num(quote?.price);
  if (!quote || low === null || high === null || price === null || high <= low) return el("span", "amount", "—");
  const wrap = el("div", `range ${dir(quote.change_pct)}`);
  const track = el("div", "range-track");
  const position = (value: number) => `${Math.min(100, Math.max(0, (value - low) / (high - low) * 100))}%`;
  const prev = num(quote.prev_close);
  if (prev !== null && prev >= low && prev <= high) {
    const tick = el("span", "range-prev");
    tick.style.left = position(prev);
    track.append(tick);
  }
  const mark = el("span", "range-mark");
  mark.style.left = position(price);
  track.append(mark);
  const amplitude = prev ? `振幅 ${fixed((high - low) / prev * 100, 2)}%` : `${fixed(low, digits)}–${fixed(high, digits)}`;
  wrap.append(track, el("div", "range-label", amplitude));
  wrap.title = `最低 ${fixed(low, digits)} · 最高 ${fixed(high, digits)}${prev !== null ? ` · 昨收 ${fixed(prev, digits)}` : ""}`;
  return wrap;
}

function emptyRow(): HTMLTableRowElement {
  const row = el("tr");
  const cell = el("td", "empty");
  cell.colSpan = 8;
  cell.append(el("strong", "", "还没有自选股"), el("p", "", "添加 600519、00700.HK 或 AAPL 这样的代码，价格和 30 日走势会在后台持续更新。"));
  const button = el("button", "btn primary", "添加自选");
  button.type = "button";
  button.dataset.action = "open-add";
  cell.append(button);
  row.append(cell);
  return row;
}

function renderRows(flash: Map<string, Direction>) {
  const body = $("rows");
  body.replaceChildren();
  if (!state.snap) {
    for (let i = 0; i < 3; i++) {
      const row = el("tr");
      for (const width of [120, 56, 70, 48, 90, 56, 120, 0]) {
        const cell = el("td");
        if (width) cell.append(skeleton(width));
        row.append(cell);
      }
      body.append(row);
    }
    return;
  }
  if (!state.snap.watchlist.length) {
    body.append(emptyRow());
    return;
  }
  for (const item of visibleItems()) {
    const quote = item.quote;
    const price = num(quote?.price);
    const digits = priceDigits(item.symbol, item.region, price, false);
    const change = dir(quote?.change_pct);
    const row = el("tr");
    row.dataset.symbol = item.symbol;

    const nameCell = el("td", "c-name");
    nameCell.append(el("span", "name", quote?.name || item.symbol));
    const meta = el("div", "meta");
    meta.append(el("span", "code", item.symbol), el("span", "tag", REGION_NAMES[item.region]));
    if (quote?.stale) meta.append(el("span", "stale", "报价可能不是最新"));
    if (item.quoteError) meta.append(el("span", "stale", quote ? "更新失败，显示上次报价" : item.quoteError));
    nameCell.append(meta);
    const quotedAt = quote?.as_of || quote?.data_as_of;
    const evidence = [quote?.source && `来源 ${quote.source}`, quotedAt && `报价 ${stamp(quotedAt)}`].filter(Boolean).join(" · ");
    if (evidence) nameCell.title = evidence;

    const loadingQuote = !quote && item.quoteLoading;
    const cell = (className: string, content: Node) => {
      const td = el("td", className);
      td.append(content);
      return td;
    };
    const priceNode = loadingQuote ? skeleton(56) : el("span", `price ${change}`, fmtPrice(price, digits));
    const flashed = flash.get(item.symbol);
    if (!loadingQuote && flashed) priceNode.classList.add(`flash-${flashed}`);
    const trendCell = el("div", "trend-cell");
    const chart = spark(item.trend, 108, 30, digits);
    if (chart) {
      const period = periodChange(item.trend);
      trendCell.append(chart, el("span", `trend-pct ${dir(period)}`, fmtPct(period)));
    } else if (item.trendError) {
      trendCell.append(el("span", "amount", "暂无走势"));
      trendCell.title = item.trendError;
    } else {
      trendCell.append(skeleton(108));
    }
    const remove = el("button", "remove");
    remove.type = "button";
    remove.dataset.remove = item.symbol;
    remove.setAttribute("aria-label", `从自选中删除 ${quote?.name || item.symbol}`);
    remove.title = "删除";
    remove.append(icon(["M5.5 5.5l9 9", "M14.5 5.5l-9 9"]));

    row.append(
      nameCell,
      cell("c-num", priceNode),
      cell("c-num", loadingQuote ? skeleton(70) : el("span", `pill ${change}`, fmtPct(quote?.change_pct))),
      cell("c-num c-change", loadingQuote ? skeleton(44) : el("span", `change ${change}`, fmtSigned(quote?.change, digits))),
      cell("c-range", loadingQuote ? skeleton(96) : rangeCell(quote, digits)),
      cell("c-num c-amount", loadingQuote ? skeleton(52) : el("span", "amount", fmtAmount(quote?.turnover))),
      cell("c-trend", trendCell),
      cell("c-act", remove),
    );
    body.append(row);
  }
  for (const th of document.querySelectorAll<HTMLTableCellElement>(".quotes th")) {
    const key = th.querySelector<HTMLButtonElement>("button")?.dataset.sort;
    if (key && key !== "order" && key === state.sort.key) th.setAttribute("aria-sort", state.sort.dir > 0 ? "descending" : "ascending");
    else th.removeAttribute("aria-sort");
  }
}

function renderTabs() {
  const node = $("market-tabs");
  node.replaceChildren();
  for (const region of REGIONS) {
    const tab = el("button", "tab");
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.dataset.region = region;
    const selected = region === state.region;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    const phase = state.snap?.phases?.[region];
    tab.append(el("span", `dot${isTrading(phase) ? " on" : ""}`), document.createTextNode(REGION_NAMES[region]));
    if (phase?.phase_label) tab.title = phase.phase_label;
    node.append(tab);
  }
}

function panel(title: string, note: string): HTMLDivElement {
  const node = el("div", "panel");
  const head = el("div", "panel-head");
  head.append(el("h3", "", title));
  if (note) head.append(el("span", "", note));
  node.append(head);
  return node;
}

function phaseNote(phase: Phase | null | undefined): string {
  if (!phase) return "";
  return [phase.phase_label, phase.local_time && `当地 ${phase.local_time.slice(11, 16)}`].filter(Boolean).join(" · ");
}

function indexPanel(market: Snapshot["market"], phase: Phase | null | undefined): HTMLDivElement {
  const node = panel("指数", phaseNote(phase));
  const table = el("table", "idx");
  const body = el("tbody");
  for (const row of market.indices) {
    const tr = el("tr");
    const name = el("td");
    name.append(el("span", "iname", INDEX_NAMES[row.code] || row.quote?.name || row.code), el("span", "icode", row.code));
    const price = el("td", `iprice ${dir(row.quote?.change_pct)}`);
    const pct = el("td", `ipct ${dir(row.quote?.change_pct)}`);
    if (row.quote && num(row.quote.price) !== null) {
      price.textContent = fmtPrice(row.quote.price, 2);
      pct.textContent = `${fmtSigned(row.quote.change, 2)}  ${fmtPct(row.quote.change_pct)}`;
    } else if (row.loading) {
      price.append(skeleton(64));
      pct.append(skeleton(80));
    } else {
      price.textContent = "—";
      pct.textContent = row.error || "暂不可用";
      pct.className = "ipct flat";
    }
    const chartCell = el("td", "ispark");
    const chart = spark(row.trend, 110, 26);
    if (chart) chartCell.append(chart);
    tr.append(name, price, pct, chartCell);
    body.append(tr);
  }
  table.append(body);
  node.append(table);
  return node;
}

function statsPanel(slot: Slot<Stats> | undefined): HTMLDivElement {
  const stats = slot?.value;
  const node = panel("涨跌分布", slot?.updatedAt ? `统计于 ${clock(slot.updatedAt)}` : "");
  if (!stats) {
    node.append(el("p", "panel-note", slot?.error && !slot.loading ? slot.error : "正在统计全市场涨跌…"));
    return node;
  }
  const [up, flat, down] = statsCounts(stats);
  const wide = el("div", "breadth-wide");
  const legend = el("div", "breadth-legend");
  legend.append(el("span", "up", `上涨 ${up}`), el("span", "flat", `平盘 ${flat}`), el("span", "down", `下跌 ${down}`));
  wide.append(breadthBar(up, flat, down), legend);
  const list = el("dl", "stats");
  const rows: Array<[string, string | number, string]> = [
    ["涨停", num(stats.limit_up_count) ?? "—", "up"],
    ["跌停", num(stats.limit_down_count) ?? "—", "down"],
    ["平均涨幅", fmtPct(stats.avg_change_pct), dir(stats.avg_change_pct)],
    ["涨幅中位数", fmtPct(stats.median_change_pct), dir(stats.median_change_pct)],
    ["总成交额", fmtAmount(stats.total_turnover), ""],
    ["统计股票", num(stats.total_stocks) ?? up + flat + down, ""],
  ];
  for (const [label, value, cls] of rows) {
    const item = el("div");
    item.append(el("dt", "", label), el("dd", cls, value));
    list.append(item);
  }
  node.append(wide, list);
  return node;
}

function sectorsPanel(slot: Snapshot["sectors"] | undefined): HTMLDivElement {
  const value = slot?.value;
  const node = panel("行业", slot?.updatedAt ? `更新于 ${clock(slot.updatedAt)}` : "");
  if (!value) {
    node.append(el("p", "panel-note", slot?.error && !slot.loading ? slot.error : "正在读取行业排行…"));
    return node;
  }
  const grid = el("div", "sectors");
  const scale = Math.max(...[...value.top, ...value.bottom].map(row => Math.abs(num(row.change_pct) ?? 0)), 1);
  for (const [title, rows] of [["领涨", value.top], ["领跌", value.bottom]] as const) {
    const column = el("div");
    column.append(el("h4", "", title));
    for (const row of rows) {
      const item = el("div", `sector ${dir(row.change_pct)}`);
      const bar = el("div", "sector-bar");
      const fill = el("span");
      fill.style.width = `${Math.abs(num(row.change_pct) ?? 0) / scale * 100}%`;
      bar.append(fill);
      item.append(el("span", "sector-name", row.name), el("span", "sector-pct", fmtPct(row.change_pct)), bar);
      if (row.leading_stock) item.append(el("span", "sector-lead", `${row.leading_stock} ${fmtPct(row.leading_change_pct)}`));
      column.append(item);
    }
    grid.append(column);
  }
  node.append(grid);
  return node;
}

function sessionPanel(region: Region, phase: Phase | null | undefined): HTMLDivElement {
  const node = panel("交易时段", "");
  const body = el("div", "session");
  const list = el("dl");
  const sessions = Array.isArray(phase?.sessions)
    ? phase.sessions.map(item => (Array.isArray(item) ? item.join("–") : "")).filter(Boolean).join(" / ")
    : "";
  const facts: Array<[string, string | undefined]> = [
    ["状态", phase?.phase_label],
    ["当地时间", phase?.local_time?.replace("T", " ").slice(0, 16)],
    ["时区", phase?.timezone],
    ["时段", sessions],
  ];
  for (const [label, value] of facts) {
    if (!value) continue;
    list.append(el("dt", "", label), el("dd", "", value));
  }
  body.append(list, el("p", "", `全市场涨跌分布与行业排行目前只有 A 股数据，${REGION_NAMES[region]}暂不提供，不用指数涨跌代替。`));
  node.append(body);
  return node;
}

function renderMarket() {
  renderTabs();
  const body = $("market-body");
  body.replaceChildren();
  const snap = state.snap;
  const market = snap?.market;
  if (!snap || !market || market.region !== state.region) {
    const loading = panel("指数", "加载中…");
    loading.append(el("p", "panel-note", "正在读取该市场的指数…"));
    body.append(loading);
    return;
  }
  const phase = snap.phases?.[state.region];
  body.append(indexPanel(market, phase));
  const side = el("div", "side");
  if (state.region === "cn") side.append(statsPanel(snap.stats), sectorsPanel(snap.sectors));
  else side.append(sessionPanel(state.region, phase));
  body.append(side);
}

function render(flash = new Map<string, Direction>()) {
  $("watch-count").textContent = state.snap ? `${state.snap.watchlist.length} / 30` : "";
  renderStatus();
  renderRibbon();
  renderPulse();
  renderFilters();
  renderRows(flash);
  renderMarket();
}

function applySnapshot(snap: Snapshot, fromCache = false) {
  const flash = new Map<string, Direction>();
  if (!fromCache && !state.fromCache) {
    for (const item of snap.watchlist) {
      const price = num(item.quote?.price);
      const previous = state.prices.get(item.symbol);
      if (price !== null && previous !== undefined && previous !== null && price !== previous) flash.set(item.symbol, price > previous ? "up" : "down");
    }
  }
  state.prices = new Map(snap.watchlist.map(item => [item.symbol, num(item.quote?.price)]));
  state.snap = snap;
  state.fromCache = fromCache;
  render(flash);
}

// ---------- polling ----------

function schedule() {
  clearTimeout(state.pollTimer);
  if (document.visibilityState !== "visible") return;
  state.pollTimer = setTimeout(poll, (state.snap?.pending ?? 0) > 0 ? POLL_BUSY : POLL_IDLE);
}

async function poll(): Promise<void> {
  clearTimeout(state.pollTimer);
  if (state.inflight) return;
  state.inflight = true;
  const region = state.region;
  try {
    const snap = await api<Snapshot>(`./api/snapshot?region=${encodeURIComponent(region)}`);
    state.error = null;
    state.lastOk = Date.now();
    if (region === state.region) {
      applySnapshot(snap);
      writeJson(CACHE_KEY, { savedAt: Date.now(), snap });
    }
  } catch (error) {
    state.error = (error as Error).message;
    renderStatus();
  } finally {
    state.inflight = false;
    if (region !== state.region) void poll();
    else schedule();
  }
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

// ---------- interactions ----------

function setAddForm(open: boolean) {
  $("add-form").hidden = !open;
  $("add-toggle").setAttribute("aria-expanded", String(open));
  if (open) $<HTMLInputElement>("symbol-input").focus();
}

async function changeWatchlist(op: "add" | "remove", symbols: string[]) {
  if (state.saving) return;
  state.saving = true;
  const message = $("add-message");
  message.className = "add-message";
  message.textContent = op === "add" ? "正在保存…" : "";
  try {
    await post("./api/watchlist", { op, symbols });
    if (op === "add") {
      $<HTMLInputElement>("symbol-input").value = "";
      message.textContent = "";
      setAddForm(false);
    }
    await poll();
  } catch (error) {
    const reason = (error as Error).message;
    if (op === "add") {
      message.className = "add-message error";
      message.textContent = reason;
    } else {
      const notice = $("watch-error");
      notice.textContent = `删除失败：${reason}`;
      notice.hidden = false;
      setTimeout(() => {
        notice.hidden = true;
      }, 6000);
    }
  } finally {
    state.saving = false;
  }
}

function selectRegion(region: Region, focus = false) {
  if (region === state.region) return;
  state.region = region;
  savePrefs();
  renderMarket();
  if (focus) document.querySelector<HTMLButtonElement>(`.tab[data-region="${region}"]`)?.focus();
  void poll();
}

const closest = <T extends Element>(event: Event, selector: string) => (event.target as Element | null)?.closest<T>(selector) ?? null;

$("add-toggle").addEventListener("click", () => setAddForm($("add-form").hidden));
$("add-cancel").addEventListener("click", () => setAddForm(false));
$("add-form").addEventListener("submit", event => {
  event.preventDefault();
  const symbols = $<HTMLInputElement>("symbol-input").value.split(/[\s,，、;；]+/).map(value => value.trim()).filter(Boolean);
  if (symbols.length) void changeWatchlist("add", symbols);
});
$("symbol-input").addEventListener("keydown", event => {
  if (event.key === "Escape") setAddForm(false);
});
$("refresh").addEventListener("click", async () => {
  $("refresh").classList.add("busy");
  try {
    await post("./api/refresh");
    state.error = null;
  } catch (error) {
    state.error = (error as Error).message;
  }
  void poll();
});
$("rows").addEventListener("click", event => {
  const remove = closest<HTMLButtonElement>(event, "button[data-remove]");
  if (remove?.dataset.remove) {
    void changeWatchlist("remove", [remove.dataset.remove]);
    return;
  }
  if (closest(event, "[data-action='open-add']")) setAddForm(true);
});
$("filters").addEventListener("click", event => {
  const chip = closest<HTMLButtonElement>(event, "button[data-filter]");
  const filter = chip?.dataset.filter;
  if (!filter || (filter !== "all" && !isRegion(filter))) return;
  state.filter = filter;
  savePrefs();
  renderFilters();
  renderRows(new Map());
});
document.querySelector(".quotes thead")?.addEventListener("click", event => {
  const key = closest<HTMLButtonElement>(event, "button[data-sort]")?.dataset.sort as SortKey | undefined;
  if (!key || !SORT_KEYS.has(key)) return;
  // Each column cycles: descending → ascending → saved order.
  if (key === "order" || (state.sort.key === key && state.sort.dir < 0)) state.sort = { key: "order", dir: -1 };
  else if (state.sort.key === key) state.sort = { key, dir: -1 };
  else state.sort = { key, dir: 1 };
  savePrefs();
  renderRows(new Map());
});
$("ribbon").addEventListener("scroll", updateRibbonNav, { passive: true });
window.addEventListener("resize", updateRibbonNav);
$("ribbon-wrap").addEventListener("click", event => {
  const nav = closest<HTMLButtonElement>(event, ".ribbon-nav");
  if (nav) $("ribbon").scrollBy({ left: Number(nav.dataset.step) * $("ribbon").clientWidth * 0.8, behavior: "smooth" });
});
$("ribbon").addEventListener("click", event => {
  const region = closest<HTMLButtonElement>(event, ".tile[data-region]")?.dataset.region;
  if (!isRegion(region)) return;
  selectRegion(region);
  document.querySelector(".market")?.scrollIntoView({ behavior: "smooth", block: "start" });
});
$("market-tabs").addEventListener("click", event => {
  const region = closest<HTMLButtonElement>(event, ".tab[data-region]")?.dataset.region;
  if (isRegion(region)) selectRegion(region);
});
$("market-tabs").addEventListener("keydown", event => {
  const index = REGIONS.indexOf(state.region);
  const next = ({ ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: REGIONS.length - 1 } as Record<string, number>)[event.key];
  if (next === undefined) return;
  event.preventDefault();
  selectRegion(REGIONS[(next + REGIONS.length) % REGIONS.length], true);
});

// Host theme: the parent dsh shell posts its scheme and palette tokens.
const initialTheme = new URLSearchParams(location.search).get("theme");
if (initialTheme === "light" || initialTheme === "dark") document.documentElement.dataset.theme = initialTheme;
window.addEventListener("message", (event: MessageEvent<{ type?: string; scheme?: string; palette?: Record<string, unknown> }>) => {
  if (event.origin !== location.origin || event.source !== window.parent) return;
  const message = event.data;
  if (!message || message.type !== "stock-analysis:theme" || (message.scheme !== "light" && message.scheme !== "dark")) return;
  document.documentElement.dataset.theme = message.scheme;
  for (const key of ["bg", "surface", "surfaceSubtle", "text", "muted", "border", "accent", "onAccent"]) {
    const value = message.palette?.[key];
    const property = key.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`);
    if (typeof value === "string" && value.length < 300) document.documentElement.style.setProperty(`--${property}`, value);
  }
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") void poll();
  else clearTimeout(state.pollTimer);
});

const prefs = readJson<{ region?: unknown; sort?: { key?: unknown; dir?: unknown }; filter?: unknown }>(PREF_KEY);
if (prefs) {
  if (isRegion(prefs.region)) state.region = prefs.region;
  if (SORT_KEYS.has(prefs.sort?.key as SortKey) && (prefs.sort?.dir === 1 || prefs.sort?.dir === -1)) state.sort = { key: prefs.sort.key as SortKey, dir: prefs.sort.dir };
  if (prefs.filter === "all" || isRegion(prefs.filter)) state.filter = prefs.filter;
}
const cached = readJson<{ savedAt: number; snap: Snapshot }>(CACHE_KEY);
if (cached?.snap && Date.now() - cached.savedAt < CACHE_TTL && cached.snap.market?.region === state.region) applySnapshot(cached.snap, true);
else render();
void poll();

export {};
