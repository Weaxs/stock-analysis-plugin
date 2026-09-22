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
        "获取股票K线数据（OHLCV）。支持A股（如600519）、港股（如00700.HK）、美股（如AAPL）、日股（如7203.T）、韩股（如005930.KS）、台股（如2330.TW）及A股ETF。需要原始历史价格自行计算或画图时用；只问指标用 get_technical_analysis，只问现价用 get_quote",
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
      description: "获取股票实时行情报价（现价、涨跌幅、量比等）。支持A股、港股、美股、日股、韩股、台股；非交易时段返回最近交易日收盘价并以 as_of/stale 标注；ETF 的 premium_discount_rate 为正=溢价、负=折价",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", ["quote", params.symbol]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_capital_flow",
      description:
        "获取A股资金流向（主力/超大单/大单/中单/小单净流入）。detail=个股每日明细，summary=多日汇总+趋势，sector_flow=板块资金流排行。可配合 get_chip_distribution 验证主力行为",
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
      description: "获取个股最近N天财经新闻快讯（轻量、无需配置）。需要深度全网情报用 search_comprehensive_intel，按主题搜索用 search_stock_news，读文章全文用 extract_article",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
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
      description: "获取股票财务摘要。A股返回最新报告期 ROE/毛利率/净利率/负债率/流动比率；其他市场返回 PE/PB/市值/营收/净利润等。A股估值、成长和分红用 get_fundamental_context",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
        periods: Type.Optional(
          Type.Number({ description: "返回最近N个报告期的财务趋势，默认 1（仅最新一期）" })
        ),
      }),
      async execute(_id, params) {
        const args = ["financials", params.symbol];
        if (params.periods && params.periods > 1) args.push("--periods", String(params.periods));
        const out = await runPy("stock_data.py", args);
        return asText(out);
      },
    });

    // --- Analysis Tools ---

    api.registerTool({
      name: "get_technical_analysis",
      description:
        "获取股票技术面分析（MA/MACD/RSI/BOLL/KDJ/成交量等指标 + 100分综合评分 + 6级买卖信号 + 趋势/偏离度/支撑压力位；多周期共振时 resonance.direction ∈ aligned_bullish/aligned_bearish/divergent）。个股技术面综合判断与买卖时机分析的首选；只要均线数值或自定义周期用 calculate_ma，专问量价用 get_volume_analysis，扫当日异动用 detect_anomaly",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
        period: Type.Optional(
          Type.Union(
            [Type.Literal("daily"), Type.Literal("weekly"), Type.Literal("monthly")],
            { description: "分析周期，默认 daily" }
          )
        ),
        count: Type.Optional(
          Type.Number({ description: "用于计算指标的K线条数，默认 120" })
        ),
        periods: Type.Optional(
          Type.String({
            description: '多周期共振分析，逗号分隔的周期列表，如 "daily,weekly"；不传则只分析 period 指定的单周期',
          })
        ),
      }),
      async execute(_id, params) {
        const args = [
          "analyze",
          params.symbol,
          "--period",
          params.period ?? "daily",
          "--count",
          String(params.count ?? 120),
        ];
        if (params.periods) args.push("--periods", params.periods);
        const out = await runPy("technical.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "analyze_pattern",
      description:
        "K线形态识别 — 检测十字星、锤子线、吞没、启明星、黄昏星、双底、20日突破等12+种经典形态。综合技术面判断用 get_technical_analysis",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
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
        "获取主要市场指数行情。CN: 上证/深证/创业板/科创50/沪深300；HK: 恒生/国企/科技；US: 道琼斯/纳斯达克/标普500；JP: 日经225/东证；KR: KOSPI/KOSDAQ；TW: 台湾加权。大盘复盘用 get_market_review 一站式获取",
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
        "获取A股板块涨跌幅排行（含领涨股、涨跌家数等）。board_type=industry（默认）为行业板块，board_type=concept 时为概念板块排行。支持查看涨幅榜/跌幅榜/双向",
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
        board_type: Type.Optional(
          Type.Union(
            [Type.Literal("industry"), Type.Literal("concept")],
            { description: "板块类型：industry=行业（默认），concept=概念" }
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
          "--board-type",
          params.board_type ?? "industry",
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_sector_constituents",
      description:
        "查询A股板块成分股（正向映射：板块→股票列表）。板块名支持模糊匹配（如「创新药」自动解析到精确板块名），东财行业/概念 + 新浪多源 failover，结果缓存24小时。仅支持A股；反向查个股所属板块用 resolve_stock_sectors",
      parameters: Type.Object({
        sector: Type.String({ description: "板块名称（可模糊，如 创新药、半导体）" }),
        board_type: Type.Optional(
          Type.Union(
            [Type.Literal("industry"), Type.Literal("concept"), Type.Literal("auto")],
            { description: "板块类型：industry=行业，concept=概念，auto=自动（默认）" }
          )
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "sector_constituents",
          params.sector,
          "--board-type",
          params.board_type ?? "auto",
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "resolve_stock_sectors",
      description:
        "查询个股所属板块（反向映射：个股→板块）。A股返回东财行业 + efinance 概念/板块（雪球行业兜底，需 XUEQIU_TOKEN），港股返回 yfinance GICS 英文 sector/industry 口径。板块级情报（如「创新药回调」）映射到池内个股时调本工具；正向查板块成分股用 get_sector_constituents",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如 600519，港股如 00700.HK）" }),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "resolve_stock_sectors",
          params.symbol,
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_stock_info",
      description:
        "获取股票基本信息（行业、板块、上市日期、总股本等）。A股返回板块/行业，其他市场返回行业/公司简介",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", ["stock_info", params.symbol]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_chip_distribution",
      description:
        "获取A股筹码分布数据（获利比例、平均成本、90%/70%成本集中度）。仅支持A股。与 get_capital_flow 配合验证主力行为",
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
        "获取A股市场整体统计（涨跌家数、涨停跌停数、平均涨幅、涨跌Top5、总成交额）。用于衡量市场整体情绪与温度",
      parameters: Type.Object({}),
      async execute() {
        const out = await runPy("stock_data.py", ["market_stats"]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_limit_up_pool",
      description:
        "获取A股涨停池/涨停板复盘——当日涨停个股，含连板数、封板资金、炸板次数、首末次封板时间、所属行业。短线情绪与龙头战法核心数据",
      parameters: Type.Object({
        date: Type.Optional(
          Type.String({ description: "日期（YYYYMMDD），默认当日；非交易日或未来日期自动回退到最近交易日（返回含 requested_date 与 stale 标注）" })
        ),
      }),
      async execute(_id, params) {
        const args = ["limit_up_pool"];
        if (params.date) args.push("--date", params.date);
        const out = await runPy("stock_data.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_dragon_tiger",
      description:
        "获取A股龙虎榜——上榜个股净买额/买入额/卖出额/上榜原因/解读，可按个股过滤",
      parameters: Type.Object({
        date: Type.Optional(
          Type.String({ description: "日期（YYYY-MM-DD），默认当日；非交易日或未来日期自动回退到最近交易日（返回含 requested_date 与 stale 标注）" })
        ),
        symbol: Type.Optional(
          Type.String({ description: "A股股票代码（可选，按个股过滤），如 600519" })
        ),
        top: Type.Optional(
          Type.Number({ description: "返回前N条，默认 20" })
        ),
      }),
      async execute(_id, params) {
        const args = ["dragon_tiger"];
        if (params.date) args.push("--date", params.date);
        if (params.symbol) args.push("--symbol", params.symbol);
        args.push("--top", String(params.top ?? 20));
        const out = await runPy("stock_data.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_hot_stocks",
      description: "获取A股全市场人气热搜榜（东方财富人气榜）",
      parameters: Type.Object({
        top: Type.Optional(
          Type.Number({ description: "返回前N只，默认 20" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "hot_stocks",
          "--top",
          String(params.top ?? 20),
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_margin_trading",
      description:
        "获取A股个股融资融券明细（融资余额/融资买入额/融券余量等，上交所/深交所官方数据按交易所分流），返回最近N个交易日序列（最新在前）。金额单位为元，short_*_shares 单位为股。仅两融标的有数据，非两融标的返回错误",
      parameters: Type.Object({
        symbol: Type.String({ description: "A股股票代码，如 600519" }),
        days: Type.Optional(
          Type.Number({ description: "返回最近N个交易日，默认 10" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "margin_trading",
          params.symbol,
          "--days",
          String(params.days ?? 10),
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_northbound_flow",
      description:
        "获取北向资金市场级净买入序列（最新在前，单位亿元）。数据口径：东方财富沪深港通历史数据；2024-08-16 起交易所停止披露日度净买额，仅 2024-08 之前历史数据可查（近期小 days 窗口会返回停披错误，加大 days 可取历史）",
      parameters: Type.Object({
        days: Type.Optional(
          Type.Number({ description: "返回最近N个交易日，默认 10" })
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("stock_data.py", [
          "northbound_flow",
          "--days",
          String(params.days ?? 10),
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_fundamental_context",
      description:
        "获取A股基本面上下文（估值 PE/PB/市值 + 营收/净利增速 + ROE/毛利率/净利率 + 分红历史）。用于评估公司质地与长期持有价值",
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
        "全市场股票筛选（AlphaSift L1 多因子硬筛 + 市场情绪调节）。按PE/PB/市值/换手率/涨跌幅/量比等因子过滤和评分，可用 l2 开启质量/成长/动量/波动率/资金流增强",
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
          Type.String({ description: "自定义筛选配置YAML文件路径，不填用默认配置" })
        ),
        l2: Type.Optional(
          Type.Boolean({ description: "开启L2量化增强（质量/成长/真实动量/波动率/资金流因子，逐股抓取数据较慢），默认关闭" })
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
        if (params.l2) args.push("--l2");
        const out = await runPy("screener.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "run_backtest",
      description:
        "策略回测（AlphaEvo）。读取YAML策略定义，在历史数据上模拟交易，输出收益率/回撤/胜率等指标。只想知道单个技术信号的历史胜率用更轻量的 evaluate_signal",
      parameters: Type.Object({
        strategy: Type.String({ description: "策略YAML文件路径" }),
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
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
        "技术信号历史准确率评估 — 回溯历史数据，统计某个技术信号触发后N日的胜率和平均收益。支持9种信号：macd_golden_cross/macd_death_cross/rsi_oversold/rsi_overbought/breakout_20d/breakdown_20d/volume_surge/ma_golden_cross/ma_death_cross。要完整模拟交易过程用 run_backtest",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
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
        "股票名称智能解析 — 输入中文名（贵州茅台）、拼音（guizhou maotai/gzmt）、部分代码，返回匹配的股票代码。仅支持A股。用户给出中文名/拼音/部分代码时先调本工具换成代码再调其他工具",
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

    api.registerTool({
      name: "get_trading_phase",
      description:
        "查询某市场当前交易时段（盘前/早盘/午间休市/午盘/交易中/盘后/休市），用于正确解读实时行情的盘中语义。支持CN/HK/US/JP/KR/TW",
      parameters: Type.Object({
        market: Type.Union(
          [Type.Literal("CN"), Type.Literal("HK"), Type.Literal("US"), Type.Literal("JP"), Type.Literal("KR"), Type.Literal("TW")],
          { description: "市场" }
        ),
      }),
      async execute(_id, params) {
        const out = await runPy("trading_calendar.py", ["phase", params.market]);
        return asText(out);
      },
    });

    // --- Standalone MA Calculator ---

    api.registerTool({
      name: "calculate_ma",
      description:
        "独立均线计算器 — 支持任意周期MA（5/10/20/30/60/120/250或自定义）+ 偏离度 + 均线排列 + 金叉死叉检测。只要均线数值或需要30/120/250等非默认周期时用本工具；综合技术面分析用 get_technical_analysis",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
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
        "独立量价分析 — 量价相关性、上涨/下跌日成交量对比、量能趋势、量价模式解读（放量上涨/缩量回调等）。综合技术面判断用 get_technical_analysis，扫当日异动用 detect_anomaly",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
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
        "多引擎股票新闻搜索（Tavily/Brave/SerpAPI/Bocha/SearXNG）。需配置至少一个搜索源的 Key 或 SearXNG URL。get_news 快讯不够或要按主题搜索时用；深度6维情报用 search_comprehensive_intel",
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
        "股票综合情报搜索 — 从6个维度（新闻/公告/行情分析/风险/业绩/行业）搜索综合信息。用于个股的深入研究/全面调研；快速看新闻用 get_news",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
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
        "获取股票社交媒体情绪数据。A股：东方财富股吧热度+雪球讨论热度（无需配置）；美股/港股：Reddit/X/Polymarket情绪（需 SENTIMENT_API_KEY）。自动按市场选数据源。面向个股维度；全市场热门讨论用 get_trending_sentiment",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
      }),
      async execute(_id, params) {
        const out = await runPy("search_intel.py", ["sentiment", params.symbol]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_trending_sentiment",
      description:
        "获取社交媒体热门趋势（Reddit/X/Polymarket热门股票讨论）。适用于发现市场热点；查个股情绪用 get_social_sentiment",
      parameters: Type.Object({}),
      async execute() {
        const out = await runPy("search_intel.py", ["trending"]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "extract_article",
      description:
        "网页文章全文提取 — 输入URL，提取文章标题、正文（最多3000字）、作者、发布日期等。配合 get_news/search_stock_news 的搜索结果做深度阅读",
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
        "A股风险专项筛查 — 检查估值、技术预警、解禁、减持、业绩预警、监管和行业政策，返回风险评级和 veto_buy。新闻搜索命中是待核实线索，不是已证实风险",
      parameters: Type.Object({
        symbol: Type.String({ description: "A股股票代码，如 600519" }),
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
        "大盘复盘 — 获取市场日度复盘数据，包含指数、涨跌统计、板块排名、新闻、市场温度与策略建议。复盘类需求的首选，无需再分别调指数/统计/板块工具",
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
        "异常/事件检测 — 一键扫描股票当前所有异动信号（MACD金叉死叉、RSI超买超卖、20日突破、放量异动、涨跌停、布林突破、KDJ极值、资金异动等），返回结构化异常列表。当日异动归因的首选；综合技术面判断用 get_technical_analysis",
      parameters: Type.Object({
        symbol: Type.String({
          description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）",
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
        "数据源诊断 — 检查当前环境各数据 provider 的包与凭据就绪情况（不探测网络可达性），输出每个市场的可用链路、缺失 env、warnings。工具拿不到数据或报错时调用排查",
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
        "市场能力边界 — 返回指定市场支持/不支持的工具列表。不确定某市场能否用某工具（如港股的筹码分布、美股的资金流）时先调本工具，避免调用不支持的工具后编造数据",
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
        "股票分析报告渲染 — 将结构化报告 JSON 通过 j2 模板渲染为 Markdown。report 字段以 skill 提供的 report_schema 为准，不要自造字段。template: brief|full。全部分析完成后的最后一步调用。仅渲染，不保存不推送",
      parameters: Type.Object({
        report: Type.Object({}, { additionalProperties: true, description: "结构化股票报告 JSON" }),
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
      "大盘复盘报告渲染 — 将结构化报告 JSON 通过 j2 模板渲染为 Markdown。report 字段以 schemas/market_review_schema.json 为准。save=true 时把报告对象追加归档到本地 JSONL（默认不保存不推送），纵向对比用 get_review_history",
      parameters: Type.Object({
        report: Type.Object({}, { additionalProperties: true, description: "结构化市场复盘" }),
        save: Type.Optional(Type.Boolean({ description: "是否归档到本地复盘 JSONL 存储，默认 false" })),
      }),
      async execute(_id, params) {
        const b64 = Buffer.from(JSON.stringify(params.report), "utf-8").toString("base64");
        const out = await runPy("report_renderer.py", [
          "market",
          "--input-b64",
          b64,
          ...(params.save ? ["--save"] : []),
        ]);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_review_history",
      description:
        "复盘归档历史 — 读取本地 JSONL 归档的最近 N 条市场复盘（最新在前），用于纵向对比（温度/姿态/主线变化）。归档由 render_market_report 的 save=true 写入，空存档返回空列表",
      parameters: Type.Object({
        limit: Type.Optional(Type.Number({ description: "返回条数，默认 10" })),
      }),
      async execute(_id, params) {
        const out = await runPy("market_review.py", ["history", "--limit", String(params.limit ?? 10)]);
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
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
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
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
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
      name: "record_signal",
      description:
        "记录一条 AI 分析建议信号（方向/入场价/目标价/止损价/持有期限），供后续胜率评估。信号持久化在本地 JSONL（$STOCK_SIGNAL_STORE 或 ~/.stock-analysis/signals.jsonl），宿主 Agent 自行决定何时 record/evaluate",
      parameters: Type.Object({
        symbol: Type.String({ description: "股票代码（A股如600519，美股如AAPL，港股如00700.HK）" }),
        direction: Type.Union([Type.Literal("buy"), Type.Literal("sell")], {
          description: "建议方向",
        }),
        entry_price: Type.Optional(Type.Number({ description: "入场价（可选）" })),
        target_price: Type.Optional(Type.Number({ description: "目标价（可选）" })),
        stop_price: Type.Optional(Type.Number({ description: "止损价（可选）" })),
        horizon_days: Type.Optional(
          Type.Number({ description: "持有期限（交易日），默认 10" })
        ),
        source: Type.Optional(
          Type.String({ description: "信号来源（如触发该建议的 skill 名）" })
        ),
        note: Type.Optional(Type.String({ description: "备注（可选）" })),
      }),
      async execute(_id, params) {
        const args = [
          "record",
          params.symbol,
          "--direction",
          params.direction,
          "--horizon-days",
          String(params.horizon_days ?? 10),
        ];
        if (params.entry_price !== undefined) args.push("--entry-price", String(params.entry_price));
        if (params.target_price !== undefined) args.push("--target-price", String(params.target_price));
        if (params.stop_price !== undefined) args.push("--stop-price", String(params.stop_price));
        if (params.source) args.push("--source", params.source);
        if (params.note) args.push("--note", params.note);
        const out = await runPy("signal_tracker.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "evaluate_signals",
      description:
        "评估到期信号：拉取记录日之后的日K，判定 target_hit/stop_hit/timeout 并计算收益，回写存储。记录建议用 record_signal，胜率复盘用 get_signal_summary",
      parameters: Type.Object({
        symbol: Type.Optional(
          Type.String({ description: "股票代码（可选，只评估该股的信号）" })
        ),
      }),
      async execute(_id, params) {
        const args = ["evaluate"];
        if (params.symbol) args.push("--symbol", params.symbol);
        const out = await runPy("signal_tracker.py", args);
        return asText(out);
      },
    });

    api.registerTool({
      name: "get_signal_summary",
      description:
        "信号胜率汇总（可按 source/symbol/status 过滤）：胜率、平均收益、结果分布与明细",
      parameters: Type.Object({
        source: Type.Optional(Type.String({ description: "按信号来源过滤（可选）" })),
        symbol: Type.Optional(Type.String({ description: "按股票代码过滤（可选）" })),
        status: Type.Optional(
          Type.Union(
            [Type.Literal("open"), Type.Literal("closed"), Type.Literal("all")],
            { description: "open=未结算，closed=已结算，all=全部（默认）" }
          )
        ),
      }),
      async execute(_id, params) {
        const args = ["summary"];
        if (params.source) args.push("--source", params.source);
        if (params.symbol) args.push("--symbol", params.symbol);
        args.push("--status", params.status ?? "all");
        const out = await runPy("signal_tracker.py", args);
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
