# Pi Agent 接入指南

本文介绍如何将 `stock-analysis-plugin` 作为 [Pi Agent](https://pi.dev) Extension 使用。

## 前置条件

- Node.js >= 18
- Python >= 3.10
- Pi Agent 已安装并可正常运行

## 安装方式

### 方式一：通过 Pi 包管理安装（推荐）

```bash
pi install npm:@weaxs/stock-analysis-plugin
```

安装后 Pi Agent 会自动执行 `postinstall` 脚本：

1. 查找系统中的 `python3`（需 >= 3.10）
2. 在插件目录下创建 `.venv` 虚拟环境
3. 安装 `tools/requirements.txt` 中的 Python 依赖

### 方式二：全局 Extension（Git）

```bash
cd ~/.pi/agent/extensions
git clone git@github.com:Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

### 方式三：项目级 Extension

```bash
mkdir -p .pi/extensions && cd .pi/extensions
git clone git@github.com:Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

项目级安装仅在该项目的 Pi Agent 会话中可用。

### 方式四：settings.json 配置

```json
{
  "packages": [
    "npm:@weaxs/stock-analysis-plugin@0.1.0"
  ]
}
```

## 验证加载

最新稳定版 Pi 1.2.0 支持原生按需工具发现。插件在 `session_start` 检查 `tool_search` 的 exposure 元数据及活动工具 API：具备这些能力时，将股票工具设为 `deferred` 并启用原生搜索，保留其他插件的活动工具；旧宿主保持原来的 67 工具全量暴露，无需升级才能继续使用。

新版中搜索已注册但未激活时，插件会尝试启用并确认成功后再隐藏股票工具；未加载搜索扩展、被宿主限制或无法启用时保持全量暴露。会话中途关闭搜索时，下次模型请求前恢复全部股票工具，并停止发送搜索指引。

插件保留 Pi 已恢复的活动工具集合，重载后不清除已发现工具；SDK 重载未触发 `session_start` 时，会在下次模型请求前完成初始化。

搜索器目前不对中文分词，因此使用英文关键词或确切工具名搜索，例如 `get_quote`、`get_financials`、`screen_stocks`、`run_backtest`。插件会在启用发现时向模型提供这条说明。SDK 若未加载原生工具搜索扩展，也会保留全量暴露。命中工具加载后留在当前会话活动集合中，长会话的集合会逐渐扩大。

启动 Pi Agent 后，输入：

```
/skills
```

应能看到 20 个已注册的 Skill（如 `stock-analysis`、`stock-screener`、`strategy-backtest` 等）。

也可以直接调用工具验证：

```
帮我查一下贵州茅台的实时行情
```

Agent 应会调用 `get_quote` 工具并返回行情数据。

## 环境变量配置

以下环境变量按功能分组，根据需要配置：

### 新闻 & 搜索（配置任一即可）

| 环境变量 | 用途 | 获取地址 |
|---------|------|---------|
| `TAVILY_API_KEY` | Tavily 搜索 | https://tavily.com |
| `BRAVE_API_KEY` | Brave 搜索 | https://brave.com/search/api/ |
| `SERPAPI_KEY` | SerpAPI | https://serpapi.com |
| `BOCHA_API_KEY` | Bocha AI 搜索 | — |

### 社交情绪

| 环境变量 | 用途 | 说明 |
|---------|------|------|
| `SENTIMENT_API_KEY` | 社交情绪分析 API 认证 | 东财股吧 + 雪球情绪 |
| `SENTIMENT_API_URL` | 情绪 API 地址 | 默认 `https://api.adanos.org` |

### 美股 / 港股数据源

| 环境变量 | 用途 | 说明 |
|---------|------|------|
| `FINNHUB_API_KEY` | Finnhub（美股行情、财报） | https://finnhub.io |
| `ALPHAVANTAGE_API_KEY` | Alpha Vantage（美股K线） | https://www.alphavantage.co |
| `LONGBRIDGE_APP_KEY` | 长桥（港股行情） | https://open.longportapp.com |
| `LONGBRIDGE_APP_SECRET` | 长桥 App Secret | 同上 |
| `LONGBRIDGE_ACCESS_TOKEN` | 长桥 Access Token | 同上 |

### A 股增强数据源（可选）

| 环境变量 | 用途 | 说明 |
|---------|------|------|
| `TUSHARE_TOKEN` | Tushare（A股历史数据备用源） | https://tushare.pro |

> 注意：A 股基础数据通过 akshare 免费获取，无需配置。Tushare 仅作为备用/增强数据源。

### 智堡（Wisburg）投研数据

智堡通过 **MCP Server** 接入，不是环境变量。需要在 Pi Agent 的 MCP 配置中添加智堡服务：

```json
{
  "mcpServers": {
    "wisburg-mcp-server": {
      "command": "npx",
      "args": ["-y", "@anthropic/wisburg-mcp-server"],
      "env": {
        "WISBURG_API_KEY": "your-wisburg-key"
      }
    }
  }
}
```

配置完成后，插件的 `wisburg-research` Skill 会自动调用智堡 MCP 工具（以 `mcp__wisburg-mcp-server__` 前缀），提供：
- 投行/券商研报、个股研究报告
- 电话会纪要、财报公告（A/港/美三市场）
- 投研资讯流、市场日报
- A股投资者问答（语义搜索）

在 Pi Agent 中设置环境变量的方式取决于你的启动方式：

```bash
# 直接设置
export TAVILY_API_KEY="your-key-here"

# 或在 .env 文件中配置
echo 'TAVILY_API_KEY=your-key-here' >> ~/.pi/.env
```

## 常见问题

### Python 依赖安装失败

如果 `npm install` 时 Python 环境创建失败，可手动安装：

```bash
cd ~/.pi/agent/extensions/stock-analysis-plugin
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
```

### 工具调用超时

部分工具（如 `screen_stocks` 全市场筛选）可能需要较长时间。如遇超时，可尝试缩小筛选范围（减少 `top` 参数）或检查网络连接。

### 升级插件

```bash
# 通过 Pi 包管理安装的：
pi install npm:@weaxs/stock-analysis-plugin@latest

# 通过 Git 安装的：
cd ~/.pi/agent/extensions/stock-analysis-plugin
git pull
npm install
```

## npm 包内容

通过 npm 安装时，包内仅包含 Pi Extension 所需文件：

```
@weaxs/stock-analysis-plugin/
├── pi/
│   └── index.ts          # Extension 入口，注册工具和 Skill
├── tools/                # Python CLI 工具
├── skills/               # SKILL.md
├── schemas/              # 策略 Schema
├── strategies/           # 策略示例
├── scripts/
│   └── setup-python.mjs  # postinstall 自动安装 Python 依赖
└── package.json          # pi.extensions + pi.skills 配置
```

> Hermes 相关文件（`hermes/`、`pyproject.toml`）不会包含在 npm 包中。
