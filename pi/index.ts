import type { ExtensionAPI } from "pi-agent";
import { existsSync } from "fs";

export default (pi: ExtensionAPI) => {
  const toolsDir = `${__dirname}/../tools`;
  const isWin = process.platform === "win32";
  const venvPython = `${__dirname}/../.venv/${isWin ? "Scripts" : "bin"}/python3`;
  const python = existsSync(venvPython) ? venvPython : "python3";
  const py = (script: string, args: string) =>
    pi.exec(`${python} ${toolsDir}/${script} ${args}`);

  // --- Data Tools ---

  pi.registerTool({
    name: "get_kline",
    description:
      "获取股票K线数据（OHLCV）。支持A股（如600519）、港股（如00700.HK）、美股（如AAPL）、日股（如7203.T）、韩股（如005930.KS）、台股（如2330.TW）及A股ETF",
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
    async execute({ symbol, period = "daily", count = 60 }) {
      const result = await py(
        "stock_data.py",
        `kline ${symbol} --period ${period} --count ${count}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_quote",
    description:
      "获取股票实时行情报价。支持A股、港股、美股、日股、韩股、台股",
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
    async execute({ symbol }) {
      const result = await py("stock_data.py", `quote ${symbol}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_capital_flow",
    description:
      "获取A股资金流向数据。detail=个股每日明细，summary=多日汇总+趋势，sector_flow=板块资金流排行",
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
    async execute({ symbol = "", mode = "detail" }) {
      const symArg = symbol ? ` ${symbol}` : "";
      const result = await py("stock_data.py", `capital_flow${symArg} --mode ${mode}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_news",
    description: "获取股票相关财经新闻",
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
    async execute({ symbol, days = 3 }) {
      const result = await py("stock_data.py", `news ${symbol} --days ${days}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_financials",
    description:
      "获取股票关键财务指标（PE/PB/市值/营收/净利润/ROE等）",
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
    async execute({ symbol }) {
      const result = await py("stock_data.py", `financials ${symbol}`);
      return result.stdout;
    },
  });

  // --- Analysis Tools ---

  pi.registerTool({
    name: "get_technical_analysis",
    description:
      "获取股票技术面分析（MA/MACD/RSI/BOLL/KDJ/成交量等指标 + 100分综合评分 + 6级买卖信号 + 趋势/偏离度/支撑压力位）",
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
    async execute({ symbol, period = "daily", count = 120 }) {
      const result = await py(
        "technical.py",
        `analyze ${symbol} --period ${period} --count ${count}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "analyze_pattern",
    description:
      "K线形态识别 — 检测十字星、锤子线、吞没、启明星、黄昏星、双底、20日突破等12+种经典形态",
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
    async execute({ symbol, period = "daily", days = 60 }) {
      const result = await py(
        "pattern.py",
        `analyze ${symbol} --period ${period} --days ${days}`
      );
      return result.stdout;
    },
  });

  // --- Market Tools ---

  pi.registerTool({
    name: "get_market_indices",
    description:
      "获取主要市场指数行情。CN: 上证/深证/创业板/科创50/沪深300；HK: 恒生/国企/科技；US: 道琼斯/纳斯达克/标普500；JP: 日经225/东证；KR: KOSPI/KOSDAQ；TW: 台湾加权",
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
    async execute({ region = "cn" }) {
      const result = await py(
        "stock_data.py",
        `market_indices --region ${region}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_sector_rankings",
    description: "获取A股行业板块涨跌幅排行（含领涨股、涨跌家数等）。支持查看涨幅榜/跌幅榜/双向",
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
    async execute({ top = 10, direction = "top" }) {
      const result = await py(
        "stock_data.py",
        `sector_rankings --top ${top} --direction ${direction}`
      );
      return result.stdout;
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
    async execute({ symbol }) {
      const result = await py("stock_data.py", `stock_info ${symbol}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_chip_distribution",
    description:
      "获取A股筹码分布数据（获利比例、平均成本、90%/70%成本集中度）。仅支持A股",
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
    async execute({ symbol }) {
      const result = await py("stock_data.py", `chip_distribution ${symbol}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_market_stats",
    description:
      "获取A股市场整体统计（涨跌家数、涨停跌停数、平均涨幅、涨跌Top5、总成交额）",
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
    async execute({ market = "A" }) {
      const result = await py("stock_data.py", `market_stats --market ${market}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_fundamental_context",
    description:
      "获取A股深度基本面（估值PE/PB/PS + 成长性营收/净利增速 + 盈利能力ROE/毛利率 + 分红历史）",
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
    async execute({ symbol }) {
      const result = await py("stock_data.py", `fundamental_context ${symbol}`);
      return result.stdout;
    },
  });

  // --- Screener & Backtest ---

  pi.registerTool({
    name: "screen_stocks",
    description:
      "全市场股票筛选（AlphaSift L1 多因子硬筛）。按PE/PB/市值/换手率/涨跌幅/量比等因子过滤和评分",
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
    async execute({ market = "A", top = 20, config }) {
      const configArg = config ? ` --config ${config}` : "";
      const result = await py(
        "screener.py",
        `screen --market ${market} --top ${top}${configArg}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "run_backtest",
    description:
      "策略回测（AlphaEvo）。读取YAML策略定义，在历史数据上模拟交易，输出收益率/回撤/胜率等指标",
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
    async execute({ strategy, symbol, start, end, capital = 1000000 }) {
      const startArg = start ? ` --start ${start}` : "";
      const endArg = end ? ` --end ${end}` : "";
      const result = await py(
        "backtest.py",
        `run ${strategy} ${symbol}${startArg}${endArg} --capital ${capital}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "evaluate_signal",
    description:
      "技术信号历史准确率评估 — 回溯历史数据，统计某个技术信号触发后N日的胜率和平均收益。支持9种信号：macd_golden_cross/macd_death_cross/rsi_oversold/rsi_overbought/breakout_20d/breakdown_20d/volume_surge/ma_golden_cross/ma_death_cross",
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
    async execute({ symbol, signal, forward_days = "3,5,10", lookback = 250 }) {
      const result = await py(
        "backtest.py",
        `evaluate_signal ${symbol} ${signal} --forward ${forward_days} --lookback ${lookback}`
      );
      return result.stdout;
    },
  });

  // --- Name Resolution ---

  pi.registerTool({
    name: "resolve_stock_name",
    description:
      "股票名称智能解析 — 输入中文名（贵州茅台）、拼音（guizhou maotai/gzmt）、部分代码，返回匹配的股票代码。仅支持A股",
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
    async execute({ query, top = 5 }) {
      const result = await py(
        "name_resolver.py",
        `resolve "${query}" --top ${top}`
      );
      return result.stdout;
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
    async execute({ market, date }) {
      const dateArg = date ? ` --date ${date}` : "";
      const result = await py(
        "trading_calendar.py",
        `check ${market}${dateArg}`
      );
      return result.stdout;
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
    async execute({ market, direction = "next", count = 5, date }) {
      const dateArg = date ? ` --date ${date}` : "";
      const result = await py(
        "trading_calendar.py",
        `${direction} ${market} --count ${count}${dateArg}`
      );
      return result.stdout;
    },
  });

  // --- Standalone MA Calculator ---

  pi.registerTool({
    name: "calculate_ma",
    description:
      "独立均线计算器 — 支持任意周期MA（5/10/20/30/60/120/250或自定义）+ 偏离度 + 均线排列 + 金叉死叉检测",
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
    async execute({ symbol, periods = "5,10,20,30,60,120,250", kline_period = "daily" }) {
      const result = await py(
        "technical.py",
        `calculate_ma ${symbol} --periods ${periods} --period ${kline_period} --count 300`
      );
      return result.stdout;
    },
  });

  // --- Volume-Price Analysis ---

  pi.registerTool({
    name: "get_volume_analysis",
    description:
      "独立量价分析 — 量价相关性、上涨/下跌日成交量对比、量能趋势、量价模式解读（放量上涨/缩量回调等）",
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
    async execute({ symbol, period = "daily", count = 60 }) {
      const result = await py(
        "volume_analysis.py",
        `analyze ${symbol} --period ${period} --count ${count}`
      );
      return result.stdout;
    },
  });

  // --- Search Intelligence ---

  pi.registerTool({
    name: "search_stock_news",
    description:
      "多引擎股票新闻搜索（支持 Tavily/Brave/SerpAPI）。需配置对应 API Key 环境变量",
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
    async execute({ query, count = 10 }) {
      const result = await py(
        "search_intel.py",
        `search "${query}" --count ${count}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "search_comprehensive_intel",
    description:
      "股票综合情报搜索 — 从6个维度（新闻/公告/行情分析/风险/业绩/行业）搜索综合信息",
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
    async execute({ symbol, name }) {
      const nameArg = name ? ` --name "${name}"` : "";
      const result = await py(
        "search_intel.py",
        `comprehensive ${symbol}${nameArg}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_social_sentiment",
    description:
      "获取股票社交媒体情绪数据（Reddit/X/Polymarket）。主要支持美股。需配置 SENTIMENT_API_KEY",
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
    async execute({ symbol }) {
      const result = await py("search_intel.py", `sentiment ${symbol}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_trending_sentiment",
    description:
      "获取社交媒体热门趋势（Reddit/X/Polymarket热门股票讨论）。数据缓存10分钟。适用于发现市场热点",
    parameters: {
      type: "object",
      properties: {},
    },
    async execute() {
      const result = await py("search_intel.py", "trending");
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "extract_article",
    description:
      "网页文章全文提取 — 输入URL，提取文章标题、正文（最多3000字）、作者、发布日期等。适用于深度阅读搜索结果中的新闻/研报",
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
    async execute({ url }) {
      const result = await py("search_intel.py", `extract "${url}"`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "screen_risk",
    description:
      "风险专项筛查 — 7维度风险检测（估值极端/技术预警/解禁到期/内部人减持/业绩预警/监管处罚/行业政策），返回风险评级和一票否决标记",
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
    async execute({ symbol, name }) {
      const nameArg = name ? ` --name "${name}"` : "";
      const result = await py(
        "risk_screening.py",
        `screen ${symbol}${nameArg}`,
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "detect_market_regime",
    description:
      "市场状态检测 — 分析大盘指数判断当前市场阶段（上涨趋势/下跌趋势/横盘震荡/高波动/板块热点），并推荐适合的分析策略",
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
    async execute({ market }) {
      const m = market || "A";
      const result = await py("market_regime.py", `detect ${m}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_market_review",
    description:
      "大盘复盘 — 获取市场日度复盘数据，包含指数、涨跌统计、板块排名、新闻、市场温度与策略建议",
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
    async execute({ market }) {
      const m = market || "A";
      const result = await py("market_review.py", `review --market ${m}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "run_watchlist_analysis",
    description:
      "批量自选股分析 — 对多只股票并行采集行情/技术/资金/风险等数据，返回汇总结果。适用于每日定时分析自选股列表",
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
    async execute({ symbols, workers = 3 }) {
      const result = await py(
        "watchlist.py",
        `analyze ${symbols} --workers ${workers}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "detect_anomaly",
    description:
      "异常/事件检测 — 一键扫描股票当前所有异动信号（MACD金叉死叉、RSI超买超卖、20日突破、放量异动、涨跌停、布林突破、KDJ极值、资金异动等），返回结构化异常列表",
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
    async execute({ symbol }) {
      const result = await py("anomaly_detect.py", `detect ${symbol}`);
      return result.stdout;
    },
  });

  // --- Diagnostics & Capabilities ---

  pi.registerTool({
    name: "diagnose_data_sources",
    description:
      "数据源诊断 — 检查当前环境可用的数据 provider（akshare/tushare/yfinance/finnhub/longbridge/alphavantage），输出每个市场的可用链路、缺失 env、warnings。用于让 agent 自解释为何拿不到数据",
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
    async execute({ market = "all" }) {
      const result = await py("diagnostics.py", `check --market ${market}`);
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "get_market_capabilities",
    description:
      "市场能力边界 — 返回指定市场支持/不支持的工具列表，避免 agent 对港股调 get_chip_distribution 或对美股调 get_capital_flow 后编造数据",
    parameters: {
      type: "object",
      properties: {
        market: { type: "string", enum: ["A", "HK", "US", "JP", "KR", "TW"], description: "市场代码" },
        symbol: { type: "string", description: "股票代码（自动识别市场，与 market 二选一）" },
      },
    },
    async execute({ market, symbol }) {
      const arg = symbol ? `--symbol ${symbol}` : `--market ${market || "A"}`;
      const result = await py("capabilities.py", `get ${arg}`);
      return result.stdout;
    },
  });

  // --- Report Rendering ---

  pi.registerTool({
    name: "render_stock_report",
    description:
      "股票分析报告渲染 — 将结构化 JSON（符合 schemas/report_schema.json）通过 j2 模板渲染为 Markdown。style: brief|full。仅渲染，不保存不推送",
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
    async execute({ report, template = "full" }) {
      const b64 = Buffer.from(JSON.stringify(report), "utf-8").toString("base64");
      const result = await py(
        "report_renderer.py",
        `stock --template ${template} --input-b64 ${b64}`
      );
      return result.stdout;
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
    async execute({ report, template = "full" }) {
      const b64 = Buffer.from(JSON.stringify(report), "utf-8").toString("base64");
      const result = await py(
        "report_renderer.py",
        `market --template ${template} --input-b64 ${b64}`
      );
      return result.stdout;
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
    async execute({ symbols, include_market_review = false, workers = 3 }) {
      const flag = include_market_review ? " --include-market-review" : "";
      const result = await py(
        "watchlist_context.py",
        `build ${symbols} --workers ${workers}${flag}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "analyze_position_context",
    description:
      "持仓上下文分析 — 输入成本/仓位/止损止盈，结合现价和技术位输出浮盈亏、离止损距离、风险级别、操作建议。无状态、不存账户",
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
    async execute({ symbol, cost, quantity, stop_loss, take_profit }) {
      const slArg = stop_loss !== undefined ? ` --stop-loss ${stop_loss}` : "";
      const tpArg = take_profit !== undefined ? ` --take-profit ${take_profit}` : "";
      const result = await py(
        "position_context.py",
        `analyze ${symbol} --cost ${cost} --quantity ${quantity}${slArg}${tpArg}`
      );
      return result.stdout;
    },
  });

  pi.registerTool({
    name: "check_alert_rules",
    description:
      "无状态告警规则检查 — 传入规则数组，返回当前是否触发。规则类型：price_below/price_above/change_pct_above/change_pct_below/volume_ratio_above/anomaly/risk_veto/risk_level_at_least。不做调度不存历史",
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
    async execute({ symbol, rules }) {
      const b64 = Buffer.from(JSON.stringify(rules), "utf-8").toString("base64");
      const result = await py("alert_rules.py", `check ${symbol} --rules-b64 ${b64}`);
      return result.stdout;
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
    async execute({ text }) {
      const b64 = Buffer.from(String(text), "utf-8").toString("base64");
      const result = await py("import_parser.py", `parse --text-b64 ${b64}`);
      return result.stdout;
    },
  });

  // --- Skill Discovery ---

  pi.on("resources_discover", () => ({
    skillPaths: [
      `${__dirname}/../skills/stock-analysis/SKILL.md`,
      `${__dirname}/../skills/stock-screener/SKILL.md`,
      `${__dirname}/../skills/strategy-backtest/SKILL.md`,
      `${__dirname}/../skills/bull-trend/SKILL.md`,
      `${__dirname}/../skills/shrink-pullback/SKILL.md`,
      `${__dirname}/../skills/ma-crossover/SKILL.md`,
      `${__dirname}/../skills/volume-breakout/SKILL.md`,
      `${__dirname}/../skills/bottom-volume/SKILL.md`,
      `${__dirname}/../skills/dragon-head/SKILL.md`,
      `${__dirname}/../skills/chan-theory/SKILL.md`,
      `${__dirname}/../skills/wave-theory/SKILL.md`,
      `${__dirname}/../skills/box-oscillation/SKILL.md`,
      `${__dirname}/../skills/emotion-cycle/SKILL.md`,
      `${__dirname}/../skills/one-yang-three-yin/SKILL.md`,
      `${__dirname}/../skills/wisburg-research/SKILL.md`,
      `${__dirname}/../skills/market-review/SKILL.md`,
      `${__dirname}/../skills/event-driven/SKILL.md`,
      `${__dirname}/../skills/expectation-repricing/SKILL.md`,
      `${__dirname}/../skills/growth-quality/SKILL.md`,
      `${__dirname}/../skills/hot-theme/SKILL.md`,
    ],
  }));
};
