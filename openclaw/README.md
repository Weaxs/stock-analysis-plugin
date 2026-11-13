# Stock Analysis — OpenClaw Plugin

A股 / 港股 / 美股 / 日股 / 韩股 / 台股 行情、技术分析、筛选与回测。OpenClaw Gateway 加载后即可在对话中调用 73 个工具。

## 安装

```bash
openclaw plugins install clawhub:@weaxs/openclaw-stock-analysis
```

或从源码（仓库根目录）：

```bash
npm install
# OpenClaw Gateway 自行加载 ./openclaw/openclaw.plugin.json
```

### Python 运行时

工具实际逻辑在 `tools/*.py`，依赖 `akshare`、`pandas`、`numpy`、`requests`。仓库根 `package.json` 的 postinstall（`scripts/setup-python.mjs`）会自动建 `.venv` 并装好依赖，无需手工操作。前提：本机有 `python3 >= 3.10`。

如果 venv 没建起来（比如 Python 缺失），plugin 会回退到系统 `python3`，请自行 `pip install -r tools/requirements.txt`。

## 工具与 Skills

73 个工具覆盖行情与基金净值、技术分析与 K 线形态、资金流与板块、估值/股东/ETF/股息、公告与 SEC、多因子选股、策略回测、市场复盘、新闻舆情、持仓风险与调仓计算。完整列表见[主仓库 README · 工具列表](https://github.com/Weaxs/stock-analysis-plugin#工具列表)，Skills 见 [Skills 一览](https://github.com/Weaxs/stock-analysis-plugin#skills-一览)。

## 其他宿主

同一套工具也以 Pi Agent extension、Hermes plugin、DeepSeek Harness (dsh) plugin 和 MCP stdio 服务器形式分发，见[主仓库 README · 快速开始](https://github.com/Weaxs/stock-analysis-plugin#快速开始)。
