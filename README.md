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
- [CI 真实宿主测试](#ci-真实宿主测试)
- [项目结构](#项目结构)
- [致谢](#致谢)
- [常见问题](#常见问题)
- [License](#license)

## 功能概览

- **70 个工具** — 行情数据、技术分析、K 线形态、资金流向、估值历史、财报日历、股东/ETF/股息、新闻舆情、风险筛查、市场状态等
- **21 个 Skills** — 综合分析、全市场选股、策略回测 + 18 个策略、复盘与研究方法论（缠论、波浪、龙头、情绪周期等）
- **策略回测引擎** — YAML DSL 定义策略，参数化条件组合，自动诊断，按需进行有限轮参数优化
- **多数据源 Failover** — 11 个数据源自动容灾切换（akshare / tushare / efinance / 腾讯行情 / 新浪行情 / pytdx / baostock / yfinance / finnhub / longbridge / alphavantage），A 股链路带粘性优选（最近成功的数据源下次优先尝试）
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

新版 Pi（已核验 1.2.0）提供原生 `tool_search` 时，插件自动启用按需工具发现，70 项能力保留注册，仅命中工具加载完整定义。缺少原生发现接口的旧版 Pi 或 SDK 宿主保持全量暴露。检索使用英文关键词或工具名，例如 `get_quote`、`get_financials`。

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

OpenClaw Gateway 启动后自动加载并注册 70 个 tool。安装时 postinstall 会自动建 `.venv` 并装好 Python 依赖（前提：本机有 `python3 >= 3.10`）。

详见 [OpenClaw 接入指南](docs/openclaw-integration.md)，或 plugin 自身说明 [`openclaw/README.md`](openclaw/README.md)。

### dsh (DeepSeek Harness) Plugin

```bash
dsh plugin --profile <你的profile> add @weaxs/dsh-stock-analysis
dsh --profile <你的profile>
```

作为 dsh bundle 安装：`dsh plugin add` 会把 `cordis.patch.yml` 叠加进 profile 组合，注册 70 个 tool + 21 个 skill。postinstall 自动建 `.venv`（pnpm 需在 profile 的 `pnpm-workspace.yaml` 里给本包配 `allowBuilds`，否则回退系统 `python3`）。

新版 dsh 的原生函数调用模式默认通过 `stock_tool_search` 按 Agent 搜索、加载所需工具，支持中文关键词或工具名；旧接口、搜索入口不可用时保留全量暴露。可设置 `toolDiscovery: false` 关闭，配置与 PTC 模式边界见下方接入文档。

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

> A 股行情另有**腾讯（qt.gtimg.cn）/ 新浪（hq.sinajs.cn）**两个免鉴权兜底源，直接 HTTP 调用，用于东财域名不可达的网络环境（境外主机、企业出网限制等）。港股实时行情也使用腾讯作为 yfinance 之后的免 key 备用源。

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
| `XUEQIU_TOKEN` | 雪球（A 股个股所属板块反查，东财不可用时的兜底） |

**搜索引擎（可选，配置任一即可）：**

| 环境变量 | 用途 |
|---------|------|
| `LIXINGER_TOKEN` | 理杏仁可选数据源：指数 PB 与港股非金融公司财务/每日估值（需对应权限） |
| `SEC_USER_AGENT` | SEC EDGAR 身份声明：应用名称与真实联系邮箱，13F/Form 4 必需 |
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
| 策略回测 | `/skill:strategy-backtest` | YAML 策略定义 → 回测 → 诊断；按需进行最多 3 轮参数优化 |

### 策略、复盘与研究（18 个）

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
| 事件驱动 | `/skill:event-driven` | 公告/政策/业绩事件的影响路径与失效条件 |
| 预期重估 | `/skill:expectation-repricing` | 市场预期与实际数据的偏差 |
| 成长质量 | `/skill:growth-quality` | 收入、利润、现金流与盈利能力的多期验证 |
| 行业 ETF 轮动研究 | `/skill:sector-rotation` | 确定性集中度/配权计算、跨市场分组的配权分项回测；完整深度模型尚未实现 |
| 热点题材 | `/skill:hot-theme` | 板块强度、扩散度、关联度与节奏 |
| 市场复盘 | `/skill:market-review` | 指数、宽度、板块、温度与次日策略 |
| Wisburg 投研 | `/skill:wisburg-research` | 检索机构研报、公告、电话会纪要和市场日报 |

## 工具列表

### 数据获取

| 工具 | 说明 |
|------|------|
| `get_announcements` | A 股巨潮公告查询：类别/日期范围/标题/原文 PDF 链接 |
| `get_us_holdings` | SEC 机构组合 / 发行人 CIK 反查机构：精确 CUSIP 映射、季度数据与修订处理（需 SEC_USER_AGENT） |
| `get_insider_trades` | SEC 股票 ticker/发行人 CIK 的 Form 4 内部人交易、代码和脚注 |
| `get_valuation_history` | A/HK PE-TTM/PB 历史、正值分位与估值 Band，实际采样窗口 |
| `get_index_valuation` | 中国指数 PE/PB 历史水位；中证官方多年滚动 PE；可选理杏仁补充 PB |
| `get_earnings_calendar` | A 股报告期披露与业绩预告；HK 已公告业绩审批董事会排期／供应商备选日期 |
| `get_shareholder_structure` | A 股股东户数/十大流通股东变化/报告期北向名义持股；HK 南向持股 |
| `get_etf_details` | A 股 ETF 净值折溢价、规模变动、披露持仓、可选基准成分与跟踪误差 |
| `get_dividends` | A/HK 历史分红/除息日历/已实现 TTM 股息与连续年数；A/HK 报告期/年度排行 |
| `get_kline` | K 线数据（OHLCV），支持日/周/月线；A 股股票/ETF 前复权，每条记录 `adjusted=qfq`，指数 `adjusted=none` |
| `get_quote` | 实时行情快照（非交易时段返回最近交易日收盘价并以 as_of/stale 标注；`pe_basis` 区分 dynamic/trailing/unknown；ETF 的 premium_discount_rate 为正=溢价、负=折价） |
| `get_capital_flow` | 资金流向（仅 A 股） |
| `get_news` | 个股相关新闻 |
| `get_financials` | 财务指标 |
| `get_stock_info` | 股票基本信息 |
| `get_chip_distribution` | 筹码分布 |
| `get_market_indices` | 主要指数行情 |
| `get_sector_rankings` | 板块涨跌排行（行业/概念） |
| `get_sector_constituents` | 板块成分股查询（仅 A 股，板块名模糊匹配） |
| `resolve_stock_sectors` | 个股所属板块查询（A 股行业+概念 / 港股 GICS） |
| `get_market_stats` | 市场统计（涨跌家数、涨停等） |
| `get_limit_up_pool` | 涨停池/涨停板复盘（连板数、封板资金、炸板次数等，仅 A 股；非交易日自动回退到最近交易日并以 requested_date/stale 标注） |
| `get_dragon_tiger` | 龙虎榜（净买额/上榜原因/解读，仅 A 股；非交易日自动回退到最近交易日并以 requested_date/stale 标注） |
| `get_hot_stocks` | 全市场人气热搜榜（东财→百度，仅 A 股） |
| `get_margin_trading` | 个股融资融券明细（融资余额/融资买入额/融券余量等，沪深交易所官方数据，仅 A 股两融标的；金额单位为元，short_*_shares 单位为股） |
| `get_northbound_flow` | 北向资金市场级净买入序列（东财沪深港通历史数据；2024-08-16 起交易所停止披露日度净买额，仅 2024-08 之前历史数据可查，仅 A 股） |
| `get_fundamental_context` | 基本面综合上下文 |

### 分析计算

| 工具 | 说明 |
|------|------|
| `calculate_rotation_metrics` | 真实MC-dropout分布集中度与历史原始配权计算；不生成预测或买入信号 |
| `backtest_rotation_allocation` | 股票/ETF代码自动取数，按市场与报价币种独立比较等权和历史配权；分项回测，非完整论文模型 |
| `analyze_trend_strategy` | 月度右侧趋势策略：单市场股票池筛选、真实旧仓保持/退出与逆波动新仓预算；直接传股票代码、自动取行情，无持仓存储，仍为not_validated |
| `get_technical_analysis` | 全面技术指标分析（MA/MACD/RSI/BOLL/KDJ；附实验性 trend_horizons 多期限月度趋势及独立趋势温度 trend_temperature；多周期共振时 resonance.direction ∈ aligned_bullish/aligned_bearish/divergent） |
| `analyze_pattern` | K 线形态识别 |
| `calculate_ma` | 多周期均线计算 |
| `get_volume_analysis` | 量价分析 |
| `evaluate_signal` | 历史信号胜率回测 |
| `detect_anomaly` | 当日技术、量价与资金异动扫描 |

`trend_temperature` 是独立趋势温度指标：`(EMA20-EMA60)/ATR14`，ATR 使用包含跳空的真实波幅简单均值。附分数、EMA差方向 `direction`、七档研究标签、前一期信息、数据日期和价格口径；`validation_status=not_validated`，真实股票试验未支持默认温度交易映射。方向按零轴计算，“平”标签不代表零方向；默认需 60 根预热，零 ATR 或坏行情返回不可用。±0.5/±1.5/±3 的分档尚未校准，不是趋势动物公式、肥尾指标、上涨概率或自动买卖信号，也不修改原综合评分。无需个人持仓存储；[公式、边界及回测说明](skills/bull-trend/references/trend-temperature.md)。

月度右侧趋势策略，通过 `analyze_trend_strategy(symbols, ...)` 调用：月初200日线上行入场、真实旧仓优先、63日波动预算；[输入格式与完整规则](skills/bull-trend/references/monthly-trend-strategy.md)。扩展及历史样本尚未通过完整效果门槛；直接提供股票代码即可自动取日K，输出信号与预算计划；实际成交/股数和共享资金回测另行核实，不自动保存持仓或下单。

趋势研究另返回 `trend_horizons`（daily、建议count=360）：21/63/252根日K过去对数收益，在月初首根有效日K收盘观察并保持；显示期限组成、方向及观察日期，不强凑七档。与回测 `monthly_momentum` 共用计算，仍为 `not_validated`；[方法、预热与验证局限](skills/bull-trend/references/trend-horizons.md)。

市场只约束新开仓的三期限实验可运行 [market_trend.py](skills/bull-trend/scripts/market_trend.py)，输入带预热的个股/同市场宽基daily OHLCV JSON，复用同一Python核心。月度入场、次日开盘、已有持仓按个股趋势退出；不会自动保存持仓或下单。[使用与当前验证边界](skills/bull-trend/references/trend-horizons.md#市场环境入场实验)。

### 筛选与回测

| 工具 | 说明 |
|------|------|
| `screen_stocks` | 全市场多因子筛选（可选 L2；factor_coverage 展示缺失因子、覆盖度、实际权重及 L2 effective_scores） |
| `screen_risk` | 风险因子筛查 |
| `run_backtest` | YAML 策略回测，默认收盘信号于次日开盘成交，execution 返回模型与限制 |
| `detect_market_regime` | 市场状态检测（牛/熊/震荡；返回实际 as_of 和坏行警告，历史不足 60 根时 above_ma60=null） |
| `get_market_review` | 市场日度复盘数据 |
| `get_review_history` | 复盘归档历史（最近 N 条，最新在前，用于纵向对比） |
| `run_watchlist_analysis` | 批量自选股分析 |

### 搜索与舆情

| 工具 | 说明 |
|------|------|
| `search_stock_news` | 关键词搜索新闻 |
| `search_comprehensive_intel` | 综合情报搜索 |
| `get_social_sentiment` | 社交媒体情绪（A 股：东财股吧 + 雪球；美港股：Reddit / X / Polymarket） |
| `get_trending_sentiment` | 社交媒体热门趋势聚合（Reddit / X / Polymarket） |
| `extract_article` | 网页正文提取 |

### 辅助

| 工具 | 说明 |
|------|------|
| `resolve_stock_name` | 股票名称 / 拼音模糊匹配 |
| `check_trading_day` | 查询是否交易日 |
| `get_trading_days` | 获取前后 N 个交易日 |
| `get_trading_phase` | 查询市场当前交易时段（盘前/盘中/盘后/休市） |
| `diagnose_data_sources` | 诊断数据源、环境变量和市场链路 |
| `get_market_capabilities` | 查询各市场支持的工具边界 |
| `parse_stock_list` | 从自然语言、CSV 或 Markdown 解析股票列表 |

### 上下文与报告

| 工具 | 说明 |
|------|------|
| `build_watchlist_context` | 构建自选股摘要与后续工具建议 |
| `analyze_position_context` | 结合成本、仓位与止损止盈分析持仓 |
| `check_alert_rules` | 无状态检查价格、涨跌幅、异动和风险规则 |
| `record_signal` | 记录 AI 分析建议信号（本地 JSONL 持久化） |
| `evaluate_signals` | 评估到期信号（target_hit/stop_hit/timeout 结算） |
| `get_signal_summary` | 信号胜率及分来源平均收益、有效收益样本数与小样本提示（按 source/symbol/status 过滤） |
| `render_stock_report` | 将结构化个股报告渲染为 Markdown，展示 evidence 与 data_quality 限制 |
| `render_market_report` | 将结构化市场复盘渲染为 Markdown（save=true 时归档到本地 JSONL） |

批量自选股分析和摘要的 `success` 表示至少获取了有效行情价格、K 线收盘价或技术评分；全部关键数据失败时计入 `failed`，部分结果仍保留。采集脚本复用返回的 K 线计算技术指标，并按股票所属市场查询大盘状态；日/韩/台市场状态暂不可用，非 A 股板块榜单也暂不可用，均会明确返回错误。

信号记录支持同一存储文件的多进程读写；评估期间新增的信号会保留。价格必须为正的有限数值，持有期限为正整数，日期格式为 YYYY-MM-DD。存储文件含损坏 JSON 行或无效信号时，记录、评估和汇总返回错误，原文件保持原样。

信号评估仅在历史 K 线覆盖记录日时结算，缺少历史窗口则返回 `skipped_no_data` 并保持未结算。风险筛查返回 `data_complete` 和 `warnings`；数据不足时评级可为 `unknown`，`risk_score`、`veto_buy` 可为 null。告警的 `evaluated` 条目含 `error` 时表示无法检查，不能将未触发理解为已通过检查。

选股的 `filters_applied` 只列实际执行的条件，显式条件缺少数据时报错。回测的 `period` 是实际数据期间，`requested_period` 保留请求期间，`warnings` 提示覆盖差异；买入预算包含佣金，年化收益和回撤按实际数据及初始资金计算。

### 无状态研究与风险计算

| 工具 | 说明 |
|------|------|
| `compare_stocks` | 比较显式输入的同行指标；检查单位、期间和口径，仅对可比数值排序 |
| `analyze_disclosure_changes` | 比较新旧公告正文与财务指标，返回来源、变更行号和增长率限制 |
| `analyze_portfolio_risk` | 一次性持仓的集中度、行业/币种敞口、价格盈亏、止损情景与可选相关性 |
| `calculate_position_size` | 按现金、风险预算、费用、滑点和交易单位计算本次可买数量 |
| `analyze_event_study` | 事件前一交易日收盘至各窗口的股票/基准收益差，标明缺失和重叠样本 |
| `validate_backtest` | 复用现有引擎，滚动训练选候选、样本外验证，比较参数及多个标的 |
| `compare_analysis_snapshots` | 比较调用方传入的快照，区分新增、删除、变化、数据失效和恢复 |

这些工具不保存自选股、账户或分析历史，也不提供调度/通知。现有数据工具采集行情和财务，宿主传入本次持仓、正文或快照；[完整输入约定与解读工作流](skills/stock-analysis/references/stateless-analysis.md)供四个宿主共用。

大输入经权限受限的临时 JSON 文件传给 Python，调用结束后清理，避免 Windows 命令行长度限制；CLI 也支持 stdin 和 `--input` 文件。公告正文由宿主读取：PDF/OCR 不包含在本次扩展中，只有标题或链接不能声称已读全文。

例如仓位预算（所有金额同币种，lot_size 由用户或交易规则确认）：

```json
{
  "equity": 100000, "cash": 20000,
  "entry_price": 100, "stop_price": 95,
  "risk_fraction": 0.01, "lot_size": 100,
  "fee_rate": 0.001, "slippage_rate": 0.001
}
```

将上述 JSON 保存为 `input.json` 后运行：

```bash
python tools/stateless_analysis.py position_size --input input.json
```

对应宿主工具是 `calculate_position_size`。其余 CLI 子命令依次为 `peers/disclosure/portfolio/event_study/robustness/snapshot`。事件研究可直接传 `symbol/benchmark/start_date/end_date/event_dates`；回测验证可传 `symbols/start_date/end_date/strategies`，由 Python 复用行情接口，避免模型重新抄写历史 K 线。显式价格/datasets 模式仍支持离线计算，两种模式互斥。

跨币种组合必须提供显式汇率；价格盈亏不含历史汇兑损益。稳健性工具仅支持 next_open、每窗口重新从现金开始，样本内敏感性不等于样本外表现。

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

行情 JSON 保留原有数组/字典形状，添加 `source`、UTC `fetched_at` 和 `data_as_of`。采集时间不是报价时间，提供方未给时间时为 null；仅提供交易日时保留 YYYY-MM-DD，不补造具体时刻；K 线 data_as_of 是该条的日期标签，周/月线标签不是实际末次成交时间；`as_of_basis=trading_calendar` 表示交易日历推断。A 股复权口径延续 qfq/none；其他市场按来源标为 total_return/qfq/unknown，不把 unknown 当已验证复权。

其他可用键：

- `exit.trailing_stop`：移动止损，价格自持仓期间最高收盘价回撤达到该比例即卖出（如 `0.08` = 峰值回撤 8% 触发）；与 `stop_loss` 同时设置时先判固定止损。`stop_loss` 必须为负数、`take_profit` 必须为正数、`trailing_stop` 须在 (0, 1) 内；`position.max_positions` 当前仅支持 1，其他配置明确报错。
- `benchmark`（顶层）：基准对比标的代码（如 `"sh000300"` 沪深300；指数代码必须带 sh/sz 交易所前缀，裸 6 位数字会被当作个股），设置后回测结果附带 `benchmark` 块（基准买入持有收益与 `excess_return` 超额收益）。基准与策略采用同一首日执行口径（next_open 用 open、same_close 用 close），无预热时以策略首条权益日期为起点；实际覆盖不符或首末价格非法时返回 error，不输出超额。
- `simulation.execution`：默认 `next_open`，前日收盘信号、次日开盘加滑点成交；显式 `same_close` 复现旧收盘成交假设。交易同时返回 signal_date/date。延迟的入场信号在新收盘失效时撤单，风险退出 pending 保留至成交。`start` 前可得历史仅用于指标预热，取数长度随策略最大 lookback 增加，交易与权益仍按请求区间统计；`warnings` 出现 `Entry indicator is unavailable` 时必须披露指标不可用及数据质量，不能声称完成预热。零成交量阻止成交；一字板限制只在输入提供同口径历史 limit_up/limit_down 时执行。`execution` 列出阻止成交次数、未成交订单及限制；止损是收盘信号，未模拟盘口排队、部分成交、结算和市场冲击。回测仅接受单一 `qfq`/`total_return`/`none` 复权口径，unknown 或混合口径明确报错。
- `simulation`（顶层）：交易成本。`slippage`（滑点，买卖双边，默认 0.001）、`commission`（佣金，买卖双边，默认 0.0015）、`stamp_tax`（印花税，仅卖出收取，默认 0；A股为 0.0005，各市场税率不同且会调整，按当下实际费率填写）。费率均须在 [0, 1) 区间，越界报错。回测结果附带 `costs` 块（实际生效费率与实付费用合计 `commission_paid`/`stamp_tax_paid`）。

### 可用指标

| 指标 | 说明 | 需要 period |
|------|------|-------------|
| `rsi` | RSI 相对强弱指数；预热不足不可用，全涨窗口为 100，全平为 50 | 是（默认 14） |
| `ma` / `ema` | 简单 / 指数移动平均 | 是（默认 20） |
| `macd_dif` / `macd_dea` / `macd` | MACD 三线 | 否 |
| `volume_ratio` | 当根成交量 / 前 N 根平均成交量（不含当根） | 是（默认 5） |
| `price_change` | 涨跌幅（%） | 否 |
| `bollinger_position` | 布林带位置（0=下轨, 1=上轨） | 是（默认 20） |
| `trend_score` | 独立实验趋势强度，默认 (EMA20−EMA60)/ATR14；fast_period/slow_period 控制 EMA | period 为 ATR 根数（默认 14） |
| `monthly_momentum` | 月初首根有效日K收盘观察过去 period 根对数收益，保持到下一观察；零成交量不计根数 | 是（默认63，要求正整数） |
| `close` / `volume` | 收盘价 / 成交量 | 否 |

### 可用运算符

`>` `<` `>=` `<=` `==` `cross_above`（上穿） `cross_below`（下穿）

示例策略见 `strategies/examples/`。

## 市场路由规则

| 代码格式 | 示例 | 市场 | 行情报价 Failover 链（K 线差异见下方） |
|----------|------|------|---------------------|
| 6 位纯数字 | `600519`, `000001`, `300750` | A 股 | akshare → tushare → efinance → 腾讯 → 新浪 → pytdx → baostock |
| 6 位纯数字（51/52/56/58/15/16/18 开头） | `510300`, `159915` | A 股 ETF | akshare（基金接口）→ tushare → efinance → 腾讯 → 新浪 → pytdx → baostock |
| 带交易所前缀（`sh`/`sz`/`bj` + 6 位数字） | `sh000001`（上证指数）, `sz399006`（创业板指） | A 股（显式指定，含指数） | 腾讯 → 新浪 |
| `.HK` 结尾 | `00700.HK`, `09988.HK` | 港股 | yfinance → 腾讯 → finnhub → longbridge |
| 英文字母 | `AAPL`, `GOOGL`, `TSLA` | 美股 | yfinance → finnhub → longbridge → alphavantage |
| `.T` 结尾 | `7203.T` | 日股 | yfinance |
| `.KS` / `.KQ` 结尾 | `005930.KS`, `036020.KQ` | 韩股 | yfinance |
| `.TW` / `.TWO` 结尾 | `2330.TW`, `6510.TWO` | 台股 | yfinance |

> 各数据源按优先级自动尝试，前一个失败后自动切换到下一个。未配置相关 env var 的数据源会被跳过。
> 港股腾讯备用源使用 `r_hk` 通道（`hk` 通道延迟约 15 分钟），仅用于实时报价，K 线仍走 yfinance → finnhub → longbridge。支持 `00700.HK` / `0700.HK` / `700.hk`，成交量单位为股，成交额及市值为港元；PB、换手率、量比在此备用源中返回 null，不套用 A 股字段。
> 裸 6 位代码一律按**个股**处理（`000001` 是平安银行）；指数需带交易所前缀（`sh000001` = 上证指数），显式前缀代码直接走腾讯/新浪（东财源只认裸代码）。
> **A 股 K 线口径**：股票/ETF 仅接受前复权数据，链路为 akshare → tushare（仅股票）→ efinance → 腾讯（仅 qfq 数据）→ baostock；带前缀的股票/ETF 先去前缀再走同一链路。新浪和 pytdx 的未复权 K 线不参与股票/ETF 容灾。显式指数 `sh000xxx` / `sz399xxx` 保留原始点位，走腾讯 → 新浪 → baostock。上述路由表的完整链路仍适用于实时行情。
> K 线响应保留原有 JSON 数组，每条 A 股记录新增 `adjusted`：股票/ETF 为 `"qfq"`，指数为 `"none"`。Tushare 日线通过 `adj_factor` 前复权，周/月线使用 `stk_week_month_adj` 的 qfq 字段；复权权限不足、因子缺失或源只返回原始价格时继续切换，所有可用复权源都失败时返回 JSON `error`，不降级到未复权股票价格。Tushare 复权接口需至少 2000 积分，见[复权因子](https://tushare.pro/document/2?doc_id=28)与[复权周/月线](https://tushare.pro/document/2?doc_id=365)。
> **粘性优选**：A 股链路（K 线 / 行情 / 市场快照 / 板块成分）会记住最近一次成功的数据源（缓存在系统临时目录，24h 有效），下次调用优先从它开始；若它已失效则自动顺延并更新记录。港美股链路不做粘性——一次 yfinance 瞬时失败不应让后续全天的 quote 降质。
> 日/韩/台股仅 yfinance 一个数据源（免费数据延迟约 15 分钟）；港股 / 美股 / 日股 / 韩股 / 台股的 ETF 与其所在市场股票走相同链路。


## 低频价投与增量数据

```bash
python tools/value_data.py valuation_history 600519 --years 5
python tools/stock_data.py financials 600519 --annual --periods 10
python tools/value_data.py earnings_calendar --report-date 2026-09-30 --symbol 600519
python tools/value_data.py shareholder_structure 600519 --report-date 2026-06-30
python tools/value_data.py shareholder_structure 0700.HK
python tools/value_data.py index_valuation 000300 --years 5
python tools/value_data.py etf_details 510300 --benchmark sh000300 --days 60
python tools/value_data.py etf_details 510300 --include-full-holdings
python tools/stock_data.py margin_trading 600519 --days 10 --include-ratio
python tools/value_data.py dividends --symbol 600519
python tools/value_data.py dividends --symbol 0700.HK --mode calendar
python tools/value_data.py dividends --mode ranking --report-date 2025-12-31
python tools/value_data.py dividends --mode ranking --market HK --report-date 2025-12-31
python tools/value_data.py earnings_calendar --symbol 0700.HK
```

财报、基本信息及估值/股东/ETF/分红等慢变数据缓存到 `~/.stock-analysis/cache`，TTL 一天；板块成分/映射一周。行情报价不缓存。缓存文件原子替换，过期/损坏重拉，错误和显式部分失败不缓存；不以过期数据伪装最新数据。原有临时目录的粘性路由和反向成分缓存保留。

估值使用可得数据的正值样本分位（`<=当前值`），返回 10/25/50/75/90 分位 Band 与实际起止日期；亏损 PE 分位为 null。当前 AKShare 已无 issue 中提到的 `stock_a_indicator_lg`，个股采用百度 PE-TTM/PB 历史，指数采用乐咕乐股；中证全指等通过中证官方历史接口取得多年滚动 PE，近期总股本 PE1 仍单独保留；PB 缺失时可配置 `LIXINGER_TOKEN` 补充理杏仁总市值加权（mcw）历史，返回每个指标的来源与口径，不拼接不同方法的同一指标。未配置或无权限时 PB 保持未知。A 股 `annual=true, periods=10` 请求十年年报；HK 按源实际可得年数返回，不补造缺失比率。

股东数据保留报告期、公告日或港交所披露日：A 股北向优先返回沪深交易所披露的季度合计数量，省略报告期时查最新已公布季度；十大流通股东中的 HKSCC 推断保留在 `northbound_nominee`，仅作明确标注的兜底，缺席不是零；HK 南向取自港交所 Stock Connect CCASS 记录，不等同完整实益股东名册。A 股财报预约/修订是计划，业绩预告是已公告预估，不是实际业绩。HK 单股优先使用 HKEX 主板/GEM 已公告业绩审批董事会排期，标记 `confirmation=official_board_meeting_notice`、`date_kind=scheduled_board_meeting`；这是计划会议日，不是实际披露日。可按已知财政期末过滤；找不到匹配公告时保留 Yahoo 供应商未来排期，`confirmation=provider_only`，未知报告期不归入指定期次，而另列 `unverified_schedules`。日期转为香港日期，公告表也不承诺覆盖所有港股。

ETF 使用披露资产规模历史（元）与披露持仓，保留持仓原始证券代码，不冒充实时完整申赎篮子；新增 `basket` 返回沪深交易所发布的完整当日 PCF（跨境代码原样保留），包含申赎单位、每篮子证券数量、现金替代规则与比例；校验基金代码、实际日期和全部组件数量，失败只提示不可用，不冒充完整。PCF 是申赎对价，不是实际持仓，证券数量也不是净值权重。`include_full_holdings=true` 时另取最近可得半年度/年度披露股票表，显式指定年份/月并请求较高行数上限，包含股票数量（股）、市值（人民币元）和实际报告期；返回全部收到的表格行，但不声称覆盖债券/现金等全资产或实时持仓，也不以 PCF 数量验证实际持仓。默认不取数百行明细。提供 `benchmark` 可另查基准指数成分。净值折溢价仅在行情/净值日期相同才计算，IOPV 折溢价另列。跟踪误差使用相同连续交易日区间的基金日净值增长与基准收盘收益差，取样本标准差乘 `sqrt(252)`，不填补缺失日期、不猜测基准。两融占比只匹配同一天的人民币元成交额，缺失则 null。

股息历史将预案与已实施分红分开，TTM 股息率以过去365天已实施名义每股现金/最新价计算（税前，未重算送转股）；空历史、已实施记录缺失日期/金额或分红币种与报价币种不一致时，受影响的总额/股息率返回未知，不猜测汇率；历史不完整时不推断连续分红年数。港股日历、现金历史与报价独立降级，保留可用部分并提示失败来源。日历独立返回已公告未来除息日期。A 股排行/选股使用指定报告期已实施方案股息率；HK 指定 `market=HK` 时使用 HKEX/LSEG 公布的年度股息率（百分数），不是当前价 TTM；HK `report_date` 仅支持数据源可得年度的年末。港股股息选股因子也使用同一年度口径。筛选 YAML 可增加 `filters.dividend_yield_min: 3`、`sort_by: dividend_score` 与可选 `dividend_report_date`；缺失为未知，不按0填充。

数据源参考：[AKShare股票接口](https://akshare.akfamily.xyz/data/stock/stock.html)、[指数接口](https://github.com/akfamily/akshare/blob/main/docs/data/index/index.md)、[基金接口](https://github.com/akfamily/akshare/blob/main/docs/data/fund/fund_public.md)、[港交所南向持股](https://www.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk)、[HKEX 年度股息率](https://www.hkex.com.hk/Listing/Sustainability/Shareholder-Value-in-Focus/Dividend-Yield-Ratio?sc_lang=en)、[理杏仁指数基本面接口](https://www.lixinger.com/api/open-api/html-doc/cn/index/fundamental)。

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

## CI 真实宿主测试

GitHub Actions 的 `e2e-host` 使用 Pi、Hermes、OpenClaw、dsh 的真实运行时执行工具调用。在仓库 **Settings → Secrets and variables → Actions** 中配置以下三项，所有 E2E 共用：

| 名称 | 存放位置 | 内容 |
|---|---|---|
| `OPENAI_BASE_URL` | Variable 或 Secret | OpenAI 兼容的 API base URL，包含服务商要求的路径（例如 `/v1`） |
| `OPENAI_API_KEY` | Secret | 对应的 API key |
| `OPENAI_MODEL` | Variable 或 Secret | 支持 tool calling 的模型 ID |

`e2e-llm` 也使用这三项配置，Hermes（含报告质量评测）、Pi、OpenClaw 和 dsh 通过四个 matrix 作业并行运行，互不因失败而取消；同仓库 PR 和主分支推送会运行，fork PR 不读取 API key。`e2e-host` 缺少任一项时会失败。URL 和 model 同时存在于 Variable 与 Secret 时，优先使用 Secret。

## 项目结构

```
stock-analysis/
├── pi/                              # Pi Agent Extension
│   └── index.ts                     #   注册 70 个工具 + 21 个 skill
├── hermes/                          # Hermes Agent Plugin
│   ├── plugin.yaml                  #   插件清单
│   ├── __init__.py                  #   register(ctx) 入口
│   ├── schemas.py                   #   工具 JSON Schema 定义
│   └── tools.py                     #   70 个 handler → subprocess 调 CLI
├── openclaw/                        # OpenClaw Plugin
│   ├── openclaw.plugin.json         #   manifest（contracts.tools）
│   ├── package.json                 #   含 openclaw 块（pluginApi/SDK 版本）
│   └── index.ts                     #   definePluginEntry + registerTool ×70
├── dsh/                             # DeepSeek Harness (dsh) Plugin
│   ├── cordis.patch.yml             #   bundle patch（insert 插件入口）
│   ├── package.json                 #   含 dsh.bundle 块（bundle manifest）
│   └── index.ts                     #   cordis apply(ctx) + defineTool ×70
│
├── tools/                           # 共享 Python CLI 工具
│   ├── stock_data.py                #   行情 / 资金流 / 新闻 / 财务 / 短线情绪
│   ├── signal_tracker.py            #   AI 买卖建议回测闭环（本地 JSONL 存储）
│   ├── technical.py                 #   技术指标分析
│   ├── pattern.py                   #   K 线形态识别
│   ├── value_data.py                #   估值/财报日历/股东/ETF/股息
│   ├── _cache.py                    #   用户目录 TTL 缓存
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

系统会自动按 akshare → tushare → efinance → 腾讯 → 新浪 → pytdx → baostock 顺序尝试（A 股链路含粘性优选：最近成功的源 24h 内优先）。腾讯 / 新浪源不依赖东财域名，在境外或受限网络下通常仍可用。如果所有源都失败，检查：网络问题、akshare 版本过低（`pip install --upgrade akshare`）、或非交易时间。配置 `TUSHARE_TOKEN` 可增加一个可靠数据源。

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

### 公告与港美股研究数据（#49–#51）

```bash
python3 tools/value_data.py announcements 600519 --category forecast --start-date 2026-01-01 --end-date 2026-10-13 --top 50
python3 tools/stock_data.py financials 0700.HK --annual --periods 10
python3 tools/value_data.py valuation_history 0700.HK --years 10
# SEC_USER_AGENT 必须配置为应用名称 + 你的真实联系邮箱
python3 tools/sec_edgar.py holdings 0001067983
python3 tools/sec_edgar.py insider_trades AAPL --days 90 --top 10
```

公告类别为 `all/forecast/express/annual/interim/quarterly/reduction/issuance/inquiry/dividend`。默认最近 90 天，最多 500 条；通过巨潮官方股票目录取得 orgId，沪深京统一使用官方 `szse` 查询栏目。业绩快报和问询使用标题关键词查询。第一版返回 PDF 原文链接，不引入 PDF 解析依赖；`total/truncated` 表示供应商匹配数量及截断，查询无结果与请求失败分开。

港股使用既有 `LIXINGER_TOKEN`，需具备港股接口权限。新增来源覆盖理杏仁非金融公司财务和每日估值，最多十年；金融公司或无权限时回退现有 Yahoo/东财财务和百度估值，返回实际期数和不足提示。财务按 `annual_report` 识别年报，保留财年末、披露日和币种，金额为报告币种，历史比率为百分数；顶层 `roe/profit_margin` 延续 Yahoo 小数比例。理杏仁财报用于增强历史，同时按整组保留可用的 Yahoo 顶层快照，`currency` 是 Yahoo 的财报币种，`market_cap_currency` 是市值币种，`snapshot_source` 标明来源，缺失值不混入其他报告期或币种的历史数字；历史币种和报告日期在 `periods` 每行保留，Yahoo 未提供明确报告日期时顶层 `report_date=null`。快照失败时 `snapshot_available=false`，财报与历史仍返回。会计口径不强行转换为 A 股，估值分位沿用正值样本算法，不拼接不同来源。Yahoo 顶层快照的 `snapshot_units` 明确：`pe=trailing`，`roe/profit_margin/dividend_yield=ratio`（小数），`debt_to_equity=percent`（百分数）；不得把 D/E 百分数当作小数倍数。

SEC 配置 `SEC_USER_AGENT`，包含应用名称与真实联系邮箱，仅通过环境变量提供。单进程最多每秒 2 次请求（多个并行进程需控制账户/IP 总量不超过 SEC 的每秒 10 次），并缓存 ticker/CIK、申报元数据和 XML 解析结果。13F 返回机构最近季度的原始申报，不是实时完整组合；通常在季度后 45 天内披露（节假日顺延），实际数据年龄以季度末为准，2023-01-03 起金额单位为美元，更早申报为千美元。修订文件另列原文链接，避免错误合并补充与重述；发行人 CIK 反查现已通过 `mode=issuer` 支持：从结构化 Schedule 13D/G 校验发行人 CIK、提取 CUSIP，再扫描官方最新已发布 13F 批量窗口。`auto` 默认有当前或已检索历史 13F 时查机构，否则查发行人；Berkshire 等兼具两种身份时显式选择 `issuer`。修订按 `RESTATEMENT` 替换与 `NEW HOLDINGS` 补充重建；无法判断的修订或缺少基础版时总量为 null，并给出 `unresolved_manager_count`。

Form 4 的 `days` 是申报日期窗口，`top=1..20` 限制文件数，返回全部已解析交易。保留多人联报、衍生品、交易代码、脚注、直接/间接持有和修订信息；仅 P/S 标为买/卖，其余行为不推断方向，价格未披露为 null。读取 `truncated/warnings`，原件与修订不要重复计数。风险组合工具 `screen_risk` 仍为 A 股专用。

接口依据：[巨潮公告查询](https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search)、[理杏仁港股财报](https://www.lixinger.com/api/open-api/html-doc/hk/company/fs/non_financial)、[理杏仁港股估值](https://www.lixinger.com/api/open-api/html-doc/hk/company/fundamental/non_financial)、[SEC API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)。

股票 CIK 反查示例：

```bash
python3 tools/sec_edgar.py holdings 0000320193 --mode issuer
# 原机构查询保持可用
python3 tools/sec_edgar.py holdings 0001067983 --mode manager
```

首次下载当前 SEC 包约 100 MB；ZIP 按周缓存，CUSIP 反查结果按天缓存，流式扫描不解压整个持仓表、不增加数据库依赖。返回 `report_date`（持仓日期）、`filing_window_end`（公开申报窗口截止）、`cusip_evidence`（原文映射依据），不把文件披露窗口当作持仓日期。`mapping_complete=false` 表示最近十份结构化 13D/G 可能未覆盖所有证券类别或 CUSIP 变更；缺映射直接报错，不做名称模糊匹配。`complete_for_mapped_cusips` 仅在该报告期没有未解决申报链时为 true，不代表实时全部股东。期权与本金持仓在 `positions` 保留，`shares` 合计只计无 PUT/CALL 的 SH 股票。不能将跨机构合计当作已去重的全市场持股。

13F CLI 预算为 300 秒，宿主另留 30 秒接收输出；其他命令按共享预算表执行。机构查询优先寻找最新已知报告期的原始 13F：较新修订对应的原始版未找到时明确报错，历史分片仍未完全检索时返回 `history_truncated` 与警告。发行人 CUSIP 查询会跳过不可访问/无效 XML，继续核对后续原文；没有任何可验证映射时才报缺失。

## 分析可靠性验证

命令预算集中于 `tools/cli_budgets.json`：通用采集默认 120 秒、市场快照 240 秒、选股 top 须为正整数，预算按候选数及 6 个 worker 的批次计算（默认 20 个候选为 1830 秒）、13F 300 秒；技术分析、市场状态、回测和风险筛查按实际嵌套调用另留预算；四个宿主额外留 30 秒接收 CLI 输出。综合采集保留安全 JSON 错误及 error_code（timeout/empty_output/invalid_json/process_failed/tool_error），不会把失败当成有效空数据。

最终报告的固定证据案例覆盖完整、缺失、过期、同口径冲突及样本不足：

```bash
.venv/bin/python -m pytest tests/test_analysis_quality.py -q
# 需要 OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL；只调用模型，数据源使用固定夹具
.venv/bin/python -m pytest tests/e2e/test_analysis_quality.py -m integration_llm -v
```

真实模型评测同时要求结构化证据检查与独立上下文的结论审稿通过：审稿检查正文事实、限制说明是否诚实，以及是否过度外推。未运行审稿、结果缺失或模糊都不能算通过；报告自行声明通过不被采信。另有人工核对的正确报告及两类反例用于验证审稿器（正确引用但正文捏造现价、3笔交易却保证长期胜率）。空回答也失败。离线渲染和规则检查不代表真实模型通过，也不证明策略未来有效。信号建议可沿用 source 标识策略版本，按来源复盘胜率、平均收益和样本量；未扣成本、未匹配基准的观察记录不能当作策略回测。

信号结算仅在价格口径可核实时进行：原始价格可直接比较；自动记录的默认 `record_date` 与 `price_date` 均按标的市场时区确定（显式 `date` 不变），并保存原始 `price_as_of`；`price_date` 晚于 `record_date` 时拒绝记录与结算；多头 target 必须高于 entry、stop 必须低于 entry，空头相反，否则拒绝记录。开盘先触 stop 按 open 结算，先触 target 按 target 保守结算，日内顺序未知的双触发仍 stop 优先；无止损的旧 HLC 记录可继续用 high/low/close 判定目标和到期，但若提供 open 也必须是正有限数值，有止损但缺 open 则拒绝结算。重复日期返回 skipped_no_data，复权乘除使用 Decimal。复权日线需该报价日的 adjustment_factor（复权价/原始价），当前日线 Tushare、Alpha Vantage 可提供。缺失系数或口径未知时返回 skipped_price_basis，保持未结算，不猜测除权比例；记录日无 K 线时仅在前后因子一致（或已有明确报价日）时使用最近锚点，旧信号缺少报价时点且记录日因子变化时也保持未结算。换算后的 exit_price 是记录时价格单位的等价值，实际市场价可不同，evaluation_price_basis 保留依据。evaluate_signals 的 CLI 预算为 300 秒，内部提前结束采集并保存已完成结果，complete/deferred/warnings 指示需要重试的记录；预算耗尽后调整未结算记录的处理顺序，下轮优先处理尚未尝试的记录，避免慢请求长期阻塞后续信号。

市场复盘数据不足时温度/姿态为 null，信号 unknown，data_quality.limitations 说明限制。自选股异动采集失败时 anomaly_status=unavailable、anomaly_count=null，保留警告并计入 summary.partial，不能当成无异常。

L1 与 L2 综合分均在排序前按可用因子重新归一化；缺失或非有限因子不作零分处理，因子组内部也仅平均有效输入。factor_coverage.ratio 保留原始权重覆盖率，effective_weights 是实际归一化后的权重，高分仍需结合覆盖率判断。估值比较需同时读取 `pe_basis`；有限非正 PE/PB 的价值子分为 0，不能因数值更低而获得最高价值分。

L2 的 `vol20` 只使用恰好 20 个有效日收益，`factor_coverage.vol20_samples` 记录实际样本数，非正/非有限收盘价先剔除，空输入为 0；质量因子按 `report_date` 分组归一，避免年度累计与单季累计直接比较。资金流 5/10/20 日窗口不足返回 null，不按 0 或完整窗口处理。HK 快照统一带 `.HK`，US 快照移除东财交易所后缀并把类别股点号转为 Yahoo 连字符。主板 ST/*ST 按 5% 识别涨跌停，其他板块比例不变。
