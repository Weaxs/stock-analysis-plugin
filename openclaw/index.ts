import { existsSync } from "node:fs";
import * as cp from "node:child_process";
import { promisify } from "node:util";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "typebox";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

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
// Published form: tools/ ships next to index.ts. Dev form: tools/ is at repo root.
const toolsDir = existsSync(join(here, "tools"))
  ? join(here, "tools")
  : join(here, "..", "tools");
const repoRoot = dirname(toolsDir);
const isWin = process.platform === "win32";
// Windows venvs ship Scripts/python.exe (there is no python3); POSIX venvs ship bin/python3.
const venvBin = join(".venv", isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python3");
const venvPython = join(repoRoot, venvBin);
// Staged-payload layout (openclaw/tools present in a dev checkout): the venv
// still lives at the repo root one level up.
const parentVenvPython = join(repoRoot, "..", venvBin);

function pythonBin(): string {
  if (existsSync(venvPython)) return venvPython;
  if (existsSync(parentVenvPython)) return parentVenvPython;
  // On Windows "python3" is usually the Microsoft Store stub — prefer "python".
  return isWin ? "python" : "python3";
}

async function runPy(script: string, args: string[]): Promise<string> {
  return executor(pythonBin(), [join(toolsDir, script), ...args]);
}

function asText(text: string) {
  return { content: [{ type: "text" as const, text }] };
}

export default definePluginEntry({
  id: "stock-analysis",
  name: "Stock Analysis",
  description:
    "Stock analysis, screening, and strategy backtesting across A/HK/US/JP/KR/TW markets",
  register(api) {
    // --- Data Tools ---

    api.registerTool({
      name: "get_kline",
      description:
        "获取股票K线数据（OHLCV）。支持A股（如600519）、港股（如00700.HK）、美股（如AAPL）、日股（如7203.T）、韩股（如005930.KS）、台股（如2330.TW）及A股ETF",
      parameters: Type.Object({
        symbol: Type.String({
          description: "股票代码，如 600519（A股）、00700.HK（港股）、AAPL（美股）、7203.T（日股）、005930.KS（韩股）、2330.TW（台股）",
        }),
        period: Type.Optional(
          Type.Union(
            [Type.Literal("daily"), Type.Literal("weekly"), Type.Literal("monthly")],
            { description: "K线周期，默认 daily" }
          )
        ),
        count: Type.Optional(Type.Number({ description: "返回数据条数，默认 60" })),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "kline",
          params.symbol,
          "--period",
          params.period ?? "daily",
          "--count",
          String(params.count ?? 60),
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_quote",
      description: "获取股票实时行情报价。支持A股、港股、美股、日股、韩股、台股",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", ["quote", params.symbol]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_capital_flow",
      description:
        "获取A股资金流向数据。detail=个股每日明细，summary=多日汇总+趋势，sector_flow=板块资金流排行",
      parameters: Type.Object({
        symbol: Type.Optional(
          Type.String({ description: "A股股票代码（detail/summary模式必填），如 600519" })
        ),
        mode: Type.Optional(
          Type.Union(
            [
              Type.Literal("detail"),
              Type.Literal("summary"),
              Type.Literal("sector_flow"),
            ],
            { description: "模式：detail=每日明细（默认），summary=多日汇总，sector_flow=板块排行" }
          )
        ),
      }),
      async execute(_id, params) {
        const args = ["capital_flow"];
        if (params.symbol) args.push(params.symbol);
        args.push("--mode", params.mode ?? "detail");
        const out = await runPy("stock_data.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_news",
      description: "获取股票相关财经新闻",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        days: Type.Optional(
          Type.Number({ description: "获取最近几天的新闻，默认 3" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "news",
          params.symbol,
          "--days",
          String(params.days ?? 3),
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_financials",
      description: "获取股票关键财务指标（PE/PB/市值/营收/净利润/ROE等）",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", ["financials", params.symbol]);
        return asText(out);
      },
    });

    // --- Analysis Tools ---

    api.registerTool({
      name: "get_technical_analysis",
      description:
        "获取股票技术面分析（MA/MACD/RSI/BOLL/KDJ/成交量等指标 + 100分综合评分 + 6级买卖信号 + 趋势/偏离度/支撑压力位）",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        period: Type.Optional(
          Type.Union(
            [Type.Literal("daily"), Type.Literal("weekly"), Type.Literal("monthly")],
            { description: "分析周期，默认 daily" }
          )
        ),
        count: Type.Optional(
          Type.Number({ description: "用于计算指标的K线条数，默认 120" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("technical.py", [
          "analyze",
          params.symbol,
          "--period",
          params.period ?? "daily",
          "--count",
          String(params.count ?? 120),
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "analyze_pattern",
      description:
        "K线形态识别 — 检测十字星、锤子线、吞没、启明星、黄昏星、双底、20日突破等12+种经典形态",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        period: Type.Optional(
          Type.Union(
            [Type.Literal("daily"), Type.Literal("weekly"), Type.Literal("monthly")],
            { description: "K线周期，默认 daily" }
          )
        ),
        days: Type.Optional(
          Type.Number({ description: "分析的K线天数，默认 60" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("pattern.py", [
          "analyze",
          params.symbol,
          "--period",
          params.period ?? "daily",
          "--days",
          String(params.days ?? 60),
        ]);
        return asText(out);
      },
    });

    // --- Market Tools ---

    api.registerTool({
      name: "get_market_indices",
      description:
        "获取主要市场指数行情。CN: 上证/深证/创业板/科创50/沪深300；HK: 恒生/国企/科技；US: 道琼斯/纳斯达克/标普500；JP: 日经225/东证；KR: KOSPI/KOSDAQ；TW: 台湾加权",
      parameters: Type.Object({
        region: Type.Optional(
          Type.Union(
            [Type.Literal("cn"), Type.Literal("hk"), Type.Literal("us"), Type.Literal("jp"), Type.Literal("kr"), Type.Literal("tw")],
            { description: "市场区域，默认 cn" }
          )
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "market_indices",
          "--region",
          params.region ?? "cn",
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_sector_rankings",
      description:
        "获取A股行业板块涨跌幅排行（含领涨股、涨跌家数等）。支持查看涨幅榜/跌幅榜/双向",
      parameters: Type.Object({
        top: Type.Optional(
          Type.Number({ description: "返回排名前N的板块，默认 10" })
        ),
        direction: Type.Optional(
          Type.Union(
            [Type.Literal("top"), Type.Literal("bottom"), Type.Literal("both")],
            { description: "top=涨幅榜（默认），bottom=跌幅榜，both=双向" }
          )
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "sector_rankings",
          "--top",
          String(params.top ?? 10),
          "--direction",
          params.direction ?? "top",
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_stock_info",
      description:
        "获取股票基本信息（行业、板块、上市日期、总股本等）。A股返回板块/行业，其他市场返回行业/公司简介",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", ["stock_info", params.symbol]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_chip_distribution",
      description:
        "获取A股筹码分布数据（获利比例、平均成本、90%/70%成本集中度）。仅支持A股",
      parameters: Type.Object({
        symbol: Type.String({ description: "A股股票代码，如 600519" }),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "chip_distribution",
          params.symbol,
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_market_stats",
      description:
        "获取A股市场整体统计（涨跌家数、涨停跌停数、平均涨幅、涨跌Top5、总成交额）",
      parameters: Type.Object({
        market: Type.Optional(
          Type.Union([Type.Literal("A")], { description: "市场，目前仅支持 A" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "market_stats",
          "--market",
          params.market ?? "A",
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_fundamental_context",
      description:
        "获取A股深度基本面（估值PE/PB/PS + 成长性营收/净利增速 + 盈利能力ROE/毛利率 + 分红历史）",
      parameters: Type.Object({
        symbol: Type.String({ description: "A股股票代码，如 600519" }),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "fundamental_context",
          params.symbol,
        ]);
        return asText(out);
      },
    });

    // --- Screener & Backtest ---

    api.registerTool({
      name: "screen_stocks",
      description:
        "全市场股票筛选（AlphaSift L1 多因子硬筛）。按PE/PB/市值/换手率/涨跌幅/量比等因子过滤和评分",
      parameters: Type.Object({
        market: Type.Optional(
          Type.Union(
            [Type.Literal("A"), Type.Literal("HK"), Type.Literal("US")],
            { description: "市场，默认 A" }
          )
        ),
        top: Type.Optional(
          Type.Number({ description: "返回排名前N的股票，默认 20" })
        ),
        config: Type.Optional(
          Type.String({ description: "自定义筛选配置YAML文件路径（可选）" })
        ),
      }),
      async execute(_id, params) {
        const args = [
          "screen",
          "--market",
          params.market ?? "A",
          "--top",
          String(params.top ?? 20),
        ];
        if (params.config) args.push("--config", params.config);
        const out = await runPy("screener.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "run_backtest",
      description:
        "策略回测（AlphaEvo）。读取YAML策略定义，在历史数据上模拟交易，输出收益率/回撤/胜率等指标",
      parameters: Type.Object({
        strategy: Type.String({ description: "策略YAML文件路径" }),
        symbol: Type.String({ description: "股票代码" }),
        start: Type.Optional(
          Type.String({ description: "回测起始日期，格式 YYYY-MM-DD" })
        ),
        end: Type.Optional(
          Type.String({ description: "回测结束日期，格式 YYYY-MM-DD" })
        ),
        capital: Type.Optional(
          Type.Number({ description: "初始资金，默认 1000000" })
        ),
      }),
      async execute(_id, params) {
        const args = ["run", params.strategy, params.symbol];
        if (params.start) args.push("--start", params.start);
        if (params.end) args.push("--end", params.end);
        args.push("--capital", String(params.capital ?? 1000000));
        const out = await runPy("backtest.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "evaluate_signal",
      description:
        "技术信号历史准确率评估 — 回溯历史数据，统计某个技术信号触发后N日的胜率和平均收益。支持9种信号：macd_golden_cross/macd_death_cross/rsi_oversold/rsi_overbought/breakout_20d/breakdown_20d/volume_surge/ma_golden_cross/ma_death_cross",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        signal: Type.Union(
          [
            Type.Literal("macd_golden_cross"),
            Type.Literal("macd_death_cross"),
            Type.Literal("rsi_oversold"),
            Type.Literal("rsi_overbought"),
            Type.Literal("breakout_20d"),
            Type.Literal("breakdown_20d"),
            Type.Literal("volume_surge"),
            Type.Literal("ma_golden_cross"),
            Type.Literal("ma_death_cross"),
          ],
          { description: "信号名称" }
        ),
        forward_days: Type.Optional(
          Type.String({ description: "逗号分隔的前瞻天数，默认 3,5,10" })
        ),
        lookback: Type.Optional(
          Type.Number({ description: "回溯K线条数，默认 250（约1年）" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("backtest.py", [
          "evaluate_signal",
          params.symbol,
          params.signal,
          "--forward",
          params.forward_days ?? "3,5,10",
          "--lookback",
          String(params.lookback ?? 250),
        ]);
        return asText(out);
      },
    });

    // --- Name Resolution ---

    api.registerTool({
      name: "resolve_stock_name",
      description:
        "股票名称智能解析 — 输入中文名（贵州茅台）、拼音（guizhou maotai/gzmt）、部分代码，返回匹配的股票代码。仅支持A股",
      parameters: Type.Object({
        query: Type.String({
          description: "股票名称、拼音、拼音首字母或部分代码",
        }),
        top: Type.Optional(
          Type.Number({ description: "返回匹配数量，默认 5" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("name_resolver.py", [
          "resolve",
          params.query,
          "--top",
          String(params.top ?? 5),
        ]);
        return asText(out);
      },
    });

    // --- Trading Calendar ---

    api.registerTool({
      name: "check_trading_day",
      description:
        "查询某日是否为交易日。支持CN（A股）、HK（港股）、US（美股）、JP（日股）、KR（韩股）、TW（台股）",
      parameters: Type.Object({
        market: Type.Union(
          [Type.Literal("CN"), Type.Literal("HK"), Type.Literal("US"), Type.Literal("JP"), Type.Literal("KR"), Type.Literal("TW")],
          { description: "市场" }
        ),
        date: Type.Optional(
          Type.String({ description: "日期（YYYY-MM-DD），不填则查今天" })
        ),
      }),
      async execute(_id, params) {
        const args = ["check", params.market];
        if (params.date) args.push("--date", params.date);
        const out = await runPy("trading_calendar.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_trading_days",
      description: "获取最近/未来N个交易日列表。支持CN/HK/US/JP/KR/TW",
      parameters: Type.Object({
        market: Type.Union(
          [Type.Literal("CN"), Type.Literal("HK"), Type.Literal("US"), Type.Literal("JP"), Type.Literal("KR"), Type.Literal("TW")],
          { description: "市场" }
        ),
        direction: Type.Optional(
          Type.Union([Type.Literal("next"), Type.Literal("prev")], {
            description: "next=未来交易日, prev=过去交易日",
          })
        ),
        count: Type.Optional(
          Type.Number({ description: "返回天数，默认 5" })
        ),
        date: Type.Optional(
          Type.String({ description: "起始日期（YYYY-MM-DD），默认今天" })
        ),
      }),
      async execute(_id, params) {
        const args = [
          params.direction ?? "next",
          params.market,
          "--count",
          String(params.count ?? 5),
        ];
        if (params.date) args.push("--date", params.date);
        const out = await runPy("trading_calendar.py", args);
        return asText(out);
      },
    });

    // --- Standalone MA Calculator ---

    api.registerTool({
      name: "calculate_ma",
      description:
        "独立均线计算器 — 支持任意周期MA（5/10/20/30/60/120/250或自定义）+ 偏离度 + 均线排列 + 金叉死叉检测",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        periods: Type.Optional(
          Type.String({
            description: "逗号分隔的MA周期列表，如 5,10,20,60,120,250",
          })
        ),
        kline_period: Type.Optional(
          Type.Union(
            [Type.Literal("daily"), Type.Literal("weekly"), Type.Literal("monthly")],
            { description: "K线周期，默认 daily" }
          )
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("technical.py", [
          "calculate_ma",
          params.symbol,
          "--periods",
          params.periods ?? "5,10,20,30,60,120,250",
          "--period",
          params.kline_period ?? "daily",
          "--count",
          "300",
        ]);
        return asText(out);
      },
    });

    // --- Volume-Price Analysis ---

    api.registerTool({
      name: "get_volume_analysis",
      description:
        "独立量价分析 — 量价相关性、上涨/下跌日成交量对比、量能趋势、量价模式解读（放量上涨/缩量回调等）",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        period: Type.Optional(
          Type.Union(
            [Type.Literal("daily"), Type.Literal("weekly"), Type.Literal("monthly")],
            { description: "K线周期，默认 daily" }
          )
        ),
        count: Type.Optional(
          Type.Number({ description: "分析K线条数，默认 60" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("volume_analysis.py", [
          "analyze",
          params.symbol,
          "--period",
          params.period ?? "daily",
          "--count",
          String(params.count ?? 60),
        ]);
        return asText(out);
      },
    });

    // --- Search Intelligence ---

    api.registerTool({
      name: "search_stock_news",
      description:
        "多引擎股票新闻搜索（支持 Tavily/Brave/SerpAPI）。需配置对应 API Key 环境变量",
      parameters: Type.Object({
        query: Type.String({ description: "搜索关键词" }),
        count: Type.Optional(
          Type.Number({ description: "返回结果数量，默认 10" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("search_intel.py", [
          "search",
          params.query,
          "--count",
          String(params.count ?? 10),
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "search_comprehensive_intel",
      description:
        "股票综合情报搜索 — 从6个维度（新闻/公告/行情分析/风险/业绩/行业）搜索综合信息",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        name: Type.Optional(
          Type.String({ description: "股票名称（可选，提升搜索准确度）" })
        ),
      }),
      async execute(_id, params) {
        const args = ["comprehensive", params.symbol];
        if (params.name) args.push("--name", params.name);
        const out = await runPy("search_intel.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_social_sentiment",
      description:
        "获取股票社交媒体情绪数据（Reddit/X/Polymarket）。主要支持美股。需配置 SENTIMENT_API_KEY",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（如 AAPL）" }),
      }),
      async execute(_id, params) {
        const out = await runPy("search_intel.py", ["sentiment", params.symbol]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_trending_sentiment",
      description:
        "获取社交媒体热门趋势（Reddit/X/Polymarket热门股票讨论）。数据缓存10分钟。适用于发现市场热点",
      parameters: Type.Object({}),
      async execute() {
        const out = await runPy("search_intel.py", ["trending"]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "extract_article",
      description:
        "网页文章全文提取 — 输入URL，提取文章标题、正文（最多3000字）、作者、发布日期等。适用于深度阅读搜索结果中的新闻/研报",
      parameters: Type.Object({
        url: Type.String({ description: "文章URL" }),
      }),
      async execute(_id, params) {
        const out = await runPy("search_intel.py", ["extract", params.url]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "screen_risk",
      description:
        "风险专项筛查 — 7维度风险检测（估值极端/技术预警/解禁到期/内部人减持/业绩预警/监管处罚/行业政策），返回风险评级和一票否决标记",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（如 600519）" }),
        name: Type.Optional(
          Type.String({ description: "股票名称（可选，提升新闻搜索准确度）" })
        ),
      }),
      async execute(_id, params) {
        const args = ["screen", params.symbol];
        if (params.name) args.push("--name", params.name);
        const out = await runPy("risk_screening.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "detect_market_regime",
      description:
        "市场状态检测 — 分析大盘指数判断当前市场阶段（上涨趋势/下跌趋势/横盘震荡/高波动/板块热点），并推荐适合的分析策略",
      parameters: Type.Object({
        market: Type.Optional(
          Type.Union(
            [Type.Literal("A"), Type.Literal("HK"), Type.Literal("US")],
            { description: "市场代码，默认 A" }
          )
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("market_regime.py", [
          "detect",
          params.market ?? "A",
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_market_review",
      description:
        "大盘复盘 — 获取市场日度复盘数据，包含指数、涨跌统计、板块排名、新闻、市场温度与策略建议",
      parameters: Type.Object({
        market: Type.Optional(
          Type.Union(
            [
              Type.Literal("A"),
              Type.Literal("HK"),
              Type.Literal("US"),
              Type.Literal("all"),
            ],
            { description: "市场代码，默认 A。all 表示所有市场" }
          )
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("market_review.py", [
          "review",
          "--market",
          params.market ?? "A",
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "run_watchlist_analysis",
      description:
        "批量自选股分析 — 对多只股票并行采集行情/技术/资金/风险等数据，返回汇总结果。适用于每日定时分析自选股列表",
      parameters: Type.Object({
        symbols: Type.String({
          description: "逗号分隔的股票代码列表，如 600519,000001,300750",
        }),
        workers: Type.Optional(
          Type.Number({
            description: "并发数，默认 3（建议不超过5，避免API限流）",
          })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("watchlist.py", [
          "analyze",
          params.symbols,
          "--workers",
          String(params.workers ?? 3),
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "detect_anomaly",
      description:
        "异常/事件检测 — 一键扫描股票当前所有异动信号（MACD金叉死叉、RSI超买超卖、20日突破、放量异动、涨跌停、布林突破、KDJ极值、资金异动等），返回结构化异常列表",
      parameters: Type.Object({
        symbol: Type.String({
          description: "股票代码（如 600519、AAPL、00700.HK）",
        }),
      }),
      async execute(_id, params) {
        const out = await runPy("anomaly_detect.py", ["detect", params.symbol]);
        return asText(out);
      },
    });

    // --- Diagnostics & Capabilities ---

    api.registerTool({
      name: "diagnose_data_sources",
      description:
        "数据源诊断 — 检查当前环境可用的数据 provider（akshare/tushare/yfinance/finnhub/longbridge/alphavantage），输出每个市场的可用链路、缺失 env、warnings",
      parameters: Type.Object({
        market: Type.Optional(
          Type.Union(
            [
              Type.Literal("A"),
              Type.Literal("HK"),
              Type.Literal("US"),
              Type.Literal("JP"),
              Type.Literal("KR"),
              Type.Literal("TW"),
              Type.Literal("all"),
            ],
            { description: "市场，默认 all" }
          )
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("diagnostics.py", [
          "check",
          "--market",
          params.market ?? "all",
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_market_capabilities",
      description:
        "市场能力边界 — 返回指定市场支持/不支持的工具列表，避免 agent 对港股调 get_chip_distribution 或对美股调 get_capital_flow 后编造数据",
      parameters: Type.Object({
        market: Type.Optional(
          Type.Union(
            [Type.Literal("A"), Type.Literal("HK"), Type.Literal("US"), Type.Literal("JP"), Type.Literal("KR"), Type.Literal("TW")],
            { description: "市场代码" }
          )
        ),
        symbol: Type.Optional(
          Type.String({ description: "股票代码（自动识别市场，与 market 二选一）" })
        ),
      }),
      async execute(_id, params) {
        const args = params.symbol
          ? ["get", "--symbol", params.symbol]
          : ["get", "--market", params.market ?? "A"];
        const out = await runPy("capabilities.py", args);
        return asText(out);
      },
    });

    // --- Report Rendering ---

    api.registerTool({
      name: "render_stock_report",
      description:
        "股票分析报告渲染 — 将结构化 JSON（符合 schemas/report_schema.json）通过 j2 模板渲染为 Markdown。template: brief|full。仅渲染，不保存不推送",
      parameters: Type.Object({
        report: Type.Object({}, { additionalProperties: true, description: "结构化股票报告" }),
        template: Type.Optional(
          Type.Union([Type.Literal("brief"), Type.Literal("full")], {
            description: "模板类型，默认 full",
          })
        ),
      }),
      async execute(_id, params) {
        const b64 = Buffer.from(JSON.stringify(params.report), "utf-8").toString("base64");
        const out = await runPy("report_renderer.py", [
          "stock",
          "--template",
          params.template ?? "full",
          "--input-b64",
          b64,
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "render_market_report",
      description:
        "大盘复盘报告渲染 — 将结构化 JSON（符合 schemas/market_review_schema.json）通过 j2 模板渲染为 Markdown",
      parameters: Type.Object({
        report: Type.Object({}, { additionalProperties: true, description: "结构化市场复盘" }),
        template: Type.Optional(
          Type.Union([Type.Literal("full")], { description: "模板类型，默认 full" })
        ),
      }),
      async execute(_id, params) {
        const b64 = Buffer.from(JSON.stringify(params.report), "utf-8").toString("base64");
        const out = await runPy("report_renderer.py", [
          "market",
          "--template",
          params.template ?? "full",
          "--input-b64",
          b64,
        ]);
        return asText(out);
      },
    });

    // --- Watchlist / Position / Alert Context ---

    api.registerTool({
      name: "build_watchlist_context",
      description:
        "自选股上下文包 — 对多只股票输出评分/趋势/异常/风险/建议 next_tools 的 agent 友好摘要。宿主 agent 决定如何写日报或深入分析",
      parameters: Type.Object({
        symbols: Type.String({ description: "逗号分隔的股票代码列表" }),
        include_market_review: Type.Optional(
          Type.Boolean({ description: "是否附带各市场复盘，默认 false" })
        ),
        workers: Type.Optional(Type.Number({ description: "并发数，默认 3" })),
      }),
      async execute(_id, params) {
        const args = [
          "build",
          params.symbols,
          "--workers",
          String(params.workers ?? 3),
        ];
        if (params.include_market_review) args.push("--include-market-review");
        const out = await runPy("watchlist_context.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "analyze_position_context",
      description:
        "持仓上下文分析 — 输入成本/仓位/止损止盈，结合现价和技术位输出浮盈亏、离止损距离、风险级别、操作建议。无状态、不存账户",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        cost: Type.Number({ description: "成本价" }),
        quantity: Type.Number({ description: "持仓数量" }),
        stop_loss: Type.Optional(Type.Number({ description: "止损价" })),
        take_profit: Type.Optional(Type.Number({ description: "止盈价" })),
      }),
      async execute(_id, params) {
        const args = [
          "analyze",
          params.symbol,
          "--cost",
          String(params.cost),
          "--quantity",
          String(params.quantity),
        ];
        if (params.stop_loss !== undefined) {
          args.push("--stop-loss", String(params.stop_loss));
        }
        if (params.take_profit !== undefined) {
          args.push("--take-profit", String(params.take_profit));
        }
        const out = await runPy("position_context.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "check_alert_rules",
      description:
        "无状态告警规则检查 — 传入规则数组，返回当前是否触发。规则类型：price_below/price_above/change_pct_above/change_pct_below/volume_ratio_above/anomaly/risk_veto/risk_level_at_least。不做调度不存历史",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码" }),
        rules: Type.Array(
          Type.Object({}, { additionalProperties: true }),
          { description: "规则列表，每项 { type, value }" }
        ),
      }),
      async execute(_id, params) {
        const b64 = Buffer.from(JSON.stringify(params.rules), "utf-8").toString("base64");
        const out = await runPy("alert_rules.py", [
          "check",
          params.symbol,
          "--rules-b64",
          b64,
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "parse_stock_list",
      description:
        "自选股/文本导入解析 — 从自然语言、CSV、Markdown 表格提取股票，自动识别 A 股 6 位代码、港股 xxxxx.HK、美股 ticker、日股 xxxx.T、韩股 xxxxxx.KS/KQ、台股 xxxx.TW，并调用 name_resolver 处理中文股票名",
      parameters: Type.Object({
        text: Type.String({ description: "待解析的文本" }),
      }),
      async execute(_id, params) {
        const b64 = Buffer.from(String(params.text), "utf-8").toString("base64");
        const out = await runPy("import_parser.py", ["parse", "--text-b64", b64]);
        return asText(out);
      },
    });
  },
});
