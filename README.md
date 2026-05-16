# pi-stock-analysis

Pi Agent 股票分析扩展 — 覆盖 A 股 / 港股 / 美股的综合分析、多因子选股和策略回测。

融合了 [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)（综合分析）、AlphaSift（多因子筛选）和 AlphaEvo（策略回测进化）三个项目的核心能力，以轻量 Pi Extension 形式提供。

## 目录

- [依赖说明](#依赖说明)
- [安装](#安装)
- [快速开始](#快速开始)
- [Skills 一览](#skills-一览)
- [工具详解](#工具详解)
- [策略回测 DSL](#策略回测-dsl)
- [市场路由规则](#市场路由规则)
- [独立 CLI 使用](#独立-cli-使用)
- [项目结构](#项目结构)
- [常见问题](#常见问题)

## 依赖说明

本扩展 **仅依赖 Pi Agent 核心运行时**，不需要额外安装任何 Pi 组件：

| 依赖 | 是否需要 | 说明 |
|------|----------|------|
| Pi Agent (`pi` CLI) | **需要** | 提供 agent 循环、LLM、TUI、工具调用框架 |
| Python 3.9+ | **需要** | 运行数据获取和计算工具 |
| pip 依赖 | **需要** | akshare, yfinance, pandas, numpy, pyyaml |
| pi-skills 插件 | 不需要 | Skills 通过 `package.json` 静态声明，Pi 原生加载 |
| Pi 定时任务 | 不需要 | 本扩展不使用定时任务 |
| Pi MCP | 不需要 | 工具通过 `pi.registerTool()` 直接注册 |
| Node.js 依赖 | 不需要 | `index.ts` 无第三方 npm 依赖，Pi 的 jiti 直接转译运行 |

**架构原则**：Pi 提供 agent 循环 / LLM / TUI，本扩展只提供：数据工具（Python CLI） + 分析策略（SKILL.md）。不需要 sub-agent，工具 + Skill 即可完成所有功能。

## 安装

### 1. Clone 到 Pi 扩展目录

```bash
# 全局安装（所有项目可用）
cd ~/.pi/agent/extensions
git clone git@github.com:Weaxs/pi-stock-analysis.git
```

或者项目级安装：

```bash
# 仅当前项目可用
cd your-project
mkdir -p .pi/extensions
cd .pi/extensions
git clone git@github.com:Weaxs/pi-stock-analysis.git
```

### 2. 安装 Python 依赖

```bash
pip install -r ~/.pi/agent/extensions/pi-stock-analysis/tools/requirements.txt
```

依赖列表：

| 包 | 用途 |
|---|------|
| akshare | A 股数据源（行情、资金流、财务、新闻） |
| yfinance | 港股 / 美股数据源 |
| pandas | 数据处理和指标计算 |
| numpy | 数值计算 |
| pyyaml | 策略 YAML 文件解析 |

### 3. 验证安装

```bash
# 启动 Pi
pi

# 检查扩展是否加载（应该能看到 stock-analysis 相关工具）
/skills
```

## 快速开始

### 综合分析一只股票

```
/skill:stock-analysis

帮我分析贵州茅台（600519）
```

Pi Agent 会自动调用 `get_quote` → `get_kline` → `get_technical_analysis` → `get_financials` → `get_capital_flow` → `get_news`，然后输出多维度分析研报。

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

### 用特定视角分析

```
/skill:chan-theory

用缠论分析一下 600519 当前的走势结构
```

```
/skill:wave-theory

用波浪理论分析 AAPL 的浪形位置
```

## Skills 一览

### 核心 Workflow（3 个）

| Skill | 调用方式 | 功能 |
|-------|----------|------|
| 综合分析 | `/skill:stock-analysis` | 技术面 + 基本面 + 资金面 + 消息面多维研判，输出结构化研报 |
| 全市场选股 | `/skill:stock-screener` | L1 多因子硬筛 → L2 LLM 智能排序 → 输出推荐列表 |
| 策略回测 | `/skill:strategy-backtest` | YAML 策略定义 → 回测 → 诊断 → LLM 变异优化 → 迭代 |

### 策略方法论（11 个）

每个策略 Skill 教会 Pi Agent 用特定的分析框架解读数据：

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

## 工具详解

扩展注册了 8 个工具供 Pi Agent 调用，也可以作为独立 Python CLI 使用。

### 数据工具（5 个）

#### get_kline — K 线数据

获取股票 OHLCV（开高低收量）历史数据。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| symbol | string | **必填** | 股票代码 |
| period | string | `daily` | K 线周期：`daily` / `weekly` / `monthly` |
| count | number | `60` | 返回数据条数 |

返回字段：`date`, `open`, `high`, `low`, `close`, `volume`, `turnover`（A 股）, `change_pct`（A 股）, `turnover_rate`（A 股）

#### get_quote — 实时行情

| 参数 | 类型 | 说明 |
|------|------|------|
| symbol | string | 股票代码 |

返回字段：`symbol`, `name`, `price`, `change`, `change_pct`, `volume`, `turnover`, `high`, `low`, `open`, `prev_close`, `market_cap`, `pe`, `pb`, `turnover_rate`（A 股）, `volume_ratio`（A 股）

#### get_capital_flow — 资金流向（仅 A 股）

| 参数 | 类型 | 说明 |
|------|------|------|
| symbol | string | A 股代码（如 `600519`） |

返回近 10 日数据：`date`, `main_net_inflow`, `main_pct`, `super_large_net`, `large_net`, `medium_net`, `small_net`

> 港股和美股调用此工具会返回 `{"error": "capital_flow only available for A-shares"}`

#### get_news — 财经新闻

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| symbol | string | **必填** | 股票代码 |
| days | number | `3` | 获取最近几天的新闻 |

返回字段：`title`, `datetime`, `source`, `url`, `content`

#### get_financials — 财务指标

| 参数 | 类型 | 说明 |
|------|------|------|
| symbol | string | 股票代码 |

A 股返回：`roe`, `net_profit_margin`, `gross_margin`, `debt_ratio`, `current_ratio`

港股 / 美股返回：`pe`, `forward_pe`, `pb`, `market_cap`, `total_revenue`, `net_income`, `profit_margin`, `roe`, `debt_to_equity`, `dividend_yield`

### 分析工具（3 个）

#### get_technical_analysis — 技术指标分析

对股票进行全面技术分析，输出 JSON 格式的指标 + 信号。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| symbol | string | **必填** | 股票代码 |
| period | string | `daily` | 分析周期 |
| count | number | `120` | 用于计算的K线条数 |

输出包含：

```
├── moving_averages    MA5/10/20/60 + 排列状态
├── macd               DIF/DEA/柱状 + 金叉/死叉
├── rsi                RSI6/12/24 + 超买超卖信号
├── bollinger          上轨/中轨/下轨 + 位置/带宽
├── kdj                K/D/J + 超买超卖
├── volume             量比 + 量能信号
├── support_resistance 支撑位/压力位列表
├── trend              短/中/长期趋势 + 综合趋势
└── signals            中文信号提示列表
```

#### screen_stocks — 全市场筛选

AlphaSift L1 多因子硬筛，按定量指标过滤和评分。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| market | string | `A` | 市场：`A` / `HK` / `US` |
| top | number | `20` | 返回 Top N |
| config | string | 无 | 自定义筛选 YAML 配置路径（可选） |

默认筛选条件：PE 0-100, PB 0-20, 市值 > 50 亿, 换手率 0.5%-20%, 涨跌幅 -5%~9.9%, 量比 > 0.5

评分维度：`value`（价值，0.4 权重）、`momentum`（动量，0.3）、`liquidity`（流动性，0.3）

自定义筛选配置示例：

```yaml
name: "低估值选股"
filters:
  pe_min: 5
  pe_max: 30
  pb_max: 5
  market_cap_min: 10e9
sort_by: "composite_score"
sort_order: "desc"
```

#### run_backtest — 策略回测

AlphaEvo 回测引擎，基于 YAML 策略定义进行信号级模拟。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| strategy | string | **必填** | 策略 YAML 文件路径 |
| symbol | string | **必填** | 股票代码 |
| start | string | 一年前 | 回测开始日期 `YYYY-MM-DD` |
| end | string | 今天 | 回测结束日期 `YYYY-MM-DD` |
| capital | number | `1000000` | 初始资金（元） |

输出指标：总收益率、年化收益率、最大回撤、夏普比率、胜率、盈亏比、交易明细、资金曲线、自动诊断

模拟参数：滑点 0.1%，手续费 0.15%（单边），A 股按 100 股整手交易

## 策略回测 DSL

策略以 YAML 格式定义，支持参数化和条件组合：

```yaml
name: "RSI Oversold Bounce"
version: "1.0"
description: "RSI超卖反弹策略"

# 参数定义 — 可在条件中用 {param_name} 引用
parameters:
  rsi_period: 14
  rsi_oversold: 30
  rsi_overbought: 70
  stop_loss: -0.05
  take_profit: 0.15

# 入场条件
entry:
  conditions:
    - indicator: "rsi"
      period: "{rsi_period}"
      operator: "<"
      value: "{rsi_oversold}"
    - indicator: "volume_ratio"
      operator: ">"
      value: 1.0
  logic: "all"              # all = 全部满足(AND)，any = 任一满足(OR)

# 出场条件
exit:
  conditions:
    - indicator: "rsi"
      period: "{rsi_period}"
      operator: ">"
      value: "{rsi_overbought}"
  logic: "any"
  stop_loss: "{stop_loss}"  # 止损比例
  take_profit: "{take_profit}" # 止盈比例

# 仓位管理
position:
  size: 1.0                 # 可用资金的比例
  max_positions: 1           # 最大持仓数
```

### 可用指标

| 指标 | 说明 | 需要 period 参数 |
|------|------|------------------|
| `rsi` | RSI 相对强弱指数 | 是（默认 14） |
| `ma` | 简单移动平均线 | 是（默认 20） |
| `ema` | 指数移动平均线 | 是（默认 20） |
| `macd_dif` | MACD DIF 线 | 否 |
| `macd_dea` | MACD DEA 线 | 否 |
| `macd` | MACD 柱状图 | 否 |
| `volume_ratio` | 量比（当日成交量 / 均量） | 是（默认 5） |
| `price_change` | 涨跌幅（%） | 否 |
| `bollinger_position` | 布林带位置（0=下轨, 1=上轨） | 是（默认 20） |
| `close` | 收盘价 | 否 |
| `volume` | 成交量 | 否 |

### 可用运算符

| 运算符 | 说明 |
|--------|------|
| `>` `<` `>=` `<=` `==` | 标准比较 |
| `cross_above` | 上穿：前一日 ≤ 阈值 且 当日 > 阈值 |
| `cross_below` | 下穿：前一日 ≥ 阈值 且 当日 < 阈值 |

### 示例策略

项目提供两个示例策略供参考和回测：

- `strategies/examples/rsi_oversold.yaml` — RSI 超卖反弹
- `strategies/examples/ma_crossover.yaml` — 均线金叉

用法：

```
/skill:strategy-backtest

用 rsi_oversold 策略回测 600519，回测 2024-01-01 到 2025-01-01
```

Pi Agent 会自动定位到 `strategies/examples/rsi_oversold.yaml` 并调用 `run_backtest` 执行回测。

## 市场路由规则

数据工具根据股票代码格式自动识别市场并选择数据源：

| 代码格式 | 示例 | 市场 | 数据源 | 说明 |
|----------|------|------|--------|------|
| 6 位纯数字 | `600519`, `000001`, `300750` | A 股 | akshare | 沪市(6开头)、深市(0/3开头) |
| 以 `.HK` 结尾 | `00700.HK`, `09988.HK` | 港股 | yfinance | 需带 `.HK` 后缀 |
| 英文字母 | `AAPL`, `GOOGL`, `TSLA` | 美股 | yfinance | 标准 ticker |

> A 股独有功能：资金流向（`get_capital_flow`）、换手率、量比、涨跌幅等实时指标
>
> 港股 / 美股：行情数据可能有 15 分钟延迟（yfinance 免费数据限制）

## 独立 CLI 使用

Python 工具可以脱离 Pi 独立使用，所有输出为 JSON 格式：

```bash
# ========== stock_data.py ==========

# K 线数据
python tools/stock_data.py kline 600519                          # A 股日K，默认60根
python tools/stock_data.py kline 600519 --period weekly --count 30 # A 股周K
python tools/stock_data.py kline AAPL --period daily --count 90   # 美股日K
python tools/stock_data.py kline 00700.HK                        # 港股日K

# 实时行情
python tools/stock_data.py quote 600519
python tools/stock_data.py quote AAPL

# 资金流向（仅 A 股）
python tools/stock_data.py capital_flow 600519

# 财经新闻
python tools/stock_data.py news 600519 --days 5

# 财务指标
python tools/stock_data.py financials 600519
python tools/stock_data.py financials AAPL

# 全市场快照（供 screener 使用）
python tools/stock_data.py market_snapshot --market A
python tools/stock_data.py market_snapshot --market HK
python tools/stock_data.py market_snapshot --market US

# ========== technical.py ==========

# 技术指标分析
python tools/technical.py analyze 600519
python tools/technical.py analyze AAPL --period weekly --count 60

# ========== screener.py ==========

# 全市场筛选
python tools/screener.py screen --market A --top 20
python tools/screener.py screen --market A --top 10 --config my_filter.yaml

# ========== backtest.py ==========

# 策略回测
python tools/backtest.py run strategies/examples/rsi_oversold.yaml 600519
python tools/backtest.py run strategies/examples/rsi_oversold.yaml 600519 --start 2024-01-01 --end 2025-01-01
python tools/backtest.py run strategies/examples/ma_crossover.yaml AAPL --capital 100000

# 重新评估回测结果
python tools/backtest.py evaluate result.json
```

## 项目结构

```
pi-stock-analysis/
├── package.json                          # Pi manifest（extensions + skills 声明）
├── index.ts                              # Extension 入口：注册 8 个工具 + skill 发现
│
├── tools/                                # Python CLI 工具
│   ├── stock_data.py                     # 行情数据（akshare + yfinance）
│   ├── technical.py                      # 技术指标（MA/MACD/RSI/BOLL/KDJ/量能）
│   ├── screener.py                       # 全市场筛选（AlphaSift L1 硬筛）
│   ├── backtest.py                       # 策略回测引擎（AlphaEvo）
│   └── requirements.txt                  # Python 依赖
│
├── skills/                               # SKILL.md 文件（Pi 原生加载）
│   ├── stock-analysis/SKILL.md           # 核心：综合分析 workflow
│   ├── stock-screener/SKILL.md           # 核心：选股 workflow
│   ├── strategy-backtest/SKILL.md        # 核心：回测 + 进化 workflow
│   ├── bull-trend/SKILL.md               # 策略：趋势追踪
│   ├── shrink-pullback/SKILL.md          # 策略：缩量回调
│   ├── ma-crossover/SKILL.md             # 策略：均线金叉
│   ├── volume-breakout/SKILL.md          # 策略：放量突破
│   ├── bottom-volume/SKILL.md            # 策略：底部放量
│   ├── dragon-head/SKILL.md              # 策略：龙头战法
│   ├── chan-theory/SKILL.md              # 策略：缠论
│   ├── wave-theory/SKILL.md              # 策略：波浪理论
│   ├── box-oscillation/SKILL.md          # 策略：箱体震荡
│   ├── emotion-cycle/SKILL.md            # 策略：情绪周期
│   └── one-yang-three-yin/SKILL.md       # 策略：一阳穿三阴
│
├── strategies/examples/                  # AlphaEvo 策略 YAML 示例
│   ├── rsi_oversold.yaml                 # RSI 超卖反弹
│   └── ma_crossover.yaml                 # 均线金叉
│
├── .gitignore
└── README.md
```

### 数据流

```
用户输入 (自然语言)
    │
    ▼
Pi Agent (LLM 理解意图)
    │
    ├── 加载 SKILL.md (分析框架/流程指引)
    │
    ├── 调用 Tool → index.ts → python3 tools/xxx.py → JSON stdout
    │   ├── get_kline          → stock_data.py kline
    │   ├── get_quote          → stock_data.py quote
    │   ├── get_capital_flow   → stock_data.py capital_flow
    │   ├── get_news           → stock_data.py news
    │   ├── get_financials     → stock_data.py financials
    │   ├── get_technical      → technical.py analyze
    │   ├── screen_stocks      → screener.py screen
    │   └── run_backtest       → backtest.py run
    │
    ▼
Pi Agent (LLM 分析数据 + 生成报告)
    │
    ▼
用户看到结构化分析结果
```

## 常见问题

### Q: 安装后 Pi 没有加载扩展？

确认扩展目录路径正确：
- 全局：`~/.pi/agent/extensions/pi-stock-analysis/package.json` 存在
- 项目级：`<project>/.pi/extensions/pi-stock-analysis/package.json` 存在

启动 `pi` 后输入 `/skills` 查看是否列出 `stock-analysis` 等技能。

### Q: Python 工具报错 `ModuleNotFoundError`？

确保安装了 Python 依赖：

```bash
pip install -r ~/.pi/agent/extensions/pi-stock-analysis/tools/requirements.txt
```

如果使用 virtualenv / conda，确保 Pi 启动时能访问到对应的 Python 环境。

### Q: A 股数据获取失败？

akshare 依赖东方财富等数据源，可能因为：
- 网络问题（需要能访问 A 股数据接口）
- akshare 版本过低（建议 `pip install --upgrade akshare`）
- 交易日之外的实时行情可能为空

### Q: 港股 / 美股数据有延迟？

yfinance 的免费数据通常有 15 分钟延迟，这是数据源的限制。如需实时数据需要付费 API。

### Q: 回测结果怎么保存？

CLI 模式下将输出重定向到文件：

```bash
python tools/backtest.py run strategy.yaml 600519 > result.json
```

后续可以重新评估：

```bash
python tools/backtest.py evaluate result.json
```

### Q: 如何自定义筛选条件？

创建一个 YAML 配置文件：

```yaml
name: "我的选股策略"
filters:
  pe_min: 5
  pe_max: 25
  pb_max: 3
  market_cap_min: 20e9
  turnover_rate_min: 2.0
sort_by: "composite_score"
sort_order: "desc"
```

然后在 Pi 中使用：

```
帮我用 /path/to/my_filter.yaml 的条件筛选 A 股
```

或者 CLI：

```bash
python tools/screener.py screen --config my_filter.yaml --top 30
```

### Q: 如何编写自定义回测策略？

参考 `strategies/examples/` 下的示例，创建新的 YAML 文件。详见 [策略回测 DSL](#策略回测-dsl) 章节。

在 Pi 中可以直接让 Agent 帮你编写策略：

```
/skill:strategy-backtest

我想做一个 MACD 金叉 + RSI 低位的策略，帮我写成 YAML 然后回测茅台
```

## License

MIT
