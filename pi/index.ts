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
      "获取股票K线数据（OHLCV）。支持A股（如600519）、港股（如00700.HK）、美股（如AAPL）",
    parameters: {
      type: "object",
      properties: {
        symbol: {
          type: "string",
          description: "股票代码，如 600519（A股）、00700.HK（港股）、AAPL（美股）",
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
      "获取股票实时行情报价。支持A股、港股、美股",
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
      "获取主要市场指数行情。CN: 上证/深证/创业板/科创50/沪深300；HK: 恒生/国企/科技；US: 道琼斯/纳斯达克/标普500",
    parameters: {
      type: "object",
      properties: {
        region: {
          type: "string",
          enum: ["cn", "hk", "us"],
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
      "获取股票基本信息（行业、板块、上市日期、总股本等）。A股返回板块/行业，HK/US返回行业/公司简介",
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
      "查询某日是否为交易日。支持CN（A股）、HK（港股）、US（美股）三市场",
    parameters: {
      type: "object",
      properties: {
        market: {
          type: "string",
          enum: ["CN", "HK", "US"],
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
      "获取最近/未来N个交易日列表。支持CN/HK/US三市场",
    parameters: {
      type: "object",
      properties: {
        market: {
          type: "string",
          enum: ["CN", "HK", "US"],
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
    ],
  }));
};
