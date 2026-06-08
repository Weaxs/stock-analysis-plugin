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
const venvPython = join(repoRoot, ".venv", isWin ? "Scripts" : "bin", "python3");

function pythonBin(): string {
  return existsSync(venvPython) ? venvPython : "python3";
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
    "Stock analysis, screening, and strategy backtesting across A/HK/US markets",
  register(api) {
    // --- Data Tools ---

    api.registerTool({
      name: "get_kline",
      description:
        "获取股票K线数据（OHLCV）。支持A股（如600519）、港股（如00700.HK）、美股（如AAPL）",
      parameters: Type.Object({
        symbol: Type.String({
          description: "股票代码，如 600519（A股）、00700.HK（港股）、AAPL（美股）",
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
      description: "获取股票实时行情报价。支持A股、港股、美股",
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
        "获取主要市场指数行情。CN: 上证/深证/创业板/科创50/沪深300；HK: 恒生/国企/科技；US: 道琼斯/纳斯达克/标普500",
      parameters: Type.Object({
        region: Type.Optional(
          Type.Union(
            [Type.Literal("cn"), Type.Literal("hk"), Type.Literal("us")],
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
        "获取股票基本信息（行业、板块、上市日期、总股本等）。A股返回板块/行业，HK/US返回行业/公司简介",
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
        "查询某日是否为交易日。支持CN（A股）、HK（港股）、US（美股）三市场",
      parameters: Type.Object({
        market: Type.Union(
          [Type.Literal("CN"), Type.Literal("HK"), Type.Literal("US")],
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
      description: "获取最近/未来N个交易日列表。支持CN/HK/US三市场",
      parameters: Type.Object({
        market: Type.Union(
          [Type.Literal("CN"), Type.Literal("HK"), Type.Literal("US")],
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
  },
});
