import { existsSync, readdirSync, readFileSync } from "node:fs";
import * as cp from "node:child_process";
import { promisify } from "node:util";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Context } from "@deepseek-ai/cordis";
import {
  defineTool,
  type InferArgs,
  type ParameterSchemaSpec,
} from "@deepseek-ai/dsh-tools";
// Type-only import: pulls in the `Context.skills` service augmentation.
import type {} from "@deepseek-ai/dsh-skill";
import { venvPythonPath } from "../scripts/venv-python.mjs";

// Indirected so tests can replace the executor without touching node:child_process.
type Executor = (bin: string, argv: string[]) => Promise<string>;

const defaultExecutor: Executor = async (bin, argv) => {
  const exec = promisify(cp.execFile);
  const { stdout } = await exec(bin, argv, { maxBuffer: 32 * 1024 * 1024 });
  return stdout;
};

let executor: Executor = defaultExecutor;
export function __setExecutor(fn: Executor) {
  executor = fn;
}

const here = dirname(fileURLToPath(import.meta.url));
// Published form: tools/ ships next to the package root (dist/../tools).
// Dev form: tools/ is at the repo root.
const toolsDir = existsSync(join(here, "tools"))
  ? join(here, "tools")
  : join(here, "..", "tools");
const pkgRoot = dirname(toolsDir);
const isWin = process.platform === "win32";

function pythonBin(): string {
  // Staged-payload layout: the venv may still live at the repo root one level up.
  for (const root of [pkgRoot, join(pkgRoot, "..")]) {
    const candidate = venvPythonPath(root);
    if (existsSync(candidate)) return candidate;
  }
  // On Windows "python3" is usually the Microsoft Store stub — prefer "python".
  return isWin ? "python" : "python3";
}

async function runPy(script: string, args: string[]): Promise<string> {
  return executor(pythonBin(), [join(toolsDir, script), ...args]);
}

// One canonical stdout contract shared by every tool: execute returns the
// CLI's raw JSON text (same relay shape as the pi/openclaw adapters), render
// relays it to the model as a single text block.
function pyTool<const S extends ParameterSchemaSpec>(opts: {
  name: string;
  description: string;
  parameters: S;
  script: string;
  argv: (args: InferArgs<S>) => string[];
}) {
  return defineTool({
    name: opts.name,
    description: opts.description,
    parameters: opts.parameters,
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }],
    },
    async execute(args) {
      return runPy(opts.script, opts.argv(args));
    },
  });
}

// --- Skills ---------------------------------------------------------------

// Published form: skills/ at the package root. Dev form: skills/ at repo root.
const skillsDir = existsSync(join(here, "skills"))
  ? join(here, "skills")
  : join(here, "..", "skills");

// Minimal single-line `key: value` read from the leading YAML frontmatter.
function frontmatterValue(raw: string, key: string): string | undefined {
  const block = raw.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  const line = block?.[1].match(new RegExp(`^${key}:\\s*(.+)$`, "m"));
  return line?.[1].trim();
}

// Hermes-style dynamic scan: every skills/<name>/SKILL.md becomes a registered
// skill, so adding a skill directory needs no adapter edit.
function registerSkills(ctx: Context) {
  if (!existsSync(skillsDir)) return;
  for (const child of readdirSync(skillsDir, { withFileTypes: true })) {
    if (!child.isDirectory()) continue;
    const skillFile = join(skillsDir, child.name, "SKILL.md");
    if (!existsSync(skillFile)) continue;
    const raw = readFileSync(skillFile, "utf-8");
    ctx.skills.register({
      name: child.name,
      description: frontmatterValue(raw, "description") ?? child.name,
      // The registry-facing body drops the frontmatter (metadata is carried by
      // the explicit name/description fields above).
      content: raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "").trimStart(),
      source: "bundled",
      path: skillFile,
      resourceBase: { kind: "directory", path: join(skillsDir, child.name) },
    });
  }
}

// --- Plugin entry (cordis function-plugin form) ----------------------------

export const name = "stock-analysis";
export const inject = ["tools", "skills"];

