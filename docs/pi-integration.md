# Pi Agent 接入指南

本文介绍如何将 `stock-analysis-plugin` 作为 [Pi Agent](https://github.com/anthropics/pi-agent) Extension 使用。

## 前置条件

- Node.js >= 18
- Python >= 3.9
- Pi Agent 已安装并可正常运行

## 安装方式

### 方式一：全局 Extension（推荐）

```bash
cd ~/.pi/agent/extensions
git clone git@github.com:Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

`npm install` 会自动执行 `postinstall` 脚本：

1. 查找系统中的 `python3`（需 >= 3.9）
2. 在插件目录下创建 `.venv` 虚拟环境
3. 安装 `tools/requirements.txt` 中的 Python 依赖

### 方式二：项目级 Extension

```bash
mkdir -p .pi/extensions && cd .pi/extensions
git clone git@github.com:Weaxs/stock-analysis-plugin.git
cd stock-analysis-plugin
npm install
```

项目级安装仅在该项目的 Pi Agent 会话中可用。

## 验证加载

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

## 工作原理

### Extension 入口

Pi Agent 通过 `package.json` 中的 `pi.extensions` 字段定位入口文件：

```json
{
  "pi": {
    "extensions": ["./pi/index.ts"]
  }
}
```

Pi Agent 使用 [jiti](https://github.com/nicolo-ribaudo/jiti) 加载 TypeScript Extension，无需编译步骤。

### 工具注册

`pi/index.ts` 导出一个函数，接收 `ExtensionAPI` 对象，通过 `pi.registerTool()` 注册 31 个工具：

```typescript
import type { ExtensionAPI } from "pi-agent";

export default (pi: ExtensionAPI) => {
  pi.registerTool({
    name: "get_kline",
    description: "获取股票K线数据...",
    parameters: {
      type: "object",
      properties: { /* ... */ },
      required: ["symbol"],
    },
    async execute({ symbol, period = "daily", count = 60 }) {
      const result = await pi.exec(`${python} ${toolsDir}/stock_data.py kline ${symbol} --period ${period} --count ${count}`);
      return result.stdout;
    },
  });
  // ... 其余 30 个工具
};
```

每个工具的 `execute` 方法通过 `pi.exec()` 调用对应的 Python CLI 脚本，参数通过命令行传递，返回 JSON 格式的标准输出。

### Skill 注册

通过 `resources_discover` 事件注册 20 个 SKILL.md：

```typescript
pi.on("resources_discover", () => ({
  skillPaths: [
    `${__dirname}/../skills/stock-analysis/SKILL.md`,
    `${__dirname}/../skills/stock-screener/SKILL.md`,
    // ...
  ],
}));
```

同时 `package.json` 的 `pi.skills` 字段也声明了 Skill 列表，用于静态发现：

```json
{
  "pi": {
    "skills": [
      "./skills/stock-analysis/SKILL.md",
      "./skills/stock-screener/SKILL.md"
    ]
  }
}
```

### Python 环境

工具执行时优先使用插件目录下的 `.venv/bin/python3`，如不存在则 fallback 到系统 `python3`：

```typescript
const venvPython = `${__dirname}/../.venv/bin/python3`;
const python = existsSync(venvPython) ? venvPython : "python3";
```

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
cd ~/.pi/agent/extensions/stock-analysis-plugin
git pull
npm install  # 会重新安装 Python 依赖
```

## 目录结构（Pi 相关）

```
stock-analysis-plugin/
├── pi/
│   └── index.ts          # Extension 入口，注册工具和 Skill
├── tools/                # 共享 Python CLI 工具
├── skills/               # 共享 SKILL.md
├── scripts/
│   └── setup-python.mjs  # postinstall 自动安装 Python 依赖
├── package.json          # pi.extensions + pi.skills 配置
└── .venv/                # 自动创建的 Python 虚拟环境
```
