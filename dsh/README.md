# Stock Analysis — DeepSeek Harness (dsh) Plugin

A股 / 港股 / 美股 / 日股 / 韩股 / 台股 行情、技术分析、筛选与回测。作为 dsh bundle 安装到 profile 后，对话中可调用 71 个工具，并自动注册 21 个工作流/策略方法论 skill。

## 按需工具发现

支持作用域工具接口和请求组装钩子的新版 dsh 默认增加一个宿主控制入口 `stock_tool_search`；按需发现只控制工具加载，不改变 71 个分析工具的名称、参数和输出。原生函数调用模式下，首次请求只暴露这个发现入口；其他插件的工具照常保留。搜索支持中文描述、英文关键词或确切工具名，例如 `财务`、`get_quote`、`run_backtest`；命中工具在下一次模型请求提供完整 schema，再按原名调用。默认最多加载 5 项，`limit` 可设 1 到 20；无匹配返回空列表。

加载集合按 Agent 隔离，搜索和回退均保留宿主已有的工具权限限制。搜索入口被禁止或移除时，下一次请求保留原先已授权的完整股票工具目录。旧版缺少相关接口时保持全量暴露；PTC / both 模式沿用宿主生成的 SDK，不做发现过滤。加载集合在本次运行中保留，恢复会话或重载插件后需重新搜索。

如需关闭发现，在 profile 中本插件条目的配置中设置：

```yaml
- name: "@weaxs/dsh-stock-analysis"
  config:
    toolDiscovery: false
```

这里的 `stock_tool_search` 是 dsh 适配器的目录控制工具，作用类似 Pi/Hermes/OpenClaw 的宿主发现入口，不增加共享 Python 分析能力，也不执行分析。

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

工具实际逻辑在 `tools/*.py`，依赖 `akshare`、`pandas`、`numpy`、`requests`。本包的 postinstall（`scripts/setup-python.mjs`）会在包目录下自动建 `.venv` 并装好依赖，前提：本机有 `python3 >= 3.10`。依赖安装失败时 postinstall 会返回非零状态，避免 pnpm 缓存未装完整的 `.venv`；解决安装问题后重新安装插件即可。

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
- `analyze_portfolio_risk` — 组合敞口、相关性、止损与确定性压力测试
- `plan_portfolio_rebalance` — 目标权重、整手和现金约束下的调仓清单

### 市场全景
- `get_market_indices` — 主要指数（CN/HK/US/JP/KR/TW）
- `get_sector_rankings` — A股板块涨跌幅排行
- `get_sector_constituents` — A股板块成分股查询（板块名模糊匹配）
- `resolve_stock_sectors` — 个股所属板块查询（A股行业+概念 / 港股 GICS）
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