export function apply(ctx: Context) {
  // --- Data Tools ---

  ctx.tools.register(
    pyTool({
      name: "get_kline",
      description:
        "获取股票K线数据（OHLCV）。支持A股（如600519）、港股（如00700.HK）、美股（如AAPL）、日股（如7203.T）、韩股（如005930.KS）、台股（如2330.TW）及A股ETF。需要原始历史价格自行计算或画图时用；只问指标用 get_technical_analysis，只问现价用 get_quote",
      parameters: {
        symbol: {
          type: "string",
          required: true,
          description:
            "股票代码，如 600519（A股）、00700.HK（港股）、AAPL（美股）、7203.T（日股）、005930.KS（韩股）、2330.TW（台股）",
        },
        period: {
          type: "string",
          enum: ["daily", "weekly", "monthly"],
          description: "K线周期，默认 daily",
        },
        count: { type: "number", description: "返回数据条数，默认 60" },
      },
      script: "stock_data.py",
      argv: (p) => [
        "kline",
        p.symbol,
        "--period",
        p.period ?? "daily",
        "--count",
        String(p.count ?? 60),
      ],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_quote",
      description: "获取股票实时行情报价（现价、涨跌幅、量比等）。支持A股、港股、美股、日股、韩股、台股。问“现在多少钱/涨了多少”用本工具",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
      },
      script: "stock_data.py",
      argv: (p) => ["quote", p.symbol],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_capital_flow",
      description:
        "获取A股资金流向（主力/超大单/大单/中单/小单净流入）。detail=个股每日明细，summary=多日汇总+趋势，sector_flow=板块资金流排行。问“主力在买还是卖/资金流入流出”用本工具，可配合 get_chip_distribution 验证",
      parameters: {
        symbol: {
          type: "string",
          description: "A股股票代码（detail/summary模式必填），如 600519",
        },
        mode: {
          type: "string",
          enum: ["detail", "summary", "sector_flow"],
          description: "模式：detail=每日明细（默认），summary=多日汇总，sector_flow=板块排行",
        },
      },
      script: "stock_data.py",
      argv: (p) => {
        const args = ["capital_flow"];
        if (p.symbol) args.push(p.symbol);
        args.push("--mode", p.mode ?? "detail");
        return args;
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_news",
      description: "获取个股最近N天财经新闻快讯（轻量、无需配置）。需要深度全网情报用 search_comprehensive_intel，按主题搜索用 search_stock_news，读文章全文用 extract_article",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
        days: { type: "number", description: "获取最近几天的新闻，默认 3" },
      },
      script: "stock_data.py",
      argv: (p) => ["news", p.symbol, "--days", String(p.days ?? 3)],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_financials",
      description: "获取股票关键财务指标（PE/PB/市值/营收/净利润/ROE等），适合快速估值快查。A股深度基本面（成长性/盈利能力/分红）用 get_fundamental_context",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
      },
      script: "stock_data.py",
      argv: (p) => ["financials", p.symbol],
    })
  );

  // --- Analysis Tools ---

  ctx.tools.register(
    pyTool({
      name: "get_technical_analysis",
      description:
        "获取股票技术面分析（MA/MACD/RSI/BOLL/KDJ/成交量等指标 + 100分综合评分 + 6级买卖信号 + 趋势/偏离度/支撑压力位）。“能买吗/现在能入场吗/技术面怎么样”首选本工具；只要均线数值或自定义周期用 calculate_ma，专问量价用 get_volume_analysis，扫当日异动用 detect_anomaly",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
        period: {
          type: "string",
          enum: ["daily", "weekly", "monthly"],
          description: "分析周期，默认 daily",
        },
        count: { type: "number", description: "用于计算指标的K线条数，默认 120" },
      },
      script: "technical.py",
      argv: (p) => [
        "analyze",
        p.symbol,
        "--period",
        p.period ?? "daily",
        "--count",
        String(p.count ?? 120),
      ],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "analyze_pattern",
      description:
        "K线形态识别 — 检测十字星、锤子线、吞没、启明星、黄昏星、双底、20日突破等12+种经典形态。问“出现了什么形态”时用；综合技术面判断用 get_technical_analysis",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
        period: {
          type: "string",
          enum: ["daily", "weekly", "monthly"],
          description: "K线周期，默认 daily",
        },
        days: { type: "number", description: "分析的K线天数，默认 60" },
      },
      script: "pattern.py",
      argv: (p) => [
        "analyze",
        p.symbol,
        "--period",
        p.period ?? "daily",
        "--days",
        String(p.days ?? 60),
      ],
    })
  );

  // --- Market Tools ---

  ctx.tools.register(
    pyTool({
      name: "get_market_indices",
      description:
        "获取主要市场指数行情。CN: 上证/深证/创业板/科创50/沪深300；HK: 恒生/国企/科技；US: 道琼斯/纳斯达克/标普500；JP: 日经225/东证；KR: KOSPI/KOSDAQ；TW: 台湾加权。“复盘今天大盘”用 get_market_review 一站式获取",
      parameters: {
        region: {
          type: "string",
          enum: ["cn", "hk", "us", "jp", "kr", "tw"],
          description: "市场区域，默认 cn",
        },
      },
      script: "stock_data.py",
      argv: (p) => ["market_indices", "--region", p.region ?? "cn"],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_sector_rankings",
      description:
        "获取A股行业板块涨跌幅排行（含领涨股、涨跌家数等）。支持查看涨幅榜/跌幅榜/双向。找热点板块/领涨板块用本工具",
      parameters: {
        top: { type: "number", description: "返回排名前N的板块，默认 10" },
        direction: {
          type: "string",
          enum: ["top", "bottom", "both"],
          description: "top=涨幅榜（默认），bottom=跌幅榜，both=双向",
        },
      },
      script: "stock_data.py",
      argv: (p) => [
        "sector_rankings",
        "--top",
        String(p.top ?? 10),
        "--direction",
        p.direction ?? "top",
      ],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_stock_info",
      description:
        "获取股票基本信息（行业、板块、上市日期、总股本等）。A股返回板块/行业，其他市场返回行业/公司简介",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
      },
      script: "stock_data.py",
      argv: (p) => ["stock_info", p.symbol],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_chip_distribution",
      description:
        "获取A股筹码分布数据（获利比例、平均成本、90%/70%成本集中度）。仅支持A股。与 get_capital_flow 配合验证主力行为",
      parameters: {
        symbol: {
          type: "string",
          required: true,
          description: "A股股票代码，如 600519",
        },
      },
      script: "stock_data.py",
      argv: (p) => ["chip_distribution", p.symbol],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_market_stats",
      description:
        "获取A股市场整体统计（涨跌家数、涨停跌停数、平均涨幅、涨跌Top5、总成交额）。问“今天市场情绪/温度如何”用本工具",
      parameters: {
        market: {
          type: "string",
          enum: ["A"],
          description: "市场，目前仅支持 A",
        },
      },
      script: "stock_data.py",
      argv: (p) => ["market_stats", "--market", p.market ?? "A"],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_fundamental_context",
      description:
        "获取A股深度基本面（估值PE/PB/PS + 成长性营收/净利增速 + 盈利能力ROE/毛利率 + 分红历史）。“公司质地如何/能不能长期持有”用本工具；快速查PE/PB用 get_financials",
      parameters: {
        symbol: {
          type: "string",
          required: true,
          description: "A股股票代码，如 600519",
        },
      },
      script: "stock_data.py",
      argv: (p) => ["fundamental_context", p.symbol],
    })
  );

  // --- Screener & Backtest ---

  ctx.tools.register(
    pyTool({
      name: "screen_stocks",
      description:
        "全市场股票筛选（AlphaSift L1 多因子硬筛）。按PE/PB/市值/换手率/涨跌幅/量比等因子过滤和评分。“帮我选股/筛选低估值/高换手股票”用本工具",
      parameters: {
        market: {
          type: "string",
          enum: ["A", "HK", "US"],
          description: "市场，默认 A",
        },
        top: { type: "number", description: "返回排名前N的股票，默认 20" },
        config: { type: "string", description: "自定义筛选配置YAML文件路径（可选）" },
      },
      script: "screener.py",
      argv: (p) => {
        const args = ["screen", "--market", p.market ?? "A", "--top", String(p.top ?? 20)];
        if (p.config) args.push("--config", p.config);
        return args;
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "run_backtest",
      description:
        "策略回测（AlphaEvo）。读取YAML策略定义，在历史数据上模拟交易，输出收益率/回撤/胜率等指标。只想知道单个技术信号的历史胜率用更轻量的 evaluate_signal",
      parameters: {
        strategy: { type: "string", required: true, description: "策略YAML文件路径" },
        symbol: { type: "string", required: true, description: "股票代码" },
        start: { type: "string", description: "回测起始日期，格式 YYYY-MM-DD" },
        end: { type: "string", description: "回测结束日期，格式 YYYY-MM-DD" },
        capital: { type: "number", description: "初始资金，默认 1000000" },
      },
      script: "backtest.py",
      argv: (p) => {
        const args = ["run", p.strategy, p.symbol];
        if (p.start) args.push("--start", p.start);
        if (p.end) args.push("--end", p.end);
        args.push("--capital", String(p.capital ?? 1000000));
        return args;
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "evaluate_signal",
      description:
        "技术信号历史准确率评估 — 回溯历史数据，统计某个技术信号触发后N日的胜率和平均收益。支持9种信号：macd_golden_cross/macd_death_cross/rsi_oversold/rsi_overbought/breakout_20d/breakdown_20d/volume_surge/ma_golden_cross/ma_death_cross。“金叉/突破策略靠不靠谱”用本工具；要完整模拟交易过程用 run_backtest",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
        signal: {
          type: "string",
          required: true,
          enum: [
            "macd_golden_cross",
            "macd_death_cross",
            "rsi_oversold",
            "rsi_overbought",
            "breakout_20d",
            "breakdown_20d",
            "volume_surge",
            "ma_golden_cross",
            "ma_death_cross",
          ],
          description: "信号名称",
        },
        forward_days: {
          type: "string",
          description: "逗号分隔的前瞻天数，默认 3,5,10",
        },
        lookback: { type: "number", description: "回溯K线条数，默认 250（约1年）" },
      },
      script: "backtest.py",
      argv: (p) => [
        "evaluate_signal",
        p.symbol,
        p.signal,
        "--forward",
        p.forward_days ?? "3,5,10",
        "--lookback",
        String(p.lookback ?? 250),
      ],
    })
  );

  // --- Name Resolution ---

  ctx.tools.register(
    pyTool({
      name: "resolve_stock_name",
      description:
        "股票名称智能解析 — 输入中文名（贵州茅台）、拼音（guizhou maotai/gzmt）、部分代码，返回匹配的股票代码。仅支持A股。用户给出中文名/拼音/部分代码时先调本工具换成代码再调其他工具",
      parameters: {
        query: {
          type: "string",
          required: true,
          description: "股票名称、拼音、拼音首字母或部分代码",
        },
        top: { type: "number", description: "返回匹配数量，默认 5" },
      },
      script: "name_resolver.py",
      argv: (p) => ["resolve", p.query, "--top", String(p.top ?? 5)],
    })
  );

  // --- Trading Calendar ---

  ctx.tools.register(
    pyTool({
      name: "check_trading_day",
      description:
        "查询某日是否为交易日。支持CN（A股）、HK（港股）、US（美股）、JP（日股）、KR（韩股）、TW（台股）",
      parameters: {
        market: {
          type: "string",
          required: true,
          enum: ["CN", "HK", "US", "JP", "KR", "TW"],
          description: "市场",
        },
        date: { type: "string", description: "日期（YYYY-MM-DD），不填则查今天" },
      },
      script: "trading_calendar.py",
      argv: (p) => {
        const args = ["check", p.market];
        if (p.date) args.push("--date", p.date);
        return args;
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_trading_days",
      description: "获取最近/未来N个交易日列表。支持CN/HK/US/JP/KR/TW",
      parameters: {
        market: {
          type: "string",
          required: true,
          enum: ["CN", "HK", "US", "JP", "KR", "TW"],
          description: "市场",
        },
        direction: {
          type: "string",
          enum: ["next", "prev"],
          description: "next=未来交易日, prev=过去交易日",
        },
        count: { type: "number", description: "返回天数，默认 5" },
        date: { type: "string", description: "起始日期（YYYY-MM-DD），默认今天" },
      },
      script: "trading_calendar.py",
      argv: (p) => {
        const args = [p.direction ?? "next", p.market, "--count", String(p.count ?? 5)];
        if (p.date) args.push("--date", p.date);
        return args;
      },
    })
  );

  // --- Standalone MA Calculator ---

  ctx.tools.register(
    pyTool({
      name: "calculate_ma",
      description:
        "独立均线计算器 — 支持任意周期MA（5/10/20/30/60/120/250或自定义）+ 偏离度 + 均线排列 + 金叉死叉检测。只要均线数值或需要30/120/250等非默认周期时用本工具；综合技术面分析用 get_technical_analysis",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
        periods: {
          type: "string",
          description: "逗号分隔的MA周期列表，如 5,10,20,60,120,250",
        },
        kline_period: {
          type: "string",
          enum: ["daily", "weekly", "monthly"],
          description: "K线周期，默认 daily",
        },
      },
      script: "technical.py",
      argv: (p) => [
        "calculate_ma",
        p.symbol,
        "--periods",
        p.periods ?? "5,10,20,30,60,120,250",
        "--period",
        p.kline_period ?? "daily",
        "--count",
        "300",
      ],
    })
  );

  // --- Volume-Price Analysis ---

  ctx.tools.register(
    pyTool({
      name: "get_volume_analysis",
      description:
        "独立量价分析 — 量价相关性、上涨/下跌日成交量对比、量能趋势、量价模式解读（放量上涨/缩量回调等）。专问量能/量价配合时用本工具；综合技术面判断用 get_technical_analysis，扫当日异动用 detect_anomaly",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
        period: {
          type: "string",
          enum: ["daily", "weekly", "monthly"],
          description: "K线周期，默认 daily",
        },
        count: { type: "number", description: "分析K线条数，默认 60" },
      },
      script: "volume_analysis.py",
      argv: (p) => [
        "analyze",
        p.symbol,
        "--period",
        p.period ?? "daily",
        "--count",
        String(p.count ?? 60),
      ],
    })
  );

  // --- Search Intelligence ---

  ctx.tools.register(
    pyTool({
      name: "search_stock_news",
      description:
        "多引擎股票新闻搜索（支持 Tavily/Brave/SerpAPI）。需配置对应 API Key 环境变量。get_news 快讯不够或要按主题搜索时用本工具；深度6维情报用 search_comprehensive_intel",
      parameters: {
        query: { type: "string", required: true, description: "搜索关键词" },
        count: { type: "number", description: "返回结果数量，默认 10" },
      },
      script: "search_intel.py",
      argv: (p) => ["search", p.query, "--count", String(p.count ?? 10)],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "search_comprehensive_intel",
      description:
        "股票综合情报搜索 — 从6个维度（新闻/公告/行情分析/风险/业绩/行业）搜索综合信息。“深入研究/全面调研这家公司”用本工具；快速看新闻用 get_news",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
        name: { type: "string", description: "股票名称（可选，提升搜索准确度）" },
      },
      script: "search_intel.py",
      argv: (p) => {
        const args = ["comprehensive", p.symbol];
        if (p.name) args.push("--name", p.name);
        return args;
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_social_sentiment",
      description:
        "获取股票社交媒体情绪数据。A股：东方财富股吧热度+雪球讨论热度（无需配置）；美股/港股：Reddit/X/Polymarket情绪（需 SENTIMENT_API_KEY）。自动按市场选数据源。“散户在讨论什么/情绪如何”用本工具；看全市场热门讨论用 get_trending_sentiment",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码（如 AAPL）" },
      },
      script: "search_intel.py",
      argv: (p) => ["sentiment", p.symbol],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_trending_sentiment",
      description:
        "获取社交媒体热门趋势（Reddit/X/Polymarket热门股票讨论）。数据缓存10分钟。“现在市场热点是什么/大家都在买什么”用本工具；查个股情绪用 get_social_sentiment",
      parameters: {},
      script: "search_intel.py",
      argv: () => ["trending"],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "extract_article",
      description:
        "网页文章全文提取 — 输入URL，提取文章标题、正文（最多3000字）、作者、发布日期等。配合 get_news/search_stock_news 的搜索结果做深度阅读",
      parameters: {
        url: { type: "string", required: true, description: "文章URL" },
      },
      script: "search_intel.py",
      argv: (p) => ["extract", p.url],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "screen_risk",
      description:
        "风险专项筛查 — 7维度风险检测（估值极端/技术预警/解禁到期/内部人减持/业绩预警/监管处罚/行业政策），返回风险评级和一票否决标记。“这股票有什么雷”用本工具；入场决策前排雷必调",
      parameters: {
        symbol: {
          type: "string",
          required: true,
          description: "股票代码（如 600519）",
        },
        name: { type: "string", description: "股票名称（可选，提升新闻搜索准确度）" },
      },
      script: "risk_screening.py",
      argv: (p) => {
        const args = ["screen", p.symbol];
        if (p.name) args.push("--name", p.name);
        return args;
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "detect_market_regime",
      description:
        "市场状态检测 — 分析大盘指数判断当前市场阶段（上涨趋势/下跌趋势/横盘震荡/高波动/板块热点），并推荐适合的分析策略。“现在大盘能进场吗/该用什么策略”用本工具",
      parameters: {
        market: {
          type: "string",
          enum: ["A", "HK", "US"],
          description: "市场代码，默认 A",
        },
      },
      script: "market_regime.py",
      argv: (p) => ["detect", p.market ?? "A"],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_market_review",
      description:
        "大盘复盘 — 获取市场日度复盘数据，包含指数、涨跌统计、板块排名、新闻、市场温度与策略建议。“复盘一下今天市场/今天大盘怎么样”首选本工具，无需再分别调指数/统计/板块工具",
      parameters: {
        market: {
          type: "string",
          enum: ["A", "HK", "US", "all"],
          description: "市场代码，默认 A。all 表示所有市场",
        },
      },
      script: "market_review.py",
      argv: (p) => ["review", "--market", p.market ?? "A"],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "run_watchlist_analysis",
      description:
        "批量自选股分析 — 对多只股票并行采集行情/技术/资金/风险等数据，返回汇总结果。“批量分析我的自选股”/每日定时分析用本工具",
      parameters: {
        symbols: {
          type: "string",
          required: true,
          description: "逗号分隔的股票代码列表，如 600519,000001,300750",
        },
        workers: {
          type: "number",
          description: "并发数，默认 3（建议不超过5，避免API限流）",
        },
      },
      script: "watchlist.py",
      argv: (p) => ["analyze", p.symbols, "--workers", String(p.workers ?? 3)],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "detect_anomaly",
      description:
        "异常/事件检测 — 一键扫描股票当前所有异动信号（MACD金叉死叉、RSI超买超卖、20日突破、放量异动、涨跌停、布林突破、KDJ极值、资金异动等），返回结构化异常列表。“今天有什么异动/为什么大涨大跌”首选本工具；综合技术面判断用 get_technical_analysis",
      parameters: {
        symbol: {
          type: "string",
          required: true,
          description: "股票代码（如 600519、AAPL、00700.HK）",
        },
      },
      script: "anomaly_detect.py",
      argv: (p) => ["detect", p.symbol],
    })
  );

  // --- Diagnostics & Capabilities ---

  ctx.tools.register(
    pyTool({
      name: "diagnose_data_sources",
      description:
        "数据源诊断 — 检查当前环境可用的数据 provider（akshare/tushare/yfinance/finnhub/longbridge/alphavantage），输出每个市场的可用链路、缺失 env、warnings。工具拿不到数据或报错时调用排查",
      parameters: {
        market: {
          type: "string",
          enum: ["A", "HK", "US", "JP", "KR", "TW", "all"],
          description: "市场，默认 all",
        },
      },
      script: "diagnostics.py",
      argv: (p) => ["check", "--market", p.market ?? "all"],
    })
  );

  ctx.tools.register(
    pyTool({
      name: "get_market_capabilities",
      description:
        "市场能力边界 — 返回指定市场支持/不支持的工具列表。不确定某市场能否用某工具（如港股的筹码分布、美股的资金流）时先调本工具，避免调用不支持的工具后编造数据",
      parameters: {
        market: {
          type: "string",
          enum: ["A", "HK", "US", "JP", "KR", "TW"],
          description: "市场代码",
        },
        symbol: { type: "string", description: "股票代码（自动识别市场，与 market 二选一）" },
      },
      script: "capabilities.py",
      argv: (p) =>
        p.symbol
          ? ["get", "--symbol", p.symbol]
          : ["get", "--market", p.market ?? "A"],
    })
  );

  // --- Report Rendering ---

  ctx.tools.register(
    pyTool({
      name: "render_stock_report",
      description:
        "股票分析报告渲染 — 将结构化 JSON（符合 schemas/report_schema.json）通过 j2 模板渲染为 Markdown。template: brief|full。全部分析完成后的最后一步调用。仅渲染，不保存不推送",
      parameters: {
        report: {
          type: "object",
          additionalProperties: true,
          required: true,
          description: "结构化股票报告",
        },
        template: {
          type: "string",
          enum: ["brief", "full"],
          description: "模板类型，默认 full",
        },
      },
      script: "report_renderer.py",
      argv: (p) => {
        const b64 = Buffer.from(JSON.stringify(p.report), "utf-8").toString("base64");
        return ["stock", "--template", p.template ?? "full", "--input-b64", b64];
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "render_market_report",
      description:
        "大盘复盘报告渲染 — 将结构化 JSON（符合 schemas/market_review_schema.json）通过 j2 模板渲染为 Markdown",
      parameters: {
        report: {
          type: "object",
          additionalProperties: true,
          required: true,
          description: "结构化市场复盘",
        },
        template: {
          type: "string",
          enum: ["full"],
          description: "模板类型，默认 full",
        },
      },
      script: "report_renderer.py",
      argv: (p) => {
        const b64 = Buffer.from(JSON.stringify(p.report), "utf-8").toString("base64");
        return ["market", "--template", p.template ?? "full", "--input-b64", b64];
      },
    })
  );

  // --- Watchlist / Position / Alert Context ---

  ctx.tools.register(
    pyTool({
      name: "build_watchlist_context",
      description:
        "自选股上下文包 — 对多只股票输出评分/趋势/异常/风险/建议 next_tools 的 agent 友好摘要。宿主 agent 决定如何写日报或深入分析",
      parameters: {
        symbols: { type: "string", required: true, description: "逗号分隔的股票代码列表" },
        include_market_review: {
          type: "boolean",
          description: "是否附带各市场复盘，默认 false",
        },
        workers: { type: "number", description: "并发数，默认 3" },
      },
      script: "watchlist_context.py",
      argv: (p) => {
        const args = ["build", p.symbols, "--workers", String(p.workers ?? 3)];
        if (p.include_market_review) args.push("--include-market-review");
        return args;
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "analyze_position_context",
      description:
        "持仓上下文分析 — 输入成本/仓位/止损止盈，结合现价和技术位输出浮盈亏、离止损距离、风险级别、操作建议。“我XX成本买的被套了/要不要止损止盈”用本工具。无状态、不存账户",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
        cost: { type: "number", required: true, description: "成本价" },
        quantity: { type: "number", required: true, description: "持仓数量" },
        stop_loss: { type: "number", description: "止损价" },
        take_profit: { type: "number", description: "止盈价" },
      },
      script: "position_context.py",
      argv: (p) => {
        const args = [
          "analyze",
          p.symbol,
          "--cost",
          String(p.cost),
          "--quantity",
          String(p.quantity),
        ];
        if (p.stop_loss !== undefined) args.push("--stop-loss", String(p.stop_loss));
        if (p.take_profit !== undefined) args.push("--take-profit", String(p.take_profit));
        return args;
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "check_alert_rules",
      description:
        "无状态告警规则检查 — 传入规则数组，返回当前是否触发。规则类型：price_below/price_above/change_pct_above/change_pct_below/volume_ratio_above/anomaly/risk_veto/risk_level_at_least。“到价/涨跌幅提醒是否触发”用本工具。不做调度不存历史",
      parameters: {
        symbol: { type: "string", required: true, description: "股票代码" },
        rules: {
          type: "array",
          items: { type: "object", additionalProperties: true },
          required: true,
          description: "规则列表，每项 { type, value }",
        },
      },
      script: "alert_rules.py",
      argv: (p) => {
        const b64 = Buffer.from(JSON.stringify(p.rules), "utf-8").toString("base64");
        return ["check", p.symbol, "--rules-b64", b64];
      },
    })
  );

  ctx.tools.register(
    pyTool({
      name: "parse_stock_list",
      description:
        "自选股/文本导入解析 — 从自然语言、CSV、Markdown 表格提取股票，自动识别 A 股 6 位代码、港股 xxxxx.HK、美股 ticker、日股 xxxx.T、韩股 xxxxxx.KS/KQ、台股 xxxx.TW，并调用 name_resolver 处理中文股票名",
      parameters: {
        text: { type: "string", required: true, description: "待解析的文本" },
      },
      script: "import_parser.py",
      argv: (p) => {
        const b64 = Buffer.from(String(p.text), "utf-8").toString("base64");
        return ["parse", "--text-b64", b64];
      },
    })
  );

  registerSkills(ctx);
}
