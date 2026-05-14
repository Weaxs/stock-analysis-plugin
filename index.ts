import type { ExtensionAPI } from "pi-agent";

export default (pi: ExtensionAPI) => {
  const toolsDir = `${__dirname}/tools`;
  const py = (script: string, args: string) =>
    pi.exec(`python3 ${toolsDir}/${script} ${args}`);

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
      "获取A股个股资金流向数据（主力/超大单/大单/中单/小单净流入）。仅支持A股",
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
      const result = await py("stock_data.py", `capital_flow ${symbol}`);
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
      "获取股票技术面分析（MA/MACD/RSI/BOLL/KDJ/成交量等指标 + 趋势信号 + 支撑压力位）",
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

  // --- Skill Discovery ---

  pi.on("resources_discover", () => ({
    skillPaths: [
      `${__dirname}/skills/stock-analysis/SKILL.md`,
      `${__dirname}/skills/stock-screener/SKILL.md`,
      `${__dirname}/skills/strategy-backtest/SKILL.md`,
      `${__dirname}/skills/bull-trend/SKILL.md`,
      `${__dirname}/skills/shrink-pullback/SKILL.md`,
      `${__dirname}/skills/ma-crossover/SKILL.md`,
      `${__dirname}/skills/volume-breakout/SKILL.md`,
      `${__dirname}/skills/bottom-volume/SKILL.md`,
      `${__dirname}/skills/dragon-head/SKILL.md`,
      `${__dirname}/skills/chan-theory/SKILL.md`,
      `${__dirname}/skills/wave-theory/SKILL.md`,
      `${__dirname}/skills/box-oscillation/SKILL.md`,
      `${__dirname}/skills/emotion-cycle/SKILL.md`,
      `${__dirname}/skills/one-yang-three-yin/SKILL.md`,
    ],
  }));
};
