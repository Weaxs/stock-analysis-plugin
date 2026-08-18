# Stock Analysis — DeepSeek Harness (dsh) Plugin

A股 / 港股 / 美股 / 日股 / 韩股 / 台股 行情、技术分析、筛选与回测。作为 dsh bundle 安装到 profile 后，对话中可调用 39 个工具，并自动注册 20 个工作流/策略方法论 skill。

## 安装

```bash
dsh plugin --profile <你的profile> add @weaxs/dsh-stock-analysis
dsh --profile <你的profile>
```

`dsh plugin add` 是 pnpm 的薄封装：安装后 dsh 会读取本包 `package.json` 里的 `dsh.bundle.patch`，把 `cordis.patch.yml` 作为一层叠加进 profile 组合。可用 `dsh --profile <你的profile> --dump-config` 验证 layer 已生效。

从源码安装（本仓库根目录）：

```bash
npm install && npm run build:dsh
dsh plugin --profile <你的profile> add ./dsh
```

### Python 运行时

工具实际逻辑在 `tools/*.py`，依赖 `akshare`、`pandas`、`numpy`、`requests`。本包的 postinstall（`scripts/setup-python.mjs`）会在包目录下自动建 `.venv` 并装好依赖，前提：本机有 `python3 >= 3.9`。

注意：dsh profile 使用 pnpm 且默认不执行第三方包的安装脚本。如 postinstall 被跳过，请在 profile 的 `pnpm-workspace.yaml` 中允许：

```yaml
allowBuilds:
  "@weaxs/dsh-stock-analysis": true
```

如果 venv 最终没建起来，plugin 会回退到系统 `python3`（Windows 为 `python`），此时请自行 `pip install -r tools/requirements.txt`。

## 工具列表

### 行情数据
- `get_kline` — K线 OHLCV（A/HK/US/JP/KR/TW）
- `get_quote` — 实时报价
- `get_capital_flow` — A股资金流（个股 / 多日 / 板块）
- `get_news` — 财经新闻
- `get_financials` — 关键财务指标

### 分析
- `get_technical_analysis` — MA/MACD/RSI/BOLL/KDJ + 综合评分
- `analyze_pattern` — 12+ 种 K 线形态识别
- `calculate_ma` — 独立均线计算
- `get_volume_analysis` — 量价分析

### 市场全景
- `get_market_indices` — 主要指数（CN/HK/US/JP/KR/TW）
- `get_sector_rankings` — A股板块涨跌幅排行
- `get_market_stats` — A股大盘统计
- `get_stock_info` — 股票基本信息
- `get_chip_distribution` — A股筹码分布
- `get_fundamental_context` — A股深度基本面
- `detect_market_regime` — 市场阶段检测
- `get_market_review` — 大盘日度复盘

### 筛选与回测
- `screen_stocks` — 全市场多因子筛选（AlphaSift L1）
- `run_backtest` — 策略回测（AlphaEvo）
- `evaluate_signal` — 信号历史准确率
- `screen_risk` — 7维度风险筛查
- `run_watchlist_analysis` — 自选股批量分析
- `detect_anomaly` — 异动信号扫描

### 报告与上下文
- `render_stock_report` / `render_market_report` — 结构化 JSON → Markdown 报告
- `build_watchlist_context` — 自选股上下文包
- `analyze_position_context` — 持仓上下文分析
- `check_alert_rules` — 无状态告警规则检查
- `parse_stock_list` — 自选股/文本导入解析
- `diagnose_data_sources` — 数据源链路诊断
- `get_market_capabilities` — 市场能力边界

### 工具类
- `resolve_stock_name` — A股名称/拼音 → 代码
- `check_trading_day` / `get_trading_days` — 交易日历

### 情报搜索（需 API Key）
- `search_stock_news` — Tavily/Brave/SerpAPI 新闻搜索
- `search_comprehensive_intel` — 6 维度综合情报
- `get_social_sentiment` — Reddit/X/Polymarket 情绪
- `get_trending_sentiment` — 社交热门趋势
- `extract_article` — 文章正文提取

## 平行 Host

同一份业务逻辑也以 [Pi Agent extension](../pi/index.ts)、[Hermes plugin](../hermes/) 和 [OpenClaw plugin](../openclaw/) 形式分发；选你用的那个生态即可。
