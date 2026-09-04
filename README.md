# stock-analysis-plugin

[![npm](https://img.shields.io/npm/v/@weaxs/stock-analysis-plugin?label=npm&logo=npm)](https://www.npmjs.com/package/@weaxs/stock-analysis-plugin)
[![PyPI](https://img.shields.io/pypi/v/stock-analysis-plugin?label=pypi&logo=pypi&logoColor=white)](https://pypi.org/project/stock-analysis-plugin/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E=3.10-blue?logo=python&logoColor=white)](https://www.python.org/)

A 股 / 港股 / 美股 / 日股 / 韩股 / 台股综合分析、多因子选股和策略回测工具集，可作为 [Pi Agent](https://github.com/anthropics/pi-agent) Extension、[Hermes Agent](https://github.com/NousResearch/hermes-agent) Plugin、[OpenClaw](https://docs.openclaw.ai/) Plugin，或 [DeepSeek Harness (dsh)](https://github.com/deepseek-ai/deepseek-harness) Plugin 使用。

底层共享同一套 Python CLI 工具和 SKILL.md 工作流，上层分别适配四个平台的注册机制。

## 目录

- [功能概览](#功能概览)
- [安装](#安装)
  - [Pi Agent Extension](#pi-agent-extension)
  - [Hermes Agent Plugin](#hermes-agent-plugin)
  - [OpenClaw Plugin](#openclaw-plugin)
  - [dsh (DeepSeek Harness) Plugin](#dsh-deepseek-harness-plugin)
  - [Python 依赖](#python-依赖)
- [快速开始](#快速开始)
- [Skills 一览](#skills-一览)
- [工具列表](#工具列表)
- [策略回测 DSL](#策略回测-dsl)
- [市场路由规则](#市场路由规则)
- [独立 CLI 使用](#独立-cli-使用)
- [项目结构](#项目结构)
- [致谢](#致谢)
- [常见问题](#常见问题)
- [License](#license)

## 功能概览

- **39 个工具** — 行情数据、技术分析、K 线形态、资金流向、财务指标、新闻舆情、风险筛查、市场状态等
- **20 个 Skills** — 综合分析、全市场选股、策略回测 + 17 个策略方法论（缠论、波浪、龙头、情绪周期等）
- **策略回测引擎** — YAML DSL 定义策略，参数化条件组合，自动诊断 + LLM 变异优化
- **多数据源 Failover** — 9 个数据源自动容灾切换（akshare / tushare / efinance / pytdx / baostock / yfinance / finnhub / longbridge / alphavantage）
- **社交舆情增强** — A 股（东财股吧 + 雪球）/ 美港股（Reddit / X / Polymarket），市场自动路由
- **四平台适配** — 同一套工具同时支持 Pi Agent、Hermes Agent、OpenClaw 和 dsh

## 安装

> 所有 npm 包同时以 scoped（`@weaxs/*`）和非 scoped 同名两种名字发布，内容一致，任选其一即可。

插件包地址：

| 平台 | 包名 | 地址 | 安装命令 |
|------|------|------|----------|
| Pi Agent | `@weaxs/stock-analysis-plugin`（或 `stock-analysis-plugin`） | [npm](https://www.npmjs.com/package/@weaxs/stock-analysis-plugin) | `npm install @weaxs/stock-analysis-plugin` |
| Hermes Agent | `stock-analysis-plugin` | [PyPI](https://pypi.org/project/stock-analysis-plugin/) | `pip install stock-analysis-plugin` |
| OpenClaw | `@weaxs/openclaw-stock-analysis`（或 `openclaw-stock-analysis`） | [npm](https://www.npmjs.com/package/@weaxs/openclaw-stock-analysis) | `openclaw plugins install clawhub:@weaxs/openclaw-stock-analysis` |
| DeepSeek Harness (dsh) | `@weaxs/dsh-stock-analysis`（或 `dsh-stock-analysis`） | [npm](https://www.npmjs.com/package/@weaxs/dsh-stock-analysis) | `dsh plugin --profile <profile> add @weaxs/dsh-stock-analysis` |

各平台的详细安装步骤见下方对应小节。

### Pi Agent Extension

```bash
# 全局安装
cd ~/.pi/agent/extensions
npm install @weaxs/stock-analysis-plugin

# 或项目级安装
mkdir -p .pi/extensions && cd .pi/extensions
npm install @weaxs/stock-analysis-plugin
```

> 也可以直接 `git clone git@github.com:Weaxs/stock-analysis-plugin.git` 使用源码版本。

Pi 启动后自动加载，输入 `/skills` 确认。详见 [Pi Agent 接入指南](docs/pi-integration.md)（[English](docs/pi-integration.en.md)）。

### Hermes Agent Plugin

```bash
pip install stock-analysis-plugin
```

安装后 Hermes 通过 `hermes_agent.plugins` entry point 自动发现并注册，无需手动复制目录。

或在 Python 中直接注册：

```python
from hermes import register
register(ctx)
```

详见 [Hermes Agent 接入指南](docs/hermes-integration.md)（[English](docs/hermes-integration.en.md)）。

### OpenClaw Plugin

```bash
openclaw plugins install clawhub:@weaxs/openclaw-stock-analysis
```

OpenClaw Gateway 启动后自动加载并注册 39 个 tool。安装时 postinstall 会自动建 `.venv` 并装好 Python 依赖（前提：本机有 `python3 >= 3.9`）。

详见 [OpenClaw 接入指南](docs/openclaw-integration.md)，或 plugin 自身说明 [`openclaw/README.md`](openclaw/README.md)。

### dsh (DeepSeek Harness) Plugin

```bash
dsh plugin --profile <你的profile> add @weaxs/dsh-stock-analysis
dsh --profile <你的profile>
```

作为 dsh bundle 安装：`dsh plugin add` 会把 `cordis.patch.yml` 叠加进 profile 组合，注册 39 个 tool + 20 个 skill。postinstall 自动建 `.venv`（pnpm 需在 profile 的 `pnpm-workspace.yaml` 里给本包配 `allowBuilds`，否则回退系统 `python3`）。

详见 [`dsh/README.md`](dsh/README.md)。

### Python 依赖

```bash
pip install -r tools/requirements.txt
```

主要依赖：

| 包 | 用途 |
|---|------|
| akshare | A 股数据源（行情、资金流、财务、新闻，含 A 股 ETF） |
| yfinance | 港股 / 美股 / 日股 / 韩股 / 台股数据源 |
| pandas / numpy | 数据处理和数值计算 |
| exchange-calendars | 交易日历（A/HK/US/JP/KR/TW） |
| newspaper4k | 新闻正文提取 |
| pypinyin | 股票名称拼音搜索 |
| pytdx | A 股 TDX 行情（免费兜底源） |
| longport | 港股 / 美股 Longbridge SDK |

### 环境变量

**数据源（可选，配置后自动加入 Failover 链）：**

| 环境变量 | 用途 |
|---------|------|
| `TUSHARE_TOKEN` | Tushare 数据源（A 股 K 线 / 行情） |
| `LONGBRIDGE_APP_KEY` | Longbridge SDK（港股 / 美股） |
| `LONGBRIDGE_APP_SECRET` | Longbridge SDK |
| `LONGBRIDGE_ACCESS_TOKEN` | Longbridge SDK |
| `ALPHAVANTAGE_API_KEY` | Alpha Vantage（美股 K 线 / 行情） |
| `FINNHUB_API_KEY` | Finnhub（港股 / 美股） |

**搜索引擎（可选，配置任一即可）：**

| 环境变量 | 用途 |
|---------|------|
| `TAVILY_API_KEY` | Tavily 搜索 |
| `BRAVE_API_KEY` | Brave 搜索 |
| `SERPAPI_KEY` | SerpAPI |
| `BOCHA_API_KEY` | Bocha AI 搜索 |
| `SEARXNG_BASE_URLS` | SearXNG 自建实例地址（逗号分隔多个，无需 Key，兜底用） |

**社交舆情（可选）：**

| 环境变量 | 用途 |
|---------|------|
| `SENTIMENT_API_URL` | 舆情 API 地址（默认 `https://api.adanos.org`） |
| `SENTIMENT_API_KEY` | 舆情 API 密钥 |

## 快速开始

### 综合分析

```
/skill:stock-analysis

帮我分析贵州茅台（600519）
```

Agent 会自动调用行情 → 技术面 → 基本面 → 资金面 → 消息面，输出多维度研报。

### 全市场选股

```
/skill:stock-screener

帮我从 A 股中筛选出低估值+高成长的股票，要求 PE < 30、PB < 5、市值 > 100 亿
```

### 策略回测

```
/skill:strategy-backtest

用 RSI 超卖反弹策略回测茅台，时间段 2024 年全年
```

### 特定策略视角

```
/skill:chan-theory
用缠论分析一下 600519 当前的走势结构

/skill:wave-theory
用波浪理论分析 AAPL 的浪形位置
```

## Skills 一览

### 核心 Workflow（3 个）

| Skill | 调用方式 | 功能 |
|-------|----------|------|
| 综合分析 | `/skill:stock-analysis` | 技术面 + 基本面 + 资金面 + 消息面多维研判，输出结构化研报 |
| 全市场选股 | `/skill:stock-screener` | L1 多因子硬筛（市场情绪调节，可选 `--l2` 量化增强）→ L2 LLM 智能排序 → 输出推荐列表 |
| 策略回测 | `/skill:strategy-backtest` | YAML 策略定义 → 回测 → 诊断 → LLM 变异优化 → 迭代 |

### 策略方法论（12 个）

| Skill | 调用方式 | 方法论 |
|-------|----------|--------|
| 趋势追踪 | `/skill:bull-trend` | 均线排列 + MACD 方向 + 趋势阶段判断 |
| 缩量回调 | `/skill:shrink-pullback` | 上升趋势中的缩量洗盘识别 |
| 均线金叉 | `/skill:ma-crossover` | MA5/10/20/60 交叉信号 + 金叉质量评估 |
| 放量突破 | `/skill:volume-breakout` | 关键阻力位的放量突破确认 |
| 底部放量 | `/skill:bottom-volume` | 底部区域异动放量 → 主力建仓信号 |
| 龙头战法 | `/skill:dragon-head` | 板块龙头识别 + 龙头 vs 跟风判断 |
| 缠论 | `/skill:chan-theory` | 笔 → 段 → 中枢 → 背驰 → 买卖点 |
| 波浪理论 | `/skill:wave-theory` | Elliott 5 浪推动 + 3 浪调整 + 斐波那契 |
| 箱体震荡 | `/skill:box-oscillation` | 箱体区间识别 + 支撑压力间波段操作 |
| 情绪周期 | `/skill:emotion-cycle` | 市场情绪冰点 → 回暖 → 狂热 → 退潮周期判断 |
| 一阳穿三阴 | `/skill:one-yang-three-yin` | K 线形态识别 + 反转信号质量评级 |
| Wisburg 研报 | `/skill:wisburg-research` | 结构化投研报告生成 |

## 工具列表

### 数据获取

| 工具 | 说明 |
|------|------|
| `get_kline` | K 线数据（OHLCV），支持日/周/月线 |
| `get_quote` | 实时行情快照 |
| `get_capital_flow` | 资金流向（仅 A 股） |
| `get_news` | 个股相关新闻 |
| `get_financials` | 财务指标 |
| `get_stock_info` | 股票基本信息 |
| `get_chip_distribution` | 筹码分布 |
| `get_market_indices` | 主要指数行情 |
| `get_sector_rankings` | 板块涨跌排行 |
| `get_market_stats` | 市场统计（涨跌家数、涨停等） |
| `get_fundamental_context` | 基本面综合上下文 |

### 分析计算

| 工具 | 说明 |
|------|------|
| `get_technical_analysis` | 全面技术指标分析（MA/MACD/RSI/BOLL/KDJ） |
| `analyze_pattern` | K 线形态识别 |
| `calculate_ma` | 多周期均线计算 |
| `get_volume_analysis` | 量价分析 |
| `evaluate_signal` | 历史信号胜率回测 |

### 筛选与回测

| 工具 | 说明 |
|------|------|
| `screen_stocks` | 全市场多因子筛选（市场情绪调节，可选 L2 量化增强） |
| `screen_risk` | 风险因子筛查 |
| `run_backtest` | YAML 策略回测 |
| `detect_market_regime` | 市场状态检测（牛/熊/震荡） |

### 搜索与舆情

| 工具 | 说明 |
|------|------|
| `search_stock_news` | 关键词搜索新闻 |
| `search_comprehensive_intel` | 综合情报搜索 |
| `get_social_sentiment` | 社交媒体情绪（A 股：东财股吧 + 雪球；美港股：Reddit / X / Polymarket） |
| `get_trending_sentiment` | 社交媒体热门趋势聚合（Reddit / X / Polymarket，10 分钟缓存） |
| `extract_article` | 网页正文提取 |

### 辅助

| 工具 | 说明 |
|------|------|
| `resolve_stock_name` | 股票名称 / 拼音模糊匹配 |
| `check_trading_day` | 查询是否交易日 |
| `get_trading_days` | 获取前后 N 个交易日 |

## 策略回测 DSL

策略以 YAML 格式定义，支持参数化和条件组合：

```yaml
name: "RSI Oversold Bounce"
version: "1.0"
description: "RSI超卖反弹策略"

parameters:
  rsi_period: 14
  rsi_oversold: 30
  rsi_overbought: 70
  stop_loss: -0.05
  take_profit: 0.15

entry:
  conditions:
    - indicator: "rsi"
      period: "{rsi_period}"
      operator: "<"
      value: "{rsi_oversold}"
    - indicator: "volume_ratio"
      operator: ">"
      value: 1.0
  logic: "all"

exit:
  conditions:
    - indicator: "rsi"
      period: "{rsi_period}"
      operator: ">"
      value: "{rsi_overbought}"
  logic: "any"
  stop_loss: "{stop_loss}"
  take_profit: "{take_profit}"

position:
  size: 1.0
  max_positions: 1
```

### 可用指标

| 指标 | 说明 | 需要 period |
|------|------|-------------|
| `rsi` | RSI 相对强弱指数 | 是（默认 14） |
| `ma` / `ema` | 简单 / 指数移动平均 | 是（默认 20） |
| `macd_dif` / `macd_dea` / `macd` | MACD 三线 | 否 |
| `volume_ratio` | 量比 | 是（默认 5） |
| `price_change` | 涨跌幅（%） | 否 |
| `bollinger_position` | 布林带位置（0=下轨, 1=上轨） | 是（默认 20） |
| `close` / `volume` | 收盘价 / 成交量 | 否 |

### 可用运算符

`>` `<` `>=` `<=` `==` `cross_above`（上穿） `cross_below`（下穿）

示例策略见 `strategies/examples/`。

## 市场路由规则

| 代码格式 | 示例 | 市场 | 数据源 Failover 链 |
|----------|------|------|---------------------|
| 6 位纯数字 | `600519`, `000001`, `300750` | A 股 | akshare → tushare → efinance → pytdx → baostock |
| 6 位纯数字（51/52/56/58/15/16/18 开头） | `510300`, `159915` | A 股 ETF | akshare（基金接口）→ tushare → efinance → pytdx → baostock |
| `.HK` 结尾 | `00700.HK`, `09988.HK` | 港股 | yfinance → finnhub → longbridge |
| 英文字母 | `AAPL`, `GOOGL`, `TSLA` | 美股 | yfinance → finnhub → longbridge → alphavantage |
| `.T` 结尾 | `7203.T` | 日股 | yfinance |
| `.KS` / `.KQ` 结尾 | `005930.KS`, `035720.KQ` | 韩股 | yfinance |
| `.TW` / `.TWO` 结尾 | `2330.TW`, `6510.TWO` | 台股 | yfinance |

> 各数据源按优先级自动尝试，前一个失败后自动切换到下一个。未配置相关 env var 的数据源会被跳过。
> 日/韩/台股仅 yfinance 一个数据源（免费数据延迟约 15 分钟）；港股 / 美股 / 日股 / 韩股 / 台股的 ETF 与其所在市场股票走相同链路。

## 独立 CLI 使用

Python 工具可脱离 Agent 独立使用，所有输出为 JSON：

```bash
# 行情数据
python tools/stock_data.py kline 600519
python tools/stock_data.py kline AAPL --period weekly --count 30
python tools/stock_data.py quote 600519
python tools/stock_data.py capital_flow 600519
python tools/stock_data.py news 600519 --days 5
python tools/stock_data.py financials AAPL

# 技术分析
python tools/technical.py analyze 600519
python tools/technical.py analyze AAPL --period weekly

# 全市场筛选
python tools/screener.py screen --market A --top 20
python tools/screener.py screen --market A --top 20 --l2
python tools/screener.py screen --market A --config my_filter.yaml

# 策略回测
python tools/backtest.py run strategies/examples/rsi_oversold.yaml 600519
python tools/backtest.py run strategies/examples/rsi_oversold.yaml 600519 --start 2024-01-01 --end 2025-01-01

# 搜索与舆情
python tools/search_intel.py search "茅台 业绩"
python tools/search_intel.py comprehensive 600519
python tools/search_intel.py sentiment 600519
python tools/search_intel.py sentiment AAPL
python tools/search_intel.py trending

# 风险筛查
python tools/risk_screening.py screen 600519

# 市场状态
python tools/market_regime.py detect A
```

## 项目结构

```
stock-analysis/
├── pi/                              # Pi Agent Extension
│   └── index.ts                     #   注册 39 个工具 + 20 个 skill
├── hermes/                          # Hermes Agent Plugin
│   ├── plugin.yaml                  #   插件清单
│   ├── __init__.py                  #   register(ctx) 入口
│   ├── schemas.py                   #   工具 JSON Schema 定义
│   └── tools.py                     #   39 个 handler → subprocess 调 CLI
├── openclaw/                        # OpenClaw Plugin
│   ├── openclaw.plugin.json         #   manifest（contracts.tools）
│   ├── package.json                 #   含 openclaw 块（pluginApi/SDK 版本）
│   └── index.ts                     #   definePluginEntry + registerTool ×39
├── dsh/                             # DeepSeek Harness (dsh) Plugin
│   ├── cordis.patch.yml             #   bundle patch（insert 插件入口）
│   ├── package.json                 #   含 dsh.bundle 块（bundle manifest）
│   └── index.ts                     #   cordis apply(ctx) + defineTool ×39
│
├── tools/                           # 共享 Python CLI 工具（12 个脚本）
│   ├── stock_data.py                #   行情 / 资金流 / 新闻 / 财务
│   ├── technical.py                 #   技术指标分析
│   ├── pattern.py                   #   K 线形态识别
│   ├── screener.py                  #   全市场多因子筛选（含市场情绪调节，--l2 量化增强）
│   ├── backtest.py                  #   策略回测引擎
│   ├── volume_analysis.py           #   量价分析
│   ├── search_intel.py              #   搜索 / 舆情 / 正文提取
│   ├── risk_screening.py            #   风险因子筛查
│   ├── market_regime.py             #   市场状态检测
│   ├── name_resolver.py             #   股票名称模糊匹配
│   ├── trading_calendar.py          #   交易日历
│   └── requirements.txt
│
├── skills/                          # 共享 SKILL.md（20 个）
│   ├── stock-analysis/              #   综合分析 workflow
│   ├── stock-screener/              #   选股 workflow
│   ├── strategy-backtest/           #   回测 + 进化 workflow
│   └── ...                          #   12 个策略方法论
│
├── strategies/examples/             # 回测策略 YAML 示例
├── schemas/                         # JSON Schema（研报结构等）
├── tests/                           # pytest 测试
├── package.json                     # npm / Pi 配置
└── tsconfig.json
```

### 数据流

```
用户 (自然语言)
    │
    ▼
Agent (Pi / Hermes / OpenClaw / dsh)
    │
    ├── 加载 SKILL.md → 分析框架 / 流程指引
    │
    ├── 调用工具 → pi/index.ts 或 hermes/tools.py 或 openclaw/index.ts 或 dsh/index.ts → python3 tools/xxx.py → JSON
    │
    ▼
Agent (LLM 分析 + 生成报告)
    │
    ▼
用户看到结构化分析结果
```

## 致谢

- [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) — A 股每日分析工具，本项目的综合分析流程参考了其设计思路
- [akshare](https://github.com/akfamily/akshare) — A 股数据源
- [yfinance](https://github.com/ranaroussi/yfinance) — 港股 / 美股数据源
- [pytdx](https://github.com/rainx/pytdx) — 通达信 TDX 协议（A 股免费兜底）
- [longport](https://github.com/longportapp/openapi-sdk) — Longbridge OpenAPI（港股 / 美股）
- [exchange-calendars](https://github.com/gerrymanoim/exchange_calendars) — 全球交易日历

## 常见问题

### Q: Python 工具报错 `ModuleNotFoundError`？

```bash
pip install -r tools/requirements.txt
```

如果使用 virtualenv / conda，确保 Agent 启动时能访问到对应的 Python 环境。

### Q: A 股数据获取失败？

系统会自动按 akshare → tushare → efinance → pytdx → baostock 顺序尝试。如果所有源都失败，检查：网络问题、akshare 版本过低（`pip install --upgrade akshare`）、或非交易时间。配置 `TUSHARE_TOKEN` 可增加一个可靠数据源。

### Q: 港股 / 美股数据有延迟？

yfinance 免费数据通常有 15 分钟延迟。配置 Longbridge（`LONGBRIDGE_APP_KEY` 等）或 Finnhub（`FINNHUB_API_KEY`）可获取更实时的数据，且作为 failover 备用源。

### Q: 如何自定义筛选条件？

创建 YAML 配置：

```yaml
name: "我的选股策略"
filters:
  pe_min: 5
  pe_max: 25
  pb_max: 3
  market_cap_min: 20e9
sort_by: "composite_score"
sort_order: "desc"
```

CLI 使用：`python tools/screener.py screen --config my_filter.yaml --top 30`

### Q: 如何编写自定义回测策略？

参考 `strategies/examples/` 下的示例和 [策略回测 DSL](#策略回测-dsl) 章节。也可以直接让 Agent 帮你编写 YAML 策略。

## License

MIT
