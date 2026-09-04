import type { ExtensionAPI } from "pi-agent";
import { existsSync } from "fs";
import { fileURLToPath } from "url";

export default (pi: ExtensionAPI) => {
  // Resolve the plugin root robustly: __dirname under jiti/CJS-style loaders,
  // else import.meta.url under native ESM (where __dirname is undefined).
  const baseDir =
    typeof __dirname !== "undefined"
      ? `${__dirname}/..`
      : fileURLToPath(new URL("..", import.meta.url));
  const toolsDir = `${baseDir}/tools`;
  const isWin = process.platform === "win32";
  // Windows venvs ship Scripts/python.exe (there is no python3); POSIX venvs ship bin/python3.
  const venvPython = `${baseDir}/.venv/${isWin ? "Scripts/python.exe" : "bin/python3"}`;
  // Fallback: on Windows "python3" is usually the Microsoft Store stub — prefer "python".
  const python = existsSync(venvPython) ? venvPython : isWin ? "python" : "python3";
  const py = (script: string, args: string[]) =>
    pi.exec(python, [`${toolsDir}/${script}`, ...args]);
  const asText = (text: string) => ({
    content: [{ type: "text" as const, text }],
    details: {},
  });

  // --- Data Tools ---

  pi.registerTool({
    name: "get_kline",
    description:
      "获取股票K线数据（OHLCV）。支持A股（如600519）、港股（如00700.HK）、美股（如AAPL）、日股（如7203.T）、韩股（如005930.KS）、台股（如2330.TW）及A股ETF。需要原始历史价格自行计算或画图时用；只问指标用 get_technical_analysis，只问现价用 get_quote",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码，如 600519（A股）、00700.HK（港股）、AAPL（美股）、7203.T（日股）、005930.KS（韩股）、2330.TW（台股）",
        },
        period: {
          type: "string",
          enum: ["daily", "weekly", "monthly"],
          description: "K线周期，默认 daily",
        },
        count: {
          type: "number",
          description: "返回数据条数，默认 60",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol, period = "daily", count = 60 }) {
      const result = await py("stock_data.py", [
        "kline",
        symbol,
        "--period",
        period,
        "--count",
        String(count),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_quote",
    description:
      "获取股票实时行情报价（现价、涨跌幅、量比等）。支持A股、港股、美股、日股、韩股、台股。问“现在多少钱/涨了多少”用本工具",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol }) {
      const result = await py("stock_data.py", ["quote", symbol]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_capital_flow",
    description:
      "获取A股资金流向（主力/超大单/大单/中单/小单净流入）。detail=个股每日明细，summary=多日汇总+趋势，sector_flow=板块资金流排行。问“主力在买还是卖/资金流入流出”用本工具，可配合 get_chip_distribution 验证",
    parameters: {
      type: "object",
      properties: {
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
    },
    async execute(_id, { symbol = "", mode = "detail" }) {
      const result = await py("stock_data.py", [
        "capital_flow",
        ...(symbol ? [symbol] : []),
        "--mode",
        mode,
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_news",
    description: "获取个股最近N天财经新闻快讯（轻量、无需配置）。需要深度全网情报用 search_comprehensive_intel，按主题搜索用 search_stock_news，读文章全文用 extract_article",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
        days: {
          type: "number",
          description: "获取最近几天的新闻，默认 3",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol, days = 3 }) {
      const result = await py("stock_data.py", ["news", symbol, "--days", String(days)]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_financials",
    description:
      "获取股票关键财务指标（PE/PB/市值/营收/净利润/ROE等），适合快速估值快查。A股深度基本面（成长性/盈利能力/分红）用 get_fundamental_context",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol }) {
      const result = await py("stock_data.py", ["financials", symbol]);
      return asText(result.stdout);
    },
  });

  // --- Analysis Tools ---

  pi.registerTool({
    name: "get_technical_analysis",
    description:
      "获取股票技术面分析（MA/MACD/RSI/BOLL/KDJ/成交量等指标 + 100分综合评分 + 6级买卖信号 + 趋势/偏离度/支撑压力位）。“能买吗/现在能入场吗/技术面怎么样”首选本工具；只要均线数值或自定义周期用 calculate_ma，专问量价用 get_volume_analysis，扫当日异动用 detect_anomaly",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
        period: {
          type: "string",
          enum: ["daily", "weekly", "monthly"],
          description: "分析周期，默认 daily",
        },
        count: {
          type: "number",
          description: "用于计算指标的K线条数，默认 120",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol, period = "daily", count = 120 }) {
      const result = await py("technical.py", [
        "analyze",
        symbol,
        "--period",
        period,
        "--count",
        String(count),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "analyze_pattern",
    description:
      "K线形态识别 — 检测十字星、锤子线、吞没、启明星、黄昏星、双底、20日突破等12+种经典形态。问“出现了什么形态”时用；综合技术面判断用 get_technical_analysis",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
        period: {
          type: "string",
          enum: ["daily", "weekly", "monthly"],
          description: "K线周期，默认 daily",
        },
        days: {
          type: "number",
          description: "分析的K线天数，默认 60",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol, period = "daily", days = 60 }) {
      const result = await py("pattern.py", [
        "analyze",
        symbol,
        "--period",
        period,
        "--days",
        String(days),
      ]);
      return asText(result.stdout);
    },
  });

  // --- Market Tools ---

  pi.registerTool({
    name: "get_market_indices",
    description:
      "获取主要市场指数行情。CN: 上证/深证/创业板/科创50/沪深300；HK: 恒生/国企/科技；US: 道琼斯/纳斯达克/标普500；JP: 日经225/东证；KR: KOSPI/KOSDAQ；TW: 台湾加权。“复盘今天大盘”用 get_market_review 一站式获取",
    parameters: {
      type: "object",
      properties: {
        region: {
          type: "string",
          enum: ["cn", "hk", "us", "jp", "kr", "tw"],
          description: "市场区域，默认 cn",
        },
      },
    },
    async execute(_id, { region = "cn" }) {
      const result = await py("stock_data.py", [
        "market_indices",
        "--region",
        region,
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_sector_rankings",
    description: "获取A股行业板块涨跌幅排行（含领涨股、涨跌家数等）。支持查看涨幅榜/跌幅榜/双向。找热点板块/领涨板块用本工具",
    parameters: {
      type: "object",
      properties: {
        top: {
          type: "number",
          description: "返回排名前N的板块，默认 10",
        },
        direction: {
          type: "string",
          enum: ["top", "bottom", "both"],
          description: "top=涨幅榜（默认），bottom=跌幅榜，both=双向",
        },
      },
    },
    async execute(_id, { top = 10, direction = "top" }) {
      const result = await py("stock_data.py", [
        "sector_rankings",
        "--top",
        String(top),
        "--direction",
        direction,
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_stock_info",
    description:
      "获取股票基本信息（行业、板块、上市日期、总股本等）。A股返回板块/行业，其他市场返回行业/公司简介",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol }) {
      const result = await py("stock_data.py", ["stock_info", symbol]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_chip_distribution",
    description:
      "获取A股筹码分布数据（获利比例、平均成本、90%/70%成本集中度）。仅支持A股。与 get_capital_flow 配合验证主力行为",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "A股股票代码，如 600519",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol }) {
      const result = await py("stock_data.py", ["chip_distribution", symbol]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_market_stats",
    description:
      "获取A股市场整体统计（涨跌家数、涨停跌停数、平均涨幅、涨跌Top5、总成交额）。问“今天市场情绪/温度如何”用本工具",
    parameters: {
      type: "object",
      properties: {
        market: {
          type: "string",
          enum: ["A"],
          description: "市场，目前仅支持 A",
        },
      },
    },
    async execute(_id, { market = "A" }) {
      const result = await py("stock_data.py", ["market_stats", "--market", market]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_fundamental_context",
    description:
      "获取A股深度基本面（估值PE/PB/PS + 成长性营收/净利增速 + 盈利能力ROE/毛利率 + 分红历史）。“公司质地如何/能不能长期持有”用本工具；快速查PE/PB用 get_financials",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "A股股票代码，如 600519",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol }) {
      const result = await py("stock_data.py", ["fundamental_context", symbol]);
      return asText(result.stdout);
    },
  });

  // --- Screener & Backtest ---

  pi.registerTool({
    name: "screen_stocks",
    description:
      "全市场股票筛选（AlphaSift L1 多因子硬筛）。按PE/PB/市值/换手率/涨跌幅/量比等因子过滤和评分。“帮我选股/筛选低估值/高换手股票”用本工具",
    parameters: {
      type: "object",
      properties: {
        market: {
          type: "string",
          enum: ["A", "HK", "US"],
          description: "市场，默认 A",
        },
        top: {
          type: "number",
          description: "返回排名前N的股票，默认 20",
        },
        config: {
          type: "string",
          description: "自定义筛选配置YAML文件路径（可选）",
        },
      },
    },
    async execute(_id, { market = "A", top = 20, config }) {
      const result = await py("screener.py", [
        "screen",
        "--market",
        market,
        "--top",
        String(top),
        ...(config ? ["--config", config] : []),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "run_backtest",
    description:
      "策略回测（AlphaEvo）。读取YAML策略定义，在历史数据上模拟交易，输出收益率/回撤/胜率等指标。只想知道单个技术信号的历史胜率用更轻量的 evaluate_signal",
    parameters: {
      type: "object",
      properties: {
        strategy: {
          type: "string",
          description: "策略YAML文件路径",
        },
        symbol: {
          type: "string",
          description: "股票代码",
        },
        start: {
          type: "string",
          description: "回测起始日期，格式 YYYY-MM-DD",
        },
        end: {
          type: "string",
          description: "回测结束日期，格式 YYYY-MM-DD",
        },
        capital: {
          type: "number",
          description: "初始资金，默认 1000000",
        },
      },
      required: ["strategy", "symbol"],
    },
    async execute(_id, { strategy, symbol, start, end, capital = 1000000 }) {
      const result = await py("backtest.py", [
        "run",
        strategy,
        symbol,
        ...(start ? ["--start", start] : []),
        ...(end ? ["--end", end] : []),
        "--capital",
        String(capital),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "evaluate_signal",
    description:
      "技术信号历史准确率评估 — 回溯历史数据，统计某个技术信号触发后N日的胜率和平均收益。支持9种信号：macd_golden_cross/macd_death_cross/rsi_oversold/rsi_overbought/breakout_20d/breakdown_20d/volume_surge/ma_golden_cross/ma_death_cross。“金叉/突破策略靠不靠谱”用本工具；要完整模拟交易过程用 run_backtest",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
        signal: {
          type: "string",
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
        lookback: {
          type: "number",
          description: "回溯K线条数，默认 250（约1年）",
        },
      },
      required: ["symbol", "signal"],
    },
    async execute(_id, { symbol, signal, forward_days = "3,5,10", lookback = 250 }) {
      const result = await py("backtest.py", [
        "evaluate_signal",
        symbol,
        signal,
        "--forward",
        String(forward_days),
        "--lookback",
        String(lookback),
      ]);
      return asText(result.stdout);
    },
  });

  // --- Name Resolution ---

  pi.registerTool({
    name: "resolve_stock_name",
    description:
      "股票名称智能解析 — 输入中文名（贵州茅台）、拼音（guizhou maotai/gzmt）、部分代码，返回匹配的股票代码。仅支持A股。用户给出中文名/拼音/部分代码时先调本工具换成代码再调其他工具",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "股票名称、拼音、拼音首字母或部分代码",
        },
        top: {
          type: "number",
          description: "返回匹配数量，默认 5",
        },
      },
      required: ["query"],
    },
    async execute(_id, { query, top = 5 }) {
      const result = await py("name_resolver.py", [
        "resolve",
        query,
        "--top",
        String(top),
      ]);
      return asText(result.stdout);
    },
  });

  // --- Trading Calendar ---

  pi.registerTool({
    name: "check_trading_day",
    description:
      "查询某日是否为交易日。支持CN（A股）、HK（港股）、US（美股）、JP（日股）、KR（韩股）、TW（台股）",
    parameters: {
      type: "object",
      properties: {
        market: {
          type: "string",
          enum: ["CN", "HK", "US", "JP", "KR", "TW"],
          description: "市场",
        },
        date: {
          type: "string",
          description: "日期（YYYY-MM-DD），不填则查今天",
        },
      },
      required: ["market"],
    },
    async execute(_id, { market, date }) {
      const result = await py("trading_calendar.py", [
        "check",
        market,
        ...(date ? ["--date", date] : []),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_trading_days",
    description:
      "获取最近/未来N个交易日列表。支持CN/HK/US/JP/KR/TW",
    parameters: {
      type: "object",
      properties: {
        market: {
          type: "string",
          enum: ["CN", "HK", "US", "JP", "KR", "TW"],
          description: "市场",
        },
        direction: {
          type: "string",
          enum: ["next", "prev"],
          description: "next=未来交易日, prev=过去交易日",
        },
        count: {
          type: "number",
          description: "返回天数，默认 5",
        },
        date: {
          type: "string",
          description: "起始日期（YYYY-MM-DD），默认今天",
        },
      },
      required: ["market"],
    },
    async execute(_id, { market, direction = "next", count = 5, date }) {
      const result = await py("trading_calendar.py", [
        direction,
        market,
        "--count",
        String(count),
        ...(date ? ["--date", date] : []),
      ]);
      return asText(result.stdout);
    },
  });

  // --- Standalone MA Calculator ---

  pi.registerTool({
    name: "calculate_ma",
    description:
      "独立均线计算器 — 支持任意周期MA（5/10/20/30/60/120/250或自定义）+ 偏离度 + 均线排列 + 金叉死叉检测。只要均线数值或需要30/120/250等非默认周期时用本工具；综合技术面分析用 get_technical_analysis",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
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
      required: ["symbol"],
    },
    async execute(_id, { symbol, periods = "5,10,20,30,60,120,250", kline_period = "daily" }) {
      const result = await py("technical.py", [
        "calculate_ma",
        symbol,
        "--periods",
        periods,
        "--period",
        kline_period,
        "--count",
        "300",
      ]);
      return asText(result.stdout);
    },
  });

  // --- Volume-Price Analysis ---

  pi.registerTool({
    name: "get_volume_analysis",
    description:
      "独立量价分析 — 量价相关性、上涨/下跌日成交量对比、量能趋势、量价模式解读（放量上涨/缩量回调等）。专问量能/量价配合时用本工具；综合技术面判断用 get_technical_analysis，扫当日异动用 detect_anomaly",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
        period: {
          type: "string",
          enum: ["daily", "weekly", "monthly"],
          description: "K线周期，默认 daily",
        },
        count: {
          type: "number",
          description: "分析K线条数，默认 60",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol, period = "daily", count = 60 }) {
      const result = await py("volume_analysis.py", [
        "analyze",
        symbol,
        "--period",
        period,
        "--count",
        String(count),
      ]);
      return asText(result.stdout);
    },
  });

  // --- Search Intelligence ---

  pi.registerTool({
    name: "search_stock_news",
    description:
      "多引擎股票新闻搜索（支持 Tavily/Brave/SerpAPI）。需配置对应 API Key 环境变量。get_news 快讯不够或要按主题搜索时用本工具；深度6维情报用 search_comprehensive_intel",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "搜索关键词",
        },
        count: {
          type: "number",
          description: "返回结果数量，默认 10",
        },
      },
      required: ["query"],
    },
    async execute(_id, { query, count = 10 }) {
      const result = await py("search_intel.py", [
        "search",
        query,
        "--count",
        String(count),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "search_comprehensive_intel",
    description:
      "股票综合情报搜索 — 从6个维度（新闻/公告/行情分析/风险/业绩/行业）搜索综合信息。“深入研究/全面调研这家公司”用本工具；快速看新闻用 get_news",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码",
        },
        name: {
          type: "string",
          description: "股票名称（可选，提升搜索准确度）",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol, name }) {
      const result = await py("search_intel.py", [
        "comprehensive",
        symbol,
        ...(name ? ["--name", name] : []),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_social_sentiment",
    description:
      "获取股票社交媒体情绪数据。A股：东方财富股吧热度+雪球讨论热度（无需配置）；美股/港股：Reddit/X/Polymarket情绪（需 SENTIMENT_API_KEY）。自动按市场选数据源。“散户在讨论什么/情绪如何”用本工具；看全市场热门讨论用 get_trending_sentiment",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码（如 AAPL）",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol }) {
      const result = await py("search_intel.py", ["sentiment", symbol]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_trending_sentiment",
    description:
      "获取社交媒体热门趋势（Reddit/X/Polymarket热门股票讨论）。数据缓存10分钟。“现在市场热点是什么/大家都在买什么”用本工具；查个股情绪用 get_social_sentiment",
    parameters: {
      type: "object",
      properties: {},
    },
    async execute() {
      const result = await py("search_intel.py", ["trending"]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "extract_article",
    description:
      "网页文章全文提取 — 输入URL，提取文章标题、正文（最多3000字）、作者、发布日期等。配合 get_news/search_stock_news 的搜索结果做深度阅读",
    parameters: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description: "文章URL",
        },
      },
      required: ["url"],
    },
    async execute(_id, { url }) {
      const result = await py("search_intel.py", ["extract", url]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "screen_risk",
    description:
      "风险专项筛查 — 7维度风险检测（估值极端/技术预警/解禁到期/内部人减持/业绩预警/监管处罚/行业政策），返回风险评级和一票否决标记。“这股票有什么雷”用本工具；入场决策前排雷必调",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码（如 600519）",
        },
        name: {
          type: "string",
          description: "股票名称（可选，提升新闻搜索准确度）",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol, name }) {
      const result = await py("risk_screening.py", [
        "screen",
        symbol,
        ...(name ? ["--name", name] : []),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "detect_market_regime",
    description:
      "市场状态检测 — 分析大盘指数判断当前市场阶段（上涨趋势/下跌趋势/横盘震荡/高波动/板块热点），并推荐适合的分析策略。“现在大盘能进场吗/该用什么策略”用本工具",
    parameters: {
      type: "object",
      properties: {
        market: {
          type: "string",
          enum: ["A", "HK", "US"],
          description: "市场代码，默认 A",
        },
      },
    },
    async execute(_id, { market }) {
      const m = market || "A";
      const result = await py("market_regime.py", ["detect", m]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_market_review",
    description:
      "大盘复盘 — 获取市场日度复盘数据，包含指数、涨跌统计、板块排名、新闻、市场温度与策略建议。“复盘一下今天市场/今天大盘怎么样”首选本工具，无需再分别调指数/统计/板块工具",
    parameters: {
      type: "object",
      properties: {
        market: {
          type: "string",
          enum: ["A", "HK", "US", "all"],
          description: "市场代码，默认 A。all 表示所有市场",
        },
      },
    },
    async execute(_id, { market }) {
      const m = market || "A";
      const result = await py("market_review.py", ["review", "--market", m]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "run_watchlist_analysis",
    description:
      "批量自选股分析 — 对多只股票并行采集行情/技术/资金/风险等数据，返回汇总结果。“批量分析我的自选股”/每日定时分析用本工具",
    parameters: {
      type: "object",
      properties: {
        symbols: {
          type: "string",
          description: "逗号分隔的股票代码列表，如 600519,000001,300750",
        },
        workers: {
          type: "number",
          description: "并发数，默认 3（建议不超过5，避免API限流）",
        },
      },
      required: ["symbols"],
    },
    async execute(_id, { symbols, workers = 3 }) {
      const result = await py("watchlist.py", [
        "analyze",
        symbols,
        "--workers",
        String(workers),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "detect_anomaly",
    description:
      "异常/事件检测 — 一键扫描股票当前所有异动信号（MACD金叉死叉、RSI超买超卖、20日突破、放量异动、涨跌停、布林突破、KDJ极值、资金异动等），返回结构化异常列表。“今天有什么异动/为什么大涨大跌”首选本工具；综合技术面判断用 get_technical_analysis",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码（如 600519、AAPL、00700.HK）",
        },
      },
      required: ["symbol"],
    },
    async execute(_id, { symbol }) {
      const result = await py("anomaly_detect.py", ["detect", symbol]);
      return asText(result.stdout);
    },
  });

  // --- Diagnostics & Capabilities ---

  pi.registerTool({
    name: "diagnose_data_sources",
    description:
      "数据源诊断 — 检查当前环境可用的数据 provider（akshare/tushare/yfinance/finnhub/longbridge/alphavantage），输出每个市场的可用链路、缺失 env、warnings。工具拿不到数据或报错时调用排查",
    parameters: {
      type: "object",
      properties: {
        market: {
          type: "string",
          enum: ["A", "HK", "US", "JP", "KR", "TW", "all"],
          description: "市场，默认 all",
        },
      },
    },
    async execute(_id, { market = "all" }) {
      const result = await py("diagnostics.py", ["check", "--market", market]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "get_market_capabilities",
    description:
      "市场能力边界 — 返回指定市场支持/不支持的工具列表。不确定某市场能否用某工具（如港股的筹码分布、美股的资金流）时先调本工具，避免调用不支持的工具后编造数据",
    parameters: {
      type: "object",
      properties: {
        market: { type: "string", enum: ["A", "HK", "US", "JP", "KR", "TW"], description: "市场代码" },
        symbol: { type: "string", description: "股票代码（自动识别市场，与 market 二选一）" },
      },
    },
    async execute(_id, { market, symbol }) {
      const args = symbol
        ? ["get", "--symbol", symbol]
        : ["get", "--market", market || "A"];
      const result = await py("capabilities.py", args);
      return asText(result.stdout);
    },
  });

  // --- Report Rendering ---

  pi.registerTool({
    name: "render_stock_report",
    description:
      "股票分析报告渲染 — 将结构化 JSON（符合 schemas/report_schema.json）通过 j2 模板渲染为 Markdown。template: brief|full。全部分析完成后的最后一步调用。仅渲染，不保存不推送",
    parameters: {
      type: "object",
      properties: {
        report: {
          type: "object",
          description: "结构化股票报告，字段参考 schemas/report_schema.json",
        },
        template: {
          type: "string",
          enum: ["brief", "full"],
          description: "模板类型，默认 full",
        },
      },
      required: ["report"],
    },
    async execute(_id, { report, template = "full" }) {
      const b64 = Buffer.from(JSON.stringify(report), "utf-8").toString("base64");
      const result = await py("report_renderer.py", [
        "stock",
        "--template",
        template,
        "--input-b64",
        b64,
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "render_market_report",
    description:
      "大盘复盘报告渲染 — 将结构化 JSON（符合 schemas/market_review_schema.json）通过 j2 模板渲染为 Markdown",
    parameters: {
      type: "object",
      properties: {
        report: { type: "object", description: "结构化市场复盘" },
        template: { type: "string", enum: ["full"], description: "模板类型，默认 full" },
      },
      required: ["report"],
    },
    async execute(_id, { report, template = "full" }) {
      const b64 = Buffer.from(JSON.stringify(report), "utf-8").toString("base64");
      const result = await py("report_renderer.py", [
        "market",
        "--template",
        template,
        "--input-b64",
        b64,
      ]);
      return asText(result.stdout);
    },
  });

  // --- Watchlist / Position / Alert Context ---

  pi.registerTool({
    name: "build_watchlist_context",
    description:
      "自选股上下文包 — 对多只股票输出评分/趋势/异常/风险/建议 next_tools 的 agent 友好摘要。宿主 agent 决定如何写日报或深入分析",
    parameters: {
      type: "object",
      properties: {
        symbols: { type: "string", description: "逗号分隔的股票代码列表" },
        include_market_review: {
          type: "boolean",
          description: "是否附带各市场复盘，默认 false",
        },
        workers: { type: "number", description: "并发数，默认 3" },
      },
      required: ["symbols"],
    },
    async execute(_id, { symbols, include_market_review = false, workers = 3 }) {
      const result = await py("watchlist_context.py", [
        "build",
        symbols,
        "--workers",
        String(workers),
        ...(include_market_review ? ["--include-market-review"] : []),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "analyze_position_context",
    description:
      "持仓上下文分析 — 输入成本/仓位/止损止盈，结合现价和技术位输出浮盈亏、离止损距离、风险级别、操作建议。“我XX成本买的被套了/要不要止损止盈”用本工具。无状态、不存账户",
    parameters: {
      type: "object",
      properties: {
        symbol: { type: "string", description: "股票代码" },
        cost: { type: "number", description: "成本价" },
        quantity: { type: "number", description: "持仓数量" },
        stop_loss: { type: "number", description: "止损价（可选）" },
        take_profit: { type: "number", description: "止盈价（可选）" },
      },
      required: ["symbol", "cost", "quantity"],
    },
    async execute(_id, { symbol, cost, quantity, stop_loss, take_profit }) {
      const result = await py("position_context.py", [
        "analyze",
        symbol,
        "--cost",
        String(cost),
        "--quantity",
        String(quantity),
        ...(stop_loss !== undefined ? ["--stop-loss", String(stop_loss)] : []),
        ...(take_profit !== undefined ? ["--take-profit", String(take_profit)] : []),
      ]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "check_alert_rules",
    description:
      "无状态告警规则检查 — 传入规则数组，返回当前是否触发。规则类型：price_below/price_above/change_pct_above/change_pct_below/volume_ratio_above/anomaly/risk_veto/risk_level_at_least。“到价/涨跌幅提醒是否触发”用本工具。不做调度不存历史",
    parameters: {
      type: "object",
      properties: {
        symbol: { type: "string", description: "股票代码" },
        rules: {
          type: "array",
          description: "规则列表，每项 { type, value }",
          items: {
            type: "object",
            properties: {
              type: { type: "string" },
              value: {},
            },
          },
        },
      },
      required: ["symbol", "rules"],
    },
    async execute(_id, { symbol, rules }) {
      const b64 = Buffer.from(JSON.stringify(rules), "utf-8").toString("base64");
      const result = await py("alert_rules.py", ["check", symbol, "--rules-b64", b64]);
      return asText(result.stdout);
    },
  });

  pi.registerTool({
    name: "parse_stock_list",
    description:
      "自选股/文本导入解析 — 从自然语言、CSV、Markdown 表格提取股票，自动识别 A 股 6 位代码、港股 xxxxx.HK、美股 ticker、日股 xxxx.T、韩股 xxxxxx.KS/KQ、台股 xxxx.TW，并调用 name_resolver 处理中文股票名",
    parameters: {
      type: "object",
      properties: {
        text: { type: "string", description: "待解析的文本" },
      },
      required: ["text"],
    },
    async execute(_id, { text }) {
      const b64 = Buffer.from(String(text), "utf-8").toString("base64");
      const result = await py("import_parser.py", ["parse", "--text-b64", b64]);
      return asText(result.stdout);
    },
  });

  // --- Skill Discovery ---

  pi.on("resources_discover", () => ({
    skillPaths: [
      `${baseDir}/skills/stock-analysis/SKILL.md`,
      `${baseDir}/skills/stock-screener/SKILL.md`,
      `${baseDir}/skills/strategy-backtest/SKILL.md`,
      `${baseDir}/skills/bull-trend/SKILL.md`,
      `${baseDir}/skills/shrink-pullback/SKILL.md`,
      `${baseDir}/skills/ma-crossover/SKILL.md`,
      `${baseDir}/skills/volume-breakout/SKILL.md`,
      `${baseDir}/skills/bottom-volume/SKILL.md`,
      `${baseDir}/skills/dragon-head/SKILL.md`,
      `${baseDir}/skills/chan-theory/SKILL.md`,
      `${baseDir}/skills/wave-theory/SKILL.md`,
      `${baseDir}/skills/box-oscillation/SKILL.md`,
      `${baseDir}/skills/emotion-cycle/SKILL.md`,
      `${baseDir}/skills/one-yang-three-yin/SKILL.md`,
      `${baseDir}/skills/wisburg-research/SKILL.md`,
      `${baseDir}/skills/market-review/SKILL.md`,
      `${baseDir}/skills/event-driven/SKILL.md`,
      `${baseDir}/skills/expectation-repricing/SKILL.md`,
      `${baseDir}/skills/growth-quality/SKILL.md`,
      `${baseDir}/skills/hot-theme/SKILL.md`,
    ],
  }));
};
